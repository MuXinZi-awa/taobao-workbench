# CHANGELOG · 任务计划（scheduler）

> 本文件依据 `manifest.json`、`README.md`、workbench 内核 `CHANGELOG.md`（插件版本跳变行）补记。
> 版本号只写有明确出处者；无出处的改动标「未记载」，不臆测。

## 0.1.4 — 2026-10-16
- `auto_campaigns` 改读 sqlite 状态库（`state_db.get_plans`），与 tg-monitor「刷新容量」同源，过滤非数字键；库读不到才回退 json。**为什么**：与 pipeline 同一处口径分裂（刷新写库、面板读 json）；读不到会让 `promo_batch` 误判「无可推计划」而拒绝执行。

## 0.1.3 — 2026-09-15
- 守护进程脱离工作台：`daemon_start()` 补 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`（原为 workbench 子进程，工作台退出会被连坐）。
- `_daemon_alive()` 补 pid 存活校验（原来只看 3 分钟内心跳，刚 kill 掉会误判为活）。

## 0.1.2 — 2026-09-14
- `promo_batch` 动作：计划组留空 → 自动取该账号「未满（count<500）」的计划（读 `plan_state_<member>.json`）；取不到拒绝执行并提示先刷容量。

## 0.1.1 — 2026-09-14
- 文件参数支持拖拽（任意位置文件上传到 `data/uploads/`）+ 已存文件下拉选择（不再按文件名在固定目录找）。
- 时间判定输入标准化（全角冒号/逗号/空格，`16：04` 也能触发）。
- 守护单例改文件锁（`msvcrt`，进程退出自动释放）。
- 面板新建/取消拆成两个函数。

## 0.1.0 — 2026-09-14
- 新增插件「任务计划」：定时驱动工作台功能，到点自动执行、不经过助手。
- 六种触发：间隔 / 一次性 / 每天（可多时间点）/ 每周（选星期）/ 每月（选号或月末）/ Cron。
- 动作注册表 + 批处理可传参（数据文件 / 每批数量 / 关联账号）+ 断点续跑（沿用 `tg_done` 机制）。
- 独立调度守护 `daemon.py`（零依赖，每分钟对齐 tick；单例；同分钟不重触发；一次性跑完自动停用）。

## 2026-09-19（版本号未记载）
- 起批切接口版：`promo_batch` 与面板起批的 script 从 `tuiguang_auto.py` 改指 `tuiguang_api.py`（`--campaigns` 留空由接口自取未满计划）。
- 注：daemon 为常驻进程，动作表缓存在内存，改配置后必须**重启守护**才生效。
