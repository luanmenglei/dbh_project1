#!/usr/bin/env python3
"""Long-window checks of the four key scaling interventions (not extra matrix repeats)."""
from evidence import require, verify_source
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import run_suite as suite
from run_scaling_probe import LAYOUTS,monitor

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--dry-run',action='store_true');p.add_argument('--balance',action='store_true');a=p.parse_args()
    conditions=[('db8_client2','postgres'),('db8_client4','opengauss'),('db8_client4','postgres'),('db12_client4','opengauss')]
    plan=[dict(layout=l,backend=b,op='concurrent_read',clients=32,repeat=0) for l,b in conditions]
    warm,duration=10,120
    if a.balance:
        LAYOUTS['db8_client8']=('8-15','24-31')
        plan=[dict(layout='db8_client8',backend=b,op='concurrent_read',clients=32,repeat=r) for r in range(3) for b in (['postgres','opengauss'] if r%2==0 else ['opengauss','postgres'])]
        warm,duration=5,20
    if a.dry_run:print(json.dumps({'plan':plan,'warmup':warm,'duration':duration},indent=2));return
    args=SimpleNamespace(out=a.out,config=ROOT/'deploy/state/targets.json',data=ROOT/'data/generated',manage_user_services=True,manage_containers=False,timeout=1800)
    suite.monitor=monitor;runner=suite.Runner(args);original=copy.deepcopy(runner.config)
    (runner.out/'plan.json').write_text(json.dumps({'plan':plan,'warmup':warm,'duration':duration,'layouts':LAYOUTS,'purpose':'adaptive extra client headroom' if a.balance else 'single long-window confirmation per condition; separate from main three-repeat matrix'},indent=2))
    paths=[Path(__file__),ROOT/'scripts/run_scaling_probe.py',ROOT/'scripts/run_suite.py',ROOT/'src/filmbench.cpp',ROOT/'sql/schema.sql',ROOT/'build/filmbench',ROOT/'build/filmbench-opengauss']
    (runner.out/'source-sha256.json').write_text(json.dumps({str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in paths},indent=2))
    try:
        from prepare import checksum
        manifest=json.loads((args.data/'s10/manifest.json').read_text())
        for table,info in manifest['files'].items():require(checksum(args.data/'s10'/f'{table}.tsv')==info['sha256'], 'run_scaling_confirmation.py:34: validation failed')
        references={t:runner.call('file',10,'fingerprint',['--table',t]) for t in manifest['counts']}
        for i,t in enumerate(plan):
            mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
            require(mem['MemAvailable']>32*2**30 and shutil.disk_usage(ROOT).free>40*2**30, 'Resource guard')
            runner.stop_user_services();runner.active=None;runner.config=copy.deepcopy(original)
            dbcpus,clientcpus=LAYOUTS[t['layout']]
            for b in ['postgres','opengauss']:
                start=runner.config[b]['start_command'];start[start.index('taskset')+2]=dbcpus
                runner.config[b]['client_prefix']=['taskset','-c',clientcpus,'nice','-n','10']
            runner.activate(t['backend']);print(f'Trial {i+1}/{len(plan)} '+json.dumps(t),flush=True)
            runner.load(t['backend'],10,phase='restore')
            for table in references:runner.equal(references[table],runner.call(t['backend'],10,'fingerprint',['--table',table]),'restored '+table)
            snapshot=runner.call(t['backend'],10,'snapshot',phase='before',layout=t['layout'])
            settings={x[0]:x[1] for x in snapshot['settings']};require(settings['fsync']=='on' and settings['synchronous_commit']=='on', 'run_scaling_confirmation.py:48: validation failed')
            r=runner.call(t['backend'],10,t['op'],['--clients','32','--warmup',str(warm),'--duration',str(duration),'--seed',str(20261013+t['repeat'])],measured=True,layout=t['layout'],clients=32,repeat=t['repeat'],db_cpus=dbcpus,client_cpus=clientcpus)
            require(r['valid'] and r['errors']==0, 'run_scaling_confirmation.py:50: validation failed');print('Result '+json.dumps(r),flush=True)
        (runner.out/'COMPLETE').write_text(suite.utc()+'\n')
    except BaseException:
        (runner.out/'FAILED').write_text(suite.utc()+'\n');raise
    finally:runner.stop_user_services()


if __name__=='__main__':main()
