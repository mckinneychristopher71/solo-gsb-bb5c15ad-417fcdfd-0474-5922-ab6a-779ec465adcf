/* 冷链装箱温控规划器前端逻辑(无第三方依赖) */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------------- 示例数据(分段运输) ---------------- */
const SAMPLE = {
  transport_hours: 24,
  box_capacity: 12,
  max_boxes: 20,
  volume_unit: "m³",
  use_segments: true,
  // 单一温区模式(未勾选分段时使用)
  zones: [
    { name: "深冷区", t_min: -30, t_max: -18 },
    { name: "冷冻区", t_min: -18, t_max: -2 },
    { name: "冷藏区", t_min: 0, t_max: 8 },
    { name: "常温区", t_min: 15, t_max: 25 },
  ],
  // 分段运输:各段时长之和 14 + 6 + 4 = 24h;干线与支线同锁温组,共用设定点
  segments: [
    {
      name: "干线冷链",
      hours: 14,
      lock_group: "干线锁温",
      zones: [
        { name: "深冷区", t_min: -30, t_max: -18 },
        { name: "冷冻区", t_min: -18, t_max: -2 },
        { name: "冷藏区", t_min: 0, t_max: 8 },
      ],
    },
    {
      name: "支线转运",
      hours: 6,
      lock_group: "干线锁温",
      zones: [
        { name: "冷冻区", t_min: -18, t_max: -2 },
        { name: "冷藏区", t_min: 0, t_max: 8 },
      ],
    },
    {
      name: "末端配送",
      hours: 4,
      zones: [
        { name: "冷藏区", t_min: 0, t_max: 8 },
        { name: "常温区", t_min: 15, t_max: 25 },
      ],
    },
  ],
  cargoes: [
    { name: "三文鱼刺身", units: 18, unit_volume: 0.4, t_min: -2, t_max: 2, preferred_t_min: -1, preferred_t_max: 1, max_degree_hours: 30, max_transport_hours: 36 },
    { name: "巴氏鲜奶", units: 40, unit_volume: 0.25, t_min: 2, t_max: 6, preferred_t_min: 3, preferred_t_max: 5, max_degree_hours: 20, max_transport_hours: "" },
    { name: "冷冻牛排", units: 30, unit_volume: 0.8, t_min: -22, t_max: -15, max_transport_hours: "" },
    { name: "冰淇淋", units: 24, unit_volume: 0.5, t_min: -28, t_max: -18, max_transport_hours: 48 },
    { name: "冷冻饺子", units: 26, unit_volume: 0.6, t_min: -18, t_max: -5, max_transport_hours: "" },
    { name: "鲜切水果", units: 20, unit_volume: 0.3, t_min: 2, t_max: 8, preferred_t_min: 3, preferred_t_max: 6, max_degree_hours: 15, max_transport_hours: "" },
    { name: "熟成奶酪(预算不足)", units: 6, unit_volume: 0.5, t_min: 2, t_max: 18, preferred_t_min: 12, preferred_t_max: 16, max_degree_hours: 40, max_transport_hours: "" },
    { name: "温控疫苗", units: 12, unit_volume: 0.15, t_min: 2, t_max: 8, max_transport_hours: 12 },
    { name: "巧克力", units: 30, unit_volume: 0.2, t_min: 15, t_max: 22, max_transport_hours: "" },
    { name: "热带水果(温区缺失)", units: 10, unit_volume: 0.4, t_min: 10, t_max: 14, max_transport_hours: "" },
  ],
};

/* ---------------- 动态行 ---------------- */
function addRow(tbody, cells) {
  const tr = document.createElement("tr");
  cells.forEach((html) => {
    const td = document.createElement("td");
    td.innerHTML = html;
    tr.appendChild(td);
  });
  tbody.appendChild(tr);
  tr.querySelector("input")?.focus();
  return tr;
}

const zoneCell = (v = "", ph = "", type = "text") =>
  `<input type="${type}" value="${v ?? ""}" placeholder="${ph}" ${type === "number" ? 'step="any"' : ""} />`;

function addZoneRow(z = {}, tbody = $("zoneRows")) {
  addRow(tbody, [
    zoneCell(z.name, "如 冷藏区"),
    zoneCell(z.t_min ?? "", "-18", "number"),
    zoneCell(z.t_max ?? "", "8", "number"),
    '<button type="button" class="btn btn-del">删除</button>',
  ]);
}

/* ---------------- 运输段卡片 ---------------- */
function addSegmentCard(seg = {}) {
  const list = $("segmentList");
  const card = document.createElement("div");
  card.className = "segment-card";
  card.innerHTML = `
    <div class="segment-head">
      <span class="seg-no"></span>
      <input class="seg-name" type="text" value="${seg.name ?? ""}" placeholder="段名称,如 干线冷链" />
      <label class="seg-hours-label">时长 h
        <input class="seg-hours" type="number" min="0" step="0.5" value="${seg.hours ?? ""}" placeholder="如 14" />
      </label>
      <label class="seg-lock-label">锁温组
        <input class="seg-lock" type="text" value="${seg.lock_group ?? ""}" placeholder="可选" title="相邻且同组的段共用一个设定点" />
      </label>
      <button type="button" class="btn btn-del btn-del-segment">删除段</button>
    </div>
    <div class="table-wrap">
      <table class="editable">
        <thead>
          <tr><th>温区名称</th><th>下限 ℃</th><th>上限 ℃</th><th></th></tr>
        </thead>
        <tbody class="seg-zone-rows"></tbody>
      </table>
    </div>
    <button type="button" class="btn btn-small btn-add-seg-zone">+ 添加温区</button>`;
  list.appendChild(card);
  const tbody = card.querySelector(".seg-zone-rows");
  (seg.zones?.length ? seg.zones : [{}]).forEach((z) => addZoneRow(z, tbody));
  renumberSegments();
  card.querySelector(".seg-name").focus();
  return card;
}

function renumberSegments() {
  [...$("segmentList").children].forEach((card, i) => {
    card.querySelector(".seg-no").textContent = `第 ${i + 1} 段`;
  });
}

$("btnAddSegment").addEventListener("click", () => addSegmentCard());

/* ---------------- 分段开关 ---------------- */
function toggleSegmentMode() {
  const on = $("useSegments").checked;
  $("singleZoneSection").hidden = on;
  $("segmentSection").hidden = !on;
  $("transportHoursField").classList.toggle("field-disabled", on);
  $("transportHours").disabled = on;
  if (on && !$("segmentList").children.length) addSegmentCard();
}
$("useSegments").addEventListener("change", toggleSegmentMode);

/* ---------------- 货物行 ---------------- */
function addCargoRow(c = {}) {
  addRow($("cargoRows"), [
    zoneCell(c.name, "批次名称"),
    zoneCell(c.units ?? "", "件数", "number"),
    zoneCell(c.unit_volume ?? "", "单件体积", "number"),
    zoneCell(c.t_min ?? "", "下限", "number"),
    zoneCell(c.t_max ?? "", "上限", "number"),
    zoneCell(c.preferred_t_min ?? "", "首选下限", "number"),
    zoneCell(c.preferred_t_max ?? "", "首选上限", "number"),
    `<input type="number" step="any" min="0" value="${c.max_degree_hours ?? ""}" placeholder="不限" />`,
    `<input type="number" step="any" min="0" value="${c.max_transport_hours ?? ""}" placeholder="不限" />`,
    '<button type="button" class="btn btn-del">删除</button>',
  ]);
}

