#!/usr/bin/env python3
"""Create isolated Docker instances on an x86_64 Ubuntu server; no deletion of existing containers."""
import argparse
import json
import os
from pathlib import Path
import platform
import secrets
import shlex
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.run(['docker',*args],check=True,text=True,**kwargs)


def private(path,text):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(text)


def pinned(image):
    docker('pull',image)
    value=docker('image','inspect',image,'--format','{{json .RepoDigests}}',capture_output=True)
    digests=json.loads(value.stdout)
    if not digests:
        raise RuntimeError('Image has no registry digest; select a registry image with a pinned digest')
    return digests[0]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpus',required=True,help='Same physical CPU IDs for either database, e.g. 0-7 (inspect topology first)')
    p.add_argument('--mems',required=True,help='NUMA node IDs, e.g. 0')
    p.add_argument('--memory',default='8g')
    p.add_argument('--postgres-image',default='postgres:18')
    p.add_argument('--opengauss-image',default='opengauss/opengauss-server:latest')
    p.add_argument('--state',type=Path,default=ROOT/'deploy/state')
    a=p.parse_args()
    if platform.system()!='Linux' or platform.machine() not in ['x86_64','amd64']:
        p.error('This deployment script targets Linux x86_64; do not benchmark through architecture emulation')
    state=a.state.resolve();state.mkdir(parents=True,exist_ok=False);state.chmod(0o700)
    # Pull mutable tags once, then use their immutable digests for every subsequent start.
    images={'postgres':pinned(a.postgres_image),'opengauss':pinned(a.opengauss_image)}
    (state/'images.json').write_text(json.dumps(images,indent=2)+'\n')
    passwords={k:'Aa9#'+secrets.token_hex(12) for k in images}
    private(state/'postgres.env',f'POSTGRES_USER=filmbench\nPOSTGRES_DB=filmbench\nPOSTGRES_PASSWORD={passwords["postgres"]}\nPOSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C\nPGDATA=/var/lib/postgresql/data\n')
    private(state/'opengauss.env',f'GS_PASSWORD={passwords["opengauss"]}\nGS_USERNAME=filmbench\nGS_USER_PASSWORD={passwords["opengauss"]}\nGS_DB=filmbench\n')
    exports=[]
    for engine,port in [('postgres',55432),('opengauss',55433)]:
        dsn=f'host=127.0.0.1 port={port} dbname=filmbench user=filmbench password={passwords[engine]} connect_timeout=10'
        exports.append(f'export {engine.upper()}_DSN={shlex.quote(dsn)}')
        directory=state/(engine+'-data');directory.mkdir()
        target='/var/lib/postgresql' if engine=='postgres' else '/var/lib/opengauss'
        args=['create','--name','filmbench-'+engine,'--cpuset-cpus',a.cpus,'--cpuset-mems',a.mems,
              '--memory',a.memory,'--memory-swap',a.memory,'--shm-size','1g',
              '--ulimit','nofile=65536:65536','--env-file',str(state/(engine+'.env')),
              '--publish',f'127.0.0.1:{port}:5432','--mount',f'type=bind,src={directory},dst={target}']
        if engine=='opengauss':
            # The official image documents privileged mode as its launch requirement.
            args+=['--privileged']
        args.append(images[engine])
        if engine=='postgres':
            args+=['postgres','-c','shared_buffers=512MB','-c','max_connections=128','-c','work_mem=4MB',
                   '-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on',
                   '-c','max_parallel_workers_per_gather=0']
        docker(*args)
    private(state/'connections.sh','\n'.join(exports)+'\n')
    config=json.loads((ROOT/'deploy/targets.example.json').read_text())
    (state/'targets.json').write_text(json.dumps(config,indent=2)+'\n')
    # Initialization is sequential; both instances are stopped at completion.
    for engine in images:
        name='filmbench-'+engine;docker('start',name)
        ready=['exec',name,'pg_isready','-U','filmbench','-d','filmbench'] if engine=='postgres' else [
            'exec',name,'su','-','omm','-c',"gsql -d filmbench -c 'SELECT 1' -v ON_ERROR_STOP=1"]
        deadline=time.monotonic()+240
        while True:
            r=subprocess.run(['docker',*ready],capture_output=True,text=True)
            if r.returncode==0:break
            if time.monotonic()>deadline:
                raise RuntimeError(f'{name} failed readiness; inspect docker logs {name}')
            time.sleep(2)
        if engine=='opengauss':
            docker('exec',name,'su','-','omm','-c',
                   "gsql -d filmbench -v ON_ERROR_STOP=1 -c 'GRANT ALL PRIVILEGES ON DATABASE filmbench TO filmbench'")
            docker('exec',name,'su','-','omm','-c',
                   "gs_guc set -D /var/lib/opengauss/data -c 'shared_buffers=512MB' -c 'max_connections=128' -c 'work_mem=4MB' -c 'fsync=on' -c 'synchronous_commit=on' -c 'full_page_writes=on' -c 'query_dop=1'")
        docker('stop',name)
    print('Created and stopped both instances. Image digests saved in',state/'images.json')
    print('Load connection variables with: source',state/'connections.sh')
    print('Next: run the small smoke suite before the full experiment. No benchmark results have been generated.')


if __name__=='__main__':
    main()
