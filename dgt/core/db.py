"""SQLite 数据库：盘点台账、文献清单、映射记录、复核队列、问题台账、作业日志等。"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS rule_version (
    version TEXT,
    digest TEXT PRIMARY KEY,
    files TEXT,
    loaded_at TEXT,
    review_note TEXT
);
CREATE TABLE IF NOT EXISTS batch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    dh_from TEXT,
    dh_to TEXT,
    program_version TEXT,
    rule_version TEXT,
    status TEXT,
    total INTEGER DEFAULT 0,
    done INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    operator TEXT
);
CREATE TABLE IF NOT EXISTS handover (
    dh TEXT PRIMARY KEY,
    sheet TEXT,
    seq INTEGER,
    counts TEXT,
    file_count INTEGER,
    dir_count INTEGER,
    data_size TEXT,
    data_bytes INTEGER
);
CREATE TABLE IF NOT EXISTS archive (
    dh TEXT PRIMARY KEY,
    root TEXT,
    period TEXT,
    period_code TEXT,
    period_score REAL,
    period_basis TEXT,
    catalog_entries TEXT,
    file_count INTEGER,
    dir_count INTEGER,
    total_bytes INTEGER,
    category_counts TEXT,
    ext_counts TEXT,
    unreadable INTEGER DEFAULT 0,
    recon_status TEXT,
    recon_detail TEXT,
    proc_status TEXT DEFAULT '未处理',
    proc_rule_version TEXT,
    proc_message TEXT,
    scanned_at TEXT,
    processed_at TEXT,
    batch_id INTEGER
);
CREATE TABLE IF NOT EXISTS case_info (
    dh TEXT PRIMARY KEY,
    fields TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS document (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dh TEXT,
    seq INTEGER,
    kind TEXT,
    category TEXT,
    volume TEXT,
    item TEXT,
    title TEXT,
    title_source TEXT,
    archive_rel TEXT,
    source_rel TEXT,
    resources TEXT,
    secrecy TEXT,
    stage_ref TEXT,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS ix_document_dh ON document(dh);
CREATE TABLE IF NOT EXISTS project (
    uuid TEXT PRIMARY KEY,
    name TEXT,
    code TEXT,
    level TEXT,
    year TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS archive_project (
    dh TEXT,
    project_uuid TEXT,
    match_status TEXT,
    basis TEXT,
    confirmer TEXT,
    confirmed_at TEXT,
    manual INTEGER DEFAULT 0,
    PRIMARY KEY (dh, project_uuid)
);
CREATE TABLE IF NOT EXISTS mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    dh TEXT,
    doc_id INTEGER,
    role TEXT,
    src_rel TEXT,
    src_name TEXT,
    is_dir INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 1,
    category TEXT,
    title TEXT,
    title_source TEXT,
    period TEXT,
    project_uuid TEXT,
    project_level TEXT,
    match_status TEXT,
    stage TEXT,
    subdir TEXT,
    file_type TEXT,
    period_id TEXT,
    version TEXT,
    target_rel TEXT,
    target_name TEXT,
    method TEXT,
    status TEXT,
    evidence TEXT,
    rule_version TEXT,
    confidence REAL,
    secrecy TEXT,
    secret_flag TEXT,
    authenticity TEXT,
    confirmer TEXT,
    reviewer TEXT,
    processed_at TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS ix_mapping_dh ON mapping(dh);
CREATE TABLE IF NOT EXISTS review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dh TEXT,
    src_rel TEXT,
    mapping_id INTEGER,
    kind TEXT,
    reason TEXT,
    suggestion TEXT,
    candidates TEXT,
    status TEXT DEFAULT '待复核',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_review_dh ON review(dh);
CREATE TABLE IF NOT EXISTS decision (
    dh TEXT,
    src_rel TEXT,
    kind TEXT,
    result TEXT,
    basis TEXT,
    confirmer TEXT,
    reviewer TEXT,
    status TEXT,
    decided_at TEXT,
    PRIMARY KEY (dh, src_rel, kind)
);
CREATE TABLE IF NOT EXISTS issue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dh TEXT,
    kind TEXT,
    description TEXT,
    suggestion TEXT,
    owner TEXT,
    status TEXT DEFAULT '未关闭',
    created_at TEXT,
    closed_at TEXT,
    auto INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_issue_dh ON issue(dh);
CREATE TABLE IF NOT EXISTS oplog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    batch_id INTEGER,
    dh TEXT,
    step TEXT,
    level TEXT,
    message TEXT,
    operator TEXT
);
CREATE INDEX IF NOT EXISTS ix_oplog_ts ON oplog(ts);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def loads(text: str | None, default=None):
    if not text:
        return default
    return json.loads(text)


class Database:
    """线程安全的轻量封装。每个线程各自持有连接。"""

    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as c:
            c.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        c = self.connect()
        cur = c.execute(sql, params)
        c.commit()
        return cur

    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        return self.connect().execute(sql, params).fetchall()

    def one(self, sql: str, params=()):
        return self.connect().execute(sql, params).fetchone()

    def scalar(self, sql: str, params=(), default=None):
        r = self.one(sql, params)
        return r[0] if r and r[0] is not None else default

    def insert(self, table: str, row: dict) -> int:
        keys = list(row)
        sql = f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})"
        return self.execute(sql, [row[k] for k in keys]).lastrowid

    def upsert(self, table: str, row: dict) -> None:
        keys = list(row)
        sql = f"INSERT OR REPLACE INTO {table} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})"
        self.execute(sql, [row[k] for k in keys])

    def log(self, step: str, message: str, dh: str | None = None, level: str = "信息",
            batch_id: int | None = None, operator: str | None = None) -> None:
        self.insert("oplog", {"ts": now(), "batch_id": batch_id, "dh": dh, "step": step,
                              "level": level, "message": message, "operator": operator})

    def get_meta(self, key: str, default=None):
        return self.scalar("SELECT value FROM meta WHERE key=?", (key,), default)

    def set_meta(self, key: str, value) -> None:
        self.upsert("meta", {"key": key, "value": str(value)})

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
