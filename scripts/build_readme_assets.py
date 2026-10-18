#!/usr/bin/env python3
"""Refresh the README preview from page 4 of the delivered report (requires Poppler)."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    renderer = shutil.which('pdftoppm')
    if renderer is None:
        raise SystemExit('pdftoppm is required; install Poppler and add it to PATH.')
    assets = ROOT / 'docs' / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        renderer, '-f', '4', '-l', '4', '-singlefile', '-scale-to', '1600', '-png',
        str(ROOT / 'output' / 'submission' / 'report.pdf'), str(assets / 'report-preview'),
    ], check=True)
    print(assets / 'report-preview.png')


if __name__ == '__main__':
    main()
