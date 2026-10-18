#!/usr/bin/env python3
"""Audit evidence, export report data/vector figures, and compile the Typst report."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess

from analyze_results import audit
from analyze_scaling_probe import analyze
from analyze_coverage import analyze as audit_coverage
from analyze_supplement import analyze as audit_supplement
from evidence import require, verify_readable_source

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'report'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default=None)
    parser.add_argument('--student-id', default=None)
    parser.add_argument('--typst', default='typst')
    parser.add_argument('--font', default='Microsoft YaHei')
    parser.add_argument('--verification', type=Path, default=ROOT/'results/review-index-health/verification.json')
    args = parser.parse_args()
    original = audit(ROOT / 'results/epyc-compact-evidence')
    scaling = analyze(ROOT / 'results/scaling-probe')
    balance = analyze(ROOT / 'results/scaling-balance')
    long = analyze(ROOT / 'results/scaling-confirmation')
    coverage = audit_coverage(ROOT / 'results/review-coverage-diverse')
    require((args.verification.parent/'COMPLETE').is_file() and
            not (args.verification.parent/'FAILED').exists(), 'Incomplete source verification')
    verification = json.loads(args.verification.read_text())
    source_hash = hashlib.sha256((ROOT / 'src/filmbench.cpp').read_bytes()).hexdigest()
    verify_readable_source(ROOT, verification['source_sha256'])
    require(all(verification['backends'][b] == 'passed' for b in ('file', 'postgres', 'opengauss')),
            'Correctness checks failed')

    def g(b, scale, op, keyword='', indexed=0, clients=0):
        return next(x for x in original['groups'] if
                    (x['backend'], x['scale'], x['op'], x['keyword'], x['indexed'], x['clients']) ==
                    (b, scale, op, keyword, indexed, clients))['metrics']

    def sg(data, layout, b, clients=32):
        return next(x for x in data['groups'] if
                    (x['layout'], x['backend'], x['op'], x['clients']) ==
                    (layout, b, 'concurrent_read', clients))['metrics']

    def q(data, layout, b):
        return sg(data, layout, b)['qps']['median']

    def paired(b, before, after):
        t = {(x['layout'], x['repeat']): x['qps'] for x in scaling['trials']
             if x['backend'] == b and x['clients'] == 32 and x['op'] == 'concurrent_read'}
        return f'{statistics.median(t[after, r] / t[before, r] for r in range(3)):.2f}'

    backends = ('file', 'postgres', 'opengauss')
    names = {'file': 'TSV file', 'postgres': 'PostgreSQL', 'opengauss': 'openGauss'}
    ops = [('search_count', 'The 子串计数', 'The'), ('update', '姓名同步更新', ''),
           ('join', '人物连接', ''), ('aggregate', '国家 / 年份聚合', '')]
    data = {'source_hash': source_hash, 'measured_count': original['measured_count'],
            'summary_count': original['summary_rows_verified'], 'serial': [], 'search': [],
            'point': [], 'cpu': [], 'interventions': [], 'compact': [], 'mixed_metrics': [],
            'joins': [[j['label'], str(j['id']), str(j['expected_rows'])] for j in coverage['joins']],
            'coverage_cases': coverage['cases'], 'coverage_requests': f'{coverage["requests"]:,}',
            'coverage_repeats': coverage['repeats'], 'performance_eligible': coverage['performance_eligible'],
            'pg_ratio': paired('postgres', 'db8_client2', 'db8_client4'),
            'og_ratio': paired('opengauss', 'db8_client4', 'db12_client4'),
            'balance': [f'{q(balance, "db8_client8", b) / 1e4:.2f}' for b in backends[1:]],
            'long_ratio': [f'{q(long, "db8_client4", "postgres") / q(long, "db8_client2", "postgres"):.2f}',
                           f'{q(long, "db12_client4", "opengauss") / q(long, "db8_client4", "opengauss"):.2f}'],
            'index_build': f'{g("file", 100, "build_index")["elapsed_s"]["median"]:.3f}'}
    for op, label, kw in ops:
        row = [label]
        for b in backends:
            v = g(b, 100, op, kw)['elapsed_s']
            row.append(f'{v["median"] * 1000:.2f}\n[{v["min"] * 1000:.2f}, {v["max"] * 1000:.2f}]')
        data['serial'].append(row)
    for word, count in [('The', 156300), ('Love', 8800), ('Star', 5000)]:
        data['search'].append([word, f'{count:,}'] +
                              [f'{g(b, 100, "search_rows", word)["elapsed_s"]["median"] * 1000:.2f}' for b in backends])
    for label, b, idx in [('文件扫描', 'file', 0), ('文件偏移索引', 'file', 1),
                          ('PostgreSQL', 'postgres', 0), ('openGauss', 'opengauss', 0)]:
        m = g(b, 100, 'point', indexed=idx)
        v = m['mean_ms']
        data['point'].append([label, f'{v["median"] * 1000:.2f}\n[{v["min"] * 1000:.2f}, {v["max"] * 1000:.2f}]',
                              f'{m["setup_plus_elapsed_s"]["median"]:.3f}'])
    for b in backends[1:]:
        m = sg(scaling, 'db8_client2', b)
        data['cpu'].append([names[b], f'{m["client_cores"]["median"]:.2f}', f'{m["db_cores"]["median"]:.2f}'])
    layouts = ['db8_client2', 'db8_client4', 'db12_client4']
    for layout, label in zip(layouts, ['8 / 2', '8 / 4', '12 / 4']):
        data['interventions'].append([label] + [f'{q(scaling, layout, b) / 1e4:.2f}' for b in backends[1:]])
    for op, label in [('concurrent_read', '只读'), ('mixed', '90% 读 + 10% 同步写')]:
        data['compact'].append([label] + [f'{g(b, 10, op, clients=8)["throughput_ops_s"]["median"] / 1e4:.2f}' for b in backends[1:]])
    for op, metric, label, factor, digits in [
        ('concurrent_read', 'throughput_ops_s', '只读吞吐（万次/秒）', .0001, 2),
        ('mixed', 'throughput_ops_s', '混合吞吐（万次/秒）', .0001, 2),
        ('concurrent_read', 'read_p95_ms', '只读负载：读 P95 / ms', 1, 3),
        ('mixed', 'read_p95_ms', '混合负载：读 P95 / ms', 1, 3),
        ('mixed', 'write_p95_ms', '混合负载：写 P95 / ms', 1, 3),
    ]:
        data['mixed_metrics'].append([label] + [
            f'{g(b, 10, op, clients=8)[metric]["median"] * factor:.{digits}f}'
            for b in backends[1:]])
    data['point_batch_ms'] = f'{g("file", 100, "point", indexed=1)["elapsed_s"]["median"] * 1000:.2f}'
    point_path = ROOT/'results/supplement-20261015-point'
    index_path = ROOT/'results/supplement-20261015-index'
    if point_path.exists() or index_path.exists():
        supplement = audit_supplement(point_path, index_path)
        require(supplement['source_sha256'] == verification['source_sha256'],
                'Supplement used a different verified C++ source')

        def formatted(value, digits=2):
            return f'{value["median"]:.{digits}f}\n[{value["min"]:.{digits}f}, {value["max"]:.{digits}f}]'

        points = {(r['backend'], r['distribution']): r for r in supplement['point']}
        sibling_cpus = [str(c) for c in list(range(73, 81))+[88, 89]]
        busiest_sibling = max(sibling_cpus, key=lambda c: supplement['cpu_busy_percent'][c])
        data['supplement'] = dict(point=[], index=[],
                                  load_range=[f'{v:.1f}' for v in supplement['load_range']],
                                  index_build_s=f'{supplement["index_build_s"]:.3f}',
                                  busiest_sibling=busiest_sibling,
                                  client_sibling_cpus=[88, 89],
                                  client_sibling_busy=[f'{supplement["cpu_busy_percent"][str(c)]:.1f}' for c in [88, 89]],
                                  sibling_busy_max=f'{supplement["cpu_busy_percent"][busiest_sibling]:.1f}')
        for backend, label in [('file', '文件偏移索引'), ('postgres', 'PostgreSQL'), ('opengauss', 'openGauss')]:
            data['supplement']['point'].append([
                label, formatted(points[backend, 'uniform']['mean_us']),
                formatted(points[backend, 'hot90']['mean_us']),
                f'{points[backend, "uniform"]["setup_s"]["median"]:.3f}'])
        for row in supplement['index']:
            data['supplement']['index'].append([
                names[row['backend']], formatted(row['with_index_ms']),
                formatted(row['without_index_ms']), formatted(row['speedup'], 1)])
    REPORT.mkdir(exist_ok=True)
    (REPORT / 'data.json').write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font_manager.findfont(args.font, fallback_to_default=False)
    plt.rcParams.update({'font.family': args.font, 'font.size': 9, 'axes.titlesize': 10,
                         'axes.labelsize': 9, 'xtick.labelsize': 8.5, 'ytick.labelsize': 8.5,
                         'axes.unicode_minus': False, 'axes.spines.top': False,
                         'axes.spines.right': False, 'axes.edgecolor': '#999999', 'axes.linewidth': .6,
                         'text.color': '#242424', 'axes.labelcolor': '#444444', 'xtick.color': '#555555',
                         'ytick.color': '#555555', 'legend.frameon': False, 'svg.hashsalt': 'filmbench-report'})
    styles = {'file': ('#888888', '^', ':'), 'postgres': ('#222222', 'o', '-'),
              'opengauss': ('#526b70', 's', '--')}
    figures = REPORT / 'figures'
    figures.mkdir(exist_ok=True)

    def error(ax, x, values, b, factor=1):
        color, marker, line = styles[b]
        ax.errorbar(x, [v['median'] * factor for v in values],
                    yerr=[[(v['median'] - v['min']) * factor for v in values],
                          [(v['max'] - v['median']) * factor for v in values]],
                    color=color, marker=marker, linestyle=line, markersize=4,
                    linewidth=1.2, capsize=2.5, label=names[b])
        ax.grid(axis='y', color='#dddddd', linewidth=.5)
        ax.set_axisbelow(True)

    def save(fig, name):
        fig.savefig(figures / name, metadata={'Date': None}, bbox_inches='tight', pad_inches=.04)
        plt.close(fig)
        svg = figures / name
        svg.write_bytes(('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines()) + '\n').encode('utf-8'))

    def shared_legend(fig, ax, columns):
        handles, labels = ax.get_legend_handles_labels()
        fig.legend(handles, labels, loc='outside upper center', ncol=columns,
                   fontsize=9, handlelength=2.5, columnspacing=2.0)

    names['file'] = '文件扫描'
    fig, axes = plt.subplots(2, 2, figsize=(6.6, 3.9), layout='constrained')
    for ax, (op, _, kw), title in zip(axes.flat, ops,
                                    ['(a) 标题子串计数：The', '(b) 姓名同步更新',
                                     '(c) 人物关系查询', '(d) 按国家和年份分组统计']):
        for b in backends:
            error(ax, [1, 10, 100], [g(b, s, op, kw)['elapsed_s'] for s in (1, 10, 100)], b, 1000)
        ax.set(xscale='log', yscale='log', xlabel='数据规模', ylabel='耗时 / ms', title=title)
        ax.set_xticks([1, 10, 100], ['1×', '10×', '100×'])
        ax.minorticks_off()
    shared_legend(fig, axes[0, 0], 3)
    save(fig, 'serial.svg')
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.25), layout='constrained')
    for ax, metric, factor, ylabel, title in zip(axes, ['qps', 'p95_ms'], [.0001, 1],
                                        ['吞吐量（万次/秒）', 'P95 延迟 / ms'],
                                        ['(a) 吞吐量', '(b) 尾延迟']):
        for b in backends[1:]:
            error(ax, [1, 4, 8, 16, 32], [sg(scaling, 'db8_client2', b, c)[metric] for c in [1, 4, 8, 16, 32]], b, factor)
        ax.set(xlabel='并发客户端数', ylabel=ylabel, title=title, ylim=(0, None))
        ax.set_xticks([1, 4, 8, 16, 32])
    shared_legend(fig, axes[0], 2)
    save(fig, 'concurrency.svg')
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.4), sharey=True, layout='constrained')
    for ax, c in zip(axes, [8, 32]):
        for b in backends[1:]:
            error(ax, [0, 1, 2], [sg(scaling, layout, b, c)['qps'] for layout in layouts], b, .0001)
        ax.set(title=f'({"a" if c == 8 else "b"}) {c} 个客户端', ylim=(0, 22),
               xlabel='绑定核数：数据库 / 客户端', xlim=(-.18, 2.18))
        ax.set_xticks([0, 1, 2], ['8 / 2', '8 / 4', '12 / 4'])
        ax.set_yticks([0, 5, 10, 15, 20])
        ax.tick_params(labelleft=True)
    axes[0].set_ylabel('吞吐量（万次/秒）')
    for x, y, target_y, text, color in [
        (.50, 21, (q(scaling, 'db8_client2', 'postgres') + q(scaling, 'db8_client4', 'postgres')) / 2e4,
         f'客户端扩展 {data["pg_ratio"]}×', styles['postgres'][0]),
        (1.47, 4.7, (q(scaling, 'db8_client4', 'opengauss') + q(scaling, 'db12_client4', 'opengauss')) / 2e4,
         f'数据库扩展 {data["og_ratio"]}×', styles['opengauss'][0]),
    ]:
        axes[1].annotate(text, xy=(x, target_y), xytext=(x, y),
                         ha='center', va='center', fontsize=8.5, color=color,
                         arrowprops=dict(arrowstyle='->', color=color, linewidth=.65),
                         bbox=dict(facecolor='white', edgecolor='none', pad=1.5))
    shared_legend(fig, axes[0], 2)
    save(fig, 'interventions.svg')

    output = ROOT / 'output'
    (output / 'pdf').mkdir(parents=True, exist_ok=True)
    command = [args.typst, 'compile', '--input', f'font={args.font}']
    for key, value in [('name', args.name), ('student-id', args.student_id)]:
        if value is not None:
            command.extend(['--input', f'{key}={value}'])
    subprocess.run(command + [str(REPORT / 'report.typ'),
                              str(output / 'pdf/report-typst.pdf')], check=True)
    submission = output / 'submission'
    submission.mkdir(exist_ok=True)
    shutil.copyfile(output / 'pdf/report-typst.pdf', submission / 'report.pdf')
    shutil.copyfile(ROOT / 'src/filmbench.cpp', submission / 'filmbench.cpp')
    (output / 'submission-manifest.json').write_text(json.dumps(
        {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(submission.iterdir()) if p.is_file()}, indent=2) + '\n')
    print(submission / 'report.pdf')


if __name__ == '__main__':
    main()
