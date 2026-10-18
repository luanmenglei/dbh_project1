"""One-off isolated correctness run; does not touch existing database instances."""
from pathlib import Path
import datetime
import getpass
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
RUNTIME = Path('/srv/benchmark/filmbench/runtime')
OUT = ROOT / 'results/review-namespace-20261018'
OUT.mkdir(parents=True, exist_ok=False)
STATE = ROOT / 'private-clusters'
STATE.mkdir(mode=0o700)
report = dict(kind='correctness only; not formal performance results',
              started=datetime.datetime.now(datetime.timezone.utc).isoformat(), backends={},
              source_sha256=hashlib.sha256((ROOT/'src/filmbench.cpp').read_bytes()).hexdigest())
snapshot = OUT/'source-snapshot'
for name in ['src/filmbench.cpp','tests/test_project.py','scripts/prepare.py','verify_namespaces.py']:
    target=snapshot/name
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/name,target)

def command(args, env=None, log=None):
    p=subprocess.run(list(map(str,args)),env=env,cwd=ROOT,capture_output=True,text=True)
    if log:
        (OUT/log).write_text(p.stdout+p.stderr)
    if p.returncode:
        raise RuntimeError(f'{Path(args[0]).name} failed; see {log}: {p.stderr[-1000:]}')
    return p.stdout

spec=importlib.util.spec_from_file_location('project_tests',ROOT/'tests/test_project.py')
tests=importlib.util.module_from_spec(spec);spec.loader.exec_module(tests)
active=None
try:
    # Check all external and openGauss internal ports before initializing anything.
    for port in (55451,55452,55453):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',port))
    command([sys.executable,ROOT/'scripts/build_user_clients.py','--runtime',RUNTIME],log='compile.log')
    report['compiler']=command(['c++','--version']).splitlines()[0]
    tests.BIN=ROOT/'build/postgres-client.sh'
    tests.test_fixture(OUT)
    tests.test_course(OUT)
    report['backends']['file']='passed'
    print('file oracle passed',flush=True)
    for backend,port in [('postgres',55451),('opengauss',55452)]:
        pg=RUNTIME/'postgres'
        gauss=RUNTIME/'opengauss/usr/opengauss'
        bindir=pg/'usr/lib/postgresql/14/bin' if backend=='postgres' else gauss/'bin'
        lib=pg/'usr/lib/x86_64-linux-gnu' if backend=='postgres' else gauss/'lib'
        env=os.environ.copy()
        env.update(LD_LIBRARY_PATH=str(lib),LANG='C.UTF-8')
        if backend=='opengauss':
            env.update(GAUSSHOME=str(gauss),GAUSSLOG=str(STATE/'gausslogs'))
        data=STATE/(backend+'-data')
        sock=STATE/(backend+'-socket');sock.mkdir(mode=0o700)
        pw=STATE/(backend+'-pw')
        password='Aa9#'+secrets.token_hex(12)
        fd=os.open(pw,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f: f.write(password+'\n')
        init=[bindir/('initdb' if backend=='postgres' else 'gs_initdb'),'-D',data,
              '--encoding=UTF8','--locale=C.UTF-8','--pwfile='+str(pw)]
        if backend=='postgres':
            init += ['-L',pg/'usr/share/postgresql/14','--auth-local=scram-sha-256','--auth-host=scram-sha-256']
        else:
            init += ['--nodename=namespace_review','--dbcompatibility=PG']
        try:
            command(init,env,backend+'-init.log')
        finally:
            pw.unlink(missing_ok=True)
        settings=f"\nlisten_addresses='127.0.0.1'\nport={port}\n"
        settings+=f"unix_socket_{'directories' if backend=='postgres' else 'directory'}='{sock}'\n"
        settings+='shared_buffers=128MB\nwork_mem=4MB\nmax_connections=32\nfsync=on\nsynchronous_commit=on\n'
        settings+='max_parallel_workers_per_gather=0\n' if backend=='postgres' else 'query_dop=1\n'
        with (data/'postgresql.conf').open('a') as f: f.write(settings)
        auth='scram-sha-256' if backend=='postgres' else 'sha256'
        (data/'pg_hba.conf').write_text(f'local all all {auth}\nhost all all 127.0.0.1/32 {auth}\n')
        ctl=bindir/('pg_ctl' if backend=='postgres' else 'gs_ctl')
        active=(ctl,data,env,backend)
        start=[ctl,'-D',data,'-l',STATE/(backend+'.log'),'-w','start']
        if backend=='opengauss': start+=['-Z','single_node']
        command(start,env,backend+'-start.log')
        client_env=os.environ.copy()
        client_env['FILMBENCH_DSN']=f'host=127.0.0.1 port={port} dbname=postgres user={getpass.getuser()} password={password} connect_timeout=5'
        tests.BIN=ROOT/f'build/{backend}-client.sh'
        report[backend+'_version']=tests.run(OUT,'snapshot',backend,env=client_env)['version']
        tests.test_fixture(OUT,backend,client_env)
        tests.test_course(OUT,backend,client_env)
        report.setdefault('functional',{})[backend]=tests.run(OUT,'functional',backend,env=client_env)
        report['backends'][backend]='passed'
        command([ctl,'-D',data,'-m','fast','-w','stop'],env,backend+'-stop.log')
        active=None
        print(backend+' oracle passed; temporary server stopped',flush=True)
    report['coverage']=['independent Python oracle','NULL and special characters','query and update parity',
        'index integrity and failed rebuild','external point request stream','s1/s2 course data',
        'read-only execution plans','concurrent read and mixed write','foreign key and rollback','restore after updates']
    report['completed']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    (OUT/'COMPLETE').write_text(report['completed']+'\n')
except Exception:
    (OUT/'FAILED').write_text(datetime.datetime.now(datetime.timezone.utc).isoformat()+'\n')
    raise
finally:
    if active:
        ctl,data,env,backend=active
        try:
            command([ctl,'-D',data,'-m','fast','-w','stop'],env,backend+'-cleanup.log')
        except Exception as exc:
            report['cleanup_error']=str(exc)
            (OUT/'FAILED').write_text(str(exc)+'\n')
    (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
