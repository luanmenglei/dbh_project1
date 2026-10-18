// FilmBench：文件操作、PostgreSQL 与 openGauss 的性能实验。
// 阅读顺序：database 的 SQL → file_backend 的对应实现 → application 的实验流程。
// common 提供通用工具，requests 准备点查序列，benchmark 负责并发和延迟统计。
// 六个命名空间按职责组织，可通过编辑器的花括号折叠；函数和类也可分别折叠。
// Linux C++17 编译：c++ -O3 -std=c++17 filmbench.cpp
// $(pkg-config --cflags --libs libpq) -pthread -o filmbench
// 输入：scripts/prepare.py 生成的四张 COPY-text TSV 表；连接参数来自环境变量。
// DDL 已内置，可用 --ddl 指定其他模式；实验输出为 JSON，便于自动核验。
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <libpq-fe.h>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <random>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>
#include <unordered_map>
#include <vector>

// 公共工具：类型、计时、输入格式、摘要校验与参数解析。
namespace common {

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
using Cell = std::optional<std::string>;
using Row = std::vector<Cell>;
using Result = std::unique_ptr<PGresult, decltype(&PQclear)>;

static double seconds(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double>(b - a).count();
}
static void require(bool ok, const std::string &message) {
    if (!ok)
        throw std::runtime_error(message);
}
static std::string json(const std::string &s) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\')
            out << '\\' << c;
        else if (c < 32)
            out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
        else
            out << c;
    }
    return out.str() + '"';
}
static std::string read_all(const fs::path &p) {
    std::ifstream f(p);
    require(bool(f), "Cannot open " + p.string());
    std::ostringstream s;
    s << f.rdbuf();
    return s.str();
}
// TSV 使用 COPY-text 转义：\N 表示 NULL，制表符、换行和反斜杠需解码。
static Row decode(const std::string &line) {
    Row out;
    size_t start = 0;
    while (true) {
        auto end = line.find('\t', start);
        std::string s = line.substr(start, end == std::string::npos ? end : end - start);
        if (s == "\\N")
            out.push_back(std::nullopt);
        else {
            std::string v;
            for (size_t i = 0; i < s.size(); ++i) {
                if (s[i] != '\\') {
                    v += s[i];
                    continue;
                }
                require(++i < s.size(), "Invalid TSV escape");
                if (s[i] == 't')
                    v += '\t';
                else if (s[i] == 'n')
                    v += '\n';
                else if (s[i] == 'r')
                    v += '\r';
                else if (s[i] == '\\')
                    v += '\\';
                else
                    throw std::runtime_error("Unsupported TSV escape");
            }
            out.push_back(v);
        }
        if (end == std::string::npos)
            break;
        start = end + 1;
    }
    return out;
}
static std::string encode(const Row &r) {
    std::string out;
    for (size_t i = 0; i < r.size(); ++i) {
        if (i)
            out += '\t';
        if (!r[i]) {
            out += "\\N";
            continue;
        }
        for (char c : *r[i]) {
            if (c == '\\')
                out += "\\\\";
            else if (c == '\t')
                out += "\\t";
            else if (c == '\n')
                out += "\\n";
            else if (c == '\r')
                out += "\\r";
            else
                out += c;
        }
    }
    return out + '\n';
}
static void scan(const fs::path &p, const std::function<void(const Row &)> &fn) {
    std::ifstream in(p);
    require(bool(in), "Cannot open " + p.string());
    std::string line;
    while (std::getline(in, line))
        fn(decode(line));
    require(!in.bad(), "Read failed: " + p.string());
}
// 行内区分 NULL、空串和字段边界；行哈希累加使核验不依赖结果顺序。
// 这是用于发现常见结果差异的摘要，不是密码学上的无碰撞证明。
struct Digest {
    uint64_t rows = 0, hash = 0;
    void add(const Row &row) {
        uint64_t h = 14695981039346656037ULL;
        auto byte = [&](unsigned char c) { h = (h ^ c) * 1099511628211ULL; };
        for (const auto &v : row) {
            byte(v ? 1 : 0);
            if (v) {
                uint64_t n = v->size();
                for (int i = 0; i < 8; ++i)
                    byte((n >> (i * 8)) & 255);
                for (unsigned char c : *v)
                    byte(c);
            }
        }
        ++rows;
        hash += h;
    }
    void consume(PGresult *r) {
        for (int i = 0; i < PQntuples(r); ++i) {
            Row row;
            for (int j = 0; j < PQnfields(r); ++j)
                row.push_back(PQgetisnull(r, i, j) ? Cell{} : Cell{PQgetvalue(r, i, j)});
            add(row);
        }
    }
};
// 统一检查参数名与整数格式；各模块按需读取，不在这里执行实验。
struct Options {
    std::map<std::string, std::string> v;
    explicit Options(int argc, char **argv) {
        for (int i = 1; i < argc; ++i) {
            std::string k = argv[i];
            require(k.rfind("--", 0) == 0, "Expected --option");
            if (k == "--reset")
                v[k] = "1";
            else {
                require(i + 1 < argc, "Missing value for " + k);
                v[k] = argv[++i];
            }
        }
        const std::vector<std::string> allowed = {
            "--backend",       "--op",      "--data",    "--schema",      "--dsn-env",
            "--ddl",           "--reset",   "--keyword", "--id",          "--requests",
            "--seed",          "--indexed", "--clients", "--warmup",      "--duration",
            "--write-percent", "--table",   "--query",   "--request-file"};
        for (const auto &x : v)
            require(std::find(allowed.begin(), allowed.end(), x.first) != allowed.end(),
                    "Unknown option: " + x.first);
    }
    std::string get(const std::string &k, const std::string &d = "") const {
        auto it = v.find(k);
        return it == v.end() ? d : it->second;
    }
    long long number(const std::string &k, long long d) const {
        std::string s = get(k, std::to_string(d));
        size_t n = 0;
        auto x = std::stoll(s, &n);
        require(n == s.size(), "Invalid integer: " + k);
        return x;
    }
};
} // namespace common

