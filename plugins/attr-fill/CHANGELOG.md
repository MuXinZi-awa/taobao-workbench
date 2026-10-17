# CHANGELOG · 补必填（attr-fill）

> 本文件依据 `manifest.json`、`README.md`、脚本 mtime（`推广一键跑/reqfill_api.py` 2026-09-21）补记。
> 版本号只写有明确出处者；无出处的改动标「未记载」，不臆测。

## 0.1.0 — 2026-09-21
- 新增插件「补必填」：老品补必填项（品牌 / 接口类型）——读编辑页校验 → 补值 → 复查「错误(0)」→（可选）真提交。
- 验收信号取编辑页自己渲染的「错误(N)」（`.optimization-assistant-simple-bar`），不取照镜子返回体。
- 补值来源：品牌 ← `brand_map.csv` 采集原始值；接口类型 ← `属性清单.csv` 采集原词；取不到就停手，不模糊硬填。
- 可搜索下拉只有文本完全相等才点候选，否则回车走自由文本。
- `POST start` 走 detach + 轮询（`rows/gap/limit/submit/resume`）；浏览器锁由子进程 `reqfill_api.py` 自己拿。
- 断点续跑按模式判：`fill` 模式跳已到「错误(0)」的，`submit` 模式只跳已提交成功的。
- 护栏与留痕：真提交前必须「错误(N)=0」；留痕 `runtime/reqfill.csv`。
- 批量提交放行（2026-09-21），护栏 = 间隔 ≥2.5s + 断点续 + 每品读回。
