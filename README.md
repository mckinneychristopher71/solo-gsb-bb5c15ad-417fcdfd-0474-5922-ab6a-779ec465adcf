# ❄ 冷链装箱温控规划器

一个可使用 **Docker Compose** 一键启动的 Web 应用：录入货物批次、箱体容量、运输时长与允许温区后，
系统按 **温区兼容性** 与 **容量约束** 自动生成分箱方案，展示每箱温度区间、装载明细以及
未能装载货物的冲突原因。支持 **分段运输温区校验**：按顺序维护各运输段的名称、时长与可用温区，
规划时对箱内货物的共同允许温度 **逐段检查**，报告首次不兼容的运输段、受影响批次与原因，
只有全程通过才标记为可运输。支持 **累计热暴露（度时）预算**：每批货物可填写首选温度区间与
最大度时预算（℃·h），规划器为每个箱子 **逐段选择设定点**（每段从可行区间端点与
首选区间端点中，选取使本段结束后箱内最大累计预算使用率最低的点，平局取较低温度）；
任何一批累计超预算时该装箱组合不可用。支持 **锁温区段**：运输段可选填锁温组，
**相邻且同组的段必须共用一个设定点**——共享设定点取自各段可行温度段（可能不连续）
交集保留的真实区间，结合组前累计热暴露，选取使整组结束时箱内最大预算使用率最低的点，
平局取较低温度；不存在共享设定点时拒绝该装箱组合，并报告首个失败组及涉及段。
支持 **单温区故障容灾评估**：基于当前输入一键触发，系统将 **每个运输段的每个可用温区
分别模拟为不可用**，复用完整装箱、锁温与热暴露规则重新规划（基准方案保持不变），
每个场景返回是否仍可运输、额外箱数、由已装载变为未装载的批次与件数及首个冲突原因，
前端以 **风险矩阵** 展示并可展开查看与基准方案的差异；评估绑定同一输入快照，
输入变化后旧结果自动标记为 **已过期**。
支持 **实测温度复核**：在分箱结果页选择已生成方案的一箱，上传含
`time_seconds,temperature` 两列的实测温度 CSV，并可填写 **测温仪器绝对误差**
（℃，非负，默认 0），系统按该箱装载批次逐批核算——相邻采样间温度按
**线性变化**，将每时刻的真实温度视为 **实测值 ± 误差**（误差带），按各批允许温区
分别列出 **确定超界**（误差带整体越界）与 **可能超界**（误差带接触或跨越界外）的
连续时间段（在误差带端点穿越边界处切段，恰在边界不算超界）；对已设首选区间的批次，
按每时刻误差带到首选区间的 **最近/最远距离** 分别积分（在距离公式改变处切段）得到
实测度时的 **下界/上界**，将预算判为 **确定超限、可能超限或确定未超限**（严格大于
预算才判超限，未设预算不判）。误差填 0 时与原复核结果一致（确定=可能、下界=上界=
实测度时）。复核绑定生成方案时的输入快照，修改输入后旧复核结果自动标记为
**已过期**，需重新生成方案后再复核。
支持 **到货交接台账**：在分箱结果页一键 **从当前方案创建交接单**，服务端按规划输入
快照重新规划并把 **各箱各批次的应交件数冻结** 存入 SQLite——此后修改规划输入、
重新生成方案均不影响已创建的交接单，**重启后仍可继续办理**（数据库文件挂在
Docker 命名卷上）。支持 **一次提交多行签收/退回记录**（每行指定箱号、批号与
正整数件数），按提交顺序逐行累计 **净签收量**（签收 +、退回 −），任一行使某箱
某批净签收量 **小于 0 或超过应交件数** 时 **整笔不入账**；支持 **撤销** 未撤销的
记录——撤销前按原顺序 **跳过该记录重放** 其余有效记录，若任何后续记录导致数量
越界则 **拒绝撤销并指出冲突记录**，原台账保持不变。页面实时展示记录列表与各箱
各批次的 **净签收 / 待交件数**。
支持在页面上随时修改输入并重新规划。

- 后端：FastAPI（规划算法见 `app/planner.py`；交接台账见 `app/handover.py`，
  使用 SQLite 持久化，见 `app/db.py`）
- 前端：原生 HTML / CSS / JavaScript（无 CDN 依赖，离线可用）
- 部署：Docker Compose（数据库初始化与持久化卷已纳入启动）

## 一、启动命令

### 方式 A：Docker Compose（推荐，单条命令一键启动）

```bash
docker compose up -d --build
```

启动即自动完成：构建镜像 → 初始化 SQLite 数据库（建表）→ 挂载持久化卷
`handover-data`（交接单与签收/退回记录重启后仍在）→ 健康检查。

查看日志 / 停止：

```bash
docker compose logs -f
docker compose down        # 保留数据卷;加 -v 才会删除台账数据
```

### 方式 B：本地 Python 运行（Python 3.11+）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

本地运行时数据库默认写到 `./data/handover.db`（可用 `HANDOVER_DB_PATH` 修改）。

## 二、访问地址与端口

| 内容 | 地址 |
| --- | --- |
| 规划器页面（含到货交接台账） | <http://localhost:8000/> |
| 健康检查 | <http://localhost:8000/api/health> |
| 规划接口 | `POST http://localhost:8000/api/plan` |
| 容灾评估接口 | `POST http://localhost:8000/api/zone-failure-assessment` |
| 实测温度复核接口 | `POST http://localhost:8000/api/temperature-review` |
| 创建交接单 | `POST http://localhost:8000/api/handover-orders` |
| 交接单列表 / 详情 | `GET http://localhost:8000/api/handover-orders` · `GET /api/handover-orders/{id}` |
| 提交签收/退回记录 | `POST http://localhost:8000/api/handover-orders/{id}/records` |
| 撤销记录 | `POST http://localhost:8000/api/handover-orders/{id}/records/{record_id}/void` |
| Swagger 接口文档 | <http://localhost:8000/docs> |

## 三、配置

