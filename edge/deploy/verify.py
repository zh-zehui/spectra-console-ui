"""Verify source provenance and syntax; optional imports/health checks never capture RF."""
import argparse
import ast
import hashlib
import importlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true', help='Only stdlib/source checks; no device or service requests')
    parser.add_argument('--health', action='store_true', help='GET the running API/UI health endpoints after import checks')
    args = parser.parse_args()
    if args.source_only and args.health:
        parser.error('--source-only and --health are mutually exclusive')
    snapshot = json.loads((ROOT / 'deploy/source-snapshot.json').read_text())
    for item in snapshot['files']:
        path = (ROOT / item['path']).resolve()
        if not path.is_relative_to(ROOT):
            raise RuntimeError('Manifest entry escapes the edge directory')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise RuntimeError('Source differs from exported Jetson snapshot: ' + item['path'])
    python_files = [path for directory in ['src', 'plugins', 'adapters', 'tests', 'scripts', 'deploy', 'vendor/usrp_client'] for path in (ROOT / directory).rglob('*.py') if '__pycache__' not in path.parts]
    for path in python_files:
        ast.parse(path.read_bytes(), filename=str(path))
    print(f'PASS: {len(snapshot["files"])} source hashes and {len(python_files)} Python syntax checks')
    if args.source_only:
        return
    for module in ['numpy', 'scipy', 'gnuradio.gr', 'uhd', 'fastapi', 'uvicorn', 'httpx', 'sigmf']:
        importlib.import_module(module)
        print('Import OK:', module)
    if args.health:
        for url in ['http://127.0.0.1:8765/healthz', 'http://127.0.0.1:8780/edge/health']:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status != 200:
                    raise RuntimeError('Health endpoint failed: ' + url)
                print('Health OK:', url)
    print('Imports checked; native receiver builds and real-model tasks require separate validation.')


if __name__ == '__main__':
    main()
