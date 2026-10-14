// Minimal report; data.json and vector figures are exported from audited evidence.
#let d = json("data.json")
#let ink = rgb("252525")
#let muted = rgb("656565")
#let body-font = sys.inputs.at("font", default: "Microsoft YaHei")
#set document(title: "FilmDB 数据库性能实验", author: sys.inputs.at("name", default: ""))
#set page(
  paper: "a4", margin: (x: 21mm, top: 21mm, bottom: 20mm),
  header: text(size: 8pt, fill: muted)[数据库系统实验 #h(1fr) FILMDB / 2026],
  footer: context [#line(length: 100%, stroke: .4pt + rgb("cccccc"))
    #v(2mm)
    #text(size: 8pt, fill: muted)[文件 · 查询 · 事务 #h(1fr) #counter(page).display("1")]
  ],
)
#set text(font: body-font, size: 10.5pt, fill: ink, lang: "zh", region: "CN")
#set par(justify: true, leading: .8em, spacing: 1em)
#set heading(numbering: none)
#show heading.where(level: 1): set text(size: 17pt, weight: "bold")
#show heading.where(level: 2): set text(size: 11pt, weight: "bold")
#show heading: set block(above: 1.3em, below: .8em)
#set raw(theme: none)
#show raw: set text(font: ("Consolas", body-font), size: 8.2pt)
#show figure.caption: set text(size: 8.3pt, fill: muted)
#set figure(gap: 2mm)
#let note(body) = block(text(size: 8.5pt, fill: muted, body))
#let tab(headers, rows, columns: auto, numeric: true) = block(above: 3mm, below: 3mm)[
  #set text(size: 9pt)
  #set par(leading: .45em)
  #table(
    columns: if columns == auto { (1fr,) * headers.len() } else { columns },
    align: (x, y) => if numeric and x > 0 { right } else { left },
    stroke: none, inset: (x: 5pt, y: 7pt),
    table.hline(y: 0, stroke: .7pt + ink),
    table.header(..headers.map(x => strong(x))),
    table.hline(y: 1, stroke: .4pt + rgb("aaaaaa")),
    ..rows.flatten().map(x => {
      let parts = str(x).split("\n")
      if parts.len() == 1 { parts.first() } else {
        [#parts.first() #linebreak()#text(size: if numeric { 7.4pt } else { 9pt }, fill: if numeric { muted } else { ink }, parts.last())]
      }
    }),
    table.hline(y: rows.len() + 1, stroke: .4pt + rgb("aaaaaa")),
  )
]

#v(7mm)
#text(size: 25pt, weight: "bold")[FilmDB 数据库性能实验]
#v(2mm)
#text(size: 17pt)[文件程序、PostgreSQL 与 openGauss]
#v(5mm)
#text(size: 9pt, fill: muted)[PostgreSQL 14.24 / openGauss 6.0.1 / C++17]
#v(6mm)
#text(size: 9pt)[姓名：#sys.inputs.at("name", default: "") #h(30mm) 学号：#sys.inputs.at("student-id", default: "")]
#v(3mm)

= 01　实验设计与评价指标

本实验比较 C++ 文件程序、PostgreSQL 14.24 和 openGauss 6.0.1 处理同一份电影数据的表现，考察数据库相比文件操作的优势，以及两套数据库各自适合的场景。实验包括标题搜索、按编号查询、多表连接、分组聚合和批量修改姓名，随后增加客户端数量，测量两套数据库的并发性能。

性能比较采用操作耗时、每秒完成的请求数和 CPU 用量三项指标。耗时用于比较查询和更新的速度，吞吐量反映并发处理能力，客户端与数据库的 CPU 用量则帮助判断性能瓶颈。此外，通过非法外键插入和事务回滚，检查数据库能否拒绝不一致的数据并撤销失败事务中的修改。

== 数据规模

数据来自课程提供的 `filmdb.sql`，使用国家、电影、人物和演职关系四张表。除原始数据外，再生成 10 倍和 100 倍数据，比较数据量增加后的运行时间。扩容方式是从原表复制并给编号加偏移量，因此保持了原有的字段分布，不代表新增的真实数据。

#tab(("规模", "电影", "人物", "演职关系"), (
  ("1×", "9,204", "16,489", "46,850"),
  ("10×", "92,040", "164,890", "468,500"),
  ("100×", "920,400", "1,648,900", "4,685,000"),
))

国家表在三个规模下均为 185 行。100 倍数据的四个 TSV 文件合计约 161.1 MiB。

== 运行环境与测量方法

实验服务器运行 Ubuntu 22.04，配备双路 AMD EPYC 7542 和 NVMe 存储。基础实验为数据库分配 8 个 CPU 核、客户端分配 2 个核，两个数据库轮流运行。每个数据库的内存上限为 8 GiB；文件程序、客户端和运行脚本共用 4 GiB 内存上限。服务器还运行其他任务。

