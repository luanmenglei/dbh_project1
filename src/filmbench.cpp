// C++17 benchmark driver. Build with libpq (PostgreSQL or openGauss client SDK).
#include <libpq-fe.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
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
#include <unordered_map>
#include <vector>
#include <fcntl.h>
#include <unistd.h>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
using Cell = std::optional<std::string>;
using Row = std::vector<Cell>;
using Result = std::unique_ptr<PGresult, decltype(&PQclear)>;

static double seconds(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double>(b-a).count();
}
static void require(bool ok, const std::string &message) {
    if (!ok) throw std::runtime_error(message);
}
static std::string json(const std::string &s) {
    std::ostringstream out; out << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') out << '\\' << c;
        else if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
        else out << c;
    }
    return out.str() + '"';
}
static std::string read_all(const fs::path &p) {
    std::ifstream f(p); require(bool(f), "Cannot open " + p.string());
    std::ostringstream s; s << f.rdbuf(); return s.str();
}
static Row decode(const std::string &line) {
    Row out; size_t start = 0;
    while (true) {
        auto end = line.find('\t', start);
        std::string s = line.substr(start, end == std::string::npos ? end : end-start);
        if (s == "\\N") out.push_back(std::nullopt);
        else {
            std::string v;
            for (size_t i=0; i<s.size(); ++i) {
                if (s[i] != '\\') { v += s[i]; continue; }
                require(++i < s.size(), "Invalid TSV escape");
                if (s[i]=='t') v += '\t'; else if (s[i]=='n') v += '\n';
                else if (s[i]=='r') v += '\r'; else if (s[i]=='\\') v += '\\';
                else throw std::runtime_error("Unsupported TSV escape");
            }
            out.push_back(v);
        }
        if (end == std::string::npos) break;
        start = end+1;
    }
    return out;
}
static std::string encode(const Row &r) {
    std::string out;
    for (size_t i=0; i<r.size(); ++i) {
        if (i) out += '\t';
        if (!r[i]) { out += "\\N"; continue; }
        for (char c : *r[i]) {
            if (c=='\\') out += "\\\\"; else if (c=='\t') out += "\\t";
            else if (c=='\n') out += "\\n"; else if (c=='\r') out += "\\r";
            else out += c;
        }
    }
    return out + '\n';
}
static void scan(const fs::path &p, const std::function<void(const Row &)> &fn) {
    std::ifstream in(p); require(bool(in), "Cannot open " + p.string());
    std::string line;
    while (std::getline(in, line)) fn(decode(line));
    require(!in.bad(), "Read failed: " + p.string());
}
struct Digest {
    uint64_t rows=0, hash=0;
    void add(const Row &row) {
        uint64_t h=14695981039346656037ULL;
        auto byte=[&](unsigned char c) { h=(h^c)*1099511628211ULL; };
        for (const auto &v:row) {
            byte(v ? 1 : 0);
            if (v) {
                uint64_t n=v->size();
                for (int i=0;i<8;++i) byte((n>>(i*8))&255);
                for (unsigned char c:*v) byte(c);
            }
        }
        ++rows; hash+=h;
    }
    void consume(PGresult *r) {
        for (int i=0;i<PQntuples(r);++i) {
            Row row;
            for (int j=0;j<PQnfields(r);++j)
                row.push_back(PQgetisnull(r,i,j) ? Cell{} : Cell{PQgetvalue(r,i,j)});
            add(row);
        }
    }
};
struct Options {
    std::map<std::string,std::string> v;
    explicit Options(int argc,char **argv) {
        for (int i=1;i<argc;++i) {
            std::string k=argv[i]; require(k.rfind("--",0)==0, "Expected --option");
            if (k=="--reset") v[k]="1";
            else { require(i+1<argc,"Missing value for "+k); v[k]=argv[++i]; }
        }
        const std::vector<std::string> allowed={"--backend","--op","--data","--schema","--dsn-env","--ddl","--reset","--keyword","--id","--requests","--seed","--indexed","--clients","--warmup","--duration","--write-percent","--table","--query"};
        for (const auto &x:v) require(std::find(allowed.begin(),allowed.end(),x.first)!=allowed.end(),"Unknown option: "+x.first);
    }
    std::string get(const std::string &k,const std::string &d="") const {
        auto it=v.find(k); return it==v.end()?d:it->second;
    }
    long long number(const std::string &k,long long d) const {
        std::string s=get(k,std::to_string(d)); size_t n=0; auto x=std::stoll(s,&n);
        require(n==s.size(),"Invalid integer: "+k); return x;
    }
};
struct DB {
    PGconn *c=nullptr;
    explicit DB(const Options &o) {
        auto env=o.get("--dsn-env","FILMBENCH_DSN"); const char *dsn=std::getenv(env.c_str());
        require(dsn && *dsn,"Set connection environment variable "+env);
        c=PQconnectdb(dsn);
        if (!c || PQstatus(c)!=CONNECTION_OK) {
            std::string err=c?PQerrorMessage(c):"allocation failed";
            if(c) PQfinish(c);
            c=nullptr; throw std::runtime_error("Connection failed: "+err);
        }
        try {
            exec("SET client_encoding='UTF8'");
            exec("SET synchronous_commit=on");
            exec("SET statement_timeout=600000");
            exec("SET search_path TO "+o.get("--schema","filmbench_s1")+",pg_catalog");
        } catch (...) { PQfinish(c); c=nullptr; throw; }
    }
    ~DB(){if(c) PQfinish(c);}
    DB(const DB&)=delete;
    Result check(PGresult *r) {
        Result out(r,PQclear);
        require(r!=nullptr,"Empty database result");
        auto s=PQresultStatus(r);
        require(s==PGRES_COMMAND_OK || s==PGRES_TUPLES_OK || s==PGRES_COPY_IN,
                std::string("Database error: ")+PQresultErrorMessage(r));
        return out;
    }
    Result exec(const std::string &s) {return check(PQexec(c,s.c_str()));}
    void prepare(const std::string &name,const std::string &sql) {check(PQprepare(c,name.c_str(),sql.c_str(),0,nullptr));}
    Result prepared(const std::string &name,const std::vector<std::string> &args={}) {
        std::vector<const char*> p; for(const auto &s:args) p.push_back(s.c_str());
        return check(PQexecPrepared(c,name.c_str(),int(p.size()),p.data(),nullptr,nullptr,0));
    }
    void copy(const std::string &table,const fs::path &file) {
        std::ifstream in(file,std::ios::binary); require(bool(in),"Missing "+file.string());
        exec("COPY "+table+" FROM STDIN");
        char buf[65536];
        while(in) {
            in.read(buf,sizeof(buf)); auto n=in.gcount();
            if(n) require(PQputCopyData(c,buf,int(n))==1,"COPY write failed");
        }
        require(!in.bad(),"COPY file read failed");
        require(PQputCopyEnd(c,nullptr)==1,"COPY end failed");
        while(auto *r=PQgetResult(c)) check(r);
    }
};
static const std::string projection="movieid,title,country,year_released,runtime";
static std::string query(const std::string &op) {
    if(op=="search_count") return "SELECT count(*) FROM movies WHERE strpos(title,$1)>0";
    if(op=="search_rows") return "SELECT movieid,title FROM movies WHERE strpos(title,$1)>0";
    if(op=="point") return "SELECT "+projection+" FROM movies WHERE movieid=$1";
    if(op=="join") return "SELECT m.movieid,m.title,c.credited_as FROM movies m JOIN credits c ON m.movieid=c.movieid WHERE c.peopleid=$1";
    if(op=="aggregate") return "SELECT country,year_released,count(*),coalesce(sum(runtime),0),count(runtime) FROM movies GROUP BY country,year_released";
    if(op=="update") return "UPDATE people SET first_name=replace(first_name,'T','Ttt'),surname=replace(surname,'T','Ttt') WHERE strpos(coalesce(first_name,''),'T')>0 OR strpos(surname,'T')>0";
    throw std::runtime_error("Unknown query: "+op);
}
static void sync_file(const fs::path &p,bool directory=false) {
    int fd=open(p.c_str(),O_RDONLY); require(fd>=0,"Cannot open for sync: "+p.string());
    int rc=fsync(fd); close(fd); require(rc==0,"fsync failed: "+p.string()+(directory?" (directory)":""));
}
static std::string replace_t(const std::string &s) {
    std::string out; for(char c:s) {out+=c; if(c=='T') out+="tt";} return out;
}
static uint64_t file_update(const fs::path &dir) {
    fs::path source=dir/"people.tsv", temp=dir/("people.tsv.tmp."+std::to_string(getpid()));
    require(!fs::exists(temp),"Temporary output already exists");
    std::ofstream out(temp,std::ios::binary); require(bool(out),"Cannot create temporary people file");
    uint64_t n=0;
    try {
        scan(source,[&](const Row &input) {
            Row r=input; require(r.size()==7,"Invalid people row"); bool changed=false;
            for(int i:{1,2}) if(r[i]) {auto s=replace_t(*r[i]); changed|=s!=*r[i]; r[i]=s;}
            n+=changed; out<<encode(r);
        });
        out.flush(); require(bool(out),"Output write failed"); out.close();
        sync_file(temp); fs::rename(temp,source); sync_file(dir,true);
    } catch (...) {out.close(); std::error_code ec; fs::remove(temp,ec); throw;}
    return n;
}
static std::vector<long long> ids(const fs::path &dir) {
    std::vector<long long> out;
    scan(dir/"movies.tsv",[&](const Row &r){out.push_back(std::stoll(r.at(0).value()));});
    require(!out.empty(),"No movie IDs"); return out;
}
static void build_index(const fs::path &dir) {
    auto source=dir/"movies.tsv", target=dir/"movies.idx";
    std::ifstream in(source,std::ios::binary); require(bool(in),"Cannot open movies");
    std::ofstream out(target,std::ios::binary); require(bool(out),"Cannot create index");
    auto stamp=std::chrono::duration_cast<std::chrono::microseconds>(fs::last_write_time(source).time_since_epoch()).count();
    out<<"FILMBENCH1 "<<fs::file_size(source)<<' '<<stamp<<'\n';
    std::string line;
    while(true) {
        auto offset=in.tellg(); if(!std::getline(in,line)) break;
        auto r=decode(line); out<<r.at(0).value()<<' '<<offset<<'\n';
    }
    require(!in.bad(),"Index source read failed"); out.flush(); require(bool(out),"Index write failed");
    out.close(); sync_file(target); sync_file(dir,true);
}
struct FileIndex {
    std::unordered_map<long long,std::streamoff> offsets;
    std::ifstream movies;
    explicit FileIndex(const fs::path &dir):movies(dir/"movies.tsv",std::ios::binary) {
        std::ifstream f(dir/"movies.idx"); std::string magic; uint64_t size; long long time;
        require(bool(f>>magic>>size>>time),"Build file index first");
        require(magic=="FILMBENCH1" && size==fs::file_size(dir/"movies.tsv") &&
                time==std::chrono::duration_cast<std::chrono::microseconds>(fs::last_write_time(dir/"movies.tsv").time_since_epoch()).count(),"Stale file index; rebuild it");
        long long id; std::streamoff offset;
        while(f>>id>>offset) offsets.emplace(id,offset);
        require(f.eof(),"Corrupt index");
    }
    void point(long long id,Digest &d) {
        auto it=offsets.find(id); if(it==offsets.end()) return;
        movies.clear(); movies.seekg(it->second); std::string line;
        require(bool(std::getline(movies,line)),"Index offset read failed");
        auto r=decode(line); require(std::stoll(r.at(0).value())==id,"Index points to wrong movie");
        r.resize(5); d.add(r);
    }
};
static void scan_point(std::ifstream &in,const std::string &id,Digest &d) {
    in.clear();in.seekg(0);std::string line;
    while(std::getline(in,line)) {
        auto r=decode(line);
        if(r.at(0).value()==id){r.resize(5);d.add(r);return;}
    }
    require(!in.bad(),"Point scan read failed");
}
static void file_query(const std::string &op,const fs::path &dir,const std::string &arg,Digest &d) {
    if(op=="search_count" || op=="search_rows") {
        uint64_t n=0;
        scan(dir/"movies.tsv",[&](const Row &r){if(r.at(1)->find(arg)!=std::string::npos) {++n;if(op=="search_rows") d.add({r[0],r[1]});}});
        if(op=="search_count") d.add({std::to_string(n)});
    } else if(op=="point") {
        std::ifstream in(dir/"movies.tsv");require(bool(in),"Cannot open movies");scan_point(in,arg,d);
    } else if(op=="join") {
        std::unordered_map<std::string,std::vector<Cell>> matches;
        scan(dir/"credits.tsv",[&](const Row &r){if(r.at(1).value()==arg) matches[r[0].value()].push_back(r[2]);});
        scan(dir/"movies.tsv",[&](const Row &r){auto it=matches.find(r[0].value());if(it!=matches.end())for(auto role:it->second)d.add({r[0],r[1],role});});
    } else if(op=="aggregate") {
        struct Group {long long count=0,sum=0,non_null=0;};
        std::map<std::pair<std::string,std::string>,Group> groups;
        scan(dir/"movies.tsv",[&](const Row &r){auto &g=groups[{r[2].value(),r[3].value()}];++g.count;if(r[4]) {g.sum+=std::stoll(*r[4]);++g.non_null;}});
        for(auto &x:groups)d.add({x.first.first,x.first.second,std::to_string(x.second.count),std::to_string(x.second.sum),std::to_string(x.second.non_null)});
    } else throw std::runtime_error("Unknown file operation: "+op);
}
// Geometric latency histogram: <=1% relative bucket width, 1 ns floor, bounded memory.
struct Histogram {
    std::map<int,uint64_t> bins; uint64_t n=0;
    void add(double us) {++bins[int(std::ceil(std::log(std::max(0.001,us))/std::log(1.01)))];++n;}
    void merge(const Histogram &other){n+=other.n;for(auto &x:other.bins)bins[x.first]+=x.second;}
    double percentile(double p) const {
        if(!n)return 0;
        uint64_t target=uint64_t(std::ceil(n*p)),count=0;
        for(auto &x:bins) {count+=x.second;if(count>=target)return std::pow(1.01,x.first)/1000.0;}
        return 0;
    }
};
static void concurrent(const Options &o) {
    auto movie_ids=ids(o.get("--data"));
    int clients=int(o.number("--clients",1)), warm=int(o.number("--warmup",30)), duration=int(o.number("--duration",120));
    int write_percent=o.get("--op")=="mixed" ? int(o.number("--write-percent",10)) : 0;
    require(clients>0 && clients<=1024 && warm>=0 && duration>0 && write_percent>=0 && write_percent<=100,"Invalid load parameters");
    std::mutex mutex; std::condition_variable cv; int ready=0; bool go=false;
    Clock::time_point start;
    struct Stats {Histogram reads,writes;uint64_t errors=0,checksum=0;std::string error;Clock::time_point finish;};
    std::vector<Stats> stats(clients); std::vector<std::thread> threads;
    for(int t=0;t<clients;++t)threads.emplace_back([&,t]{
        std::unique_ptr<DB> db;
        try {
            db=std::make_unique<DB>(o); db->prepare("read",query("point"));
            db->prepare("write","UPDATE movies SET runtime=CASE WHEN runtime IS NULL THEN 1 WHEN runtime>=300 THEN 1 ELSE runtime+1 END WHERE movieid=$1");
        } catch(const std::exception &e) {stats[t].error=e.what();}
        {std::unique_lock<std::mutex> lock(mutex);++ready;cv.notify_all();cv.wait(lock,[&]{return go;});}
        if(!stats[t].error.empty())return;
        std::mt19937_64 rng(uint64_t(o.number("--seed",20261012))+uint64_t(t));
        auto measured=start+std::chrono::seconds(warm),end=measured+std::chrono::seconds(duration);
        Digest sink;
        while(Clock::now()<end) {
            std::string id=std::to_string(movie_ids[rng()%movie_ids.size()]); bool write=int(rng()%100)<write_percent;
            auto before=Clock::now(); if(before>=end)break; bool record=before>=measured;
            try {
                auto result=db->prepared(write?"write":"read",{id});
                if(write) require(std::string(PQcmdTuples(result.get()))=="1","Update missed a movie");
                else {
                    require(PQntuples(result.get())==1,"Read missed a movie");
                    require(std::string(PQgetvalue(result.get(),0,0))==id,"Read returned wrong movie");
                    sink.consume(result.get());
                }
                auto after=Clock::now();
                if(record){(write?stats[t].writes:stats[t].reads).add(seconds(before,after)*1e6);stats[t].finish=after;}
            } catch(const std::exception &e) {
                if(record)++stats[t].errors;
                stats[t].error=e.what();stats[t].finish=Clock::now();break;
            }
        }
        stats[t].checksum=sink.hash;
    });
    {std::unique_lock<std::mutex> lock(mutex);cv.wait(lock,[&]{return ready==clients;});start=Clock::now();go=true;cv.notify_all();}
    for(auto &t:threads)t.join();
    Histogram reads,writes,all;uint64_t errors=0,checksum=0;std::string first_error;
    auto measured=start+std::chrono::seconds(warm),finish=measured+std::chrono::seconds(duration);
    for(auto &s:stats){reads.merge(s.reads);writes.merge(s.writes);errors+=s.errors;checksum+=s.checksum;if(!s.error.empty()&&first_error.empty())first_error=s.error;finish=std::max(finish,s.finish);}
    all.merge(reads);all.merge(writes);double elapsed=seconds(measured,finish);
    std::cout<<"{\"backend\":"<<json(o.get("--backend"))<<",\"op\":"<<json(o.get("--op"))
             <<",\"clients\":"<<clients<<",\"warmup_s\":"<<warm<<",\"requested_duration_s\":"<<duration
             <<",\"elapsed_s\":"<<elapsed<<",\"operations\":"<<all.n<<",\"reads\":"<<reads.n<<",\"writes\":"<<writes.n
             <<",\"errors\":"<<errors<<",\"valid\":"<<(first_error.empty()?"true":"false")
             <<",\"throughput_ops_s\":"<<all.n/elapsed<<",\"p50_ms\":"<<all.percentile(.5)
             <<",\"p95_ms\":"<<all.percentile(.95)<<",\"p99_ms\":"<<all.percentile(.99)
             <<",\"read_p95_ms\":"<<reads.percentile(.95)<<",\"write_p95_ms\":"<<writes.percentile(.95)
             <<",\"consumed_read_checksum_including_warmup\":"<<json(std::to_string(checksum))
             <<",\"error\":"<<json(first_error)<<"}\n";
    require(first_error.empty(),"Load run invalid: "+first_error);
}
int main(int argc,char **argv) {
    try {
        Options o(argc,argv);auto op=o.get("--op"),backend=o.get("--backend","file");fs::path dir=o.get("--data");
        require(backend=="file" || backend=="postgres" || backend=="opengauss","Unknown backend");
        auto schema=o.get("--schema","filmbench_s1");require(std::regex_match(schema,std::regex("filmbench_s[1-9][0-9]*")),"Schema must be filmbench_sN");
        std::cout<<std::setprecision(12);
        if(op=="concurrent_read" || op=="mixed") {require(backend!="file","Concurrent workloads require a DB");concurrent(o);return 0;}
        auto setup_start=Clock::now();std::unique_ptr<DB> db;
        if(backend!="file")db=std::make_unique<DB>(o);
        if(op=="load") {
            require(bool(db),"load requires a database");db->exec("BEGIN");
            if(o.get("--reset")=="1")db->exec("DROP SCHEMA IF EXISTS "+schema+" CASCADE");
            db->exec("CREATE SCHEMA "+schema);db->exec("SET search_path TO "+schema+",pg_catalog");
            db->exec(read_all(o.get("--ddl","sql/schema.sql")));
            for(auto t:{"countries","movies","people","credits"})db->copy(t,dir/(std::string(t)+".tsv"));
            db->exec("COMMIT");
            for(auto t:{"countries","movies","people","credits"})db->exec(std::string("ANALYZE ")+t);
            std::cout<<"{\"op\":\"load\",\"elapsed_s\":"<<seconds(setup_start,Clock::now())<<"}\n";return 0;
        }
        if(op=="snapshot") {
            require(bool(db),"snapshot requires a database");
            auto r=db->exec("SELECT version()");
            std::cout<<"{\"version\":"<<json(PQgetvalue(r.get(),0,0))<<",\"libpq_version\":"<<PQlibVersion()<<",\"settings\":[";
            r=db->exec("SELECT name,setting,unit FROM pg_settings ORDER BY name");
            for(int i=0;i<PQntuples(r.get());++i){if(i)std::cout<<',';std::cout<<'[';for(int j=0;j<3;++j){if(j)std::cout<<',';std::cout<<json(PQgetisnull(r.get(),i,j)?"":PQgetvalue(r.get(),i,j));}std::cout<<']';}
            std::cout<<"],\"relations\":[";
            r=db->exec("SELECT relname,pg_relation_size(c.oid),pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='"+schema+"' AND c.relkind='r' ORDER BY relname");
            for(int i=0;i<PQntuples(r.get());++i){if(i)std::cout<<',';std::cout<<'['<<json(PQgetvalue(r.get(),i,0))<<','<<PQgetvalue(r.get(),i,1)<<','<<PQgetvalue(r.get(),i,2)<<']';}
            std::cout<<"]}\n";return 0;
        }
        if(op=="explain") {
            require(bool(db),"explain requires a database");auto q=o.get("--query","search_count");
            require(q=="search_count"||q=="search_rows"||q=="point"||q=="join"||q=="aggregate","Only read-only plans supported");
            db->prepare("plan","EXPLAIN (ANALYZE, BUFFERS) "+query(q));
            auto arg=(q=="point"||q=="join")?o.get("--id","1"):o.get("--keyword","Love");
            auto r=db->prepared("plan",q=="aggregate"?std::vector<std::string>{}:std::vector<std::string>{arg});
            std::cout<<"{\"query\":"<<json(q)<<",\"plan\":[";
            for(int i=0;i<PQntuples(r.get());++i){if(i)std::cout<<',';std::cout<<json(PQgetvalue(r.get(),i,0));}
            std::cout<<"]}\n";return 0;
        }
        if(op=="build_index") {require(!db,"File-only operation");build_index(dir);std::cout<<"{\"op\":\"build_index\",\"elapsed_s\":"<<seconds(setup_start,Clock::now())<<",\"bytes\":"<<fs::file_size(dir/"movies.idx")<<"}\n";return 0;}
        if(op=="fingerprint") {
            auto table=o.get("--table","movies");require(table=="movies"||table=="people"||table=="credits"||table=="countries","Invalid table");
            Digest d;
            if(db) {
                // Cursor bounds client memory and keeps a consistent snapshot.
                db->exec("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
                db->exec("DECLARE verify_cursor CURSOR FOR SELECT * FROM "+table);
                while(true){auto r=db->exec("FETCH 10000 FROM verify_cursor");if(!PQntuples(r.get()))break;d.consume(r.get());}
                db->exec("COMMIT");
            } else scan(dir/(table+".tsv"),[&](const Row &r){d.add(r);});
            std::cout<<"{\"rows\":"<<d.rows<<",\"checksum\":"<<json(std::to_string(d.hash))<<"}\n";return 0;
        }
        std::vector<long long> movie_ids;std::unique_ptr<FileIndex> index;std::ifstream point_file;
        long long requests=op=="point"?o.number("--requests",1000):1;require(requests>0,"requests must be positive");
        if(op=="point") {
            movie_ids=ids(dir);
            if(!db && o.get("--indexed","0")=="1")index=std::make_unique<FileIndex>(dir);
            else if(!db) {point_file.open(dir/"movies.tsv");require(bool(point_file),"Cannot open movies");}
        }
        std::string arg=op=="join" ? o.get("--id","1") : o.get("--keyword","Love");
        if(db)db->prepare("bench",query(op));
        Digest digest;uint64_t affected=0;Histogram latency;
        std::mt19937_64 rng(uint64_t(o.number("--seed",20261012)));
        auto start=Clock::now();double setup=seconds(setup_start,start);
        for(long long i=0;i<requests;++i) {
            if(op=="point")arg=std::to_string(movie_ids[rng()%movie_ids.size()]);
            auto before=Clock::now();
            if(db) {
                if(op=="update")db->exec("BEGIN");
                auto r=db->prepared("bench",op=="aggregate"||op=="update"?std::vector<std::string>{}:std::vector<std::string>{arg});
                if(op=="update") {affected=std::stoull(PQcmdTuples(r.get()));db->exec("COMMIT");}
                else digest.consume(r.get());
            } else if(op=="update")affected=file_update(dir);
            else if(index)index->point(std::stoll(arg),digest);
            else if(op=="point")scan_point(point_file,arg,digest);
            else file_query(op,dir,arg,digest);
            latency.add(seconds(before,Clock::now())*1e6);
        }
        double elapsed=seconds(start,Clock::now());
        std::cout<<"{\"backend\":"<<json(backend)<<",\"op\":"<<json(op)<<",\"requests\":"<<requests
                 <<",\"setup_s\":"<<setup<<",\"elapsed_s\":"<<elapsed<<",\"mean_ms\":"<<elapsed*1000/requests
                 <<",\"rows\":"<<digest.rows<<",\"checksum\":"<<json(std::to_string(digest.hash))
                 <<",\"affected\":"<<affected<<",\"p50_ms\":"<<latency.percentile(.5)<<",\"p95_ms\":"<<latency.percentile(.95)<<"}\n";
        return 0;
    } catch(const std::exception &e) {std::cerr<<"filmbench: "<<e.what()<<'\n';return 1;}
}
