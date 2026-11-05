"""Issue one-use, password-free reader links in the controlled pilot directory."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from annotator.db import connect, initialize


def main() -> None:
    private = Path(os.environ["ANNOTATOR_PILOT_DIR"]).resolve()
    db = private / "annotations.sqlite3"
    destination = private / "reader_access_links.json"
    if destination.exists():
        print("ACCESS_LINKS_ALREADY_ISSUED")
        return
    initialize(db)
    links = {}
    with connect(db) as cx:
        for reader in ("pilot_reader1", "pilot_reader2"):
            if not cx.execute("SELECT 1 FROM users WHERE username=?", (reader,)).fetchone():
                raise RuntimeError("PILOT_READER_MISSING")
            token = secrets.token_urlsafe(48)
            cx.execute("INSERT INTO access_links VALUES (?,?,?,NULL)",
                       (hashlib.sha256(token.encode()).hexdigest(), reader, time.time() + 30 * 86400))
            links[reader] = f"http://127.0.0.1:8767/access/{token}"
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(links, stream, indent=2)
    print("ACCESS_LINKS_ISSUED", len(links))


if __name__ == "__main__":
    main()
