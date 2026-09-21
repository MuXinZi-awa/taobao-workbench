# 补必填插件（attr-fill）v0.1

> 老品补必填项：**读编辑页校验 → 补值 → 复查「错误(0)」→（可选）真提交**。
> 专治两个必填项卡提交：**品牌**（平台推荐值）/ **接口类型**（属性清单的采集原词）。

## 界面

```
操作     间隔 [2.5] 秒/品   最多 [0] 个   ☑补完即提交（真提交）  ☑断点续跑
         [▶ 开始补必填]  [清空]
指定商品 粘一列料号或淘宝ID（一行一个）/ 料号,淘宝ID / 拖 csv·xlsx
进度     🧩 补必填 3/9 · 当前 xxx · 错误(0) 2 跳过0 停手失败1 · 已结束
结果     [本次结果] [历史留痕]   料号 | 淘宝ID | 原错误 | 补了什么 | 现错误 | 结果
```

- **验收信号 = 编辑页自己渲染的「错误(N)」**（左栏 `.optimization-assistant-simple-bar`），
  **不是**照镜子（`previewDraftSubmit`）返回体——那里面没有校验结果。
- 补值：**品牌 ← `brand_map.csv` 的采集原始值**（TE Connectivity/BOSCH…，原样填、不做别名映射）；
  **接口类型 ← `属性清单.csv` 的采集原词**；取不到就**停手**，绝不模糊硬填。
  平台推荐值（`recommendInfo`）只当参考，不再用于填值。
- 可搜索下拉：只有**文本完全相等**才点候选，否则**回车走自由文本**（防「连接器 → 0.8条形连接器」这种被偷换）。
- **运行日志**在工作台右上「运行日志」Tab（读 `manifest.log` = `runtime/reqfill_run.log`）。

## 后端 action（`server.py`）

| action | 干什么 |
|---|---|
| `POST start` | 组参数 → **detach** 起 `reqfill_api.py --batch`；参数：rows/gap/limit/submit/resume |
| `POST progress` | 读 `runtime/progress.json`（`kind=补必填`）——面板自刷 |
| `POST result` | 读 `runtime/reqfill_last.json` → 本次结果表 |
| `POST ledger` | 读 `runtime/reqfill.csv` 尾部 → 历史留痕 |

**为什么 detach**：每品要开编辑页 + 真输入 + （可选）提交，挂在 HTTP 请求上等体验很差。Popen + 轮询。
**浏览器锁**：由子进程 `reqfill_api.py` 自己拿，插件 server 不碰浏览器。

## 命令行

```bash
python reqfill_api.py --item 868303791275 [--liaohao 1379029]
python reqfill_api.py --batch runtime\_pl_tmp\req.csv [--gap 2.5] [--limit N] [--no-resume]
python reqfill_api.py --item 855289576176 --submit     # 单品真提交
```

- `--submit`：真点「提交宝贝信息」（写进商品）。**批量提交已放行**（2026-09-21）；护栏 = 间隔 ≥2.5s + 断点续 + 每品读回。
- **断点续跑按模式判**：只填不提交的「错误(0)」不会留在平台（客户端状态，关页即失），
  所以 `fill` 模式跳过「已到错误(0)」的、`submit` 模式只跳过「已提交成功」的——否则一次只填不交会把后续提交跑全跳掉。

## 护栏与留痕

- 真提交前**必须** `错误(N)=0`，否则不提交（`run_item` 里卡住）。
- 留痕：`runtime/reqfill.csv`（时间/账号/料号/淘宝ID/原错误/补了什么/现错误/结果）。
- 快照：无（补必填不改整份表单；提交由平台自己的表单完成，不拼 body）。

实证、坑、未验证项见 `工具\老品必填项-品牌与接口类型.md`。
