#!/usr/bin/env python3
"""Content revision only: integrated reader report and updated learning guide."""
import statistics as st
from build_report_versions import Document, ROOT, val, learning
from analyze_scaling_probe import analyze

MAIN=analyze(ROOT/'results/scaling-probe')
BALANCE=analyze(ROOT/'results/scaling-balance')
LONG=analyze(ROOT/'results/scaling-confirmation')


def v(layout,backend,metric='qps',clients=32,data=MAIN):
    g=next(g for g in data['groups'] if (g['layout'],g['backend'],g['op'],g['clients'])==(layout,backend,'concurrent_read',clients))
    return g['metrics'][metric]['median']


def ratio(backend,lo,hi):
    t={(x['layout'],x['repeat']):x['qps'] for x in MAIN['trials'] if x['backend']==backend and x['clients']==32 and x['op']=='concurrent_read'}
    return st.median(t[hi,r]/t[lo,r] for r in range(3))


def resource_table(d):
    rows=[['数据库核 / 客户端核','PG 万次/s','OG 万次/s']]
    for layout,label in [('db8_client2','8 / 2'),('db8_client4','8 / 4'),('db12_client4','12 / 4')]:
        rows.append([label,f'{v(layout,"postgres")/1e4:.2f}',f'{v(layout,"opengauss")/1e4:.2f}'])
    d.table(rows,[211,140,140])


