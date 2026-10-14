# FilmDB 数据库性能实验

对比 C++ 文件程序、PostgreSQL 14.24 和 openGauss 6.0.1。课程数据来自 `data/source/filmdb.sql`。

## 当前提交入口

- **最终报告与源码**：`output/submission/report.pdf`、`output/submission/filmbench.cpp`，分别提交，不压缩。姓名与学号按用户要求留空。
- **可编辑 Typst 报告**：[report/report.typ](report/report.typ)，搭配同目录的 `data.json` 和 `figures/`。简约排版，矢量图表；修改文稿后可以直接用 Typst 编译。
- **修改与验证记录**：[审查处理记录](docs/审查处理记录.md)。
- `output/pdf/report-final.pdf`、`report-teacher*`、`experiment-guide*` 是历史版本；当前最终稿是 `output/pdf/report-typst.pdf`，同步至提交目录。

正式 compact 实验已于北京时间 2026-10-12 23:31:06 完成，873 条命令记录中有 318 条计时记录；后续 CPU 干预实验共 66＋6＋4 轮。性能结论来自这些已完成的原始数据。
2026-10-13 新版增加计时拆分、约束与回滚验证、不同关联数量和热点请求覆盖。该轮服务器负载较高，新增覆盖只作为正确性证据，不加入正式性能排名。

## 目录与版本

| 路径 | 用途 |
|---|---|
| `src/filmbench.cpp` | 当前 Linux C++17 实现；内置建表 SQL |
| `scripts/prepare.py` | 白名单解析课程 INSERT，生成四个 TSV 与 SHA-256 清单 |
| `scripts/run_suite.py` | 主实验、数据恢复、结果校验与资源采样 |
| `scripts/run_coverage.py` | 共享请求文件、均匀/热点点查、分层人物连接与事务验证 |
| `scripts/analyze_*.py` | 结果审查，含原始 stdout、矩阵、统计和源码身份 |
| `report/` | Typst 文稿、已审查数据摘要与矢量图表；可独立编译 |
| `scripts/build_typst_report.py` | 审查原始证据、重建图表和数据、编译并更新提交文件 |
| `tests/test_project.py` | 独立 Python oracle 与临时 PostgreSQL 集成测试 |
| `tests/test_harness.py` | 本地清理与证据审查回归测试 |
| `experiments/baseline-20261012/` | 原性能实验源码快照 |
| `results/*/source-snapshot/` | 每次实验实际源码；不与后续修改混淆 |

仓库包含 `results/` 下的原始实验记录、统计结果和源码快照，以及 `output/submission/` 中的最终 PDF、C++ 源码和 `output/submission-manifest.json` 哈希清单。克隆后可直接查看报告，或用已有记录重新构建报告。重复的结果压缩包、旧版报告、生成的大规模数据、数据库状态和临时文件不纳入 Git。

## Ubuntu 编译与数据准备

需要 Python 3.9+、C++17、libpq 开发库；完整临时数据库测试还需 PostgreSQL 服务端工具。

```bash
sudo apt-get install build-essential libpq-dev postgresql python3 python3-venv numactl sysstat
make
python3 scripts/prepare.py --scales 1 10 100
```

已有数据目录不能覆盖；只生成缺少的规模即可。四表为 countries、movies、people、credits；后三张按关系复制并平移 ID，国家表保持 185 行。该扩容保持原字段分布，不代表独立新增真实数据。

单独编译提交的源码：

```bash
c++ -O3 -std=c++17 filmbench.cpp -I"$(pg_config --includedir)" \
  -L"$(pg_config --libdir)" -Wl,-rpath,"$(pg_config --libdir)" -lpq -pthread -o filmbench
```

默认 DDL 已内置，`--ddl sql/schema.sql` 可显式覆盖。数据仍需四个 COPY-text TSV；其来源、转义规则和生成脚本均保留在项目中。

### 从提交文件开始复现

仅有 `report.pdf` 与 `filmbench.cpp` 时，先获取配套课程数据和转换脚本。以下固定版本中的 `src/filmbench.cpp` 与当前提交源码 SHA-256 相同；该版本用于复现程序，最新报告以提交 PDF 为准。

```bash
git clone https://github.com/luanmenglei/dbh_project1.git
cd dbh_project1
git checkout 668d1c787030c21b61bc062c55fca962ef4571d7
python3 scripts/prepare.py --scales 1
c++ -O3 -std=c++17 src/filmbench.cpp -I"$(pg_config --includedir)" \
  -L"$(pg_config --libdir)" -Wl,-rpath,"$(pg_config --libdir)" -lpq -pthread -o filmbench
./filmbench --backend file --op search_rows --data data/generated/s1 --keyword The
```

