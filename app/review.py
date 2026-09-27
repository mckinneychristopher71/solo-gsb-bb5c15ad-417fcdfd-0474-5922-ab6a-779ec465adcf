"""实测温度复核。

基于生成方案时的输入快照(plan_input,与 /api/plan 请求体相同)重新规划并
定位指定箱子,以上传的实测温度 CSV(含 time_seconds、temperature 两列)
按该箱装载批次逐批核算:

- CSV 校验:时间从 0 严格递增至全程秒数(运输时长 × 3600),至少两条记录,
  时间与温度均须为有限数值;任何非法记录整单拒绝(由入口统一转为 400)。
- 测温仪器绝对误差 instrument_error(℃,非负,默认 0):每时刻的真实温度
  视为实测值 ± 误差构成的闭区间(误差带),相邻采样点间误差带端点同样按
  线性变化。
- 确定超界:误差带整体越出允许温区(下界仍高于上限,或上界仍低于下限);
  可能超界:误差带只要接触或跨越界外(上界高于上限,或下界低于下限);
  恰在边界(相等)不算超界,两类均在误差带端点穿越边界处切段,
  仅在边界点接触的零时长段不输出。
- 度时界:对已设首选温度区间的批次,每时刻取误差带到首选区间的最近距离
  (度时下界)与最远距离(上界),在距离公式改变处切段后梯形精确积分并
  除以 3600;实测度时仍按实测值本身积分。下界严格大于预算判确定超限,
  上界不大于预算判确定未超限,否则判可能超限;未设预算不判。
- instrument_error 为 0 时误差带退化为实测温度曲线,结果与原复核一致。

响应携带输入快照指纹(SHA-256),输入变化后前端据此将旧复核结果标记为已过期。
"""
from __future__ import annotations

import csv
import io
import math
from typing import Any

from .planner import EPS, _r, input_fingerprint, plan

# 末条记录时间与全程秒数的容许相对误差(吸收运输时长 4 位小数规整的换算误差)
_TIME_TOL = 1e-6


def _dist(t: float, lo: float, hi: float) -> float:
    """温度 t 到闭区间 [lo, hi] 的距离(区间内为 0)。"""
    if t < lo:
        return lo - t
    if t > hi:
        return t - hi
    return 0.0


def _parse_csv(csv_text: str, expected_seconds: float) -> list[tuple[float, float]]:
    """解析并校验实测温度 CSV,返回 [(秒, 温度), ...];非法记录抛 ValueError。"""
    if not csv_text or not csv_text.strip():
        raise ValueError(
            "实测温度 CSV 为空,请上传含 time_seconds,temperature 两列的文件"
        )
    reader = csv.reader(io.StringIO(csv_text.lstrip("\ufeff")))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(
            "实测温度 CSV 为空,请上传含 time_seconds,temperature 两列的文件"
        )
    header = [h.strip().lower() for h in rows[0]]
    if "time_seconds" not in header or "temperature" not in header:
        raise ValueError("CSV 首行须为表头,且包含 time_seconds,temperature 两列")
    ti, vi = header.index("time_seconds"), header.index("temperature")

    samples: list[tuple[float, float]] = []
    for line_no, row in enumerate(rows[1:], start=2):
        if max(ti, vi) >= len(row):
            raise ValueError(
                f"第 {line_no} 行缺少 time_seconds 或 temperature 列,已拒绝"
            )
        raw_t, raw_v = row[ti].strip(), row[vi].strip()
        try:
            t, temp = float(raw_t), float(raw_v)
        except ValueError:
            raise ValueError(
                f"第 {line_no} 行存在非法数值(time_seconds={raw_t!r}, "
                f"temperature={raw_v!r}),已拒绝"
            ) from None
        if not (math.isfinite(t) and math.isfinite(temp)):
            raise ValueError(
                f"第 {line_no} 行的数值须为有限值(time_seconds={raw_t!r}, "
                f"temperature={raw_v!r}),已拒绝"
            )
        samples.append((t, temp))

    if len(samples) < 2:
        raise ValueError("实测温度记录至少需要两条(覆盖全程起点与终点)")
    if abs(samples[0][0]) > 1e-9:
        raise ValueError(
            f"时间必须从 0 秒开始,首条记录为 {_r(samples[0][0])} 秒,已拒绝"
        )
    for i, ((ta, _), (tb, _)) in enumerate(zip(samples, samples[1:])):
        if tb <= ta:
            raise ValueError(
                f"时间必须严格递增:第 {i + 2} 条记录 {_r(tb)} 秒未大于"
                f"前一条 {_r(ta)} 秒,已拒绝"
            )
    last = samples[-1][0]
    if abs(last - expected_seconds) > _TIME_TOL * max(1.0, expected_seconds):
        raise ValueError(
            f"末条记录时间须等于全程秒数 {_r(expected_seconds)} 秒"
            f"(运输时长 × 3600),实际为 {_r(last)} 秒,已拒绝"
        )
    return samples


