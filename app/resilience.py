"""单温区故障容灾评估。

基于同一份输入快照:依次将每个运输段的每个可用温区分别模拟为不可用,
复用完整装箱、锁温与热暴露规则重新规划(基准方案本身不被修改),
每个故障场景返回:

- 是否仍可运输:基准方案中已装载的货物在该场景下是否仍能全部装载
  (即没有任何批次由已装载变为未装载);
- 额外箱数:场景箱数 - 基准箱数;
- 由已装载变为未装载的批次与件数;
- 首个冲突原因:首个新增未装载批次在场景规划中的未装载原因。

全部场景按 未装载件数、额外箱数 降序(影响大者居前),段序号、温区序
升序稳定排序。评估结果携带输入快照指纹(SHA-256),输入变化后前端据此
将旧结果标记为已过期。
"""
from __future__ import annotations

import copy
from typing import Any

from .planner import input_fingerprint, plan


def _loaded_units_by_batch(result: dict[str, Any]) -> dict[int, int]:
    """按批号汇总方案中的已装载件数。"""
    loaded: dict[int, int] = {}
    for box in result["boxes"]:
        for it in box["items"]:
            loaded[it["batch_no"]] = loaded.get(it["batch_no"], 0) + it["units"]
    return loaded


def _batch_names(payload: dict[str, Any]) -> dict[int, str]:
    """批号 -> 批次名称(与 plan() 的默认命名规则一致)。"""
    return {
        i + 1: str(c.get("name") or f"批次 {i + 1}")
        for i, c in enumerate(payload.get("cargoes") or [])
    }


def _norm_zone(z: dict[str, Any], idx: int) -> dict[str, Any]:
    return {
        "name": str(z.get("name") or f"温区 {idx}"),
        "t_min": float(z["t_min"]),
        "t_max": float(z["t_max"]),
    }


def _segments_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """与 plan() 一致的运输段视图:分段模式取 segments,否则全程视为单段。"""
    raw = payload.get("segments") or []
    if raw:
        return [
            {
                "no": i + 1,
                "name": str(s.get("name") or f"第 {i + 1} 段").strip()
                or f"第 {i + 1} 段",
                "zones": [_norm_zone(z, j + 1) for j, z in enumerate(s.get("zones") or [])],
            }
            for i, s in enumerate(raw)
        ]
    return [
        {
            "no": 1,
            "name": "全程",
            "zones": [
                _norm_zone(z, j + 1) for j, z in enumerate(payload.get("zones") or [])
            ],
        }
    ]


def _remove_zone(payload: dict[str, Any], seg_no: int, zone_idx: int) -> dict[str, Any]:
    """返回移除了指定温区的输入深拷贝;基准输入不被修改。"""
    mod = copy.deepcopy(payload)
    if mod.get("segments"):
        zones = mod["segments"][seg_no - 1]["zones"]
    else:
        zones = mod["zones"]
    del zones[zone_idx]
    return mod


def _newly_unloaded(
    baseline_loaded: dict[int, int],
    scenario_loaded: dict[int, int],
    names: dict[int, str],
) -> list[dict[str, Any]]:
    """由已装载变为未装载的批次与件数(基准已装、场景少装的部分)。"""
    out = []
    for no in sorted(baseline_loaded):
        diff = baseline_loaded[no] - scenario_loaded.get(no, 0)
        if diff > 0:
            out.append({"batch_no": no, "name": names[no], "units": diff})
    return out


def _first_new_conflict(
    result: dict[str, Any], newly: list[dict[str, Any]]
) -> str | None:
    """场景规划中首个"新增未装载"批次的未装载原因;无新增未装载时为 None。"""
    newly_nos = {n["batch_no"] for n in newly}
    for u in result["unloaded"]:
        if u["batch_no"] in newly_nos:
            return u["reason"]
    return None