端口通过环境变量 `APP_PORT` 修改（默认 `8000`）；台账数据库文件在容器内的路径
通过 `HANDOVER_DB_PATH` 修改（默认 `/data/handover.db`，目录已挂载命名卷
`handover-data` 持久化，一般无需修改）：

```bash
# 方式 1：复制示例配置后修改
cp .env.example .env
#   .env 内容:APP_PORT=9000
#             HANDOVER_DB_PATH=/data/handover.db

# 方式 2：启动时临时指定
APP_PORT=9000 docker compose up -d --build
```

随后访问 <http://localhost:9000/>。

## 四、使用说明

1. 填写 **运输时长**、**单箱容量**、**最大箱数**、**体积单位**。
2. 选择温区维护方式（二选一）：
   - **单一温区（默认）**：在 **允许温区** 中维护全程可用的温区（名称 + 上下限，℃）；
   - **分段运输**：勾选「启用分段运输」后，按执行顺序维护 **运输段**
     （段名称 + 时长 h + 该段可用温区），总运输时长自动按各段时长求和，
     「运输时长」输入框随之禁用；每个运输段还可选填 **锁温组**——
     相邻且锁温组相同的段将 **共用一个设定点**（留空表示不参与锁温）。
3. 在 **货物批次** 中录入每批货物：件数、单件体积、允许温度区间（硬边界），
   以及可选的「首选温度区间 + 度时预算」（℃·h，留空表示不限）和
   「最长运输时长」（留空表示不限）。
4. 点击 **「▶ 生成分箱方案」**：
   - 每箱展示 **可设定温度区间**（可能含多个不相连温度段）、**推荐设定温度**、
     匹配温区、容量利用率与装载明细（批次、件数、占用体积）；
   - 分段模式下每箱额外展示 **逐段校验表**（每段的可设定温度、**逐段设定点**、
     匹配温区），全部通过才标记 **「✓ 全程可运输」**；属于锁温组的段会标注
     **🔒 锁温组徽章** 与 **共享设定点**（并列出共用该设定点的段号）；
   - 箱内存在预算批次时展示 **热暴露核算表**：各批首选区间、**累计暴露量**、
     预算、**剩余预算**、使用率与状态（超限时标注 **首次超限段**）；
   - 未能装载的货物会在下方红色区域列出 **具体冲突原因**；分段不兼容时
     附带 **首次不兼容的运输段** 徽章（第几段 · 段名），热暴露超预算时
     附带 **首次超限段** 徽章，锁温组不存在共享设定点时附带
     **锁温组失败** 徽章（组名 + 涉及段号）。
5. 直接在页面修改任意输入后再次点击按钮即可重新规划；点击 **「载入示例」**
   可快速体验分段运输（示例包含首段不兼容、末段不兼容、运输超时、
   热暴露预算不足、容量不足等多种冲突，并将「干线冷链 + 支线转运」
   划入同一锁温组「干线锁温」）。
6. 点击 **「🛡 容灾评估」** 触发单温区故障容灾评估：
   - 页面下方出现 **风险矩阵**：每行是一个运输段，每个单元格代表
     「该温区不可用」的故障场景，颜色标识风险等级
     （绿 = 无影响，黄 = 仍可运输但需更多箱子，红 = 不可运输）；
   - 点击任意单元格可 **展开与基准方案的差异**：箱数/已装件数对比、
     由已装载变为未装载的批次与件数、首个冲突原因；
   - 评估绑定触发时的 **输入快照**（响应含快照指纹）；此后修改任意输入，
     评估结果上方会出现 **「已过期」** 标记，重新点击按钮即可按新输入评估。
7. 生成方案后，在结果页 **实测温度复核** 区选择任一箱子，可选填写
   **测温仪器绝对误差（℃，非负，默认 0；留空按 0 处理）**，再上传实测温度 CSV
   （首行表头须含 `time_seconds,temperature` 两列；时间从 0 严格递增至
   全程秒数，至少两条记录），点击 **「📈 开始复核」**：
   - 每批展示 **确定超界区间** 与 **可能超界区间**（起止秒数 + 时刻、持续时长、
     实测温度范围；误差 > 0 时附误差带可能温度范围，可能区间以黄色行区分）、
     **实测度时**、**度时范围（下界 ~ 上界）**、**预算剩余范围** 与判定
     （**确定超限 / 可能超限 / 确定未超限**；未设首选区间的批次不核算度时，
     未设预算的批次不判预算超限）；
   - 顶部统计条展示仪器误差值、确定/可能超界批次数与预算判定汇总；
   - **误差为 0 时不展示「可能超界」区，判定与原复核完全一致**；
   - 修改仪器误差后旧结果上方出现 **「仪器误差已修改,请重新复核」** 提示；
   - 复核绑定 **生成方案时的输入快照**；此后修改任意输入，复核区出现
     **「已过期」** 标记并禁用复核按钮，重新生成方案后即可再次复核；
   - 点击 **「下载 CSV 模板」** 可按当前方案全程秒数生成空白模板。
8. 结果页底部的 **到货交接台账** 区管理交接单（无需重新生成方案即可查看，
   页面打开时自动加载已有交接单，重启后可继续办理）：
   - 生成方案后点击 **「🧾 从当前方案创建交接单」**，系统按当前方案冻结
     各箱各批次的应交件数生成新交接单；此后修改规划输入不影响该单；
   - 通过下拉框切换交接单，页面展示 **应交/净签收/待交件数** 明细表、
     汇总徽标与全部签收/退回记录；
   - 在 **登记签收/退回** 区一次添加多行（类型 + 箱号·批号 + 件数），
     点击 **「✔ 提交记录」** 整笔入账；任一行越界时整笔不入账并提示
     违规行与原因；
   - 每条有效记录可点击 **「撤销」**；若撤销会导致后续记录越界，则拒绝
     撤销并指出冲突记录（需先撤销其后的冲突记录）。

## 五、规划规则

- 货物允许温区必须与至少一个可用温区有交集，否则判定为 **温区不兼容**；
- 同一箱内所有货物的允许温区交集，且交集必须仍落在某个可用温区内；
- **分段运输**：箱内货物的共同允许温度必须在 **每一个运输段** 都能被该段
  某个温区覆盖；任一环节不满足即整批不可运输，并报告 **首次不兼容的运输段、
  受影响批次与原因**；只有全程通过才标记为可运输；
