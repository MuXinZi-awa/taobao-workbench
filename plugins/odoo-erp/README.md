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


## 新品上架报表（已实现 · 只读）

**是什么**：模块 `ylhc_bs_product_report`（v19.0.0.3）里的模型 `product.inbound.report`（20 字段），
配套 `product.inbound.wizard`（`start_time`/`end_time` 必填 + `product_ids` 型号）。
入口菜单 xmlid：`ylhc_bs_product_report.product_inbound_wizard_action_window_menu`（「新品上架」）。

**报表语义（照平台截图原文）**
- 查「**历史首次入库**」落在指定区间内的产品：某品 5/1 首入、6/1 再入 → 查 6 月**不显示**它，只有查 5 月才有
- 只显示首次入库记录，且**该首次入库时间必须在区间内**；按首入时间**倒序**
- 入库时间 = **`stock.picking.date_done`**（Date of Transfer）——不是计划的 `scheduled_date`，也不是 `stock.move.date`（那是 Date Scheduled）
- 只统计 **`state = done`** 的单据

**口径（梓帆 2026-09-21 定案）：只算 `incoming`（收货单）**
dropship / internal / outgoing 一概不算——「其他不归我们」。以后别翻案，改口径先问。
（沿革：定案前拿 2026-08 比过三种口径——只算 incoming **77** / 加上代发货 78 / 全部 74，
差异几乎全来自 `dropship`；梓帆看完数字定了「只算入库」。）

**取数（只读实测到字段级）**

| 要什么 | 从哪取 |
|---|---|
| 首次入库时间 | `stock.picking.date_done`（样例 `WH/OUT/02010 date_done=2026-09-22 05:39:26`，计划 05:39:23） |
| 完成与否 | `stock.picking.state`（取值含 `done`） |
| 产品 / 数量 | `stock.move.picking_id` → picking；`product_id` / `quantity` |
| 入库类型 | `stock.picking.type.code` = `incoming`(Receipt) / `outgoing`(Delivery) / `internal` / `dropship` |

⚠️ `stock.move.price_unit` 抽样**全是 0**，不能拿它当单价（下面用估算）。

**实现**
- 面板：本插件加了一页（不新开插件）——起止日期 + 六个快捷（本月/上月/本季/上季/本年/去年）+ 型号（可空）
- 后端两个 action：`inbound?from&to&kw`（取数）、`inbound-export?from&to&kw`（落 CSV + XLSX）
- 落盘：`paths.DATA\新品入库报表\新品入库_<起>_<止>.csv/.xlsx`（路径走 paths，不写死）
- XLSX 是**手写**的（zip + 三段 XML）：内核里没有 openpyxl，标准库反而稳
- 全程只读（search_read / read），一个写接口都不碰

**单价 = 估算：成本 × 数量系数**（不是 ERP 里的真实报价）
- 规律**从 428 条成交单归纳**（销售单行，只读抽样）：价格/成本 的比值中位 **1.47**（四分位 1.25~1.78），且**随数量递减**
  | 数量 | 系数 |
  |---|---|
  | <100 | ×1.55 |
  | 100 ~ 1000 | ×1.50 |
  | 1000 ~ 10000 | ×1.31 |
  | ≥10000 | ×1.25 |
- **成本 ≤ 0 → 留空**，注明「这个品没有成本，估不出」（不许瞎填）
- 列名写 **`单价(估算)`**，备注里也写明不是真实报价
- 排掉过的两条路（记一笔，免得重踩）：
  - **成交价与产品自身价 `list_price` 只有 8% 相同** ⇒ 不能拿产品自身价当答案
  - **不解析 Odoo 的价表规则**（具体产品/类别/百分比/公式，以及那个 XML-RPC 调不到的私有方法）——不值当；
    相关代码（面板「价格表」下拉、server 端 `plid` 参数、`_pricelist_price` / `_categ_chain` / `pricelists` action）**已删除**，
    以后要真报价请翻 git 历史

**验收记录（2026-08，只读实测）**
- 结果 **77 个品** ✓（面板点「上月」同样 77）
- 抽 4 个品核对「报告的首入 = 该品全历史最小 `date_done`」，且不存在更早的 done 收货单 → 4/4 ✓
- 单价估算：77/77 有值（例：`HVSL800082B135` 成本 179.65 → 278.45）；无成本的品会留空并注明
- 导出：CSV + XLSX 落 `paths.DATA\新品入库报表\`；用 openpyxl 读回 = 78 行（1 表头 + 77）× 9 列，
  表头 `[首次入库日期, 型号, 名称, 编码, 类别, 数量, 单价(估算), 单价备注, 收货单]` ✓