预期 `rows=1563`，表示匹配 1,563 部电影。若使用 `--op search_count`，程序返回一条计数结果的摘要，此时 `rows=1` 不表示只匹配一部电影。若编译单独下载的提交文件，将编译命令的 `src/filmbench.cpp` 替换为该文件的路径即可。

准备一个专用实验数据库后，将真实连接参数放入 `FILMBENCH_DSN`（不要提交密码）。默认 schema 为 `filmbench_s1`，第一次导入不加 `--reset`，遇到已有 schema 时程序拒绝覆盖：

```bash
./filmbench --backend postgres --op load --data data/generated/s1
./filmbench --backend postgres --op search_rows --data data/generated/s1 --keyword The
```

openGauss 需用其配套 libpq 重新编译，在相应连接环境下将后端改为 `opengauss`。上述小规模查询用于核对功能，不应拿本机测量与报告中的服务器数字拼成新的比较。

### 性能实验与提交源码的对应关系

- 原主实验实际源码为 `experiments/baseline-20261012/src/filmbench.cpp`，同时保存在 `results/epyc-compact-evidence/source-snapshot/`。对应结果来自该快照，不能标为当前版本重新测得。
- 历史点查在准备阶段扫描 TSV 收集候选 ID，在执行阶段随机选 ID 并转成字符串；当前版本先生成完整请求序列，再开始查询计时。两版 `elapsed_s` 的边界存在差异，不能只按字段同名直接合并。
- 原始并发记录已分别保存 `read_p95_ms` 和 `write_p95_ms`。报告取“各轮 P95 的中位数”，不是把三轮请求合并后重新计算 P95。
- 120 秒 CPU 干预确认每个条件只有一轮；原三轮矩阵与单轮确认分别解释。文件索引构建耗时同样只有一次测量。

### 尚待服务器完成的补测

长批次点查可使用现有补测器：均匀、热点两种分布，每轮共享同一请求文件，轮间使用不同种子；三种后端核对内容摘要。以下为待执行命令，不代表已经取得性能结果。服务启动参数需恢复正式实验的 CPU 与内存配置，不能直接复用先前正确性检查的两核配置。

```bash
python3 scripts/run_coverage.py --config deploy/state/targets.json --manage-user-services \
  --out results/new-point-performance --scale 100 --requests 100000 --repeats 5
python3 scripts/analyze_coverage.py results/new-point-performance
```

运行前需确认服务器满足负载和磁盘阈值；运行期间也应观察共享负载，异常轮次不用于排名。报告保留原 100 次点查的结果及其短窗口局限；此前 `performance_eligible=false` 的正确性记录不改标为性能记录。

索引作用的单独验证应在专用实验 schema 中进行：保持数据库、数据、人物编号和结果列不变，仅改变 `credits_person_idx` 是否存在，检查实际执行计划，分别预热、交替测量至少五轮，并核对结果摘要。原始源码允许用 `--ddl` 传入只删除这一条 `CREATE INDEX` 的建表文件，可通过重新导入建立两种状态；导入和索引建立不计入查询时间。当前正式记录尚未包含此对照，不据此给出索引加速倍数。

```bash
./build/filmbench --backend file --op search_count --data data/generated/s1 --keyword The
./build/filmbench --backend file --op build_index --data data/generated/s1
./build/filmbench --backend file --op point --data data/generated/s1 --indexed 1 --requests 1000
```

## 数据库部署与连接

共享服务器使用普通用户私有实例，版本、资源边界与已完成的历史运行见 [用户目录部署](docs/用户目录部署.md)。连接从 `POSTGRES_DSN`、`OPENGAUSS_DSN` 等命名环境变量读取，密码不写入报告或记录。

已经有用户目录 SDK 时，可只编译当前客户端，不重建数据库；输出必须为新目录：

```bash
python3 scripts/build_user_clients.py --runtime /path/to/runtime --out /path/to/new-build
```

openGauss 客户端需要匹配其认证协议的 libpq。将目标配置中 `binary` 指向对应 wrapper。

独立服务器也可使用原 Docker 路线：

```bash
python3 scripts/deploy_ubuntu.py --cpus 0-7 --mems 0 --memory 8g
python3 scripts/build_opengauss_client.py
source deploy/state/connections.sh
```

CPU 编号只是示例，按真实 NUMA/SMT 拓扑选择，并为客户端预留核心。部署脚本记录镜像摘要，拒绝覆盖既有部署；版本以实际 `version()` 为准。Docker 使用 `--manage-containers`，用户服务使用 `--manage-user-services`，二者互斥。运行器在成功、异常或中断退出时尝试停止两套受管后端，清理失败会标记 FAILED。

