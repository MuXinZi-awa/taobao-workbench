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