// 数据库后端：连接管理、表结构、SQL 与外键/回滚验证。
namespace database {
using common::Digest;
using common::json;
using common::Options;
using common::require;
using common::Result;
namespace fs = std::filesystem;

// 封装 libpq 的连接、结果释放、参数化查询和 COPY 导入；失败时抛出异常。
struct DB {
    PGconn *c = nullptr;
    explicit DB(const Options &o) {
        auto env = o.get("--dsn-env", "FILMBENCH_DSN");
        const char *dsn = std::getenv(env.c_str());
        require(dsn && *dsn, "Set connection environment variable " + env);
        c = PQconnectdb(dsn);
        if (!c || PQstatus(c) != CONNECTION_OK) {
            std::string err = c ? PQerrorMessage(c) : "allocation failed";
            if (c)
                PQfinish(c);
            c = nullptr;
            throw std::runtime_error("Connection failed: " + err);
        }
        try {
            exec("SET client_encoding='UTF8'");
            exec("SET synchronous_commit=on");
            exec("SET statement_timeout=600000");
            exec("SET search_path TO " + o.get("--schema", "filmbench_s1") + ",pg_catalog");
        } catch (...) {
            PQfinish(c);
            c = nullptr;
            throw;
        }
    }
    ~DB() {
        if (c)
            PQfinish(c);
    }
    DB(const DB &) = delete;
    Result check(PGresult *r) {
        Result out(r, PQclear);
        require(r != nullptr, "Empty database result");
        auto s = PQresultStatus(r);
        require(s == PGRES_COMMAND_OK || s == PGRES_TUPLES_OK || s == PGRES_COPY_IN,
                std::string("Database error: ") + PQresultErrorMessage(r));
        return out;
    }
    Result exec(const std::string &s) {
        return check(PQexec(c, s.c_str()));
    }
    void prepare(const std::string &name, const std::string &sql) {
        check(PQprepare(c, name.c_str(), sql.c_str(), 0, nullptr));
    }
    Result prepared(const std::string &name, const std::vector<std::string> &args = {}) {
        std::vector<const char *> p;
        for (const auto &s : args)
            p.push_back(s.c_str());
        return check(PQexecPrepared(c, name.c_str(), int(p.size()), p.data(), nullptr, nullptr, 0));
    }
    void copy(const std::string &table, const fs::path &file) {
        std::ifstream in(file, std::ios::binary);
        require(bool(in), "Missing " + file.string());
        exec("COPY " + table + " FROM STDIN");
        char buf[65536];
        while (in) {
            in.read(buf, sizeof(buf));
            auto n = in.gcount();
            if (n)
                require(PQputCopyData(c, buf, int(n)) == 1, "COPY write failed");
        }
        require(!in.bad(), "COPY file read failed");
        require(PQputCopyEnd(c, nullptr) == 1, "COPY end failed");
        while (auto *r = PQgetResult(c))
            check(r);
    }
};
// 内置 DDL 使提交文件可独立编译；replica 将业务唯一性限定在扩容副本内。
static const char *default_schema = R"FILMDB(
CREATE TABLE countries (
    country_code varchar(2) PRIMARY KEY,
    country_name varchar(50) NOT NULL UNIQUE,
    continent varchar(20) NOT NULL
);
CREATE TABLE movies (
    movieid bigint PRIMARY KEY,
    title varchar(100) NOT NULL,
    country varchar(2) NOT NULL REFERENCES countries(country_code),
    year_released integer NOT NULL,
    runtime integer,
    replica integer NOT NULL,
    UNIQUE(title, country, year_released, replica)
);
CREATE TABLE people (
    peopleid bigint PRIMARY KEY,
    first_name varchar(30),
    surname varchar(30) NOT NULL,
    born integer NOT NULL,
    died integer,
    gender varchar(1) NOT NULL,
    replica integer NOT NULL,
    UNIQUE(surname, first_name, replica)
);
CREATE TABLE credits (
    movieid bigint NOT NULL REFERENCES movies(movieid),
    peopleid bigint NOT NULL REFERENCES people(peopleid),
    credited_as varchar(1) NOT NULL,
    PRIMARY KEY(movieid, peopleid, credited_as)
);
CREATE INDEX credits_person_idx ON credits(peopleid, movieid);
)FILMDB";
static const std::string projection = "movieid,title,country,year_released,runtime";
// 与 file_backend::file_query 对应；count 返回计数，rows 返回编号和标题。
static std::string query(const std::string &op) {
    if (op == "search_count")
        return "SELECT count(*) FROM movies WHERE strpos(title,$1)>0";
    if (op == "search_rows")
        return "SELECT movieid,title FROM movies WHERE strpos(title,$1)>0";
    if (op == "point")
        return "SELECT " + projection + " FROM movies WHERE movieid=$1";
    if (op == "join")
        return "SELECT m.movieid,m.title,c.credited_as FROM movies m JOIN credits c ON "
               "m.movieid=c.movieid WHERE c.peopleid=$1";
    if (op == "aggregate")
        return "SELECT country,year_released,count(*),coalesce(sum(runtime),0),count(runtime) FROM "
               "movies GROUP BY country,year_released";
    if (op == "update")
        return "UPDATE people SET "
               "first_name=replace(first_name,'T','Ttt'),surname=replace(surname,'T','Ttt') WHERE "
               "strpos(coalesce(first_name,''),'T')>0 OR strpos(surname,'T')>0";
    throw std::runtime_error("Unknown query: " + op);
}

