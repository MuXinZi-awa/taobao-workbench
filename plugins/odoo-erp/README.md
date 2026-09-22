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
