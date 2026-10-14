#!/usr/bin/env python3
"""Create a concise reader-facing report and a detailed learning companion.

Uses existing figures from build_report.py; audits the evidence before authoring.
Requires reportlab; font defaults target the author's Windows workstation.
"""
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from analyze_results import audit

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT/'results/epyc-compact-evidence'
OUT = ROOT/'output'
DATA = audit(EVIDENCE)
pdfmetrics.registerFont(TTFont('TextCN', os.environ.get('FILMBENCH_BODY_FONT', 'C:/Windows/Fonts/simsun.ttc'), subfontIndex=0))
pdfmetrics.registerFont(TTFont('HeadCN', os.environ.get('FILMBENCH_HEADING_FONT', 'C:/Windows/Fonts/simhei.ttf')))
STYLE = {
    'title': ParagraphStyle('title', fontName='HeadCN', fontSize=22, leading=29, textColor=colors.HexColor('#163f56'), spaceAfter=12),
    'h': ParagraphStyle('h', fontName='HeadCN', fontSize=14, leading=21, textColor=colors.HexColor('#163f56'), spaceBefore=8, spaceAfter=8),
    'p': ParagraphStyle('p', fontName='TextCN', fontSize=10.5, leading=17.5, spaceAfter=9, wordWrap='CJK'),
    'small': ParagraphStyle('small', fontName='TextCN', fontSize=8.6, leading=13, spaceAfter=7, wordWrap='CJK', textColor=colors.HexColor('#536773')),
    'cell': ParagraphStyle('cell', fontName='TextCN', fontSize=8.8, leading=12.5, wordWrap='CJK'),
    'code': ParagraphStyle('code', fontName='TextCN', fontSize=9, leading=14, spaceAfter=10, wordWrap='CJK', backColor=colors.HexColor('#eff4f7'), borderPadding=6),
}


def g(b, s, op, kw='', idx=0, c=0):
    return next(x for x in DATA['groups'] if (x['backend'],x['scale'],x['op'],x['keyword'],x['indexed'],x['clients']) == (b,s,op,kw,idx,c))


def val(b,s,op,m='elapsed_s',kw='',idx=0,c=0):
    return g(b,s,op,kw,idx,c)['metrics'][m]['median']


class Document:
    def __init__(self, name, label):
        self.name, self.label = name, label
        self.story, self.text = [], []

    def p(self, text, style='p'):
        self.story.append(Paragraph(escape(text).replace('\n','<br/>'), STYLE[style]))
        self.text.append(text+'\n')

    def h(self,text): self.p(text,'h')

    def paragraphs(self,text):
        for part in text.strip().split('\n\n'): self.p(part.strip())

    def page(self,title):
        if self.story: self.story.append(PageBreak())
        self.h(title)

    def table(self,rows,widths=None):
        widths = widths or [491/len(rows[0])]*len(rows[0])
        t=Table([[Paragraph(escape(str(v)).replace('\n','<br/>'),STYLE['cell']) for v in r] for r in rows],colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8f0f5')),('VALIGN',(0,0),(-1,-1),'TOP'),
                              ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
                              ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
                              ('LINEBELOW',(0,0),(-1,0),.6,colors.HexColor('#9bb4c3')),
                              ('LINEBELOW',(0,1),(-1,-1),.25,colors.HexColor('#dce5eb'))]))
        self.story.extend([t,Spacer(1,8)])
        self.text.extend(' | '.join(map(str,r))+'\n' for r in rows)

    def image(self,name,height,caption):
        self.story.append(Image(str(OUT/'figures'/name),width=491,height=height))
        self.p(caption,'small')
        self.text.append('图像文件：output/figures/'+name+'\n')

    def save(self):
        def footer(canvas,doc):
            canvas.setStrokeColor(colors.HexColor('#d3dfe6'));canvas.line(52,43,A4[0]-52,43)
            canvas.setFont('HeadCN',8);canvas.setFillColor(colors.HexColor('#607582'))
            canvas.drawString(52,29,self.label);canvas.drawRightString(A4[0]-52,29,str(doc.page))
        (OUT/'text').mkdir(parents=True,exist_ok=True)
        (OUT/'pdf').mkdir(parents=True,exist_ok=True)
        (OUT/'text'/f'{self.name}.txt').write_text('\n'.join(self.text),encoding='utf-8')
        doc=SimpleDocTemplate(str(OUT/'pdf'/f'{self.name}.pdf'),pagesize=A4,leftMargin=52,rightMargin=52,topMargin=43,bottomMargin=56,
                              title=self.label,author='',subject='2026-10-12 compact experiment')
        doc.build(self.story,onFirstPage=footer,onLaterPages=footer)
        print(OUT/'pdf'/f'{self.name}.pdf')