- 分段模式下总运输时长 = 各段时长之和；若同时显式填写运输时长且与段时长
  之和不一致，接口返回 400 提示；
- 同一批货物允许以 **件** 为最小单位拆分到多个箱子；
- 装箱采用 First-Fit-Decreasing 启发式：温度下限越低（要求越苛刻）的批次优先，
  同温限按总体积降序，依次尝试已有箱子，放不下再开新箱；
- **累计热暴露（度时）预算**（可选，按批次填写）：
  - 每批货物可填写 **首选温度区间** 与 **最大度时预算**（℃·h）；
    允许温区仍是硬边界，首选区间只是"更希望待在"的温度带；
    未填写预算的批次保持原规则（逐段设定点取最宽可行区间中点）；
  - 规划器为每个箱子 **逐段选择一个设定点**，该点同时落在箱内货物允许温区
    交集与该段某个可用温区内；
  - **单段暴露量 = 段时长 × 设定点到该批首选区间的距离**（区间内距离为 0），
    全程累计即为该批的累计热暴露；
  - 每段从 **可行区间端点** 与落在可行区间内的 **首选区间端点** 中，
    选取使 **本段结束后箱内最大累计预算使用率** 最低的点，平局取较低温度；
  - **任何货物累计超预算时该装箱组合不可用**：装箱时若某批的加入会导致
    箱内任一批次超预算，则该批无法装入此箱；单独装箱即超预算的批次
    整批不予装载，并报告按最优设定点的累计暴露与 **首次超限段**；
- **锁温区段**（可选，按运输段填写锁温组）：
  - **相邻且锁温组相同的连续段必须共用一个设定点**；未填写锁温组的段
    各自独立选点，被未分组段隔开的同名组互不构成约束；
  - 共享设定点取自 **组内各段可行温度段（箱内允许温区交集 ∩ 该段各温区，
    可能为不连续的多段）交集保留的真实区间**，不做凸包合并；
  - 选点目标与逐段规则一致：结合 **组前各批累计热暴露**，从真实区间端点与
    落在区间内的首选区间端点中，选取使 **整组结束时箱内最大累计预算使用率**
    最低的点，平局取较低温度；组内各段按共享设定点逐段累计暴露；
  - **交集为空（不存在共享设定点）时该装箱组合不可用**：装箱时导致交集为空的
    批次无法装入此箱；单独装箱即无共享设定点的批次整批不予装载，
    并报告 **首个失败的锁温组及涉及段**；
- 运输时长超过货物「最长运输时长」时，该批整批不予装载并给出原因；
- 达到最大箱数或单件体积超过单箱容量时，剩余货物按冲突输出。

## 六、单温区故障容灾评估规则

- 评估与规划使用 **同一份输入快照**：先按原规则算出基准方案，随后依次将
  **每个运输段的每个可用温区分别模拟为不可用**（其余输入不变），
  复用完整装箱、锁温与热暴露规则 **重新规划**；基准方案本身不被修改；
- 每个故障场景返回：
  - **是否仍可运输**：基准方案中已装载的货物在该场景下是否仍能全部装载
    （即没有任何批次由已装载变为未装载）；
  - **额外箱数**：场景箱数 − 基准箱数（可为负，表示货物装不下后箱数减少）；
  - **由已装载变为未装载的批次与件数**：基准已装、场景中少装的批次及差值件数；
  - **首个冲突原因**：场景规划中首个新增未装载批次的未装载原因
    （无新增未装载时为 `null`）；
- 某温区是其所在运输段的 **唯一温区** 时，该段失效后不再有任何可用温区，
  所有批次均无法通过该段，场景直接判定为不可运输；
- 场景按 **未装载件数、额外箱数降序**（影响大者居前），**段序号、温区序升序**
  稳定排序；
- 响应携带输入快照的 **SHA-256 指纹**（`input_fingerprint`），前端据此在
  输入变化后将旧评估结果标记为已过期。

## 七、实测温度复核规则

- 复核请求携带 **生成方案时的输入快照**（`plan_input`，与 `/api/plan` 请求体
  相同）、箱号（`box_no`）、实测温度 CSV 文本与 **测温仪器绝对误差**
  （`instrument_error`，℃，非负，默认 `0`）；服务端按快照 **重新规划** 并
  定位箱子，再按该箱装载批次逐批核算，保证复核与页面所示方案一致；
- **CSV 校验**（任何一条不满足即整单拒绝，返回 400；误差为负/非数字返回 422）：
  - 首行须为表头，且包含 `time_seconds`、`temperature` 两列（列顺序不限，
    额外列忽略，表头大小写/空白容忍）；
  - 至少 **两条** 记录；`time_seconds` 从 **0** 开始 **严格递增**，末条必须
    等于 **全程秒数**（运输时长 × 3600）；
  - 时间与温度均须为 **有限数值**（拒绝空值、非数字、NaN、±Inf）；
- **误差带**：每时刻真实温度视为闭区间 `[T − err, T + err]`；相邻采样点间
  实测温度 `T(t)` 线性变化，误差带两个端点同样线性；
- **确定超界区间**：误差带 **整体** 越出允许温区——`T − err > 上限` 或
  `T + err < 下限`；
- **可能超界区间**：误差带 **接触或跨越** 界外——`T + err > 上限` 或
  `T − err < 下限`（确定超界集合是可能超界集合的子集）；
- 两类区间都在实测温度穿越 `允许边界 ± err` 处 **切段**，合并仅经过边界点的
  相邻段；**恰在边界（相等）不算超界**，仅在单个时刻相切（零时长）不产生区间；
  区间附起止秒数、持续时长、实测温度极值与误差带可能温度极值；
- **度时下界 / 上界**（仅已设首选区间的批次）：每时刻取误差带到首选区间的
  **最近距离**（误差带与首选区间相交即为 0）与 **最远距离**（误差带端点距离
  之较大者），分别对时间 **积分**（在距离公式改变处，即首选边界 ±err 与首选
  区间中点处切段后梯形精确积分）并 **除以 3600**；实测度时仍按实测值本身积分，
  恒有 `下界 ≤ 实测度时 ≤ 上界`；