def _densify(
    samples: list[tuple[float, float]], bounds: tuple[float, ...]
) -> list[tuple[float, float]]:
    """在温度线性穿越任一边界值处插入采样点,返回稠密点列。

    插入后每个小区间内温度线性且不再穿越任何边界,超界判定与距离函数
    在区间内都是线性的,可精确切段与积分。
    """
    pts = [samples[0]]
    for (t0, T0), (t1, T1) in zip(samples, samples[1:]):
        mids: list[tuple[float, float]] = []
        if T0 != T1:
            for b in bounds:
                u = (b - T0) / (T1 - T0)
                if EPS < u < 1.0 - EPS:
                    mids.append((t0 + u * (t1 - t0), b))
        mids.sort()
        pts.extend(mids)
        pts.append((t1, T1))
    return pts


def _interval_dict(run: list[float], err: float) -> dict[str, Any]:
    """把连续超界段状态 [起点, 终点, 实测最低, 实测最高, 触下界, 触上界] 转输出。"""
    s, e, mn, mx, low_side, high_side = run
    return {
        "start_s": _r(s),
        "end_s": _r(e),
        "duration_s": _r(e - s),
        "min_temperature": _r(mn),
        "max_temperature": _r(mx),
        # 该段内误差带(真实温度可能范围)的极值
        "band_min_temperature": _r(mn - err),
        "band_max_temperature": _r(mx + err),
        "side": (
            "both"
            if low_side and high_side
            else "low"
            if low_side
            else "high"
        ),
    }