def teacher():
    d=Document('report-teacher','电影数据性能实验 | 精简报告')
    d.p('文件与数据库：访问路径与准备成本','title')
    d.p('C++ 文件操作、PostgreSQL 14.24 与 openGauss 6.0.1 的实测比较','small')
    d.paragraphs('''本实验关注两个问题：数据库相对直接文件操作的优势来自哪里，以及两种数据库在相同任务下表现如何。实验发现，文件偏移索引的查询循环很快，但短任务中的准备成本会改变排名；连接操作的主要差距来自扫描与索引的访问路径；PostgreSQL 在本次扫描、聚合及短时并发中较快。这些结果评价具体实现，不构成产品的全面排名。''')
    d.h('1  方法与计时边界')
    d.table([['数据倍率','电影','人物','演职关系'],['1×','9,204','16,489','46,850'],['10×','92,040','164,890','468,500'],['100×','920,400','1,648,900','4,685,000']],[62,140,140,149])
    d.paragraphs('''使用课程电影数据的四张表，countries 固定 185 行，其余表按完整关系复制并平移主外键。副本保留标题和姓名，唯一约束按 replica 隔离。100× TSV 合计约 161.1 MiB；该方法保留了分布，但不代表更多真实样本。

实验运行于 Ubuntu 22.04、双路 EPYC 7542 与 NVMe 存储。数据库/文件使用 CPU 8-15，数据库客户端使用 16-17；两库依次运行。数据库各限 8 GiB，文件及运行器作用域限 4 GiB，因而不声称等内存压力。两库 shared_buffers=512 MB、work_mem=4 MB，关闭查询内部并行，启用 fsync、同步提交及 full_page_writes。openGauss 使用 PG 兼容模式。

C++17 驱动以 steady_clock 计时，并消费全部查询结果。连接、预编译、候选 ID 和文件索引加载计入独立的 setup_s；主计时包含查询执行、结果返回和摘要计算。串行每轮先预热，再测量，重复 3 次；本文报告中位数，图中误差线为最小值至最大值。导入和更新后以行数及内容摘要校验，另用独立 Python 测试覆盖 NULL、转义、更新及恢复。''')
    d.h('2  扫描：返回结果也有成本')
    d.table([['100×，耗时 ms','文件','PostgreSQL','openGauss'],['The 子串计数','529.2','128.1','322.3'],['返回匹配 ID、标题','554.5','209.6','402.7']],[194,99,99,99])
    d.paragraphs('''标题检索区分大小写，使用字面子串 The、Love、Star。100× 的命中数分别为 156,300、8,800、5,000。两库诊断计划均为顺序扫描。The 返回较多行时，数据库的结果返回耗时明显高于仅计数；另外两词返回较少行，两种操作耗时更接近。这里观察到的是扫描、传输与消费的总成本差异，没有证据把收益归因于标题索引。''')

    d.page('3  点查：区分复用索引与一次性短任务')
    d.table([['100×，100 个请求','查询均值 ms/次','准备 s','准备 + 整批 s']]+[
        [label,f'{val(b,100,"point","mean_ms",idx=i):.4f}',f'{val(b,100,"point","setup_s",idx=i):.4f}',f'{val(b,100,"point","setup_plus_elapsed_s",idx=i):.4f}']
        for label,b,i in [('文件扫描','file',0),('文件偏移索引','file',1),('PostgreSQL','postgres',0),('openGauss','opengauss',0)]],[155,112,93,131])
    d.paragraphs('''四种方案使用同一组随机电影 ID。文件扫描每次从头寻找；文件索引将 ID 映射到字节偏移，查询时定位读取；SQL 使用主键索引。文件索引在查询循环中每次约 4.84 μs，但加载候选 ID 和索引后，整批约 0.741 s，高于 PostgreSQL 的 0.569 s。索引构建还需约 0.946 s（仅测 1 次，不在表中）。因此，“查询循环更快”不能直接推出“一次性任务更快”。最后一列按每轮相加后取中位数。''')
    d.h('4  更新、连接和聚合')
    d.image('relational.png',182,'图 1  不含准备的操作耗时；横纵轴均为对数刻度，点为 3 轮中位数，误差线为 min/max。')
    d.paragraphs('''100× 将人物名、姓中的 T 替换为 Ttt，共影响 128,700 行。文件写临时文件并同步、原子替换、同步目录；数据库计入 BEGIN、UPDATE 和 COMMIT。文件 / PostgreSQL / openGauss 分别为 1.828 / 1.472 / 3.162 s。每轮从原始数据恢复，避免再次替换导致工作量变化。

按 peopleid=3442 连接查询，各规模均返回 54 行。100× 文件耗时 1.814 s，两个数据库分别为 1.42 和 1.07 ms。文件扫描 credits 和 movies；两库通过 credits_person_idx 和 movies_pkey 执行嵌套循环。因此，大幅差距主要反映持久索引与全表扫描的差别，不能全部归因于引擎。

国家/年份聚合返回 1,511 组。100× 文件 / PostgreSQL / openGauss 为 722.4 / 234.0 / 345.7 ms。文件逐行解码并维护 std::map，两库采用 HashAggregate。结果支持当前数据库执行路径较快，但更高效的文件解析与哈希聚合可能改变差距。''')

    d.page('5  并发：观察增长趋势，而非宣称最大容量')
    d.paragraphs('''在 10× 数据上测试 1、4、8 个客户端，每线程复用连接和预编译语句。负载为纯主键点查，或约 90% 点查、10% 单行 runtime 更新；每轮恢复数据，预热 5 s、测量 20 s，每组合 3 轮，共 36 轮。客户端为闭环：上一请求完成后才发送下一请求。''')
    d.image('concurrency.png',279,'图 2  吞吐与端到端 P95。P95 使用约 1% 桶宽的直方图估计，先求各轮分位数，再取中位数；误差线为轮次 min/max，不是置信区间。')
    d.paragraphs('''8 客户端时，PostgreSQL / openGauss 的纯读吞吐约为 94,102 / 73,506 ops/s，混合吞吐为 94,283 / 55,003 ops/s；混合 P95 为 0.141 / 0.318 ms。两库吞吐均随客户端增加而增长，PostgreSQL 从 4 到 8 客户端增长趋缓。但客户端只使用两个核，当前证据不能排除客户端瓶颈，因而不把拐点解释为数据库容量上限。

PostgreSQL 的混合吞吐仅比纯读高约 0.2%，结合轮次波动，应视为接近。36 轮未检测到错误；并发吞吐最大相对极差为 26.2%（openGauss 单客户端混合），说明三轮短时测试仍不足以支持精确的极限性能结论。''')
    d.h('6  结论与局限')
    d.paragraphs('''本实验最明确的发现是：访问路径决定了点查与连接的主要差异，而准备成本决定短任务能否兑现索引收益。PostgreSQL 在本次扫描、聚合和并发中表现较好，但结果受版本、实现和资源配置约束。共享服务器并非独占，复制数据分布单一，预热实验不代表冷启动；也未进行崩溃恢复测试。选择方案时，应同时考虑索引复用、更新语义及并发维护需求，而非只比较单项最短耗时。''')
    d.p('证据：2026-10-12 compact 正式运行，共 318 条计时记录，矩阵与汇总审查通过。实现见 src/filmbench.cpp；原始记录、配置和执行计划见 results/epyc-compact-evidence/。','small')
    d.save()


