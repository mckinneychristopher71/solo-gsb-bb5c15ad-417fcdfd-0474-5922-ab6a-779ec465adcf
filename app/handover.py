"""到货交接台账(SQLite 持久化)。

- 从当前分箱方案创建交接单:按方案输出冻结各箱各批次的应交件数,
  连同规划输入快照与指纹一并入库;此后修改规划输入不影响已建交接单。
- 签收/退回记录一次可提交多行,每行指定箱号、批号与正整数件数;
  按提交顺序累计净签收量(签收 +件数,退回 −件数),任一时刻不得小于 0
  或超过该箱该批的应交量;任一行违规则整笔不入账。
- 允许撤销未撤销的记录:跳过该记录重放其余有效记录,若任何后续记录
  导致数量越界则拒绝撤销并指出冲突记录,原台账保持不变。
- 数据存于 SQLite(路径由环境变量 HANDOVER_DB_PATH 指定,默认
  data/handover.db),服务重启后可继续办理。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

from .planner import input_fingerprint, plan

_KIND_LABEL = {"sign": "签收", "return": "退回"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS handover_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    plan_fingerprint TEXT NOT NULL,
    plan_input TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handover_lines (
    order_id INTEGER NOT NULL REFERENCES handover_orders(id) ON DELETE CASCADE,
    box_no INTEGER NOT NULL,
    batch_no INTEGER NOT NULL,
    batch_name TEXT NOT NULL,
    expected_units INTEGER NOT NULL CHECK (expected_units > 0),
    PRIMARY KEY (order_id, box_no, batch_no)
);
CREATE TABLE IF NOT EXISTS handover_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES handover_orders(id) ON DELETE CASCADE,
    submission_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('sign', 'return')),
    box_no INTEGER NOT NULL,
    batch_no INTEGER NOT NULL,
    units INTEGER NOT NULL CHECK (units > 0),
    note TEXT,
    created_at TEXT NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0,
    undone_at TEXT,
    UNIQUE (order_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_handover_records_order
    ON handover_records (order_id, seq);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


class UndoConflictError(Exception):
    """撤销会导致其余有效记录重放越界;conflicts 为冲突记录列表。"""

    def __init__(self, message: str, conflicts: list[dict[str, Any]]):
        super().__init__(message)
        self.conflicts = conflicts


def _db_path() -> str:
    return os.environ.get("HANDOVER_DB_PATH", "data/handover.db")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def init_db() -> None:
    """建库建表(幂等);服务启动时调用,各接口首次使用前也会自动调用。"""
    global _conn
    with _lock:
        if _conn is not None:
            return
        path = _db_path()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        _conn = conn


def _conn_ready() -> sqlite3.Connection:
    if _conn is None:
        init_db()
    assert _conn is not None
    return _conn


def _fetch_lines(conn: sqlite3.Connection, order_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT box_no, batch_no, batch_name, expected_units "
        "FROM handover_lines WHERE order_id=? ORDER BY box_no, batch_no",
        (order_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_records(conn: sqlite3.Connection, order_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, submission_id, seq, kind, box_no, batch_no, units, note, "
        "created_at, undone, undone_at FROM handover_records "
        "WHERE order_id=? ORDER BY seq",
        (order_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _net_units(
    lines: list[dict[str, Any]], records: list[dict[str, Any]]
) -> dict[tuple[int, int], int]:
    """按提交顺序重放有效(未撤销)记录,得到各箱各批次的净签收量。"""
    net = {(l["box_no"], l["batch_no"]): 0 for l in lines}
    for r in records:
        if r["undone"]:
            continue
        key = (r["box_no"], r["batch_no"])
        net[key] += r["units"] if r["kind"] == "sign" else -r["units"]
    return net


def _detail(
    conn: sqlite3.Connection,
    order_row: dict[str, Any],
    lines: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """组装交接单详情:应交明细 + 各箱批次净签收/待交 + 全部记录。"""
    net = _net_units(lines, records)
    lines_out = [
        {
            **l,
            "net_signed_units": net[(l["box_no"], l["batch_no"])],
            "pending_units": l["expected_units"] - net[(l["box_no"], l["batch_no"])],
        }
        for l in lines
    ]
    records_out = [
        {
            "record_id": r["id"],
            "seq": r["seq"],
            "submission_id": r["submission_id"],
            "kind": r["kind"],
            "kind_label": _KIND_LABEL[r["kind"]],
            "box_no": r["box_no"],
            "batch_no": r["batch_no"],
            "units": r["units"],
            "note": r["note"],
            "created_at": r["created_at"],
            "undone": bool(r["undone"]),
            "undone_at": r["undone_at"],
        }
        for r in records
    ]
    expected_total = sum(l["expected_units"] for l in lines_out)
    signed_total = sum(l["net_signed_units"] for l in lines_out)
    return {
        "id": order_row["id"],
        "title": order_row["title"],
        "created_at": order_row["created_at"],
        "plan_fingerprint": order_row["plan_fingerprint"],
        "lines": lines_out,
        "records": records_out,
        "summary": {
            "line_count": len(lines_out),
            "expected_units": expected_total,
            "net_signed_units": signed_total,
            "pending_units": expected_total - signed_total,
            "record_count": len(records_out),
            "active_record_count": sum(1 for r in records_out if not r["undone"]),
            "undone_record_count": sum(1 for r in records_out if r["undone"]),
            "complete": bool(lines_out)
            and all(l["pending_units"] == 0 for l in lines_out),
        },
    }


def list_orders() -> list[dict[str, Any]]:
    """交接单列表(新的在前),含应交/净签收/待交汇总。"""
    with _lock:
        conn = _conn_ready()
        rows = conn.execute(
            "SELECT o.id, o.title, o.created_at, o.plan_fingerprint, "
            "(SELECT COUNT(*) FROM handover_lines l WHERE l.order_id = o.id) "
            "  AS line_count, "
            "COALESCE((SELECT SUM(l.expected_units) FROM handover_lines l "
            "  WHERE l.order_id = o.id), 0) AS expected_units, "
            "COALESCE((SELECT SUM(CASE WHEN r.kind = 'sign' THEN r.units "
            "  ELSE -r.units END) FROM handover_records r "
            "  WHERE r.order_id = o.id AND r.undone = 0), 0) AS net_signed_units "
            "FROM handover_orders o ORDER BY o.id DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["pending_units"] = d["expected_units"] - d["net_signed_units"]
        d["complete"] = d["line_count"] > 0 and d["pending_units"] == 0
        out.append(d)
    return out


def get_order(order_id: int) -> dict[str, Any] | None:
    """交接单详情;不存在返回 None。"""
    with _lock:
        conn = _conn_ready()
        row = conn.execute(
            "SELECT * FROM handover_orders WHERE id=?", (order_id,)
        ).fetchone()
        if row is None:
            return None
        return _detail(
            conn,
            dict(row),
            _fetch_lines(conn, order_id),
            _fetch_records(conn, order_id),
        )


def create_order(plan_input: dict[str, Any], title: str | None = None) -> dict[str, Any]:
    """从当前方案创建交接单:按输入快照重新规划并冻结各箱各批次应交件数。

    输入不合法时抛 ValueError(与 plan 一致,由入口统一转为 400)。
    """
    result = plan(plan_input)
    boxes = result.get("boxes") or []
    if not boxes:
        raise ValueError("当前方案没有装出货物的箱子,无法创建交接单")
    # 冻结应交明细:同一箱同一批次合并计件
    lines: dict[tuple[int, int], dict[str, Any]] = {}
    for box in boxes:
        for it in box["items"]:
            key = (int(box["box_no"]), int(it["batch_no"]))
            line = lines.setdefault(
                key,
                {
                    "box_no": key[0],
                    "batch_no": key[1],
                    "batch_name": str(it["name"]),
                    "expected_units": 0,
                },
            )
            line["expected_units"] += int(it["units"])
    fingerprint = input_fingerprint(plan_input)
    title = (title or "").strip() or f"交接单 {datetime.now():%Y%m%d-%H%M%S}"
    with _lock:
        conn = _conn_ready()
        cur = conn.execute(
            "INSERT INTO handover_orders "
            "(title, created_at, plan_fingerprint, plan_input) VALUES (?,?,?,?)",
            (title, _now(), fingerprint, json.dumps(plan_input, ensure_ascii=False)),
        )
        order_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO handover_lines "
            "(order_id, box_no, batch_no, batch_name, expected_units) "
            "VALUES (?,?,?,?,?)",
            [
                (order_id, l["box_no"], l["batch_no"], l["batch_name"], l["expected_units"])
                for l in lines.values()
            ],
        )
        conn.commit()
    return get_order(order_id)


def submit_records(order_id: int, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """提交一笔(多行)签收/退回记录;任一行越界则整笔不入账。

    每行:kind(sign=签收/return=退回)、box_no、batch_no、units(正整数)、
    note(可选)。按行序累计净签收量,始终不得小于 0 或超过应交量。
    返回更新后的交接单详情;交接单不存在返回 None;行越界抛 ValueError。
    """
    with _lock:
        conn = _conn_ready()
        order_row = conn.execute(
            "SELECT * FROM handover_orders WHERE id=?", (order_id,)
        ).fetchone()
        if order_row is None:
            return None
        lines = _fetch_lines(conn, order_id)
        records = _fetch_records(conn, order_id)
        expected = {(l["box_no"], l["batch_no"]): l["expected_units"] for l in lines}
        names = {(l["box_no"], l["batch_no"]): l["batch_name"] for l in lines}
        net = _net_units(lines, records)
        # 先整体校验:任一行违规直接抛错,数据库不做任何写入
        for i, row in enumerate(rows, 1):
            key = (int(row["box_no"]), int(row["batch_no"]))
            label = _KIND_LABEL[row["kind"]]
            if key not in expected:
                raise ValueError(
                    f"第 {i} 行({label}):交接单中不存在 第 {key[0]} 箱 × "
                    f"批次 {key[1]} 的应交明细,整笔未入账"
                )
            delta = row["units"] if row["kind"] == "sign" else -row["units"]
            new = net[key] + delta
            if new < 0:
                raise ValueError(
                    f"第 {i} 行({label} {row['units']} 件「{names[key]}」):"
                    f"第 {key[0]} 箱 × 批次 {key[1]} 当前净签收 {net[key]} 件,"
                    f"退回后将为 {new} 件,小于 0,整笔未入账"
                )
            if new > expected[key]:
                raise ValueError(
                    f"第 {i} 行({label} {row['units']} 件「{names[key]}」):"
                    f"第 {key[0]} 箱 × 批次 {key[1]} 净签收将为 {new} 件,"
                    f"超过应交量 {expected[key]} 件,整笔未入账"
                )
            net[key] = new
        submission_id = conn.execute(
            "SELECT COALESCE(MAX(submission_id), 0) + 1 "
            "FROM handover_records WHERE order_id=?",
            (order_id,),
        ).fetchone()[0]
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM handover_records WHERE order_id=?",
            (order_id,),
        ).fetchone()[0]
        now = _now()
        for row in rows:
            seq += 1
            conn.execute(
                "INSERT INTO handover_records "
                "(order_id, submission_id, seq, kind, box_no, batch_no, "
                " units, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    order_id,
                    submission_id,
                    seq,
                    row["kind"],
                    int(row["box_no"]),
                    int(row["batch_no"]),
                    int(row["units"]),
                    (row.get("note") or "").strip() or None,
                    now,
                ),
            )
        conn.commit()
        return _detail(
            conn,
            dict(order_row),
            _fetch_lines(conn, order_id),
            _fetch_records(conn, order_id),
        )


def undo_record(order_id: int, record_id: int) -> dict[str, Any] | None:
    """撤销一条未撤销的记录。

    跳过该记录重放其余有效记录,若任何后续记录导致数量越界则拒绝撤销
    (抛 UndoConflictError 并附冲突记录列表),原台账保持不变。
    返回更新后的交接单详情;交接单或记录不存在返回 None。
    """
    with _lock:
        conn = _conn_ready()
        order_row = conn.execute(
            "SELECT * FROM handover_orders WHERE id=?", (order_id,)
        ).fetchone()
        if order_row is None:
            return None
        lines = _fetch_lines(conn, order_id)
        records = _fetch_records(conn, order_id)
        target = next((r for r in records if r["id"] == record_id), None)
        if target is None:
            return None
        if target["undone"]:
            raise ValueError(f"记录 #{target['seq']} 已撤销,不能重复撤销")
        expected = {(l["box_no"], l["batch_no"]): l["expected_units"] for l in lines}
        net = {key: 0 for key in expected}
        conflicts: list[dict[str, Any]] = []
        for r in records:
            if r["undone"] or r["id"] == record_id:
                continue
            key = (r["box_no"], r["batch_no"])
            delta = r["units"] if r["kind"] == "sign" else -r["units"]
            new = net[key] + delta
            if new < 0 or new > expected[key]:
                conflicts.append(
                    {
                        "record_id": r["id"],
                        "seq": r["seq"],
                        "kind": r["kind"],
                        "kind_label": _KIND_LABEL[r["kind"]],
                        "box_no": key[0],
                        "batch_no": key[1],
                        "units": r["units"],
                        "net_after": new,
                        "expected_units": expected[key],
                    }
                )
            net[key] = new
        if conflicts:
            parts = "、".join(
                f"记录 #{c['seq']}({c['kind_label']} {c['units']} 件,"
                f"第 {c['box_no']} 箱 × 批次 {c['batch_no']})"
                f"将使净签收量变为 {c['net_after']} 件(应交 {c['expected_units']} 件)"
                for c in conflicts
            )
            raise UndoConflictError(
                f"无法撤销记录 #{target['seq']}"
                f"({_KIND_LABEL[target['kind']]} {target['units']} 件):"
                f"跳过该记录重放其余有效记录时越界——{parts};"
                f"已拒绝撤销,台账保持不变",
                conflicts,
            )
        conn.execute(
            "UPDATE handover_records SET undone=1, undone_at=? WHERE id=?",
            (_now(), record_id),
        )
        conn.commit()
        return _detail(
            conn,
            dict(order_row),
            _fetch_lines(conn, order_id),
            _fetch_records(conn, order_id),
        )
