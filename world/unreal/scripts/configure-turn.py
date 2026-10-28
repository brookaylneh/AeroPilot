"""Configure same-host TURN and explicitly rotate its shared credential."""

import argparse
import ipaddress
import json
import os
import secrets
from pathlib import Path

from mission_planner.environment import load_env

load_env()
parser = argparse.ArgumentParser()
parser.add_argument("--public-ip", required=True)
parser.add_argument("--private-ip", required=True)
parser.add_argument("--rotate", action="store_true", help="Create a new shared credential")
args = parser.parse_args()
public = str(ipaddress.IPv4Address(args.public_ip))
private = str(ipaddress.IPv4Address(args.private_ip))
assert ipaddress.IPv4Address(private).is_private
os.umask(0o077)
root = Path(__file__).resolve().parents[3]
directory = root / ".secrets"
directory.mkdir(mode=0o700, exist_ok=True)
auth = directory / "turn.json"
if args.rotate:
    secret = secrets.token_hex(32)
else:
    secret = json.loads(auth.read_text())["secret"]
assert len(secret) == 64 and all(char in "0123456789abcdef" for char in secret)
auth.write_text(json.dumps({"host": public, "secret": secret}))
config = directory / "turnserver.conf"
config.write_text(f"""listening-port={int(os.environ["SNR_TURN_PORT"])}
listening-ip={private}
relay-ip={private}
external-ip={public}/{private}
min-port={int(os.environ["SNR_TURN_RELAY_MIN"])}
max-port={int(os.environ["SNR_TURN_RELAY_MAX"])}
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
""")
auth.chmod(0o600)
config.chmod(0o600)
print("TURN configured; credential values are not logged.")