- **预算判定**（已设预算时，严格大于预算才计超限）：
  - **确定超限**（`certain_exceeded`）：度时下界也严格大于预算；
  - **确定未超限**（`within`）：度时上界不大于预算；
  - **可能超限**（`possible_exceeded`）：上界大于预算但下界不大于预算；
  - 同时给出预算剩余范围 `预算 − 上界`（最不利）~ `预算 − 下界`（最有利）；
- **误差为 0 时** 误差带退化为实测温度曲线：可能超界区间与确定超界区间相同、
  度时下界=上界=实测度时、`within`/`certain_exceeded` 判定与原复核一致；
- 响应携带输入快照的 SHA-256 指纹（`input_fingerprint`），输入变化后前端
  将旧复核结果标记为已过期，需重新生成方案后再复核。

## 八、到货交接台账规则

- **创建交接单**：请求体携带规划输入快照（`plan_input`，与 `/api/plan`
  相同），服务端按快照 **重新规划**，把结果中各箱各批次的已装件数冻结为
  **应交件数**（同一箱同一批多次出现的件数合并）；方案没有任何已装载货物时
  拒绝创建（400）。交接单连同输入快照、快照指纹一并存入 SQLite，
  **此后修改规划输入、重新生成方案均不影响已创建的交接单**，服务重启后
  可继续办理；
- **提交签收/退回记录**：一次提交 **多行**，每行指定 `kind`
  （`receive` 签收 / `return` 退回）、`box_no`、`batch_no` 与正整数 `units`；
  服务端在既有台账基础上 **按提交顺序逐行累计净签收量**（签收 +、退回 −），
  任何一行使该箱该批净签收量 **小于 0 或超过应交件数**，或箱号/批号不在
  交接单明细中，则 **整笔不入账**（事务整体回滚，400 并指明违规行）；
  同一笔提交的记录共享一个 **提交批次号**（`submission_id`，按单递增）；
- **撤销记录**：仅可撤销 **未撤销** 的记录（重复撤销返回 400）。撤销前按
  原提交顺序 **跳过该记录重放** 其余有效记录：若任一后续记录导致净签收量
  越界，则 **拒绝撤销**（409），响应 `conflicts` 指出 **冲突记录**
  （记录号、类型、箱号、批号、件数、越界后的数量与应交量），
  **原台账保持不变**；需先撤销其后的冲突记录。重放通过才把该记录标记为
  已撤销（保留记录与撤销时间，不物理删除）；
- **台账展示**：交接单详情返回各箱各批次的应交件数、**净签收** 与
  **待交件数**（应交 − 净签收），以及全部记录（含已撤销）与汇总；
- **持久化**：三张表（`handover_orders` / `handover_items` /
  `handover_records`）存于 SQLite（WAL 模式、外键约束），数据库文件路径由
  `HANDOVER_DB_PATH` 指定，Compose 下默认 `/data/handover.db` 并挂载命名卷
  `handover-data`，容器重启/重建后数据仍在。

### 到货交接台账接口示例

```bash
# 1) 从当前方案创建交接单(plan_input 与 /api/plan 请求体相同)
curl -X POST http://localhost:8000/api/handover-orders \
  -H "Content-Type: application/json" \
  -d '{
    "plan_input": {
      "transport_hours": 24, "box_capacity": 12,
      "zones": [{"name": "冷藏区", "t_min": 0, "t_max": 8}],
      "cargoes": [
        {"name": "鲜奶", "units": 40, "unit_volume": 0.25, "t_min": 2, "t_max": 6},
        {"name": "三文鱼", "units": 10, "unit_volume": 0.4, "t_min": -2, "t_max": 2}
      ]
    }
  }'
# → 201;items 冻结为 箱1·批1 鲜奶 应交 32、箱1·批2 三文鱼 应交 10、箱2·批1 鲜奶 应交 8

# 2) 一次提交多行签收/退回(按行序累计,任一行越界则整笔不入账)
curl -X POST http://localhost:8000/api/handover-orders/1/records \
  -H "Content-Type: application/json" \
  -d '{"records": [
        {"kind": "receive", "box_no": 1, "batch_no": 1, "units": 20},
        {"kind": "receive", "box_no": 1, "batch_no": 2, "units": 10},
        {"kind": "return",  "box_no": 1, "batch_no": 2, "units": 3}
      ]}'
# → 201;净签收:箱1·批1 = 20,箱1·批2 = 7

# 3) 撤销一条记录(若导致后续记录越界则 409 并附 conflicts,台账不变)
curl -X POST http://localhost:8000/api/handover-orders/1/records/2/void

# 4) 查看列表与详情(重启后仍可查询、继续办理)
curl http://localhost:8000/api/handover-orders
curl http://localhost:8000/api/handover-orders/1
```

### 到货交接台账响应字段

| 字段 | 说明 |
| --- | --- |
| `id` / `created_at` / `input_fingerprint` | 交接单号、创建时间与创建时规划输入快照的 SHA-256 指纹 |
| `plan_input` | 创建时的规划输入快照(冻结保存,不随后续修改变化) |
| `items[]` | 应交明细:`box_no` / `batch_no` / `batch_name` / `due_units`(应交件数)/ `net_signed_units`(净签收)/ `pending_units`(待交 = 应交 − 净签收) |
| `records[]` | 全部记录:`id`、`submission_id`(提交批次号)、`kind`(`receive`/`return`)、`box_no` / `batch_no` / `batch_name`、`units`、`voided`、`created_at` / `voided_at` |
| `summary` | 箱数、明细项数、应交/净签收/待交总件数、记录总数/有效数/已撤销数 |
| 撤销 409 响应 `conflicts[]` | 冲突记录明细:`record_id`、`kind`、`box_no`、`batch_no`、`units`、`resulting_net`(重放后越界的净签收量)、`due_units` |

## 九、接口示例

### 单一温区模式（兼容原有调用）

