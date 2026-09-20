# 改库存 / 改售价插件（stock-edit）v0.2

> 懒人操作：**粘一列** 或 **直接改全店**；库存默认 5w，可改；每品默认 2.5 秒间隔。
> **本轮只到「照镜子」（`previewDraftSubmit`），不发 `submit.htm`** —— 第一发真提交要单独点头。

## 界面

```
操作      改什么 [库存|售价|库存+售价]  数值 [50000]  间隔 [2.5] 秒/品  最多 [0] 个  ☑照镜子
          [▶ 开始改全店]  [▶ 只跑下面这些]  [清空]
指定商品  粘一列料号或淘宝ID（一行一个）/ 料号,淘宝ID / 拖 csv·xlsx
进度      📦 改库存 3/9 · 当前 xxx · 通过1 跳过8 失败0 · 已结束
结果      [本次结果] [历史留痕]   料号 | 淘宝ID | 形态 | 项目 | 旧值 | 新值 | 结果
```

- **全店为主**（直接读当前激活账号的在售清单，不用先导表）；个别品用「只跑下面这些」
- 输入写法：`料号,淘宝ID` / 纯淘宝ID / **纯料号**（先查流水线 state，查不到走一次分流）
- **运行日志**不在面板底部——在工作台右上「运行日志」Tab（读 `manifest.log` = `runtime/stock_edit_run.log`）

## 后端 action

| action | 干什么 |
|---|---|
| `POST start` | 组参数 → **detach** 起 `stock_api.py`（`mode=shop|rows`）；参数：rows/qty/what/gap/limit/mirror |
| `GET progress` | 读 `runtime/progress.json`（`kind=库存`）——面板自刷 |
| `GET result` | 读 `runtime/stock_last.json` → 本次结果表 |
| `GET ledger` | 读 `runtime/stock_edit.csv` 尾部 → 历史留痕 |

**为什么 detach**：workbench 是 `ThreadingTCPServer`，挂着等不会卡死别人，但全店几千个跑几小时的请求体验很差。Popen + 轮询。
**浏览器锁**：由子进程 `stock_api.py` 自己拿，插件 server 不碰浏览器。

## 命令行

```bash
python stock_api.py --shop --qty 50000 --what stock --gap 2.5 --mirror [--limit N]
python stock_api.py --item 868303791275 --what price --mirror
python stock_api.py --batch runtime\_pl_tmp\x.csv --qty 50000 --mirror
python stock_api.py --rollback runtime\attr_snapshots\<快照>.json
python pricing.py            # 自校：定价规则 vs 新品定价表
```

`--what`：`stock` / `price` / `both`。`--gap` 默认 2.5 秒。`--no-resume` 不跳过已通过的。

## 字段与规则

- 库存：单 SKU 改 `quantity`；多 SKU 改每个 `sku[i].skuStock` + 顶层 `quantity`（= 各 SKU 之和）
- 售价：单 SKU 改 `price`；多 SKU 改每个 `sku[i].skuPrice` + 顶层 `price`；值 = 成本 × 倍数（见 `工具\定价规则.md`）
- **不动**：`globalStock`（计数方式）、`subStock`（开关）、`skuPostCouponPrice`（券后价）

## 护栏与留痕

- 守卫：顶层键数不变 + 变化点＝目标集合；回传前再复检一次；不过就**就地停手**
- 快照：`runtime/attr_snapshots/<淘宝ID>_<what>_<时间>.json`
- 留痕：`runtime/stock_edit.csv`（时间/账号/料号/淘宝ID/项目/旧值/新值/结果）
- **断点续跑**：按留痕跳过「本账号 + (淘宝ID, 项目) 已照镜子通过」的；掐掉后重跑接着跑

详细实证、坑、未验证项见 `工具\淘宝批量改库存.md`。
