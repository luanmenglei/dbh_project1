#!/usr/bin/env python3
"""Plot completed scaling experiment, including paired intervention ratios."""
import argparse
import json
from pathlib import Path
import statistics as st
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from analyze_scaling_probe import analyze


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('results',type=Path);p.add_argument('--out',type=Path,default=Path('output/figures/scaling'));a=p.parse_args()
    data=analyze(a.results);a.out.mkdir(parents=True,exist_ok=True)
    def group(layout,b,c,op='concurrent_read'):
        return next(g for g in data['groups'] if (g['layout'],g['backend'],g['clients'],g['op'])==(layout,b,c,op))
    colors={'postgres':'#176c9d','opengauss':'#d67932'}
    names={'postgres':'PostgreSQL','opengauss':'openGauss'}
    plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':200})
    def error(ax,x,vals,label,color,factor=1,style='-'):
        ax.errorbar(x,[v['median']*factor for v in vals],yerr=[[(v['median']-v['min'])*factor for v in vals],[(v['max']-v['median'])*factor for v in vals]],label=label,color=color,marker='o',capsize=3,linestyle=style)
    fig,axes=plt.subplots(1,2,figsize=(7.3,2.85))
    for b in colors:
        for ax,metric,factor,label in [(axes[0],'qps',.001,'Throughput (k ops/s)'),(axes[1],'p95_ms',1,'P95 latency (ms)')]:
            error(ax,[1,4,8,16,32],[group('db8_client2',b,c)['metrics'][metric] for c in [1,4,8,16,32]],names[b],colors[b],factor)
            ax.set_xlabel('Concurrent clients');ax.set_ylabel(label);ax.set_xticks([1,8,16,32]);ax.grid(alpha=.2);ax.set_ylim(bottom=0)
    axes[0].legend();fig.suptitle('Same resource budget: 8 database + 2 client cores');fig.tight_layout();fig.savefig(a.out/'extended-curve.png');plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(7.3,2.85))
    for ax,metric,limit,title in [(axes[0],'client_cores',2,'Client CPU'),(axes[1],'db_cores',8,'Database CPU')]:
        for b in colors:error(ax,[1,4,8,16,32],[group('db8_client2',b,c)['metrics'][metric] for c in [1,4,8,16,32]],names[b],colors[b])
        ax.axhline(limit,color='#666666',ls=':',label=f'{limit}-core allocation');ax.set_xlabel('Concurrent clients');ax.set_ylabel('Average busy cores');ax.set_title(title);ax.set_ylim(0,limit*1.1);ax.grid(alpha=.2);ax.set_xticks([1,8,16,32])
    axes[0].legend(fontsize=8);fig.tight_layout();fig.savefig(a.out/'cpu-bottleneck.png');plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(7.3,2.9));layouts=['db8_client2','db8_client4','db12_client4']
    for ax,c in zip(axes,[8,32]):
        for b in colors:error(ax,[0,1,2],[group(layout,b,c)['metrics']['qps'] for layout in layouts],names[b],colors[b],.001)
        ax.set_xticks([0,1,2],['DB8 / C2','DB8 / C4','DB12 / C4']);ax.set_title(f'{c} concurrent clients');ax.set_ylabel('Throughput (k ops/s)');ax.set_ylim(bottom=0);ax.grid(alpha=.2)
    axes[0].legend();fig.tight_layout();fig.savefig(a.out/'resource-interventions.png');plt.close(fig)
    ratios=[]
    for b in colors:
        for c in [8,32]:
            for lo,hi in [('db8_client2','db8_client4'),('db8_client4','db12_client4')]:
                rows={}
                for t in data['trials']:
                    if t['backend']==b and t['clients']==c and t['op']=='concurrent_read' and t['layout'] in [lo,hi]:rows[t['layout'],t['repeat']]=t['qps']
                rr=[rows[hi,r]/rows[lo,r] for r in range(3)]
                ratios.append(dict(backend=b,clients=c,from_layout=lo,to_layout=hi,paired_ratios=rr,median=st.median(rr),minimum=min(rr),maximum=max(rr)))
    (a.results/'intervention-ratios.json').write_text(json.dumps(ratios,indent=2))
    print(json.dumps(ratios,indent=2))


if __name__=='__main__':main()
