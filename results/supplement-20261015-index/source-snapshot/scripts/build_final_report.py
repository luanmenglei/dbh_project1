#!/usr/bin/env python3
"""Build the canonical submission from audited historical performance and revised correctness evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='')
    parser.add_argument('--student-id', default='')
    parser.add_argument('--body-font')
    parser.add_argument('--heading-font')
    args = parser.parse_args()
    if args.body_font:
        os.environ['FILMBENCH_BODY_FONT'] = args.body_font
    if args.heading_font:
        os.environ['FILMBENCH_HEADING_FONT'] = args.heading_font
    from build_report_versions import Document, OUT, DATA, g, val
    from analyze_scaling_probe import analyze
    from analyze_coverage import analyze as audit_coverage
    from evidence import require
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import statistics as st

    scaling = analyze(ROOT/'results/scaling-probe')
    balance = analyze(ROOT/'results/scaling-balance')
    long = analyze(ROOT/'results/scaling-confirmation')
    coverage = audit_coverage(ROOT/'results/review-coverage-diverse')
    verification = json.loads((ROOT/'results/review-correctness/verification.json').read_text())
    source_hash = hashlib.sha256((ROOT/'src/filmbench.cpp').read_bytes()).hexdigest()
    require(verification['source_sha256'] == source_hash, 'Submitted C++ differs from verified source')
    require(all(verification['backends'][b] == 'passed' for b in ['file', 'postgres', 'opengauss']), 'Correctness checks failed')
    names = {'file': 'TSV file', 'postgres': 'PostgreSQL', 'opengauss': 'openGauss'}
    colors = {'file': '#8492a6', 'postgres': '#176c9d', 'opengauss': '#d67932'}
    (OUT/'figures').mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False})
    fig, axes = plt.subplots(2, 2, figsize=(8, 5.5))
    for ax, (op, title, keyword) in zip(axes.flat, [('search_count', 'Substring count: The', 'The'),
                                                   ('update', 'Durable name update', ''),
                                                   ('join', 'Join: 54 output rows', ''),
                                                   ('aggregate', 'Country/year aggregate', '')]):
        for backend in names:
            values = [g(backend, s, op, keyword)['metrics']['elapsed_s'] for s in [1, 10, 100]]
            ax.errorbar([1, 10, 100], [v['median']*1000 for v in values],
                        yerr=[[(v['median']-v['min'])*1000 for v in values], [(v['max']-v['median'])*1000 for v in values]],
                        marker='o', capsize=3, label=names[backend], color=colors[backend])
        ax.set_xscale('log'); ax.set_yscale('log'); ax.set_xticks([1, 10, 100], ['1x', '10x', '100x'])
        ax.set_xlabel('Dataset scale'); ax.set_ylabel('Elapsed time (ms)'); ax.set_title(title); ax.grid(alpha=.2)
    axes[0, 0].legend(fontsize=8)
    fig.tight_layout(); fig.savefig(OUT/'figures/serial-scales.png', dpi=200); plt.close(fig)

    def q(data, layout, backend, metric='qps'):
        return next(x for x in data['groups'] if (x['layout'], x['backend'], x['op'], x['clients']) ==
                    (layout, backend, 'concurrent_read', 32))['metrics'][metric]['median']
    def paired(backend, before, after):
        t = {(x['layout'], x['repeat']): x['qps'] for x in scaling['trials'] if x['backend'] == backend and x['clients'] == 32 and x['op'] == 'concurrent_read'}
        return st.median(t[after, r]/t[before, r] for r in range(3))

    d = Document('report-final', 'FilmDB | 数据库性能与功能评估')
    d.p('文件与数据库：\n访问路径、事务与性能边界', 'title')
    d.p('PostgreSQL 14.24、openGauss 6.0.1 与 C++ 文件程序的实验比较', 'small')
    d.p('姓名：'+args.name+'　　　　　　　　学号：'+args.student_id, 'small')
    d.h('1  问题与实验设计')
    d.p('实验回答两个问题：数据库相对直接操作文件提供什么优势；在给定评价标准下，PostgreSQL 与 openGauss 哪个更合适。比较标准包括查询和同步更新耗时、并发吞吐与尾延迟，以及约束和事务的可验证行为。')
    d.p('主要发现是：持久索引决定了连接查询的访问成本；文件偏移索引的稳态点查很快；本次 PostgreSQL 的扫描、聚合和更新表现较好。并发测试还发现，更快的数据库可能先把客户端跑满，因此曲线平台必须结合两端资源干预解释。')
    d.table([['规模', '电影', '人物', '演职关系'], ['1×', '9,204', '16,489', '46,850'],
             ['10×', '92,040', '164,890', '468,500'], ['100×', '920,400', '1,648,900', '4,685,000']], [62, 140, 140, 149])
    d.p('四张核心表来自课程 filmdb.sql，国家表固定 185 行；其余表复制并平移主外键，replica 区分副本唯一约束，保留原有字段分布和 NULL。100× TSV 约 161.1 MiB，适合观察扫描规模趋势，但复制扩容没有增加独立真实样本，也不构成超内存场景。')
    d.h('环境与公平性')
    d.p('Ubuntu 22.04、双路 AMD EPYC 7542、NVMe。原实验数据库与文件使用 CPU 8–15，数据库客户端使用 16–17；两库轮流运行。数据库各限 8 GiB，文件、运行器和客户端共处 4 GiB 作用域，不能据此比较单位内存效率。核心没有独占预留。')
    d.p('两库 shared_buffers=512 MB、work_mem=4 MB；关闭单查询内部并行，开启 fsync、同步提交和 full_page_writes。串行查询先执行一次相同操作预热；并发统一预热 5 s、测量 20 s。每配置通常 3 轮，展示中位数和 min/max；索引构建每档仅一次。')
    d.p('原性能实验 318 条计时记录；扩展性补实验 76 轮。2026-10-13 的新版另做正确性与查询覆盖验证，使用独立输出目录。性能数字始终引用当时的源码快照；新版计时字段不能反向补写到旧数据中。', 'small')

    d.page('2  规模变化：扫描、更新、连接与聚合')
    d.image('serial-scales.png', 337, '图 1  三档规模、三轮中位数及 min/max；横纵轴均为对数尺度。毫秒单位一致，误差线过短时可能被点遮住。')
    d.p('扫描、文件更新和文件连接随数据量明显增长。数据库连接通过 credits(peopleid,movieid) 与 movies 主键索引查找，三档固定返回 54 行；文件实现每次扫描关联表和电影表，因此两者工作量随规模变化的方式不同。聚合三档均输出 1,511 组；文件用 std::map，两库使用 HashAggregate。')
    rows = [['100× 耗时 ms', '文件', 'PostgreSQL', 'openGauss']]
    for op, title, kw in [('search_count', 'The 子串计数', 'The'), ('update', '姓名同步更新', ''), ('join', '人物连接', ''), ('aggregate', '国家/年份聚合', '')]:
        row = [title]
        for b in names:
            v = g(b, 100, op, kw)['metrics']['elapsed_s']
            row.append(f'{v["median"]*1000:.2f}\n[{v["min"]*1000:.2f}, {v["max"]*1000:.2f}]')
        rows.append(row)
    d.table(rows, [137, 118, 118, 118])
    d.p('姓名更新把 first_name 和 surname 中每个 T 替换为 Ttt，每轮从原始姓名恢复。文件计入临时文件写入、fsync 与原子替换，SQL 计入 BEGIN、UPDATE 和同步 COMMIT；100× 两侧均修改 128,700 行。', 'small')
    d.p('表内为中位数 [最小值, 最大值]。数据库连接的数量级优势主要反映持久索引与临时扫描的差异。若文件也维护永久连接索引，差距可能改变；这些结果不能代表任意文件格式或最佳优化实现。', 'small')

    d.page('3  返回量、点查与计时口径')
    d.p('标题检索是区分大小写的字面子串匹配：SQL 用 strpos(title,$1)>0，文件用 string::find。两种实现分别只返回计数或消费全部 movieid/title，计时包括结果传输和摘要计算，不逐行打印。两库实际计划都是顺序扫描。')
    rows = [['100× 关键词', '返回行数', '文件 ms', 'PG ms', 'OG ms']]
    for word, count in [('The', 156300), ('Love', 8800), ('Star', 5000)]:
        rows.append([word, f'{count:,}']+[f'{val(b,100,"search_rows",kw=word)*1000:.2f}' for b in names])
    d.table(rows, [110, 95, 95, 95, 96])
    d.h('点查的稳态速度与准备工作分别解释')
    rows = [['100×，100 次', '每次查询 μs', '旧 setup＋整批 s']]
    for label, b, idx in [('文件扫描', 'file', 0), ('文件偏移索引', 'file', 1), ('PostgreSQL', 'postgres', 0), ('openGauss', 'opengauss', 0)]:
        rows.append([label, f'{val(b,100,"point","mean_ms",idx=idx)*1000:.2f}', f'{val(b,100,"point","setup_plus_elapsed_s",idx=idx):.3f}'])
    d.table(rows, [181, 145, 165])
    d.p('旧 setup 包含扫描整个 movies.tsv、收集候选 ID，以及连接、预编译或文件索引加载。数据库约 0.55–0.65 s 的 setup 含有测试程序准备请求的成本，不能解释为数据库本身必须花费这么久初始化。上表总时长只描述旧测试流程，不能据此直接判定普通应用中短任务的优劣。')
    d.p(f'偏移索引构建另需 {val("file",100,"build_index"):.3f} s，仅一次测量，未包含在上表 setup 中。索引装载可在进程内复用，构建成本取决于数据变更频率；实际应用应按连接寿命和查询批数摊销，不能只看一次短循环或一个准备总数。')
    d.h('新版实现的四个时间边界')
    d.table([['阶段', '指标与含义'], ['请求准备', 'request_prepare_s：读取共享请求文件，或生成候选 ID 与序列'],
             ['后端准备', 'backend_setup_s：连接、预编译、文件索引加载'], ['查询批次', 'elapsed_s：执行、传输、结果消费及测量循环'],
             ['索引构建', 'build_index 独立运行；单独记录，按复用次数摊销']], [107, 384])
    d.p('外部请求文件由固定种子一次生成，所有后端读取相同文件并核对 SHA-256。新版已验证计时字段和结果一致性；旧性能记录没有这些分项，因此本报告不虚构其拆分数值。', 'small')

    d.page('4  功能验证与更广的查询覆盖')
    d.p('数据库将关系约束、事务和索引维护统一提供给应用。为给出可观察证据，新版在两套实际实例上增加以下实验，检查的是错误处理与回滚行为，不涉及断电恢复。')
    d.table([['实验', 'PostgreSQL', 'openGauss'], ['插入不存在电影的演职关系', '拒绝；23503', '拒绝；23503'],
             ['先更新 runtime，再触发外键错误', '中间值已变化', '中间值已变化'],
             ['ROLLBACK 后检查原值和四表指纹', '全部恢复', '全部恢复']], [255, 118, 118])
    d.p('操作顺序：BEGIN → 更新一行并读回新值 → 插入非法外键 → 检查错误 SQLSTATE → ROLLBACK → 再读并核对四表指纹。它证明了本次配置下，事务中已经执行的合法更新可以随整笔失败操作撤销。')
    d.p('当前文件程序只实现单文件临时写入、fsync、原子替换与父目录 fsync，未实现跨表外键检查和多步事务。文件方案原则上也能增加这些机制，但应用需要承担相应实现和维护成本。这里没有将功能缺失混入性能耗时，也未声称已经验证所有隔离级别或崩溃恢复。')
    d.h('补充查询：避免只验证一个高关联人物')
    d.table([['关联类别', 'peopleid', '预期输出行数']]+[[j['label'], j['id'], j['expected_rows']] for j in coverage['joins']], [181, 155, 155])
    d.p(f'另测试均匀随机点查与热点点查：热点负载 90% 请求来自前 1% 电影 ID，其余 10% 在全体中均匀选择。每轮 {coverage["requests"]:,} 次、{coverage["repeats"]} 轮；文件偏移索引和两库共 {coverage["cases"]} 个配置轮次，行数与内容摘要全部一致。请求文件、种子、重复编号和源码哈希均保存。')
    if coverage['performance_eligible']:
        d.p('补充运行通过负载准入检查；分项计时保存在 coverage.json，作为独立的串行补实验，不能与旧计时混成同一组重复。')
    else:
        d.p('补充运行时共享服务器负载超过原性能实验阈值，因此使用双核限额和低优先级完成正确性验证，记录均标记为非正式性能测量。这扩展了功能覆盖，但不支持新的热点性能排名。预设的性能补测命令会在负载过高时停止。')

    d.page('5  并发：先识别客户端是否已经饱和')
    d.p('原实验在 10× 数据上测试 1、4、8 个同步客户端，纯读或约 90% 点查＋10% 自动提交 runtime 更新。PG 的吞吐较早放缓，OG 在这个小区间内近线性增长。补实验扩大至 16、32 客户端，并分别增加数据库和客户端 CPU。')
    d.image('scaling/extended-curve.png', 193, '图 2  新基线：数据库 8 核、客户端 2 核；三轮中位数和 min/max。P95 是每轮直方图分位数的跨轮中位数。')
    d.table([['32 客户端纯读', '客户端忙核 / 2', '数据库忙核 / 8']]+[[names[b], f'{q(scaling,"db8_client2",b,"client_cores"):.2f}', f'{q(scaling,"db8_client2",b,"db_cores"):.2f}'] for b in ['postgres', 'opengauss']], [211, 140, 140])
    d.p('PG 先用满客户端的两核，数据库尚有余量；OG 在高并发下先用满数据库八核。客户端承担请求生成、libpq 协议、结果摘要和直方图记录，不能假定这些成本为零。')
    d.image('scaling/resource-interventions.png', 190, '图 3  分别调整两端 CPU；增加核心在低并发时不一定提速。DB 表示数据库，C 表示客户端。')
    d.p('CPU 用量来自客户端进程和数据库 cgroup 的累计 CPU 时间差分，采样间隔 0.5 s。内部采样窗口与完整测量窗口不完全重合，属于近似计数；资源干预提供了额外验证。', 'small')

    d.page('6  干预结果与两道问题的回答')
    rows = [['数据库核 / 客户端核', 'PG 万次/s', 'OG 万次/s']]
    for layout, label in [('db8_client2', '8 / 2'), ('db8_client4', '8 / 4'), ('db12_client4', '12 / 4')]:
        rows.append([label]+[f'{q(scaling,layout,b)/1e4:.2f}' for b in ['postgres', 'opengauss']])
    d.table(rows, [211, 140, 140])
    d.p(f'固定数据库 8 核，客户端 2→4 核，PG 三轮配对吞吐比中位数为 {paired("postgres","db8_client2","db8_client4"):.2f}；固定客户端 4 核，数据库 8→12 核，OG 为 {paired("opengauss","db8_client4","db12_client4"):.2f}。计数与干预共同支持两库先遇到不同位置的 CPU 约束。')
    d.p(f'独立追加 8＋8 核、32 客户端，每库三轮：PG 达 {q(balance,"db8_client8","postgres")/1e4:.2f} 万次/s，数据库约 8 核满载；OG 约 {q(balance,"db8_client8","opengauss")/1e4:.2f} 万次/s。另四个关键条件各测一次 120 s，PG 增加客户端核、OG 增加数据库核的比值分别为 {q(long,"db8_client4","postgres")/q(long,"db8_client2","postgres"):.2f} 和 {q(long,"db12_client4","opengauss")/q(long,"db8_client4","opengauss"):.2f}，复现短窗口方向。')
    d.h('数据库相对直接文件操作的优势是什么？')
    d.p('性能优势取决于访问路径和复用方式：持久索引使固定输出规模的连接避免全表扫描，文件偏移索引也可以获得很低的点查延迟。功能优势体现在统一提供 SQL、约束、索引维护与事务：本次额外观察到了外键拒绝和失败事务回滚，应用无需为每个操作重新编写这些机制。')
    d.h('按什么标准，哪个数据库更合适？')
    d.p('在本次版本、数据和配置下，PG 的扫描、聚合、姓名同步更新和热数据并发吞吐较好；100× 人物连接中位数 OG 较低。数据库选择仍取决于主要负载。这里没有足够证据对完整功能、运维成本、恢复能力或单位内存效率作总体排名。')
    d.table([['原 compact，8 客户端', 'PG 万次/s', 'OG 万次/s']]+[[label]+[f'{val(b,10,op,"throughput_ops_s",c=8)/1e4:.2f}' for b in ['postgres','opengauss']] for op,label in [('concurrent_read','只读'),('mixed','90% 读＋10% 同步写')]], [211,140,140])
    d.h('结论的适用范围')
    d.p('性能结论覆盖预热后的复制电影数据、给定查询和有限重复。共享 CPU/I/O、内存预算不等、短测量和一次性的索引构建测量限制外推；闭环客户端也不能预测固定外部到达率下的排队延迟。新增热点与不同关联数量只完成正确性覆盖，尚不构成性能证据。')
    d.p('补实验换用了同 NUMA 节点中较空闲的核心，并重新测量基线；干预效果在新基线内部比较。独立追加组不混入主矩阵，单次两分钟测量不代表长期容量。两库内部函数成本、冷缓存、超内存 I/O 和崩溃恢复仍未测量。', 'small')

    d.page('7  实现、复现与证据索引')
    d.p('提交文件为 report.pdf 与 filmbench.cpp，分开提交。源码是 Linux C++17 程序，依赖 libpq 和 pthread；建表 SQL 已内置，可用 --ddl 显式覆盖。四个 TSV 由课程 filmdb.sql 中白名单 INSERT 解析生成，不执行原始 SQL。')
    d.p('完整项目地址：https://github.com/luanmenglei/dbh_project1\n本地修订稿与远程仓库可能不同；以随附源码、实验源码快照和哈希为准。', 'small')
    d.h('从课程数据到最小运行')
    d.p('make\npython3 scripts/prepare.py --scales 1\n./build/filmbench --backend file --op search_count --data data/generated/s1 --keyword The\n./build/filmbench --backend file --op build_index --data data/generated/s1', 'code')
    d.p('数据库连接只从命名环境变量读取。配置好自己的 FILMBENCH_DSN 后，使用以下命令导入到专用 schema；--reset 只用于允许重建的实验 schema。', 'small')
    d.p('./build/filmbench --backend postgres --op load --data data/generated/s1 --schema filmbench_s1 --reset\n./build/filmbench --backend postgres --op functional --schema filmbench_s1', 'code')
    d.p('openGauss 使用匹配其认证协议的 libpq 编译客户端。Docker 路线见 deploy_ubuntu.py；已有普通用户 SDK 可用 build_user_clients.py 编译到新目录，避免改动历史二进制。README 给出配置、依赖、完整运行与补测命令。')
    d.table([['证据目录 / 入口', '内容'], ['results/epyc-compact-evidence', f'{DATA["measured_count"]} 条原实验计时、三档数据、原始输出及 555 项统计'],
             ['results/scaling-probe、scaling-balance、scaling-confirmation', '66＋6＋4 轮资源干预与延长窗口'],
             ['results/review-correctness、review-coverage-diverse', '新版独立 oracle、约束/回滚、54 个查询覆盖轮次'],
             ['source-snapshot 与 SHA-256 清单', '实验所用源码；审查不要求当前代码等于历史代码'],
             ['tests/test_harness.py', '服务清理、缺失/损坏证据、python -O 下拒绝无效结果']], [249, 242])
    d.p('性能审查重算统计、核对矩阵和重复编号，并与原始 stdout 对照；输入清单用 SHA-256，查询结果用行数与带 NULL/长度标记的无序摘要。摘要可检测常见错误，不是数学上的无碰撞证明。新版与原版源代码各有身份记录。', 'small')
    d.p('提交源码 SHA-256：'+source_hash, 'small')
    d.save()
    submission = OUT/'submission'
    submission.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OUT/'pdf/report-final.pdf', submission/'report.pdf')
    shutil.copyfile(ROOT/'src/filmbench.cpp', submission/'filmbench.cpp')
    (OUT/'submission-manifest.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in submission.iterdir() if p.is_file()}, indent=2))
    print(submission)


if __name__ == '__main__':
    main()
