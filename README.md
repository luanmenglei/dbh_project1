# FilmDB 数据库性能实验

以课程提供的 `filmdb.sql` 为基础，对比 C++ 文件操作、PostgreSQL 和 openGauss。正式性能结果必须在 Ubuntu EPYC 服务器上运行后生成；本地验证数据不能当作服务器实验结果。

## 文件导航

- `docs/实验方案.md`：研究问题、实验矩阵、公平性和报告结构。
- `src/filmbench.cpp`：完整、单文件 C++17 实现；文件操作和数据库客户端共用结果校验与计时口径。
- `scripts/prepare.py`：解析课程 SQL 中四张核心表，校验外键，输出可复现的扩展 TSV 数据。
- `sql/schema.sql`：PostgreSQL/openGauss 共用的目标表结构。
- `scripts/deploy_ubuntu.py`：创建两个独立数据库容器，记录镜像摘要，限制 CPU/内存。
- `scripts/build_opengauss_client.py`：使用所选 openGauss 镜像自带的 libpq 编译客户端。
- `scripts/run_suite.py`：自动加载、校验、预热、重复测试、恢复数据、运行并发负载，保存原始输出。
- `scripts/summarize.py`：生成 CSV 汇总及可选图表。
- `tests/test_project.py`：独立 Python 结果校验与临时 PostgreSQL 集成测试。
- `data/source/filmdb.sql`：课程 SQL 原样副本。

## 1. Ubuntu 准备

需要 Docker Engine、Python 3.9+、C++17 编译器、PostgreSQL libpq 开发库。不要用跨架构模拟运行正式实验。

```bash
sudo apt-get update
sudo apt-get install -y build-essential libpq-dev postgresql python3 python3-venv numactl sysstat
make
```

`make` 默认读取 `pg_config`。如果有多个 PostgreSQL 安装，可使用 `make PG_CONFIG=/path/to/pg_config`。Python 数据准备、运行和 CSV 汇总只使用标准库。

本机生成的 `build/` 二进制不能直接复制到 Ubuntu 使用，需要重新编译。

## 2. 数据生成

```bash
python3 scripts/prepare.py --scales 1 10 100
```

每个输出目录必须尚不存在，防止覆盖已有数据。生成数据不进入 Git 仓库，首次克隆后可一次生成三档；若已有 s1、s10，只需运行 `--scales 100`。

每档有 `countries.tsv`、`movies.tsv`、`people.tsv`、`credits.tsv` 和 `manifest.json`。清单记录源文件/输出文件 SHA-256、行数、ID 映射步长和预期查询计数。程序只解析白名单表的字面量 INSERT，不执行原始 SQL，也不会尝试执行其中的其他语句。

扩展时电影、人物和关联表按副本扩展，国家表保持 185 行；标题、姓名保留原样，新增 `replica` 列限定唯一约束的作用范围。NULL 与空字符串严格区分，TSV 转义规则与 PostgreSQL COPY text 一致。

## 3. 服务器部署

先用 `lscpu -e=CPU,CORE,SOCKET,NODE`、`numactl --hardware` 查看拓扑。选同一 NUMA 节点上的物理核心；给客户端预留独立核心，不要把 SMT 兄弟线程误计为独立物理核心。

以下 `0-7`、NUMA 节点 `0` 和 `8g` 只是示例，应根据实际配置替换：

```bash
python3 scripts/deploy_ubuntu.py --cpus 0-7 --mems 0 --memory 8g
python3 scripts/build_opengauss_client.py
source deploy/state/connections.sh
```

脚本默认先拉取 `postgres:18` 和 `opengauss/opengauss-server:latest`，再固定为不可变镜像摘要；正式运行使用摘要，不会随 tag 漂移。可通过 `--postgres-image`、`--opengauss-image` 选择指定发行版或摘要。检查 `images.json` 与数据库 `version()`，确定两者均为你要比较的发行版；若选择了 RC/测试版，应在报告注明或换稳定版本。这里不将未验证的镜像 tag 写成已测试版本。

脚本创建 `filmbench-postgres`、`filmbench-opengauss`，分别绑定本机 `127.0.0.1:55432/55433`；结束时两者均停止。数据使用独立宿主机目录，数据库共用相同资源限制。脚本不会删除同名容器或覆盖既有部署目录。