$("btnAddZone").addEventListener("click", () => addZoneRow());
$("btnAddCargo").addEventListener("click", () => addCargoRow());
document.addEventListener("click", (e) => {
  if (e.target.classList.contains("btn-del-segment")) {
    e.target.closest(".segment-card").remove();
    renumberSegments();
    return;
  }
  if (e.target.classList.contains("btn-add-seg-zone")) {
    const tbody = e.target.closest(".segment-card").querySelector(".seg-zone-rows");
    addZoneRow({}, tbody);
    return;
  }
  if (e.target.classList.contains("btn-del")) e.target.closest("tr").remove();
});

/* ---------------- 读取/校验输入 ---------------- */
function num(v, label, rowName) {
  if (v === "" || v === null || Number.isNaN(Number(v)))
    throw new Error(`${rowName ? `「${rowName}」的` : ""}${label}必须填写数字`);
  return Number(v);
}

function readRows(tbody, nFields, labels, rowPrefix = "") {
  const out = [];
  [...tbody.querySelectorAll("tr")].forEach((tr, i) => {
    const inputs = [...tr.querySelectorAll("input")];
    const rowName =
      nFields === 3 ? `${rowPrefix}温区 ${i + 1}` : (inputs[0].value || `批次 ${i + 1}`);
    const obj = {};
    inputs.forEach((inp, idx) => {
      if (idx >= labels.length) return;
      const key = labels[idx].key;
      if (labels[idx].numeric) obj[key] = num(inp.value, labels[idx].label, rowName);
      else obj[key] = inp.value.trim();
    });
    out.push(obj);
  });
  return out;
}

const ZONE_FIELDS = [
  { key: "name", label: "名称" },
  { key: "t_min", label: "下限", numeric: true },
  { key: "t_max", label: "上限", numeric: true },
];

function readZones(tbody, rowPrefix = "") {
  const zones = readRows(tbody, 3, ZONE_FIELDS, rowPrefix).map((z, i) => ({
    ...z,
    name: z.name || `温区 ${i + 1}`,
  }));
  for (const z of zones)
    if (z.t_min > z.t_max) throw new Error(`温区「${z.name}」下限不能高于上限`);
  return zones;
}

function collectSegments() {
  const cards = [...$("segmentList").children];
  if (!cards.length) throw new Error("请至少添加一个运输段");
  return cards.map((card, i) => {
    const name = card.querySelector(".seg-name").value.trim() || `第 ${i + 1} 段`;
    const hours = num(card.querySelector(".seg-hours").value, "时长", `运输段「${name}」`);
    if (hours <= 0) throw new Error(`运输段「${name}」的时长必须大于 0`);
    const zones = readZones(card.querySelector(".seg-zone-rows"), `「${name}」`);
    if (!zones.length) throw new Error(`运输段「${name}」请至少添加一个可用温区`);
    const lockGroup = card.querySelector(".seg-lock").value.trim();
    return { name, hours, zones, lock_group: lockGroup || null };
  });
}

function collectInput() {
  const useSeg = $("useSegments").checked;
  const zones = useSeg ? [] : readZones($("zoneRows"));
  const segments = useSeg ? collectSegments() : [];

  const cargoes = readRows($("cargoRows"), 9, [
    { key: "name", label: "名称" },
    { key: "units", label: "件数", numeric: true },
    { key: "unit_volume", label: "单件体积", numeric: true },
    { key: "t_min", label: "温度下限", numeric: true },
    { key: "t_max", label: "温度上限", numeric: true },
    { key: "preferred_t_min", label: "首选温度下限", numeric: false },
    { key: "preferred_t_max", label: "首选温度上限", numeric: false },
    { key: "max_degree_hours", label: "度时预算", numeric: false },
    { key: "max_transport_hours", label: "最长运输时长", numeric: false },
  ]).map((c, i) => ({
    ...c,
    name: c.name || `批次 ${i + 1}`,
    preferred_t_min: c.preferred_t_min === "" ? null : Number(c.preferred_t_min),
    preferred_t_max: c.preferred_t_max === "" ? null : Number(c.preferred_t_max),
    max_degree_hours: c.max_degree_hours === "" ? null : Number(c.max_degree_hours),
    max_transport_hours: c.max_transport_hours === "" ? null : Number(c.max_transport_hours),
  }));

  if (!useSeg && zones.length === 0) throw new Error("请至少添加一个允许温区");
  if (cargoes.length === 0) throw new Error("请至少添加一批货物");
  for (const c of cargoes) {
    if (c.units <= 0) throw new Error(`货物「${c.name}」件数必须大于 0`);
    if (c.unit_volume <= 0) throw new Error(`货物「${c.name}」单件体积必须大于 0`);
    if (c.t_min > c.t_max) throw new Error(`货物「${c.name}」温度下限不能高于上限`);
    if ((c.preferred_t_min === null) !== (c.preferred_t_max === null))
      throw new Error(`货物「${c.name}」的首选温度区间需同时填写下限和上限`);
    if (c.preferred_t_min !== null && c.preferred_t_min > c.preferred_t_max)
      throw new Error(`货物「${c.name}」首选温度下限不能高于上限`);
    if (c.max_degree_hours !== null) {
      if (c.max_degree_hours < 0) throw new Error(`货物「${c.name}」度时预算不能为负`);
      if (c.preferred_t_min === null)
        throw new Error(`货物「${c.name}」填写了度时预算,请同时填写首选温度区间`);
    }
    if (c.max_transport_hours !== null && c.max_transport_hours < 0)
      throw new Error(`货物「${c.name}」最长运输时长不能为负`);
  }

  return {
    // 分段模式下总时长由后端按各段时长求和
    transport_hours: useSeg ? null : num($("transportHours").value, "运输时长"),
    box_capacity: num($("boxCapacity").value, "单箱容量"),
    max_boxes: num($("maxBoxes").value, "最大箱数"),
    volume_unit: $("volumeUnit").value.trim() || "体积单位",
    zones,
    segments,
    cargoes,
  };
}

/* ---------------- 渲染结果 ---------------- */
const fmt = (v) => Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 });

const fmtSegs = (segs) =>
  segs
    .map((s) => (s.t_min === s.t_max ? `${fmt(s.t_min)}℃` : `${fmt(s.t_min)}~${fmt(s.t_max)}℃`))
    .join(" 或 ");

