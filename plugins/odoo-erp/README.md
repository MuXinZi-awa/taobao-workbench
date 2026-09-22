# ERP 数据（odoo-erp）

Odoo ERP **只读**查询插件：输入料号 → 显示售价 / 成本 / 库存 / 品牌 / 描述 / 分类。

## 依赖
- 内核「设置 → 连接」里配置并**激活**一个 Odoo 连接（URL / 端口 / 数据库 / 账号 / 密码）
- 凭据经内核 `conn_store` 读取（DPAPI 加密存储）

## 安全
- **只读**：仅用 `authenticate` / `search_read` / `search_count` —— 绝不 create/write/unlink
- 生产库，任何写操作都不做

## 数据说明（epptc / jdt_v19_pro 实测）
- 料号存在 **`name`** 字段（`default_code` 为空）
- 27584 条产品；有售价 15013 / 有库存 563 / 带品牌 912 / 带电商描述 507
- 字段：`list_price`(售价) `standard_price`(成本) `qty_available`(库存) `product_information_brand`(品牌) `description_ecommerce`(英文描述) `categ_id`(分类)

## API
- `conn` → 当前激活 Odoo 连接信息
- `query?kw=料号&exact=1` → 产品数据（只读）
- `stats` → 产品总数

## 版本
- 0.1.0 · 2026-09-10 · 首版（料号查询 + 连接状态）


## 新品上架报表（预备中 · 未实现 · 只读探明）

**是什么**：模块 `ylhc_bs_product_report`（v19.0.0.3）里的模型 `product.inbound.report`（20 字段），
配套 `product.inbound.wizard`（`start_time`/`end_time` 必填 + `pricelist_id` 价格表 + `product_ids` 型号）。
入口菜单 xmlid：`ylhc_bs_product_report.product_inbound_wizard_action_window_menu`（「新品上架」）。

**报表语义（照平台截图原文，已定）**
- 查「**历史首次入库**」落在指定区间内的产品：某品 5/1 首入、6/1 再入 → 查 6 月**不显示**它，只有查 5 月才有
- 只显示首次入库记录，且**该首次入库时间必须在区间内**
- 入库时间 = **`stock.picking.date_done`**（Date of Transfer）——**不是**计划的 `scheduled_date`，也不是 `stock.move.date`（那是 Date Scheduled）
- 只统计 **`state = done`** 的单据
- 按首次入库时间**倒序**

**取数（只读实测）**
| 要什么 | 从哪取 | 实测 |
|---|---|---|
| 首次入库时间 | `stock.picking.date_done` | ✓ 字段存在；样例 `WH/OUT/02010 date_done=2026-09-22 05:39:26`（计划 05:39:23） |
| 完成与否 | `stock.picking.state` | ✓ 取值含 `done` |
| 产品/数量 | `stock.move.picking_id` → picking；`product_id` / `quantity` | ✓ 关联字段确认 |
| 入库类型 | `stock.picking.type.code` | ✓ `incoming`(Receipt) / `outgoing`(Delivery) / `internal` / `dropship` |
| 单价 | **`product.pricelist.item`**（6015 条规则可读：`applied_on`/`min_quantity`/`compute_price`/`fixed_price`） | ⚠️ **不要在 `stock.move.price_unit` 上取** —— 抽样 5 条全是 `0.0` |
| 价格表 | `product.pricelist`（74 张，Default + 客户表） | ✓ 可读 |

**规模抽样**（只 count，没全扫）：`stock.picking` 共 **6335** —— incoming **1144** / outgoing **1903** /
internal **2901**（另有约 387 无类型）。⇒ **只算 incoming 还是全算，结果差约 5 倍**，所以口径必须先定。

**★ 待定（等拍板，别自己定）**
1. **是否只算 `incoming`（入库类型）**：样例里出现 `WH/OUT/`、`WH/PICK/`，说明这张表范围可能不只入库
2. **取数走哪条**：(A) 自己按上面语义算（`stock.move`×`stock.picking` 取每产品 `MIN(date_done)`，**纯只读**，建议）
   还是 (B) 调它的 wizard 拿官方结果（最准，但要 create/write = 一次写）

**面板形态（设计，未实现）**：本插件内加一页（不新开插件）—— 开始/结束日期 + 快捷（本月/上月/本季/上季/本年/去年）
+ 产品型号（可留空）+ 价格表（可留空）；结果表列：日期 / 型号 / 名称 / 编码 / 类别 / 数量 / 单价 / 收货单；
导出 CSV/xlsx **直接落文件**（不依赖 Odoo 的「导出全部」）。

**已知实现难点**：Odoo 里算价表价的是**私有方法**（XML-RPC 调不到 `_get_product_price`），
所以选固定的价格表后要**读 item 规则自己算**（这套规则形态与 `工具\定价规则.md` 一致：`0_product_variant` + `min_quantity` 阶梯 + `fixed_price`）。

### 口径实跑对照（2026-08，只读实测 · 待梓帆定）

按「每产品全历史最小 date_done 落在 8 月内」算，同一月份三种口径：

