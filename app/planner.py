"""冷链装箱温控规划器核心算法。

规则:
- 每个温区是一个闭区间 [t_min, t_max],货物允许温区同理。
- 货物只能装入"允许温区与某个箱体可用温区存在交集"的箱子。
- 箱内可行温度 = 已装入货物允许温区的交集;实际可设定温度还必须落在某个
  温区内,因此最终输出的温度段是 "可行交集 ∩ 各温区" 的合并结果。
- 支持分段运输:按顺序维护若干运输段(名称/时长/可用温区),箱内货物的
  共同允许温度必须在每一段都能被该段某个温区覆盖,否则报告首次不兼容的
  运输段;只有全程通过才标记为可运输。未提供运输段时退化为全程单一温区。
- 支持累计热暴露预算(度时预算):货物可填写首选温度区间与最大度时预算
  (℃·h),允许温区仍作为硬边界。规划器为每个箱子逐段选择设定点(同时落在
  箱内货物允许温区交集与该段某个温区内),单段暴露量 = 段时长 × 设定点到
  该批首选区间的距离;每段从可行区间端点与首选区间端点中选取使本段结束后
  箱内最大累计预算使用率最低的点,平局取较低温度。任何货物累计超预算时
  该装箱组合不可用;未填写预算的批次保持原规则。
- 支持锁温区段:运输段可选填锁温组,相邻且同组的段必须共用一个设定点。
  共享设定点取自各段可行温度段(可能不连续)交集保留的真实区间,结合组前
  累计热暴露,选取使整组结束时箱内最大预算使用率最低的点,平局取较低温度;
  交集为空(不存在共享设定点)时该装箱组合不可用,并报告首个失败组及涉及段。
- 同一批货物可按"件"为最小单位拆分到多个箱子。
- 采用 First-Fit-Decreasing 启发式:温度下限越低(要求越苛刻)的批次优先,
  同温限按总体积降序;依次塞入已有箱子,放不下再开新箱。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

EPS = 1e-9
# 未装载原因中最多列举几个温区/箱子名称
_DETAIL_LIMIT = 3


def input_fingerprint(payload: dict[str, Any]) -> str:
    """输入快照指纹:键序无关的规范化 JSON 的 SHA-256。"""
    canon = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _r(v: float) -> float:
    """规整浮点输出,消除 -0.0 之类误差。"""
    v = round(float(v), 4)
    return 0.0 if abs(v) < 1e-9 else v


@dataclass
class _Iv:
    lo: float
    hi: float

    def intersect(self, other: "_Iv") -> "_Iv | None":
        lo, hi = max(self.lo, other.lo), min(self.hi, other.hi)
        return _Iv(lo, hi) if lo <= hi + EPS else None


def _dist_to_iv(t: float, iv: _Iv) -> float:
    """温度 t 到闭区间的距离(区间内为 0)。"""
    if t < iv.lo:
        return iv.lo - t
    if t > iv.hi:
        return t - iv.hi
    return 0.0


def _usage_ratio(exposure: float, budget: float) -> float:
    """预算使用率;预算为 0 时,任何正暴露都视为无穷大。"""
    if budget <= EPS:
        return 0.0 if exposure <= EPS else float("inf")
    return exposure / budget


@dataclass
class _Zone:
    name: str
    iv: _Iv


@dataclass
class _Segment:
    """一个运输段:按顺序执行,段内可使用一组温区;可选锁温组。"""

    no: int
    name: str
    hours: float
    zones: list[_Zone]
    # 锁温组名称;相邻且同组的段必须共用一个设定点,None 表示不参与
    lock_group: str | None = None


def _first_failed_segment(segments: list[_Segment], iv: _Iv) -> _Segment | None:
    """返回首个没有任何温区能覆盖 iv 的运输段;全程兼容时返回 None。"""
    for seg in segments:
        if not any(iv.intersect(z.iv) is not None for z in seg.zones):
            return seg
    return None


def _intersect_sets(a: list["_Iv"], b: list["_Iv"]) -> list["_Iv"]:
    """两组温度段(各自互不重叠)的交集,结果保留真实区间(可能不连续)。"""
    return _merge_segments(
        [s for x in a for y in b if (s := x.intersect(y)) is not None]
    )


def _lock_units(segments: list[_Segment]) -> list[tuple[str | None, list[int]]]:
    """把运输段划分为设定点决策单元。

    相邻且锁温组相同的段构成一个单元(必须共用一个设定点);未填写锁温组的
    段各自独立成单元。返回 [(锁温组名|None, [段下标, ...]), ...]。
    """
    units: list[tuple[str | None, list[int]]] = []
    i = 0
    while i < len(segments):
        group = segments[i].lock_group
        if group is None:
            units.append((None, [i]))
            i += 1
            continue
        j = i
        while j + 1 < len(segments) and segments[j + 1].lock_group == group:
            j += 1
        units.append((group, list(range(i, j + 1))))
        i = j + 1
    return units


def _unit_feasible(
    segments: list[_Segment], idxs: list[int], feasible: _Iv
) -> list[_Iv]:
    """一个决策单元的共享可行温度段:单元内各段可行温度段的交集。

    每段可行温度段 = 箱内允许温区交集 ∩ 该段各温区(可能为不连续的多段);
    单元交集同样保留真实区间,不做凸包合并。交集为空表示不存在共享设定点。
    """
    common: list[_Iv] | None = None
    for i in idxs:
        seg_feas = _merge_segments(
            [s for z in segments[i].zones if (s := feasible.intersect(z.iv)) is not None]
        )
        common = seg_feas if common is None else _intersect_sets(common, seg_feas)
        if not common:
            return []
    return common or []


def _first_failed_lock_run(
    segments: list[_Segment], feasible: _Iv
) -> tuple[str, list[_Segment]] | None:
    """返回首个不存在共享设定点的锁温组(组名, 涉及段);全部可行时返回 None。"""
    for group, idxs in _lock_units(segments):
        if group is None or len(idxs) < 2:
            continue  # 单段组无额外共享约束,逐段温区校验已覆盖
        if not _unit_feasible(segments, idxs, feasible):
            return group, [segments[i] for i in idxs]
    return None


@dataclass
class _Loaded:
    batch_no: int
    name: str
    units: int
    volume: float


@dataclass
class _Box:
    index: int
    capacity: float
    segments: list[_Segment]
    used: float = 0.0
    items: list[_Loaded] = field(default_factory=list)
    # 已装入货物允许温区的交集;首件货物装入时初始化
    feasible: _Iv | None = None
    # 箱内批次(按批号去重),用于热暴露核算
    lots: list["_Batch"] = field(default_factory=list)
    # 最近一次 try_load 失败原因:capacity / temp / lock / budget
    last_fail: str | None = None

    def try_load(self, batch: "_Batch", iv: _Iv) -> bool:
        """尝试装入 1 件货物,成功则更新箱内温度交集与容量。"""
        self.last_fail = None
        if self.used + batch.unit_volume > self.capacity + EPS:
            self.last_fail = "capacity"
            return False
        new_feasible = iv if self.feasible is None else self.feasible.intersect(iv)
        if new_feasible is None:
            self.last_fail = "temp"
            return False
        # 交集后的温度必须在每个运输段都至少能被该段一个温区覆盖
        if _first_failed_segment(self.segments, new_feasible) is not None:
            self.last_fail = "temp"
            return False
        # 相邻同组运输段必须存在共享设定点(各段可行温度段交集非空)
        if _first_failed_lock_run(self.segments, new_feasible) is not None:
            self.last_fail = "lock"
            return False
        already_in = any(lot.no == batch.no for lot in self.lots)
        if not already_in:
            # 新批次进入会收窄可行温度区间,需按新组合重算逐段设定点与
            # 各批累计热暴露;任何一批超预算则该装箱组合不可用
            new_lots = self.lots + [batch]
            if any(lot.budget is not None for lot in new_lots) and not _simulate_heat(
                self.segments, new_feasible, new_lots
            )["ok"]:
                self.last_fail = "budget"
                return False
        if self.items and self.items[-1].batch_no == batch.no:
            self.items[-1].units += 1
            self.items[-1].volume = _r(self.items[-1].volume + batch.unit_volume)
        else:
            self.items.append(
                _Loaded(
                    batch_no=batch.no,
                    name=batch.name,
                    units=1,
                    volume=batch.unit_volume,
                )
            )
        self.used = _r(self.used + batch.unit_volume)
        self.feasible = new_feasible
        if not already_in:
            self.lots.append(batch)
        return True

    def can_take(self, iv: _Iv) -> bool:
        """该箱是否在温区上能容纳此温度区间的货物(不考虑剩余容量)。"""
        candidate = iv if self.feasible is None else self.feasible.intersect(iv)
        if candidate is None or _first_failed_segment(self.segments, candidate) is not None:
            self.last_fail = "temp"
            return False
        if _first_failed_lock_run(self.segments, candidate) is not None:
            self.last_fail = "lock"
            return False
        return True


@dataclass
class _Batch:
    no: int
    name: str
    units: int
    unit_volume: float
    iv: _Iv
    max_hours: float | None
    pref: _Iv | None
    budget: float | None
    total_volume: float


def _fmt_names(names: list[str]) -> str:
    head = "、".join(names[:_DETAIL_LIMIT])
    if len(names) > _DETAIL_LIMIT:
        head += f" 等 {len(names)} 项"
    return head


def _merge_segments(segs: list[_Iv]) -> list[_Iv]:
    """合并相交或端点相接的温度段。"""
    segs = sorted(segs, key=lambda s: (s.lo, s.hi))
    merged: list[_Iv] = []
    for s in segs:
        if merged and s.lo <= merged[-1].hi + EPS:
            merged[-1].hi = max(merged[-1].hi, s.hi)
        else:
            merged.append(_Iv(s.lo, s.hi))
    return merged


def _simulate_heat(
    segments: list[_Segment], feasible: _Iv, lots: list[_Batch]
) -> dict[str, Any]:
    """对给定箱内批次组合,按决策单元选取设定点并累计各批热暴露。

    决策单元 = 相邻同锁温组的连续段(共用一个设定点)或单个未分组段。
    每个单元的候选设定点 = 单元共享可行温度段(单元内各段可行温度段的交集,
    保留真实区间,可能不连续)的端点,以及落在可行区间内的各批首选区间端点;
    结合单元开始前各批累计热暴露,选取使"本单元结束后箱内最大累计预算使用
    率"最低的点,平局取较低温度。箱内没有填写度时预算的批次时,退化为原规则
    (最宽可行区间的中点)。锁温组内各段依次按共享设定点累计暴露。
    """
    budgeted = [lot for lot in lots if lot.budget is not None]
    pref_lots = [lot for lot in lots if lot.pref is not None]
    cumulative: dict[int, float] = {lot.no: 0.0 for lot in pref_lots}
    first_exceeded: dict[int, int] = {}
    setpoints: list[float | None] = [None] * len(segments)

    for _group, idxs in _lock_units(segments):
        feas_set = _unit_feasible(segments, idxs, feasible)
        if not feas_set:
            # 理论上不可达(装箱前已逐段/逐组校验),仅作兜底
            continue
        unit_hours = sum(segments[i].hours for i in idxs)
        if not budgeted:
            widest = max(feas_set, key=lambda s: s.hi - s.lo)
            t = (widest.lo + widest.hi) / 2
        else:
            cands: set[float] = set()
            for s in feas_set:
                cands.update((s.lo, s.hi))
            for lot in pref_lots:
                assert lot.pref is not None
                for ep in (lot.pref.lo, lot.pref.hi):
                    if any(s.lo - EPS <= ep <= s.hi + EPS for s in feas_set):
                        cands.add(ep)
            best_t: float | None = None
            best_obj = float("inf")
            for cand in sorted(cands):
                obj = max(
                    _usage_ratio(
                        cumulative[lot.no]
                        + unit_hours * _dist_to_iv(cand, lot.pref),
                        lot.budget if lot.budget is not None else 0.0,
                    )
                    for lot in budgeted
                )
                if best_t is None or obj < best_obj - EPS:
                    best_obj = obj
                    best_t = cand
            t = best_t
        # 同一单元(锁温组)内各段共用该设定点,逐段累计热暴露
        for i in idxs:
            setpoints[i] = t
            seg = segments[i]
            for lot in pref_lots:
                assert lot.pref is not None
                cumulative[lot.no] += seg.hours * _dist_to_iv(t, lot.pref)
            for lot in budgeted:
                if (
                    lot.budget is not None
                    and lot.no not in first_exceeded
                    and cumulative[lot.no] > lot.budget + EPS
                ):
                    first_exceeded[lot.no] = seg.no

    return {
        "setpoints": setpoints,
        "exposures": cumulative,
        "first_exceeded": first_exceeded,
        "ok": not first_exceeded,
    }


def _parse_zones(raw_zones: list[dict[str, Any]], ctx: str) -> list[_Zone]:
    """解析温区列表;ctx 为错误消息前缀(如所属运输段)。"""
    zones: list[_Zone] = []
    for i, z in enumerate(raw_zones):
        lo, hi = float(z["t_min"]), float(z["t_max"])
        name = str(z.get("name") or f"温区 {i + 1}")
        if lo > hi + EPS:
            raise ValueError(f"{ctx}温区 {name} 的温度下限高于上限")
        zones.append(_Zone(name=name, iv=_Iv(lo, hi)))
    return zones


def plan(payload: dict[str, Any]) -> dict[str, Any]:
    """根据请求 payload 生成分箱方案;输入不合法时抛 ValueError。"""
    box_capacity = float(payload.get("box_capacity", 0))
    max_boxes = int(payload.get("max_boxes", 100))
    volume_unit = str(payload.get("volume_unit") or "体积单位").strip() or "体积单位"

    if box_capacity <= 0:
        raise ValueError("箱体容量必须大于 0")
    if max_boxes <= 0:
        raise ValueError("最大箱数必须大于 0")

    th_raw = payload.get("transport_hours")
    raw_segments = payload.get("segments") or []
    segment_mode = bool(raw_segments)

    segments: list[_Segment] = []
    if segment_mode:
        # 分段运输:总时长为各段时长之和;显式给的 transport_hours 必须一致
        total_hours = 0.0
        for i, s in enumerate(raw_segments):
            name = str(s.get("name") or f"第 {i + 1} 段").strip() or f"第 {i + 1} 段"
            hours = float(s["hours"])
            if hours <= 0:
                raise ValueError(f"运输段 {name} 的时长必须大于 0")
            zones = _parse_zones(s.get("zones") or [], f"运输段 {name} 的")
            if not zones:
                raise ValueError(f"运输段 {name} 至少需要定义一个可用温区")
            # 锁温组:相邻且同组的段共用一个设定点;留空表示不参与
            lock_group = str(s.get("lock_group") or "").strip() or None
            segments.append(
                _Segment(
                    no=i + 1,
                    name=name,
                    hours=hours,
                    zones=zones,
                    lock_group=lock_group,
                )
            )
            total_hours += hours
        total_hours = _r(total_hours)
        if th_raw not in (None, ""):
            th = float(th_raw)
            if th < 0:
                raise ValueError("运输时长不能为负数")
            if abs(th - total_hours) > 1e-6:
                raise ValueError(
                    f"运输时长 {_r(th)}h 与各运输段时长之和 {_r(total_hours)}h "
                    f"不一致,请修正,或留空自动按段时长求和"
                )
        transport_hours = total_hours
    else:
        # 兼容模式:全程单一温区,视为唯一运输段
        transport_hours = 0.0 if th_raw in (None, "") else float(th_raw)
        if transport_hours < 0:
            raise ValueError("运输时长不能为负数")
        zones = _parse_zones(payload.get("zones") or [], "")
        if not zones:
            raise ValueError("至少需要定义一个允许温区")
        segments.append(
            _Segment(no=1, name="全程", hours=transport_hours, zones=zones)
        )

    batches: list[_Batch] = []
    for i, c in enumerate(payload.get("cargoes") or []):
        name = str(c.get("name") or f"批次 {i + 1}")
        lo, hi = float(c["t_min"]), float(c["t_max"])
        if lo > hi + EPS:
            raise ValueError(f"货物 {name} 的允许温度下限高于上限")
        units = int(c["units"])
        unit_volume = float(c["unit_volume"])
        if units <= 0:
            raise ValueError(f"货物 {name} 的件数必须大于 0")
        if unit_volume <= 0:
            raise ValueError(f"货物 {name} 的单件体积必须大于 0")
        mh = c.get("max_transport_hours")
        max_hours = None if mh in (None, "", 0, "0") else float(mh)
        if max_hours is not None and max_hours < 0:
            raise ValueError(f"货物 {name} 的最长运输时长不能为负数")
        # 首选温度区间(与度时预算配合使用);下限/上限需同时填写
        p_lo_raw, p_hi_raw = c.get("preferred_t_min"), c.get("preferred_t_max")
        pref: _Iv | None = None
        if (p_lo_raw in (None, "")) != (p_hi_raw in (None, "")):
            raise ValueError(f"货物 {name} 的首选温度区间需同时填写下限和上限")
        if p_lo_raw not in (None, ""):
            p_lo, p_hi = float(p_lo_raw), float(p_hi_raw)
            if p_lo > p_hi + EPS:
                raise ValueError(f"货物 {name} 的首选温度下限高于上限")
            pref = _Iv(p_lo, p_hi)
        # 最大度时预算(℃·h);留空表示不限,该批保持原规则
        mb = c.get("max_degree_hours")
        budget = None if mb in (None, "") else float(mb)
        if budget is not None:
            if budget < 0:
                raise ValueError(f"货物 {name} 的度时预算不能为负数")
            if pref is None:
                raise ValueError(
                    f"货物 {name} 填写了度时预算,请同时填写首选温度区间"
                )
        batches.append(
            _Batch(
                no=i + 1,
                name=name,
                units=units,
                unit_volume=unit_volume,
                iv=_Iv(lo, hi),
                max_hours=max_hours,
                pref=pref,
                budget=budget,
                total_volume=_r(units * unit_volume),
            )
        )
    if not batches:
        raise ValueError("至少需要录入一批货物")

    boxes: list[_Box] = []
    unloaded: list[dict[str, Any]] = []

    def add_unloaded(batch: _Batch, units: int, reason: str, **extra: Any) -> None:
        unloaded.append(
            {
                "batch_no": batch.no,
                "name": batch.name,
                "units": units,
                "volume": _r(units * batch.unit_volume),
                "reason": reason,
                **extra,
            }
        )

    # 温度下限越低越苛刻,优先装;同温限按总体积降序
    for b in sorted(batches, key=lambda x: (x.iv.lo, -x.total_volume)):
        # 1) 运输时长预检
        if b.max_hours is not None and transport_hours > b.max_hours + EPS:
            add_unloaded(
                b,
                b.units,
                f"运输时长 {_r(transport_hours)}h 超过该批允许的 "
                f"{_r(b.max_hours)}h,全程温度无法保证",
            )
            continue

        # 2) 逐段温区兼容性预检:每一段都必须有温区覆盖该批允许温区
        failed_seg = _first_failed_segment(segments, b.iv)
        if failed_seg is not None:
            if segment_mode:
                add_unloaded(
                    b,
                    b.units,
                    f"运输段「{failed_seg.name}」(第 {failed_seg.no} 段,"
                    f"{_r(failed_seg.hours)}h)的可用温区 "
                    f"({_fmt_names([z.name for z in failed_seg.zones])}) 与允许温区 "
                    f"[{_r(b.iv.lo)}, {_r(b.iv.hi)}]℃ 均无交集,全程温度无法保证",
                    failed_segment_no=failed_seg.no,
                    failed_segment_name=failed_seg.name,
                )
            else:
                add_unloaded(
                    b,
                    b.units,
                    f"允许温区 [{_r(b.iv.lo)}, {_r(b.iv.hi)}]℃ 与全部可用温区 "
                    f"({_fmt_names([z.name for z in segments[0].zones])}) 均无交集",
                )
            continue

        # 3) 锁温组预检:相邻同组段必须存在共享设定点;单批即不可行时,
        #    可行交集只会随拼箱收窄,任何装箱组合均不可用
        failed_lock = _first_failed_lock_run(segments, b.iv)
        if failed_lock is not None:
            group_name, run_segs = failed_lock
            seg_desc = "、".join(f"第 {s.no} 段「{s.name}」" for s in run_segs)
            add_unloaded(
                b,
                b.units,
                f"锁温组「{group_name}」({seg_desc})要求共用一个设定点,"
                f"但允许温区 [{_r(b.iv.lo)}, {_r(b.iv.hi)}]℃ 在各段可行温度段的"
                f"交集为空,不存在共享设定点,任何装箱组合均不可用",
                failed_lock_group=group_name,
                failed_lock_segments=[
                    {"segment_no": s.no, "name": s.name} for s in run_segs
                ],
            )
            continue

        # 4) 先尝试已有箱子(First-Fit)
        remaining = b.units
        unusable_boxes = 0
        budget_blocked = False
        lock_blocked = False
        for box in boxes:
            if remaining == 0:
                break
            if not box.can_take(b.iv):
                unusable_boxes += 1
                if box.last_fail == "lock":
                    lock_blocked = True
                continue
            while remaining > 0 and box.try_load(b, b.iv):
                remaining -= 1
            if remaining > 0:
                # 温区兼容但本箱容量已不足、锁温组无共享设定点或热暴露预算超限
                unusable_boxes += 1
                if box.last_fail == "budget":
                    budget_blocked = True
                if box.last_fail == "lock":
                    lock_blocked = True

        # 5) 开新箱继续装,直到装完或达到上限
        while remaining > 0:
            if len(boxes) >= max_boxes:
                if segment_mode:
                    compatible_note = "逐段温区均兼容"
                else:
                    covering = [
                        z.name
                        for z in segments[0].zones
                        if b.iv.intersect(z.iv) is not None
                    ]
                    compatible_note = f"温区兼容({_fmt_names(covering)})"
                if budget_blocked and lock_blocked:
                    detail = "均无兼容温度、容量不足、锁温组无共享设定点或热暴露预算超限"
                elif budget_blocked:
                    detail = "均无兼容温度、容量不足或热暴露预算超限"
                elif lock_blocked:
                    detail = "均无兼容温度、容量不足或锁温组无共享设定点"
                elif unusable_boxes:
                    detail = "均无兼容温度或容量不足"
                else:
                    detail = "容量不足"
                add_unloaded(
                    b,
                    remaining,
                    f"{compatible_note},"
                    f"但已达最大箱数 {max_boxes},现有 {len(boxes)} 个箱子{detail}",
                    tried_boxes=len(boxes),
                )
                break
            if b.unit_volume > box_capacity + EPS:
                add_unloaded(
                    b,
                    remaining,
                    f"单件体积 {_r(b.unit_volume)} {volume_unit} 超过单箱容量 "
                    f"{_r(box_capacity)} {volume_unit},新箱也无法装载",
                    new_box_failed=True,
                )
                break
            box = _Box(index=len(boxes) + 1, capacity=box_capacity, segments=segments)
            if not box.try_load(b, b.iv):
                if box.last_fail == "budget" and b.budget is not None:
                    # 单独装箱即超预算:可行区间只会随拼箱收窄,任何组合均不可用
                    sim = _simulate_heat(segments, b.iv, [b])
                    first_no = sim["first_exceeded"].get(b.no)
                    first_seg = segments[first_no - 1] if first_no else None
                    exceeded_note = (
                        f"(首次超限:第 {first_seg.no} 段「{first_seg.name}」)"
                        if first_seg
                        else ""
                    )
                    add_unloaded(
                        b,
                        remaining,
                        f"按逐段最优设定点,该批全程累计热暴露 "
                        f"{_r(sim['exposures'][b.no])} ℃·h 超过度时预算 "
                        f"{_r(b.budget)} ℃·h{exceeded_note},任何装箱组合均不可用",
                        cumulative_exposure=_r(sim["exposures"][b.no]),
                        max_degree_hours=_r(b.budget),
                        first_exceeded_segment_no=(
                            first_seg.no if first_seg else None
                        ),
                        first_exceeded_segment_name=(
                            first_seg.name if first_seg else None
                        ),
                    )
                    break
                if box.last_fail == "lock":
                    # 理论上不可达(锁温组预检已拦截单批不可行情形),仅作兜底
                    failed_lock = _first_failed_lock_run(segments, b.iv)
                    if failed_lock is not None:
                        group_name, run_segs = failed_lock
                        seg_desc = "、".join(
                            f"第 {s.no} 段「{s.name}」" for s in run_segs
                        )
                        add_unloaded(
                            b,
                            remaining,
                            f"锁温组「{group_name}」({seg_desc})要求共用一个设定点,"
                            f"但允许温区 [{_r(b.iv.lo)}, {_r(b.iv.hi)}]℃ 在各段可行"
                            f"温度段的交集为空,不存在共享设定点,任何装箱组合均不可用",
                            failed_lock_group=group_name,
                            failed_lock_segments=[
                                {"segment_no": s.no, "name": s.name}
                                for s in run_segs
                            ],
                        )
                        break
                # 理论上不可达(预检已保证逐段兼容),仅作兜底
                failed = _first_failed_segment(segments, b.iv)
                if segment_mode and failed is not None:
                    reason = (
                        f"允许温区 [{_r(b.iv.lo)}, {_r(b.iv.hi)}]℃ 与运输段"
                        f"「{failed.name}」(第 {failed.no} 段)可用温区"
                        f"({_fmt_names([z.name for z in failed.zones])})无有效温度交集"
                    )
                else:
                    reason = (
                        f"允许温区 [{_r(b.iv.lo)}, {_r(b.iv.hi)}]℃ 与新箱可用温区"
                        f"({_fmt_names([z.name for z in segments[0].zones])})"
                        f"无有效温度交集"
                    )
                add_unloaded(b, remaining, reason, new_box_failed=True)
                break
            boxes.append(box)
            remaining -= 1
            while remaining > 0 and box.try_load(b, b.iv):
                remaining -= 1

    # 6) 组装输出
    # 段下标 -> (锁温组名, 组内段号列表),用于按段标注共享设定点
    lock_members: dict[int, tuple[str, list[int]]] = {}
    for group, idxs in _lock_units(segments):
        if group is None:
            continue
        nos = [segments[i].no for i in idxs]
        for i in idxs:
            lock_members[i] = (group, nos)

    out_boxes: list[dict[str, Any]] = []
    loaded_units = 0
    loaded_volume = 0.0
    for box in boxes:
        assert box.feasible is not None
        # 逐段设定点:箱内有预算批次时按"最大累计预算使用率最低"贪心选取,
        # 否则保持原规则(最宽可行区间中点)
        heat_sim = _simulate_heat(box.segments, box.feasible, box.lots)
        setpoints = heat_sim["setpoints"]
        # 逐段校验:每段可设定温度 = 箱内货物共同交集 ∩ 该段各温区,再合并相邻段
        segment_checks: list[dict[str, Any]] = []
        envelope_raw: list[_Iv] = []
        transportable = True
        for idx, seg in enumerate(box.segments):
            merged = _merge_segments(
                [
                    s
                    for z in seg.zones
                    if (s := box.feasible.intersect(z.iv)) is not None
                ]
            )
            matched = [
                z.name
                for z in seg.zones
                if box.feasible.intersect(z.iv) is not None
            ]
            ok = bool(merged)
            lock_info = lock_members.get(idx)
            check: dict[str, Any] = {
                "segment_no": seg.no,
                "name": seg.name,
                "hours": _r(seg.hours),
                "ok": ok,
                "temperature_segments": [
                    {"t_min": _r(s.lo), "t_max": _r(s.hi)} for s in merged
                ],
                "matched_zones": matched,
                "recommended_setpoint": (
                    _r(setpoints[idx]) if setpoints[idx] is not None else None
                ),
                # 锁温组:组名、共用设定点的段号、共享设定点(未分组段为 None)
                "lock_group": lock_info[0] if lock_info else None,
                "lock_group_segments": lock_info[1] if lock_info else None,
                "shared_setpoint": (
                    _r(setpoints[idx])
                    if lock_info and setpoints[idx] is not None
                    else None
                ),
            }
            if ok:
                envelope_raw.extend(merged)
            else:
                transportable = False
                check["reason"] = (
                    f"箱内货物共同允许温度 [{_r(box.feasible.lo)}, "
                    f"{_r(box.feasible.hi)}]℃ 与该段可用温区均无交集"
                )
            segment_checks.append(check)

        # 全程温度包络(兼容字段)= 各段可设定温度段合并后的结果
        envelope = _merge_segments(envelope_raw)
        widest = max(envelope, key=lambda s: s.hi - s.lo)
        matched_all = list(
            dict.fromkeys(name for c in segment_checks for name in c["matched_zones"])
        )
        items_out = []
        for it in box.items:
            loaded_units += it.units
            loaded_volume += it.volume
            items_out.append(
                {
                    "batch_no": it.batch_no,
                    "name": it.name,
                    "units": it.units,
                    "volume": _r(it.volume),
                }
            )
        # 各批热暴露核算:仅填写了首选区间/度时预算的批次有对应数值
        lots_by_no = {lot.no: lot for lot in box.lots}
        seg_names = {s.no: s.name for s in box.segments}
        heat_exposure: list[dict[str, Any]] = []
        for it in box.items:
            lot = lots_by_no[it.batch_no]
            exposure = heat_sim["exposures"].get(lot.no)
            first_no = heat_sim["first_exceeded"].get(lot.no)
            heat_exposure.append(
                {
                    "batch_no": lot.no,
                    "name": lot.name,
                    "preferred_t_min": _r(lot.pref.lo) if lot.pref else None,
                    "preferred_t_max": _r(lot.pref.hi) if lot.pref else None,
                    "max_degree_hours": (
                        _r(lot.budget) if lot.budget is not None else None
                    ),
                    "cumulative_exposure": (
                        _r(exposure) if exposure is not None else None
                    ),
                    "remaining_budget": (
                        _r(lot.budget - exposure)
                        if lot.budget is not None and exposure is not None
                        else None
                    ),
                    "usage_ratio": (
                        _r(_usage_ratio(exposure, lot.budget))
                        if lot.budget is not None and exposure is not None
                        else None
                    ),
                    "exceeded": (
                        lot.no in heat_sim["first_exceeded"]
                        if lot.budget is not None
                        else None
                    ),
                    "first_exceeded_segment_no": first_no,
                    "first_exceeded_segment_name": (
                        seg_names.get(first_no) if first_no else None
                    ),
                }
            )
        out_boxes.append(
            {
                "box_no": box.index,
                "temperature_range": {
                    "t_min": _r(envelope[0].lo),
                    "t_max": _r(envelope[-1].hi),
                },
                "temperature_segments": [
                    {"t_min": _r(s.lo), "t_max": _r(s.hi)} for s in envelope
                ],
                "recommended_setpoint": _r((widest.lo + widest.hi) / 2),
                "matched_zones": matched_all,
                "transportable": transportable,
                "segment_checks": segment_checks,
                "heat_exposure": heat_exposure,
                "heat_exposure_ok": heat_sim["ok"],
                "used_volume": _r(box.used),
                "capacity": _r(box.capacity),
                "utilization": _r(min(1.0, box.used / box.capacity)),
                "items": items_out,
            }
        )

    total_units = sum(c.units for c in batches)
    total_volume = sum(c.total_volume for c in batches)
    unloaded_units = sum(u["units"] for u in unloaded)
    all_transportable = unloaded_units == 0 and all(
        b["transportable"] for b in out_boxes
    )

    return {
        "summary": {
            "total_cargo_units": total_units,
            "loaded_units": loaded_units,
            "unloaded_units": unloaded_units,
            "total_cargo_volume": _r(total_volume),
            "loaded_volume": _r(loaded_volume),
            "unloaded_volume": _r(total_volume - loaded_volume),
            "boxes_used": len(boxes),
            "max_boxes": max_boxes,
            "all_loaded": unloaded_units == 0,
            "transport_hours": _r(transport_hours),
            "segment_mode": segment_mode,
            "all_transportable": all_transportable,
        },
        "segments": [
            {
                "segment_no": seg.no,
                "name": seg.name,
                "hours": _r(seg.hours),
                "lock_group": seg.lock_group,
                "zones": [
                    {"name": z.name, "t_min": _r(z.iv.lo), "t_max": _r(z.iv.hi)}
                    for z in seg.zones
                ],
            }
            for seg in segments
        ],
        "boxes": out_boxes,
        "unloaded": unloaded,
        "volume_unit": volume_unit,
    }
