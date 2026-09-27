"""SQLite 持久化层:到货交接台账。

- 数据库路径由环境变量 HANDOVER_DB_PATH 指定,默认 ./data/handover.db
  (Docker Compose 中固定为 /data/handover.db 并挂载命名卷持久化);
- 首次使用时自动创建父目录并建表(IF NOT EXISTS,可重复启动);
- 单连接 + 可重入锁,多步写入用 BEGIN IMMEDIATE 显式事务保证整笔原子性
  (签收/退回提交与撤销要么全部入账,要么全部不入账);
- WAL 模式 + 外键约束。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

DB_PATH = os.environ.get("HANDOVER_DB_PATH", "data/handover.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS handover_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    plan_input TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handover_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES handover_orders(id) ON DELETE CASCADE,
    box_no INTEGER NOT NULL,
    batch_no INTEGER NOT NULL,
    batch_name TEXT NOT NULL,
    due_units INTEGER NOT NULL CHECK (due_units > 0),
    UNIQUE (order_id, box_no, batch_no)
);

CREATE TABLE IF NOT EXISTS handover_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES handover_orders(id) ON DELETE CASCADE,
    submission_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('receive', 'return')),
    box_no INTEGER NOT NULL,
    batch_no INTEGER NOT NULL,
    units INTEGER NOT NULL CHECK (units > 0),
    voided INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    voided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_handover_items_order
    ON handover_items(order_id, box_no, batch_no);
CREATE INDEX IF NOT EXISTS idx_handover_records_order
    ON handover_records(order_id, id);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    """返回全局连接(懒初始化:建目录、建表、开 WAL 与外键)。"""
    global _conn
    with _lock:
        if _conn is None:
            if DB_PATH != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
            conn = sqlite3.connect(DB_PATH, check_same_thread=False,
                                   isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            with _lock:
                conn.executescript(SCHEMA)
            _conn = conn
        return _conn


def init() -> None:
    """启动时显式初始化数据库(建目录 + 建表)。"""
    get_conn()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """显式事务:多步写入要么全部提交,要么整体回滚(整笔不入账)。"""
    conn = get_conn()
    with _lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def reset() -> None:
    """关闭并丢弃当前连接(仅测试用,便于切换数据库路径)。"""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
