"""Administrative commands. Output contains counts only."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from .app import hash_password, import_manifest
from .db import connect, initialize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("runtime/annotations.sqlite3"))
    command = parser.add_subparsers(dest="command", required=True)
    add = command.add_parser("add-user")
    add.add_argument("username")
    add.add_argument("role", choices=["admin", "reader1", "reader2", "adjudicator"])
    imp = command.add_parser("import-manifest")
    imp.add_argument("manifest", type=Path)
    imp.add_argument("--data-root", type=Path, action="append", required=True)
    args = parser.parse_args()
    initialize(args.db)
    if args.command == "add-user":
        password = getpass.getpass("New password: ")
        if len(password) < 12:
            raise SystemExit("PASSWORD_TOO_SHORT")
        with connect(args.db) as db:
            db.execute("INSERT INTO users(username,role,password_hash) VALUES (?,?,?)",
                       (args.username, args.role, hash_password(password)))
        print("USER_CREATED")
    elif args.command == "import-manifest":
        roots = tuple(p.resolve() for p in args.data_root)
        print(json.dumps(import_manifest(args.db, args.manifest, roots), sort_keys=True))


if __name__ == "__main__":
    main()