// 验证非法外键被拒绝，以及失败事务回滚后先前完成的片长修改被撤销。
static void functional_checks(DB &db) {
    auto before = db.exec("SELECT movieid,runtime FROM movies ORDER BY movieid LIMIT 1");
    require(PQntuples(before.get()) == 1, "Functional checks require loaded data");
    Digest original;
    original.consume(before.get());
    std::string id = PQgetvalue(before.get(), 0, 0);
    auto invalid = [&] {
        Result r(PQexec(db.c, "INSERT INTO credits SELECT (SELECT max(movieid)+1 FROM "
                              "movies),peopleid,'A' FROM people ORDER BY peopleid LIMIT 1"),
                 PQclear);
        require(bool(r), "Missing constraint test result");
        const char *state = PQresultErrorField(r.get(), PG_DIAG_SQLSTATE);
        require(PQresultStatus(r.get()) == PGRES_FATAL_ERROR && state &&
                    std::string(state) == "23503",
                "Expected foreign-key violation (23503)");
    };
    db.exec("BEGIN");
    invalid();
    db.exec("ROLLBACK");
    db.exec("BEGIN");
    auto changed = db.exec("UPDATE movies SET runtime=coalesce(runtime,0)+1 WHERE movieid=" + id);
    require(std::string(PQcmdTuples(changed.get())) == "1", "Rollback test update missed row");
    auto intermediate = db.exec("SELECT movieid,runtime FROM movies WHERE movieid=" + id);
    Digest during;
    during.consume(intermediate.get());
    require(during.hash != original.hash, "Test update did not change value");
    invalid();
    db.exec("ROLLBACK");
    auto after = db.exec("SELECT movieid,runtime FROM movies WHERE movieid=" + id);
    Digest restored;
    restored.consume(after.get());
    require(restored.rows == original.rows && restored.hash == original.hash,
            "Failed transaction was not rolled back");
    std::cout << "{\"op\":\"functional\",\"foreign_key_rejected\":true,\"sqlstate\":\"23503\","
                 "\"intermediate_update_observed\":true,\"transaction_rollback_restored\":true}\n";
}
} // namespace database

