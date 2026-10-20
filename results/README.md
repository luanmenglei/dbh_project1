# 实验记录索引

[返回项目首页](../README.md) · [报告](../output/submission/report.pdf) · [复现与审查命令](../docs/复现指南.md)

每批实验独立保存输入、配置、原始输出与源码身份。性能记录按批次解释；功能验证中的计时不能改标成正式性能结果。

2026-10-20 对公开文件及 Git 历史中的服务器连接标识和私人路径进行了脱敏，示例地址与目录不用于连接原服务器。涉及文本变动的文件摘要已同步更新，公开源码快照的摘要对应脱敏后的字节；这些记录不能再称为未经处理的原始字节副本。测量数值、数据集、C++ 标程、提交报告 PDF 和硬件配置保持不变，脱敏不代表重新运行实验。

## 报告使用的性能证据

| 目录 | 内容 | 报告位置 |
| :--- | :--- | :--- |
| [epyc-compact-evidence](epyc-compact-evidence/) | 原主实验：873 条命令、318 条计时、三个数据规模。 | 第 01–03、05 节 |
| [scaling-probe](scaling-probe/) | 66 轮并发与 CPU 绑定干预。 | 第 04 节 |
| [scaling-balance](scaling-balance/) | 数据库与客户端各绑定 8 核的 6 轮附加实验。 | 第 04 节 |
| [scaling-confirmation](scaling-confirmation/) | 四个条件各一次 120 秒确认；每个条件没有多轮范围。 | 第 04 节 |
| [supplement-20261015-point](supplement-20261015-point/) | 五轮均匀/热点点查，300 万次正式请求；另有 60 次分层连接。 | 第 07 节 |
| [supplement-20261015-index](supplement-20261015-index/) | 两库五轮有／无人物索引配对，共 20 次查询，验证计划与四表摘要。 | 第 07 节 |
| [supplement-20261015-metadata](supplement-20261015-metadata/) | 本批协议、运行日志及 [审查统计](supplement-20261015-metadata/analysis.json)。 | 第 07 节 |

2026-10-15 补测采用明确声明的共享服务器协议：记录整机负载与实验核、SMT 兄弟核活动；110 项均通过磁盘、内存及采样检查。它与原实验的绑定及计时条件不同，不合并统计，也不能由跨批差值推断代码优化收益。

## 功能与回归证据

| 目录 | 用途 |
| :--- | :--- |
| [review-index-health](review-index-health/) | 命名空间重组前 C++ 的三后端正确性验证、索引损坏拒绝与失败重建检查；保留为历史基线。 |
| [review-namespace-20261018](review-namespace-20261018/) | 当前命名空间版的两套 SDK 编译与文件、PostgreSQL、openGauss 正确性回归；使用独立临时实例，不属于性能数据。 |
| [review-coverage-health](review-coverage-health/) | 逐轮健康记录流程的 18 个小规模覆盖案例，仅功能验证。 |
| [review-index-ablation](review-index-ablation/) | 索引对照运行器的 4 个小规模案例，仅功能验证。 |
| [review-coverage-diverse](review-coverage-diverse/) | 54 个多样查询覆盖案例；报告第 05 节引用的历史功能证据。 |
| [review-coverage](review-coverage/) | 早期查询覆盖记录，保留用于追溯。 |
| [review-correctness](review-correctness/) | 早期版本的三后端正确性验证。 |
| [review-harness-smoke](review-harness-smoke/) | 主运行器冒烟与结果审查，非正式性能比较。 |
| [server-user-verification](server-user-verification/) | 早期用户目录服务验证摘要，不是完整性能运行。 |

## 如何读一批记录

| 文件 | 含义 |
| :--- | :--- |
| `run.json` / `host.json` | 实际参数、目标配置和主机信息；连接使用命名环境变量。 |
| `records.jsonl` | 逐条命令、测量标签、日志编号与解析结果。 |
| `*.stdout` / `*.stderr` | 程序原始输出与错误输出。 |
| `*-resources.jsonl` | 运行期间的资源采样；主机计数可能包含其他任务。 |
| `source-snapshot/` / `source-sha256.json` | 当时运行的源码与 SHA-256；保留原始字节。 |
| `requests/` | 补测三后端共用的请求文件及其哈希。 |
| `coverage.json` / `ablation.json` / `analysis.json` | 对应运行器或审查器生成的摘要。 |
| `COMPLETE` / `FAILED` | 运行及清理是否完成；性能资格还需检查测量标记与协议。 |

不同年代的记录不一定具有全部字段，审查器按对应协议校验。元数据目录和早期服务摘要不使用 `COMPLETE` 标记。

`epyc-compact/` 的四份文件与 `epyc-compact-evidence/` 对应文件逐字节相同，已停止重复上传。本地副本可以保留；报告及审查程序统一使用后者。

新实验目录默认忽略。需要发布新一批证据时，先确认完整性、去除连接凭据，再在根目录 `.gitignore` 中明确加入该目录；不要改写旧批次的原始记录。
