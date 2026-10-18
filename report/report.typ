// DBMS 的优势与性能比较：数据来自同目录 data.json，图为 figures/ 下的矢量图。
#let d = json("data.json")
#let ink = rgb("252525")
#let muted = rgb("656565")
#let body-font = sys.inputs.at("font", default: "Microsoft YaHei")
#set document(title: "DBMS 的优势与性能比较", author: sys.inputs.at("name", default: "易楚曦"))
#set text(font: body-font, size: 10.5pt, fill: ink, lang: "zh", region: "CN")
#set page(paper: "a4", margin: (x: 21mm, y: 20mm), numbering: "1", number-align: right + bottom)
#set par(justify: true, leading: .8em, spacing: 1em)
#set heading(numbering: none)
#show heading.where(level: 1): set text(size: 17pt, weight: "bold")
#show heading.where(level: 2): set text(size: 11pt, weight: "bold")
#show heading: set block(above: 1.3em, below: .8em)
#set raw(theme: none)
#show raw: set text(font: ("Consolas", body-font), size: 8.2pt)
#show figure.caption: set text(size: 8.7pt, fill: rgb("4b4b4b"))
#show figure.caption: it => align(left, it)
#show figure.where(kind: table): set figure.caption(position: top)
#set figure(gap: 2mm)
#let note(body) = block(above: 2mm)[
  #set par(justify: false, leading: .6em)
  #align(left, text(size: 8.5pt, fill: rgb("505050"), body))
]
#let tab(headers, rows, caption: none, key: "", notes: none, columns: auto, numeric: true) = [
  #figure(
    block(width: 100%)[
  #set text(size: 9pt, number-type: "lining", number-width: "tabular")
  #set par(leading: .45em)
  #table(
    columns: if columns == auto { (1fr,) * headers.len() } else { columns },
    align: (x, y) => if numeric and x > 0 { right } else { left },
    stroke: none, inset: (x: 5pt, y: 6pt),
    table.hline(y: 0, stroke: .7pt + ink),
    table.header(..headers.map(x => strong(x))),
    table.hline(y: 1, stroke: .4pt + rgb("aaaaaa")),
    ..rows.flatten().map(x => {
      let parts = str(x).split("\n")
      if parts.len() == 1 { parts.first() } else {
        [#parts.first() #linebreak()#text(size: if numeric { 8.2pt } else { 9pt }, fill: if numeric { rgb("505050") } else { ink }, parts.last())]
      }
    }),
    table.hline(y: rows.len() + 1, stroke: .4pt + rgb("aaaaaa")),
  )
  #if notes != none { note(notes) }
  ], kind: table, supplement: [表], caption: caption)
  #label(key)
]
#set heading(numbering: "1.1")

#text(size: 24pt, weight: "bold")[DBMS 的优势与性能比较]
#v(2mm)
#text(size: 9pt)[姓名：#sys.inputs.at("name", default: "易楚曦") #h(30mm) 学号：#sys.inputs.at("student-id", default: "12510903")]



*GitHub 项目* #h(1fr) 

#link("https://github.com/luanmenglei/dbh_project1")[github.com/luanmenglei/dbh_project1]

= 结果

*1. 相比直接操作文件，DBMS 的独特优势是什么？*

*DBMS 提供现成的关联约束、事务回滚和查询功能，减少应用程序自行实现和维护这些功能的工作。*

实验验证了以下能力：

- *引用约束：* 两库都拒绝插入引用不存在电影的演职记录，避免产生无效关联。
- *事务回滚：* 上述插入失败后，回滚也撤销了事务中先前完成的片长修改。
- *索引支持：* 两库提供现成的索引与查询机制；本实验中，添加人物索引显著缩短了关联查询耗时。

这些能力也可以用文件和代码自行实现，但需要额外维护。实际上，在本实验中，已装载索引的文件程序按编号读取单条记录比使用数据库更快。

*2. PostgreSQL 和 openGauss 哪个更好？按什么标准？*

*综合本实验的耗时、每秒完成的操作数和 P95 延迟，PostgreSQL 整体表现更好。*

#figure(
  table(
    columns: (2.4fr, 1.2fr, 1.2fr, 1.4fr),
    align: (left, right, right, left),
    inset: 6pt,
    stroke: none,

    table.hline(stroke: 0.8pt),
    table.header(
      [*指标与条件*],
      [*PostgreSQL*],
      [*openGauss*],
      [*本次表现*],
    ),
    table.hline(stroke: 0.5pt),

    [100× 标题子串计数耗时],
    [128.13 ms], [322.35 ms], [PostgreSQL 更快],

    [100× 固定返回 54 行的连接耗时],
    [1.42 ms], [1.07 ms], [openGauss 更快],

    [10×、8 客户端混合负载吞吐量],
    [9.43 万次/秒], [5.50 万次/秒], [PostgreSQL 更高],

    table.hline(stroke: 0.8pt),
  ),
  caption: figure.caption(
    position: top,
    [两库代表性结果；完整指标见后文],
  ),
  supplement: [表],
)

= 实验