// 文件后端：持久化姓名更新、偏移索引、查询与分组统计。
namespace file_backend {
using common::Cell;
using common::decode;
using common::Digest;
using common::encode;
using common::require;
using common::Row;
using common::scan;
namespace fs = std::filesystem;

static void sync_file(const fs::path &p, bool directory = false) {
    int fd = open(p.c_str(), O_RDONLY);
    require(fd >= 0, "Cannot open for sync: " + p.string());
    int rc = fsync(fd);
    close(fd);
    require(rc == 0, "fsync failed: " + p.string() + (directory ? " (directory)" : ""));
}
static std::string replace_t(const std::string &s) {
    std::string out;
    for (char c : s) {
        out += c;
        if (c == 'T')
            out += "tt";
    }
    return out;
}
// T → Ttt：重写全部记录，同步临时文件、替换原文件，再同步目录。
// 返回值只统计实际改变的行数；即使只改一行，也会重写整张人物文件。
static uint64_t file_update(const fs::path &dir) {
    fs::path source = dir / "people.tsv",
             temp = dir / ("people.tsv.tmp." + std::to_string(getpid()));
    require(!fs::exists(temp), "Temporary output already exists");
    std::ofstream out(temp, std::ios::binary);
    require(bool(out), "Cannot create temporary people file");
    uint64_t n = 0;
    try {
        scan(source, [&](const Row &input) {
            Row r = input;
            require(r.size() == 7, "Invalid people row");
            bool changed = false;
            for (int i : {1, 2})
                if (r[i]) {
                    auto s = replace_t(*r[i]);
                    changed |= s != *r[i];
                    r[i] = s;
                }
            n += changed;
            out << encode(r);
        });
        out.flush();
        require(bool(out), "Output write failed");
        out.close();
        sync_file(temp);
        fs::rename(temp, source);
        sync_file(dir, true);
    } catch (...) {
        out.close();
        std::error_code ec;
        fs::remove(temp, ec);
        throw;
    }
    return n;
}

// 保存 movieid → 字节偏移；结束标记和摘要用于发现截断或损坏的索引。
static void build_index(const fs::path &dir) {
    auto source = dir / "movies.tsv", target = dir / "movies.idx";
    auto temp = dir / ("movies.idx.tmp." + std::to_string(getpid()));
    require(!fs::exists(temp), "Temporary index already exists");
    std::ifstream in(source, std::ios::binary);
    require(bool(in), "Cannot open movies");
    std::ofstream out(temp, std::ios::binary);
    require(bool(out), "Cannot create temporary index");
    auto stamp = std::chrono::duration_cast<std::chrono::microseconds>(
                     fs::last_write_time(source).time_since_epoch())
                     .count();
    auto size = fs::file_size(source);
    try {
        out << "FILMBENCH2 " << size << ' ' << stamp << '\n';
        Digest entries;
        std::string line;
        while (true) {
            auto offset = in.tellg();
            if (!std::getline(in, line))
                break;
            auto r = decode(line);
            auto id = r.at(0).value();
            auto position = std::to_string(static_cast<std::streamoff>(offset));
            out << id << ' ' << position << '\n';
            entries.add({id, position});
        }
        require(!in.bad(), "Index source read failed");
        require(size == fs::file_size(source) &&
                    stamp == std::chrono::duration_cast<std::chrono::microseconds>(
                                 fs::last_write_time(source).time_since_epoch())
                                 .count(),
                "Movies changed during index build");
        // The footer detects complete-line truncation as well as damaged entries.
        out << "END " << entries.rows << ' ' << entries.hash << '\n';
        out.flush();
        require(bool(out), "Index write failed");
        out.close();
        require(bool(out), "Index close failed");
        sync_file(temp);
        fs::rename(temp, target);
        sync_file(dir, true);
    } catch (...) {
        out.close();
        std::error_code error;
        fs::remove(temp, error);
        throw;
    }
}
// 装载并检查索引属于准备阶段；point 只定位、读取和核对目标记录。
struct FileIndex {
    std::unordered_map<long long, std::streamoff> offsets;
    std::ifstream movies;
    explicit FileIndex(const fs::path &dir) : movies(dir / "movies.tsv", std::ios::binary) {
        std::ifstream f(dir / "movies.idx");
        std::string magic;
        uint64_t size;
        long long time;
        require(bool(f >> magic >> size >> time), "Build file index first");
        require(magic == "FILMBENCH2", "Unsupported index version; rebuild it");
        require(bool(movies), "Cannot open movies");
        require(size == fs::file_size(dir / "movies.tsv") &&
                    time == std::chrono::duration_cast<std::chrono::microseconds>(
                                fs::last_write_time(dir / "movies.tsv").time_since_epoch())
                                .count(),
                "Stale file index; rebuild it");
        Digest entries;
        std::string token;
        std::streamoff previous = -1;
        bool complete = false;
        while (f >> token) {
            if (token == "END") {
                uint64_t count, hash;
                require(bool(f >> count >> hash), "Incomplete index footer; rebuild it");
                require(count == entries.rows && hash == entries.hash,
                        "Index integrity check failed; rebuild it");
                f >> std::ws;
                require(f.eof() && !f.bad(), "Unexpected trailing index data");
                complete = true;
                break;
            }
            size_t parsed = 0;
            long long id = std::stoll(token, &parsed);
            std::streamoff offset;
            require(parsed == token.size() && id > 0 && bool(f >> offset), "Invalid index entry");
            require(offset > previous && offset >= 0 && uint64_t(offset) < size,
                    "Invalid index offset");
            require(offsets.emplace(id, offset).second, "Duplicate movie ID in index");
            entries.add({token, std::to_string(offset)});
            previous = offset;
        }
        require(complete, "Truncated index; rebuild it");
    }
    void point(long long id, Digest &d) {
        auto it = offsets.find(id);
        if (it == offsets.end())
            return;
        movies.clear();
        movies.seekg(it->second);
        std::string line;
        require(bool(std::getline(movies, line)), "Index offset read failed");
        auto r = decode(line);
        require(std::stoll(r.at(0).value()) == id, "Index points to wrong movie");
        r.resize(5);
        d.add(r);
    }
};
static void scan_point(std::ifstream &in, const std::string &id, Digest &d) {
    in.clear();
    in.seekg(0);
    std::string line;
    while (std::getline(in, line)) {
        auto r = decode(line);
        if (r.at(0).value() == id) {
            r.resize(5);
            d.add(r);
            return;
        }
    }
    require(!in.bad(), "Point scan read failed");
}
// 连接扫描演职关系表和电影表，聚合使用有序映射；均不使用电影偏移索引。
static void file_query(const std::string &op, const fs::path &dir, const std::string &arg,
                       Digest &d) {
    if (op == "search_count" || op == "search_rows") {
        uint64_t n = 0;
        scan(dir / "movies.tsv", [&](const Row &r) {
            if (r.at(1)->find(arg) != std::string::npos) {
                ++n;
                if (op == "search_rows")
                    d.add({r[0], r[1]});
            }
        });
        if (op == "search_count")
            d.add({std::to_string(n)});
    } else if (op == "point") {
        std::ifstream in(dir / "movies.tsv");
        require(bool(in), "Cannot open movies");
        scan_point(in, arg, d);
    } else if (op == "join") {
        std::unordered_map<std::string, std::vector<Cell>> matches;
        scan(dir / "credits.tsv", [&](const Row &r) {
            if (r.at(1).value() == arg)
                matches[r[0].value()].push_back(r[2]);
        });
        scan(dir / "movies.tsv", [&](const Row &r) {
            auto it = matches.find(r[0].value());
            if (it != matches.end())
                for (auto role : it->second)
                    d.add({r[0], r[1], role});
        });
    } else if (op == "aggregate") {
        struct Group {
            long long count = 0, sum = 0, non_null = 0;
        };
        std::map<std::pair<std::string, std::string>, Group> groups;
        scan(dir / "movies.tsv", [&](const Row &r) {
            auto &g = groups[{r[2].value(), r[3].value()}];
            ++g.count;
            if (r[4]) {
                g.sum += std::stoll(*r[4]);
                ++g.non_null;
            }
        });
        for (auto &x : groups)
            d.add({x.first.first, x.first.second, std::to_string(x.second.count),
                   std::to_string(x.second.sum), std::to_string(x.second.non_null)});
    } else
        throw std::runtime_error("Unknown file operation: " + op);
}
} // namespace file_backend