```bash
curl -X POST http://localhost:8000/api/plan \
  -H "Content-Type: application/json" \
  -d '{
    "transport_hours": 24,
    "box_capacity": 12,
    "max_boxes": 20,
    "volume_unit": "m³",
    "zones": [{"name": "冷藏区", "t_min": 0, "t_max": 8}],
    "cargoes": [{"name": "鲜奶", "units": 40, "unit_volume": 0.25,
                 "t_min": 2, "t_max": 6}]
  }'
```

### 分段运输模式

提供 `segments`（非空）即启用分段校验；`transport_hours` 可省略，
默认取各段时长之和（若显式填写则必须与段时长之和一致）：

```bash
curl -X POST http://localhost:8000/api/plan \
  -H "Content-Type: application/json" \
  -d '{
    "box_capacity": 12,
    "segments": [
      {"name": "干线冷链", "hours": 14,
       "zones": [{"name": "冷冻区", "t_min": -18, "t_max": -2},
                 {"name": "冷藏区", "t_min": 0, "t_max": 8}]},
      {"name": "末端配送", "hours": 4,
       "zones": [{"name": "冷藏区", "t_min": 0, "t_max": 8},
                 {"name": "常温区", "t_min": 15, "t_max": 25}]}
    ],
    "cargoes": [
      {"name": "三文鱼", "units": 10, "unit_volume": 0.4, "t_min": -2, "t_max": 2},
      {"name": "冷冻牛排", "units": 5, "unit_volume": 0.8, "t_min": -22, "t_max": -15}
    ]
  }'
```

### 热暴露（度时）预算

货物批次可额外填写 `preferred_t_min` / `preferred_t_max`（首选温度区间，需同时填写）
与 `max_degree_hours`（最大度时预算 ℃·h，留空表示不限）：

```bash
curl -X POST http://localhost:8000/api/plan \
  -H "Content-Type: application/json" \
  -d '{
    "box_capacity": 12,
    "segments": [
      {"name": "干线冷链", "hours": 14,
       "zones": [{"name": "冷藏区", "t_min": 0, "t_max": 8}]},
      {"name": "末端配送", "hours": 4,
       "zones": [{"name": "冷藏区", "t_min": 0, "t_max": 8}]}
    ],
    "cargoes": [
      {"name": "三文鱼", "units": 10, "unit_volume": 0.4, "t_min": -2, "t_max": 2,
       "preferred_t_min": -1, "preferred_t_max": 1, "max_degree_hours": 30},
      {"name": "巴氏鲜奶", "units": 20, "unit_volume": 0.25, "t_min": 2, "t_max": 6,
       "preferred_t_min": 3, "preferred_t_max": 5, "max_degree_hours": 20}
    ]
  }'
```

两批允许温区的交集只有单点 2℃，设定点被锁定在 2℃，全程 18h 每批各累计
18 ℃·h 暴露。响应中 `boxes[0].heat_exposure` 示例：

```json
[
  {"batch_no": 1, "name": "三文鱼", "preferred_t_min": -1.0, "preferred_t_max": 1.0,
   "max_degree_hours": 30.0, "cumulative_exposure": 18.0, "remaining_budget": 12.0,
   "usage_ratio": 0.6, "exceeded": false,
   "first_exceeded_segment_no": null, "first_exceeded_segment_name": null},
  {"batch_no": 2, "name": "巴氏鲜奶", "preferred_t_min": 3.0, "preferred_t_max": 5.0,
   "max_degree_hours": 20.0, "cumulative_exposure": 18.0, "remaining_budget": 2.0,
   "usage_ratio": 0.9, "exceeded": false,
   "first_exceeded_segment_no": null, "first_exceeded_segment_name": null}
]
```

若把「巴氏鲜奶」的预算改为 10，则它无法与三文鱼同箱（同箱暴露 18 > 10），
会自动改放新箱并按其首选区间选择设定点。若某批即使单独装箱也必然超预算，
则整批不予装载——例如在三段运输（干线 14h / 支线 6h / 末端 4h，各段均有
冷藏区 [0,8]℃，末端另有常温区 [15,25]℃）下，允许温区 [2,18]℃、
首选区间 [12,16]℃、预算 40 ℃·h 的「熟成奶酪」，最优设定点下全程仍累计
80 ℃·h，`unloaded` 条目为：

```json
{
  "batch_no": 1,
  "name": "熟成奶酪",
  "units": 6,
  "volume": 3.0,
  "reason": "按逐段最优设定点,该批全程累计热暴露 80.0 ℃·h 超过度时预算 40.0 ℃·h(首次超限:第 1 段「干线冷链」),任何装箱组合均不可用",
  "cumulative_exposure": 80.0,
  "max_degree_hours": 40.0,
  "first_exceeded_segment_no": 1,
  "first_exceeded_segment_name": "干线冷链"
}
```

### 锁温区段（共享设定点）

运输段可填写 `lock_group`（锁温组名称，可选）；**相邻且同组的段必须共用一个
设定点**，共享设定点取自各段可行温度段交集保留的真实区间，并使整组结束时
箱内最大预算使用率最低：

```bash
curl -X POST http://localhost:8000/api/plan \
  -H "Content-Type: application/json" \
  -d '{
    "box_capacity": 12,
    "segments": [
      {"name": "干线冷链", "hours": 14, "lock_group": "全程锁温",
       "zones": [{"name": "冷藏区", "t_min": 0, "t_max": 8}]},
      {"name": "末端配送", "hours": 4, "lock_group": "全程锁温",
       "zones": [{"name": "冷藏区", "t_min": 0, "t_max": 8},
                 {"name": "常温区", "t_min": 15, "t_max": 25}]}
    ],
    "cargoes": [
      {"name": "三文鱼", "units": 10, "unit_volume": 0.4, "t_min": -2, "t_max": 2,
       "preferred_t_min": -1, "preferred_t_max": 1, "max_degree_hours": 30}
    ]
  }'
```

两段同属「全程锁温」，共享可行区间为 [0,2]℃，共享设定点选为 0℃（暴露为 0）。
响应中 `boxes[0].segment_checks` 会按段标出锁温组与共享设定点：

