# 优化管理工作台（OH Workbench）

淘宝「连接器专业」店铺运营一体化工作台：插件化内核 + 本地优先，**零 pip 依赖、任意电脑可跑**。

## 快速开始

```bat
:: 双击或命令行启动（内核 http://127.0.0.1:8900）
工具\启动优化管理面板.bat
```

浏览器打开 `http://127.0.0.1:8900/`（无需联网，纯本地）。

> 依赖：`工具\_node_portable`（仅 JS 语法检查用）；预览 PDF 转图需 `fitz`（推广一键跑 runtime 自带 python 环境）。

## 架构（简单内核 + 插件丰富）

```
workbench/
├── workbench.py        # 内核（标准库 HTTP：8900）——静态/文件/搜索/插件加载
├── index.html          # 主界面：四区布局 + 设置体系
├── vendor/             # 开源库本地化（xlsx 等——不联网）
├── plugins/            # 插件目录（每插件一个文件夹，拖 zip 即装）
│   ├── dual-check/     # 查双百（单查/批量拖表，SheetJS 解析）
│   ├── pipeline/       # 流水线（上品/优化/推广状态机——v0.1 骨架）
│   └── tg-monitor/     # 推广监控台（11 计划组容量 + 单品/批量推广工程）
└── docs/
```

## 插件规范

- **zip 四件套**：`manifest.json` + `panel.html` + `server.py`（可选）+ `README.md`
- `manifest.json`：`{"id","name","version","desc","panel","has_server","icon"}`（**字段是 `panel` 不是 `entry`**）
- 拖入工作台设置-插件页即安装（zip），或直接放 `plugins/<id>/` 目录
- `server.py` 定义 `handle(action, params)`——内核每次调用**热重载**（改代码刷新生效）
- **主题跟随**：宿主切主题会 `postMessage` 广播 CSS 变量到插件 iframe——插件颜色请用 `var(--card, 纸色fallback)` 这类变量写法，别写死色
- 图标：manifest `icon` 字段放 emoji

## 主题

设置-界面：纸色（默认）/ 清新 / 暖阳 / **深邃蓝**（delve 黑白致敬）/ **莫兰迪**（雾蓝灰）。
主题 = 颜色变量 + 组件形态覆盖；插件 iframe 跟随主题（需插件用 CSS 变量）。

## 数据与路径

- 素材料号文件夹：`办公室工作\素材\产品素材\{料号}\`
- 推广记录权威全集：`办公室工作\数据\推广记录.csv`（0908 并入早期全量汇总）
- 推广池/运行状态/对账报告：`推广一键跑\runtime\`

## Git

- 公开仓：`github.com/MuXinZi-awa/taobao-workbench`（本工作台，含插件）
- 更新记录见 [CHANGELOG.md](CHANGELOG.md)

## 相关

- 运营经验/踩坑落盘：`OH-WorkSpace\_运维经验_推广素材插件_0908.md`
- 上品/推广脚本仓（私有 EXE）：店铺核心逻辑，不公开
