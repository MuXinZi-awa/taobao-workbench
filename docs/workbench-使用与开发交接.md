# 优化管理工作台（OH Workbench）· 使用与开发交接

> 写给接手的会话/开发者：读完这一份就能改代码、加插件、不踩已知的坑。
> 作者：莉莉（lili）｜ 更新：2026-11-09 ｜ 对应内核版本：**0.8.37**

---

## 一、这是什么

淘宝店铺运营一体化工作台。**插件化内核 + 本地优先 + 零 pip 依赖**（只用 Python 标准库），浏览器界面，跑在本机 8900 端口。

一句话架构：**内核只做四件事（静态文件 / 文件读写 / 搜索 / 插件加载），业务能力全在 `plugins/<id>/` 里。**

几个核心原则：

- **不加第三方依赖到内核**——内核只用 `http.server` + 标准库，任何机器 copy 过去就能跑
- **每个插件是自治的一小块**：`manifest.json` + `panel.html` + `server.py`（可选）+ `README.md`
- **不做云端**，所有数据在本机磁盘上

---

## 二、跑起来

| 方式 | 命令 |
|---|---|
| **正式入口（用户用这个）** | 双击 `工具\工作台（双击启动）\OHWorkbench.exe` |
| 命令行/源码 | `工具\启动工作台.bat` |
| 停止 | `工具\停止工作台.bat` |
| 验证在跑 | `GET http://127.0.0.1:8900/api/ping` → `{"app":"workbench","core":"0.8.37",...}` |

浏览器打开 `http://127.0.0.1:8900/`。

> ⚠️ **别在别的地方再放一份工作台**。现在有两份：`工具\workbench\`（源码，**改代码改这里**）和 `工具\工作台（双击启动）\`（exe 的配套文件）。
> 两份不同步时会出现「补丁打在一个上、你双击的是另一个」的事故。
> 查同步：`python build_app.py --check-sync`；对齐：`python build_app.py --sync`。

---

## 三、目录与关键文件

```
工具/workbench/
├── workbench.py      # 内核（1128 行，标准库 HTTP :8900）——静态/文件/搜索/插件加载
├── index.html        # 主界面（四区布局 + 设置），每次请求现读磁盘，改完强刷即可
├── paths.py          # 全部路径配置（支持 WB_* 环境变量覆盖）
├── paths.local.json  # 本机路径覆盖（不进 git）
├── conn_store.py     # 连接库（账号/密码 DPAPI 加密）
├── state_db.py       # 状态库
├── preflight.py      # 启动自检
├── registry.json     # 插件市场清单（版本号要对齐 manifest！）
├── build_app.py      # 打包 / 同步 exe 配套
├── CHANGELOG.md      # 更新记录（66 KB，改了什么写这里）
├── vendor/           # 开源库本地化（xlsx 等，不联网）
└── plugins/<id>/     # 插件目录
```

**路径权威口**是 `paths.py` 的 `_DEFAULTS`，不要在各插件里硬编码路径：

```python
import paths
paths.get("mat_root")     # 素材根：办公室工作\素材\产品素材  ← 与上品流程同处
paths.get("data")         # 数据目录
paths.get("tg_root")      # 推广一键跑
paths.get("python")       # 插件跑批用的解释器（默认 推广一键跑\runtime\python.exe）
```

---

## 四、插件机制（核心，改插件必看）

### 4.1 四件套

```
plugins/<id>/
├── manifest.json   必需
├── panel.html      必需（面板界面）
├── server.py       可选（有后端才要）
└── README.md       建议（给下一个接手的人）
```

`manifest.json` 字段（**是 `panel` 不是 `entry`**）：

```json
{
  "id": "my-plugin",
  "name": "我的插件",
  "version": "0.1.0",
  "desc": "一句话说清它干什么",
  "panel": "panel.html",
  "has_server": true,
  "icon": "🔧",
  "log": "my_plugin.log"
}
```

### 4.2 后端约定

```python
def handle(action, qs):
    """action = 子路径，qs = query dict（GET/POST 的 ?k=v）"""
    if action == "status":
        return {"ok": True, ...}
    return {"ok": False, "error": "未知 action"}