def _excursion_bands(
    samples: list[tuple[float, float]], lo: float, hi: float, err: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按误差带求 (确定超界区间, 可能超界区间)。

    实测温度 T(t) 线性变化,真实温度误差带为 [T-err, T+err](端点同样线性):

    - 确定超界:误差带整体越界——T-err > hi(必超上限)或 T+err < lo(必超下限);
    - 可能超界:误差带接触或跨越界外——T+err > hi(可能超上限)或
      T-err < lo(可能超下限);
    - 恰在边界(相等)不算超界。

    在 T 穿越 lo±err、hi±err 处切段后,各谓词在小区间内恒定;严格成立的
    相邻小区间合并为一个连续区间(仅经过边界点不隔断),零时长的点接触
    不产生区间。可能超界集合包含确定超界集合。
    """
    if err > EPS:
        bounds = tuple(sorted({lo - err, lo + err, hi - err, hi + err}))
    else:
        # 误差为 0 时退化为原复核:只在允许温区边界处切段
        bounds = (lo, hi)
    pts = _densify(samples, bounds)

    cert_out: list[list[float]] = []
    poss_out: list[list[float]] = []
    cert_run: list[float] | None = None
    poss_run: list[float] | None = None

    def accumulate(
        run: list[float] | None,
        flagged: bool,
        out: list[list[float]],
        seg_lo: bool,
        seg_hi: bool,
        t0: float,
        t1: float,
        T0: float,
        T1: float,
    ) -> list[float] | None:
        if flagged:
            seg_lo_t, seg_hi_t = min(T0, T1), max(T0, T1)
            if run is None:
                return [t0, t1, seg_lo_t, seg_hi_t, seg_lo, seg_hi]
            run[1] = t1
            run[2] = min(run[2], seg_lo_t)
            run[3] = max(run[3], seg_hi_t)
            run[4] = run[4] or seg_lo
            run[5] = run[5] or seg_hi
            return run
        if run is not None:
            out.append(run)
        return None

    for (t0, T0), (t1, T1) in zip(pts, pts[1:]):
        Tm = (T0 + T1) / 2.0
        band_lo, band_hi = Tm - err, Tm + err
        c_low = band_hi < lo  # 误差带整体低于下限
        c_high = band_lo > hi  # 误差带整体高于上限
        p_low = band_lo < lo  # 误差带跨入下限外侧
        p_high = band_hi > hi  # 误差带跨入上限外侧
        cert_run = accumulate(
            cert_run, c_low or c_high, cert_out, c_low, c_high, t0, t1, T0, T1
        )
        poss_run = accumulate(
            poss_run, p_low or p_high, poss_out, p_low, p_high, t0, t1, T0, T1
        )
    if cert_run is not None:
        cert_out.append(cert_run)
    if poss_run is not None:
        poss_out.append(poss_run)

    return (
        [_interval_dict(r, err) for r in cert_out],
        [_interval_dict(r, err) for r in poss_out],
    )


def _band_dists(
    T: float, err: float, lo: float, hi: float
) -> tuple[float, float]:
    """实测温度 T(误差 ±err)时,误差带到首选区间 [lo, hi] 的(最近, 最远)距离。

    到凸区间的距离为凸函数:误差带 [T-err, T+err] 与区间相交时最近距离为 0,
    最远距离取误差带两端点距离的较大者;整体位于区间一侧时两端点距离即为
    最远/最近。恰在边界(误差带端点等于区间端点)距离为 0。
    """
    band_lo, band_hi = T - err, T + err
    if band_hi <= lo:  # 误差带整体不高于下限(含恰在下限)
        return lo - band_hi, lo - band_lo
    if band_lo >= hi:  # 误差带整体不低于上限(含恰在上限)
        return band_lo - hi, band_hi - hi
    # 误差带与首选区间相交:最近为 0,最远在端点处取得
    return 0.0, max(_dist(band_lo, lo, hi), _dist(band_hi, lo, hi))


def _degree_hour_bounds(
    samples: list[tuple[float, float]],
    pref_lo: float,
    pref_hi: float,
    err: float,
) -> tuple[float, float, float]:
    """返回 (实测度时, 度时下界, 度时上界),单位 ℃·h(秒积分 ÷ 3600)。

    下界/上界分别为误差带到首选区间最近/最远距离对时间的积分。距离函数
    的斜率只可能在 T 穿越 pref_lo±err、pref_hi±err 以及两端点距离相等处
    (T = 首选区间中点)改变,统一在这些温度处切段,每小段内距离线性,
    梯形求和为精确积分。实测度时按实测值本身到首选区间距离积分。
    """
    kink_bounds = {
        pref_lo,
        pref_hi,
        pref_lo - err,
        pref_lo + err,
        pref_hi - err,
        pref_hi + err,
        (pref_lo + pref_hi) / 2.0,
    }
    pts = _densify(samples, tuple(sorted(kink_bounds)))
    measured_s = near_s = far_s = 0.0
    for (t0, T0), (t1, T1) in zip(pts, pts[1:]):
        dt = t1 - t0
        measured_s += (
            (_dist(T0, pref_lo, pref_hi) + _dist(T1, pref_lo, pref_hi)) / 2.0 * dt
        )
        n0, f0 = _band_dists(T0, err, pref_lo, pref_hi)
        n1, f1 = _band_dists(T1, err, pref_lo, pref_hi)
        near_s += (n0 + n1) / 2.0 * dt
        far_s += (f0 + f1) / 2.0 * dt
    return measured_s / 3600.0, near_s / 3600.0, far_s / 3600.0


def _parse_error(raw: Any) -> float:
    """解析测温仪器绝对误差(℃,非负,默认 0);非法抛 ValueError。"""
    if raw is None or raw == "":
        return 0.0
    try:
        err = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"测温仪器绝对误差须为非负数值(℃),实际为 {raw!r},已拒绝"
        ) from None
    if not math.isfinite(err) or err < 0:
        raise ValueError(
            f"测温仪器绝对误差须为非负有限值(℃),实际为 {raw!r},已拒绝"
        )
    return err


def review(payload: dict[str, Any]) -> dict[str, Any]:
    """实测温度复核入口;输入不合法时抛 ValueError(与 plan 一致)。"""
    plan_input = payload.get("plan_input") or {}
    box_no = payload.get("box_no")
    csv_text = payload.get("csv") or ""
    err = _parse_error(payload.get("instrument_error", 0.0))
    if box_no is None:
        raise ValueError("请指定要复核的箱号 box_no")
    box_no = int(box_no)

    # 复核绑定生成方案时的输入快照:按快照重新规划并定位箱子
    result = plan(plan_input)
    transport_hours = float(result["summary"]["transport_hours"])
    expected_s = transport_hours * 3600.0
    if expected_s <= 0:
        raise ValueError("运输时长为 0,无法进行实测温度复核")

    boxes = result["boxes"]
    box = next((b for b in boxes if b["box_no"] == box_no), None)
    if box is None:
        raise ValueError(
            f"方案中不存在第 {box_no} 箱(本次方案共 {len(boxes)} 箱),"
            f"请重新生成方案后选择有效箱号"
        )

    samples = _parse_csv(csv_text, expected_s)

    cargoes = plan_input.get("cargoes") or []
    # 箱内装载批次(按批号去重并汇总件数)
    units_by_no: dict[int, int] = {}
    for it in box["items"]:
        units_by_no[it["batch_no"]] = units_by_no.get(it["batch_no"], 0) + it["units"]

    batches_out: list[dict[str, Any]] = []
    for no in sorted(units_by_no):
        c = cargoes[no - 1]
        name = str(c.get("name") or f"批次 {no}")
        lo, hi = float(c["t_min"]), float(c["t_max"])
        p_lo, p_hi = c.get("preferred_t_min"), c.get("preferred_t_max")
        has_pref = p_lo not in (None, "") and p_hi not in (None, "")
        mb = c.get("max_degree_hours")
        budget = None if mb in (None, "") else float(mb)

        certain, possible = _excursion_bands(samples, lo, hi, err)
        measured = lower = upper = None
        if has_pref:
            measured, lower, upper = _degree_hour_bounds(
                samples, float(p_lo), float(p_hi), err
            )

        verdict: str | None = None
        remaining: float | None = None
        remaining_lo: float | None = None
        remaining_hi: float | None = None
        exceeded: bool | None = None
        if budget is not None and lower is not None and upper is not None:
            # 严格大于预算才判超限:
            # 度时下界也大于预算 → 确定超限;上界不大于预算 → 确定未超限;
            # 其余(预算落在 [下界, 上界] 内)→ 可能超限。
            if lower > budget + EPS:
                verdict = "certain_exceeded"
            elif upper <= budget + EPS:
                verdict = "within"
            else:
                verdict = "possible_exceeded"
            exceeded = verdict == "certain_exceeded"
            remaining = budget - float(measured)
            remaining_lo = budget - upper  # 最不利情况下剩余(可为负)
            remaining_hi = budget - lower  # 最有利情况下剩余

        batches_out.append(
            {
                "batch_no": no,
                "name": name,
                "units": units_by_no[no],
                "allowed_t_min": _r(lo),
                "allowed_t_max": _r(hi),
                "within_limits": not certain,
                "certainly_within_limits": not possible,
                "excursion_count": len(certain),
                "possible_excursion_count": len(possible),
                "excursions": certain,
                "possible_excursions": possible,
                "preferred_t_min": _r(float(p_lo)) if has_pref else None,
                "preferred_t_max": _r(float(p_hi)) if has_pref else None,
                "measured_degree_hours": (
                    _r(measured) if measured is not None else None
                ),
                "degree_hours_lower": _r(lower) if lower is not None else None,
                "degree_hours_upper": _r(upper) if upper is not None else None,
                "max_degree_hours": _r(budget) if budget is not None else None,
                "remaining_budget": (
                    _r(remaining) if remaining is not None else None
                ),
                "remaining_budget_lower": (
                    _r(remaining_lo) if remaining_lo is not None else None
                ),
                "remaining_budget_upper": (
                    _r(remaining_hi) if remaining_hi is not None else None
                ),
                "budget_exceeded": exceeded,
                "budget_verdict": verdict,
            }
        )

    return {
        "input_fingerprint": input_fingerprint(plan_input),
        "box_no": box_no,
        "transport_hours": _r(transport_hours),
        "duration_seconds": _r(expected_s),
        "sample_count": len(samples),
        "instrument_error": _r(err),
        "batches": batches_out,
        "summary": {
            "batch_count": len(batches_out),
            "batches_with_excursions": sum(
                1 for b in batches_out if b["excursion_count"]
            ),
            "total_excursions": sum(b["excursion_count"] for b in batches_out),
            "batches_with_certain_excursions": sum(
                1 for b in batches_out if b["excursion_count"]
            ),
            "batches_with_possible_excursions": sum(
                1 for b in batches_out if b["possible_excursion_count"]
            ),
            "total_certain_excursions": sum(
                b["excursion_count"] for b in batches_out
            ),
            "total_possible_excursions": sum(
                b["possible_excursion_count"] for b in batches_out
            ),
            "budget_checked_count": sum(
                1 for b in batches_out if b["budget_verdict"] is not None
            ),
            "budget_exceeded_count": sum(
                1 for b in batches_out if b["budget_verdict"] == "certain_exceeded"
            ),
            "budget_certain_exceeded_count": sum(
                1 for b in batches_out if b["budget_verdict"] == "certain_exceeded"
            ),
            "budget_possible_exceeded_count": sum(
                1 for b in batches_out if b["budget_verdict"] == "possible_exceeded"
            ),
            "all_within_limits": all(b["within_limits"] for b in batches_out),
            "all_certainly_within_limits": all(
                b["certainly_within_limits"] for b in batches_out
            ),
        },
    }
