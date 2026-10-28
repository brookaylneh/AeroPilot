"""Verify a pasted Quixel authorization header and save it privately for downloads."""

import getpass
import http.client
import json
import os
from pathlib import Path

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[3]


def verify(authorization):
    assert authorization.startswith("Bearer "), "Copy the complete Authorization header value."
    token = authorization.removeprefix("Bearer ")
    assert len(token) >= 24 and not any(c.isspace() for c in token), "Invalid token format."
    connection = http.client.HTTPSConnection("quixel.com", timeout=30)
    try:
        connection.request(
            "GET",
            "/v1/users/self",
            headers={"Authorization": authorization, "Accept": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200, f"Quixel authentication failed: HTTP {response.status}."
        profile = json.loads(response.read())
        assert isinstance(profile, dict) and profile, "Quixel returned an invalid user response."
    finally:
        connection.close()
    return token


def main():
    load_env()
    path = ROOT / ".secrets/quixel-auth.json"
    assert not path.exists(), "A Quixel credential already exists; inspect its status first."
    authorization = getpass.getpass("Paste the Quixel Authorization value (input hidden): ")
    token = verify(authorization.strip())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump({"token": token}, output)
        output.write("\n")
    print(
        "Quixel authentication verified. Credential saved privately in .secrets/quixel-auth.json."
    )


if __name__ == "__main__":
    main()