def teacher():
    d=Document('report-teacher-v3','电影数据性能实验 | 内容修订版')
    d.p('文件与数据库：快在哪里，平台又属于谁？','title')
    d.p('C++ 文件程序、PostgreSQL 14.24 与 openGauss 6.0.1 的实验比较','small')
    d.p('本实验回答两个问题：数据库相对直接操作文件提供什么优势，以及按什么标准比较两个数据库。结果显示，访问路径和准备成本会改变文件与 SQL 的排名；并发曲线还会受到发压端限制。补实验通过分别增加客户端与数据库核心，解释了初测中 PostgreSQL 提前放缓、openGauss 近线性增长的现象。')
    d.h('1  数据、比较对象与计时')
    d.table([['倍率','电影','人物','演职关系'],['1×','9,204','16,489','46,850'],['10×','92,040','164,890','468,500'],['100×','920,400','1,648,900','4,685,000']],[62,140,140,149])
    d.p('四张表中的 countries 固定为 185 行；其余表按关系复制并平移主外键，保留姓名、标题和原分布，以 replica 区分副本唯一约束。100× TSV 合计约 161.1 MiB。该规模可以展示扫描成本变化，但远小于服务器内存；并发仅用 10×，不代表超内存工作集或生产容量。复制扩容也没有增加独立真实样本。')
    d.p('同一 C++17 源文件实现 TSV 扫描、偏移索引、文件更新，以及两个 libpq SQL 后端。steady_clock 计时，消费查询全部结果；连接、预编译、候选 ID 和文件索引加载另计 setup_s。结果按行数与内容摘要核对，更新每轮恢复原数据。普通配置重复 3 次，报告中位数及 min/max；文件索引构建每档仅一次。')
    d.h('预热测的是已访问数据的情形')
    d.p('串行只读查询先执行一次相同操作，再另起进程正式测量，主要减少首次加载数据页和索引页的差异；不复用上次客户端连接。更新先恢复数据并预扫描，不提前更新一次。并发在同一批连接上先预热 5 s，再测量 20 s。预热不保证数据全部驻留缓存，本实验也不衡量冷启动性能。')
    d.h('资源限制及其边界')
    d.p('Ubuntu 22.04、双路 EPYC 7542、NVMe；普通用户私有实例，两库依次运行。原实验数据库/文件使用 CPU 8-15，客户端 16-17。数据库服务各限 8 GiB，文件程序、运行器及数据库客户端所在作用域限 4 GiB；这是保护上限而非实际占用，两方案并非相同总内存预算，不能据此比较单位内存效率。')
    d.p('两库 shared_buffers=512 MB、work_mem=4 MB，关闭查询内部并行，开启 fsync、同步提交和 full_page_writes；openGauss 为 PG 兼容模式。后续资源干预保持这些配置、查询及二进制不变，只调整两端 CPU 范围。共享服务器 CPU 没有独占预留。','small')

    d.p('证据：原 compact 318 条计时记录；补实验 76 条，均通过结果审查。源代码 src/filmbench.cpp；原始数据与分析见 results/epyc-compact-evidence、scaling-probe、scaling-balance、scaling-confirmation。','small')

    d.page('2  数据库的优势：访问路径与维护机制')
    d.p('文件程序也可以有索引。数据库的优势不能简化为“数据库有索引、文件没有”；应同时看读取路径、准备成本，以及谁负责维护查询所依赖的结构和一致性。')
    rows=[['100×，耗时 ms','文件','PostgreSQL','openGauss']]
    for label,op,kw in [('The 子串计数','search_count','The'),('返回匹配 ID、标题','search_rows','The'),('姓名 T→Ttt 更新','update',''),('人物到电影连接','join',''),('国家/年份聚合','aggregate','')]:
        rows.append([label]+[f'{val(b,100,op,kw=kw)*1000:,.2f}' for b in ['file','postgres','opengauss']])
    d.table(rows,[194,99,99,99])
    d.p('标题检索使用区分大小写的字面子串，测试 The、Love、Star；上表列命中较多的 The（156,300 行）。SQL 分别计数或返回两列，文件逐行解析并匹配；两库计划均为顺序扫描。因此差距不能归因于标题索引，返回结果的传输和消费也计入耗时。')
    d.p('连接固定 peopleid=3442，三档均返回 54 行。文件扫描 credits 与 movies，两库使用 credits_person_idx 和 movies_pkey 做嵌套循环；数量级差异主要反映访问路径。聚合在三档均返回 1,511 组，文件用 std::map，两库用 HashAggregate；更高效的文件实现可能改变差距。')
    d.h('文件索引很快，但准备成本会改变短任务排名')
    d.table([['100×，100 次点查','查询均值 μs','准备＋整批 s']]+[[label,f'{val(b,100,"point","mean_ms",idx=i)*1000:.2f}',f'{val(b,100,"point","setup_plus_elapsed_s",idx=i):.3f}'] for label,b,i in [('文件扫描','file',0),('文件偏移索引','file',1),('PostgreSQL','postgres',0),('openGauss','opengauss',0)]],[211,140,140])
    d.p('偏移索引使文件每次查询约 4.84 μs，但准备加整批约 0.741 s，高于 PG 的 0.569 s。索引构建另需约 0.946 s，仅测一次；文件改变后需要重建。因此不能只取查询循环时间判断一次性任务的优劣。')
    d.h('数据库把什么工作从应用中接走？')
    d.p('SQL 用声明式查询表达检索、连接和聚合，由数据库选择计划并维护持久索引；主外键与唯一约束集中定义。相比之下，当前文件程序需要自己编写解析、索引定位、聚合及文件替换逻辑，偏移索引也没有事务性增量维护。优势是这些机制由一个系统共同管理，而非文件在原理上不能实现。')
    d.p('姓名更新影响 128,700 行。文件写临时文件、同步并原子替换；SQL 在事务中 UPDATE 后 COMMIT，计入同步提交。这比较了当前实现的更新路径，但没有证明两者具备同等故障语义，也没有通过崩溃、隔离或恢复测试验证数据库所有事务保证。')

    d.page('3  从并发曲线提出问题，再用资源计数定位')
    d.p('初测在 10× 数据上使用 1、4、8 个同步客户端，负载为主键点查，或约 90% 点查加 10% 单行 runtime 更新。8 客户端纯读 PG/OG 为 9.41/7.35 万次/s，混合为 9.43/5.50 万次/s。PG 从 4 到 8 客户端增长趋缓，OG 近线性增长。仅凭这段曲线，不能认定 PG 引擎先到上限。')
    d.h('补实验：将数据库和客户端作为两个资源来源')
    d.p('重新测量数据库 8 核＋客户端 2 核的基线，将纯读并发扩至 16、32；再分别改为 8＋4 和 12＋4，主矩阵 66 轮、每组 3 次。每轮恢复 10× 数据、核对四表并记录配置，预热 5 s、测量 20 s。轮次间调整条件顺序与两库先后，保留原二进制、查询和持久化设置。')
    d.p('因原客户端附近有其他任务，本轮改用空闲程度较高的同 NUMA 核：数据库 8-15 或另加 20-23，客户端 24-25 或 24-27；最多 16 个不同物理核。所有干预效果在新基线内部比较，不能把跨时段绝对变化全部归因于加核。')
    d.image('scaling/extended-curve.png',193,'图 1  补实验 8＋2 基线。点为 3 轮中位数，误差线为 min/max；P95 先按各轮约 1% 桶宽直方图估计，再取中位数。')
    d.table([['32 客户端，纯读','客户端忙核 / 2','数据库忙核 / 8']]+[[label,f'{v("db8_client2",b,"client_cores"):.2f}',f'{v("db8_client2",b,"db_cores"):.2f}'] for b,label in [('postgres','PostgreSQL'),('opengauss','openGauss')]],[211,140,140])
    d.p('PG 客户端已接近 2 核满载，数据库只使用约 4 核；OG 数据库接近 8 核满载。客户端还承担 libpq 调用、结果核对和摘要、随机请求生成及直方图记录，不能把它视作零开销的测量器。OG 在 16-32 并发时也进入平台，延迟随排队增加。')
    d.p('CPU 核用量来自 0.5 s 采样的客户端 utime+stime 与数据库 cgroup usage_usec 差分，取预热后的内部窗口。该窗口与 C++ 完整计时窗口不完全重合，属于近似资源测量；下面的单变量干预提供了额外证据。','small')

    d.page('4  干预结果、比较标准与结论边界')
    resource_table(d)
    d.p(f'上表为 32 客户端纯读，单位万次/s。固定数据库 8 核，客户端 2→4 核，PG 逐轮配对吞吐比中位数为 {ratio("postgres","db8_client2","db8_client4"):.2f}；固定客户端 4 核，数据库 8→12 核，OG 为 {ratio("opengauss","db8_client4","db12_client4"):.2f}。CPU 计数与干预共同支持：PG 原平台主要来自发压端，OG 高并发首先受数据库侧 CPU 限制。')
    d.image('scaling/resource-interventions.png',190,'图 2  同一数据、程序下分别改变两端核心。3 轮中位数及 min/max。8 客户端组说明增加核心不一定提速，不能把高并发收益推广到所有配置。')
    d.p(f'随后独立追加数据库 8 核＋客户端 8 核，每库 3 轮：PG 达 {v("db8_client8","postgres",data=BALANCE)/1e4:.2f} 万次/s，数据库约 8 核满载，客户端约 4.6 核；瓶颈转移至数据库侧。OG 约 {v("db8_client8","opengauss",data=BALANCE)/1e4:.2f} 万次/s。另对四个关键条件各测一次 120 s，PG 加客户端核、OG 加数据库核的吞吐比分别为 2.03 和 1.42，复现短窗口方向；单次两分钟测量不是长期容量证明。')
    d.h('按什么标准，哪个数据库更好？')
    d.p('在本次版本、数据和实现下，PG 的扫描、聚合、姓名更新及热数据并发吞吐较好；连接的 100× 中位数 OG 较低，但两者都在毫秒级，不能据单个短查询宣布总体胜负。若标准是完整功能、运维成本、恢复能力或单位内存效率，本实验没有足够证据排名。')
    d.h('两道问题的回答及适用范围')
    d.p('相对当前文件程序，数据库的价值是把查询优化、索引维护、关系约束和事务管理统一提供给应用；实际性能收益要看访问路径及结构能否复用。文件索引短循环更快，也不必然使含准备的一次性任务更快。')
    d.p('CPU 并发曲线不能单独代表引擎扩展性：更快的数据库可能先把客户端跑满。本结论只覆盖缓存预热后的复制电影数据与给定查询；内存预算不等、共享资源、短测量和有限重复均限制外推。没有验证冷缓存、超内存 I/O、故障恢复或最佳全参数调优，也未定位两库内部函数成本差异。')
    d.save()


