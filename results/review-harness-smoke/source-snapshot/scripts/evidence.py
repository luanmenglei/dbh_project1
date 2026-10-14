"""Checks that stay active under python -O and immutable experiment source snapshots."""
import hashlib
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
