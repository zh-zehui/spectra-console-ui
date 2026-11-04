"""Restore pinned upstream source and the deployed local patches, without resets."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=None, capture=False):
    return subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.PIPE if capture else None).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify existing checkouts; no network or writes')
    parser.add_argument('--only', action='append', help='Restore only the named vendor; repeatable')
    args = parser.parse_args()
    lock = json.loads((ROOT / 'deploy/vendor-lock.json').read_text())
    known = {item['name'] for item in lock['vendors']}
    if set(args.only or []) - known:
        parser.error('Unknown vendor name')
    for item in lock['vendors']:
        name, commit, url = item['name'], item['commit'], item['url']
        if args.only and name not in args.only:
            continue
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or not re.fullmatch(r'[0-9a-f]{40}', commit) or not url.startswith('https://github.com/'):
            raise RuntimeError('Invalid vendor lock entry')
        target = ROOT / 'vendor' / name
        if not target.exists():
            if args.check:
                raise RuntimeError(f'{name}: checkout is missing')
            target.parent.mkdir(parents=True, exist_ok=True)
            run('git', 'init', str(target))
            run('git', 'remote', 'add', 'origin', url, cwd=target)
            run('git', 'fetch', '--depth', '1', 'origin', commit, cwd=target)
            run('git', 'checkout', '--detach', 'FETCH_HEAD', cwd=target)
        if run('git', 'rev-parse', 'HEAD', cwd=target, capture=True).decode().strip() != commit:
            raise RuntimeError(f'{name}: different commit; existing checkout left unchanged')
        expected = (ROOT / item['patch']).read_bytes() if item['patch'] else b''
        actual = run('git', 'diff', '--binary', 'HEAD', cwd=target, capture=True)
        if actual != expected:
            if args.check or actual or not expected:
                raise RuntimeError(f'{name}: local tracked changes differ from the deployed patch')
            patch = ROOT / item['patch']
            run('git', 'apply', '--check', str(patch), cwd=target)
            run('git', 'apply', str(patch), cwd=target)
            if run('git', 'diff', '--binary', 'HEAD', cwd=target, capture=True) != expected:
                raise RuntimeError(f'{name}: applied patch differs from snapshot')
        print(f'OK {name} {commit} + deployed patch' if expected else f'OK {name} {commit}')


if __name__ == '__main__':
    main()