def _run_scenario(
    payload: dict[str, Any],
    baseline: dict[str, Any],
    seg: dict[str, Any],
    zone_idx: int,
) -> dict[str, Any]:
    """执行单个温区故障场景:seg 的第 zone_idx 个温区(0 基)模拟为不可用。"""
    zone = seg["zones"][zone_idx]
    base_summary = baseline["summary"]
    baseline_loaded = _loaded_units_by_batch(baseline)
    names = _batch_names(payload)
    head = {
        "segment_no": seg["no"],
        "segment_name": seg["name"],
        "zone_index": zone_idx + 1,
        "zone_name": zone["name"],
        "zone": {"name": zone["name"], "t_min": zone["t_min"], "t_max": zone["t_max"]},
    }

    if len(seg["zones"]) == 1:
        # 该段唯一温区失效后不再有任何可用温区:所有批次均无法通过该段,
        # 无需重新规划即可判定(此时构造的输入对 plan 非法,单独合成结果)
        newly = _newly_unloaded(baseline_loaded, {}, names)
        newly_units = sum(n["units"] for n in newly)
        return {
            **head,
            "transportable": newly_units == 0,
            "boxes_used": 0,
            "extra_boxes": -base_summary["boxes_used"],
            "loaded_units": 0,
            "newly_unloaded": newly,
            "newly_unloaded_units": newly_units,
            "first_conflict_reason": (
                (
                    f"运输段「{seg['name']}」(第 {seg['no']} 段)在温区"
                    f"「{zone['name']}」失效后不再有任何可用温区,全部批次均无法"
                    f"通过该段,全程温度无法保证"
                )
                if newly_units
                else None
            ),
        }

    result = plan(_remove_zone(payload, seg["no"], zone_idx))
    s_summary = result["summary"]
    newly = _newly_unloaded(baseline_loaded, _loaded_units_by_batch(result), names)
    newly_units = sum(n["units"] for n in newly)
    return {
        **head,
        "transportable": newly_units == 0,
        "boxes_used": s_summary["boxes_used"],
        "extra_boxes": s_summary["boxes_used"] - base_summary["boxes_used"],
        "loaded_units": s_summary["loaded_units"],
        "newly_unloaded": newly,
        "newly_unloaded_units": newly_units,
        "first_conflict_reason": _first_new_conflict(result, newly),
    }


def assess_zone_failures(payload: dict[str, Any]) -> dict[str, Any]:
    """单温区故障容灾评估入口;输入不合法时抛 ValueError(与 plan 一致)。"""
    # 基准方案:与 /api/plan 完全相同的规则,且评估过程不修改该结果
    baseline = plan(payload)
    base_summary = baseline["summary"]

    scenarios = [
        _run_scenario(payload, baseline, seg, zone_idx)
        for seg in _segments_of(payload)
        for zone_idx in range(len(seg["zones"]))
    ]
    # 稳定排序:未装载件数、额外箱数降序(影响大者居前),段序号、温区序升序
    scenarios.sort(
        key=lambda s: (
            -s["newly_unloaded_units"],
            -s["extra_boxes"],
            s["segment_no"],
            s["zone_index"],
        )
    )

    return {
        "input_fingerprint": input_fingerprint(payload),
        "baseline": {
            "boxes_used": base_summary["boxes_used"],
            "loaded_units": base_summary["loaded_units"],
            "unloaded_units": base_summary["unloaded_units"],
            "total_cargo_units": base_summary["total_cargo_units"],
            "all_transportable": base_summary["all_transportable"],
            "transport_hours": base_summary["transport_hours"],
            "segment_mode": base_summary["segment_mode"],
        },
        "scenarios": scenarios,
        "summary": {
            "scenario_count": len(scenarios),
            "transportable_count": sum(1 for s in scenarios if s["transportable"]),
            "failed_count": sum(1 for s in scenarios if not s["transportable"]),
            "max_newly_unloaded_units": max(
                (s["newly_unloaded_units"] for s in scenarios), default=0
            ),
            "max_extra_boxes": max((s["extra_boxes"] for s in scenarios), default=0),
        },
        "volume_unit": baseline["volume_unit"],
    }