两库的 shared_buffers 均设为 512 MB，work_mem 为 4 MB，关闭单条查询内部的并行执行。更新完成后等待数据持久化。串行查询先预热一次；并发测试先预热 5 秒，再测量 20 秒。每组通常重复三次，表中给出中位数，图中的误差线表示最小值和最大值。

#pagebreak()
= 02　数据规模与操作耗时

#figure(image("figures/serial.svg", width: 100%), caption: [三个数据规模的耗时。横纵轴均使用对数刻度。])

标题计数和分组聚合都要读取整张表，耗时随数据量增加。文件程序逐行解析 TSV，聚合时用 `std::map` 保存各组统计量；两库的执行计划采用 HashAggregate，以哈希表累计各组结果。100 倍数据下，两库在这两项操作中都快于文件程序；其中 PostgreSQL 的聚合耗时约为文件程序的三分之一。

#tab(("100× 耗时 / ms", "文件", "PostgreSQL", "openGauss"), d.serial, columns: (1.3fr, 1fr, 1fr, 1fr))
#note[表内为中位数，下方为［最小值，最大值］。]

连接查询的差距最大。文件程序先扫描演职关系表，再扫描电影表；数据库通过人物索引找到关联记录，再按电影主键读取详情。三个规模都返回 54 行，因此数据库耗时变化很小，文件程序却随表的增大明显变慢。100 倍数据下，文件用时约 1.81 秒，两库都只需约 1 毫秒。

姓名更新把名字和姓氏中的 T 替换为 Ttt，100 倍数据共修改 128,700 行。文件程序写入临时文件、同步磁盘后替换原文件，数据库在事务中执行 UPDATE 并提交；每轮开始前恢复原始姓名，避免在上轮结果上继续替换。文件程序要重写整张人物文件，数据库只改动匹配的记录。这一项文件用时 1.83 秒，PostgreSQL 1.47 秒，openGauss 3.16 秒：数据库在批量更新上并不必然更快，openGauss 的用时约为 PostgreSQL 的两倍。

#pagebreak()
= 03　标题搜索与索引查询

== 标题搜索

标题搜索分别使用 The、Love 和 Star，区分大小写。SQL 用 `strpos(title,$1)>0`，文件程序用 `string::find`。除了统计匹配数量，还测量返回所有匹配电影编号和标题的耗时。

#tab(("100× 关键词", "返回行数", "文件 / ms", "PG / ms", "OG / ms"), d.search)

三个关键词都要扫描电影表，但匹配行数相差很大。以 PostgreSQL 为例，The 匹配 156,300 行，只计数用时 128.13 ms，返回编号和标题用时 209.60 ms；Love 和 Star 分别匹配 8,800 行和 5,000 行，返回结果均用时约 125 ms。返回更多记录会增加传输和处理工作，因此评价搜索性能时，除了表的大小，还应考虑结果集的大小。

== 文件索引与数据库点查

#tab(("100×，100 次点查", "单次查询 / μs", "准备＋100 次查询 / s"), d.point, columns: (1.25fr, 1fr, 1.25fr))

顺序查找需要从文件开头逐行寻找电影编号，平均一次约 263 ms。文件索引保存每个编号在文件中的位置，查询时先查内存中的哈希表，再跳到相应位置读取记录，平均只需 4.84 μs。PostgreSQL 和 openGauss 的主键查询分别为 97.43 μs 和 194.43 μs。

文件程序在本进程内查哈希表并读取记录，数据库查询还涉及客户端与服务端通信及语句执行。本实验只在按编号查询时使用文件索引，标题搜索和人物连接仍采用扫描。若要加快这些操作，还需另外设计索引或修改查询程序。

== 准备时间如何计算

表中最后一列是准备加上执行 100 次查询的合计时间。准备阶段要生成随机查询编号，程序为此扫描电影文件，因此数据库的准备时间也包含这段文件读取；除此之外还分别包含装载索引或建立数据库连接。文件索引虽然单次查询最快，但这一小批查询加上准备时间后共用 0.741 秒，PostgreSQL 为 0.569 秒。

100 倍数据的文件索引另需 #d.index_build 秒建立，未计入表中最后一列。索引和数据库连接都可以反复使用，查询次数越多，平均到每次查询上的准备成本越小。

#pagebreak()
= 04　约束与事务

数据库可以通过约束检查数据是否符合预先定义的规则。例如，演职关系中的电影编号应当对应一部存在的电影。向演职关系表插入一个不存在的电影编号时，PostgreSQL 和 openGauss 都拒绝了该操作，并返回外键错误 23503。