```json
[
  {"segment_no": 1, "name": "干线冷链", "hours": 14.0, "ok": true,
   "temperature_segments": [{"t_min": 0.0, "t_max": 2.0}],
   "matched_zones": ["冷藏区"], "recommended_setpoint": 0.0,
   "lock_group": "全程锁温", "lock_group_segments": [1, 2],
   "shared_setpoint": 0.0},
  {"segment_no": 2, "name": "末端配送", "hours": 4.0, "ok": true,
   "temperature_segments": [{"t_min": 0.0, "t_max": 2.0}],
   "matched_zones": ["冷藏区"], "recommended_setpoint": 0.0,
   "lock_group": "全程锁温", "lock_group_segments": [1, 2],
   "shared_setpoint": 0.0}
]
```

若各段可行温度段的交集为空，则不存在共享设定点，该装箱组合被拒绝——
例如把上例「末端配送」的温区改为只有常温区 [15,25]℃，一批允许温区
[2,20]℃ 的「鲜花」每段单独都可覆盖，但没有任何温度能同时满足两段，
整批不予装载，`unloaded` 条目报告首个失败组及涉及段：

```json
{
  "batch_no": 1,
  "name": "鲜花",
  "units": 8,
  "volume": 2.4,
  "reason": "锁温组「全程锁温」(第 1 段「干线冷链」、第 2 段「末端配送」)要求共用一个设定点,但允许温区 [2.0, 20.0]℃ 在各段可行温度段的交集为空,不存在共享设定点,任何装箱组合均不可用",
  "failed_lock_group": "全程锁温",
  "failed_lock_segments": [
    {"segment_no": 1, "name": "干线冷链"},
    {"segment_no": 2, "name": "末端配送"}
  ]
}
```

### 单温区故障容灾评估

`POST /api/zone-failure-assessment`，请求体与 `/api/plan` **完全相同**
（评估基于同一输入快照，基准方案按原规则计算且不被修改）：

```bash
curl -X POST http://localhost:8000/api/zone-failure-assessment \
  -H "Content-Type: application/json" \
  -d '{
    "box_capacity": 12,
    "segments": [
      {"name": "干线冷链", "hours": 14,
       "zones": [{"name": "冷冻区", "t_min": -18, "t_max": -2},
                 {"name": "冷藏区", "t_min": 0, "t_max": 8}]},
      {"name": "末端配送", "hours": 4,
       "zones": [{"name": "冷藏区", "t_min": 0, "t_max": 8},
                 {"name": "常温区", "t_min": 15, "t_max": 25}]}
    ],
    "cargoes": [
      {"name": "三文鱼", "units": 10, "unit_volume": 0.4, "t_min": -2, "t_max": 2},
      {"name": "冷冻牛排", "units": 5, "unit_volume": 0.8, "t_min": -22, "t_max": -15}
    ]
  }'
```

响应（节选，场景已按 未装载件数、额外箱数、段序号、温区序 稳定排序）：

```json
{
  "input_fingerprint": "883221ac9970daa2…",
  "baseline": {"boxes_used": 1, "loaded_units": 10, "unloaded_units": 5,
               "total_cargo_units": 15, "all_transportable": false,
               "transport_hours": 18.0, "segment_mode": true},
  "scenarios": [
    {"segment_no": 2, "segment_name": "末端配送", "zone_index": 1, "zone_name": "冷藏区",
     "zone": {"name": "冷藏区", "t_min": 0.0, "t_max": 8.0},
     "transportable": false, "boxes_used": 0, "extra_boxes": -1, "loaded_units": 0,
     "newly_unloaded": [{"batch_no": 1, "name": "三文鱼", "units": 10}],
     "newly_unloaded_units": 10,
     "first_conflict_reason": "运输段「末端配送」(第 2 段,4.0h)的可用温区 (常温区) 与允许温区 [-2.0, 2.0]℃ 均无交集,全程温度无法保证"},
    {"segment_no": 1, "segment_name": "干线冷链", "zone_index": 1, "zone_name": "冷冻区",
     "zone": {"name": "冷冻区", "t_min": -18.0, "t_max": -2.0},
     "transportable": true, "boxes_used": 1, "extra_boxes": 0, "loaded_units": 10,
     "newly_unloaded": [], "newly_unloaded_units": 0, "first_conflict_reason": null}
  ],
  "summary": {"scenario_count": 4, "transportable_count": 3, "failed_count": 1,
              "max_newly_unloaded_units": 10, "max_extra_boxes": 0},
  "volume_unit": "体积单位"
}
```

上例中「冷冻牛排」在基准方案里就因末端无冷冻温区而未装载；末端「冷藏区」
失效后，基准已装的 10 件三文鱼失去唯一可用温区，场景不可运输
（`newly_unloaded` 精确列出受影响的批次与件数）。

### 容灾评估响应字段

| 字段 | 说明 |
| --- | --- |
| `input_fingerprint` | 输入快照的 SHA-256 指纹；输入变化后前端据此将旧结果标记为已过期 |
| `baseline` | 基准方案摘要：箱数、已装/未装件数、是否全程可运输、总时长、是否分段模式 |
| `scenarios[]` | 每个「运输段 × 温区」故障场景，按未装载件数、额外箱数降序，段序号、温区序升序稳定排序 |
| `scenarios[].segment_no` / `segment_name` | 失效温区所属运输段序号与名称 |
| `scenarios[].zone_index` / `zone_name` / `zone` | 失效温区在段内的序号（1 起）、名称与温度区间 |
| `scenarios[].transportable` | 基准已装载货物在该场景下是否仍能全部装载（无新增未装载） |
| `scenarios[].boxes_used` / `extra_boxes` | 场景箱数与相对基准的额外箱数（可为负） |
| `scenarios[].loaded_units` | 场景已装件数 |
| `scenarios[].newly_unloaded[]` | 由已装载变为未装载的批次：`batch_no`、`name`、`units`（差值件数） |
| `scenarios[].newly_unloaded_units` | 新增未装载总件数 |
| `scenarios[].first_conflict_reason` | 首个新增未装载批次的冲突原因；无新增未装载时为 `null` |
| `summary` | 场景总数、仍可运输/不可运输场景数、最大新增未装件数、最大额外箱数 |

