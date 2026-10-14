#!/usr/bin/env python3
"""Causal scaling follow-up, private instances only; never modifies baseline sources."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import time
from types import SimpleNamespace
import run_suite as suite

ROOT = Path(__file__).resolve().parents[1]
LAYOUTS = {'db8_client2': ('8-15','24,25'),
           'db8_client4': ('8-15','24-27'),
           'db12_client4': ('8-15,20-23','24-27')}


def monitor(stop,pid,destination,cgroup=None):
    with destination.open('w') as f:
        while not stop.is_set():
            sample={'time':suite.utc(),'monotonic_s':time.monotonic(),'client_pid':pid,'clock_ticks':os.sysconf('SC_CLK_TCK')}
            paths={'client_stat':f'/proc/{pid}/stat','client_status':f'/proc/{pid}/status',
                   'client_io':f'/proc/{pid}/io','host_cpu':'/proc/stat','host_load':'/proc/loadavg'}
            if cgroup:
                for name in ['cpu.stat','memory.current','memory.events','io.stat']:
                    paths['db_'+name]=str(Path(cgroup)/name)
            for key,path in paths.items():
                try: sample[key]=Path(path).read_text()
                except OSError: pass
            f.write(json.dumps(sample)+'\n');f.flush();stop.wait(.5)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--smoke',action='store_true')
    ap.add_argument('--dry-run',action='store_true')
    a=ap.parse_args()
    plan=[]
    for repeat in range(1 if a.smoke else 3):
        blocks=list(LAYOUTS);random.Random(20261013+repeat).shuffle(blocks)
        for layout in blocks:
            backends=['postgres','opengauss']
            if repeat%2:backends.reverse()
            for backend in backends:
                conditions=[('concurrent_read',c) for c in ([1,4,8,16,32] if layout=='db8_client2' else [8,32])]
                if layout=='db8_client2':conditions += [('mixed',8),('mixed',32)]
                if a.smoke:conditions=[('concurrent_read',2)]
                random.Random(20261013+repeat).shuffle(conditions)
                for op,clients in conditions:
                    plan.append(dict(layout=layout,backend=backend,op=op,clients=clients,repeat=repeat))
    warm,duration=(0,2) if a.smoke else (5,20)
    if a.dry_run:
        print(json.dumps({'trials':len(plan),'window_s':len(plan)*(warm+duration),'layouts':LAYOUTS,'plan':plan},indent=2));return
    args=SimpleNamespace(out=a.out,config=ROOT/'deploy/state/targets.json',data=ROOT/'data/generated',
                         manage_user_services=True,manage_containers=False,timeout=1800)
    suite.monitor=monitor
    runner=suite.Runner(args)
    original=copy.deepcopy(runner.config)
    (runner.out/'plan.json').write_text(json.dumps({'plan':plan,'warmup':warm,'duration':duration,'layouts':LAYOUTS},indent=2))
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),ROOT/'scripts/run_suite.py',ROOT/'src/filmbench.cpp',ROOT/'sql/schema.sql',ROOT/'build/filmbench',ROOT/'build/filmbench-opengauss']}
    (runner.out/'source-sha256.json').write_text(json.dumps(hashes,indent=2))
    try:
        manifest=json.loads((args.data/'s10/manifest.json').read_text())
        from prepare import checksum
        for table,info in manifest['files'].items():
            assert checksum(args.data/'s10'/f'{table}.tsv')==info['sha256']
        (runner.out/'s10-manifest.json').write_text(json.dumps(manifest,indent=2))
        references={table:runner.call('file',10,'fingerprint',['--table',table]) for table in manifest['counts']}
        previous=None
        for i,trial in enumerate(plan):
            mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
            health={'time':suite.utc(),'mem_available':mem['MemAvailable'],'disk_free':shutil.disk_usage(ROOT).free,
                    'loadavg':Path('/proc/loadavg').read_text().strip()}
            with (runner.out/'health.jsonl').open('a') as stream:stream.write(json.dumps(health)+'\n')
            if health['mem_available']<32*2**30 or health['disk_free']<40*2**30 or float(health['loadavg'].split()[0])>32:
                raise RuntimeError('Shared-server resource guard: stop experiment and release services')
            layout,backend=trial['layout'],trial['backend']
            if previous!=(layout,backend):
                runner.stop_user_services();runner.active=None;runner.config=copy.deepcopy(original)
                dbcpus,clientcpus=LAYOUTS[layout]
                for b in ['postgres','opengauss']:
                    start=runner.config[b]['start_command'];pos=start.index('taskset');assert start[pos+1]=='-c';start[pos+2]=dbcpus
                    runner.config[b]['client_prefix']=['taskset','-c',clientcpus,'nice','-n','10']
                runner.activate(backend);previous=(layout,backend)
            print(f'Trial {i+1}/{len(plan)} '+json.dumps(trial),flush=True)
            runner.load(backend,10,phase='restore',layout=layout)
            for table in references:
                runner.equal(references[table],runner.call(backend,10,'fingerprint',['--table',table]),'restored '+table)
            snap=runner.call(backend,10,'snapshot',phase='before',layout=layout)
            settings={r[0]:r[1] for r in snap['settings']}
            assert settings['fsync']=='on' and settings['synchronous_commit']=='on'
            dbcpus,clientcpus=LAYOUTS[layout]
            result=runner.call(backend,10,trial['op'],['--clients',str(trial['clients']),'--warmup',str(warm),
                               '--duration',str(duration),'--seed',str(20261013+trial['repeat'])],
                               measured=True,repeat=trial['repeat'],clients=trial['clients'],layout=layout,
                               db_cpus=dbcpus,client_cpus=clientcpus)
            assert result['valid'] and result['errors']==0
            print('Result '+json.dumps(result),flush=True)
        (runner.out/'COMPLETE').write_text(suite.utc()+'\n')
    except BaseException:
        (runner.out/'FAILED').write_text(suite.utc()+'\n');raise
    finally:
        runner.stop_user_services()


if __name__=='__main__':main()
