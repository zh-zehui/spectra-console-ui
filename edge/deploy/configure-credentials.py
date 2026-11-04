"""Create the existing backend credential format interactively, without logging keys."""
import getpass
import json
import os
from pathlib import Path
import secrets


def main():
    folder = Path.home() / '.config/spectrum-agent'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    target = folder / 'credentials.json'
    if target.exists():
        raise SystemExit('Credential file already exists; it was not overwritten.')
    key = getpass.getpass('DeepSeek API key (hidden): ').strip()
    if not key:
        raise SystemExit('A key is required; no credential file was created.')
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        json.dump({'deepseek_api_key': key, 'api_token': secrets.token_urlsafe(32)}, output)
        output.write('\n')
    print('Credential file created with mode 600. The generated API token was not printed.')


if __name__ == '__main__':
    main()