// 请求准备：读取有效电影编号，生成或复用跨后端一致的点查序列。
namespace requests {
using common::Options;
using common::require;
using common::Row;
using common::scan;
namespace fs = std::filesystem;

static std::vector<long long> ids(const fs::path &dir) {
    std::vector<long long> out;
    scan(dir / "movies.tsv", [&](const Row &r) { out.push_back(std::stoll(r.at(0).value())); });
    require(!out.empty(), "No movie IDs");
    return out;
}
static std::vector<std::string> point_requests(const Options &o, long long count) {
    std::vector<std::string> requests;
    if (!o.get("--request-file").empty()) {
        std::ifstream in(o.get("--request-file"));
        require(bool(in), "Cannot open request file");
        std::string value;
        while (std::getline(in, value)) {
            size_t n = 0;
            auto id = std::stoll(value, &n);
            require(n == value.size() && id > 0, "Invalid request ID");
            requests.push_back(std::to_string(id));
        }
        require(!in.bad() && requests.size() == size_t(count),
                "Request file length must equal --requests");
    } else {
        auto movie_ids = ids(o.get("--data"));
        std::mt19937_64 rng(uint64_t(o.number("--seed", 20261012)));
        for (long long i = 0; i < count; ++i)
            requests.push_back(std::to_string(movie_ids[rng() % movie_ids.size()]));
    }
    return requests;
}
} // namespace requests

