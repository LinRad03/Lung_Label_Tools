"""SQLite schema and transaction helpers. Database remains in a controlled directory."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
 username TEXT PRIMARY KEY, role TEXT NOT NULL, password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username), expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS access_links (
 token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username),
 expires REAL NOT NULL, used_at REAL
);
CREATE TABLE IF NOT EXISTS cases (
 case_id TEXT PRIMARY KEY, project TEXT NOT NULL, cohort TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('development','test','review')),
 patient_group TEXT NOT NULL, examination_group TEXT NOT NULL,
 ct_path TEXT NOT NULL, report_path TEXT, mask_path TEXT,
 linkage_status TEXT NOT NULL, image_sha256 TEXT NOT NULL, source_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queues (
 queue_id TEXT PRIMARY KEY, project TEXT NOT NULL, cohort TEXT NOT NULL,
 disease TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('assisted','blind')),
 seed INTEGER NOT NULL, patient_budget INTEGER NOT NULL, frozen_hash TEXT NOT NULL,
 created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
 task_id INTEGER PRIMARY KEY AUTOINCREMENT, queue_id TEXT NOT NULL REFERENCES queues(queue_id),
 case_id TEXT NOT NULL REFERENCES cases(case_id), reader TEXT NOT NULL REFERENCES users(username),
 stage TEXT NOT NULL CHECK(stage IN ('A','B','adjudication')),
 status TEXT NOT NULL DEFAULT 'pending', version INTEGER NOT NULL DEFAULT 0,
 draft_json TEXT NOT NULL DEFAULT '{}', lease_until REAL, submitted_at REAL,
 UNIQUE(queue_id,case_id,reader,stage)
);
CREATE TABLE IF NOT EXISTS annotation_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(task_id),
 version INTEGER NOT NULL, stage TEXT NOT NULL, payload TEXT NOT NULL,
 action TEXT NOT NULL, saved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, actor TEXT NOT NULL,
 queue_id TEXT, task_id INTEGER, at REAL NOT NULL, detail_code TEXT
);
"""


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)


@contextmanager
def connect(path: Path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