```

调用方式：

```
POST /api/plg/<插件id>/<action>?k=v
```

**关键：`server.py` 每次调用都重新 `exec_module` 一次——改了代码不用重启，下一次调用就生效。**

> 反过来：**`panel.html` 走浏览器缓存**，改完必须 Ctrl+F5 强刷；`index.html` 每次现读磁盘，改完刷新就行。

### 4.3 前端约定

- 面板放在 iframe 里，**颜色必须用 CSS 变量**（`var(--card, #fff)` 这类），宿主切主题会 `postMessage` 广播变量；写死色会在换主题后变成一块突兀的白
- **⚠️ 事件绑定有个坑**：这个页面上 `addEventListener` + `data-*` 那套**有过不生效的先例**（点了不保存、不报错、控制台干净）。稳妥做法是用**内联 `onclick="fn(id)"`**，且 `fn` 必须是 `function` 声明（挂在 window 上）。
- **别用 `let` 声明被内联 onclick 引用的顶层变量**——内联处理器在元素→document→window 作用域链里执行，`let` 不挂 window，会 `ReferenceError` 且看不出来。用 `var`。

### 4.4 加一个新插件的最小步骤

1. `plugins/my-plugin/` 建目录，写上面四个文件
2. `registry.json` 里登记（版本号与 manifest 一致，否则面板会亮「可更新」，谁点了安装就把刚改的覆盖回去）
3. 刷新工作台 → 侧边栏出现
4. 装给别的机器：把目录打成 zip（四件套在 zip 根或单层目录），拖进「设置 → 插件」页

---

## 五、现有插件清单

| id | 名称 | 干什么 | 后端 |
|---|---|---|---|
| `pipeline` | ⚙ 流水线 | 上品/优化/推广一条龙状态机：拖清单 → 查询分流 → 属性 → 素材 → 整理 → 人工审核 → 上品/优化 → 推广 | 72 KB |
| `dual-check` | ✓ 查双百 | 单查料号 + 批量拖表查双百 | 10 KB |
| `attr-fill` | 🧩 补必填 | 老品补必填（品牌/接口类型）→ 复查「错误(0)」→ 提交 | 6.5 KB |
| `stock-edit` | 📦 改库存 | 改库存/售价，照镜子确认，提交走 CLI 真点击 + 读回 | 7.8 KB |
| `adreport` | 📊 报表 | 万相台报表台：账户概览/计划/场景/日走势 | 24.9 KB |
| `tg-monitor` | 📡 推广监控 | 计划组容量看板 + 推广进度两源合并 + 登录态大灯 | 20 KB |
| `promo-stock-check` | 🔍 推广库存巡检 | ERP 在手 × 万相台单元对撞：在手 0 且在推 → 该停；有货但停 → 该开 | 10.7 KB |
| `order-report` | 📦 订单看板 | 已卖出宝贝状态分布 + 订单列表/详情（只读） | 51.7 KB |
| `odoo-erp` | 🏢 ERP 数据 | Odoo 只读查询（xmlrpc）：料号 → 售价/库存/成本/品牌 | 14.5 KB |
| `scheduler` | 🕒 任务计划 | 定时驱动工作台功能（间隔/每天/每周/Cron），不经过助手 | 14.6 KB |
| `xyq-img` | 🖼 AI 主图 | 小云雀免费生图：料号 + 模板 + 参考图 → 3:4 主图 | 6.4 KB |

---

## 六、连接库（多店铺隔离，重要）

`conn_store.py` 管账号，密码用 Windows DPAPI 加密。

```python
import conn_store
c = conn_store.active_of("taobao")   # 或 "odoo"
# → {"id","type","name","url","dbname","username","secret"(明文),"profile","member_id",...}
conn_store.activate(cid)             # 切活动连接
```

**铁律：任何跟店铺相关的功能，都要按 `member_id` 隔离数据。**

理由：同一台机子会切主店/二店/三店，`member_id` 不同。写死的路径或缓存会把 A 店的数据当 B 店的用，而且不报错。

标准做法：

```python
mid = str(c["member_id"]) if c else "shared"
d = os.path.join(RT_ROOT, mid)       # 数据按账号分目录
```

而且**每次跑批前先校验当前登录的店铺**，不一致就停（`pipeline` 里有「店铺防线」，别删）。

---

## 七、已知的坑（每条都是踩出来的，别重踩）

### 7.1 内核线程里跑不了 asyncio / playwright

现象：`name 'base_events' is not defined`，playwright 一碰就死。

原因：`http.server` 的 handler 线程里 `asyncio.run()` 会炸。

**做法：所有浏览器活都起子进程跑**（`subprocess.run/popopen`），插件只负责起进程、读结果。

另有一条：**子进程不能拿管道接 playwright 的输出**。Chrome 用 `--remote-debugging-pipe` 起，父子进程 stdio 被管道接管时 Chrome 启动阶段直接退出（`exitCode=21`），看起来像锁/登录问题，其实不是。改成写临时文件再读。

### 7.2 浏览器"只连不开"

```python
b = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
```

**不要自己 `launch_persistent_context`**——会和正在跑的批次抢 profile 锁，而且登录态得重来。

然后：
- **不关浏览器、不关 context、不关别人的标签**
- 自己开的标签用完就关（**标签堆到 10 个以上，页面操作会静默失效**，所有操作看起来成功、实际全无效）
- 不按进程名杀 chrome（会把用户正在看的窗口一起杀掉）

### 7.3 遮罩弹窗（最阴的一个）

平台经常弹全屏广告/活动弹窗。它**不报错**，只是把鼠标事件吃掉——脚本「点了但没反应」，日志还挺好看。

关它的判据链（按优先级，2026-10-29 实测）：

1. **元素有 `mx-click` 且值含 `cancel`/`close`/`dismiss`** ← 最直白，例：`mx-click="cnt_dlg_2165cancel()"`
2. **`button` 包 `i.mx-iconfont` 且文本为空**，面积最小的那个
3. **裸 `i.mx-iconfont` + 祖先链上有「全屏(>80% 视口)且 z-index≥1000」的层**
4. **点视口四角**（点遮罩外面等于取消）
5. **`pointer-events:none`**（不隐藏，用户仍可见，鼠标穿透）

**不要做的**：
- 按 `class` 找——后半段是每次构建的随机 hash（`bXMILLdiFx` / `icQRQlpkc`），每次都不一样
- 按 `×`/`✕` 文本找——图标靠字体渲染，文本是空的
- 用 ESC——关不掉
- 用 `[class*=mxgc-icon]` 这种宽类——会命中左侧菜单图标
- 只看 `z-index` 不看尺寸——侧边栏容器也有高 z-index，会误选导航图标

实现见 `plugins/promo-stock-check/_worker.py` 的 `dismiss_overlays()`，可直接抄。

> **配合策略**：每批操作前都清一次（不只是进页面时清一次）。

### 7.4 前端两个静默失败

见 §4.3：`addEventListener`/`data-*` 那套在这个页面有不生效先例；内联 `onclick` 引用的变量不能用 `let`。

两种都是「点了没反应、控制台干净」，排查成本极高。

### 7.5 版本号双处

`registry.json` 的版本和 `manifest.json` 的版本不一致时，面板会亮「可更新」——**谁手贱点了安装，`_pkgs` 里的旧 zip 就把你刚改的插件盖回去**。改版本时两处一起改。

（历史遗留：2026-11-09 查出来 `pipeline` 和 `scheduler` 早就对不上了，已修。新加插件时记得同时登记 registry。）

### 7.6 长任务会随平台重启被带走

用 `Start-Process` 起的进程是当前进程的子进程，上层重启就一起死。要脱离进程树用 WMI：

```powershell
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = "..."}
```

（`subprocess.Popen(creationflags=0x00000008)` 在 Python 里等价：`DETACHED_PROCESS`）

### 7.7 判断成败只认「读回」

- HTTP 200、`success.htm`、脚本自己打的「成功」都**不算**
- 提交成功 ≠ 生效；页面出现某个词 ≠ 那件事发生了
- **落库状态要重新查一次**（接口查 / 重开页面读），不一致就以读回为准
- 同类失败连续 3 次 = 必失败，停手查根因，别重试

---

## 八、红线

1. **不改内核去迁就某个插件**——插件是外挂，内核保持简单
2. **不在插件里硬编码账号/密码/member_id**——一律走 `conn_store`
3. **不把 `wb_*` 这类浏览器 profile 目录提交进 git**（里面有 cookie/登录态）
4. **`git add .` 一律不用**——手动列文件；`.bak_*` / `_snap` / `__pycache__` 不许进库
5. **改文件前先落快照**（`xxx.py.bak_MMDD_描述`）
6. **注释只写「代码无法自述的约束 / 反直觉逻辑 / 外部系统行为」**；不写修改原因和日期，那归 CHANGELOG

---

## 九、未完成 / 待办（截至 2026-11-09）

| 项 | 状态 |
|---|---|
| 工作台推 GitHub | 上次推到 `a9d78dd`，之后改了 3 个文件 + 新增 7 个未跟踪，**没提交**（见下） |
| `promo-stock-check` 批量启用 | 单元级验证过（读回 `start 1/1`），**批量没跑过** |
| 遮罩自愈 | 假弹窗三种场景全 PASS，**真弹窗只验过一次**，遇到新变体要补 DOM |
| 接 odoo 模块 | 老板要「工作台做成模块接入 odoo」。方案：odoo 当壳（任务模型/看板）、工作台当手（执行）；长任务必须留外部进程，塞进 odoo worker 必超时。**8900 目前没有鉴权，对外前必须先加 token + IP 白名单** |
| 双份目录同步 | 每次改完源码记得 `build_app.py --check-sync` |

**未提交的具体文件（截至 2026-11-09）**：

```
 M index.html
 M plugins/pipeline/scripts/_mtop_api.py
 M plugins/pipeline/server.py
?? paths.local.json.example
?? plugins/pipeline/_bak_20261023_150856_server.py      ← 快照，不该入库
?? plugins/pipeline/_probe_state.py                     ← 诊断脚本
?? plugins/pipeline/scripts/_onsale_enum.py
?? plugins/pipeline/scripts/_shop_dupcheck.py
?? plugins/pipeline/scripts/_shop_triage.py
?? plugins/promo-stock-check/                            ← 新插件，整目录待入库
```

��交前记得：**别 `git add .`**，按目录/文件手动 add；`_bak_*` / `_probe_*` 先移走或加进 `.gitignore`。

**2026-11-09 已修**：`registry.json` 里 `pipeline` 0.2.17→0.2.24、`scheduler` 0.1.3→0.1.4 已对齐；`promo-stock-check` 已补登记（备份 `registry.json.bak_1109_align`）。

---

## 十、参考

- 更新记录：`CHANGELOG.md`
- 运营经验总纲：`OH-WorkSpace\工具\运维经验-总纲.md`
- 公开仓：`github.com/MuXinZi-awa/taobao-workbench`

---

## 附：写文档给 AI 会话用的一句话心得

上面这些坑，**每一条都是花了半天以上才定位的**，而且大多不报错、日志好看、只是结果不对。如果你接手后遇到「操作成功但没生效」，先按 §7 逐条排一遍，不要从零开始猜。