| 口径 | 本月首次入库的产品 |
|---|---|
| ① 只算 `incoming`（收货单） | **77** |
| ② `incoming` + `dropship`（入库 + 代发货） | **78** |
| ③ 全部（含 internal 调拨 / outgoing 交货） | **74** |

三个数字两两交集 61 ⇒ ①独有 **16** 个、③独有 **13** 个。**差异几乎全来自 `dropship`（代发货）**：
- ①独有例：`13768626` 最早单是 `2025-11-01 dropship DS/00192` → 全算时它的「首入」在去年 11 月，8 月不显示
- ③独有例：`3901-2021` 最早单是 `2026-08-31 dropship DS/00378`，**还没入库** → 只算入库时它不算「入库新品」
- 另有少量 internal/outgoing（如 `15324981`、`174662-7` 最早是 WH/PICK + WH/OUT）

⇒ 真正要定的是**「代发货算不算货到了」**，而不是笼统的「要不要含内部调拨」。

### 价格表算价核对（只读实测）
- **固定阶梯一致** ✓：`ZE05-2022SCF`（表「深圳市熙游信息技术有限公司」）销售单行 qty=200000 → `price_unit=0.13`，与该表规则 `200000起=0.13` 吻合
- **另两条对不上** ✗：`828922-1`（单行 qty 5000，规则最低 30000 起）、`26-01-3114`（qty 189，规则最低 100000 起）
  ⇒ 价表价来源**不止** `0_product_variant` + `fixed`，还有按类别/全局/百分比/公式规则，或落回产品自身价
- ⇒ 「单价」列能做，但**必须把这几类规则都解析**；且库里有 **74 张价格表**，导出前得先选定一张（面板那个「价格表」就是干这个的）

### 实现与验收（2026-09-21）

**口径定案（梓帆 2026-09-21）**：**只算 `incoming`（收货单）**——dropship / internal / outgoing 一概不算，
「其他不归我们」。以后别翻案，改口径先问。

- 面板：本插件加了一页（不新开插件）——起止日期 + 六个快捷（本月/上月/本季/上季/本年/去年）+ 型号（可空）+ 价格表（可空）
- 后端两个 action：`inbound?from&to&kw&plid`（取数）、`inbound-export?...`（落 CSV + XLSX）
- 落盘：`paths.DATA\新品入库报表\新品入库_<起>_<止>.csv/.xlsx`（路径走 paths，不写死）
- XLSX 是**手写**的（zip + 三段 XML）：内核里没有 openpyxl，标准库反而稳
- 全程只读；`stock.move.price_unit` 是 0，不碰它

**单价怎么算（实测结论）**
- 优先级：具体产品 > 产品 > 类别（含父类）> 全局；同级取 `min_quantity` 最大且 ≤ 数量的那条
- **本库 6015 条规则全是 `fixed`**（`percentage` / `formula` 各 **0** 条）⇒ 现有数据下 fixed 一条路就够；
  percentage / formula 已实现但**库里没有这种规则，未实测**
- 没命中阶梯时**回落产品自身价**（与 Odoo 一致：`828922-1` qty=5000、阶梯从 30000 起，销售单行价 0.08 = 它的 `list_price`）
- 带取整/上下限的 formula、或 `base=pricelist` 的规则 → **留空并注明原因**（不瞎填）
- ⚠️ 销售单行不能总当参照：`26-01-3114` 的旧单是 2.0，而现规则 qty=1600 是 2.1（改价前的历史单）

**验收记录（2026-08，只读实测）**
- 结果 **77 个品** ✓（与只算 incoming 的口径计算一致）
- 抽 4 个品核对「报告的首入 = 该品全历史最小 date_done」，且不存在更早的 done 收货单 → 4/4 ✓
- 单价：表 Default → 77/77 有值（21 行命中阶梯、其余回落自身价并注明）
- 导出：CSV 9181 B / XLSX 5804 B，用 openpyxl 读回 → sheet「新品入库」78 行 × 9 列，表头与列序对得上 ✓

### 单价口径（2026-09-21 更正：不陪价表玩）

梓帆口径：**别去解析 Odoo 那几类价格规则（不值当）**，直接从 ERP 成交数据归纳一个「差不多」的规律；
主菜是清单，单价只是附带，算不出就留空。

**从 428 条成交单（销售单行，只读抽样）归纳**：
- 价格/成本 的比值中位 **1.47**（四分位 1.25~1.78）；与 list_price 完全相等的只有 8%
- **比值随数量递减**：数量 <100 → 1.55 ｜ 100~1000 → 1.50 ｜ 1000~10000 → 1.31 ｜ ≥10000 → 1.25
- ⇒ 采用**成本 × 数量系数**这个简单可解释的公式（不查价格表、不解析规则）
- 成本 ≤ 0 的品 → **留空**，注明「这个品没有成本，估不出」
- 结果表/导出的列名写 **单价(估算)**，备注里也写明「不是 ERP 里的真实报价」

（旧版解析价表规则的那套代码保留在文件里但已不再走：_pricelist_price。以后要真报价再翻。）