另一项能力是事务。测试的这笔事务包含两步操作：先修改一部电影的片长，再插入一条非法演职关系。第二步失败后执行 ROLLBACK，随后重新读取片长，检查第一步的修改是否一起撤销。

#tab(("实验", "PostgreSQL", "openGauss"), (
  ("插入不存在电影的演职关系", "拒绝；23503", "拒绝；23503"),
  ("先更新 runtime，再触发外键错误", "中间值已变化", "中间值已变化"),
  ("回滚后检查片长和四张表", "全部恢复", "全部恢复"),
), columns: (2.1fr, 1fr, 1fr))

两库在事务中都能读到修改后的片长，回滚后都恢复了原值。程序还比较了四张表的行数和内容摘要，结果与事务开始前一致。这两项检查只覆盖外键约束和单笔事务的回滚行为，没有测试各个隔离级别下的并发读写，也没有测试断电或进程崩溃后的恢复。

文件程序也能通过写临时文件再替换原文件来完成更新，但本实验的文件程序没有实现外键检查和事务。若要自行实现，还需要增加相应的约束检查和事务管理，以处理多表修改的顺序、失败恢复以及表之间的一致性。

== 检查不同查询条件

连接查询选择四个人物编号，分别返回 0、1、22 和 54 行，检查空结果和不同数量的关联记录。文件程序与两个数据库的返回内容一致。

#tab(("查询条件", "人物编号", "返回行数"), d.joins.enumerate().map(((i, row)) => (("无匹配", "少量关联", "中等关联", "较多关联").at(i), row.at(1), row.at(2))))

主键查询采用两种编号分布：均匀随机选择，以及把 90% 的请求集中在前 1% 的电影编号上。每轮执行 #d.coverage_requests 次，重复 #d.coverage_repeats 轮，三个实现的返回结果均一致。

#if d.performance_eligible [
#note[这组查询的详细记录保存在 coverage.json。]
] else [
#note[这组检查运行时服务器较忙，因此只核对查询结果，耗时未纳入性能比较。]
]

#pagebreak()
= 05　并发性能与 CPU 占用

并发测试使用 10 倍数据，每个客户端收到结果后再发送下一条请求。先测试 1、4、8 个客户端：两个数据库的吞吐量都随客户端数量上升，PostgreSQL 始终更高，但增加的客户端越多，它的增幅越小。为了判断放缓的原因，继续测试 16 和 32 个客户端，并记录两端 CPU 用量。

#figure(image("figures/concurrency.svg", width: 100%), caption: [数据库分配 8 核，客户端分配 2 核。P95 表示约 95% 的请求能在这一时间内完成。])

#tab(("32 客户端纯读", "客户端用量 / 核", "数据库用量 / 核"), d.cpu, columns: (1.3fr, 1fr, 1fr))

32 个客户端下，PostgreSQL 的客户端已经用满分配到的 2 个核，而数据库只用了约 4 个核，说明 CPU 先被客户端的请求生成和结果处理占满。openGauss 则相反：数据库已接近 8 核满载，客户端还有少量余量。两侧的瓶颈不同。

#figure(image("figures/interventions.svg", width: 100%), caption: [增加 CPU 后的吞吐量。DB 表示数据库核数，C 表示客户端核数。])

保持数据库 8 核，把客户端从 2 核增加到 4 核，PostgreSQL 的吞吐量明显上升，openGauss 变化较小。随后保持客户端 4 核，把数据库增加到 12 核，openGauss 的吞吐量也随之上升。

#pagebreak()
= 06　结果分析与结论

#tab(("数据库核 / 客户端核", "PG / 万次·s⁻¹", "OG / 万次·s⁻¹"), d.interventions, columns: (1.4fr, 1fr, 1fr))
#note[32 客户端纯读；三轮吞吐中位数。]

32 个客户端下，给客户端增加两个核后，PostgreSQL 从每秒 9.21 万次提高到 18.69 万次，约为原来的两倍；数据库核数没有变化，说明此前仍有处理能力，只是客户端来不及发送和接收更多请求。openGauss 在数据库从 8 核增加到 12 核后，从每秒 8.72 万次提高到 12.49 万次，把客户端从 2 核加到 4 核则几乎没有变化。

进一步给数据库和客户端各分配 8 核，PostgreSQL 达到每秒 #d.balance.at(0) 万次，数据库此时接近 8 核满载；openGauss 为每秒 #d.balance.at(1) 万次。将关键配置的测量时间延长到 120 秒后，增加客户端核数使 PG 吞吐量变为原来的 #d.long_ratio.at(0) 倍，增加数据库核数使 OG 变为 #d.long_ratio.at(1) 倍。两个数据库都受资源限制，但限制在客户端还是数据库一侧并不相同，评价吞吐量时要同时看两端的占用。

