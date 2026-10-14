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

生成数据、数据库状态、结果、PDF 和临时文件不纳入 Git。完整实验结果需由已有证据副本提供，或在 Ubuntu 上运行生成；只克隆源码不能凭空得到历史实验数字。

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
