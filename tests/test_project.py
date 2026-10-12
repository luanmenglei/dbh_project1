#!/usr/bin/env python3
"""Behavioral tests using an independent Python oracle and a disposable PostgreSQL cluster."""
import argparse
from collections import defaultdict
import getpass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import platform
import datetime

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('prepare',ROOT/'scripts/prepare.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
BIN=ROOT/'build/filmbench'


def digest(rows):
    total=0;count=0;mask=(1<<64)-1
    for row in rows:
        h=14695981039346656037
        for value in row:
            data=b'\0' if value is None else b'\1'+len(str(value).encode()).to_bytes(8,'little')+str(value).encode()
            for c in data:
                h=((h^c)*1099511628211)&mask
        total=(total+h)&mask;count+=1
    return {'rows':count,'checksum':str(total)}


def same(actual, expected):
    assert {k:actual[k] for k in ['rows','checksum']}==expected,(actual,expected)


def run(data,op,backend='file',extra=(),env=None,ok=True):
    cmd=[str(BIN),'--backend',backend,'--op',op,'--data',str(data),'--schema','filmbench_s1']+list(extra)
    p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,env=env,timeout=120)
    if not ok:
        assert p.returncode!=0,(cmd,p.stdout)
        return p
    assert p.returncode==0,(cmd,p.stderr)
    return json.loads(p.stdout)


def test_fixture(root,backend='file',env=None):
    data=root/('fixture-'+backend);data.mkdir()
    movies=[[1,"The O'Brien\tLove\n\\N",'gb',2001,100,0],
            [3,'Star 星','gb',2001,None,0],[7,'Love','us',2002,None,0],
            [8,'lower the','us',2002,90,0]]
    people=[[1,None,'Test',1970,None,'?',0],[5,'TtT','Other',1980,None,'F',0]]
    credits=[[1,1,'A'],[1,1,'D'],[3,1,'A'],[7,5,'D']]
    countries=[['gb','United Kingdom','EUROPE'],['us','United States','AMERICA']]
    tables={'movies':movies,'people':people,'credits':credits,'countries':countries}
    for table,rows in tables.items():
        (data/(table+'.tsv')).write_text(''.join('\t'.join(map(prepare.encode,r))+'\n' for r in rows))
    if backend!='file':
        run(data,'load',backend,['--reset'],env)
    for table,rows in tables.items():
        same(run(data,'fingerprint',backend,['--table',table],env),digest(rows))
    for keyword in ['The','Love','Star','missing','',"O'Brien",'星','\\N']:
        matched=[r for r in movies if keyword in r[1]]
        same(run(data,'search_count',backend,['--keyword',keyword],env),digest([[len(matched)]]))
        same(run(data,'search_rows',backend,['--keyword',keyword],env),digest([r[:2] for r in matched]))
    joined=[[m[0],m[1],c[2]] for m in movies for c in credits if c[0]==m[0] and c[1]==1]
    same(run(data,'join',backend,['--id','1'],env),digest(joined))
    groups=defaultdict(list)
    for r in movies:groups[(r[2],r[3])].append(r[4])
    expected=[[country,year,len(vals),sum(x for x in vals if x is not None),sum(x is not None for x in vals)]
              for (country,year),vals in groups.items()]
    same(run(data,'aggregate',backend,env=env),digest(expected))
    reference=run(data,'point','file',['--requests','37','--seed','7'])
    if backend=='file':
        run(data,'build_index')
        actual=run(data,'point',extra=['--requests','37','--seed','7','--indexed','1'])
    else:
        actual=run(data,'point',backend,['--requests','37','--seed','7'],env)
        for op in ['search_count','search_rows','point','join','aggregate']:
            plan=run(data,'explain',backend,['--query',op],env)
            assert plan['plan']
    same(actual,{k:reference[k] for k in ['rows','checksum']})
    result=run(data,'update',backend,env=env)
    assert result['affected']==2
    updated=[[v.replace('T','Ttt') if i in [1,2] and v is not None else v for i,v in enumerate(r)] for r in people]
    same(run(data,'fingerprint',backend,['--table','people'],env),digest(updated))
    assert updated[1][1]=='TtttTtt'
    if backend=='file':
        with (data/'movies.tsv').open('a') as f:f.write('9\tExtra\tgb\t2003\t100\t0\n')
        run(data,'point',extra=['--indexed','1'],ok=False)
    else:
        for op in ['concurrent_read','mixed']:
            result=run(data,op,backend,['--clients','2','--warmup','0','--duration','1','--write-percent','50'],env)
            assert result['valid'] and result['errors']==0 and result['operations']>0
            if op=='mixed':assert result['reads']>0 and result['writes']>0
        # Re-import really restores the original state after both updates and mixed writes.
        run(data,'load',backend,['--reset'],env)
        for table,rows in tables.items():
            same(run(data,'fingerprint',backend,['--table',table],env),digest(rows))


