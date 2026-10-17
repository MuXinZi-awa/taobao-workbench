# CHANGELOG · ERP 数据（odoo-erp）

> 本文件依据 `manifest.json`、`README.md`、`_交接_0929_工作台插件市场.md`、workbench 内核 `CHANGELOG.md` 补记。
> 版本号只写有明确出处者；无出处的改动标「未记载」，不臆测。

## 2026-09-21 ~ 09-23（版本号未记载，manifest 仍 0.1.0）
- 面板加一页「新品上架报表」（不新开插件）：起止日期 + 六个快捷（本月/上月/本季/上季/本年/去年）+ 型号（可空）。
- 后端两个 action：`inbound?from&to&kw`（取数）、`inbound-export?from&to&kw`（落 CSV + XLSX）。
- 口径（2026-09-21 定案）：只算 `incoming`（收货单）；取 `stock.picking.date_done`（首次入库时间，倒序）、只统计 `state=done`。
- 单价 = 成本 × 随量递减系数（<100×1.55 / 100~1000×1.50 / 1000~10000×1.31 / ≥10000×1.25），列名标「单价(估算)」；成本 ≤0 留空。
- 落盘 `paths.DATA\新品入库报表\`；XLSX 手写（zip + 三段 XML，不依赖 openpyxl）；全程只读。
- 删除未走通的价格表规则相关代码（`plid` / `_pricelist_price` / `_categ_chain` / `pricelists` action）。

## 0.1.0 — 2026-09-10
- 新增插件「ERP 数据」：Odoo 只读查询——料号 → 售价 / 成本 / 库存 / 品牌 / 描述 / 分类。
- 只读约束：仅 `authenticate` / `search_read` / `search_count`，绝不 create/write/unlink。
- 依赖内核「设置 → 连接」里激活的 Odoo 连接；凭据经内核 `conn_store`（DPAPI 加密）读取。

## 2026-09-09（版本号未记载）
- 修：精确匹配开关常驻（原来随结果数消失，结果变 1 条就回不去）。
