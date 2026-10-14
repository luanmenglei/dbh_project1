#!/usr/bin/env python3
"""Generate report-ready CSV summaries; optionally produce PNG charts with matplotlib."""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
import statistics


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('results',type=Path)
    p.add_argument('--plots',action='store_true')
    a=p.parse_args()
    if not (a.results/'COMPLETE').exists() or (a.results/'FAILED').exists():
        p.error('Refusing to summarize an incomplete/failed suite as valid results')
    records=[json.loads(line) for line in (a.results/'records.jsonl').read_text().splitlines()]
    groups=defaultdict(list)
    for x in records:
        if not x['measured']:
            continue
        r=x['result']
        if r.get('valid',True) is False or r.get('errors',0):
            raise ValueError('Invalid measured run')
        key=(x['backend'],x['scale'],x['op'],x.get('keyword',''),x.get('indexed',0),x.get('clients',0))
        groups[key].append(r)
    rows=[]
    for key, values in sorted(groups.items()):
        for metric in ['elapsed_s','setup_s','mean_ms','throughput_ops_s','p50_ms','p95_ms','p99_ms','read_p95_ms','write_p95_ms','bytes']:
            vals=[v[metric] for v in values if metric in v]
            if vals:
                rows.append(dict(zip(['backend','scale','op','keyword','indexed','clients'],key),metric=metric,
                                 trials=len(vals),median=statistics.median(vals),minimum=min(vals),maximum=max(vals)))
    with (a.results/'summary.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['backend','scale','op','keyword','indexed','clients','metric','trials','median','minimum','maximum'])
        writer.writeheader();writer.writerows(rows)
    if a.plots:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plotdir=a.results/'plots';plotdir.mkdir(exist_ok=True)
        selections=[('search_count','elapsed_s'),('point','mean_ms'),('update','elapsed_s'),
                    ('join','elapsed_s'),('aggregate','elapsed_s'),
                    ('concurrent_read','throughput_ops_s'),('concurrent_read','p95_ms'),
                    ('mixed','throughput_ops_s'),('mixed','p95_ms')]
        for op, metric in selections:
            concurrent=op in ['concurrent_read','mixed']
            series=defaultdict(list)
            for r in rows:
                if r['op']!=op or r['metric']!=metric:
                    continue
                label=r['backend']+(' indexed' if r['indexed'] else '')
                if concurrent:label+=f" s{r['scale']}"
                elif r['keyword']:label+=' '+r['keyword']
                series[label].append(r)
            if not series:
                continue
            fig, ax=plt.subplots(figsize=(8,5))
            for label, points in series.items():
                field='clients' if concurrent else 'scale'
                points.sort(key=lambda r:r[field])
                x=[r[field] for r in points];y=[r['median'] for r in points]
                ax.errorbar(x,y,yerr=[[r['median']-r['minimum'] for r in points],
                                      [r['maximum']-r['median'] for r in points]],marker='o',capsize=3,label=label)
            ax.set_xlabel('Concurrent clients' if concurrent else 'Dataset multiplier')
            ax.set_ylabel(metric);ax.set_title(op+' (median; bars = min/max)')
            ax.grid(alpha=.25);ax.legend(fontsize=8);fig.tight_layout()
            fig.savefig(plotdir/f'{op}-{metric}.png',dpi=180);plt.close(fig)
    print(a.results/'summary.csv')


if __name__=='__main__':
    main()
