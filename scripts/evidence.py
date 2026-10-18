"""Checks that stay active under python -O and immutable experiment source snapshots."""
import hashlib
import json
from pathlib import Path


def require(condition, message='Evidence validation failed'):
    if not condition:
        raise ValueError(message)


def source_file(evidence, root, name):
    relative = Path(name)
    require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe source manifest path')
    snapshot = evidence / 'source-snapshot'
    return (snapshot if snapshot.is_dir() else root) / relative


def verify_source(evidence, root, name, digest):
    path = source_file(evidence, root, name)
    require(path.is_file(), 'Missing experiment source: ' + str(path))
    require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Experiment source hash mismatch: ' + name)


def verify_readable_source(root, expected):
    """Bind the reorganized delivery to its new correctness run and historical source.

    Historical timings remain attached to their original source snapshots. The
    namespace refactor has its own three-backend correctness evidence, not new
    performance measurements.
    """
    current = (root / 'src/filmbench.cpp').read_bytes()
    current_hash = hashlib.sha256(current).hexdigest()
    if current_hash == expected:
        return
    record = json.loads((root / 'docs/source-readability.json').read_text(encoding='utf-8'))
    require(record['kind'] == 'namespace-refactor', 'Unknown source presentation')
    require(record['source_sha256'] == current_hash, 'Reorganized source hash mismatch')
    require(record['verified_source_sha256'] == expected, 'Historical source differs from evidence')
    baseline_path = 'results/review-index-health/source-snapshot/src/filmbench.cpp'
    require(record['baseline'] == baseline_path, 'Unexpected historical baseline path')
    require(hashlib.sha256((root / baseline_path).read_bytes()).hexdigest() == expected,
            'Historical baseline hash mismatch')
    review = root / 'results/review-namespace-20261018'
    require(record['verification'] == 'results/review-namespace-20261018/verification.json',
            'Unexpected namespace verification path')
    require((review / 'COMPLETE').is_file() and not (review / 'FAILED').exists(),
            'Incomplete namespace verification')
    proof = json.loads((review / 'verification.json').read_text(encoding='utf-8'))
    require(proof['source_sha256'] == current_hash, 'Source differs from verified namespace source')
    require(all(proof['backends'].get(b) == 'passed' for b in ('file', 'postgres', 'opengauss')),
            'Namespace backend verification failed')
    verify_source(review, root, 'src/filmbench.cpp', current_hash)
