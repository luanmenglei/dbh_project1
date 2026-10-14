#!/usr/bin/env python3
"""Build the Chinese report and PNG figures from audited raw experiment records.

Dependencies: reportlab, matplotlib. Font paths can be overridden for other hosts.
Run: python scripts/build_report.py results/epyc-compact-evidence
"""
import argparse
import json
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from analyze_results import audit


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('results', type=Path)
    ap.add_argument('--out', type=Path, default=Path('output'))
    ap.add_argument('--body-font', default='C:/Windows/Fonts/simsun.ttc')
    ap.add_argument('--heading-font', default='C:/Windows/Fonts/simhei.ttf')
    a = ap.parse_args()
    data = audit(a.results)
    groups = data['groups']
    figures = a.out / 'figures'
    figures.mkdir(parents=True, exist_ok=True)
    (a.out / 'pdf').mkdir(parents=True, exist_ok=True)
    def item(backend, scale, op, keyword='', indexed=0, clients=0):
        return next(g for g in groups if (g['backend'], g['scale'], g['op'], g['keyword'], g['indexed'], g['clients']) ==
                    (backend, scale, op, keyword, indexed, clients))
    def med(backend, scale, op, metric='elapsed_s', **kw):
        return item(backend, scale, op, **kw)['metrics'][metric]['median']
    names = {'file': 'TSV file', 'postgres': 'PostgreSQL', 'opengauss': 'openGauss'}
    palette = {'file': '#8492a6', 'postgres': '#176c9d', 'opengauss': '#d67932'}
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.spines.top': False,
                         'axes.spines.right': False, 'axes.titleweight': 'bold', 'savefig.dpi': 200})
    def lines(ax, op, metric='elapsed_s', factor=1000, keyword='', indexed=0):
        for b in names:
            vals = [item(b, s, op, keyword, indexed)['metrics'][metric] for s in [1, 10, 100]]
            y = [v['median'] * factor for v in vals]
            ax.errorbar([1, 10, 100], y, yerr=[[(v['median']-v['min'])*factor for v in vals],
                         [(v['max']-v['median'])*factor for v in vals]], marker='o', capsize=3,
                        color=palette[b], label=names[b])
        ax.set_xscale('log'); ax.set_yscale('log'); ax.set_xticks([1, 10, 100], ['1x', '10x', '100x'])
        ax.set_xlabel('Dataset scale'); ax.set_ylabel('Elapsed time (ms)'); ax.grid(alpha=.18)
    fig, axes = plt.subplots(1, 2, figsize=(7.3, 2.8))
    lines(axes[0], 'search_count', keyword='The'); axes[0].set_title('Substring count: The')
    lines(axes[1], 'search_rows', keyword='The'); axes[1].set_title('Return matching rows: The')
    axes[0].legend(fontsize=8); fig.tight_layout(); fig.savefig(figures/'search.png'); plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(7.3, 2.7))
    for ax, op, title in zip(axes, ['update', 'join', 'aggregate'], ['Durable name update', 'Join by person ID', 'Group by country/year']):
        lines(ax, op); ax.set_title(title, fontsize=10)
    axes[0].legend(fontsize=7); fig.tight_layout(); fig.savefig(figures/'relational.png'); plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(7.3, 4.15))
    for row, op in enumerate(['concurrent_read', 'mixed']):
        for col, (metric, factor, ylabel) in enumerate([('throughput_ops_s', .001, 'Throughput (k ops/s)'), ('p95_ms', 1, 'P95 latency (ms)')]):
            ax = axes[row, col]
            for b in ['postgres', 'opengauss']:
                vals = [item(b, 10, op, clients=c)['metrics'][metric] for c in [1, 4, 8]]
                ax.errorbar([1, 4, 8], [v['median']*factor for v in vals],
                            yerr=[[(v['median']-v['min'])*factor for v in vals], [(v['max']-v['median'])*factor for v in vals]],
                            marker='o', capsize=3, color=palette[b], label=names[b])
            ax.set_title('Read only' if row == 0 else '90% read / 10% update')
            ax.set_xticks([1, 4, 8]); ax.set_xlabel('Concurrent clients'); ax.set_ylabel(ylabel)
            ax.grid(alpha=.18); ax.set_ylim(bottom=0)
    axes[0, 0].legend(fontsize=8); fig.tight_layout(); fig.savefig(figures/'concurrency.png'); plt.close(fig)

    pdfmetrics.registerFont(TTFont('BodyCN', a.body_font, subfontIndex=0))
    pdfmetrics.registerFont(TTFont('HeadingCN', a.heading_font))
    styles = {
        'title': ParagraphStyle('title', fontName='HeadingCN', fontSize=23, leading=31, textColor=colors.HexColor('#123a50'), spaceAfter=12),
        'sub': ParagraphStyle('sub', fontName='HeadingCN', fontSize=11, leading=17, textColor=colors.HexColor('#527083'), spaceAfter=12),
        'h': ParagraphStyle('h', fontName='HeadingCN', fontSize=15, leading=22, textColor=colors.HexColor('#123a50'), spaceBefore=10, spaceAfter=8),
        'p': ParagraphStyle('p', fontName='BodyCN', fontSize=10.4, leading=17, spaceAfter=8, wordWrap='CJK'),
        'small': ParagraphStyle('small', fontName='BodyCN', fontSize=8.4, leading=13, spaceAfter=7, wordWrap='CJK', textColor=colors.HexColor('#52616a')),
        'cell': ParagraphStyle('cell', fontName='BodyCN', fontSize=9, leading=13, wordWrap='CJK'),
    }
    story = []
    def p(text, style='p'):
        story.append(Paragraph(escape(text), styles[style]))
    def h(text): p(text, 'h')
    def table(rows, widths):
        t = Table([[Paragraph(escape(str(v)), styles['cell']) for v in row] for row in rows], colWidths=widths, repeatRows=1, hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e8f0f5')),
                              ('VALIGN', (0,0), (-1,-1), 'TOP'), ('LEFTPADDING', (0,0), (-1,-1), 7),
                              ('RIGHTPADDING', (0,0), (-1,-1), 7), ('TOPPADDING', (0,0), (-1,-1), 7),
                              ('BOTTOMPADDING', (0,0), (-1,-1), 7),
                              ('LINEBELOW', (0,0), (-1,0), .6, colors.HexColor('#9eb6c4')),
                              ('LINEBELOW', (0,1), (-1,-1), .25, colors.HexColor('#dce3e7'))]))
        story.extend([t, Spacer(1, 10)])
    def image(name, height): story.append(Image(str(figures/name), width=491, height=height))
    def page(): story.append(PageBreak())

    p('文件与数据库的操作性能比较', 'title')
    p('PostgreSQL 14.24 · openGauss 6.0.1 · C++ / TSV\nProject 1 实验报告草稿 | 实测日期：2026-10-12', 'sub')
    p('本实验比较相同电影数据在顺序文件、文件偏移索引和关系数据库中的访问代价。结果表明：访问路径对点查和连接的影响很大；数据库在本次扫描与聚合测试中优于当前 TSV 实现；准备成本、结果传输及持久化语义会改变比较口径。结论仅适用于本文的数据、实现和受限资源配置。')
    h('1  数据与实验设计')
    table([['倍率', '电影 movies', '人物 people', '演职关系 credits'],
           ['1×', '9,204', '16,489', '46,850'], ['10×', '92,040', '164,890', '468,500'],
           ['100×', '920,400', '1,648,900', '4,685,000']], [64,142,142,143])
    p('countries 固定为 185 行。放大数据由原始关系的完整副本构成，平移主键与外键，唯一约束按 replica 隔离；保留原始文本与分布。100× 的四个 TSV 合计约 161.1 MiB。这增加了规模，但没有增加真实数据的多样性。')
    table([['操作', '一致的逻辑任务'], ['标题检索', '区分大小写的字面子串 The / Love / Star；分别计数、返回 movieid 与 title。'],
           ['主键点查', '固定随机种子；每次 100 个请求。比较文件逐次扫描、偏移索引和 SQL 主键索引。'],
           ['更新', '人物 first_name 和 surname 中 T 替换为 Ttt；每轮恢复原始数据。'],
           ['连接与聚合', '指定 peopleid 查询参演影片；按国家和年份计算 count(*)、sum(runtime)、count(runtime)。'],
           ['并发', '10× 数据；1 / 4 / 8 个客户端；纯点查或约 90% 点查、10% runtime 更新。']], [78,413])
    h('2  实现与环境')
    p('C++17 驱动通过 libpq 调用数据库，并解码 TSV。结果以行数及无序 FNV-1a 摘要核对；NULL 与空串区分。服务器为双路 AMD EPYC 7542，Ubuntu 22.04，数据位于 /data1 的 NVMe 文件系统；GCC 11.4 编译。两套数据库在 benchmark-user 用户目录内独立运行，openGauss 使用 PG 兼容模式。')
    p('CPU 8-15 分配给文件任务或数据库，CPU 16-17 分配给数据库客户端，共不超过 10 个物理核；两库顺序运行。单个数据库内存上限 8 GiB，文件/运行器作用域上限 4 GiB，Nice=10。CPU 亲和性不等于独占，仍可能受到共享服务器其他任务影响。', 'small')

    page(); h('3  计时口径与标题检索')
    p('普通查询每轮先预热，随后计时；每组重复 3 次。steady_clock 记录查询、结果接收和摘要计算的耗时，连接及预编译等准备计入 setup_s，另行报告。数据库 shared_buffers=512 MB、work_mem=4 MB，查询并行关闭；fsync、synchronous_commit、full_page_writes 开启。实验未清空系统页缓存，因此是预热条件下的比较。')
    image('search.png', 188)
    p('图 1  标题 The 的计数与结果返回耗时。横纵轴均为对数刻度；点为 3 轮中位数，误差线为最小值至最大值，非置信区间。其余关键词保留在完整汇总中。', 'small')
    p('100× 时，The 命中 156,300 行。仅计数的文件 / PostgreSQL / openGauss 耗时分别为 529.2 / 128.1 / 322.3 ms；返回匹配行增至 554.5 / 209.6 / 402.7 ms。Love 和 Star 分别命中 8,800 与 5,000 行，两个数据库的返回行代价更接近计数代价。诊断计划显示，两库的子串检索均使用顺序扫描；计时差异不能解释为标题索引收益。')
    h('4  点查：查询快，不代表准备便宜')
    table([['100×，每轮 100 次', '每次查询均值\n(ms)', '准备时间\n(s)', '准备 + 整批查询\n(s)']] +
          [[label, f'{med(b,100,"point","mean_ms",indexed=i):.4f}', f'{med(b,100,"point","setup_s",indexed=i):.4f}',
            f'{med(b,100,"point","setup_plus_elapsed_s",indexed=i):.4f}']
           for label,b,i in [('文件扫描','file',0),('文件偏移索引','file',1),('PostgreSQL','postgres',0),('openGauss','opengauss',0)]], [146,105,100,140])
    p('表中每列分别取 3 轮中位数，最后一列由每轮 setup_s + elapsed_s 重新计算。准备阶段均读取候选电影 ID；文件索引版本还加载偏移索引。文件索引单次点查约 4.84 μs，但这一批含准备共约 0.741 s，高于 PostgreSQL 的约 0.569 s。100× 索引另需约 0.946 s 构建，且只测量 1 次；不包含在上表中。')
    p('因此，长期复用索引的查询循环与一次性启动的短任务应分开评价。文件扫描按请求从头查找，规模增长使其成本上升；数据库诊断计划均使用 movies 主键索引。此处未把文件索引扩展为具备并发写入和事务功能的通用存储系统。')

    page(); h('5  更新、连接与聚合')
    image('relational.png', 182)
    p('图 2  各规模的操作耗时，均不含准备阶段。点为 3 轮中位数，误差线为最小值至最大值；纵轴为对数刻度。', 'small')
    table([['100×，中位数', '文件 (ms)', 'PostgreSQL (ms)', 'openGauss (ms)']] +
          [[label]+[f'{med(b,100,op)*1000:,.2f}' for b in names]
           for label,op in [('持久化姓名更新','update'),('按人物连接','join'),('国家/年份聚合','aggregate')]], [140,110,121,120])
    h('更新：比较完整持久化操作')
    p('100× 更新 128,700 个人物。文件端扫描并写出新文件，完成文件同步、原子替换及目录同步；数据库在显式事务中更新，并计入 COMMIT。PostgreSQL 为 1.472 s，文件为 1.828 s，openGauss 为 3.162 s。这里比较的是两种实际实现完成同一逻辑更新的代价，不是等量写盘字节的微基准。启用持久化不等于已经验证断电恢复；本实验未做故障注入。')
    h('连接：主要差别是访问路径')
    p('固定 peopleid=3442，在每个规模均返回 54 条结果。文件实现扫描 credits，再扫描 movies；两个数据库使用 credits_person_idx 和 movies_pkey 执行嵌套循环连接。因此，100× 文件约 1.814 s 与数据库约 1 ms 的差距，主要体现完整扫描和索引访问的差别，不能直接推广为数据库引擎相对文件系统的通用加速比。')
    h('聚合：相同结果，不同执行实现')
    p('100× 聚合返回 1,511 组。文件实现逐行解码并维护 std::map；两库诊断计划均为顺序扫描加 HashAggregate。PostgreSQL / openGauss 分别为 234.0 / 345.7 ms，文件为 722.4 ms。该结果支持“当前数据库执行路径快于当前 TSV 实现”；它并未排除更优化的文件解析或哈希聚合实现达到不同结果。')
    p('EXPLAIN ANALYZE 是单独的诊断记录，其执行时间不混入正式 3 轮统计。毫秒级连接结果对调度与准备边界敏感，不能仅凭这组数据断言 openGauss 的连接能力整体优于 PostgreSQL。', 'small')

    page(); h('6  短时并发测试')
    p('每线程复用一条数据库连接与预编译语句；预热 5 s，测量 20 s，每个组合重复 3 轮，共 36 轮。混合负载随机选择约 10% 的操作更新 runtime；它与前述批量姓名更新是不同任务。每轮恢复数据，成功读取也实际消费结果。')
    image('concurrency.png', 279)
    p('图 3  10× 数据的吞吐量和端到端 P95。点为 3 轮中位数，误差线为最小值至最大值。P95 先在每轮用约 1% 宽度的对数直方图估计，再取轮次中位数，不是合并全部请求后的分位数。', 'small')
    table([['8 客户端，中位数', 'PostgreSQL', 'openGauss'],
           ['纯读吞吐 (ops/s)', f'{med("postgres",10,"concurrent_read","throughput_ops_s",clients=8):,.0f}', f'{med("opengauss",10,"concurrent_read","throughput_ops_s",clients=8):,.0f}'],
           ['混合吞吐 (ops/s)', f'{med("postgres",10,"mixed","throughput_ops_s",clients=8):,.0f}', f'{med("opengauss",10,"mixed","throughput_ops_s",clients=8):,.0f}'],
           ['混合 P95 (ms)', '0.141', '0.318'], ['混合写操作 P95 (ms)', '0.180', '0.441']], [237,127,127])
    p('8 客户端下，PostgreSQL 纯读与混合吞吐分别约为 openGauss 的 1.28 倍和 1.71 倍。两库从 1 增至 8 客户端均有增长；PostgreSQL 从 4 到 8 客户端的增长趋缓。但客户端仅有 2 个 CPU 核，缺少排除客户端瓶颈的证据，不能把该拐点认定为数据库最大容量。')
    p('PostgreSQL 的混合负载中位吞吐略高于纯读，差距仅约 0.2%；结合轮次波动，应视为基本接近，不能解释为“加入写操作提升性能”。36 轮记录均 valid=true、errors=0；这只能说明本次运行未检测到错误。')

    page(); h('7  结果审查与结论边界')
    p('正式运行于 2026-10-12 22:37:48 至 23:31:06（北京时间），约 53 分 18 秒。结果包含 COMPLETE 标记，未出现 FAILED 标记；共 873 条命令记录，其中计时记录 318 条：105 组各 3 轮、3 组索引构建各 1 次。审查脚本验证了完整实验矩阵、重复编号、计时有效性、27 组跨后端结果摘要及更新行数，并重算核对 555 项 CSV 统计。')
    p('独立 Python 正确性测试覆盖 file / PostgreSQL / openGauss，包含 NULL、空串、转义字符、Unicode、重复参演角色、全 NULL 聚合、点查一致性、更新与过期索引拒绝。摘要比对仍有理论碰撞可能，不能替代所有逐字段断言；本次结果还依赖独立测试与行数检查提供交叉证据。')
    table([['限制', '对结论的影响'],
           ['仅 3 次计时重复', '不报告显著性或置信区间。最明显的短查询波动是 1× PostgreSQL 连接：1.38-3.98 ms。'],
           ['共享服务器、轮换顺序', '串行轮次轮换后端、并发轮次交替两库先后，但未完全随机化；系统活动及缓存仍可能影响结果。'],
           ['短时且仅一种并发规模', '并发只测试 10×、20 s 窗口，不能代表持续高压、长尾故障或最大承载能力。'],
           ['资源预算与实现不同', '文件作用域 4 GiB、数据库 8 GiB；未验证等内存压力。两库客户端最多使用 2 核。'],
           ['数据复制与预热', '放大后分布重复，连接结果大小固定；结果不代表冷启动或更复杂的真实业务。']], [128,363])
    p('并发吞吐的最大相对极差为 26.2%（openGauss，1 客户端混合负载，约 6,245-8,059 ops/s）。因此保留全部轮次与误差线，不删除慢轮次。当前证据足以完成课程范围内的比较；若继续研究，应优先重复波动较大的短查询，并单独检验客户端限制，而不是直接扩大整套实验。')
    h('8  可复现材料与提交前检查')
    p('src/filmbench.cpp 为实验 C++ 源码；scripts/prepare.py 生成带清单和哈希的数据；scripts/run_suite.py 调度实验；scripts/analyze_results.py 审查原始记录；scripts/build_report.py 从通过审查的数据生成本文和图表。results/epyc-compact-evidence 保存原始记录、配置、诊断计划、资源采样与运行时源码哈希。本文使用的五个源码/配置文件哈希与正式运行一致。')
    p('本报告中的数据均取自本次运行，不依赖外部性能宣传。审查和制图在本地完成，没有为写报告再次启动数据库负载。课程要求分别提交 report.pdf 与 C++ 源文件，不压缩。当前稿未填写姓名、学号及个人工作说明；这些内容需要由提交者按实际情况补充，不能自动代填。')
    p('结论：对于本次实现，索引决定点查与连接的主要效率差异，PostgreSQL 在扫描、聚合及短时并发中表现较好。选用存储方式时还应考虑索引准备与复用、事务和并发维护成本；本文测量没有支持对文件存储或任一数据库做普遍优劣判断。')

    def footer(canvas, doc):
        canvas.setStrokeColor(colors.HexColor('#d3dfe6')); canvas.line(52, 43, A4[0]-52, 43)
        canvas.setFont('HeadingCN', 8); canvas.setFillColor(colors.HexColor('#607582'))
        canvas.drawString(52, 29, 'FILMBENCH  /  compact 实测报告草稿')
        canvas.drawRightString(A4[0]-52, 29, str(doc.page))
    out = a.out/'pdf'/'report.pdf'
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=52, rightMargin=52, topMargin=43, bottomMargin=56,
                            title='文件与数据库的操作性能比较', author='', subject='Project 1 compact experiment report')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(out.resolve())


if __name__ == '__main__':
    main()