== 读写混合负载

混合负载中，约 90% 的请求按主键读取电影，10% 更新一部电影的片长，每条更新单独提交。基础实验的结果如下。

#tab(("8 客户端", "PG / 万次·s⁻¹", "OG / 万次·s⁻¹"), d.compact, columns: (1.4fr, 1fr, 1fr))

加入更新后，PostgreSQL 的吞吐量基本不变，openGauss 从每秒 7.35 万次降到 5.50 万次。每条更新都要单独提交，写入开销直接体现在吞吐量上。这组以读取为主、夹杂少量单行更新的请求，PostgreSQL 处理得更快。

== 实现方式的选择

如果只需按编号读取静态数据，本实验的文件偏移索引查询最快。需要关联多张表、修改数据并维护一致性时，数据库更合适：人物连接可借助索引避免扫描全表，外键拒绝非法引用，事务负责把一组修改一起提交或撤销。文件程序也可以继续增加这些功能，但需要自行实现相应的检查和管理逻辑。

在两个数据库之间，FilmDB 实验更支持选择 PostgreSQL。它在标题搜索、分组聚合、批量姓名更新、主键点查和读写混合负载中都优于 openGauss；并发测试中，在 1 到 32 个客户端的各档配置下，相同核数分配时的吞吐量也都更高。人物连接查询是例外，由 openGauss 小幅领先，100 倍数据下两者分别为 1.07 ms 和 1.42 ms。这个结论限定在本次数据、配置和请求方式下。

本实验使用复制扩容的数据，数据可以全部放入内存，查询经过预热，每轮更新前重新导入数据。共享服务器上的短时测试尚未覆盖数据超出内存或长期更新的情况。后续可扩大数据规模并持续运行混合负载，观察磁盘读取和旧版本清理的影响。

#pagebreak()
= 07　实现与复现

主程序为 `filmbench.cpp`，使用 Linux C++17、libpq 和 pthread。`--backend` 选择文件、PostgreSQL 或 openGauss，`--op` 选择查询、更新、导入或并发测试。建表 SQL 已内置，完整项目包含数据准备、实验运行和报告生成脚本。

#note[项目地址：#link("https://github.com/luanmenglei/dbh_project1")[github.com/luanmenglei/dbh_project1]]

== 编译与运行

```sh
make
python3 scripts/prepare.py --scales 1
./build/filmbench --backend file --op search_count \
  --data data/generated/s1 --keyword The
./build/filmbench --backend file --op build_index \
  --data data/generated/s1
```

数据库连接参数从环境变量 `FILMBENCH_DSN` 读取。配置连接后，以下命令重建实验 schema、导入数据，再检查外键和回滚。

```sh
./build/filmbench --backend postgres --op load \
  --data data/generated/s1 --schema filmbench_s1 --reset
./build/filmbench --backend postgres --op functional \
  --schema filmbench_s1
```

openGauss 客户端使用其配套 libpq 编译。依赖安装、实例部署和完整实验命令见 README。

== 数据准备和计时

`prepare.py` 从课程 SQL 中提取四张表并生成 TSV。扩容时复制电影、人物和演职关系，给各副本的编号增加偏移量，使关联关系保持对应；`replica` 字段区分不同副本中标题、姓名相同的记录。国家表保持不变。

查询计时包括执行、返回结果和计算结果摘要。当前程序分别记录请求准备、后端准备和查询时间；第 03 节使用原实验的准备总时间。索引建立单独计时，每个规模测一次。两库开启 fsync、同步提交和 full_page_writes；并发延迟用桶宽约 1% 的直方图统计，CPU 时间每 0.5 秒采样一次。文件程序更新姓名时同样先写临时文件，再 fsync 并替换原文件，使两种更新的落盘条件接近。

== 执行计划与实验记录

100 倍人物连接的单次 EXPLAIN 记录中，PG 的 Execution Time 为 0.480 ms，OG 的 Total runtime 为 0.735 ms。EXPLAIN 在另一次连接中执行，记录服务端诊断时间；正文表格使用预热后、包括结果接收的三轮计时中位数。

#tab(("结果目录（均在 results/ 下）", "内容"), (
  ("epyc-compact-evidence", "三个规模的查询、更新与并发测试"),
  ("scaling-probe / scaling-balance\nscaling-confirmation", "调整 CPU 核数及 120 秒测量"),
  ("review-correctness\nreview-coverage-diverse", "独立参考程序、外键、回滚与查询结果检查"),
), columns: (1.3fr, 1fr), numeric: false)

#note[各目录保存原始输出、配置和源码快照。scripts/build_typst_report.py 核对记录后生成图表、编译 PDF，并更新提交文件的 SHA-256 清单。]
