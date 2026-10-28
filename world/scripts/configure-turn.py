"""Generate same-server TURN credentials without placing secrets in source/logs."""
import argparse
import json
import os
from pathlib import Path
import secrets
import ipaddress

parser = argparse.ArgumentParser()
parser.add_argument('--public-ip', required=True)
parser.add_argument('--private-ip', required=True)
args = parser.parse_args()
public = str(ipaddress.IPv4Address(args.public_ip))
private = str(ipaddress.IPv4Address(args.private_ip))
os.umask(0o077)
root = Path(__file__).resolve().parents[2]
directory = root / '.secrets'
directory.mkdir(mode=0o700, exist_ok=True)
auth = directory / 'turn.json'
secret = json.loads(auth.read_text())['secret'] if auth.exists() else secrets.token_hex(32)
auth.write_text(json.dumps({'host': public, 'secret': secret}))
(directory / 'turnserver.conf').write_text(f'''listening-port=3478
listening-ip={private}
relay-ip={private}
external-ip={public}/{private}
min-port=49300
max-port=49350
realm=wildlands.local
fingerprint
use-auth-secret
static-auth-secret={secret}
user-quota=4
total-quota=16
max-bps=6250000
no-cli
no-tls
no-dtls
no-tcp-relay
no-multicast-peers
denied-peer-ip=0.0.0.0-255.255.255.255
allowed-peer-ip={private}
allowed-peer-ip={public}
pidfile=/tmp/turnserver.pid
log-file=stdout
simple-log
''')
print('TURN configured: expiring browser credentials, relay peers restricted to this server.')
