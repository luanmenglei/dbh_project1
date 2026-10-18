#!/usr/bin/env python3
"""Parse only whitelisted INSERT data; never execute the supplied SQL script."""
import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

TABLES = {'countries': 3, 'movies': 5, 'people': 6, 'credits': 3}


def parse_values(text):
    values, i = [], 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i == len(text):
            raise ValueError('Missing value')
        if text[i] == "'":
            i += 1
            chars = []
            while True:
                if i >= len(text):
                    raise ValueError('Unterminated SQL string')
                if text[i] == "'":
                    if i + 1 < len(text) and text[i + 1] == "'":
                        chars.append("'")
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    chars.append(text[i])
                    i += 1
            values.append(''.join(chars))
        else:
            m = re.match(r'(NULL|-?\d+)(?=\s*(?:,|$))', text[i:])
            if not m:
                raise ValueError('Unsupported SQL value: ' + text[i:i+40])
            values.append(None if m[1] == 'NULL' else int(m[1]))
            i += m.end()
        while i < len(text) and text[i].isspace():
            i += 1
        if i == len(text):
            break
        if text[i] != ',':
            raise ValueError('Expected comma')
        i += 1
        if not text[i:].strip():
            raise ValueError('Trailing comma')
    return values


def read_source(path):
    tables = {t: [] for t in TABLES}
    for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        m = re.match(r'^INSERT INTO (\w+) VALUES\((.*)\);\s*$', line)
        if m and m[1] in tables:
            try:
                row = parse_values(m[2])
                if len(row) != TABLES[m[1]]:
                    raise ValueError('Wrong column count')
                tables[m[1]].append(row)
            except ValueError as exc:
                raise ValueError(f'Line {n}: {exc}') from exc
        elif re.match(r'^INSERT INTO (countries|movies|people|credits)\b', line):
            raise ValueError(f'Unsupported INSERT format at line {n}')
    if any(not rows for rows in tables.values()):
        raise ValueError('A required table is empty')
    for t, key in [('countries', lambda r:r[0]), ('movies', lambda r:r[0]),
                   ('people', lambda r:r[0]), ('credits', tuple)]:
        if len({key(r) for r in tables[t]}) != len(tables[t]):
            raise ValueError(f'Duplicate primary key in {t}')
    movies = {r[0] for r in tables['movies']}
    people = {r[0] for r in tables['people']}
    countries = {r[0] for r in tables['countries']}
    if any(r[2] not in countries for r in tables['movies']):
        raise ValueError('Missing country')
    if any(r[0] not in movies or r[1] not in people for r in tables['credits']):
        raise ValueError('Broken credit foreign key')
    for t in tables:
        tables[t].sort(key=lambda r: r[0] if t != 'credits' else tuple(r))
    return tables


def encode(value):
    if value is None:
        return r'\N'
    return str(value).replace('\\', '\\\\').replace('\t', r'\t').replace('\n', r'\n').replace('\r', r'\r')


def checksum(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def prepare(source, destination, scales):
    tables = read_source(source)
    strides = {t: max(r[0] for r in tables[t]) + 1 for t in ['movies', 'people']}
    appearances = Counter(r[1] for r in tables['credits'])
    join_id = min(appearances, key=lambda x: (-appearances[x], x))
    for scale in scales:
        if scale < 1:
            raise ValueError('Scale must be positive')
        out = destination / f's{scale}'
        out.mkdir(parents=True, exist_ok=False)
        counts = {}
        for table, rows in tables.items():
            count = 0
            with (out / f'{table}.tsv').open('w', encoding='utf-8', newline='\n') as f:
                for replica in range(1 if table == 'countries' else scale):
                    for original in rows:
                        row = original.copy()
                        if table in strides:
                            row[0] += replica * strides[table]
                            row.append(replica)
                        elif table == 'credits':
                            row[0] += replica * strides['movies']
                            row[1] += replica * strides['people']
                        f.write('\t'.join(map(encode, row)) + '\n')
                        count += 1
            counts[table] = count
        metadata = {
            'format': 1, 'scale': scale, 'source_sha256': checksum(source),
            'counts': counts, 'id_strides': strides,
            'expansion': 'Exact relational replicas; original text preserved; unique constraints scoped by replica.',
            'files': {t: {'sha256': checksum(out/f'{t}.tsv'), 'bytes': (out/f'{t}.tsv').stat().st_size}
                      for t in tables},
            'expected': {
                'search_count': {k: scale*sum(k in r[1] for r in tables['movies']) for k in ['The','Love','Star']},
                'updated_people': scale*sum('T' in (r[1] or '') or 'T' in r[2] for r in tables['people']),
                'join_peopleid': join_id
            }
        }
        (out/'manifest.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False)+'\n')
        print(json.dumps({'directory': str(out), 'counts': counts}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('data/source/filmdb.sql'))
    p.add_argument('--out', type=Path, default=Path('data/generated'))
    p.add_argument('--scales', type=int, nargs='+', default=[1,10,100])
    a = p.parse_args()
    prepare(a.source, a.out, a.scales)