openGauss 官方镜像的启动方式要求 privileged 模式，本部署采用该方式。请在用于本课程实验的服务器上部署。客户端使用 openGauss 自带 libpq 处理其认证协议，不通过修改密码算法绕过认证兼容性。镜像布局或系统动态库不兼容时，SDK 构建会停止，需要匹配该版本的官方客户端 SDK。

连接信息写入权限为 600 的 `connections.sh`，不写入实验结果。容器元数据采集不会导出包含密码的 `Config.Env`。不要把 `deploy/state/` 作为作业材料提交。

可以编辑 `deploy/state/targets.json` 中各目标的 `client_prefix`：例如 `["taskset", "-c", "8-11"]`。CPU 编号仍须按真实拓扑选择；客户端资源充足并与数据库核心分离。文件基线可绑定到数据库实验使用的核心集，以获得相同的可用 CPU 范围。

## 4. 必须先跑小规模冒烟实验

```bash
python3 scripts/run_suite.py \
  --config deploy/state/targets.json --manage-containers \
  --out results/server-smoke --scales 1 --repeats 1 \
  --point-requests 10 --clients 1 2 \
  --warmup 2 --duration 5 --load-repeats 1
```

运行器会轮流启停数据库，先检查数据库连接、持久化设置、全表指纹和各查询结果；任何失败均停止，不把失败数据汇总为有效结论。openGauss 的实际部署、客户端兼容性和执行计划语法以这次服务器冒烟验证为准。

只验证文件部分也可以运行：

```bash
python3 scripts/run_suite.py --backends file --out results/file-smoke \
  --scales 1 --repeats 1 --point-requests 10 --concurrency-scales
```

## 5. 正式实验

```bash
python3 scripts/run_suite.py \
  --config deploy/state/targets.json --manage-containers \
  --out results/epyc-formal --scales 1 10 100
```

默认配置：批量操作重复 5 次；主键测试每轮 100 次；并发在 1 倍、100 倍数据上测 1、2、4、8、16、32 个客户端；每次预热 30 秒，测量 120 秒，重复 3 次。并发测量加预热约 6 小时，另加导入、恢复、串行测试时间。可先减少 `--clients`、`--load-repeats`，不要以冒烟时长替代正式实验。

`--out` 必须是新目录。只有全部正确性与实验步骤通过，才生成 `COMPLETE`；异常生成 `FAILED` 并保留诊断输出。恢复操作包含在原始记录中，但不计入正式操作延迟。请保证足够的磁盘空间容纳两套数据库、索引、WAL 和原始数据。

已有其他数据库实例时，可自行配置连接环境变量，使用 `targets.example.json`，不加 `--manage-containers`。此模式由你保证资源隔离。`load --reset` 只会删除目标数据库中明确命名的 `filmbench_sN` schema；不要让其他工作使用这些 schema。

## 6. 汇总与图表

```bash
python3 scripts/summarize.py results/epyc-formal
python3 -m venv .venv
.venv/bin/pip install -r requirements-plots.txt
.venv/bin/python scripts/summarize.py results/epyc-formal --plots
```

产物包括 `summary.csv` 与 `plots/*.png`。图中点为各轮中位数，误差线为最小值到最大值，不是置信区间。P95/P99 是每轮延迟直方图分位数，再跨轮取中位数；不声称是合并所有请求得到的全局分位数。

`records.jsonl` 保存每个命令、阶段、结果及日志名称；`*-resources.jsonl` 在 Linux 上保存宿主机、客户端以及可取得的 cgroup v2 原始计数器。宿主机磁盘统计包含其他任务；cgroup 内存包含缓存，也不等同于数据库进程 RSS。短操作不足一秒，资源采样不足以准确描述峰值，不能从这些采样虚构精确峰值。

## 7. 本地正确性测试

```bash
make test
```

测试创建独立的临时 PostgreSQL 集群，只监听临时 Unix socket，不访问你已有的数据库。需要以普通用户运行，且 `pg_config --bindir` 中有 `initdb`、`pg_ctl`。测试结束会停止并删除临时实例。

没有 PostgreSQL 服务端工具时，可显式运行 `python3 tests/test_project.py --skip-postgres`；这只能证明文件部分通过。

## 提交材料

最终应提交真实实验结果写成的 `report.pdf` 和 `src/filmbench.cpp`，遵循课程“不打包压缩”的要求。辅助脚本和完整复现实验包可按课程允许的方式公开并提供链接。不要提交密码、数据库目录或将本地冒烟数据当作正式报告结果。当前阶段提供方案和代码，尚未撰写带性能结论的报告。