// 性能测量：近似延迟分位数、闭环并发、预热与读写统计。
namespace benchmark {
using common::Clock;
using common::Digest;
using common::json;
using common::Options;
using common::require;
using common::seconds;
using database::DB;
using database::query;
using requests::ids;

// Geometric latency histogram: <=1% relative bucket width, 1 ns floor, bounded memory.
// 几何分桶近似分位数，避免保存全部延迟样本；输入微秒，输出毫秒。
struct Histogram {
    std::map<int, uint64_t> bins;
    uint64_t n = 0;
    void add(double us) {
        ++bins[int(std::ceil(std::log(std::max(0.001, us)) / std::log(1.01)))];
        ++n;
    }
    void merge(const Histogram &other) {
        n += other.n;
        for (auto &x : other.bins)
            bins[x.first] += x.second;
    }
    double percentile(double p) const {
        if (!n)
            return 0;
        uint64_t target = uint64_t(std::ceil(n * p)), count = 0;
        for (auto &x : bins) {
            count += x.second;
            if (count >= target)
                return std::pow(1.01, x.first) / 1000.0;
        }
        return 0;
    }
};
// 每线程一个连接，连接准备完成后共同开始预热，再按请求开始时间纳入测量。
static void concurrent(const Options &o) {
    auto movie_ids = ids(o.get("--data"));
    int clients = int(o.number("--clients", 1)), warm = int(o.number("--warmup", 30)),
        duration = int(o.number("--duration", 120));
    int write_percent = o.get("--op") == "mixed" ? int(o.number("--write-percent", 10)) : 0;
    require(clients > 0 && clients <= 1024 && warm >= 0 && duration > 0 && write_percent >= 0 &&
                write_percent <= 100,
            "Invalid load parameters");
    std::mutex mutex;
    std::condition_variable cv;
    int ready = 0;
    bool go = false;
    Clock::time_point start;
    struct Stats {
        Histogram reads, writes;
        uint64_t errors = 0, checksum = 0;
        std::string error;
        Clock::time_point finish;
    };
    std::vector<Stats> stats(clients);
    std::vector<std::thread> threads;
    for (int t = 0; t < clients; ++t)
        threads.emplace_back([&, t] {
            std::unique_ptr<DB> db;
            try {
                db = std::make_unique<DB>(o);
                db->prepare("read", query("point"));
                db->prepare("write",
                            "UPDATE movies SET runtime=CASE WHEN runtime IS NULL THEN 1 WHEN "
                            "runtime>=300 THEN 1 ELSE runtime+1 END WHERE movieid=$1");
            } catch (const std::exception &e) {
                stats[t].error = e.what();
            }
            {
                std::unique_lock<std::mutex> lock(mutex);
                ++ready;
                cv.notify_all();
                cv.wait(lock, [&] { return go; });
            }
            if (!stats[t].error.empty())
                return;
            std::mt19937_64 rng(uint64_t(o.number("--seed", 20261012)) + uint64_t(t));
            auto measured = start + std::chrono::seconds(warm),
                 end = measured + std::chrono::seconds(duration);
            Digest sink;
            // Closed-loop clients send the next request only after consuming this result.
            // Requests started during warmup never enter the latency/throughput sample.
            while (Clock::now() < end) {
                std::string id = std::to_string(movie_ids[rng() % movie_ids.size()]);
                bool write = int(rng() % 100) < write_percent;
                auto before = Clock::now();
                if (before >= end)
                    break;
                bool record = before >= measured;
                try {
                    auto result = db->prepared(write ? "write" : "read", {id});
                    if (write)
                        require(std::string(PQcmdTuples(result.get())) == "1",
                                "Update missed a movie");
                    else {
                        require(PQntuples(result.get()) == 1, "Read missed a movie");
                        require(std::string(PQgetvalue(result.get(), 0, 0)) == id,
                                "Read returned wrong movie");
                        sink.consume(result.get());
                    }
                    auto after = Clock::now();
                    if (record) {
                        (write ? stats[t].writes : stats[t].reads)
                            .add(seconds(before, after) * 1e6);
                        stats[t].finish = after;
                    }
                } catch (const std::exception &e) {
                    if (record)
                        ++stats[t].errors;
                    stats[t].error = e.what();
                    stats[t].finish = Clock::now();
                    break;
                }
            }
            stats[t].checksum = sink.hash;
        });
    {
        std::unique_lock<std::mutex> lock(mutex);
        cv.wait(lock, [&] { return ready == clients; });
        start = Clock::now();
        go = true;
        cv.notify_all();
    }
    for (auto &t : threads)
        t.join();
    Histogram reads, writes, all;
    uint64_t errors = 0, checksum = 0;
    std::string first_error;
    auto measured = start + std::chrono::seconds(warm),
         finish = measured + std::chrono::seconds(duration);
    // Include completion time of requests started before the measurement deadline.
    for (auto &s : stats) {
        reads.merge(s.reads);
        writes.merge(s.writes);
        errors += s.errors;
        checksum += s.checksum;
        if (!s.error.empty() && first_error.empty())
            first_error = s.error;
        finish = std::max(finish, s.finish);
    }
    all.merge(reads);
    all.merge(writes);
    double elapsed = seconds(measured, finish);
    std::cout << "{\"backend\":" << json(o.get("--backend")) << ",\"op\":" << json(o.get("--op"))
              << ",\"clients\":" << clients << ",\"warmup_s\":" << warm
              << ",\"requested_duration_s\":" << duration << ",\"elapsed_s\":" << elapsed
              << ",\"operations\":" << all.n << ",\"reads\":" << reads.n
              << ",\"writes\":" << writes.n << ",\"errors\":" << errors
              << ",\"valid\":" << (first_error.empty() ? "true" : "false")
              << ",\"throughput_ops_s\":" << all.n / elapsed << ",\"p50_ms\":" << all.percentile(.5)
              << ",\"p95_ms\":" << all.percentile(.95) << ",\"p99_ms\":" << all.percentile(.99)
              << ",\"read_p95_ms\":" << reads.percentile(.95)
              << ",\"write_p95_ms\":" << writes.percentile(.95)
              << ",\"consumed_read_checksum_including_warmup\":" << json(std::to_string(checksum))
              << ",\"error\":" << json(first_error) << "}\n";
    require(first_error.empty(), "Load run invalid: " + first_error);
}
} // namespace benchmark