function renderSummary(s, unit) {
  const cls = s.all_loaded ? "ok" : "bad";
  const statusText = s.all_loaded
    ? s.segment_mode ? "✓ 全程可运输" : "✓ 全部装载"
    : "⚠ 存在冲突";
  $("summary").innerHTML = `
    <div class="stat ${cls}"><div class="k">装载状态</div>
      <div class="v"><span class="badge ${s.all_loaded ? "badge-ok" : "badge-bad"}">
        ${statusText}</span></div></div>
    <div class="stat"><div class="k">已装 / 总件数</div><div class="v">${fmt(s.loaded_units)} / ${fmt(s.total_cargo_units)}</div></div>
    <div class="stat"><div class="k">已装 / 总体积</div><div class="v">${fmt(s.loaded_volume)} / ${fmt(s.total_cargo_volume)} ${unit}</div></div>
    <div class="stat"><div class="k">使用箱数</div><div class="v">${s.boxes_used} <small class="muted">/ ${s.max_boxes}</small></div></div>
    <div class="stat"><div class="k">运输时长</div>
      <div class="v">${fmt(s.transport_hours)} h${s.segment_mode ? ' <small class="muted">分段</small>' : ""}</div></div>
    <div class="stat ${s.unloaded_units ? "bad" : "ok"}"><div class="k">未装件数</div><div class="v">${fmt(s.unloaded_units)}</div></div>
  `;
}

