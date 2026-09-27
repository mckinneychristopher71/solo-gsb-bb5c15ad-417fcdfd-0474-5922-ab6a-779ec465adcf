"""到货交接台账。

- 交接单从规划输入快照(plan_input,与 /api/plan 请求体相同)创建:服务端按
  快照重新规划,把各箱各批次的已装件数冻结为「应交件数」存入 SQLite;
  此后修改规划输入、重新生成方案均不影响已创建的交接单,重启后仍可继续办理。
- 签收/退回记录支持一次提交多行:每行指定箱号、批号与正整数件数,按提交
  顺序在既有台账上逐行累计净签收量(签收 +、退回 -),任何一步使某箱某批的
  净签收量小于 0 或超过应交件数,则整笔提交不入账(事务整体回滚)。
- 撤销:仅可撤销未撤销的记录。撤销前按原提交顺序跳过该记录重放其余有效
  记录,若任一后续记录导致净签收量越界,则拒绝撤销并指出冲突记录,
  原台账保持不变;重放通过才把该记录标记为已撤销(不删除,保留审计痕迹)。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from . import db
from .planner import input_fingerprint, plan

KIND_RECEIVE = "receive"  # 签收
KIND_RETURN = "return"  # 退回
_KINDS = (KIND_RECEIVE, KIND_RETURN)
_KIND_LABEL = {KIND_RECEIVE: "签收", KIND_RETURN: "退回"}


class HandoverError(ValueError):
    """台账业务校验错误(HTTP 400)。"""


class HandoverNotFound(HandoverError):
    """交接单或记录不存在(HTTP 404)。"""


class VoidConflictError(HandoverError):
    """撤销会导致后续记录越界(HTTP 409),携带冲突记录明细。"""

    def __init__(self, message: str, conflicts: list[dict[str, Any]]):
        super().__init__(message)
        self.conflicts = conflicts


def _now() -> str:
    """本地时间 ISO 串(秒级),用于台账时间戳。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _norm_units(raw: Any, ctx: str) -> int:
    """件数必须为正整数;非法抛 HandoverError。"""
    if isinstance(raw, bool):
        raise HandoverError(f"{ctx}件数必须为正整数,实际为 {raw!r}")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float) and raw.is_integer():
        value = int(raw)
    elif isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
    else:
        raise HandoverError(f"{ctx}件数必须为正整数,实际为 {raw!r}")
    if value <= 0:
        raise HandoverError(f"{ctx}件数必须为正整数,实际为 {value}")
    return value


def _norm_positive_int(raw: Any, label: str, ctx: str) -> int:
    value = _norm_units(raw, f"{ctx}{label}")
    return value