def test_course(root,backend='file',env=None):
    tables=prepare.read_source(ROOT/'data/source/filmdb.sql')
    assert {k:len(v) for k,v in tables.items()}=={'countries':185,'movies':9204,'people':16489,'credits':46850}
    out=root/'course'
    if not out.exists():prepare.prepare(ROOT/'data/source/filmdb.sql',out,[1,2])
    for scale in [1,2]:
        data=out/f's{scale}'
        if backend!='file':run(data,'load',backend,['--reset'],env)
        for word,count in [('The',1563),('Love',88),('Star',50)]:
            same(run(data,'search_count',backend,['--keyword',word],env),digest([[count*scale]]))
        if backend!='file':
            for table in tables:
                reference=run(data,'fingerprint','file',['--table',table])
                same(run(data,'fingerprint',backend,['--table',table],env),reference)
    run(root,'search_count',extra=['--schema','public'],ok=False)
    assert prepare.parse_values("1,'O''Brien',NULL,-2,'a,b'")==[1,"O'Brien",None,-2,'a,b']
    try:prepare.parse_values("1,load_extension('bad')")
    except ValueError:pass
    else:raise AssertionError('SQL expression accepted')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--skip-postgres',action='store_true')
    p.add_argument('--out',type=Path,default=ROOT/'results/verification.json')
    a=p.parse_args();a.out.parent.mkdir(parents=True,exist_ok=True)
    started=time.time();report={'file':'not run','postgres':'not run','opengauss':'not run (requires Ubuntu server)',
        'platform':platform.platform(),'verified_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'coverage':['independent Python oracle','NULL and empty substring','apostrophe, tab, newline, backslash, Unicode',
                    'duplicate movie roles','all-NULL aggregate group','sequential/indexed/SQL point parity',
                    'durable variable-length update','stale index rejection','read-only EXPLAIN plans',
                    'concurrent read and mixed write','restore after updates','s1/s2 course-data parity',
                    'full harness and CSV summary','schema scope guard']}
    with tempfile.TemporaryDirectory(prefix='fbtest-',dir='/tmp') as tmp:
        root=Path(tmp)
        test_fixture(root);test_course(root);report['file']='passed'
        if not a.skip_postgres:
            bindir=Path(subprocess.check_output([os.environ.get('PG_CONFIG','pg_config'),'--bindir'],text=True).strip())
            report['postgres_version']=subprocess.check_output([str(bindir/'postgres'),'--version'],text=True).strip()
            cluster=root/'pg';socket=root/'sock';socket.mkdir()
            subprocess.run([str(bindir/'initdb'),'-D',str(cluster),'--auth-local=trust','--auth-host=reject',
                            '-U',getpass.getuser(),'--encoding=UTF8','--locale=C'],check=True,capture_output=True)
            opts=f"-k {socket} -h '' -p 55439 -c shared_buffers=32MB -c max_connections=20"
            subprocess.run([str(bindir/'pg_ctl'),'-D',str(cluster),'-l',str(root/'pg.log'),'-o',opts,'-w','start'],check=True,capture_output=True)
            env=os.environ.copy();env['FILMBENCH_DSN']=f'host={socket} port=55439 dbname=postgres user={getpass.getuser()} connect_timeout=5'
            try:
                test_fixture(root,'postgres',env);test_course(root,'postgres',env)
                # Full orchestration/summary smoke test, including rotating backends and repeated restores.
                env['POSTGRES_DSN']=env['FILMBENCH_DSN']
                suite=root/'suite'
                subprocess.run(['python3',str(ROOT/'scripts/run_suite.py'),'--out',str(suite),'--data',str(root/'course'),
                                '--backends','file','postgres','--scales','1','--repeats','2','--point-requests','5',
                                '--clients','1','2','--warmup','0','--duration','1','--load-repeats','1'],
                               check=True,cwd=ROOT,env=env,capture_output=True,text=True)
                subprocess.run(['python3',str(ROOT/'scripts/summarize.py'),str(suite)],check=True,capture_output=True)
                assert (suite/'COMPLETE').exists() and (suite/'summary.csv').stat().st_size>0
                report['postgres']='passed (isolated temporary cluster; SQL/file parity and suite smoke)'
            finally:
                subprocess.run([str(bindir/'pg_ctl'),'-D',str(cluster),'-m','fast','-w','stop'],check=True,capture_output=True)
        report['elapsed_s']=time.time()-started
        report['source_sha256']=prepare.checksum(ROOT/'data/source/filmdb.sql')
        report['source_code_sha256']=prepare.checksum(ROOT/'src/filmbench.cpp')
        report['implementation_sha256']={str(p.relative_to(ROOT)):prepare.checksum(p)
                                        for pattern in ['scripts/*.py','sql/*.sql','tests/*.py'] for p in ROOT.glob(pattern)}
        a.out.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
