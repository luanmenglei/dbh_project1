"""Checks that stay active under python -O and immutable experiment source snapshots."""
import hashlib
import difflib
import json
import re
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
    """Accept the exact verified source, or its recorded annotation-only presentation.

    Keep the historical experiment identity separate from the delivered file hash.
    This is deliberately not a general waiver for refactored or reformatted code.
    """
    current = (root / 'src/filmbench.cpp').read_bytes()
    current_hash = hashlib.sha256(current).hexdigest()
    if current_hash == expected:
        return
    record = json.loads((root / 'docs/source-readability.json').read_text(encoding='utf-8'))
    require(record['kind'] == 'comments-and-guarded-folding-only', 'Unknown source presentation')
    require(record['source_sha256'] == current_hash, 'Annotated source hash mismatch')
    require(record['verified_source_sha256'] == expected, 'Annotation baseline differs from evidence')
    baseline_path = 'results/review-index-health/source-snapshot/src/filmbench.cpp'
    require(record['baseline'] == baseline_path, 'Unexpected annotation baseline path')
    baseline = (root / baseline_path).read_bytes()
    require(hashlib.sha256(baseline).hexdigest() == expected, 'Annotation baseline hash mismatch')
    before, after = baseline.splitlines(keepends=True), current.splitlines(keepends=True)
    # The verified baseline has one raw SQL string. Inserting even a // comment
    # inside it would change SQL, so prohibit insertions inside all raw literals.
    raw_spans = []
    for match in re.finditer(rb'R"([^ ()\\\t\r\n]{0,16})\(', baseline):
        end = baseline.find(b')' + match[1] + b'"', match.end())
        require(end >= 0, 'Unclosed baseline raw literal')
        raw_spans.append((match.start(), end))
    offsets = [0]
    for line in before:
        offsets.append(offsets[-1] + len(line))
    for tag, i, j, a, b in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        require(tag == 'insert', 'Source presentation changed existing code')
        require(not any(start < offsets[i] <= end for start, end in raw_spans),
                'Annotations inserted inside a raw literal')
        require(i == 0 or not before[i - 1].rstrip().endswith(b'\\'),
                'Annotations inserted after a continued line')
        added = [line.strip() for line in after[a:b]]
        k = 0
        while k < len(added):
            line = added[k]
            if not line or (line.startswith(b'//') and not line.endswith(b'\\')):
                k += 1
                continue
            require(k + 2 < len(added) and line == b'#ifdef _MSC_VER' and
                    (added[k + 1].startswith(b'#pragma region ') or
                     added[k + 1] == b'#pragma endregion') and added[k + 2] == b'#endif',
                    'Source presentation contains executable changes')
            k += 3
