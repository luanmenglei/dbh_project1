#!/usr/bin/env python3
"""Configure private instances from packages already extracted under runtime/.

Runs as an ordinary Linux user. Does not install system packages or run benchmarks.
Use a memory-limited user scope and taskset when invoking this script.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)


def wrapper(path, environment, command):
    text = '#!/bin/sh\nset -eu\numask 077\nulimit -n 65536\n'
    for key, value in environment.items():
        text += f'export {key}={shlex.quote(str(value))}\n'
    text += 'exec ' + shlex.join(list(map(str, command))) + ' "$@"\n'
    private(path, text)
    path.chmod(0o700)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-cpus', required=True)
    parser.add_argument('--client-cpus', required=True)
    args = parser.parse_args()
    if os.getuid() == 0:
        parser.error('Run as an ordinary user, never root')

    def cpus(value):
        result = set()
        for part in value.split(','):
            ends = list(map(int, part.split('-')))
            if len(ends) == 1:
                result.add(ends[0])
            elif len(ends) == 2 and ends[0] <= ends[1]:
                result.update(range(ends[0], ends[1]+1))
            else:
                parser.error('Invalid CPU list')
        return result

    db_cpus, client_cpus = cpus(args.db_cpus), cpus(args.client_cpus)
    if not db_cpus or not client_cpus or db_cpus & client_cpus or len(db_cpus | client_cpus) > 10:
        parser.error('Disjoint nonempty CPU sets must total at most 10 logical CPUs')
    state = ROOT/'deploy/state'
    state.mkdir(mode=0o700, parents=True, exist_ok=False)
    build = ROOT/'build'
    build.mkdir(exist_ok=True)
    pg_root = ROOT/'runtime/postgres'
    pg_bin = pg_root/'usr/lib/postgresql/14/bin'
    pg_lib = pg_root/'usr/lib/x86_64-linux-gnu'
    gauss = ROOT/'runtime/opengauss/usr/opengauss'
    packages = ROOT/'packages'
    metadata = {'uid': os.getuid(), 'root': str(ROOT), 'db_cpus': sorted(db_cpus),
                'client_cpus': sorted(client_cpus), 'memory_limit': '8G per database',
                'packages': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted(packages.glob('*.deb'))}}
    (state/'installation.json').write_text(json.dumps(metadata, indent=2)+'\n')
    configs = {}
    exports = []
    for backend, port in [('postgres', 55432), ('opengauss', 55433)]:
        bin_dir = pg_bin if backend == 'postgres' else gauss/'bin'
        lib = pg_lib if backend == 'postgres' else gauss/'lib'
        environment = {'LD_LIBRARY_PATH': str(lib), 'LANG': 'C.UTF-8'}
        if backend == 'opengauss':
            environment.update(GAUSSHOME=str(gauss), GAUSSLOG=str(state/'opengauss-tool-logs'))
        env = os.environ.copy()
        env.update(environment)
        # Fail on missing runtime dependencies before initializing a database.
        executable = bin_dir/('postgres' if backend == 'postgres' else 'gaussdb')
        linked = run(['ldd', executable], env=env, capture_output=True, text=True)
        (state/(backend+'-ldd.txt')).write_text(linked.stdout)
        if 'not found' in linked.stdout:
            raise RuntimeError(f'{backend}: missing runtime dependency; see saved ldd output')
        version = run([executable, '--version'], env=env, capture_output=True, text=True)
        (state/(backend+'-version.txt')).write_text(version.stdout+version.stderr)
        data = state/(backend+'-data')
        socket = state/(backend+'-socket')
        logs = state/(backend+'-logs')
        socket.mkdir(mode=0o700)
        logs.mkdir(mode=0o700)
        password = 'Aa9#'+secrets.token_hex(12)
        password_file = state/(backend+'-init-password')
        private(password_file, password+'\n')
        init = [bin_dir/('initdb' if backend == 'postgres' else 'gs_initdb'),
                '-D', data, '--encoding=UTF8', '--locale=C.UTF-8', '--pwfile='+str(password_file)]
        if backend == 'postgres':
            init += ['-L', pg_root/'usr/share/postgresql/14', '--auth-local=scram-sha-256',
                     '--auth-host=scram-sha-256']
        else:
            # A compatibility treats empty strings as NULL, unlike the source/file oracle.
            init += ['--nodename=filmbench', '--dbcompatibility=PG']
        try:
            output = run(init, env=env, capture_output=True, text=True)
            (state/(backend+'-init.log')).write_text(output.stdout+output.stderr)
        except subprocess.CalledProcessError as exc:
            (state/(backend+'-init.log')).write_text((exc.stdout or '')+(exc.stderr or ''))
            raise RuntimeError(f'{backend}: initialization failed; see saved log') from None
        finally:
            password_file.unlink()
        settings = f"\n# Filmbench private instance\nlisten_addresses='127.0.0.1'\nport={port}\n"
        settings += f"unix_socket_{'directories' if backend == 'postgres' else 'directory'}='{socket}'\n"
        settings += "shared_buffers=512MB\nwork_mem=4MB\nmax_connections=64\nfsync=on\nsynchronous_commit=on\nfull_page_writes=on\n"
        settings += f"logging_collector=on\nlog_directory='{logs}'\nlog_rotation_age=1d\nlog_rotation_size=16MB\n"
        settings += 'max_parallel_workers_per_gather=0\n' if backend == 'postgres' else 'query_dop=1\n'
        with (data/'postgresql.conf').open('a') as stream:
            stream.write(settings)
        authentication = 'scram-sha-256' if backend == 'postgres' else 'sha256'
        (data/'pg_hba.conf').write_text(f'local all all {authentication}\nhost all all 127.0.0.1/32 {authentication}\n')
        server_wrapper = state/(backend+'-server.sh')
        wrapper(server_wrapper, environment, [executable, '-D', data]+([] if backend == 'postgres' else ['--single_node']))
        service = 'filmbench-'+backend+'.service'
        start = ['systemd-run', '--user', '--quiet', '--collect', '--unit='+service,
                 '--property=MemoryMax=8G', '--property=MemorySwapMax=0', '--property=TasksMax=256',
                 '--property=Nice=10', '--property=KillMode=control-group',
                 '--property=TimeoutStopSec=60', '--property=KillSignal=SIGINT',
                 'taskset', '-c', args.db_cpus, server_wrapper]
        dsn = f'host=127.0.0.1 port={port} dbname=postgres user={os.environ["USER"]} password={password} connect_timeout=5'
        exports.append(f'export {backend.upper()}_DSN={shlex.quote(dsn)}')
        include = pg_root/'usr/include/postgresql' if backend == 'postgres' else gauss/'include'
        headers = list(include.rglob('libpq-fe.h'))
        if len(headers) != 1:
            raise RuntimeError(f'{backend}: cannot locate unique libpq header')
        output = build/('filmbench' if backend == 'postgres' else 'filmbench-opengauss')
        run(['c++', '-O3', '-std=c++17', '-Wall', '-Wextra', '-Wpedantic', ROOT/'src/filmbench.cpp',
             '-I'+str(headers[0].parent), '-I'+str(include), '-I'+str(include/'postgresql/server'),
             '-L'+str(lib), '-Wl,-rpath,'+str(lib),
             '-Wl,-rpath-link,'+str(lib), '-lpq', '-pthread', '-o', output], env=env)
        client_wrapper = build/(backend+'-client.sh')
        client_environment = dict(environment)
        if backend == 'opengauss':
            # The host compiler may require a newer libstdc++ than the server SDK ships.
            # Keep the server's environment intact; use the host runtime for the client.
            client_environment['LD_LIBRARY_PATH'] = '/usr/lib/x86_64-linux-gnu:'+str(lib)
        wrapper(client_wrapper, client_environment, [output])
        configs[backend] = {'binary': str(client_wrapper), 'dsn_env': backend.upper()+'_DSN',
                            'service': service, 'start_command': list(map(str, start)),
                            'client_prefix': ['taskset', '-c', args.client_cpus, 'nice', '-n', '10']}
        private(state/(backend+'-start.sh'), '#!/bin/sh\nset -eu\n'+shlex.join(list(map(str, start)))+'\n')
        (state/(backend+'-start.sh')).chmod(0o700)
    configs['file'] = {'binary': str(build/'postgres-client.sh'),
                       'client_prefix': ['taskset', '-c', args.db_cpus, 'nice', '-n', '10']}
    private(state/'connections.sh', '\n'.join(exports)+'\n')
    (state/'targets.json').write_text(json.dumps(configs, indent=2)+'\n')
    print('Configured private instances and clients; no databases started and no benchmarks run.')


if __name__ == '__main__':
    main()
