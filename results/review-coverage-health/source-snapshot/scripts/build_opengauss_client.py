#!/usr/bin/env python3
"""Build the same C++ driver against the selected openGauss image's own libpq."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--container',default='filmbench-opengauss')
    p.add_argument('--gausshome',default='/usr/local/opengauss')
    p.add_argument('--sdk',type=Path,default=ROOT/'build/opengauss-sdk')
    p.add_argument('--targets',type=Path,default=ROOT/'deploy/state/targets.json')
    a=p.parse_args();sdk=a.sdk.resolve();sdk.mkdir(parents=True,exist_ok=False)
    # docker cp works for stopped containers and does not start a benchmark database.
    for name in ['include','lib']:
        subprocess.run(['docker','cp',f'{a.container}:{a.gausshome}/{name}',str(sdk/name)],check=True)
    headers=list((sdk/'include').rglob('libpq-fe.h'))
    libraries=list((sdk/'lib').rglob('libpq.so'))
    if len(headers)!=1 or len(libraries)!=1:
        raise RuntimeError('Cannot uniquely locate libpq headers/library; check selected image layout or supply a matching official SDK')
    lib=libraries[0].parent
    output=ROOT/'build/filmbench-opengauss'
    subprocess.run(['c++','-O3','-std=c++17','-Wall','-Wextra','-Wpedantic',str(ROOT/'src/filmbench.cpp'),
                    '-I'+str(headers[0].parent),'-L'+str(lib),'-Wl,-rpath,'+str(lib),'-Wl,-rpath-link,'+str(lib),
                    '-lpq','-pthread','-o',str(output)],check=True)
    wrapper=ROOT/'build/filmbench-opengauss.sh'
    wrapper.write_text('#!/bin/sh\nexport LD_LIBRARY_PATH='+shlex.quote(str(lib))+'${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\nexec '+shlex.quote(str(output))+' "$@"\n')
    wrapper.chmod(0o755)
    targets=json.loads(a.targets.read_text());targets['opengauss']['binary']=str(wrapper)
    a.targets.write_text(json.dumps(targets,indent=2)+'\n')
    print('Built native openGauss client and updated',a.targets)


if __name__=='__main__':
    main()
