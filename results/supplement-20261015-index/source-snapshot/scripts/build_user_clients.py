#!/usr/bin/env python3
"""Build revised clients using existing private SDKs, without reinitializing databases."""
import argparse
import os
from pathlib import Path
import subprocess
from configure_user_instances import wrapper

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime', type=Path, default=ROOT/'runtime')
    p.add_argument('--out', type=Path, default=ROOT/'build')
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for backend in ['postgres', 'opengauss']:
        pg = a.runtime/'postgres'
        gauss = a.runtime/'opengauss/usr/opengauss'
        include = pg/'usr/include/postgresql' if backend == 'postgres' else gauss/'include'
        lib = pg/'usr/lib/x86_64-linux-gnu' if backend == 'postgres' else gauss/'lib'
        headers = list(include.rglob('libpq-fe.h'))
        if len(headers) != 1:
            raise ValueError('Expected one libpq header in '+str(include))
        output = a.out/('filmbench' if backend == 'postgres' else 'filmbench-opengauss')
        launcher = a.out/(backend+'-client.sh')
        if output.exists() or launcher.exists():
            raise FileExistsError('Use a fresh --out directory: '+str(output))
        env = os.environ.copy()
        env['LD_LIBRARY_PATH'] = '/usr/lib/x86_64-linux-gnu:'+str(lib)
        subprocess.run(['c++', '-O3', '-std=c++17', '-Wall', '-Wextra', '-Wpedantic', str(ROOT/'src/filmbench.cpp'),
                        '-I'+str(headers[0].parent), '-I'+str(include), '-I'+str(include/'postgresql/server'),
                        '-L'+str(lib), '-Wl,-rpath,'+str(lib), '-Wl,-rpath-link,'+str(lib),
                        '-lpq', '-pthread', '-o', str(output)], env=env, check=True)
        wrapper(launcher, {'LD_LIBRARY_PATH': env['LD_LIBRARY_PATH'], 'LANG': 'C.UTF-8'}, [output])
        print(output)


if __name__ == '__main__':
    main()
