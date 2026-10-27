#!/usr/bin/env python3
"""Fetch the preview's CC0 material maps and OFL fonts; verify their pinned hashes."""
import hashlib
import json
from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parent.parent / 'public' / 'assets'
sources = json.loads((root / 'sources.json').read_text())
checksums = json.loads((root / 'checksums.json').read_text())
for name, url in sources.items():
    target = root / name
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == checksums[name]:
        continue
    data = urllib.request.urlopen(url, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != checksums[name]:
        raise SystemExit(f'Checksum mismatch for {name}; upstream content changed. Review before updating the lock.')
    target.write_bytes(data)
    print(f'Fetched {name}')
print('All preview assets verified.')
