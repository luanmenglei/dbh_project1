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
from evidence import require

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'report'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='')
    parser.add_argument('--student-id', default='')
    parser.add_argument('--typst', default='typst')
    parser.add_argument('--font', default='Microsoft YaHei')
    args = parser.parse_args()
    original = audit(ROOT / 'results/epyc-compact-evidence')
    scaling = analyze(ROOT / 'results/scaling-probe')
    balance = analyze(ROOT / 'results/scaling-balance')
    long = analyze(ROOT / 'results/scaling-confirmation')
    coverage = audit_coverage(ROOT / 'results/review-coverage-diverse')
    verification = json.loads((ROOT / 'results/review-correctness/verification.json').read_text())
    source_hash = hashlib.sha256((ROOT / 'src/filmbench.cpp').read_bytes()).hexdigest()
    require(verification['source_sha256'] == source_hash, 'Source differs from verified C++')
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
            'point': [], 'cpu': [], 'interventions': [], 'compact': [],
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
        data['point'].append([label, f'{m["mean_ms"]["median"] * 1000:.2f}',
                              f'{m["setup_plus_elapsed_s"]["median"]:.3f}'])
    for b in backends[1:]:
        m = sg(scaling, 'db8_client2', b)
        data['cpu'].append([names[b], f'{m["client_cores"]["median"]:.2f}', f'{m["db_cores"]["median"]:.2f}'])
    layouts = ['db8_client2', 'db8_client4', 'db12_client4']
    for layout, label in zip(layouts, ['8 / 2', '8 / 4', '12 / 4']):
        data['interventions'].append([label] + [f'{q(scaling, layout, b) / 1e4:.2f}' for b in backends[1:]])
    for op, label in [('concurrent_read', '只读'), ('mixed', '90% 读 + 10% 同步写')]:
        data['compact'].append([label] + [f'{g(b, 10, op, clients=8)["throughput_ops_s"]["median"] / 1e4:.2f}' for b in backends[1:]])
    REPORT.mkdir(exist_ok=True)
    (REPORT / 'data.json').write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.spines.top': False,
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
        fig.tight_layout(pad=1.3)
        fig.savefig(figures / name, metadata={'Date': None}, bbox_inches='tight')
        plt.close(fig)
        svg = figures / name
        svg.write_bytes(('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines()) + '\n').encode('utf-8'))

    fig, axes = plt.subplots(2, 2, figsize=(7.6, 5.1))
    for ax, (op, _, kw), title in zip(axes.flat, ops,
                                    ['Substring count: The', 'Durable name update',
                                     'Join: 54 output rows', 'Country/year aggregate']):
        for b in backends:
            error(ax, [1, 10, 100], [g(b, s, op, kw)['elapsed_s'] for s in (1, 10, 100)], b, 1000)
        ax.set(xscale='log', yscale='log', xlabel='Dataset scale', ylabel='Elapsed time (ms)', title=title)
        ax.set_xticks([1, 10, 100], ['1x', '10x', '100x'])
    axes[0, 0].legend(fontsize=8)
    save(fig, 'serial.svg')
    fig, axes = plt.subplots(1, 2, figsize=(7.6, 2.65))
    for ax, metric, factor, ylabel in zip(axes, ['qps', 'p95_ms'], [.001, 1],
                                        ['Throughput (k ops/s)', 'P95 latency (ms)']):
        for b in backends[1:]:
            error(ax, [1, 4, 8, 16, 32], [sg(scaling, 'db8_client2', b, c)[metric] for c in [1, 4, 8, 16, 32]], b, factor)
        ax.set(xlabel='Concurrent clients', ylabel=ylabel, ylim=(0, None))
        ax.set_xticks([1, 8, 16, 32])
    axes[0].legend(fontsize=8)
    save(fig, 'concurrency.svg')
    fig, axes = plt.subplots(1, 2, figsize=(7.6, 2.65))
    for ax, c in zip(axes, [8, 32]):
        for b in backends[1:]:
            error(ax, [0, 1, 2], [sg(scaling, layout, b, c)['qps'] for layout in layouts], b, .001)
        ax.set(ylabel='Throughput (k ops/s)', title=f'{c} concurrent clients', ylim=(0, None))
        ax.set_xticks([0, 1, 2], ['DB8 / C2', 'DB8 / C4', 'DB12 / C4'])
    axes[0].legend(fontsize=8)
    save(fig, 'interventions.svg')

    output = ROOT / 'output'
    (output / 'pdf').mkdir(parents=True, exist_ok=True)
    subprocess.run([args.typst, 'compile', '--input', f'name={args.name}', '--input',
                    f'student-id={args.student_id}', '--input', f'font={args.font}', str(REPORT / 'report.typ'),
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