实验采用课程电影数据 `filmdb.sql`，包含国家、电影、人物和演职关系四张表。
后三表扩展为原来的 $1, 10, 100$ 倍，并平移编号以保持关联关系。
复制后的数据保留原字段分布，规模见 @tab-data。

#tab(("规模", "电影", "人物", "演职关系"), (
  ("1×", "9,204", "16,489", "46,850"),
  ("10×", "92,040", "164,890", "468,500"),
  ("100×", "920,400", "1,648,900", "4,685,000"),
), caption: [数据规模与记录数；100 倍 TSV 合计约 161.1 MiB], key: "tab-data")

测试使用 PostgreSQL 14.24（下称 PG）和 openGauss 6.0.1（下称 OG），运行于 Ubuntu 22.04 服务器，硬件为双路 AMD EPYC 7542 和 NVMe 固态硬盘。
两库轮流运行，各限 8 GiB 内存；文件程序、客户端与脚本共用 4 GiB。
关闭单条查询内部的并行执行，基础配置将数据库与客户端分别绑定 8 核与 2 核。

*实现。* 主程序使用 C++17，数据库端通过 libpq 执行 SQL，文件端读取 TSV（以制表符分隔的文本文件）。
标题搜索区分大小写，两端分别使用 `strpos(title,$1)>0` 和 `string::find`，返回计数或电影编号与标题。
姓名更新均将名字和姓氏中的大写 T 替换为 Ttt；数据库提交事务，文件端重写临时文件、同步落盘后替换原文件。

*测量与核验。* 正式计时前先运行查询，使缓存进入稳定状态，即“预热”。串行查询预热一次；并发预热 5 秒，再测量 20 秒。通常每组测三轮，报告中位数及最小值至最大值。
计时包含查询执行、结果接收及校验值计算，不含数据导入及连接准备；下文称为端到端耗时。
三个实现通过结果校验值（内容摘要）核对查询与更新结果。每轮更新前恢复原数据，两库开启同步提交与 `fsync`，保证提交时同步落盘。

#[
  #set text(size: 9pt)
  #set par(justify: false, leading: .6em)

  #figure(
    table(
      columns: (1fr, 5fr),
      align: left,
      inset: 4pt,
      stroke: none,

      table.hline(stroke: 0.8pt),
      table.header(
        [*实验*],
        [*比较方法*],
      ),
      table.hline(stroke: 0.5pt),

      [标题搜索],
      [搜索标题包含 The、Love、Star 的电影，比较计数与返回编号、标题的耗时。],

      [批量更新],
      [将姓名中的 T 替换为 Ttt，比较文件重写与数据库更新的耗时。],

      [单条记录查询],
      [每轮随机查 100 个编号，比较文件扫描、文件索引及两库主键查询；另做每轮十万次补测。],

      [人物关联查询],
      [按人物编号连接演职关系表与电影表；三个规模均固定返回 54 行。],

      [同库索引对照],
      [固定数据与查询，五轮交替增删数据库中的人物索引，比较有、无索引的耗时。],

      [分组聚合],
      [按国家和年份统计电影数、非空片长总和及数量。],

      [并发只读],
      [将客户端数从 1 增加到 32，测量吞吐量与 P95 延迟。],

      [CPU 资源干预],
      [分别增加客户端与数据库绑定的 CPU 核数，观察吞吐变化。],

      [混合读写],
      [10× 数据、8 个客户端下，比较只读与约 90% 读、10% 同步写的负载。],

      [外键与回滚],
      [先修改片长，再插入非法演职记录；两库拒绝插入，回滚后片长及四表内容摘要恢复。],

      table.hline(stroke: 0.8pt),
    ),
    caption: figure.caption(
      position: top,
      [实验设计概览],
    ),
    supplement: [表],
  )
]

== 访问方式对查询性能的影响

#figure(image("figures/serial.svg", width: 94%), caption: [数据规模与四类操作的耗时。横纵轴均为对数刻度，各面板纵轴范围不同；点为三轮中位数，误差线为最小值至最大值。人物连接在三个规模下均返回 54 行。]) <fig-serial>

人物关联查询中，文件程序依次扫描演职关系表与电影表；数据库先经人物索引定位演职记录，再用电影主键读取详情。
100 倍下文件端约 #d.serial.at(2).at(1).split("\n").first() ms，数据库索引连接约 1 ms。
三个规模下均为 openGauss 略快于 PostgreSQL。

为单独验证索引的作用，另在 100 倍数据下固定同一查询，五轮交替增删数据库中的人物索引，每次预热后计时，导入与建索引不计入耗时。
PG 的有、无索引耗时中位数分别为 1.43、345.48 ms，OG 为 1.24、703.52 ms；逐轮加速比的中位数分别为 243.3、566.9 倍。
这说明人物索引是该查询高效的关键；这组补测与图中的原实验分开统计。

标题计数和分组统计均需遍历电影表。100 倍下，PG 分组统计耗时为文件端的约三分之一。

== 已装载索引下的单条记录读取

按编号查询单条记录称为“点查”。文件偏移索引记录每个编号对应的文件位置，查询时直接跳到该位置读取；数据库使用主键查找。
@tab-point-short 中，文件扫描平均单次约 263 ms，已装载索引的文件查询约 4.84 μs。

