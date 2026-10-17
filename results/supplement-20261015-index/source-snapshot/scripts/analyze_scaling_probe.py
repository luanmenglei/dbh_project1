#!/usr/bin/env python3
"""Analyze scaling trial CPU counters and paired resource interventions."""
from evidence import require, verify_source
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics as st


def proc_cpu(sample):
    # /proc/PID/stat fields 14/15; command name may contain spaces or parentheses.
    fields=sample['client_stat'].rsplit(')',1)[1].split()
    return (int(fields[11])+int(fields[12]))/sample['clock_ticks']


def db_cpu(sample):
    return int(dict(line.split() for line in sample['db_cpu.stat'].splitlines())['usage_usec'])/1e6


def analyze(path,partial=False):
    plan=json.loads((path/'plan.json').read_text())
    records=[json.loads(s) for s in (path/'records.jsonl').read_text().splitlines()]
    measured=[r for r in records if r['measured']]
    expected={(t['layout'],t['backend'],t['op'],t['clients'],t['repeat']) for t in plan['plan']}
    actual={(r['layout'],r['backend'],r['op'],r['clients'],r['repeat']) for r in measured}
    require(len(actual)==len(measured) and actual<=expected, 'analyze_scaling_probe.py:27: validation failed')
    if not partial:
        require((path/'COMPLETE').exists() and not (path/'FAILED').exists() and actual==expected, 'analyze_scaling_probe.py:29: validation failed')
    def cpuset(s):
        values=set()
        for part in s.split(','):
            lo,_,hi=part.partition('-');values.update(range(int(lo),int(hi or lo)+1))
        return values
    trials=[];groups=defaultdict(list)
    for r in measured:
        require(json.loads((path/(r['log']+'.stdout')).read_text()) == r['result'], 'Record differs from raw output')
        v=r['result'];require(v['valid'] and v['errors']==0, 'analyze_scaling_probe.py:37: validation failed')
        raw=[json.loads(s) for s in (path/(r['log']+'-resources.jsonl')).read_text().splitlines()]
        # Exclude process setup and warmup conservatively. Window is interior,
        # because unmodified C++ does not export its steady_clock start timestamp.
        first=raw[0]['monotonic_s']
        ss=[s for s in raw if s['monotonic_s']>=first+plan['warmup']+1 and 'client_stat' in s and 'db_cpu.stat' in s]
        require(len(ss)>=2, 'Insufficient interior CPU samples')
        start,end=ss[0],ss[-1];seconds=end['monotonic_s']-start['monotonic_s']
        allowed=cpuset(r['client_cpus']);db_allowed=cpuset(r['db_cpus'])
        require(not allowed & db_allowed and len(allowed|db_allowed)<=16, 'analyze_scaling_probe.py:46: validation failed')
        for sample in ss:
            status=dict(line.split(':',1) for line in sample['client_status'].splitlines() if ':' in line)
            require(cpuset(status['Cpus_allowed_list'].strip())==allowed, 'Client affinity changed')
        client=(proc_cpu(end)-proc_cpu(start))/seconds
        db=(db_cpu(end)-db_cpu(start))/seconds
        require(client>=0 and db>=0, 'analyze_scaling_probe.py:52: validation failed')
        qps=v['throughput_ops_s']
        row={k:r[k] for k in ['layout','backend','op','clients','repeat','log']}
        row.update(qps=qps,p95_ms=v['p95_ms'],p99_ms=v['p99_ms'],read_p95_ms=v['read_p95_ms'],write_p95_ms=v['write_p95_ms'],
                   client_cores=client,db_cores=db,client_cpu_us_per_op=client/qps*1e6,db_cpu_us_per_op=db/qps*1e6,
                   sample_window_s=seconds,db_memory_mib_max=max(int(s.get('db_memory.current',0)) for s in ss)/2**20)
        for k in ['oom','oom_kill']:
            for s in ss:
                events=dict(line.split() for line in s.get('db_memory.events','').splitlines())
                require(int(events.get(k,0))==0, 'Memory failure')
        trials.append(row);groups[(r['layout'],r['backend'],r['op'],r['clients'])].append(row)
    summary=[]
    for key,rows in sorted(groups.items()):
        result=dict(zip(['layout','backend','op','clients'],key),n=len(rows),metrics={})
        for metric in ['qps','p95_ms','p99_ms','client_cores','db_cores','client_cpu_us_per_op','db_cpu_us_per_op','db_memory_mib_max']:
            vv=[r[metric] for r in rows];result['metrics'][metric]={'median':st.median(vv),'min':min(vv),'max':max(vv)}
        summary.append(result)
    out={'complete':not partial,'expected':len(expected),'observed':len(actual),'cpu_method':'interior sample deltas after process start + warmup + 1s; approximate CPU/op uses full trial QPS','trials':trials,'groups':summary}
    root=Path(__file__).resolve().parents[1]
    hashes=json.loads((path/'source-sha256.json').read_text())
    for name,digest in hashes.items():
        if name.startswith(('src/','sql/','scripts/')):
            verify_source(path, root, name, digest)
    baseline=root/'results/epyc-compact-evidence/formal-source-sha256.txt'
    if baseline.exists():
        prior={name:digest for digest,name in (line.split(maxsplit=1) for line in baseline.read_text().splitlines())}
        for name in ['build/filmbench','build/filmbench-opengauss']:
            require(hashes[name]==prior[name], 'Benchmark binary differs from original experiment')
    (path/'scaling-analysis.json').write_text(json.dumps(out,indent=2))
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('results',type=Path);p.add_argument('--partial',action='store_true');a=p.parse_args()
    out=analyze(a.results,a.partial)
    print(f"{out['observed']}/{out['expected']} trials")
    for g in out['groups']:
        m=g['metrics'];print(g['layout'],g['backend'],g['op'],g['clients'],f"n={g['n']}",*[f"{k}={m[k]['median']:.3f}" for k in ['qps','client_cores','db_cores','p95_ms']])