### 实测温度复核

`POST /api/temperature-review`，请求体携带 **生成方案时的输入快照**
（`plan_input`，与 `/api/plan` 相同）、箱号、实测温度 CSV 文本，以及可选的
**测温仪器绝对误差** `instrument_error`（℃，非负，默认 0）：

```bash
curl -X POST http://localhost:8000/api/temperature-review \
  -H "Content-Type: application/json" \
  -d '{
    "plan_input": {
      "transport_hours": 4,
      "box_capacity": 12,
      "zones": [{"name": "宽温区", "t_min": -25, "t_max": 25}],
      "cargoes": [
        {"name": "三文鱼", "units": 2, "unit_volume": 0.4,
         "t_min": 0, "t_max": 10,
         "preferred_t_min": 2, "preferred_t_max": 4, "max_degree_hours": 30},
        {"name": "牛排", "units": 2, "unit_volume": 0.8,
         "t_min": -5, "t_max": 5}
      ]
    },
    "box_no": 1,
    "csv": "time_seconds,temperature\n0,0\n3600,10\n7200,20\n10800,10\n14400,0\n",
    "instrument_error": 1.0
  }'
```

全程 4h = 14400 秒，温度 0→10→20→10→0℃ 线性变化，仪器误差 ±1℃。
三文鱼允许温区 [0,10]℃：实测温度超过 11℃ 时误差带才 **整体** 越界
（3960~10440 秒为确定超界），实测温度超过 9℃ 时误差带已 **接触** 界外
（3240~11160 秒为可能超界）；度时按误差带到首选区间 [2,4]℃ 的最近/最远
距离积分，得 22.6~29.8 ℃·h（实测度时 26.0），预算 30 ℃·h 时上界也不超预算，
判定 **确定未超限**。响应（节选）：

```json
{
  "input_fingerprint": "6e374770731f0a95…",
  "box_no": 1,
  "transport_hours": 4.0,
  "duration_seconds": 14400.0,
  "sample_count": 5,
  "instrument_error": 1.0,
  "batches": [
    {"batch_no": 1, "name": "三文鱼", "units": 2,
     "allowed_t_min": 0.0, "allowed_t_max": 10.0,
     "within_limits": false, "certainly_within_limits": false,
     "excursion_count": 1, "possible_excursion_count": 3,
     "excursions": [
       {"start_s": 3960.0, "end_s": 10440.0, "duration_s": 6480.0,
        "min_temperature": 11.0, "max_temperature": 20.0,
        "band_min_temperature": 10.0, "band_max_temperature": 21.0, "side": "high"}
     ],
     "possible_excursions": [
       {"start_s": 0.0, "end_s": 360.0, "duration_s": 360.0,
        "min_temperature": 0.0, "max_temperature": 1.0,
        "band_min_temperature": -1.0, "band_max_temperature": 2.0, "side": "low"},
       {"start_s": 3240.0, "end_s": 11160.0, "duration_s": 7920.0,
        "min_temperature": 9.0, "max_temperature": 20.0,
        "band_min_temperature": 8.0, "band_max_temperature": 21.0, "side": "high"},
       {"start_s": 14040.0, "end_s": 14400.0, "duration_s": 360.0,
        "min_temperature": 0.0, "max_temperature": 1.0,
        "band_min_temperature": -1.0, "band_max_temperature": 2.0, "side": "low"}
     ],
     "preferred_t_min": 2.0, "preferred_t_max": 4.0,
     "measured_degree_hours": 26.0,
     "degree_hours_lower": 22.6, "degree_hours_upper": 29.8,
     "max_degree_hours": 30.0,
     "remaining_budget": 4.0,
     "remaining_budget_lower": 0.2, "remaining_budget_upper": 7.4,
     "budget_exceeded": false, "budget_verdict": "within"},
    {"batch_no": 2, "name": "牛排", "units": 2,
     "allowed_t_min": -5.0, "allowed_t_max": 5.0,
     "within_limits": false, "certainly_within_limits": false,
     "excursion_count": 1, "possible_excursion_count": 1,
     "preferred_t_min": null, "preferred_t_max": null,
     "measured_degree_hours": null,
     "degree_hours_lower": null, "degree_hours_upper": null,
     "max_degree_hours": null,
     "remaining_budget": null,
     "remaining_budget_lower": null, "remaining_budget_upper": null,
     "budget_exceeded": null, "budget_verdict": null}
  ],
  "summary": {"batch_count": 2,
              "batches_with_certain_excursions": 2,
              "batches_with_possible_excursions": 2,
              "total_certain_excursions": 2,
              "total_possible_excursions": 4,
              "budget_checked_count": 1,
              "budget_certain_exceeded_count": 0,
              "budget_possible_exceeded_count": 0,
              "all_within_limits": false,
              "all_certainly_within_limits": false}
}
```

`instrument_error` 省略或填 0 时，可能超界区间与确定超界区间完全相同、
度时下界=上界=实测度时，结果与不考虑仪器误差的原复核一致（原字段
`batches_with_excursions` / `total_excursions` / `budget_exceeded_count`
仍保留输出）。把三文鱼预算改为 25 ℃·h（落在度时范围 22.6~29.8 内）时
`budget_verdict` 为 `possible_exceeded`（**可能超限**）；改为 10 ℃·h
（小于下界 22.6）时为 `certain_exceeded`（**确定超限**，且 `budget_exceeded`
为 `true`）。

### 实测温度复核响应字段

