#!/usr/bin/env python3
"""Explain audited causal scaling experiments and append them to both reading versions."""
import io
import json
from pathlib import Path
import statistics as st
from pypdf import PdfReader,PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from analyze_scaling_probe import analyze
from build_report_versions import Document,OUT,ROOT


def main():
    primary=analyze(ROOT/'results/scaling-probe')
    balance=analyze(ROOT/'results/scaling-balance')
    long=analyze(ROOT/'results/scaling-confirmation')
    def group(layout,b,c=32,data=primary):
        return next(g for g in data['groups'] if (g['layout'],g['backend'],g['op'],g['clients'])==(layout,b,'concurrent_read',c))
    def value(layout,b,metric='qps',c=32,data=primary):return group(layout,b,c,data)['metrics'][metric]['median']
    def ratio(b,lo,hi):
        rows={(t['layout'],t['repeat']):t['qps'] for t in primary['trials'] if t['backend']==b and t['clients']==32 and t['op']=='concurrent_read'}
        r=[rows[hi,i]/rows[lo,i] for i in range(3)]
        return st.median(r),min(r),max(r)
    pg=ratio('postgres','db8_client2','db8_client4');og=ratio('opengauss','db8_client4','db12_client4')
    assert pg[1]>1.3 and og[1]>1.2,'Reassess narrative: resource intervention not consistently positive'
    d=Document('scaling-investigation','扩展性追查 | 2026-10-13 补充实验')
    d.p('曲线的平台，究竟是谁的上限？','title')
    d.p('补充研究：区分数据库侧与发压客户端侧的瓶颈','small')
    d.paragraphs('''初始实验中，PostgreSQL 吞吐起点较高，但在 4-8 客户端间开始放缓；openGauss 则近线性增长。仅看曲线，容易将它解释为两种数据库扩展能力的差异。补实验表明，在这套同步点查程序中，两库先触及的是不同位置的 CPU 约束：PostgreSQL 先受到发压客户端限制，openGauss 在更高并发下首先用满数据库侧 CPU。''')
    rows=[['32 客户端，纯读','PG 吞吐 / 万次每秒','OG 吞吐 / 万次每秒']]
    for l,label in [('db8_client2','数据库8核 + 客户端2核'),('db8_client4','数据库8核 + 客户端4核'),('db12_client4','数据库12核 + 客户端4核')]:
        rows.append([label,f'{value(l,"postgres")/1e4:.2f}',f'{value(l,"opengauss")/1e4:.2f}'])
    d.table(rows,[211,140,140])
    d.paragraphs(f'''保持数据库 8 核不变，只将客户端从 2 核增至 4 核，PostgreSQL 的逐轮配对吞吐比中位数为 {pg[0]:.2f} 倍，三轮范围 {pg[1]:.2f}-{pg[2]:.2f} 倍。原“平台”随客户端资源增加明显上移，因此它不是 PostgreSQL 引擎在原服务器上的容量上限。

保持客户端 4 核不变，将数据库从 8 核增至 12 核，openGauss 的配对吞吐比中位数为 {og[0]:.2f} 倍，三轮范围 {og[1]:.2f}-{og[2]:.2f} 倍。数据库 CPU 在这两组分别平均使用约 {value('db8_client4','opengauss','db_cores'):.2f}/8 核和 {value('db12_client4','opengauss','db_cores'):.2f}/12 核。这支持数据库侧 CPU 资源是该配置下的主要约束。''')
    d.image('scaling/resource-interventions.png',194,'图 A  同一数据和程序下的资源干预。点为 3 轮中位数，误差线为 min/max；DB 表示数据库核数，C 表示客户端核数。')
    d.p('方法：s10 数据、原 C++ 二进制与查询不变；主矩阵 66 轮，每轮预热 5 s、测量 20 s。另有 6 轮客户端余量测试和 4 轮两分钟验证。最多同时使用 16 个不同物理核，普通用户运行。两端 CPU 分开计量，后端及条件顺序轮换。原实验的 1-8 客户端结果仍有效，但不能再将其曲线平台当作引擎上限。','small')

    d.page('1  从曲线到资源证据')
    d.image('scaling/extended-curve.png',193,'图 B  同预算基线扩到 32 客户端：数据库 8 核、客户端 2 核。点为 3 轮中位数，误差线为 min/max；P95 由各轮约 1% 桶宽的直方图估计后取中位数。折线连接实测点，不是拟合曲线。')
    d.image('scaling/cpu-bottleneck.png',193,'图 C  同一组实验中，客户端进程和数据库 cgroup 的平均 CPU 核用量。虚线是允许使用的核心数量。')
    d.paragraphs(f'''32 客户端基线下，PostgreSQL 客户端平均占用约 {value('db8_client2','postgres','client_cores'):.2f}/2 核，数据库占用约 {value('db8_client2','postgres','db_cores'):.2f}/8 核。openGauss 对应约 {value('db8_client2','opengauss','client_cores'):.2f}/2 核和 {value('db8_client2','opengauss','db_cores'):.2f}/8 核。CPU 计数与改变资源后的吞吐响应共同支持瓶颈判断；只看其中一项并不充分。

openGauss 从 8 到 32 客户端也不再保持原先的线性斜率；更多请求开始排队，P95 增大。相对平滑的线性增长区间，并不代表每条查询更快或最终容量必然更高。它也可能表示单请求较慢，当前并发数还不足以触及资源上限。''')
    d.h('一个有用但近似的解释')
    d.p('吞吐上界 ≈ min(客户端核数 / 每请求客户端 CPU 秒，数据库核数 / 每请求数据库 CPU 秒)','code')
    d.p('这是 CPU 服务需求的近似模型，不是独立测出的精确容量公式。同步往返、锁、调度和 I/O 还可能进一步限制吞吐，且每请求 CPU 成本会随并发变化。PostgreSQL 更早达到客户端可生成/消费请求的速率上限；openGauss 在 1-8 客户端区间尚有数据库侧扩展空间，随后也出现平台。')

    d.page('2  更多客户端余量与持续验证')
    d.h('固定数据库 8 核，把客户端增加到 8 核')
    rows=[['32 客户端，纯读','吞吐 万次/s','客户端忙核','数据库忙核']]
    for b,label in [('postgres','PostgreSQL'),('opengauss','openGauss')]:
        rows.append([label,f'{value("db8_client8",b,data=balance)/1e4:.2f}',f'{value("db8_client8",b,"client_cores",data=balance):.2f}/8',f'{value("db8_client8",b,"db_cores",data=balance):.2f}/8'])
    d.table(rows,[155,112,112,112])
    factors=[value('db8_client8',b,data=balance)/value('db8_client4',b) for b in ['postgres','opengauss']]
    d.p(f'8 核客户端相对 4 核客户端的吞吐中位数比，PostgreSQL 为 {factors[0]:.2f}，openGauss 为 {factors[1]:.2f}。这是观察到 PostgreSQL 发压端仍较忙后追加的独立三轮测试；执行时段不同，这两个比值属于描述性比较，并非同时随机配对的因果估计。')
    d.p('当客户端扩大到 8 核时，PostgreSQL 数据库侧也接近 8 核满载，而客户端只使用约 4.6 核。这说明继续解除发压端限制后，瓶颈确实可以转移到数据库侧；原先约 9 万次/秒的平台不是数据库的固定上限。')
    d.h('两分钟窗口：不混入原三轮统计')
    rows=[['条件（数据库 / 客户端）','20秒组中位数 万次/s','120秒单轮 万次/s','单轮 P95 ms']]
    for l,b in [('db8_client2','postgres'),('db8_client4','postgres'),('db8_client4','opengauss'),('db12_client4','opengauss')]:
        label=('PG ' if b=='postgres' else 'OG ')+l.replace('db','').replace('_client',' / ')
        rows.append([label,f'{value(l,b)/1e4:.2f}',f'{value(l,b,data=long)/1e4:.2f}',f'{value(l,b,"p95_ms",data=long):.3f}'])
    d.table(rows,[155,120,119,97])
    lpg=value('db8_client4','postgres',data=long)/value('db8_client2','postgres',data=long)
    log=value('db12_client4','opengauss',data=long)/value('db8_client4','opengauss',data=long)
    assert lpg>1.3 and log>1.2,'Reassess narrative: long-window check did not reproduce effect'
    d.paragraphs(f'''长窗口中，PostgreSQL 增加客户端核心的吞吐比为 {lpg:.2f}，openGauss 增加数据库核心的比为 {log:.2f}，与短窗口干预方向一致。每个条件仅一次 120 秒测量，主要用来检查结论是否只出现在短时突发中；它不是长期稳定性或生产容量证明。

主矩阵与两组追加实验共 76 条计时记录，结果均有效且无请求错误。没有提高内存上限、关闭持久化、调整线程池或修改查询来获得更好看的数字；这次重点是定位限制所在，而非寻找所有调优参数的最优组合。''')
    d.h('为什么客户端也需要这么多 CPU？')
    d.p('每个同步线程不仅发 SQL，还要生成请求、调用 libpq、解析并核对返回行、计算结果摘要，以及记录延迟直方图。直方图更新发生在单次延迟计时之后，但仍占用客户端 CPU 并影响整段吞吐。这些工作在资源干预中保持不变；现有计数不能将客户端成本进一步准确分摊给各个函数。')

    d.page('3  能确认的原因与仍然未知的部分')
    d.table([['证据强度','结论'],['直接计数','PostgreSQL 原两核客户端接近满载；openGauss 高并发的数据库侧接近分配核心数。'],['受控干预','固定数据库资源，增加客户端资源可显著提高 PG 吞吐；固定客户端资源，增加数据库资源可提高 OG 吞吐。'],['合理解释','原曲线形状受不同位置的资源上限影响，不能用其弯曲程度直接比较数据库扩展性。'],['尚未定位','为什么两个发行版的每请求服务端 CPU 成本不同；具体由优化、执行、协议、资源管理或其他内部路径各贡献多少。'],['未研究','最优全参数调优、远程网络、持久高压、冷缓存、故障恢复、不同规模与复杂业务。']],[97,394])
    d.p('openGauss 的 query_dop=1 限制单条查询的内部并行度，不等于数据库总共只能用一核。本次多个连接已实际占满 8 核或 12 核。对于当前单行主键查询，不能仅凭该参数为 1 就判断性能被错误锁死；需要区分查询内并行与连接之间的并发。')
    d.p('图 A 的 8 客户端组也提醒我们，加核并不总有收益：openGauss 的变化很小，PostgreSQL 在数据库 8→12 核时反而下降。低并发时可同时执行的请求有限，额外核心不能保证提速；下降的具体原因尚未剖析，不能擅自归因于某种缓存或调度机制。')
    d.paragraphs('''CPU 秒来自客户端 /proc/PID/stat 的 utime+stime，以及数据库独立 cgroup 的 usage_usec。每 0.5 秒采样，剔除进程启动后预热时长再加 1 秒之前的样本，以内部稳定窗口求差分。原二进制没有导出准确的测量时钟起点，因此 CPU 窗口与完整吞吐窗口不完全重合；CPU 微秒/请求只能作为近似服务需求。

运行前整机 CPU 约 3%、可用内存约 229 GiB。原客户端附近有其他任务活动，本轮避开这些核，在同一 NUMA 节点选择新的客户端核心并重新跑基线。因此资源干预必须在本轮内部比较，不能把旧时间段与新时间段的绝对差全部归因于配置。CPU 并不独占，线程调度和共享缓存仍可能造成波动。

本机没有可用的 perf 命令，内核性能事件权限也有限；没有为剖析而改共享服务器系统设置。因此本文定位到的是客户端/数据库两侧的资源瓶颈，不将其夸大为已经揭示 openGauss 每个内部函数的成本。''')
    d.h('报告中值得保留的发现')
    d.p('相同的资源分配不一定让被测数据库都充分发挥性能。更快的数据库可能先把发压端跑满，表现为提前进入平台；单请求成本较高的数据库反而可能在较窄并发区间呈现更漂亮的直线。观察曲线后，应结合两端资源计数与单变量干预，再判断平台属于谁。')
    d.p('原始材料：results/scaling-probe、results/scaling-balance、results/scaling-confirmation；协议：docs/scaling-probe-protocol.txt；审查：scripts/analyze_scaling_probe.py；每轮日志、资源计数、配置及源码哈希均保留。','small')
    d.p('方法参考：PostgreSQL 14 pgbench 文档的 Good Practices 明确提醒发压客户端可能成为瓶颈，并建议延长测试、重复验证。https://www.postgresql.org/docs/14/pgbench.html。本研究使用自编 C++ 客户端，引用该文档说明方法风险，不把 pgbench 的实现等同于当前客户端。','small')
    d.save()
    appendix=PdfReader(OUT/'pdf/scaling-investigation.pdf')
    for base,dest,count in [('report-teacher','report-teacher-v2',1),('experiment-guide','experiment-guide-v2',len(appendix.pages))]:
        writer=PdfWriter()
        for page in PdfReader(OUT/'pdf'/f'{base}.pdf').pages:writer.add_page(page)
        for page in list(appendix.pages)[:count]:writer.add_page(page)
        for i,page in enumerate(writer.pages,1):
            stream=io.BytesIO();c=canvas.Canvas(stream,pagesize=A4);c.setFillColorRGB(1,1,1);c.rect(A4[0]-87,22,39,16,fill=1,stroke=0)
            c.setFont('HeadCN',8);c.setFillColorRGB(.38,.46,.51);c.drawRightString(A4[0]-52,29,str(i));c.save();stream.seek(0);page.merge_page(PdfReader(stream).pages[0])
        writer.add_metadata({'/Title':'电影数据性能实验：含扩展性追查','/Author':''})
        with (OUT/'pdf'/f'{dest}.pdf').open('wb') as f:writer.write(f)
        original=(OUT/'text'/f'{base}.txt').read_text(encoding='utf-8')
        added=(OUT/'text/scaling-investigation.txt').read_text(encoding='utf-8')
        if count==1:added=added.split('1  从曲线到资源证据')[0]
        (OUT/'text'/f'{dest}.txt').write_text(original+'\n\n补充实验（2026-10-13）\n\n'+added,encoding='utf-8')
        print(OUT/'pdf'/f'{dest}.pdf')


if __name__=='__main__':main()