def guide_extension(d):
    d.page('10A  补实验：从疑问到受控干预')
    d.p('初测留下的问题不是“哪条曲线看起来更直”，而是 PG 平台到底属于数据库还是客户端。若客户端已满，增加数据库核心应帮助很小；若数据库已满，增加客户端核心应帮助很小。我们通过分别改变两端资源，检查这一预测。')
    d.table([['分配','数据库 CPU','客户端 CPU','总物理核'],['DB8/C2','8-15','24,25','10'],['DB8/C4','8-15','24-27','12'],['DB12/C4','8-15,20-23','24-27','16'],['追加 DB8/C8','8-15','24-31','16']],[115,143,143,90])
    d.p('新核心在运行前检查过空闲程度，均位于 NUMA 节点 0、不使用 SMT 兄弟；它们不是独占预留。原客户端 16/17 附近有其他任务，故换核后重新跑基线。不能把旧初测与新测量的差值当作严格的资源干预效果。')
    d.p('主矩阵为 DB8/C2 纯读 1/4/8/16/32 客户端、混合 8/32；另两种分配纯读 8/32。两库、三轮，共 66 条。每轮恢复 s10，核对四表指纹并检查持久化配置；按固定种子改变块和条件顺序，两库先后交替。每次 5 s 预热加 20 s 测量。')
    d.image('scaling/extended-curve.png',193,'新基线扩至 32 客户端。数据点为三轮中位数，误差线为 min/max，不是置信区间。')
    d.p('OG 在 8 客户端约 7.39 万次/s，到 16 和 32 客户端仅增至 8.30 和 8.43 万次/s，P95 上升；它也出现平台。原先 1-8 的近线性区间太窄，不足以推断最终容量会超过 PG。')
    d.p('两端每 0.5 s 采样一次。CPU 秒除以墙钟秒得到平均忙核数，例如 2 核代表同一秒合计执行了约 2 CPU 秒。客户端读 /proc/PID/stat，数据库读独立 cgroup cpu.stat；取进程启动后预热时长再加 1 s 之后的内部样本。窗口不完全对齐，CPU 微秒/请求只能近似解释。')

    d.page('10B  瓶颈证据：计数必须与干预一起看')
    d.image('scaling/cpu-bottleneck.png',193,'DB8/C2 下客户端与数据库忙核数。虚线是各自允许使用的核心数。')
    resource_table(d)
    d.p(f'32 客户端下，PG 客户端约 {v("db8_client2","postgres","client_cores"):.2f}/2 核、数据库约 {v("db8_client2","postgres","db_cores"):.2f}/8 核；增加客户端 2→4 核的三轮配对吞吐比中位数为 {ratio("postgres","db8_client2","db8_client4"):.2f}。OG 数据库约 {v("db8_client2","opengauss","db_cores"):.2f}/8 核；固定客户端 4 核而增加数据库 8→12 核，配对比为 {ratio("opengauss","db8_client4","db12_client4"):.2f}。')
    d.p('这解释了“PG 起点高、较早放缓”：当前 PG 服务端完成请求较快，先把发压端的生成、协议、结果消费与统计能力耗尽；OG 单请求服务端 CPU 成本更高，需要更多并发才占满服务端。这里只定位到两侧资源约束，尚未把成本分摊给具体数据库函数。')
    d.p('一个近似上界是 min(客户端核数 / 每请求客户端 CPU 秒，数据库核数 / 每请求数据库 CPU 秒)。同步往返、锁、I/O 和调度还能进一步限制吞吐，每请求成本也随并发变化，因此它不是精确的容量公式。')
    d.p('C++ 将直方图更新放在单次延迟计时结束之后；它仍占用客户端 CPU，并影响整段吞吐。两库沿用相同测量代码，可以比较当前完整路径，但不能将差距全部归到数据库内核。')
    d.p('query_dop=1 限制单条查询内部并行，不等于数据库总共只能用一核；多个连接已经实际占用 8 或 12 核。低并发组增加数据库核心未必提速，PG 的 8 客户端组反而下降；具体原因没有剖析，不能擅自归因于缓存或调度。')

    d.page('10C  追加验证、未解决的问题和证据入口')
    d.p('观察到 PG 的四核客户端仍接近满载后，追加 DB8/C8、32 客户端，每库三轮。这是根据已有结果设计的扩展，不能伪称最初就预注册；与主矩阵执行时段不同，跨组比值属于描述性比较。')
    d.table([['DB8/C8，32 并发','万次/s','客户端忙核','数据库忙核']]+[[label,f'{v("db8_client8",b,data=BALANCE)/1e4:.2f}',f'{v("db8_client8",b,"client_cores",data=BALANCE):.2f}/8',f'{v("db8_client8",b,"db_cores",data=BALANCE):.2f}/8'] for b,label in [('postgres','PostgreSQL'),('opengauss','openGauss')]],[155,112,112,112])
    d.p('PG 继续增长至约 21.82 万次/s，数据库侧接近 8 核满载，说明移除发压端限制后瓶颈可以转移。OG 仍约 8.51 万次/s，增加客户端核心没有带来同样的收益。')
    rows=[['条件','20 s 三轮中位 万次/s','120 s 单轮 万次/s']]
    for l,b in [('db8_client2','postgres'),('db8_client4','postgres'),('db8_client4','opengauss'),('db12_client4','opengauss')]:
        rows.append([b+' '+l.replace('db','').replace('_client','/'),f'{v(l,b)/1e4:.2f}',f'{v(l,b,data=LONG)/1e4:.2f}'])
    d.table(rows,[171,160,160])
    d.p('长窗口预热 10 s，测量 120 s；四个条件各一次，不混进主矩阵三轮统计。两类干预比为 2.03 与 1.42，说明关键方向并非只在二十秒突发中出现，但这不是长期稳定性证明。全部补实验 76 条有效、请求错误为零。')
    d.p('主矩阵每轮前检查可用内存、磁盘和 load1，阈值分别为 32 GiB、40 GiB 和 32；追加组检查内存、磁盘。主矩阵观察到的最低可用内存约 228 GiB、最低剩余磁盘约 124 GiB。完成后两库停止、私有端口不再监听；这些记录不能证明共享缓存或 I/O 绝对没有干扰。')
    d.p('没有可用 perf，且内核性能事件权限受限；未修改共享服务器全局设置。线程池、代码生成、内存预算等没有逐项调优，不能称为各产品最佳性能。本轮结果解释的是当前热数据点查路径，仍未回答冷缓存、复杂查询、超内存工作集和故障恢复问题。')
    d.p('复核入口：scripts/run_scaling_probe.py 与 run_scaling_confirmation.py 保存计划、源码哈希、逐轮记录和资源样本；analyze_scaling_probe.py 核对矩阵、有效性、客户端亲和性、数据库 OOM 计数及原二进制哈希。结果目录分别为 results/scaling-probe、scaling-balance、scaling-confirmation。')


if __name__=='__main__':
    teacher()
    learning(name='experiment-guide-v3',extension=guide_extension)
