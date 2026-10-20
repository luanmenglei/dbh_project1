#!/usr/bin/env python3
"""Check tracked text for private server identifiers before publishing.

Use --history to scan every blob reachable from local branches and tags.
This targeted metadata check is not a comprehensive secret scanner. Review
PDFs, images, archives and credentials separately before publishing them.
"""
import argparse
import ipaddress
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ((0x0A000000, 8), (0xAC100000, 12), (0xC0A80000, 16)))
IP = re.compile(rb'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
PERSONAL_PATH = re.compile(rb'/(?:data\d*|home)/[^/\s"\x27]+/filmbench(?:/|\b)')
PRIVATE_KEY = re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----')


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def problems(data):
    if b'\x00' in data or data.startswith(b'%PDF'):
        return []
    found = []
    for match in IP.finditer(data):
        try:
            address = ipaddress.ip_address(match[0].decode())
        except ValueError:
            continue
        if any(address in network for network in PRIVATE_NETWORKS):
            found.append('private IPv4 address')
            break
    if PERSONAL_PATH.search(data):
        found.append('personal deployment path')
    if PRIVATE_KEY.search(data):
        found.append('private key')
    return found


def contents(history):
    if not history:
        for name in git('ls-files', '-z').split(b'\0'):
            if name:
                path = ROOT / name.decode('utf-8')
                if path.is_file():
                    yield name.decode('utf-8'), path.read_bytes()
        return
    process = subprocess.Popen(['git', '-C', str(ROOT), 'cat-file', '--batch'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for entry in git('rev-list', '--objects', '--branches', '--tags').splitlines():
            oid, _, name = entry.partition(b' ')
            process.stdin.write(oid + b'\n')
            process.stdin.flush()
            header = process.stdout.readline().split()
            data = process.stdout.read(int(header[2]))
            process.stdout.read(1)
            if header[1] == b'blob':
                yield oid.decode()[:12] + ':' + name.decode('utf-8', 'replace'), data
    finally:
        process.stdin.close()
        process.wait()
    if process.returncode:
        raise RuntimeError('git cat-file failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    args = parser.parse_args()
    count = failures = 0
    for name, data in contents(args.history):
        count += 1
        found = problems(data)
        if found:
            failures += 1
            # Never echo sensitive values into CI or terminal logs.
            print(name + ': ' + ', '.join(found))
    print(f'Checked {count} files/blobs; {failures} findings.')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