| 字段 | 说明 |
| --- | --- |
| `input_fingerprint` | 输入快照的 SHA-256 指纹；输入变化后前端据此将旧复核标记为已过期 |
| `box_no` / `transport_hours` / `duration_seconds` | 复核箱号、全程时长（h）与全程秒数 |
| `instrument_error` | 本次复核使用的测温仪器绝对误差（℃，默认 0） |
| `sample_count` | 通过校验的采样记录数 |
| `batches[].allowed_t_min` / `allowed_t_max` | 该批允许温区（硬边界） |
| `batches[].excursions[]` | **确定超界**区间（误差带整体越界）：`start_s` / `end_s`（起止秒数）、`duration_s`、`min_temperature` / `max_temperature`（实测温度极值）、`band_min_temperature` / `band_max_temperature`（误差带可能温度极值）、`side`（`high` 超上限 / `low` 超下限 / `both`） |
| `batches[].possible_excursions[]` | **可能超界**区间（误差带接触或跨越界外；包含确定超界区间），字段结构同上 |
| `batches[].within_limits` / `certainly_within_limits` | 是否无确定超界 / 是否连可能超界都没有（即可确定真实温度全程在允许温区内） |
| `batches[].excursion_count` / `possible_excursion_count` | 确定 / 可能超界区间数 |
| `batches[].measured_degree_hours` | 实测累计度时（℃·h，实测值距离积分 ÷ 3600）；未设首选区间为 `null` |
| `batches[].degree_hours_lower` / `degree_hours_upper` | 度时 **下界 / 上界**（误差带到首选区间最近/最远距离积分 ÷ 3600）；未设首选区间为 `null`，误差为 0 时均等于实测度时 |
| `batches[].max_degree_hours` / `remaining_budget` | 度时预算与按实测度时计算的剩余预算（可为负）；未设预算为 `null` |
| `batches[].remaining_budget_lower` / `remaining_budget_upper` | 预算剩余的最不利值（预算 − 上界）/ 最有利值（预算 − 下界）；未设预算为 `null` |
| `batches[].budget_verdict` | 预算判定：`certain_exceeded`（确定超限）/ `possible_exceeded`（可能超限）/ `within`（确定未超限）；未设预算为 `null` |
| `batches[].budget_exceeded` | 兼容字段：仅 `budget_verdict == "certain_exceeded"` 时为 `true`；未设预算为 `null` |
| `summary` | 批次数、确定/可能超界批次数与区间数、预算受检批次数与确定/可能超限批次数、是否全部批次无确定超界（`all_within_limits`）/ 无可能超界（`all_certainly_within_limits`）；另保留 `batches_with_excursions`、`total_excursions`、`budget_exceeded_count` 兼容字段 |

### 响应字段（新增部分）

| 字段 | 说明 |
| --- | --- |
| `summary.segment_mode` | 是否分段运输模式 |
| `summary.transport_hours` | 总运输时长（分段模式为各段时长之和） |
| `summary.all_transportable` | 全部装载且每箱全程逐段通过才为 `true` |
| `segments[]` | 回显规范化后的运输段（序号 / 名称 / 时长 / 温区 / `lock_group` 锁温组） |
| `boxes[].transportable` | 该箱是否全程逐段通过温区校验 |
| `boxes[].segment_checks[]` | 逐段校验结果：`ok`、该段可设定温度段、**逐段设定点** `recommended_setpoint`（箱内有预算批次时按"最大累计预算使用率最低"贪心选取，否则取最宽可行区间中点）、匹配温区，失败时附 `reason`；锁温组段额外包含 `lock_group`（组名）、`lock_group_segments`（共用设定点的段号列表）与 `shared_setpoint`（**共享设定点**），未分组段这三个字段为 `null` |
| `boxes[].heat_exposure[]` | 箱内各批热暴露核算：首选区间、`max_degree_hours`（预算）、`cumulative_exposure`（累计暴露量 ℃·h）、`remaining_budget`（剩余预算）、`usage_ratio`（使用率）、`exceeded`、`first_exceeded_segment_no` / `first_exceeded_segment_name`（首次超限段）；未填写首选区间/预算的批次对应字段为 `null` |
| `boxes[].heat_exposure_ok` | 箱内所有预算批次是否均在预算内（超预算的装箱组合不会出现在结果中） |
| `unloaded[].failed_segment_no` / `failed_segment_name` | 首次不兼容的运输段序号与名称（仅分段温度冲突时出现），`reason` 中含完整原因 |
| `unloaded[].cumulative_exposure` / `max_degree_hours` | 按逐段最优设定点的全程累计暴露与预算（仅热暴露超预算时出现） |
| `unloaded[].first_exceeded_segment_no` / `first_exceeded_segment_name` | 热暴露首次超限的运输段序号与名称（仅热暴露超预算时出现） |
| `unloaded[].failed_lock_group` / `failed_lock_segments` | 首个不存在共享设定点的锁温组名与涉及段列表（`segment_no` + `name`，仅锁温组冲突时出现），`reason` 中含完整原因 |

分段模式下，上面的「冷冻牛排」会因末端配送段无冷冻温区而整批不可运输，
响应中 `unloaded` 对应条目示例：

```json
{
  "batch_no": 2,
  "name": "冷冻牛排",
  "units": 5,
  "volume": 4.0,
  "reason": "运输段「末端配送」(第 2 段,4.0h)的可用温区 (冷藏区、常温区) 与允许温区 [-22.0, -15.0]℃ 均无交集,全程温度无法保证",
  "failed_segment_no": 2,
  "failed_segment_name": "末端配送"
}
```

## 十、项目结构

```
.
├── app/
│   ├── main.py        # FastAPI 入口、异常映射与静态资源挂载
│   ├── planner.py     # 温区兼容 + 容量约束 + 热暴露预算 + 锁温区段分箱算法
│   ├── resilience.py  # 单温区故障容灾评估(逐温区模拟失效并重规划)
│   ├── review.py      # 实测温度复核(CSV 校验、仪器误差带、确定/可能超界切段、度时上下界积分)
│   ├── handover.py    # 到货交接台账(交接单冻结、多行签收/退回整笔入账、撤销重放校验)
│   ├── db.py          # SQLite 连接与建表(WAL、外键、显式事务)
│   └── schemas.py     # 请求模型
├── static/
│   ├── index.html     # 单页前端
│   ├── styles.css
│   └── app.js
├── tests/
│   └── test_handover.py  # 台账 API 测试(python tests/test_handover.py)
├── Dockerfile
├── docker-compose.yml # 含 SQLite 初始化与 handover-data 持久化卷
├── .env.example
└── requirements.txt
```