def learning(name='experiment-guide', extension=None):
    d=Document(name,'电影数据实验 | 详细理解与写作手册')
    d.p('从代码到结论：整个实验怎么做、怎么看','title')
    d.p('详细阅读版 · 用于理解、复核和独立写作 · 原实验与 2026-10-13 补实验','small')
    d.h('阅读路线')
    d.paragraphs('''这份材料把已经做过的事情讲清楚，不是另一份需要原样提交的课程报告。教师版展示结论；本版解释设计选择、代码执行顺序、测量边界以及容易误读的地方。你读完后，应能离开本文，用自己的话解释图上的每条曲线。

先读第 1-3 节，知道比较对象、数据和服务器；再读第 4-9 节，掌握每种操作与计时；第 10-13 节解释结果、正确性和证据；第 14 节提供人工写作路径。附录保留全部配置组的主要指标，避免只看正文挑出的几个数字。

本文区分三件事。“实测事实”指原始记录直接支持的数值和执行计划；“解释”指结合代码合理推导的原因；“未证明”指需要额外对照实验才能确认的说法。比如文件连接使用扫描、数据库使用索引是事实；索引是巨大差距的重要原因是有根据的解释；数据库永远比文件快则没有得到证明。''')
    d.table([['先记住的事实','本次实际情况'],['对象','一个 C++ 源文件的文件后端，加 PostgreSQL 14.24 和 openGauss 6.0.1 两个 SQL 后端。'],['数据','4 张电影关系表；1×、10×、100×；并发只测试 10×。'],['原实验','2026-10-12 22:37:48 至 23:31:06，北京时间，约 53 分 18 秒。'],['重复','原普通配置 3 轮，文件索引构建每档仅 1 次；36 轮并发。'],['原证据','873 条命令记录，其中 318 条正式计时；555 项汇总统计核对通过。'],['补实验','2026-10-13 01:33-02:33；另 76 轮，最多 16 个物理核。第 10A-10C 节解释瓶颈验证。']],[110,381])
    d.h('三个容易先入为主的误解')
    d.paragraphs('''第一，数据库本身也使用文件。这里比较的是“自己写的 TSV 操作程序”与“数据库提供的索引、查询和事务执行路径”，不是硬盘与数据库的对抗。

第二，数据库的 CPU 范围有 8 核，不代表单条查询真的用了 8 核。主实验关闭查询内部并行；并发测试通过多个会话增加同时执行的请求。

第三，查询耗时与程序从启动到退出的时间不同。连接、ID 列表、索引加载都需要时间，本实验单独记录，解释结论时必须说明用了哪一种口径。''')

    d.page('1  问题、对象与实验矩阵')
    d.paragraphs('''课程要求既包含文件与数据库比较，也包含两个 DBMS 的比较。仅执行一条 SELECT 后报时间不能回答这两个问题：文件可能没有索引，查询可能只返回一行，数据库连接时间可能比执行还长。因此我们把访问方式、结果大小、更新语义和并发分别展开。

实验不是通用数据库榜单。它测的是这套 C++ 实现、这份电影数据、两个已部署版本与明确参数下的行为。所有跨系统结果先验证逻辑一致，再比较速度。''')
    d.table([['组','任务','正式配置'],['A1/A2','标题包含 The / Love / Star，分别只计数、返回两列','3 后端 × 3 规模 × 3 关键词 × 2 操作 × 3 轮 = 162'],['B','随机主键点查，每轮 100 次','文件扫描、文件索引、PG、OG × 3 规模 × 3 轮 = 36'],['C/D','姓名更新、人物连接、国家年份聚合','3 后端 × 3 规模 × 3 操作 × 3 轮 = 81'],['E/F','纯读与 90/10 混合负载','2 库 × 1 规模 × 3 并发数 × 2 负载 × 3 轮 = 36'],['索引准备','文件偏移索引构建','3 个规模各 1 次 = 3']],[52,203,236])
    d.paragraphs('''合计 162 + 36 + 81 + 36 + 3 = 318 条计时记录。108 个配置组中，105 组各有 3 条记录，另 3 组只有一次索引构建。不要把 873 条命令记录全部称作独立性能样本：其中还有加载、校验、快照、预热和诊断。

精简方案保留三档规模，删减了并发配置。36 轮并发的固定窗口为 36 × (5 + 20) = 900 秒，也就是 15 分钟；总运行 53 分钟还包括导入、恢复、索引构建、完整性校验和串行查询。不能把两者的差都归为程序调度开销。

原 full 配置在脚本中仍存在：串行 5 轮，并发 s1/s100，1/2/4/8/16/32 客户端，预热 30 秒、测量 120 秒。它没有运行，不能当作正式结果。后续瓶颈研究使用另行设计的 76 轮矩阵，须与此处 compact 参数区分。''')
    d.h('为什么不把所有任务合成一个总分')
    d.paragraphs('''一次聚合、一次主键点查和一次批量更新的工作量、返回结果和语义不同。直接平均各操作耗时没有明确业务含义。本实验按任务分别展示：如果实际业务主要是点查，关注索引及连接复用；如果是一次性数据处理，应把准备时间一起看；如果是多用户服务，关注吞吐、延迟和正确性。''')

    d.page('2  数据从哪里来，放大后意味着什么')
    d.paragraphs('''原始输入保存在 data/source/filmdb.sql。prepare.py 不执行整个 SQL 文件，而是解析白名单 countries、movies、people、credits 的字面量 INSERT，拒绝不支持的表达式，检查列数、主键重复和外键。这样可以避开源脚本的方言混用，也避免执行与实验无关的语句。

countries 是国家表；movies 包含电影 ID、标题、国家、年份、片长；people 包含人物 ID、名、姓、出生/死亡年份及性别；credits 用电影 ID、人物 ID、角色建立多对多关系。credits 的复合主键包含角色，同一人物在同一电影中出现多个角色不能简单去重。''')
    d.table([['规模','countries','movies','people','credits'],['1×','185','9,204','16,489','46,850'],['10×','185','92,040','164,890','468,500'],['100×','185','920,400','1,648,900','4,685,000']],[51,80,115,120,125])
    d.paragraphs('''电影 ID 的平移步长为 9,205，人物为 16,490，均等于原始最大 ID 加 1。第 r 个副本使用原 ID + r × 步长；credits 的两类外键同步平移。举例：原电影 ID=1 在第二个副本变为 9,206，相关 credits 必须引用 9,206，而不是还指向原电影。

movies 和 people 增加 replica，原有业务唯一约束将 replica 纳入范围。否则相同标题、姓名等被复制后，可能违反唯一约束。countries 不复制；标题不加后缀，否则子串匹配率会改变。ID 并不保证连续，因此点查从实际 ID 列表抽取，不从一个任意整数区间猜 ID。

TSV 用制表符分列，特殊字符采用 COPY text 风格转义；NULL 是反斜线加 N，空字符串是长度为零的字段。换行和制表符在字段中必须转义，否则一条逻辑记录会被错误拆成多条。C++ 使用 optional<string> 区分 NULL 与空字符串。''')
    d.h('复制数据的收益与限制')
    d.paragraphs('''规模放大让扫描成本变化可见，同时保持关键词比例和表间关系。100× The / Love / Star 命中 156,300 / 8,800 / 5,000 行。人物 3442 仍只代表第一个副本的人物，其连接结果始终 54 行。该人物由准备脚本选择为参演关联数最多者，若并列取最小 ID。

但这是高度相关的复制数据，不是独立采样的新电影。国家/年份分组仍为 1,511 组。连接和聚合的输出规模没有随倍率等比例增加，故它们测的是特定的规模变化方式，不能直接外推到分组数量或热点分布也改变的业务。''')
    d.p('证据位置：各规模 s*-manifest.json 记录原 SQL 与 TSV 的 SHA-256、字节数、行数、步长和预期计数；目标约束见 sql/schema.sql。','small')

    d.page('3  共享服务器上的实际部署与影响边界')
    d.paragraphs('''正式项目位于 /srv/benchmark/filmbench，进程由 benchmark-user 运行。服务器为 Ubuntu 22.04.5、双路 AMD EPYC 7542，共 64 个物理核、128 个逻辑 CPU。数据库和文件程序的范围为逻辑 CPU 8-15，客户端为 16、17；这些编号对应十个不同物理核，未使用其 SMT 兄弟线程 72-81，均在 NUMA 节点 0。

这些设置只规定允许在哪些核执行，没有独占预留；其他人的进程仍可被调度到相同核。内存未强制绑定 NUMA 节点。Nice=10 降低 CPU 调度优先级，也不代表完全不影响其他人。磁盘没有限速，共享缓存和 I/O 仍可能竞争。''')
    d.table([['项目','实际配置'],['数据库资源','每库 MemoryMax=8G、MemorySwapMax=0、TasksMax=256；依次运行。'],['运行器与文件','所在作用域内存上限 4G、TasksMax=128；文件程序使用 CPU 8-15。'],['缓存与查询','shared_buffers=512MB、work_mem=4MB、max_connections=64；PG 并行度参数为 0，OG query_dop=1。'],['持久化','fsync、synchronous_commit、full_page_writes 均开启。'],['网络','PG 127.0.0.1:55432；OG 55433 及内部 55434；不开放远程数据库监听。'],['生命周期','用户级临时服务，完成/失败后停库；未启用开机启动或用户 lingering。']],[100,391])
    d.paragraphs('''没有 apt install、sudo、Docker 或系统级数据库服务。PostgreSQL 软件包使用 dpkg-deb -x 解压到私有目录；openGauss 解压包内的服务器归档。包名虽然带 6.0.0，内部实际版本是 6.0.1，应以二进制和 SQL version() 为准。PostgreSQL 实际版本是 14.24，不能照搬旧 Docker 示例的 18。

部署遇到两项实质兼容问题：openGauss 默认 A 模式将空串当作 NULL，原始数据中的空 surname 因 NOT NULL 约束导入失败，改为初始化 PG 兼容模式；openGauss 包内 C++ 运行库较旧，客户端由宿主编译器构建后需要较新 GLIBCXX，因而客户端 wrapper 优先使用系统 C++ 运行库，服务器仍使用自己的库。没有改全局动态库。

连接密码由环境变量传入，不进入命令行结果记录。connections.sh 为 600，状态目录为 700；它们不应随报告提交。默认 README 的 Docker 路线是历史备选方案，详细部署事实应以用户目录部署记录、installation.json 和正式 run.json 为准。''')

    d.page('3A  内存限额、预热与数据规模意味着什么')
    d.h('保护上限不等于占用，也不是等资源对照')
    d.paragraphs('''数据库服务限 8 GiB，是为共享服务器保留运行余量的保护性配置，不是通过实验找到的最低需求或最优值。文件程序、运行器及数据库客户端所在作用域限 4 GiB。数据库方案有独立的服务作用域，因而两种方案并不拥有相同的总内存预算。限额不是预分配；shared_buffers=512 MB 也不是数据库全部内存占用。

相同限额亦不保证相同内存压力：数据库缓存、操作系统文件缓存、进程工作集和并发数量都不同。没有观察到请求错误，并不能单独证明内存回收从未影响性能。本研究不据此比较单位内存效率，也不声称数据库在相同总内存资源下胜出。''')
    d.h('预热在代码里具体做了什么')
    d.paragraphs('''串行只读查询先执行一次相同操作，再另起进程正式测量；预热结果不纳入三轮统计。前一次访问可能让文件页、数据库数据页和索引页留在缓存中，降低首次加载造成的执行顺序偏差。但两个进程不复用客户端连接，连接和预编译仍在各自 setup_s 中计量。

更新先恢复原始 people 数据，再扫描核对指纹；扫描同时起到预热读取的作用，不提前执行一次更新。并发测试则在同一批连接上预热 5 秒、测量 20 秒，追加长窗口为 10 秒加 120 秒。文件索引构建每档只测一次，不属于三轮预热查询。

预热体现数据已访问过的情形，不保证缓存完全就绪，也不能替代冷启动测量。没有清空共享服务器缓存；重启数据库并不会清空操作系统页缓存。''')
    d.h('为什么有几百万行，仍不称作超内存实验')
    d.p('100× 包含约 92 万电影、165 万人物和 469 万演职关系，但 TSV 总量约 161.1 MiB；行数多不等于字节数超过内存。数据库关系和索引大小又不同于 TSV 字节数。并发只在更小的 10× 数据上运行。因此这组结果适合研究访问路径、解析/执行开销及热数据 CPU 瓶颈，不足以推断冷读、超内存随机 I/O 或生产规模容量。')
    d.p('放大采用复制与主外键平移，保留原分布，不增加独立真实样本。聚合仍为 1,511 组，所选人物连接仍为 54 行；若新业务同时增加分组数、改变热点或倾斜，现有倍率曲线不能直接外推。')

    d.page('4  程序怎样协作，一轮实验怎样走完')
    d.table([['文件','职责'],['prepare.py','解析源数据，生成三档 TSV 和清单。'],['filmbench.cpp','文件操作、SQL 客户端、计时、结果消费、指纹、并发请求。'],['configure_user_instances.py','初始化私有实例、配置参数、编译两个 libpq 客户端；不用于覆盖当前实例。'],['verify_user_instances.py / tests','小规模独立正确性验证，不能把其性能当作正式结果。'],['run_suite.py','检查输入、启停后端、导入、预热、重复、恢复、监控和保存证据。'],['summarize.py / analyze_results.py','汇总统计；对完整矩阵、摘要、统计及源码哈希进行审查。']],[175,316])
    d.paragraphs('''以一个规模为例：先校验每个 TSV 的 SHA-256；建立文件参考指纹和电影偏移索引；依次加载两库，获取版本和配置，核对四张表内容，保存只读 EXPLAIN ANALYZE；建立文件查询参考结果，然后执行 3 轮正式串行比较。

每轮后端顺序轮换，例如 file→PG→OG，下一轮从 PG 开始，以降低总是同一后端先跑的影响。每个只读查询在每次计时前有一次相同查询预热。文件点查包含扫描与索引两个配置。每个计时结果都与文件参考行数和摘要比较。

姓名更新必须单独恢复：文件从原始 people.tsv 复制到工作目录；数据库重建实验 schema 并导入原始数据，再预热读取 people。正式更新后再次检查指纹和受影响行数。恢复、导入、预热不计入更新 elapsed_s，但确实占用总实验时间。

仅在 s10 进入并发阶段。每轮交替 PG 和 OG 的先后；每种负载、每个客户端数都重新加载数据。数据库加载执行建表、COPY、提交和 ANALYZE；ANALYZE 让优化器获得统计信息。最后写 COMPLETE，失败则保留诊断并写 FAILED，退出清理停止服务。''')
    d.h('每个命令的证据怎样保存')
    d.paragraphs('''records.jsonl 每行是一次成功解析的命令结果，含 backend、scale、op、measured、repeat、command、log 和 result。相同 log 前缀对应 stdout、stderr 和 resources.jsonl。measured=false 的诊断或预热不能混入性能中位数。索引构建虽然 measured=true，但没有 3 次重复。

运行时每秒采样 /proc 与可用的数据库 cgroup 计数。主机 CPU、内存、磁盘统计包含其他用户，不能直接称作本数据库利用率；极短查询通常没有足够采样点。memory.peak 可能覆盖服务此前导入阶段，不能无条件当作某个查询的峰值。''')

    d.page('5  标题检索：同一个问题的两种返回方式')
    d.p('SELECT count(*) FROM movies WHERE strpos(title,$1)>0;\nSELECT movieid,title FROM movies WHERE strpos(title,$1)>0;','code')
    d.paragraphs('''$1 是参数，不拼接进 SQL。字面子串意味着 The 与 the 不同，% 和 _ 没有通配符含义。C++ 用 string::find 做同样判断；对本次 UTF-8 数据和这些关键词，与 SQL 结果经过了校验。程序不把匹配行逐条打印到终端，避免终端速度影响比较。

文件端 getline 读取一行，decode 解码字段，检查标题；计数模式最终只把一个数字加入摘要，返回行模式把每个匹配的 movieid/title 加入摘要。它仍会解码整条 TSV 记录，不能把这项成绩当作经过专门优化的列式文本扫描速度。

数据库通过 libpq 的 prepared statement 执行，获取结果并消费所有字段。计数只传回一行；返回行模式要接收和处理大量结果。因此即使服务端过滤方式相同，客户端端到端时间也不相同。''')
    d.image('search.png',188,'两图展示 The：点为 3 轮中位数，误差线为 min/max；纵轴为毫秒，对数刻度。')
    d.paragraphs('''100× 计数时，文件 529.2 ms、PG 128.1 ms、OG 322.3 ms；返回 156,300 行时分别是 554.5、209.6、402.7 ms。PG 返回行比计数约多 81.5 ms，但这是两组中位数的差，只可作描述，不是单独测出的“纯网络时间”：执行、数据表示、客户端摘要等也可能变化。

诊断显示两库均为 Seq Scan，随后计数为 Aggregate。普通索引没有在这次任意位置子串查询中使用。不能说 PG 快是因为用了标题索引，也不能只凭耗时就断言差距具体来自磁盘、缓存或某段内部代码；那些需要更专门的剖析。

The、Love、Star 三组的作用是比较不同结果量；原数据匹配率约 17%、0.96%、0.54%。文件所有行都要解析，所以匹配较少时扫描时间不会按命中率成比例下降。两库返回 Love、Star 的时间接近计数，是与较小返回量一致的现象。''')

    d.page('6  点查：文件索引究竟省掉了什么')
    d.paragraphs('''点查 SQL 返回 movieid、title、country、year_released、runtime，条件为 movieid=$1。文件也只取前五列参与校验，不把 replica 加入一侧输出。每轮从真实 ID 向量用 mt19937_64 和固定种子抽取 100 个请求，可重复命中；通过 rng()%size 取位置，目标近似均匀，并不是顺序遍历全部电影。

扫描版每个请求 clear 并 seekg(0)，从文件开头解码直到找到目标。数据按 ID 排列，对于均匀分布的请求，通常需要看相当一部分记录，因此整批 100 次会反复扫描。文件打开本身在准备阶段，逐次查找在查询阶段。

索引构建先扫描电影文件，用 tellg 记下每一行的字节偏移，写出 ID→offset。文件头带原文件大小和修改时间。查询进程把索引读入 unordered_map，随后哈希定位、seekg、读取一行并校验 ID。它没有把电影正文全部复制进索引，正文读取仍会发生，只是可能命中操作系统页缓存。

索引的大小/修改时间保护用于拒绝常见过期情况，并非完整内容身份认证；正式输入另有 SHA-256 检查。索引没有事务增量维护能力，修改电影文件后需要重建。不能据此把它视为自制数据库已完成。''')
    d.table([['100×，3 轮中位数','每次查询 ms','setup_s','整批总计 s']]+[
        [label,f'{val(b,100,"point","mean_ms",idx=i):.6f}',f'{val(b,100,"point","setup_s",idx=i):.6f}',f'{val(b,100,"point","setup_plus_elapsed_s",idx=i):.6f}']
        for label,b,i in [('文件扫描','file',0),('文件索引','file',1),('PostgreSQL','postgres',0),('openGauss','opengauss',0)]],[155,112,104,120])
    d.paragraphs('''mean_ms = elapsed_s × 1000 / 100。例如文件索引整批约 0.000484 秒，除以 100 是约 0.004844 ms，也就是 4.844 μs。不要把整批时间错写成每次时间，或者把毫秒错当成微秒。

所有点查实现准备时都读取电影 ID 列表，这部分约半秒；索引版还加载索引，setup 中位数约 0.741 秒。于是它查询循环最快，但短批任务含准备后并非最快。PG 和 OG 还包含连接及预编译。表中最后一列是每轮先相加再取中位数，不能用两列中位数相加来保证得到相同结果。

100× 索引额外构建一次约 0.946 秒；构建不在上述总计中。复用更多请求可摊薄准备成本，但当前只有每轮 100 次的实测，不能把简单线性外推当作已跑过的大批次结果。''')

    d.page('7  姓名更新：为什么必须恢复与同步')
    d.p("UPDATE people SET first_name=replace(first_name,'T','Ttt'), surname=replace(surname,'T','Ttt') WHERE strpos(coalesce(first_name,''),'T')>0 OR strpos(surname,'T')>0;",'code')
    d.paragraphs('''更新只针对大写 T，处理名和姓中的所有出现，不是把整个人名替换为 Ttt。小写 t 不变，NULL 的名保持 NULL，空字符串保持空。affected 统计被改变的人物行数，不是替换字符总数；一个人名中多个 T 也只算一行。

这个变换不是幂等：T 第一次变成 Ttt，第二次会变成 Ttttt。所以在已修改数据上重复测试，会同时改变内容和写入量。每轮恢复原始姓名是实验有效性的必要条件，而不是可省略的清理工作。

文件中变长字段不能简单原位覆盖。代码扫描原文件，把更新或未更新的每行都写入新临时文件，flush/close 后 fsync 临时文件，再 rename 替换目标，最后 fsync 父目录。计时包括整个读写同步过程。失败时清理临时文件，不把半成品当作成功结果。

数据库执行显式 BEGIN、参数化 UPDATE、COMMIT，并启用同步提交。数据库可能更新元组、索引并产生 WAL；本次没有逐项测出这些机制各自消耗多少，不能凭差值编造 WAL 写入量。持久化配置和成功提交也不等于已通过真实断电恢复验证。''')
    d.table([['规模','受影响人物','文件 ms','PG ms','OG ms']]+[[str(s)+'×',f'{1287*s:,}']+[f'{val(b,s,"update")*1000:.3f}' for b in ['file','postgres','opengauss']] for s in [1,10,100]],[52,115,108,108,108])
    d.paragraphs('''100× PG 的中位数 1.472 秒低于文件 1.828 秒，OG 为 3.162 秒。这个事实不能简单表述为“数据库只改局部一定更快”：OG 在这里比文件更慢，而且 SQL 实现承担的事务与约束维护工作不同。

两侧完成同一逻辑内容变换，但并非相同物理 I/O 路径。文件每轮重写整个 people.tsv，数据库用自己的更新机制；本实验比较实际方案完成任务的代价，不是每写一个字节的速度。

结果校验先确认更新前文件/表与原始参考一致，更新后核对全部内容指纹，并检查 1×/10×/100× 分别受影响 1,287/12,870/128,700 行。文件前后字节数和数据库关系大小另有记录；它们是逻辑文件/关系占用，不是设备实际累计写入字节。''')

    d.page('8  连接与聚合：读懂访问路径')
    d.p('SELECT m.movieid,m.title,c.credited_as FROM movies m JOIN credits c ON m.movieid=c.movieid WHERE c.peopleid=$1;','code')
    d.paragraphs('''文件端先扫描全部 credits，挑出指定人物的 movieid 和角色，存到 unordered_map<ID, vector<role>>，再扫描 movies 输出匹配项。vector 保留同一电影的多个角色。如果用单一 role 覆盖，会使结果错误，即使程序更快也没有比较意义。

数据库建有 credits(peopleid,movieid) 索引和 movies 主键索引。两库诊断都是 Nested Loop：先用人物索引找到相关 credits，再对每个 movieid 查电影主键。输出固定 54 行，所以库表变大后不必按全表大小扫描。

100× 文件 1,814.34 ms，PG 1.42 ms，OG 1.07 ms。这个数量级差距是临时扫描方案与持久索引方案的比较。若文件也事先维护人物到电影的永久索引，可能显著缩小差距，但那是尚未实现和测量的另一方案。''')
    d.p('SELECT country,year_released,count(*),coalesce(sum(runtime),0),count(runtime) FROM movies GROUP BY country,year_released;','code')
    d.paragraphs('''聚合用国家和年份作为组键。count(*) 数所有电影，count(runtime) 只数片长非 NULL 的电影；sum 只累加非 NULL 值。全 NULL 组用 coalesce 将 sum 表示为 0，同时非空计数为 0；如需平均值，应先检查 count(runtime)>0，否则平均值仍应视为 NULL，而不是 0 分钟。

例如一组片长为 100、NULL、120，输出为 count(*)=3、sum=220、count(runtime)=2，平均应是 110 而不是 73.33。本实验返回整数和计数，避免两个实现的浮点平均舍入误差干扰一致性验证。

文件维护 std::map<pair<country,year>, Group>；两个数据库均采用 Seq Scan + HashAggregate。所有规模返回 1,511 组，100× 三者为 722.42 / 234.02 / 345.67 ms。文件实现的文本解码、字符串处理、有序树维护，与数据库的类型化存储及哈希路径都不同。当前证据不能把全部差值精确分配给某个单一因素。''')
    d.h('为什么不拿 EXPLAIN 的时间替代正式结果')
    d.paragraphs('''EXPLAIN ANALYZE 会实际执行查询并增加诊断统计；它在独立阶段运行，缓存和时机也不同。正确用法是用它确认访问路径、返回行数等，再用正式计时轮次比较端到端时间。不能从计划中选一个更好看的执行毫秒数，替换报告里的 3 轮中位数。''')

    d.page('9  并发负载、吞吐和尾延迟怎么算')
    d.paragraphs('''每个线程建立自己的 libpq 连接并预编译读写语句，然后在条件变量屏障等待。所有线程就绪后统一启动，先共同预热 5 秒，随后测量 20 秒。线程按请求开始时刻决定是否纳入测量：预热结束前开始的请求不计入，结束边界前开始的请求等待完成，吞吐分母包含最后一个请求的排空时间。

纯读每次根据随机电影 ID 查询一行并验证 ID；混合模式约 10% 的请求执行单行 runtime 更新。NULL 或 >=300 设为 1，其余加 1。这是自动提交 UPDATE，与前面的批量姓名更新不是同一种写入。客户端可能随机碰到同一行，相关锁竞争属于负载；没有自动重试隐藏错误。

并发种子为 20261012 + repeat + 线程号；相同配置在不同轮次有不同种子。虽然各库起始种子一致，但闭环请求数量和线程交错受执行速度影响，因此不保证每个库实际完成完全相同的操作序列。90/10 是随机选择概率，不保证测量窗口内恰好 10.000% 写。''')
    d.table([['字段','含义'],['operations / elapsed_s','成功计入直方图的请求数除以测量时长，得到 ops/s；不能用 25 秒作为分母。'],['reads、writes、errors','测量区间成功读写数量与错误；预热失败同样使 valid=false，不能仅看 errors。'],['p50/p95/p99_ms','单轮所有成功请求延迟的近似分位数；单位毫秒。'],['read/write_p95_ms','分别在读请求与写请求中求 P95，不能由总 P95 按比例拆分。'],['checksum_including_warmup','用于证明读结果被消费，含预热；并发间有不同执行次序，不能要求不同后端摘要相同。']],[166,325])
    d.paragraphs('''这是闭环而非固定到达率模型。每线程一次只挂一个同步请求，等待返回后才发送下一个。数据库变慢时，客户端自然少发请求；因此测得的延迟不能直接预测外部请求持续涌入时的排队尾延迟。

直方图按约 1% 的几何桶宽记录，P95 意思是约 95% 请求不超过对应时延。它不是最大值，也不是平均值。串行一轮只有一次的扫描/更新，不适合拿 P95 讲尾延迟；并发含大量请求才有实际意义。图表先求每轮 P95，再对 3 个 P95 取中位数，不是把所有轮次请求合并排序。

以 PG 8 客户端纯读为例，吞吐约 94,102 ops/s；OG 为 73,506，二者比约 1.28。PG 混合约 94,283，OG 55,003，比约 1.71。混合 P95 为 0.141/0.318 ms；写请求 P95 为 0.180/0.441 ms。读写尾延迟的差异不能只从总吞吐推断。''')

    d.page('10  怎样解释图，怎样避免过度解释')
    d.image('concurrency.png',279,'并发曲线：3 轮中位数与 min/max。上排纯读，下排约 90% 读 / 10% 写。')
    d.paragraphs('''原曲线显示两库从 1 到 8 客户端总体都有吞吐增长，PG 4→8 的增长较小。初测不能区分数据库饱和与两核客户端饱和。后续通过分别增加两端核心并记录 CPU 计数，确认 PG 原平台主要来自发压端；具体设计与结果紧接本节展开。因此不再将客户端瓶颈仅列为待验证猜测。

PG 混合中位吞吐比纯读约高 0.2%，远不足以推出“写入让数据库变快”。它们属于不同随机工作负载和时段，重复间波动、运行状态都会影响小差异；合理表达是两者在当前精度下接近。

三轮样本不适合给出很精确的总体置信区间。误差线只表示这三轮的范围。并发最大相对极差 (max-min)/median 为 26.2%，来自 OG 单客户端混合；最快约 8,059、最慢约 6,245 ops/s。保留这个波动比只展示最快一次更有信息。

1× PG 连接从约 1.38 到 3.98 ms，虽然绝对差只有几毫秒，相对波动很大。若两库的单次连接只差零点几毫秒，不宜给出绝对排名。放大规模后扫描与索引的数量级差距则远大于这些短时波动，结论更稳妥。''')

    if extension:
        extension(d)
    d.page('11  正确性、统计和资源证据的可信范围')
    d.paragraphs('''第一层是输入身份：manifest 中的 SHA-256 与输入文件逐一核对，避免使用被上次更新污染的数据。第二层是导入一致性：每张表计算行数和内容指纹，数据库指纹通过只读可重复读事务的游标分批提取，每批 10,000 行，避免全部结果驻留客户端。

第三层是查询结果：每行以 NULL 标记、字段长度和内容编码后做 FNV-1a，再对行哈希做模 2^64 加和。这样不依赖返回行顺序，但重复行会被多次计入；行数也单独比较。NULL 与空字符串、ab+c 与 a+bc 不会因为编码边界模糊而直接等同。不过哈希有理论碰撞风险，不能当作数学上的无碰撞证明。

第四层是独立测试：Python oracle 覆盖特殊字符、Unicode、空串、NULL、多角色、全 NULL 聚合、更新与恢复，文件索引过期拒绝等。数据库并发测试验证请求成功、行数和返回 ID；它不是完整的并发可串行化证明，也没有故障注入。

第五层是完成后的审查：analyze_results.py 重新构造期望矩阵，检查没有漏组或多组、重复编号完整，确认 measured 记录有效、27 组只读结果摘要跨后端一致、更新行数符合清单。它还逐项重算 555 行 summary.csv 的中位数和最小/最大值，并核对正式记录中的五个源码/DDL 文件哈希。''')
    d.h('一行汇总应怎样读')
    d.p('backend=postgres, scale=100, op=point, indexed=0, clients=0, metric=mean_ms, trials=3','code')
    d.paragraphs('''这表示 SQL 主键点查在 100× 下有 3 轮，每轮内部 100 个请求。CSV 的 median 是三个“每轮请求均值”的中位数；不是直接把 300 个请求排序的中位延迟。indexed=0 在数据库行表示未使用“文件索引选项”，并不表示 SQL 没有索引。

资源日志中的主机磁盘计数可能含其他用户；数据库关系大小包括不同类别对象时也不能等价为物理磁盘写放大。memory.current 是采样时刻值，memory.peak 可能是整个服务生命周期最大值。本文不把这些未完成归因的数字加工成精确“每个查询 CPU 或 I/O 用量”。

完成标记是必要证据，不是全部证据：它要与原始记录、无失败标记、重复矩阵、有效性和结果校验共同看。启动服务成功也不能证明实验完成；已回收的临时服务状态更不能代替保存的结果。''')

    d.page('12  本次结果能回答什么，不能回答什么')
    d.table([['可以据此回答','不能据此回答'],['当前 TSV 扫描、索引和 SQL 路径在给定任务下耗时多少。','任何文件实现都一定输给任何数据库。'],['文件偏移索引的准备成本会改变 100 请求短批次的排名。','任意请求数或长期复用场景的准确盈亏平衡点。'],['两库的这次连接计划都使用索引，文件实现则扫描。','连接的全部收益都来自 DBMS 内核，而与索引无关。'],['当前参数下 PG 的扫描、聚合、并发中位表现较好。','PG 对所有业务、所有版本都优于 OG。'],['8 客户端以内存在增长趋势，36 轮未检测到错误。','数据库已到达最大容量，或能够长期保持同等吞吐。'],['更新包含提交/同步，原始数据每轮恢复。','已验证断电恢复、事务隔离全部性质或硬件持久性。']],[245,246])
    d.paragraphs('''还有几项公平性边界应主动理解。CPU 范围相近不等于实际用量相同，文件主流程为单线程。数据库 8 GiB 与文件作用域 4 GiB 不是相同总内存预算。当前数据量小，不能把结果解释为超内存压力实验。

数据库与文件内部结构不同是本实验要研究的实际方案差异；但若想单独研究“执行引擎”，就要进一步控制索引、算法、数据表示和缓存，当前没有做到这一点。libpq 也有客户端/协议开销，文件调用没有相同通信路径。

串行固定随机种子让工作负载更可比，却只覆盖一组请求序列。后端顺序有轮换，但没有完全随机交错；三档规模顺序运行，仍可能受共享服务器时段变化影响。磁盘和 OS 页缓存没有清空，重启实例也不等于冷缓存。''')
    d.h('如果以后要继续研究，优先做什么')
    d.paragraphs('''客户端瓶颈已经通过补实验验证，不再列为尚未执行的建议。仍未定位的是两库每请求 CPU 成本差异的具体内部来源；这需要更细的剖析。若改问内存效率，则应统一总预算并改变数据工作集与内存的比例。一次只改变关键因素，比直接扩大所有规模更能解释原因。

这些更细的内部剖析、内存对照和故障恢复测试尚未执行。完成课程报告不要求补跑完整 full 矩阵；若扩展问题，应另行设计并考虑共享服务器上的可接受影响。''')

    d.page('12A  回到作业问题：数据库究竟提供什么优势')
    d.p('不能把“文件没有索引”作为结论前提，因为本项目已经实现文件偏移索引，而且它的热查询循环比 SQL 点查更快。数据库本身也使用文件；真正比较的是应用自行组织数据访问，与数据库提供的一整套查询和维护机制。')
    d.table([['观察或机制','本次证据','能得出的结论'],['索引访问','文件偏移索引点查很快；数据库连接计划使用持久索引。','访问路径及复用决定收益，索引不是数据库独有。'],['准备与维护','文件短批次含准备后排名改变；文件改变后索引需重建。','应用实现还要承担结构加载与维护，不能只比较热循环。'],['声明式查询','SQL 表达过滤、连接、聚合，数据库选择计划。','应用少写针对各任务的访问算法，但未量化开发工时。'],['关系约束','DDL 集中定义主键、外键和唯一约束。','数据库方案使用了约束机制；未全面测试所有约束边界。'],['事务与恢复','SQL 更新在事务中提交，持久化开关开启；文件采用同步替换。','比较了当前更新路径，没有验证完整隔离、崩溃恢复保证。']],[96,196,199])
    d.paragraphs('''文件程序当然也能继续增加并发控制、增量索引和恢复日志，但需要自行设计它们如何协作。数据库将这些职责集中管理，是本作业中比“某个查询更快”更完整的功能解释。这里的功能分析来自实现与配置，不应伪装成已经测出了可靠性或开发成本收益。

“哪个数据库更好”必须先给标准。若标准是本次预热电影数据上的扫描、更新、聚合耗时和点查并发吞吐，PG 多数配置较好；OG 的 100× 连接中位数较低，但两者都很短，不能据此推导普遍优势。若标准改为恢复、运维、复杂查询或内存效率，目前不能排名。

因此完整回答应包含三层：先给当前实现的实测差异，再用访问路径与资源干预解释主要原因，最后限定结论范围。当前数据足以展示这些机制，却没有覆盖超内存 I/O；当前更新包含同步，却没有故障注入；当前并发定位了两端瓶颈，却没有定位具体内核函数。''')

    d.page('13  如何定位证据、复现分析和查代码')
    d.paragraphs('''本地证据目录是 results/epyc-compact-evidence，服务器正式目录是 /srv/benchmark/filmbench/results/epyc-compact。它们是同一次正式运行的结果副本；不要与 user-smoke、user-correctness 混淆，后两者用于验证而非正式性能结论。

run.json 说明实际参数和后端配置；host.json 记录主机、CPU、磁盘和编译器；s*-manifest.json 说明数据身份；records.jsonl 是逐次命令结果；summary.csv 是聚合视图；analysis.json 是本地审查产物；formal-source-sha256.txt 是启动时源码与二进制哈希。每个 records 条目的 log 可定位对应 stdout、stderr、资源采样。''')
    d.p('python scripts/analyze_results.py results/epyc-compact-evidence\npython scripts/build_integrated_reports.py','code')
    d.paragraphs('''以上命令读取本地证据并生成审查/文档，不启动数据库。整合版还需要 results/scaling-probe、scaling-balance、scaling-confirmation 及已有图表；原图和补实验图分别由 build_report.py 与 plot_scaling_probe.py 生成。依赖 reportlab、matplotlib 及中文字体，默认使用 Windows 宋体/黑体。服务器实验二进制是 Linux 程序，不能直接在 Windows 本地运行。

重新正式测量则完全不同：run_suite.py 的 load --reset 会重建实验 schema，configure_user_instances.py 用于新实例而不是覆盖已配置状态。重跑必须用新的输出目录，核对 CPU/内存限制、数据和目标连接。本文不自动重启数据库，也不建议为修改报告重新跑整套实验。''')
    lines=(ROOT/'src/filmbench.cpp').read_text(encoding='utf-8').splitlines()
    loc=[]
    for token,desc in [('static Row decode','TSV 解码与 NULL'),('struct Digest','结果摘要'),('struct DB','libpq 连接与调用'),('static std::string query','全部 SQL'),('static uint64_t file_update','持久化文件更新'),('static void build_index','文件索引构建'),('struct FileIndex','索引加载和点查'),('static void file_query','扫描、连接、聚合'),('struct Histogram','延迟直方图'),('static void concurrent','并发线程和计时窗口'),('int main','命令分派与串行计时')]:
        n=next(i for i,s in enumerate(lines,1) if token in s);loc.append([desc,f'src/filmbench.cpp:{n}'])
    d.table([['阅读目的','当前源码位置']]+loc,[225,266])

    d.page('14  怎样据此写出你自己的报告')
    d.paragraphs('''建议先合上教师版，用一页纸写下：我比较了什么、为什么这样比较、哪个结果改变了原先直觉、证据在哪里、还有什么不确定。再打开附录核对数值。这样最终文本来自你的理解，而不是机械替换自动草稿的词语。

第一段只说实验要回答的问题和最明确的发现。方法部分保留会影响结果的条件：数据放大方法、实际版本、资源范围、预热、重复、计时边界和结果校验。不要把 SSH、解压软件包和每次排错的经过全部写进正文；除非某个兼容问题改变了数据语义，比如 openGauss 的空串处理。

结果部分可以围绕三个问题写：索引点查为何快但短任务未必快；连接为什么差三个数量级而不能称作纯引擎差距；PG 的并发表现较好但为何不能说已测出上限。每个段落按“具体数值 → 实现/计划证据 → 有限结论”的顺序，不必逐行复述所有表格。

读图时先注明坐标单位与误差线含义。需要写加速比时明确分子和分母，例如 OG 耗时/PG 耗时才是 PG 相对 OG 的耗时加速比；吞吐比则是 PG ops/s / OG ops/s。避免把几十微秒的小差异描述成巨大提升，也不要把 min/max 写成置信区间。''')
    d.h('写完前，尝试不看资料回答这些问题')
    d.paragraphs('''为什么查询平均 4.84 μs 的文件索引，100 次短任务却比 PG 慢？为什么 100× 时连接仍然只返回 54 行？为什么姓名替换必须每轮恢复？count(*) 和 count(runtime) 有什么区别？为什么 PG 的混合吞吐略高于纯读不能说明写入有益？为什么 8 个客户端不等于 8 个发压 CPU 核？

参考要点分别是：准备与构建成本；人物 ID 只属于一个副本；替换非幂等；NULL 不参与后者；小差异处于波动和工作负载差异中；客户端线程受两个核的亲和性限制。能解释这些，比背一张性能表更能体现对实验的理解。''')
    d.h('个人贡献与 AI 使用如何写')
    d.paragraphs('''这两份文档由 AI 根据代码和实测记录辅助整理，不应把它们标为人工原创。你的报告可以基于对材料的理解重新组织论证，并按实际参与情况说明设计取舍、阅读与核对工作、代码或配置修改。不要把自动执行的所有步骤描述为自己逐条手写，也不要凭空添加主观经历。

姓名、学号、个人贡献应由你本人填写。若课程要求披露辅助工具，按实际使用范围说明。课程截图强调内容相关、有用、简洁；满足这一要求的关键是保留真实发现与证据，不是把文章换成某种口吻。最终分别提交 report.pdf 与 C++ 源文件，不把带密码的部署目录或完整运行包混入作业。''')

    for scale in [1,10,100]:
        d.page(f'附录 A{scale}  {scale}× 串行配置完整结果')
        d.p('每格为“中位数 [最小值, 最大值]”，单位 ms；每组 3 轮。点查使用每轮 mean_ms（100 请求的均值），其他操作使用 elapsed_s × 1000。准备时间不含在本表。PG=PostgreSQL；OG=openGauss。','small')
        rows=[['操作 / 条件','文件','PG','OG']]
        def cell(b,op,kw='',idx=0,metric='elapsed_s'):
            m=g(b,scale,op,kw,idx)['metrics'][metric];factor=1 if metric=='mean_ms' else 1000
            return f'{m["median"]*factor:.4f}\n[{m["min"]*factor:.4f}, {m["max"]*factor:.4f}]'
        for kw in ['The','Love','Star']:
            for op,lab in [('search_count','计数'),('search_rows','返回行')]: rows.append([f'{kw} {lab}']+[cell(b,op,kw) for b in ['file','postgres','opengauss']])
        rows.append(['点查 / 扫描或 SQL']+[cell(b,'point',metric='mean_ms') for b in ['file','postgres','opengauss']])
        rows.append(['文件索引点查',cell('file','point',idx=1,metric='mean_ms'),'不适用','不适用'])
        for op,lab in [('update','姓名更新'),('join','人物连接'),('aggregate','国家年份聚合')]:rows.append([lab]+[cell(b,op) for b in ['file','postgres','opengauss']])
        d.table(rows,[116,125,125,125])
        d.h('点查准备与文件索引构建')
        d.table([['配置','setup 中位数 s','setup + 整批中位数 s']]+[[lab,f'{val(b,scale,"point","setup_s",idx=i):.6f}',f'{val(b,scale,"point","setup_plus_elapsed_s",idx=i):.6f}'] for lab,b,i in [('文件扫描','file',0),('文件索引','file',1),('PG','postgres',0),('OG','opengauss',0)]],[145,164,182])
        d.p(f'文件索引构建 {val("file",scale,"build_index"):.6f} s，仅一次测量，不计算波动；构建不包含在点查准备或查询时间中。','small')

    for op,title in [('concurrent_read','纯读'),('mixed','90/10 混合')]:
        d.page('附录 B  10× 并发：'+title)
        d.p('下表每行是一轮原始记录，吞吐单位 ops/s，延迟单位 ms。保留全部 18 轮，不只列中位数。repeat 为从 0 开始的轮次编号；预热 5 s，测量约 20 s。','small')
        records=[json.loads(line) for line in (EVIDENCE/'records.jsonl').read_text().splitlines()]
        rows=[['后端','客户端','轮次','吞吐','P95','P99','写 P95']]
        for r in sorted([r for r in records if r['measured'] and r['op']==op],key=lambda r:(r['backend'],r['clients'],r['repeat'])):
            v=r['result'];rows.append(['PG' if r['backend']=='postgres' else 'OG',r['clients'],r['repeat'],f'{v["throughput_ops_s"]:,.1f}',f'{v["p95_ms"]:.4f}',f'{v["p99_ms"]:.4f}',f'{v["write_p95_ms"]:.4f}' if op=='mixed' else '无写入'])
        d.table(rows,[46,52,42,106,81,81,83])
        d.paragraphs('''每轮均 errors=0、valid=true。纯读没有写请求，write_p95_ms=0 是输出中的“无样本”占位，不代表存在零延迟写入。混合读、写、总请求的延迟分布不同，若要继续细分，可读取同一原始记录中的 read_p95_ms 与 reads/writes。

复核方法：任选一个后端与客户端数，把三轮吞吐从小到大排列，取中间值，应与正文/图中的中位数一致。P95 列同样按轮次取中位数，不按各轮请求量加权。原始结果保留更高精度，表中仅为阅读做四舍五入。''')
    d.save()


if __name__=='__main__':
    teacher()
    learning()