// 命令执行：准备输入、分派操作、执行串行计时并输出结果。
namespace application {
using benchmark::concurrent;
using benchmark::Histogram;
using common::Clock;
using common::Digest;
using common::json;
using common::Options;
using common::read_all;
using common::require;
using common::Row;
using common::scan;
using common::seconds;
using database::DB;
using database::default_schema;
using database::functional_checks;
using database::query;
using file_backend::build_index;
using file_backend::file_query;
using file_backend::file_update;
using file_backend::FileIndex;
using file_backend::scan_point;
using requests::point_requests;
namespace fs = std::filesystem;

static int run(int argc, char **argv) {
    try {
        // 1. 校验参数；并发实验直接交给 benchmark，串行点查先准备请求序列。
        Options o(argc, argv);
        auto op = o.get("--op"), backend = o.get("--backend", "file");
        fs::path dir = o.get("--data");
        require(backend == "file" || backend == "postgres" || backend == "opengauss",
                "Unknown backend");
        auto schema = o.get("--schema", "filmbench_s1");
        require(std::regex_match(schema, std::regex("filmbench_s[1-9][0-9]*")),
                "Schema must be filmbench_sN");
        std::cout << std::setprecision(12);
        if (op == "concurrent_read" || op == "mixed") {
            require(backend != "file", "Concurrent workloads require a DB");
            concurrent(o);
            return 0;
        }
        auto setup_start = Clock::now();
        long long requests = op == "point" ? o.number("--requests", 1000) : 1;
        require(requests > 0, "requests must be positive");
        std::vector<std::string> request_ids;
        double request_prepare = 0;
        if (op == "point") {
            request_ids = point_requests(o, requests);
            request_prepare = seconds(setup_start, Clock::now());
        }
        auto backend_start = Clock::now();
        std::unique_ptr<DB> db;
        if (backend != "file")
            db = std::make_unique<DB>(o);
        // 2. 功能验证和导入是独立命令，不进入下面的查询计时。
        if (op == "functional") {
            require(bool(db), "functional requires a database");
            functional_checks(*db);
            return 0;
        }
        if (op == "load") {
            require(bool(db), "load requires a database");
            db->exec("BEGIN");
            if (o.get("--reset") == "1")
                db->exec("DROP SCHEMA IF EXISTS " + schema + " CASCADE");
            db->exec("CREATE SCHEMA " + schema);
            db->exec("SET search_path TO " + schema + ",pg_catalog");
            db->exec(o.get("--ddl").empty() ? default_schema : read_all(o.get("--ddl")));
            for (auto t : {"countries", "movies", "people", "credits"})
                db->copy(t, dir / (std::string(t) + ".tsv"));
            db->exec("COMMIT");
            for (auto t : {"countries", "movies", "people", "credits"})
                db->exec(std::string("ANALYZE ") + t);
            std::cout << "{\"op\":\"load\",\"elapsed_s\":" << seconds(setup_start, Clock::now())
                      << "}\n";
            return 0;
        }
        // 3. 保存配置、关系大小或执行计划，用于解释和追溯实验结果。
        if (op == "snapshot") {
            require(bool(db), "snapshot requires a database");
            auto r = db->exec("SELECT version()");
            std::cout << "{\"version\":" << json(PQgetvalue(r.get(), 0, 0))
                      << ",\"libpq_version\":" << PQlibVersion() << ",\"settings\":[";
            r = db->exec("SELECT name,setting,unit FROM pg_settings ORDER BY name");
            for (int i = 0; i < PQntuples(r.get()); ++i) {
                if (i)
                    std::cout << ',';
                std::cout << '[';
                for (int j = 0; j < 3; ++j) {
                    if (j)
                        std::cout << ',';
                    std::cout << json(PQgetisnull(r.get(), i, j) ? "" : PQgetvalue(r.get(), i, j));
                }
                std::cout << ']';
            }
            std::cout << "],\"relations\":[";
            r = db->exec(
                "SELECT relname,pg_relation_size(c.oid),pg_total_relation_size(c.oid) FROM "
                "pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='" +
                schema + "' AND c.relkind='r' ORDER BY relname");
            for (int i = 0; i < PQntuples(r.get()); ++i) {
                if (i)
                    std::cout << ',';
                std::cout << '[' << json(PQgetvalue(r.get(), i, 0)) << ','
                          << PQgetvalue(r.get(), i, 1) << ',' << PQgetvalue(r.get(), i, 2) << ']';
            }
            std::cout << "]}\n";
            return 0;
        }
        if (op == "explain") {
            require(bool(db), "explain requires a database");
            auto q = o.get("--query", "search_count");
            require(q == "search_count" || q == "search_rows" || q == "point" || q == "join" ||
                        q == "aggregate",
                    "Only read-only plans supported");
            db->prepare("plan", "EXPLAIN (ANALYZE, BUFFERS) " + query(q));
            auto arg =
                (q == "point" || q == "join") ? o.get("--id", "1") : o.get("--keyword", "Love");
            auto r = db->prepared("plan", q == "aggregate" ? std::vector<std::string>{}
                                                           : std::vector<std::string>{arg});
            std::cout << "{\"query\":" << json(q) << ",\"plan\":[";
            for (int i = 0; i < PQntuples(r.get()); ++i) {
                if (i)
                    std::cout << ',';
                std::cout << json(PQgetvalue(r.get(), i, 0));
            }
            std::cout << "]}\n";
            return 0;
        }
        // 4. 独立测量建索引；全表摘要用于核对导入、更新和恢复。
        if (op == "build_index") {
            require(!db, "File-only operation");
            build_index(dir);
            std::cout << "{\"op\":\"build_index\",\"elapsed_s\":"
                      << seconds(setup_start, Clock::now())
                      << ",\"bytes\":" << fs::file_size(dir / "movies.idx") << "}\n";
            return 0;
        }
        if (op == "fingerprint") {
            auto table = o.get("--table", "movies");
            require(table == "movies" || table == "people" || table == "credits" ||
                        table == "countries",
                    "Invalid table");
            Digest d;
            if (db) {
                // Cursor bounds client memory and keeps a consistent snapshot.
                db->exec("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
                db->exec("DECLARE verify_cursor CURSOR FOR SELECT * FROM " + table);
                while (true) {
                    auto r = db->exec("FETCH 10000 FROM verify_cursor");
                    if (!PQntuples(r.get()))
                        break;
                    d.consume(r.get());
                }
                db->exec("COMMIT");
            } else
                scan(dir / (table + ".tsv"), [&](const Row &r) { d.add(r); });
            std::cout << "{\"rows\":" << d.rows << ",\"checksum\":" << json(std::to_string(d.hash))
                      << "}\n";
            return 0;
        }
        // 5. 准备连接、索引和 SQL 后执行串行实验；更新计时包含事务提交。
        std::unique_ptr<FileIndex> index;
        std::ifstream point_file;
        if (op == "point") {
            if (!db && o.get("--indexed", "0") == "1")
                index = std::make_unique<FileIndex>(dir);
            else if (!db) {
                point_file.open(dir / "movies.tsv");
                require(bool(point_file), "Cannot open movies");
            }
        }
        std::string arg = op == "join" ? o.get("--id", "1") : o.get("--keyword", "Love");
        if (db)
            db->prepare("bench", query(op));
        Digest digest;
        uint64_t affected = 0;
        Histogram latency;
        // Prepare requests, connections and the file index before query timing starts.
        auto start = Clock::now();
        double setup = seconds(setup_start, start), backend_setup = seconds(backend_start, start);
        for (long long i = 0; i < requests; ++i) {
            if (op == "point")
                arg = request_ids[size_t(i)];
            auto before = Clock::now();
            if (db) {
                if (op == "update")
                    db->exec("BEGIN");
                auto r = db->prepared("bench", op == "aggregate" || op == "update"
                                                   ? std::vector<std::string>{}
                                                   : std::vector<std::string>{arg});
                if (op == "update") {
                    affected = std::stoull(PQcmdTuples(r.get()));
                    db->exec("COMMIT");
                } else
                    digest.consume(r.get());
            } else if (op == "update")
                affected = file_update(dir);
            else if (index)
                index->point(std::stoll(arg), digest);
            else if (op == "point")
                scan_point(point_file, arg, digest);
            else
                file_query(op, dir, arg, digest);
            latency.add(seconds(before, Clock::now()) * 1e6);
        }
        double elapsed = seconds(start, Clock::now());
        std::cout << "{\"backend\":" << json(backend) << ",\"op\":" << json(op)
                  << ",\"requests\":" << requests
                  << ",\"timing_version\":2,\"request_prepare_s\":" << request_prepare
                  << ",\"backend_setup_s\":" << backend_setup << ",\"setup_s\":" << setup
                  << ",\"elapsed_s\":" << elapsed << ",\"mean_ms\":" << elapsed * 1000 / requests
                  << ",\"rows\":" << digest.rows
                  << ",\"checksum\":" << json(std::to_string(digest.hash))
                  << ",\"affected\":" << affected << ",\"p50_ms\":" << latency.percentile(.5)
                  << ",\"p95_ms\":" << latency.percentile(.95) << "}\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "filmbench: " << e.what() << '\n';
        return 1;
    }
}
} // namespace application

// 全局入口只委托给命令执行模块；错误处理由 application::run 统一完成。
int main(int argc, char **argv) {
    return application::run(argc, argv);
}
