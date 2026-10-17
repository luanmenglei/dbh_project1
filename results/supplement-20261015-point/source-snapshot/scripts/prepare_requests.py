#!/usr/bin/env python3
"""Generate common external request streams and degree-stratified joins once per experiment."""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
from prepare import checksum


def generate(data, out, requests=10000, repeats=3):
    out.mkdir(parents=True, exist_ok=False)
    movies = [int(line.split('\t', 1)[0]) for line in (data/'movies.tsv').read_text(encoding='utf-8').splitlines()]
    people = [int(line.split('\t', 1)[0]) for line in (data/'people.tsv').read_text(encoding='utf-8').splitlines()]
    degree = Counter(int(line.split('\t')[1]) for line in (data/'credits.tsv').read_text().splitlines())
    ordered = sorted(people, key=lambda p: (degree[p], p))
    nonzero = [p for p in ordered if degree[p]]
    distinct_degrees = sorted({degree[p] for p in nonzero})
    middle_degree = distinct_degrees[len(distinct_degrees)//2]
    medium = min(p for p in nonzero if degree[p] == middle_degree)
    absent = max(people)+1
    joins = [{'label': label, 'id': person, 'expected_rows': degree[person]} for label, person in
             [('absent', absent), ('low', nonzero[0]), ('medium', medium), ('high', nonzero[-1])]]
    streams = []
    hot = movies[:max(1, len(movies)//100)]
    for repeat in range(repeats):
        for distribution in ['uniform', 'hot90']:
            rng = random.Random(20261013+repeat)
            ids = [rng.choice(hot if distribution == 'hot90' and rng.random() < .9 else movies) for _ in range(requests)]
            dest = out/f'{distribution}-{repeat}.txt'
            dest.write_text(''.join(str(i)+'\n' for i in ids))
            streams.append(dict(distribution=distribution, repeat=repeat, file=dest.name,
                                sha256=checksum(dest), count=requests, distinct=len(set(ids))))
    manifest = dict(seed_base=20261013, requests=requests, repeats=repeats, joins=joins, streams=streams,
                    data_files={p.name: checksum(p) for p in data.glob('*.tsv')},
                    hot_definition='90% from the first 1% of sorted movie IDs; remaining 10% uniform over all IDs',
                    medium_definition='Median of the distinct positive credit counts; not the median person')
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--requests', type=int, default=10000)
    p.add_argument('--repeats', type=int, default=3)
    a = p.parse_args()
    if a.requests < 1 or a.repeats < 1:
        p.error('requests and repeats must be positive')
    print(json.dumps(generate(a.data, a.out, a.requests, a.repeats), indent=2))
