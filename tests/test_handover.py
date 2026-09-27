"""到货交接台账 API 测试(stdlib unittest,无需额外依赖)。

运行:python tests/test_handover.py
每个用例使用独立的临时 SQLite 数据库,互不干扰。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

# 单温区方案:三文鱼(批2)10 件 + 鲜奶(批1)40 件
# 规划结果:箱1 = 三文鱼 10 + 鲜奶 32,箱2 = 鲜奶 8
PLAN = {
    "transport_hours": 24,
    "box_capacity": 12,
    "max_boxes": 20,
    "volume_unit": "m³",
    "zones": [{"name": "冷藏区", "t_min": 0, "t_max": 8}],
    "cargoes": [
        {"name": "鲜奶", "units": 40, "unit_volume": 0.25, "t_min": 2, "t_max": 6},
        {"name": "三文鱼", "units": 10, "unit_volume": 0.4, "t_min": -2, "t_max": 2},
    ],
}


class HandoverTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self._tmp.name, "handover.db")
        db.reset()
        self.client = TestClient(app)
        self.client.__enter__()  # 触发 lifespan:初始化数据库

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        db.reset()
        self._tmp.cleanup()

    # ---------- 辅助 ----------
    def create_order(self, plan_input=None) -> dict:
        resp = self.client.post(
            "/api/handover-orders", json={"plan_input": plan_input or PLAN}
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def submit(self, order_id: int, records: list[dict]):
        return self.client.post(
            f"/api/handover-orders/{order_id}/records", json={"records": records}
        )

    def nets(self, order: dict) -> dict[tuple[int, int], int]:
        return {
            (i["box_no"], i["batch_no"]): i["net_signed_units"]
            for i in order["items"]
        }

    # ---------- 创建交接单 ----------
    def test_create_order_freezes_due_units(self):
        order = self.create_order()
        due = {(i["box_no"], i["batch_no"]): i["due_units"] for i in order["items"]}
        self.assertEqual(due, {(1, 1): 32, (1, 2): 10, (2, 1): 8})
        self.assertTrue(all(i["net_signed_units"] == 0 for i in order["items"]))
        self.assertEqual(order["summary"]["total_due_units"], 50)
        self.assertEqual(order["summary"]["pending_units"], 50)
        self.assertEqual(order["records"], [])

    def test_create_order_rejects_unloadable_plan(self):
        bad = dict(PLAN)
        bad["cargoes"] = [
            {"name": "超低温", "units": 5, "unit_volume": 0.5,
             "t_min": -30, "t_max": -20}
        ]
        resp = self.client.post("/api/handover-orders", json={"plan_input": bad})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("无法创建交接单", resp.json()["detail"])

    def test_create_order_rejects_invalid_plan_input(self):
        resp = self.client.post(
            "/api/handover-orders", json={"plan_input": {"box_capacity": 0}}
        )
        self.assertIn(resp.status_code, (400, 422))

    def test_order_immune_to_plan_input_changes(self):
        order = self.create_order()
        # 修改规划输入重新规划(结果不同),交接单应保持不变
        changed = dict(PLAN)
        changed["box_capacity"] = 6
        resp = self.client.post("/api/plan", json=changed)
        self.assertEqual(resp.status_code, 200)
        again = self.client.get(f"/api/handover-orders/{order['id']}").json()
        due = {(i["box_no"], i["batch_no"]): i["due_units"] for i in again["items"]}
        self.assertEqual(due, {(1, 1): 32, (1, 2): 10, (2, 1): 8})

    def test_get_missing_order_404(self):
        resp = self.client.get("/api/handover-orders/999")
        self.assertEqual(resp.status_code, 404)

    # ---------- 多行提交 ----------
    def test_submit_multi_row_in_order(self):
        order = self.create_order()
        oid = order["id"]
        resp = self.submit(oid, [
            {"kind": "receive", "box_no": 1, "batch_no": 1, "units": 20},
            {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 10},
            {"kind": "receive", "box_no": 2, "batch_no": 1, "units": 8},
        ])
        self.assertEqual(resp.status_code, 201, resp.text)
        order = resp.json()
        self.assertEqual(self.nets(order), {(1, 1): 20, (1, 2): 10, (2, 1): 8})
        self.assertEqual(order["summary"]["pending_units"], 12)
        # 同一笔提交共享 submission_id
        sids = {r["submission_id"] for r in order["records"]}
        self.assertEqual(len(sids), 1)

    def test_submit_row_exceeding_due_rejects_whole_batch(self):
        oid = self.create_order()["id"]
        self.submit(oid, [
            {"kind": "receive", "box_no": 1, "batch_no": 1, "units": 20},
        ])
        before = self.client.get(f"/api/handover-orders/{oid}").json()
        resp = self.submit(oid, [
            {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 5},  # 合法行
            {"kind": "receive", "box_no": 1, "batch_no": 1, "units": 13},  # 20+13>32
        ])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("整笔未入账", resp.json()["detail"])
        self.assertIn("第 2 行", resp.json()["detail"])
        after = self.client.get(f"/api/handover-orders/{oid}").json()
        # 合法的第一行也不得入账
        self.assertEqual(after["records"], before["records"])
        self.assertEqual(self.nets(after), {(1, 1): 20, (1, 2): 0, (2, 1): 0})

    def test_submit_return_below_zero_rejected(self):
        oid = self.create_order()["id"]
        self.submit(oid, [
            {"kind": "receive", "box_no": 2, "batch_no": 1, "units": 8},
        ])
        resp = self.submit(oid, [
            {"kind": "return", "box_no": 2, "batch_no": 1, "units": 9},
        ])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("小于 0", resp.json()["detail"])

    def test_submit_same_row_cumulative_within_batch(self):
        """同一笔提交内多行按顺序累计:前两行签收满额后第三行超限,整笔拒绝。"""
        oid = self.create_order()["id"]
        resp = self.submit(oid, [
            {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 6},
            {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 4},
            {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 1},  # 累计 11>10
        ])
        self.assertEqual(resp.status_code, 400)
        order = self.client.get(f"/api/handover-orders/{oid}").json()
        self.assertEqual(order["records"], [])

    def test_submit_return_then_receive_same_batch_ok(self):
        oid = self.create_order()["id"]
        resp = self.submit(oid, [
            {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 10},
            {"kind": "return", "box_no": 1, "batch_no": 2, "units": 4},
            {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 3},
        ])
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(self.nets(resp.json())[(1, 2)], 9)

    def test_submit_unknown_box_or_batch_rejected(self):
        oid = self.create_order()["id"]
        resp = self.submit(oid, [
            {"kind": "receive", "box_no": 9, "batch_no": 1, "units": 1},
        ])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不存在", resp.json()["detail"])
        resp = self.submit(oid, [
            {"kind": "receive", "box_no": 2, "batch_no": 2, "units": 1},  # 箱2无批2
        ])
        self.assertEqual(resp.status_code, 400)

    def test_submit_validation_errors(self):
        oid = self.create_order()["id"]
        # 空提交
        resp = self.submit(oid, [])
        self.assertEqual(resp.status_code, 422)
        # 件数为 0 / 负数 / 非整数
        for units in (0, -3, 1.5, "abc"):
            resp = self.submit(oid, [
                {"kind": "receive", "box_no": 1, "batch_no": 1, "units": units},
            ])
            self.assertIn(resp.status_code, (400, 422), f"units={units!r}")
        # 非法类型
        resp = self.submit(oid, [
            {"kind": "sign", "box_no": 1, "batch_no": 1, "units": 1},
        ])
        self.assertEqual(resp.status_code, 422)
        # 不存在的交接单
        resp = self.submit(999, [
            {"kind": "receive", "box_no": 1, "batch_no": 1, "units": 1},
        ])
        self.assertEqual(resp.status_code, 404)

    # ---------- 撤销 ----------
    def _order_with_records(self) -> tuple[int, list[dict]]:
        """构造含 5 条记录的台账,返回 (交接单 id, 记录列表)。"""
        oid = self.create_order()["id"]
        self.submit(oid, [
            {"kind": "receive", "box_no": 1, "batch_no": 1, "units": 20},
            {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 10},
            {"kind": "receive", "box_no": 2, "batch_no": 1, "units": 8},
        ])
        self.submit(oid, [
            {"kind": "return", "box_no": 1, "batch_no": 2, "units": 5},
            {"kind": "receive", "box_no": 1, "batch_no": 1, "units": 12},
        ])
        order = self.client.get(f"/api/handover-orders/{oid}").json()
        return oid, order["records"]

    def test_void_conflicting_record_rejected(self):
        """撤销签收会使后续退回越界:拒绝撤销并指出冲突记录,台账不变。"""
        oid, records = self._order_with_records()
        recv = next(r for r in records if r["batch_no"] == 2 and r["kind"] == "receive")
        ret = next(r for r in records if r["kind"] == "return")
        resp = self.client.post(
            f"/api/handover-orders/{oid}/records/{recv['id']}/void"
        )
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertIn("无法撤销", body["detail"])
        self.assertEqual(body["conflicts"][0]["record_id"], ret["id"])
        self.assertEqual(body["conflicts"][0]["resulting_net"], -5)
        # 原台账保持不变
        order = self.client.get(f"/api/handover-orders/{oid}").json()
        self.assertFalse(any(r["voided"] for r in order["records"]))
        self.assertEqual(self.nets(order), {(1, 1): 32, (1, 2): 5, (2, 1): 8})

    def test_void_return_would_exceed_due_rejected(self):
        """撤销退回会使后续签收超过应交量:拒绝撤销。"""
        oid = self.create_order()["id"]
        self.submit(oid, [
            {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 10},
        ])
        self.submit(oid, [
            {"kind": "return", "box_no": 1, "batch_no": 2, "units": 4},
        ])
        self.submit(oid, [
            {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 4},  # 回到 10
        ])
        order = self.client.get(f"/api/handover-orders/{oid}").json()
        ret = next(r for r in order["records"] if r["kind"] == "return")
        resp = self.client.post(
            f"/api/handover-orders/{oid}/records/{ret['id']}/void"
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("超过应交", resp.json()["detail"])
        self.assertEqual(resp.json()["conflicts"][0]["resulting_net"], 14)

    def test_void_in_safe_order_succeeds(self):
        """先撤销后续冲突记录后即可撤销先前记录。"""
        oid, records = self._order_with_records()
        recv = next(r for r in records if r["batch_no"] == 2 and r["kind"] == "receive")
        ret = next(r for r in records if r["kind"] == "return")
        # 先撤销退回
        resp = self.client.post(f"/api/handover-orders/{oid}/records/{ret['id']}/void")
        self.assertEqual(resp.status_code, 200, resp.text)
        # 再撤销签收即可通过
        resp = self.client.post(
            f"/api/handover-orders/{oid}/records/{recv['id']}/void"
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        order = resp.json()
        self.assertEqual(self.nets(order), {(1, 1): 32, (1, 2): 0, (2, 1): 8})
        voided = [r for r in order["records"] if r["voided"]]
        self.assertEqual(len(voided), 2)
        self.assertTrue(all(r["voided_at"] for r in voided))
        self.assertEqual(order["summary"]["voided_record_count"], 2)
        self.assertEqual(order["summary"]["active_record_count"], 3)

    def test_void_twice_and_missing(self):
        oid, records = self._order_with_records()
        ret = next(r for r in records if r["kind"] == "return")
        self.client.post(f"/api/handover-orders/{oid}/records/{ret['id']}/void")
        resp = self.client.post(f"/api/handover-orders/{oid}/records/{ret['id']}/void")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("已撤销", resp.json()["detail"])
        resp = self.client.post(f"/api/handover-orders/{oid}/records/999/void")
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post(f"/api/handover-orders/999/records/{ret['id']}/void")
        self.assertEqual(resp.status_code, 404)

    # ---------- 持久化 ----------
    def test_persistence_across_reconnect(self):
        """模拟重启:关闭连接后重新打开同一数据库文件,台账可继续办理。"""
        oid, records = self._order_with_records()
        db.reset()  # 相当于进程重启后重新连接
        resp = self.client.get("/api/handover-orders")
        self.assertEqual(resp.status_code, 200)
        orders = resp.json()["orders"]
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["id"], oid)
        self.assertEqual(orders[0]["net_signed_units"], 45)
        self.assertEqual(orders[0]["pending_units"], 5)
        # 重启后可以继续提交记录
        resp = self.submit(oid, [
            {"kind": "return", "box_no": 2, "batch_no": 1, "units": 3},
        ])
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(self.nets(resp.json())[(2, 1)], 5)

    def test_list_orders_summary(self):
        oid, _ = self._order_with_records()
        resp = self.client.get("/api/handover-orders")
        self.assertEqual(resp.status_code, 200)
        (summary,) = resp.json()["orders"]
        self.assertEqual(summary["id"], oid)
        self.assertEqual(summary["box_count"], 2)
        self.assertEqual(summary["item_count"], 3)
        self.assertEqual(summary["total_due_units"], 50)
        self.assertEqual(summary["net_signed_units"], 45)
        self.assertEqual(summary["record_count"], 5)
        self.assertEqual(summary["active_record_count"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