function renderSegmentChecks(box) {
  const rows = box.segment_checks
    .map((c) => {
      const icon = c.ok ? "✓" : "✗";
      const lockBadge = c.lock_group
        ? `<span class="lock-badge" title="相邻同组段共用设定点">🔒 ${escapeHtml(c.lock_group)}</span>`
        : "";
      const setpointText = c.lock_group
        ? `共享设定点 ${fmt(c.shared_setpoint)}℃（锁温组「${escapeHtml(c.lock_group)}」· 第 ${c.lock_group_segments.join("、")} 段共用）`
        : `设定点 ${fmt(c.recommended_setpoint)}℃`;
      const detail = c.ok
        ? `可设定 ${fmtSegs(c.temperature_segments)} · ${setpointText} · ${c.matched_zones.map(escapeHtml).join("、")}`
        : escapeHtml(c.reason || "该段无可用温区");
      return `<tr class="${c.ok ? "seg-ok" : "seg-bad"}">
        <td class="num">${icon}</td>
        <td>第 ${c.segment_no} 段 · ${escapeHtml(c.name)}${lockBadge}<small class="muted">（${fmt(c.hours)}h）</small></td>
        <td>${detail}</td>
      </tr>`;
    })
    .join("");
  return `
    <table class="seg-checks">
      <thead><tr><th></th><th>运输段逐段校验</th><th>可设定温度 / 失败原因</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

/* 各批累计热暴露与度时预算核算表(箱内无预算批次时不渲染) */
function renderHeatExposure(box) {
  const entries = box.heat_exposure || [];
  if (!entries.some((e) => e.max_degree_hours !== null)) return "";
  const rows = entries
    .map((e) => {
      const pref =
        e.preferred_t_min !== null
          ? `${fmt(e.preferred_t_min)}~${fmt(e.preferred_t_max)}℃`
          : "—";
      const show = (v) => (v !== null ? fmt(v) : "—");
      let status, bar = "";
      if (e.max_degree_hours === null) {
        status = '<span class="muted">未设预算</span>';
      } else if (e.exceeded) {
        status = `<span class="heat-text-bad">⚠ 第 ${e.first_exceeded_segment_no} 段超限</span>`;
      } else {
        status = '<span class="heat-text-ok">✓ 预算内</span>';
      }
      if (e.usage_ratio !== null) {
        const pct = Math.min(100, Math.round(e.usage_ratio * 100));
        const cls = e.exceeded ? "heat-bad" : e.usage_ratio >= 0.7 ? "heat-warn" : "heat-ok";
        bar = `<div class="heat-bar" title="使用率 ${pct}%"><i class="${cls}" style="width:${pct}%"></i></div>`;
      }
      return `<tr>
        <td>#${e.batch_no} ${escapeHtml(e.name)}</td>
        <td class="num">${pref}</td>
        <td class="num">${show(e.cumulative_exposure)}</td>
        <td class="num">${show(e.max_degree_hours)}</td>
        <td class="num">${show(e.remaining_budget)}</td>
        <td>${bar}${status}</td>
      </tr>`;
    })
    .join("");
  return `
    <table class="seg-checks heat-table">
      <thead><tr><th>热暴露核算(批次)</th><th class="num">首选区间</th>
        <th class="num">累计暴露 ℃·h</th><th class="num">预算 ℃·h</th>
        <th class="num">剩余预算 ℃·h</th><th>使用率 / 状态</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderBox(box, unit, segmentMode) {
  const segText = fmtSegs(box.temperature_segments);
  const items = box.items
    .map(
      (it) => `<tr>
        <td>#${it.batch_no} ${escapeHtml(it.name)}</td>
        <td class="num">${fmt(it.units)} 件</td>
        <td class="num">${fmt(it.volume)} ${unit}</td>
      </tr>`
    )
    .join("");
  const pct = Math.round(box.utilization * 100);
  const transportBadge = box.transportable
    ? '<span class="badge badge-ok">✓ 全程可运输</span>'
    : '<span class="badge badge-bad">⚠ 分段不兼容</span>';
  const heatNote = (box.heat_exposure || []).some((e) => e.max_degree_hours !== null)
    ? ` · 热暴露: <strong class="${box.heat_exposure_ok ? "heat-text-ok" : "heat-text-bad"}">${
        box.heat_exposure_ok ? "✓ 全部批次预算内" : "⚠ 存在超限批次"
      }</strong>`
    : "";
  const card = document.createElement("div");
  card.className = "box-card";
  card.innerHTML = `
    <div class="box-head">
      <span class="box-title">📦 第 ${box.box_no} 箱 ${segmentMode ? transportBadge : ""}</span>
      <span><span class="temp-chip">${fmt(box.temperature_range.t_min)} ~ ${fmt(box.temperature_range.t_max)} ℃</span>
      <span class="temp-segments">可设定: ${segText}</span></span>
    </div>
    <div class="meta-line">
      推荐设定温度 <strong>${fmt(box.recommended_setpoint)}℃</strong> ·
      匹配温区: ${box.matched_zones.map(escapeHtml).join("、")} ·
      容量 ${fmt(box.used_volume)} / ${fmt(box.capacity)} ${unit}（${pct}%）${heatNote}
    </div>
    <div class="cap-bar"><i style="width:${pct}%"></i></div>
    ${segmentMode ? renderSegmentChecks(box) : ""}
    ${renderHeatExposure(box)}
    <table class="items">
      <thead><tr><th>装载批次</th><th class="num">件数</th><th class="num">占用体积</th></tr></thead>
      <tbody>${items}</tbody>
    </table>`;
  $("boxes").appendChild(card);
}

function renderUnloaded(unloaded, unit) {
  const wrap = $("unloaded");
  wrap.innerHTML = "";
  if (!unloaded.length) {
    $("unloadedTitle").hidden = true;
    return;
  }
  $("unloadedTitle").hidden = false;
  unloaded.forEach((u) => {
    const segBadge = u.failed_segment_name
      ? `<span class="seg-badge">首次不兼容:第 ${u.failed_segment_no} 段 · ${escapeHtml(u.failed_segment_name)}</span>`
      : "";
    const heatBadge = u.first_exceeded_segment_name
      ? `<span class="seg-badge">首次超限:第 ${u.first_exceeded_segment_no} 段 · ${escapeHtml(u.first_exceeded_segment_name)}</span>`
      : "";
    const lockBadge = u.failed_lock_group
      ? `<span class="seg-badge">锁温组「${escapeHtml(u.failed_lock_group)}」无共享设定点:第 ${u.failed_lock_segments.map((s) => s.segment_no).join("、")} 段</span>`
      : "";
    const div = document.createElement("div");
    div.className = "unloaded-item";
    div.innerHTML = `
      <div class="u-title">⛔ #${u.batch_no} ${escapeHtml(u.name)} — 未能装载 ${fmt(u.units)} 件（${fmt(u.volume)} ${unit}）${segBadge}${heatBadge}${lockBadge}</div>
      <div class="u-reason">原因：${escapeHtml(u.reason)}</div>`;
    wrap.appendChild(div);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

/* ---------------- 提交 ---------------- */
async function runPlan() {
  const btn = $("btnPlan");
  $("errorBox").hidden = true;
  let payload;
  try {
    payload = collectInput();
  } catch (e) {
    showError(e.message);
    return;
  }
  btn.disabled = true;
  btn.textContent = "规划中…";
  try {
    const resp = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `规划失败（HTTP ${resp.status}）`);
    const newSnapshot = JSON.stringify(payload);
    if (reviewSnapshot !== null && reviewSnapshot !== newSnapshot) {
      /* 方案已随输入变化,旧复核结果作废,需按新方案重新复核 */
      reviewSnapshot = null;
      $("reviewMeta").innerHTML = "";
      $("reviewResult").innerHTML = "";
    }
    planSnapshot = newSnapshot;
    planData = data;
    renderResult(data);
    markReviewStale();
    refreshCreateOrderBtn();
  } catch (e) {
    showError(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ 生成分箱方案";
  }
}

function renderResult(data) {
  $("resultPlaceholder").hidden = true;
  $("resultView").hidden = false;
  renderSummary(data.summary, data.volume_unit);
  $("boxCount").textContent = `（共 ${data.boxes.length} 箱）`;
  $("boxes").innerHTML = "";
  data.boxes.forEach((b) => renderBox(b, data.volume_unit, data.summary.segment_mode));
  renderUnloaded(data.unloaded, data.volume_unit);
  setupReview(data);
}

function showError(msg) {
  $("resultPlaceholder").hidden = true;
  $("resultView").hidden = true;
  $("errorBox").hidden = false;
  $("errorBox").textContent = "⚠ " + msg;
}

$("btnPlan").addEventListener("click", runPlan);

/* ---------------- 单温区故障容灾评估 ---------------- */
/* 评估绑定评估时刻的输入快照;任何输入变化都会把旧结果标记为已过期 */
let assessSnapshot = null; // 评估时 collectInput() 的 JSON 串
let assessData = null; // 最近一次评估响应
let activeScenario = null; // 当前展开的场景 key

/* ---------------- 实测温度复核 ---------------- */
/* 复核绑定生成方案时的输入快照;输入变化后旧复核过期,需重新生成方案 */
let planSnapshot = null; // 最近一次成功生成方案时 collectInput() 的 JSON 串
let planData = null; // 最近一次方案响应
let reviewSnapshot = null; // 复核结果对应的方案快照

const scenarioKey = (s) => `${s.segment_no}-${s.zone_index}`;

async function runAssess() {
  const btn = $("btnAssess");
  $("errorBox").hidden = true;
  let payload;
  try {
    payload = collectInput();
  } catch (e) {
    showError(e.message);
    return;
  }
  btn.disabled = true;
  btn.textContent = "评估中…";
  try {
    const resp = await fetch("/api/zone-failure-assessment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `评估失败（HTTP ${resp.status}）`);
    assessSnapshot = JSON.stringify(payload);
    assessData = data;
    activeScenario = null;
    renderAssessment(data);
  } catch (e) {
    showError(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "🛡 容灾评估";
  }
}

/* 风险等级:不可运输 > 仍可运输但需加箱/有新增未装 > 无影响 */
function scenarioLevel(s) {
  if (!s.transportable) return "bad";
  if (s.extra_boxes > 0 || s.newly_unloaded_units > 0) return "warn";
  return "ok";
}

const LEVEL_LABEL = { ok: "✓ 无影响", warn: "⚠ 仍可运输", bad: "✗ 不可运输" };

function renderAssessment(data) {
  $("resultPlaceholder").hidden = true;
  $("assessView").hidden = false;
  $("assessStale").hidden = true;
  const b = data.baseline;
  const sm = data.summary;
  $("assessMeta").innerHTML = `
    <span class="assess-chip">基准方案: ${b.boxes_used} 箱 · 已装 ${fmt(b.loaded_units)} 件
      ${b.all_transportable ? "· ✓ 全程可运输" : "· ⚠ 基准已存在冲突"}</span>
    <span class="assess-chip">场景数: ${sm.scenario_count}</span>
    <span class="assess-chip ${sm.failed_count ? "chip-bad" : "chip-ok"}">不可运输: ${sm.failed_count}</span>
    <span class="assess-chip ${sm.max_extra_boxes > 0 ? "chip-warn" : ""}">最大额外箱数: +${fmt(sm.max_extra_boxes)}</span>
    <span class="assess-chip muted">快照 ${data.input_fingerprint.slice(0, 8)}</span>`;

  /* 风险矩阵:行 = 运输段,列 = 该段各可用温区(按段序、温区序排列) */
  const bySeg = new Map();
  data.scenarios.forEach((s) => {
    if (!bySeg.has(s.segment_no)) bySeg.set(s.segment_no, { name: s.segment_name, cells: [] });
    bySeg.get(s.segment_no).cells.push(s);
  });
  const rows = [...bySeg.entries()]
    .map(([no, seg]) => {
      seg.cells.sort((a, b2) => a.zone_index - b2.zone_index);
      const cells = seg.cells
        .map((s) => {
          const lv = scenarioLevel(s);
          const delta =
            lv === "ok"
              ? ""
              : `<div class="cell-delta">${
                  s.newly_unloaded_units > 0 ? `未装 ${fmt(s.newly_unloaded_units)} 件` : ""
                }${s.newly_unloaded_units > 0 && s.extra_boxes > 0 ? " · " : ""}${
                  s.extra_boxes > 0 ? `+${fmt(s.extra_boxes)} 箱` : ""
                }</div>`;
          return `<button type="button" class="risk-cell risk-${lv}" data-key="${scenarioKey(s)}"
              title="模拟温区「${escapeHtml(s.zone_name)}」不可用">
            <div class="cell-zone">${escapeHtml(s.zone_name)}</div>
            <div class="cell-range">${fmt(s.zone.t_min)}~${fmt(s.zone.t_max)}℃</div>
            <div class="cell-level">${LEVEL_LABEL[lv]}</div>${delta}
          </button>`;
        })
        .join("");
      return `<div class="matrix-row">
        <div class="matrix-seg">第 ${no} 段<span>${escapeHtml(seg.name)}</span></div>
        <div class="matrix-cells">${cells}</div>
      </div>`;
    })
    .join("");
  $("riskMatrix").innerHTML = `
    <div class="matrix-head">
      <span>每个单元格 = 将该温区模拟为不可用后重新规划的结果(点击展开与基准方案的差异)</span>
      <span class="matrix-legend">
        <i class="dot dot-ok"></i>无影响 <i class="dot dot-warn"></i>仍可运输
        <i class="dot dot-bad"></i>不可运输
      </span>
    </div>
    ${rows}`;
  $("scenarioDetail").hidden = true;
}

function renderScenarioDetail(s) {
  const b = assessData.baseline;
  const unit = assessData.volume_unit;
  const lv = scenarioLevel(s);
  const newly = s.newly_unloaded.length
    ? `<table class="items newly-table">
        <thead><tr><th>由已装载变为未装载的批次</th><th class="num">件数</th></tr></thead>
        <tbody>${s.newly_unloaded
          .map(
            (n) => `<tr><td>#${n.batch_no} ${escapeHtml(n.name)}</td>
              <td class="num">${fmt(n.units)} 件</td></tr>`
          )
          .join("")}</tbody>
      </table>`
    : '<div class="muted detail-none">无批次由已装载变为未装载</div>';
  const diffRows = [
    ["使用箱数", `${b.boxes_used}`, `${s.boxes_used}（${s.extra_boxes >= 0 ? "+" : ""}${fmt(s.extra_boxes)}）`],
    ["已装件数", `${fmt(b.loaded_units)}`, `${fmt(s.loaded_units)}（-${fmt(s.newly_unloaded_units)}）`],
  ]
    .map(
      (r) => `<tr><td>${r[0]}</td><td class="num">${r[1]}</td><td class="num">${r[2]}</td></tr>`
    )
    .join("");
  $("scenarioDetail").innerHTML = `
    <div class="detail-head">
      <span class="badge badge-${lv === "ok" ? "ok" : lv === "warn" ? "warn" : "bad"}">${LEVEL_LABEL[lv]}</span>
      <strong>第 ${s.segment_no} 段「${escapeHtml(s.segment_name)}」 · 温区「${escapeHtml(s.zone_name)}」
        (${fmt(s.zone.t_min)}~${fmt(s.zone.t_max)}℃) 不可用</strong>
      <button type="button" class="btn btn-small detail-close">收起</button>
    </div>
    <table class="items diff-table">
      <thead><tr><th>对比项</th><th class="num">基准方案</th><th class="num">故障场景</th></tr></thead>
      <tbody>${diffRows}</tbody>
    </table>
    <div class="detail-sub">新增未装载(共 ${fmt(s.newly_unloaded_units)} 件)</div>
    ${newly}
    <div class="detail-sub">首个冲突原因</div>
    <div class="detail-reason">${s.first_conflict_reason ? escapeHtml(s.first_conflict_reason) : "无(场景仍可全程运输)"}</div>`;
  $("scenarioDetail").hidden = false;
}

$("btnAssess").addEventListener("click", runAssess);

/* 矩阵单元格点击:展开/收起基准差异 */
$("riskMatrix").addEventListener("click", (e) => {
  const cell = e.target.closest(".risk-cell");
  if (!cell || !assessData) return;
  const key = cell.dataset.key;
  if (activeScenario === key) {
    activeScenario = null;
    $("scenarioDetail").hidden = true;
    cell.classList.remove("active");
    return;
  }
  activeScenario = key;
  [...$("riskMatrix").querySelectorAll(".risk-cell")].forEach((c) =>
    c.classList.toggle("active", c.dataset.key === key)
  );
  const s = assessData.scenarios.find((x) => scenarioKey(x) === key);
  if (s) renderScenarioDetail(s);
});

$("scenarioDetail").addEventListener("click", (e) => {
  if (e.target.classList.contains("detail-close")) {
    activeScenario = null;
    $("scenarioDetail").hidden = true;
    [...$("riskMatrix").querySelectorAll(".risk-cell")].forEach((c) => c.classList.remove("active"));
  }
});

/* ---------------- 实测温度复核 ---------------- */
const SIDE_LABEL = { high: "超上限", low: "超下限", both: "双向超界" };

const fmtHms = (s) => {
  const t = Math.round(s);
  const hh = String(Math.floor(t / 3600)).padStart(2, "0");
  const mm = String(Math.floor((t % 3600) / 60)).padStart(2, "0");
  const ss = String(t % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
};

/* 复核区随方案刷新:填充箱选择;无可复核箱子时隐藏 */
function setupReview(data) {
  const view = $("reviewView");
  if (!data.boxes.length) {
    view.hidden = true;
    return;
  }
  view.hidden = false;
  $("reviewBox").innerHTML = data.boxes
    .map((b) => {
      const batchNos = new Set(b.items.map((i) => i.batch_no));
      const units = b.items.reduce((s, i) => s + i.units, 0);
      return `<option value="${b.box_no}">第 ${b.box_no} 箱 · ${batchNos.size} 批 / ${units} 件 · ` +
        `${fmt(b.temperature_range.t_min)}~${fmt(b.temperature_range.t_max)}℃</option>`;
    })
    .join("");
}

function showReviewError(msg) {
  $("reviewError").hidden = false;
  $("reviewError").textContent = "⚠ " + msg;
}

async function runReview() {
  $("reviewError").hidden = true;
  if (!planSnapshot) {
    showReviewError("请先生成分箱方案");
    return;
  }
  const file = $("reviewFile").files[0];
  if (!file) {
    showReviewError("请选择实测温度 CSV 文件(含 time_seconds,temperature 两列)");
    return;
  }
  const boxNo = Number($("reviewBox").value);
  if (!boxNo) {
    showReviewError("请选择要复核的箱子");
    return;
  }
  const errRaw = $("reviewErrorInput").value.trim();
  let instrumentError = 0;
  if (errRaw !== "") {
    instrumentError = Number(errRaw);
    if (!(Number.isFinite(instrumentError) && instrumentError >= 0)) {
      showReviewError("测温仪器绝对误差须为非负数值(℃),留空按 0 处理");
      return;
    }
  }
  let csvText;
  try {
    csvText = await file.text();
  } catch (e) {
    showReviewError("CSV 文件读取失败,请重试");
    return;
  }
  const btn = $("btnReview");
  btn.disabled = true;
  btn.textContent = "复核中…";
  try {
    const resp = await fetch("/api/temperature-review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        plan_input: JSON.parse(planSnapshot),
        box_no: boxNo,
        csv: csvText,
        instrument_error: instrumentError,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `复核失败（HTTP ${resp.status}）`);
    reviewSnapshot = planSnapshot;
    renderReview(data);
  } catch (e) {
    showReviewError(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "📈 开始复核";
  }
}

function renderReview(data) {
  const sm = data.summary;
  const err = Number(data.instrument_error || 0);
  $("reviewStale").hidden = true;
  $("reviewStale").textContent = "⚠ 输入已变化,复核结果已过期,请重新生成方案";
  const errChip = `
    <span class="assess-chip" title="真实温度视为实测值 ± 仪器绝对误差">🌡 仪器绝对误差 ±${fmt(err)} ℃</span>`;
  let limitChip;
  if (sm.all_certainly_within_limits) {
    limitChip = '<span class="assess-chip chip-ok">✓ 全部批次确定未超界(误差带未接触界外)</span>';
  } else if (sm.all_within_limits) {
    limitChip = `<span class="assess-chip chip-warn">⚠ ${sm.batches_with_possible_excursions} 批可能超界(无确定超界)</span>`;
  } else {
    limitChip = `<span class="assess-chip chip-bad">⚠ ${sm.batches_with_certain_excursions} 批确定超界 · ${sm.batches_with_possible_excursions} 批可能超界</span>`;
  }
  let budgetChip;
  if (sm.budget_checked_count === 0) {
    budgetChip = '<span class="assess-chip muted">无受检预算批次</span>';
  } else if (sm.budget_certain_exceeded_count) {
    budgetChip = `<span class="assess-chip chip-bad">确定超限 ${sm.budget_certain_exceeded_count} · 可能超限 ${sm.budget_possible_exceeded_count} / 受检 ${sm.budget_checked_count} 批</span>`;
  } else if (sm.budget_possible_exceeded_count) {
    budgetChip = `<span class="assess-chip chip-warn">可能超限 ${sm.budget_possible_exceeded_count} / 受检 ${sm.budget_checked_count} 批</span>`;
  } else {
    budgetChip = `<span class="assess-chip chip-ok">✓ 确定未超限 ${sm.budget_checked_count} / 受检 ${sm.budget_checked_count} 批</span>`;
  }
  $("reviewMeta").innerHTML = `
    <span class="assess-chip">第 ${data.box_no} 箱 · 全程 ${fmt(data.transport_hours)} h（${fmt(data.duration_seconds)} 秒）</span>
    <span class="assess-chip">采样 ${fmt(data.sample_count)} 条</span>
    ${err > 0 ? errChip : ""}
    ${limitChip}
    ${budgetChip}
    <span class="assess-chip muted">快照 ${data.input_fingerprint.slice(0, 8)}</span>`;
  $("reviewResult").innerHTML = data.batches.map((b) => renderReviewBatch(b, err)).join("");
}

/* 渲染单个确定/可能超界区间表;err>0 时额外列出误差带可能温度范围 */
function renderExcursionTable(rows, title, rowClass, err, okNote) {
  if (!rows.length) {
    return `<div class="muted review-none">✓ ${okNote}</div>`;
  }
  const bandHead = err > 0 ? "<th class='num'>误差带可能温度</th>" : "";
  const bandCell = (x) =>
    err > 0
      ? `<td class="num">${fmt(x.band_min_temperature)} ~ ${fmt(x.band_max_temperature)} ℃</td>`
      : "";
  const body = rows
    .map(
      (x, i) => `<tr class="${rowClass}">
        <td>第 ${i + 1} 段 · ${SIDE_LABEL[x.side] || x.side}</td>
        <td class="num">${fmt(x.start_s)} s（${fmtHms(x.start_s)}）</td>
        <td class="num">${fmt(x.end_s)} s（${fmtHms(x.end_s)}）</td>
        <td class="num">${fmt(x.duration_s)} s</td>
        <td class="num">${fmt(x.min_temperature)} ~ ${fmt(x.max_temperature)} ℃</td>
        ${bandCell(x)}
      </tr>`
    )
    .join("");
  return `<table class="seg-checks review-exc">
      <thead><tr><th>${title}（线性插值,边界处切段）</th><th class="num">开始</th><th class="num">结束</th><th class="num">持续</th><th class="num">实测温度</th>${bandHead}</tr></thead>
      <tbody>${body}</tbody>
    </table>`;
}

const BUDGET_VERDICT = {
  certain_exceeded: { label: "⚠ 确定超限", cls: "heat-text-bad" },
  possible_exceeded: { label: "⚠ 可能超限", cls: "heat-text-warn" },
  within: { label: "✓ 确定未超限", cls: "heat-text-ok" },
};

function renderReviewBatch(b, err) {
  const certBlock = renderExcursionTable(
    b.excursions,
    err > 0 ? "确定超界区间(误差带整体越界)" : "超界区间",
    "seg-bad",
    err,
    err > 0
      ? "无确定超界区间(误差带未整体越出允许温区)"
      : "全程实测温度均在允许温区内(恰在边界不算超界)"
  );
  const possBlock = renderExcursionTable(
    b.possible_excursions,
    "可能超界区间(误差带接触或跨越界外)",
    "seg-warn",
    err,
    "误差带全程未接触界外,可确定真实温度均在允许温区内"
  );
  let headBadge;
  if (!b.within_limits) {
    headBadge = `<span class="badge badge-bad">⚠ 确定超界 ${b.excursion_count} 段</span>`;
  } else if (!b.certainly_within_limits) {
    headBadge = `<span class="badge badge-warn">⚠ 可能超界 ${b.possible_excursion_count} 段</span>`;
  } else {
    headBadge = '<span class="badge badge-ok">✓ 确定全程在允许温区内</span>';
  }
  let heat;
  if (b.measured_degree_hours === null) {
    heat = '<span class="muted">未设首选温度区间,不核算实测度时</span>';
  } else if (b.max_degree_hours === null) {
    heat = `实测度时 <strong>${fmt(b.measured_degree_hours)}</strong> ℃·h <span class="muted">· 未设预算,不判预算超限</span>` +
      (err > 0
        ? `<br><span class="muted">度时范围(误差带最近/最远距离积分) ${fmt(b.degree_hours_lower)} ~ ${fmt(b.degree_hours_upper)} ℃·h · 未设预算,不判预算超限</span>`
        : "");
  } else {
    const v = BUDGET_VERDICT[b.budget_verdict] || BUDGET_VERDICT.within;
    if (err > 0) {
      heat = `实测度时 <strong>${fmt(b.measured_degree_hours)}</strong> ℃·h · 预算 ${fmt(b.max_degree_hours)} ℃·h<br>` +
        `度时范围(误差带最近/最远距离积分) <strong>${fmt(b.degree_hours_lower)} ~ ${fmt(b.degree_hours_upper)}</strong> ℃·h<br>` +
        `预算剩余范围(最不利 ~ 最有利) <strong class="${v.cls}">${fmt(b.remaining_budget_lower)} ~ ${fmt(b.remaining_budget_upper)}</strong> ℃·h · ` +
        `<span class="${v.cls}">${v.label}</span>`;
    } else {
      const cls = b.budget_exceeded ? "heat-text-bad" : "heat-text-ok";
      heat = `实测度时 <strong>${fmt(b.measured_degree_hours)}</strong> ℃·h · 预算 ${fmt(b.max_degree_hours)} ℃·h · ` +
        `剩余预算 <strong class="${cls}">${fmt(b.remaining_budget)}</strong> ℃·h · ` +
        `<span class="${cls}">${b.budget_exceeded ? "⚠ 超出预算" : "✓ 预算内"}</span>`;
    }
  }
  return `<div class="review-batch">
    <div class="rb-head">
      <span class="rb-title">#${b.batch_no} ${escapeHtml(b.name)} <small class="muted">${fmt(b.units)} 件</small></span>
      <span class="temp-chip">允许 ${fmt(b.allowed_t_min)} ~ ${fmt(b.allowed_t_max)} ℃</span>
      ${headBadge}
    </div>
    ${certBlock}
    ${err > 0 ? possBlock : ""}
    <div class="rb-heat">${heat}</div>
  </div>`;
}

$("btnReview").addEventListener("click", runReview);

/* 按当前方案全程秒数生成空白 CSV 模板 */
$("btnCsvTemplate").addEventListener("click", () => {
  if (!planData) return;
  const total = Math.max(1, Math.round(planData.summary.transport_hours * 3600));
  const step = Math.max(1, Math.round(total / 96));
  const lines = ["time_seconds,temperature"];
  for (let t = 0; t < total; t += step) lines.push(`${t},4`);
  lines.push(`${total},4`);
  const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "实测温度模板.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
});

/* 输入变化:复核绑定生成方案时的快照,输入不一致则禁用复核并提示重新生成方案 */
function markReviewStale() {
  if ($("reviewView").hidden || planSnapshot === null) return;
  let cur = null;
  try {
    cur = JSON.stringify(collectInput());
  } catch (e) {
    cur = null; // 输入暂时不完整,也视为已变化
  }
  const stale = cur !== planSnapshot;
  $("reviewStale").hidden = !stale;
  $("reviewStale").textContent = "⚠ 输入已变化,复核结果已过期,请重新生成方案";
  $("btnReview").disabled = stale;
  $("reviewBox").disabled = stale;
  $("reviewErrorInput").disabled = stale;
  $("reviewFile").disabled = stale;
  $("btnCsvTemplate").disabled = stale;
}

/* 仪器误差变化:旧复核结果与当前误差不再对应时提示重新复核(不改方案快照) */
$("reviewErrorInput").addEventListener("input", () => {
  if (reviewSnapshot !== null && planSnapshot !== null) {
    let cur = null;
    try {
      cur = JSON.stringify(collectInput());
    } catch (e) {
      cur = null;
    }
    if (cur === planSnapshot) {
      $("reviewStale").hidden = false;
      $("reviewStale").textContent = "⚠ 仪器误差已修改,请重新复核";
      $("btnReview").disabled = false;
    }
  }
});

/* 输入变化:旧评估结果标记为已过期(快照比对,恢复原始输入会自动撤销标记) */
function markAssessmentStale() {
  if ($("assessView").hidden || assessSnapshot === null) return;
  let cur = null;
  try {
    cur = JSON.stringify(collectInput());
  } catch (e) {
    cur = null; // 输入暂时不完整,也视为已变化
  }
  $("assessStale").hidden = cur === assessSnapshot;
}

/* 输入变化:旧评估/复核结果标记为已过期(快照比对,恢复原始输入会自动撤销标记) */
function markResultsStale() {
  markAssessmentStale();
  markReviewStale();
}
document.querySelector(".input-panel").addEventListener("input", markResultsStale);
document.querySelector(".input-panel").addEventListener("click", (e) => {
  /* 删除行/段等按钮不触发 input 事件,延迟一帧再比对 */
  if (e.target.closest(".btn")) setTimeout(markResultsStale, 0);
});

$("btnSample").addEventListener("click", () => {
  $("transportHours").value = SAMPLE.transport_hours;
  $("boxCapacity").value = SAMPLE.box_capacity;
  $("maxBoxes").value = SAMPLE.max_boxes;
  $("volumeUnit").value = SAMPLE.volume_unit;
  $("useSegments").checked = !!SAMPLE.use_segments;
  toggleSegmentMode();
  $("zoneRows").innerHTML = "";
  $("cargoRows").innerHTML = "";
  $("segmentList").innerHTML = "";
  SAMPLE.zones.forEach((z) => addZoneRow(z));
  SAMPLE.segments.forEach((s) => addSegmentCard(s));
  SAMPLE.cargoes.forEach(addCargoRow);
  showError("");
  $("errorBox").hidden = true;
  $("resultView").hidden = true;
  $("resultPlaceholder").hidden = false;
  markResultsStale();
});

/* ---------------- 到货交接台账 ---------------- */
/* 交接单冻结在服务端 SQLite,与当前规划输入无关;页面加载即拉取已有交接单,
   支持重启后继续办理。当前方案快照 planSnapshot 仅用于创建新交接单。 */
let handoverOrders = [];
let currentOrder = null;

const HANDOVER_KIND_LABEL = { receive: "签收", return: "退回" };

function showHandoverError(msg) {
  $("handoverError").hidden = false;
  $("handoverError").textContent = "⚠ " + msg;
}

function clearHandoverError() {
  $("handoverError").hidden = true;
  $("handoverError").textContent = "";
}

async function loadHandoverOrders(preferId) {
  clearHandoverError();
  try {
    const resp = await fetch("/api/handover-orders");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `加载交接单列表失败(HTTP ${resp.status})`);
    handoverOrders = data.orders || [];
  } catch (e) {
    showHandoverError(e.message);
    return;
  }
  const sel = $("handoverOrderSelect");
  if (!handoverOrders.length) {
    sel.innerHTML = "";
    currentOrder = null;
    $("recordRows").innerHTML = "";
    renderHandover();
    return;
  }
  const ids = handoverOrders.map((o) => o.id);
  const target =
    preferId && ids.includes(preferId) ? preferId
    : currentOrder && ids.includes(currentOrder.id) ? currentOrder.id
    : ids[ids.length - 1];
  sel.innerHTML = handoverOrders
    .map(
      (o) => `<option value="${o.id}" ${o.id === target ? "selected" : ""}>#${o.id} · ${o.created_at} · 应交 ${o.total_due_units} · 待交 ${o.pending_units} 件</option>`
    )
    .join("");
  await loadOrderDetail(target);
}

async function loadOrderDetail(id) {
  clearHandoverError();
  try {
    const resp = await fetch(`/api/handover-orders/${id}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `加载交接单详情失败(HTTP ${resp.status})`);
    currentOrder = data;
    renderHandover();
  } catch (e) {
    showHandoverError(e.message);
  }
}

function recordItemOptions() {
  if (!currentOrder) return "";
  return currentOrder.items
    .map(
      (it) => `<option value="${it.box_no}:${it.batch_no}">第 ${it.box_no} 箱 · 批 ${it.batch_no}「${escapeHtml(it.batch_name)}」· 应交 ${it.due_units} / 待交 ${it.pending_units} 件</option>`
    )
    .join("");
}

function addRecordRow(focus = true) {
  if (!currentOrder) return;
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><select class="rec-kind">
      <option value="receive">签收(+)</option>
      <option value="return">退回(−)</option>
    </select></td>
    <td><select class="rec-item">${recordItemOptions()}</select></td>
    <td><input class="rec-units" type="number" min="1" step="1" placeholder="正整数" /></td>
    <td><button type="button" class="btn btn-del">删除</button></td>`;
  $("recordRows").appendChild(tr);
  if (focus) tr.querySelector(".rec-units").focus();
  return tr;
}

function refreshRecordRowSelects() {
  [...$("recordRows").querySelectorAll("select.rec-item")].forEach((sel) => {
    const kept = sel.value;
    sel.innerHTML = recordItemOptions();
    if ([...sel.options].some((o) => o.value === kept)) sel.value = kept;
  });
}

function renderHandover() {
  $("handoverEmpty").hidden = handoverOrders.length > 0;
  $("handoverBody").hidden = !currentOrder;
  if (!currentOrder) {
    $("handoverMeta").innerHTML = "";
    $("handoverItems").innerHTML = "";
    $("handoverRecords").innerHTML = "";
    return;
  }
  const s = currentOrder.summary;
  $("handoverMeta").innerHTML = `
    <span class="assess-chip">🧾 交接单 #${currentOrder.id} · 创建于 ${currentOrder.created_at}</span>
    <span class="assess-chip">${s.box_count} 箱 · ${s.item_count} 项明细</span>
    <span class="assess-chip">应交 <strong>${s.total_due_units}</strong> 件</span>
    <span class="assess-chip chip-ok">净签收 <strong>${s.net_signed_units}</strong> 件</span>
    <span class="assess-chip ${s.pending_units === 0 ? "chip-ok" : "chip-warn"}">待交 <strong>${s.pending_units}</strong> 件</span>
    <span class="assess-chip muted">记录 ${s.record_count} 条(有效 ${s.active_record_count} · 已撤销 ${s.voided_record_count}) · 快照 ${currentOrder.input_fingerprint.slice(0, 8)}</span>`;

  $("handoverItems").innerHTML = `
    <table class="seg-checks handover-table">
      <thead><tr>
        <th>箱号</th><th>批号</th><th>批次名称</th>
        <th class="num">应交件数</th><th class="num">净签收</th><th class="num">待交件数</th><th>状态</th>
      </tr></thead>
      <tbody>${currentOrder.items
        .map(
          (it) => `<tr>
            <td class="num">第 ${it.box_no} 箱</td>
            <td class="num">#${it.batch_no}</td>
            <td>${escapeHtml(it.batch_name)}</td>
            <td class="num">${it.due_units}</td>
            <td class="num">${it.net_signed_units}</td>
            <td class="num">${it.pending_units}</td>
            <td>${
              it.pending_units === 0
                ? '<span class="badge badge-ok">✓ 已交清</span>'
                : it.net_signed_units > 0
                ? '<span class="badge badge-warn">部分签收</span>'
                : '<span class="muted">待签收</span>'
            }</td>
          </tr>`
        )
        .join("")}</tbody>
    </table>`;

  if (!currentOrder.records.length) {
    $("handoverRecords").innerHTML = '<div class="muted review-none">暂无签收/退回记录</div>';
  } else {
    $("handoverRecords").innerHTML = `
      <table class="seg-checks handover-table">
        <thead><tr>
          <th>记录</th><th>提交批次</th><th>类型</th><th>箱号</th><th>批号(批次)</th>
          <th class="num">件数</th><th>状态</th><th>登记时间</th><th>操作</th>
        </tr></thead>
        <tbody>${currentOrder.records
          .map(
            (r) => `<tr class="${r.voided ? "rec-voided" : ""}">
              <td class="num">#${r.id}</td>
              <td class="num">第 ${r.submission_id} 笔</td>
              <td><span class="badge ${r.kind === "receive" ? "badge-ok" : "badge-warn"}">${HANDOVER_KIND_LABEL[r.kind]}</span></td>
              <td class="num">第 ${r.box_no} 箱</td>
              <td>#${r.batch_no} ${escapeHtml(r.batch_name)}</td>
              <td class="num">${r.kind === "receive" ? "+" : "−"}${r.units}</td>
              <td>${r.voided ? `<span class="badge badge-bad">已撤销</span><br><small class="muted">${r.voided_at || ""}</small>` : "有效"}</td>
              <td class="muted">${r.created_at}</td>
              <td>${r.voided ? "" : `<button type="button" class="btn btn-del btn-void" data-id="${r.id}">撤销</button>`}</td>
            </tr>`
          )
          .join("")}</tbody>
      </table>`;
  }
  refreshRecordRowSelects();
  /* 无记录行时补一行空行,便于直接填写(不抢焦点) */
  if (currentOrder && !$("recordRows").children.length) addRecordRow(false);
}

async function submitRecords() {
  clearHandoverError();
  if (!currentOrder) return;
  const rows = [...$("recordRows").querySelectorAll("tr")];
  if (!rows.length) {
    showHandoverError("请至少添加一行签收/退回记录");
    return;
  }
  const records = [];
  for (let i = 0; i < rows.length; i++) {
    const kind = rows[i].querySelector(".rec-kind").value;
    const itemVal = rows[i].querySelector(".rec-item").value;
    const [boxNo, batchNo] = itemVal.split(":").map(Number);
    const raw = rows[i].querySelector(".rec-units").value.trim();
    if (!/^\d+$/.test(raw) || Number(raw) <= 0) {
      showHandoverError(`第 ${i + 1} 行的件数须为正整数,请修改后重新提交`);
      return;
    }
    records.push({ kind, box_no: boxNo, batch_no: batchNo, units: Number(raw) });
  }
  const btn = $("btnSubmitRecords");
  btn.disabled = true;
  btn.textContent = "提交中…";
  try {
    const resp = await fetch(`/api/handover-orders/${currentOrder.id}/records`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ records }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `提交失败(HTTP ${resp.status})`);
    currentOrder = data;
    $("recordRows").innerHTML = "";
    renderHandover();
    addRecordRow();
    await loadHandoverOrders(currentOrder.id);
  } catch (e) {
    showHandoverError(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "✔ 提交记录";
  }
}

async function voidRecord(recordId) {
  clearHandoverError();
  if (!currentOrder) return;
  try {
    const resp = await fetch(
      `/api/handover-orders/${currentOrder.id}/records/${recordId}/void`,
      { method: "POST" }
    );
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `撤销失败(HTTP ${resp.status})`);
    currentOrder = data;
    renderHandover();
    await loadHandoverOrders(currentOrder.id);
  } catch (e) {
    showHandoverError(e.message);
  }
}

async function createHandoverOrder() {
  clearHandoverError();
  if (!planSnapshot) {
    showHandoverError("请先生成分箱方案,再从当前方案创建交接单");
    return;
  }
  const btn = $("btnCreateOrder");
  btn.disabled = true;
  btn.textContent = "创建中…";
  try {
    const resp = await fetch("/api/handover-orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan_input: JSON.parse(planSnapshot) }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `创建交接单失败(HTTP ${resp.status})`);
    await loadHandoverOrders(data.id);
  } catch (e) {
    showHandoverError(e.message);
  } finally {
    btn.textContent = "🧾 从当前方案创建交接单";
    refreshCreateOrderBtn();
  }
}

function refreshCreateOrderBtn() {
  const btn = $("btnCreateOrder");
  btn.disabled = !planSnapshot;
  btn.title = planSnapshot
    ? "按当前方案冻结各箱各批次的应交件数,生成新的交接单"
    : "请先生成分箱方案,再从当前方案创建交接单";
}

$("btnCreateOrder").addEventListener("click", createHandoverOrder);
$("btnRefreshOrders").addEventListener("click", () => loadHandoverOrders());
$("handoverOrderSelect").addEventListener("change", (e) => {
  if (e.target.value) loadOrderDetail(Number(e.target.value));
});
$("btnAddRecordRow").addEventListener("click", addRecordRow);
$("btnSubmitRecords").addEventListener("click", submitRecords);
$("handoverRecords").addEventListener("click", (e) => {
  const btn = e.target.closest(".btn-void");
  if (btn) voidRecord(Number(btn.dataset.id));
});

/* ---------------- 初始空行 ---------------- */
addZoneRow({ name: "冷藏区", t_min: 0, t_max: 8 });
addCargoRow({ name: "", units: "", unit_volume: "", t_min: "", t_max: "" });

/* 台账:初始加载已有交接单(重启续办),创建按钮待首次生成方案后启用 */
refreshCreateOrderBtn();
loadHandoverOrders();