#tab(("后端 / 访问路径", "平均单次 / μs", "三轮范围 / μs"), d.point.map(row => (row.at(0), row.at(1).split("\n").first(), row.at(1).split("\n").last())), columns: (1fr, 1fr, 1.3fr),
caption: [短批次主键点查：100 倍数据，每轮 100 次请求], key: "tab-point-short",
notes: [三轮使用同一随机序列，报告每轮平均单次耗时的中位数及范围。计时包含逐次随机选择编号和转换字符串，不含候选编号读取、连接及索引准备；文件索引建立另需 #d.index_build s（单次测量）。])

== 扫描、更新与混合负载比较

*在预热后的全表子串搜索、分组聚合及批量字符串替换中，100 倍规模下 PG 的端到端耗时均低于 OG。*

#tab(("操作", "文件 / ms", "PG / ms", "OG / ms"), d.serial, columns: (1.3fr, 1fr, 1fr, 1fr),
caption: [四类串行操作的耗时：100 倍数据], key: "tab-serial",
notes: [三轮操作耗时的中位数，下行为［最小值，最大值］。
本表与混合负载来自第一批实验，客户端 CPU 绑定与 @sec-concurrency 不同，两批分别比较。])

=== 标题搜索与结果集大小

#tab(("关键词", "返回行数", "文件 / ms", "PG / ms", "OG / ms"), d.search,
caption: [返回电影编号与标题的搜索：100 倍数据，三轮耗时中位数], key: "tab-search")

@tab-search 的三组查询都扫描电影表，PG 均较快。PG 的 The 返回记录耗时 #d.search.at(0).at(3) ms、仅计数 #d.serial.at(0).at(2).split("\n").first() ms，
说明接收和处理结果也有成本。Love 和 Star 返回行数相差 3,800 行而耗时接近。

=== 批量更新的端到端耗时

把姓名中的大写 T 替换成 Ttt，100 倍数据共修改 128,700 行。文件重写整张人物文件，同步后替换原文件；
数据库更新匹配记录并提交事务。@tab-serial 中 PG 最快，OG 慢于文件端。

=== 读多写少负载的吞吐量与尾延迟

#tab(("负载与指标", "PostgreSQL", "openGauss"), d.mixed_metrics.map(row => row.map(x => x.split("\n").first())), columns: (1.6fr, 1fr, 1fr),
caption: [只读与混合负载：10 倍数据，8 个客户端], key: "tab-mixed",
notes: [各项为三轮中位数。P95 先逐轮分别统计读、写请求，再取中位数，未合并样本。
混合负载约 90% 为主键读取、10% 为单行片长更新，每次更新单独同步提交；属第一批实验。])

@tab-mixed 显示，加入少量同步写后，两库的读 P95 都上升，OG 的混合吞吐下降更明显。
PG 的两种负载吞吐接近，但读 P95 仍由 0.106 ms 升至 0.121 ms，说明少量写入也会影响读请求的延迟。

== 并发扩展与 CPU 瓶颈分析 <sec-concurrency>

*在数据库绑定 8 核、客户端绑定 2 核的并发只读下，增加可用 CPU 核数的实验显示，高并发时 PostgreSQL 的吞吐主要受客户端 CPU 限制，openGauss 主要受数据库 CPU 限制。*

#figure(image("figures/concurrency.svg", width: 100%), caption: [并发只读扩展：10 倍数据，数据库绑定 8 核、客户端绑定 2 核。点为三轮中位数，误差线为最小值至最大值。每个客户端收到结果后再发下一次请求；P95 先逐轮计算，再取中位数。]) <fig-concurrency>
#figure(image("figures/interventions.svg", width: 100%), caption: [CPU 资源干预：10 倍数据，与 @fig-concurrency 同批；各条件查询与数据库设置相同。点为三轮中位数，误差线为最小值至最大值，两面板纵轴一致。标注倍数为逐轮配对比值的中位数。]) <fig-interventions>

@fig-concurrency 中，并发从 1 增加到 16 时 PG 吞吐上升；高并发时吞吐趋于平台，P95 延迟随之上升。
32 客户端纯读下，PG 客户端用了 #d.cpu.at(0).at(1) 核、数据库仅 #d.cpu.at(0).at(2) 核；
OG 客户端 #d.cpu.at(1).at(1) 核、数据库 #d.cpu.at(1).at(2) 核，接近 8 核上限。
这里的核数是平均 CPU 用量，表示相当于多少个核心持续工作；由每 0.5 秒采样估算，剔除启动与预热，其窗口与吞吐测量不完全重合。

@fig-interventions 的 32 客户端组中，客户端从 2 核增至 4 核，PG 吞吐由 #d.interventions.at(0).at(1) 升至 #d.interventions.at(1).at(1) 万次/秒，OG 变化较小；
随后数据库从 8 核增至 12 核，OG 由 #d.interventions.at(1).at(2) 升至 #d.interventions.at(2).at(2) 万次/秒，PG 仅小幅上升。
延长至预热 10 秒、测量 120 秒的单轮确认得到相同趋势，详细记录见仓库。
