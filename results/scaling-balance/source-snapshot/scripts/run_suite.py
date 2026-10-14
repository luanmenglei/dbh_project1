#!/usr/bin/env python3
"""Run reproducible experiments. Connection strings are read only from named env vars."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]

PROFILES = {
    'compact': dict(scales=[1, 10, 100], repeats=3, point_requests=100,
                    clients=[1, 4, 8], concurrency_scales=[10],
                    load_repeats=3, warmup=5, duration=20),
    'full': dict(scales=[1, 10, 100], repeats=5, point_requests=100,
                 clients=[1, 2, 4, 8, 16, 32], concurrency_scales=[1, 100],
                 load_repeats=3, warmup=30, duration=120),
}


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def host_info():
    result = {'platform': platform.platform(), 'python': platform.python_version(), 'time': utc()}
    for name, args in [('cpu', ['lscpu', '-J']), ('disk', ['lsblk', '-J', '-o', 'NAME,TYPE,SIZE,ROTA,MOUNTPOINTS']),
                       ('numa', ['numactl', '--hardware']), ('compiler', ['c++', '--version'])]:
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=15)
            result[name] = r.stdout or r.stderr
        except (OSError, subprocess.TimeoutExpired) as e:
            result[name] = str(e)
    return result


def monitor(stop, pid, destination, cgroup=None):
    """Raw counters, not invented per-engine utilization; host counters can include other jobs."""
    with destination.open('w') as f:
        while not stop.is_set():
            sample = {'time': utc(), 'monotonic_s': time.monotonic(), 'client_pid': pid}
            paths = {'host_cpu': '/proc/stat', 'host_memory': '/proc/meminfo',
                     'host_disks': '/proc/diskstats', 'client_status': f'/proc/{pid}/status',
                     'client_io': f'/proc/{pid}/io'}
            if cgroup:
                for name in ['cpu.stat', 'memory.current', 'memory.peak', 'io.stat']:
                    paths['cgroup_'+name] = str(Path(cgroup)/name)
            for name, path in paths.items():
                try:
                    sample[name] = Path(path).read_text()
                except OSError:
                    pass
            f.write(json.dumps(sample)+'\n')
            f.flush()
            stop.wait(1)


class Runner:
    def __init__(self, args):
        self.args = args
        self.out = args.out.resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        self.config = json.loads(args.config.read_text()) if args.config else {}
        self.counter = 0
        self.active = None
        (self.out/'host.json').write_text(json.dumps(host_info(), indent=2))
        # Never serialize environment variables or Docker Config.Env (contains passwords).
        config = {'args': {k:str(v) if isinstance(v, Path) else v for k,v in vars(args).items()},
                  'targets': self.config, 'started': utc()}
        (self.out/'run.json').write_text(json.dumps(config, indent=2))

    def activate(self, backend):
        if not (self.args.manage_containers or self.args.manage_user_services) or self.active == backend:
            return
        if self.args.manage_user_services:
            for target in ['postgres', 'opengauss']:
                service = self.config.get(target, {}).get('service')
                if not service:
                    raise ValueError('--manage-user-services needs both service names in config')
                if target != backend:
                    self.stop_user_service(service)
            if backend != 'file':
                target = self.config[backend]
                subprocess.run(target['start_command'], check=True, capture_output=True)
                deadline = time.monotonic()+60
                while True:
                    p = subprocess.run(self.command(backend, 1, 'snapshot'), cwd=ROOT, capture_output=True)
                    if p.returncode == 0:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError(f'{backend} did not become ready: '+p.stderr.decode(errors='replace'))
                    time.sleep(1)
                group = subprocess.check_output(['systemctl', '--user', 'show', target['service'],
                                                 '-p', 'ControlGroup', '--value'], text=True).strip()
                target['cgroup'] = str(Path('/sys/fs/cgroup')/group.lstrip('/')) if group else None
            self.active = backend
            return
        for target in ['postgres', 'opengauss']:
            container = self.config.get(target, {}).get('container')
            if not container:
                raise ValueError('--manage-containers needs both container names in config')
            if target != backend:
                subprocess.run(['docker', 'stop', container], check=True, capture_output=True)
        if backend != 'file':
            target = self.config[backend]
            subprocess.run(['docker', 'start', target['container']], check=True, capture_output=True)
            # Save reproducibility metadata without container secrets.
            r = subprocess.run(['docker', 'inspect', '--format',
                                '{{json .Id}} {{json .Image}} {{json .HostConfig.CpusetCpus}} {{json .HostConfig.CpusetMems}} {{json .HostConfig.Memory}}',
                                target['container']], check=True, capture_output=True, text=True)
            (self.out/f'{backend}-container.txt').write_text(r.stdout)
            if not target.get('cgroup'):
                pid=subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}',target['container']],text=True).strip()
                try:
                    for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines():
                        if line.startswith('0::'):
                            target['cgroup']=str(Path('/sys/fs/cgroup')/line[3:].lstrip('/'))
                except OSError:
                    pass
            deadline = time.monotonic()+180
            while True:
                p = subprocess.run(self.command(backend, 1, 'snapshot'), cwd=ROOT, capture_output=True)
                if p.returncode == 0:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError(f'{backend} did not become ready: '+p.stderr.decode(errors='replace'))
                time.sleep(2)
        self.active = backend

    def command(self, backend, scale, op, extra=None, data=None):
        target = self.config.get(backend, {})
        binary = target.get('binary', str(ROOT/'build/filmbench'))
        cmd = list(target.get('client_prefix', [])) + [binary, '--backend', backend, '--op', op,
               '--data', str(data or (self.args.data/f's{scale}').resolve()), '--schema', f'filmbench_s{scale}',
               '--dsn-env', target.get('dsn_env', backend.upper()+'_DSN')]
        return cmd + (extra or [])

    def call(self, backend, scale, op, extra=None, data=None, measured=False, **labels):
        self.counter += 1
        stem = f'{self.counter:05d}-{backend}-s{scale}-{op}'
        cmd = self.command(backend, scale, op, extra, data)
        started = utc()
        process = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stop = threading.Event()
        thread = threading.Thread(target=monitor, args=(stop,process.pid,self.out/(stem+'-resources.jsonl'),
                                                        self.config.get(backend,{}).get('cgroup')), daemon=True)
        thread.start()
        try:
            stdout, stderr = process.communicate(timeout=self.args.timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            stderr += '\nHarness timeout: command killed; do not reuse partial update state.\n'
        finally:
            stop.set()
            thread.join()
        (self.out/(stem+'.stdout')).write_text(stdout)
        (self.out/(stem+'.stderr')).write_text(stderr)
        if process.returncode != 0:
            raise RuntimeError(f'{stem} failed; see saved stderr. '+stderr[-2000:])
        result = json.loads(stdout)
        record = {'backend': backend, 'scale': scale, 'op': op, 'started': started, 'finished':utc(),
                  'measured': measured, 'command':cmd, 'log':stem, **labels, 'result':result}
        with (self.out/'records.jsonl').open('a') as f:
            f.write(json.dumps(record)+'\n')
        return result

    def load(self, backend, scale, **labels):
        return self.call(backend,scale,'load',['--reset','--ddl',str(ROOT/'sql/schema.sql')],**labels)

    @staticmethod
    def stop_user_service(service):
        result = subprocess.run(['systemctl', '--user', 'stop', service], capture_output=True, text=True)
        if result.returncode:
            # --collect removes successfully stopped transient units. Absence is already stopped.
            state = subprocess.run(['systemctl', '--user', 'show', service, '-p', 'LoadState', '--value'],
                                   capture_output=True, text=True)
            if state.stdout.strip() != 'not-found':
                raise RuntimeError(f'Cannot stop {service}: '+result.stderr)

    def stop_user_services(self):
        if self.args.manage_user_services:
            for backend in ['postgres', 'opengauss']:
                service = self.config.get(backend, {}).get('service')
                if service:
                    self.stop_user_service(service)

    @staticmethod
    def equal(a, b, context):
        for key in ['rows', 'checksum']:
            if a[key] != b[key]:
                raise AssertionError(f'{context}: {key}: {a[key]} != {b[key]}')

    def run_scale(self, scale):
        self.activate('file')
        data = (self.args.data/f's{scale}').resolve()
        manifest = json.loads((data/'manifest.json').read_text())
        if manifest['scale'] != scale:
            raise ValueError('Manifest scale mismatch')
        # Reject stale/corrupted/mutated input before measuring.
        from prepare import checksum
        for table, info in manifest['files'].items():
            if checksum(data/f'{table}.tsv') != info['sha256']:
                raise ValueError(f'Input checksum mismatch: {table}')
        (self.out/f's{scale}-manifest.json').write_text(json.dumps(manifest, indent=2))
        references = {}
        for table in manifest['counts']:
            references[table] = self.call('file',scale,'fingerprint',['--table',table])
        self.call('file',scale,'build_index',measured=True)
        for backend in self.args.backends:
            self.activate(backend)
            if backend == 'file':
                continue
            self.load(backend,scale)
            snapshot = self.call(backend,scale,'snapshot')
            settings = {x[0]:x[1] for x in snapshot['settings']}
            if settings.get('fsync') != 'on' or settings.get('synchronous_commit') != 'on':
                raise ValueError('Main suite requires fsync=on and synchronous_commit=on')
            for table in references:
                actual = self.call(backend,scale,'fingerprint',['--table',table])
                self.equal(references[table],actual,f'{backend}: initial {table}')
            for op in ['search_count','search_rows','point','join','aggregate']:
                plan_id=manifest['expected']['join_peopleid'] if op=='join' else 1
                self.call(backend,scale,'explain',['--query',op,'--id',str(plan_id)],phase='diagnostic')

        workloads = []
        for keyword in ['The','Love','Star']:
            for op in ['search_count','search_rows']:
                workloads.append((op,['--keyword',keyword],{'keyword':keyword}))
        workloads += [('point',['--requests',str(self.args.point_requests)],{}),
                      ('join',['--id',str(manifest['expected']['join_peopleid'])],{}), ('aggregate',[],{})]
        expected = {}
        self.activate('file')
        for op, extra, labels in workloads:
            key = (op,labels.get('keyword',''))
            expected[key] = self.call('file',scale,op,extra,**labels)
        for repeat in range(self.args.repeats):
            order = self.args.backends[repeat%len(self.args.backends):]+self.args.backends[:repeat%len(self.args.backends)]
            for backend in order:
                self.activate(backend)
                for op, extra, labels in workloads:
                    for indexed in ([0,1] if backend=='file' and op=='point' else [0]):
                        options=extra+(['--indexed',str(indexed)] if op=='point' else [])
                        self.call(backend,scale,op,options,phase='warmup',**labels)
                        r=self.call(backend,scale,op,options,measured=True,repeat=repeat,indexed=indexed,**labels)
                        self.equal(expected[(op,labels.get('keyword',''))],r,f'{backend} {op}')

                # Every update trial starts from the original names. Restore is outside timing.
                if backend=='file':
                    work=self.out/f'update-work-s{scale}'
                    work.mkdir(exist_ok=True)
                    shutil.copyfile(data/'people.tsv',work/'people.tsv')
                    before=self.call('file',scale,'fingerprint',['--table','people'],data=work)
                    self.equal(references['people'],before,'file pre-update')
                    r=self.call('file',scale,'update',data=work,measured=True,repeat=repeat)
                    updated=self.call('file',scale,'fingerprint',['--table','people'],data=work)
                    expected['updated']=updated
                    with (self.out/'file-update-size.jsonl').open('a') as f:
                        f.write(json.dumps({'scale':scale,'repeat':repeat,'before':(data/'people.tsv').stat().st_size,
                                            'after':(work/'people.tsv').stat().st_size})+'\n')
                else:
                    self.load(backend,scale,phase='restore_before_update')
                    self.call(backend,scale,'snapshot',phase='before_update')
                    # Explicit warm scan outside timed UPDATE, consistent with file fingerprint.
                    self.call(backend,scale,'fingerprint',['--table','people'],phase='warmup')
                    r=self.call(backend,scale,'update',measured=True,repeat=repeat)
                    updated=self.call(backend,scale,'fingerprint',['--table','people'])
                    if 'updated' not in expected:
                        work=self.out/f'update-reference-s{scale}';work.mkdir(exist_ok=True)
                        shutil.copyfile(data/'people.tsv',work/'people.tsv')
                        self.call('file',scale,'update',data=work)
                        expected['updated']=self.call('file',scale,'fingerprint',['--table','people'],data=work)
                    self.equal(expected['updated'],updated,f'{backend}: updated people')
                    self.call(backend,scale,'snapshot',phase='after_update')
                if r['affected'] != manifest['expected']['updated_people']:
                    raise AssertionError('Wrong affected row count')

        if scale not in self.args.concurrency_scales:
            return
        for repeat in range(self.args.load_repeats):
            dbs=[b for b in self.args.backends if b!='file']
            if repeat%2:
                dbs.reverse()
            for backend in dbs:
                self.activate(backend)
                for op in ['concurrent_read','mixed']:
                    for clients in self.args.clients:
                        # Clean runtime values, statistics and index layout for each trial.
                        self.load(backend,scale,phase='restore_before_load')
                        self.call(backend,scale,'snapshot',phase='before_load')
                        self.call(backend,scale,op,['--clients',str(clients),'--warmup',str(self.args.warmup),
                                 '--duration',str(self.args.duration),'--seed',str(20261012+repeat)],
                                 measured=True,repeat=repeat,clients=clients)
                        if op=='mixed':
                            # Runtime is intentionally changed; other fields/counts checked in SQL tests.
                            self.call(backend,scale,'snapshot',phase='after_load')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path)
    p.add_argument('--out',type=Path,required=True,help='Must not already exist')
    p.add_argument('--profile',choices=PROFILES,default='compact',
                   help='Parameter preset; explicit options override it (default: compact)')
    p.add_argument('--dry-run',action='store_true',help='Print resolved parameters without creating output or starting services')
    p.add_argument('--data',type=Path,default=ROOT/'data/generated')
    p.add_argument('--backends',nargs='+',choices=['file','postgres','opengauss'],default=['file','postgres','opengauss'])
    p.add_argument('--scales',type=int,nargs='+')
    p.add_argument('--repeats',type=int)
    p.add_argument('--point-requests',type=int)
    p.add_argument('--clients',type=int,nargs='+')
    p.add_argument('--concurrency-scales',type=int,nargs='*')
    p.add_argument('--load-repeats',type=int)
    p.add_argument('--warmup',type=int)
    p.add_argument('--duration',type=int)
    p.add_argument('--timeout',type=int,default=1800)
    management=p.add_mutually_exclusive_group()
    management.add_argument('--manage-containers',action='store_true')
    management.add_argument('--manage-user-services',action='store_true')
    a=p.parse_args()
    for name, value in PROFILES[a.profile].items():
        if getattr(a, name) is None:
            setattr(a, name, value)
    if a.repeats<1 or a.load_repeats<1 or a.point_requests<1 or a.duration<1 or a.warmup<0 or any(x<1 for x in a.scales+a.clients+a.concurrency_scales):
        p.error('Counts/durations must be positive; warmup can be zero')
    if a.dry_run:
        parameters = {k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()}
        trials = (sum(b != 'file' for b in a.backends)
                  * sum(s in a.concurrency_scales for s in a.scales)
                  * len(a.clients) * 2 * a.load_repeats)
        print(json.dumps({'parameters': parameters, 'concurrency_trials': trials,
                          'concurrency_window_s': trials*(a.warmup+a.duration),
                          'note': 'Window excludes import, restore, serial operations, startup and drain time.'}, indent=2))
        return
    runner=Runner(a)
    try:
        for scale in a.scales:
            print(f'Running scale {scale}',flush=True)
            runner.run_scale(scale)
    except Exception:
        (runner.out/'FAILED').write_text(utc()+'\n')
        raise
    finally:
        runner.stop_user_services()
    (runner.out/'COMPLETE').write_text(utc()+'\n')
    print('Complete:',runner.out)


if __name__=='__main__':
    main()