def _load_order_row(conn, order_id: int):
    row = conn.execute(
        "SELECT id, created_at, input_fingerprint, plan_input "
        "FROM handover_orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    if row is None:
        raise HandoverNotFound(f"交接单 #{order_id} 不存在")
    return row


def _load_items(conn, order_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT box_no, batch_no, batch_name, due_units FROM handover_items "
        "WHERE order_id = ? ORDER BY box_no, batch_no",
        (order_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_records(conn, order_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, submission_id, kind, box_no, batch_no, units, voided, "
        "created_at, voided_at FROM handover_records "
        "WHERE order_id = ? ORDER BY id",
        (order_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _replay(
    due: dict[tuple[int, int], int],
    records: list[dict[str, Any]],
    skip_id: int | None = None,
) -> tuple[dict[tuple[int, int], int], dict[str, Any] | None, int, int]:
    """按提交顺序重放记录,返回 (净签收表, 首个越界记录, 越界后的值, 应交量)。

    skip_id 用于撤销前预演:跳过该记录重放其余有效记录。所有记录顺利入账
    时越界记录为 None;一旦某行使净签收量小于 0 或超过应交量,立即返回该
    冲突记录及越界结果(不重放后续行)。
    """
    nets: dict[tuple[int, int], int] = {}
    for r in records:
        if skip_id is not None and r["id"] == skip_id:
            continue
        key = (r["box_no"], r["batch_no"])
        delta = r["units"] if r["kind"] == KIND_RECEIVE else -r["units"]
        new_net = nets.get(key, 0) + delta
        limit = due.get(key, 0)
        if new_net < 0 or new_net > limit:
            return nets, r, new_net, limit
        nets[key] = new_net
    return nets, None, 0, 0


def create_order(plan_input: dict[str, Any]) -> dict[str, Any]:
    """按规划输入快照创建交接单,冻结各箱各批次应交件数。"""
    result = plan(plan_input)  # 输入非法时抛 ValueError,由入口统一转 400

    # 按 (箱号, 批号) 汇总已装件数,冻结为应交件数
    due: dict[tuple[int, int], dict[str, Any]] = {}
    for box in result["boxes"]:
        for it in box["items"]:
            key = (int(box["box_no"]), int(it["batch_no"]))
            entry = due.setdefault(
                key,
                {
                    "box_no": key[0],
                    "batch_no": key[1],
                    "batch_name": str(it["name"]),
                    "due_units": 0,
                },
            )
            entry["due_units"] += int(it["units"])
    items = sorted(due.values(), key=lambda e: (e["box_no"], e["batch_no"]))
    if not items:
        raise HandoverError(
            "当前方案没有任何已装载的货物,无法创建交接单;请先调整输入使货物可装载"
        )

    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO handover_orders (created_at, input_fingerprint, plan_input) "
            "VALUES (?, ?, ?)",
            (_now(), input_fingerprint(plan_input), json.dumps(plan_input,
                                                              ensure_ascii=False)),
        )
        order_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO handover_items (order_id, box_no, batch_no, batch_name, "
            "due_units) VALUES (?, ?, ?, ?, ?)",
            [
                (order_id, it["box_no"], it["batch_no"], it["batch_name"],
                 it["due_units"])
                for it in items
            ],
        )
    return get_order(order_id)


def list_orders() -> list[dict[str, Any]]:
    """交接单列表(按创建顺序),附应交/净签收/待交汇总。"""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, created_at, input_fingerprint FROM handover_orders ORDER BY id"
    ).fetchall()
    out = []
    for row in rows:
        order_id = row["id"]
        items = _load_items(conn, order_id)
        records = _load_records(conn, order_id)
        nets, _, _, _ = _replay(
            {(i["box_no"], i["batch_no"]): i["due_units"] for i in items},
            [r for r in records if not r["voided"]],
        )
        total_due = sum(i["due_units"] for i in items)
        total_net = sum(nets.values())
        out.append(
            {
                "id": order_id,
                "created_at": row["created_at"],
                "input_fingerprint": row["input_fingerprint"],
                "box_count": len({i["box_no"] for i in items}),
                "item_count": len(items),
                "total_due_units": total_due,
                "net_signed_units": total_net,
                "pending_units": total_due - total_net,
                "record_count": len(records),
                "active_record_count": sum(1 for r in records if not r["voided"]),
            }
        )
    return out


def get_order(order_id: int) -> dict[str, Any]:
    """交接单详情:应交明细 + 各箱批次净签收/待交 + 全部记录。"""
    conn = db.get_conn()
    order = _load_order_row(conn, order_id)
    items = _load_items(conn, order_id)
    records = _load_records(conn, order_id)

    names = {(i["box_no"], i["batch_no"]): i["batch_name"] for i in items}
    nets, _, _, _ = _replay(
        {(i["box_no"], i["batch_no"]): i["due_units"] for i in items},
        [r for r in records if not r["voided"]],
    )

    items_out = []
    for it in items:
        key = (it["box_no"], it["batch_no"])
        net = nets.get(key, 0)
        items_out.append(
            {
                **it,
                "net_signed_units": net,
                "pending_units": it["due_units"] - net,
            }
        )
    records_out = [
        {
            **r,
            "voided": bool(r["voided"]),
            "batch_name": names.get((r["box_no"], r["batch_no"]), ""),
        }
        for r in records
    ]

    total_due = sum(i["due_units"] for i in items_out)
    total_net = sum(i["net_signed_units"] for i in items_out)
    return {
        "id": order["id"],
        "created_at": order["created_at"],
        "input_fingerprint": order["input_fingerprint"],
        "plan_input": json.loads(order["plan_input"]),
        "items": items_out,
        "records": records_out,
        "summary": {
            "box_count": len({i["box_no"] for i in items_out}),
            "item_count": len(items_out),
            "total_due_units": total_due,
            "net_signed_units": total_net,
            "pending_units": total_due - total_net,
            "record_count": len(records_out),
            "active_record_count": sum(1 for r in records_out if not r["voided"]),
            "voided_record_count": sum(1 for r in records_out if r["voided"]),
        },
    }


def submit_records(order_id: int, records: list[dict[str, Any]]) -> dict[str, Any]:
    """一次提交多行签收/退回记录;任一行违规则整笔不入账。"""
    if not records:
        raise HandoverError("本次提交至少需要一行签收/退回记录")

    # 1) 逐行规范化(不依赖数据库的校验:类型、箱号/批号/件数为正整数)
    rows: list[dict[str, Any]] = []
    for idx, raw in enumerate(records, start=1):
        ctx = f"第 {idx} 行的"
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in _KINDS:
            raise HandoverError(
                f"第 {idx} 行的类型须为 receive(签收)或 return(退回),"
                f"实际为 {raw.get('kind')!r},整笔未入账"
            )
        rows.append(
            {
                "kind": kind,
                "box_no": _norm_positive_int(raw.get("box_no"), "箱号", ctx),
                "batch_no": _norm_positive_int(raw.get("batch_no"), "批号", ctx),
                "units": _norm_units(raw.get("units"), ctx),
            }
        )

    # 2) 同一事务内读取台账、逐行校验并写入:任一行越界则整体回滚
    with db.transaction() as conn:
        _load_order_row(conn, order_id)
        items = _load_items(conn, order_id)
        due = {(i["box_no"], i["batch_no"]): i["due_units"] for i in items}
        names = {(i["box_no"], i["batch_no"]): i["batch_name"] for i in items}
        for idx, r in enumerate(rows, start=1):
            if (r["box_no"], r["batch_no"]) not in due:
                raise HandoverError(
                    f"第 {idx} 行:交接单 #{order_id} 中不存在第 {r['box_no']} 箱 "
                    f"批 {r['batch_no']} 的应交明细,整笔未入账"
                )

        active = [r for r in _load_records(conn, order_id) if not r["voided"]]
        nets, _, _, _ = _replay(due, active)
        for idx, r in enumerate(rows, start=1):
            key = (r["box_no"], r["batch_no"])
            delta = r["units"] if r["kind"] == KIND_RECEIVE else -r["units"]
            new_net = nets.get(key, 0) + delta
            limit = due[key]
            label = _KIND_LABEL[r["kind"]]
            name = names[key]
            if new_net > limit:
                raise HandoverError(
                    f"第 {idx} 行:{label} {r['units']} 件后,第 {r['box_no']} 箱 "
                    f"批 {r['batch_no']}「{name}」的净签收量将达 {new_net} 件,"
                    f"超过应交 {limit} 件,整笔未入账"
                )
            if new_net < 0:
                raise HandoverError(
                    f"第 {idx} 行:{label} {r['units']} 件后,第 {r['box_no']} 箱 "
                    f"批 {r['batch_no']}「{name}」的净签收量将为 {new_net} 件,"
                    f"小于 0,整笔未入账"
                )
            nets[key] = new_net

        # 全部通过才入账(同一提交批次号,随事务整体提交)
        row = conn.execute(
            "SELECT COALESCE(MAX(submission_id), 0) + 1 AS next_sid "
            "FROM handover_records WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        submission_id = int(row["next_sid"])
        now = _now()
        conn.executemany(
            "INSERT INTO handover_records (order_id, submission_id, kind, box_no, "
            "batch_no, units, voided, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            [
                (order_id, submission_id, r["kind"], r["box_no"], r["batch_no"],
                 r["units"], now)
                for r in rows
            ],
        )
    return get_order(order_id)


def void_record(order_id: int, record_id: int) -> dict[str, Any]:
    """撤销一条未撤销的记录;撤销会使后续记录越界时拒绝并保持原台账。"""
    with db.transaction() as conn:
        _load_order_row(conn, order_id)
        items = _load_items(conn, order_id)
        due = {(i["box_no"], i["batch_no"]): i["due_units"] for i in items}
        names = {(i["box_no"], i["batch_no"]): i["batch_name"] for i in items}
        records = _load_records(conn, order_id)

        target = next((r for r in records if r["id"] == record_id), None)
        if target is None:
            raise HandoverNotFound(
                f"交接单 #{order_id} 中不存在记录 #{record_id}"
            )
        if target["voided"]:
            raise HandoverError(f"记录 #{record_id} 已撤销,无需重复撤销")

        # 跳过该记录,按原提交顺序重放其余有效记录
        active = [r for r in records if not r["voided"]]
        _, conflict, new_net, limit = _replay(due, active, skip_id=record_id)
        if conflict is not None:
            key = (conflict["box_no"], conflict["batch_no"])
            label = _KIND_LABEL[conflict["kind"]]
            name = names.get(key, "")
            conflict_info = {
                "record_id": conflict["id"],
                "submission_id": conflict["submission_id"],
                "kind": conflict["kind"],
                "box_no": conflict["box_no"],
                "batch_no": conflict["batch_no"],
                "batch_name": name,
                "units": conflict["units"],
                "resulting_net": new_net,
                "due_units": limit,
            }
            bound = "小于 0" if new_net < 0 else f"超过应交 {limit} 件"
            raise VoidConflictError(
                f"无法撤销记录 #{record_id}:撤销后重放至记录 #{conflict['id']}"
                f"({label} 第 {conflict['box_no']} 箱 批 {conflict['batch_no']}"
                f"「{name}」{conflict['units']} 件)时,该箱该批净签收量将变为 "
                f"{new_net} 件,{bound};请先撤销其后的冲突记录",
                [conflict_info],
            )

        conn.execute(
            "UPDATE handover_records SET voided = 1, voided_at = ? WHERE id = ?",
            (_now(), record_id),
        )
    return get_order(order_id)
