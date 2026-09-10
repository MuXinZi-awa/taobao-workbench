# 报表（adreport）· v0.2.0

万相台报表台：把"要点很深才看得到"的数据拉平到一处——**账户概览 / 推广计划(11) / 营销场景 / 日走势**，指标可点切走势、本期比上期、随范围联动。

## 面板

| 区块 | 内容 |
|---|---|
| 范围 | 今日 / 近7天 / 近30天 / 自定义(≤30天)；本期与"前一等长周期"对比 |
| 账户概览 | 10 张指标卡（花费/成交额/投产比/展现/点击/CTR/平均点击花费/转化率/成交笔数/加购）——**点卡切换走势主角** |
| 日走势 | 选中指标的折线（本期实线 vs 上期虚线）；**悬浮折点看准确值** |
| 推广计划 | 11 个计划（关键词1_已满 … 关键词11），**随范围切换** |
| 营销场景 | 关键词推广等，**随范围切换**（今日走实时） |
| 趋势解读 | 高点 / 挂零日 / 投产比 / 场景集中度 |

## 采集

- 面板「**刷新**」= 后台**静默采集**（headless，不弹浏览器），采完自动重载
- **惰性定时**：每天 **9:00 / 21:00** 后首次打开面板时，若该时点尚未采过 → 自动静默采一次
  （内核无调度器，故用"惰性触发"，不改内核；`SCHEDULE_HOURS` 在 `server.py` 可改）
- 手动（有头、可看过程）：双击 `推广一键跑\采集报表数据.bat`
- 采集脚本：`推广一键跑/_report_fetch.py`（`--headless` 可选）；输出 `runtime\report_data.json`
- 单次约 **60s**（1 账户走势 + 11 计划 × 3 范围 + 今日账户/计划/场景 + 场景 × 3）；实时数据有时点误差约 1min

## 本地库（sqlite3 · 累积存档）

`推广一键跑\runtime\report.db`——把采集数据**按日累积**（防店铺数据过期/被删）：

| 表 | 内容 |
|---|---|
| `daily` | 账户按日（date 主键 upsert，历史越攒越长）|
| `plan_snap` | 每次采集的计划区间快照（带时间/范围）|
| `scene_snap` | 场景区间快照 |

- 读取**优先走库**（`days` 从 `daily` 读，全历史、秒回）；库空才回退 JSON
- 采集脚本每次跑完自动入库（账户按日 upsert）
- 库文件在 `runtime/`（已被 .gitignore 保护，业务数据不入仓）

## server action

- `report` → `days / total / scenes / scenesByRange / plansByRange / today / plansToday / scenesToday / insight / refreshing`
- `refresh` → 后台启动静默采集

## 接口备忘（以后扩展用）

| 用途 | 接口 | 要点 |
|---|---|---|
| 账户/日/场景 | `POST report/query.json?bizCode=universalBP` | `queryDomains: date/account/scene` |
| 花费占比 | `report/chargeSum.json` | 同 body |
| 计划列表(11) | `campaign/findList.json?bizCode=onebpSearch` | `{statusList:["start","pause"], pageSource:"simplifyQueryList"}` |
| 单计划数据 | `report/query.json?bizCode=onebpSearch` | **`strategyCampaignIdIn:[id]`**（不是 campaignIdList！）+ `sourceList:["campaign_detail"]` |
| 今日实时 | 同上 | `fromRealTime:true`，日粒度不含当天 |
| csrfId | 页面请求正则 | `csrfId=([0-9a-f_]+)` |
