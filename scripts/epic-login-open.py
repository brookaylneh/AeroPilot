#!/usr/bin/env python3
"""Capture Epic's browser sign-in URL on this headless server.

Mounted as xdg-open only in explicit MetaHuman or Fab authentication commands.
The original URL is kept in a private file, never printed in engine logs.
"""
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

if len(sys.argv) != 2:
    raise SystemExit(2)
url = sys.argv[1]
parts = urlsplit(url)
if parts.scheme != 'https' or parts.hostname not in ('www.epicgames.com', 'epicgames.com', 'accounts.epicgames.com'):
    raise SystemExit('The requested browser destination is not an Epic sign-in host.')
root = Path(os.environ['SNR_AUTH_DIR'])
root.mkdir(mode=0o700, exist_ok=True)
filename = os.environ.get('SNR_AUTH_FILE', 'metahuman-login.url')
if filename not in ('metahuman-login.url', 'fab-login.url'):
    raise SystemExit(2)
fd = os.open(root / filename, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write(url + '\n')