## 测试与正式运行

```bash
python3 tests/test_harness.py
make test
# 没有 PostgreSQL 服务端时，只验证文件端：
python3 tests/test_project.py --skip-postgres
```

`make test` 使用独立临时 PostgreSQL 集群，不连接已有数据库。两套私有实例的检查另用：

```bash
source deploy/state/connections.sh
python3 scripts/verify_user_instances.py --out results/new-correctness
```

先跑新目录中的小规模冒烟，再运行正式矩阵；共享服务器沿用用户 scope 的内存与 CPU 限制：

```bash
python3 scripts/run_suite.py --config deploy/state/targets.json --manage-user-services \
  --out results/new-smoke --scales 1 --repeats 1 --point-requests 10 \
  --clients 1 2 --concurrency-scales 1 --warmup 0 --duration 1 --load-repeats 1
python3 scripts/run_suite.py --profile compact --out results/new-formal --dry-run
python3 scripts/run_suite.py --profile compact --config deploy/state/targets.json \
  --manage-user-services --out results/new-formal
python3 scripts/summarize.py results/new-formal
python3 scripts/analyze_results.py results/new-formal
```

compact：串行 s1/s10/s100，每配置 3 轮、点查 100 次；并发 s10、1/4/8 客户端、预热 5 s、测量 20 s、3 轮。full 保留更长矩阵。参数可显式覆盖。`--out` 必须为新目录；只有实验及清理均成功才写 COMPLETE，失败保留日志并写 FAILED。

原始表和输入哈希、查询摘要、受影响行数会校验。每轮更新和混合负载前恢复数据；`load --reset` 只用于明确允许重建的 `filmbench_sN` schema。

## 新版计时与扩展查询

`timing_version=2` 区分 `request_prepare_s`、`backend_setup_s`、`elapsed_s`；`setup_s` 为准备总量，索引构建独立记录。旧数据的 setup 含全表候选 ID 扫描，不能视为数据库本身初始化耗时，也不能从旧数字反推新分项。

```bash
python3 scripts/prepare_requests.py --data data/generated/s10 --out results/new-requests \
  --requests 10000 --repeats 5
python3 scripts/run_coverage.py --config deploy/state/targets.json --manage-user-services \
  --out results/new-coverage --requests 10000 --repeats 5
python3 scripts/analyze_coverage.py results/new-coverage
```

共享请求序列包含均匀分布与 90% 请求落在前 1% ID 的热点分布。连接选择不存在、低、正关联数量的中间档和高关联人物；中间档按不同关联数量取中位，避免人数中位与低档都只返回一行。

正式补测要求启动时 load1≤32、磁盘空闲>40 GiB；负载不满足时停止。显式增加 `--correctness-only` 可做功能验证，记录标记为非性能测量，不产生新的性能排名。

`--op functional` 验证非法外键被拒绝，以及“先更新、再失败、再回滚”恢复原值。补充运行还核对回滚后的四表指纹；这些证据不等同于隔离级别或崩溃恢复测试。

## 报告重建与证据审查

```bash
python3 -m pip install -r requirements-plots.txt
python3 scripts/build_typst_report.py
```

安装 Typst 0.14.2 或兼容版本并加入 PATH（也可传 `--typst /path/to/typst`）。上面的完整重建会审查历史及新版证据，生成 `report/data.json`、三个 SVG 图表、`output/pdf/report-typst.pdf`，并更新 `output/submission/report.pdf`、`filmbench.cpp` 和提交哈希清单。需要完整 `results/` 证据及 `results/review-correctness/verification.json`；不会用伪造数字补缺。

仅修改文稿时，无需 Python、数据库或原始实验目录，可直接使用已导出的数据和矢量图：

```bash
typst compile report/report.typ report.pdf
# 填写署名（默认留空）：
typst compile --input name=姓名 --input student-id=学号 report/report.typ report.pdf
```

使用 Windows 微软雅黑；其他系统安装静态版 `Noto Sans CJK SC`，通过 Typst 的 `--input 'font=Noto Sans CJK SC'` 或完整构建脚本的 `--font 'Noto Sans CJK SC'` 指定。等宽字体使用 Consolas（其他系统可替换为 DejaVu Sans Mono）。避免使用当前 Typst 尚未完整支持的可变字体。完整构建脚本也支持 `--name` 和 `--student-id`。历史 ReportLab 构建器 `build_final_report.py` 保留供追溯，不再作为当前报告入口。

审查使用显式异常，`python -O` 不会关闭检查；同时校验统计完整性和原始 stdout。历史哈希对应 `source-snapshot`，允许当前实现继续修正而不破坏旧证据。
