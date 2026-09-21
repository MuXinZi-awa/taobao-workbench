# -*- coding: utf-8 -*-
"""启动自检 · 说人话：缺什么、怎么办（不甩一屏红字）

壳启动时用它决定「弹窗说一句 / 继续跑」；内核用 /api/preflight 把它交给面板显示。
等级：
    ok    正常
    warn  能跑，但值得看一眼（比如业务目录还没填过）——不拦
    block 起不来（缺 WebView2 / 缺 workbench.py / 没法写日志）——拦

自检缝：环境变量 WB_SELFTEST 可指定强制失败项（只为验证提示文案，例如 no-webview2）。
"""
import os

WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
_WV2_KEYS = [
    (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "HKLM"),
    (r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "HKLM"),
]


def _selftest(which):
    return which in (os.environ.get("WB_SELFTEST") or "").split(",")


def webview2_version():
    """WebView2 运行时版本（空 = 没装）"""
    if _selftest("no-webview2"):
        return ""
    try:
        import winreg
        for path, hk in _WV2_KEYS:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, path) as k:
                        v, _t = winreg.QueryValueEx(k, "pv")
                        if v:
                            return str(v)
                except Exception:
                    pass
    except Exception:
        pass
    for base in (r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application",
                 os.path.join(os.environ.get("LOCALAPPDATA", ""),
                              "Microsoft", "EdgeWebView", "Application")):
        try:
            vs = [d for d in os.listdir(base) if d[:1].isdigit()]
            if vs:
                return sorted(vs)[-1]
        except Exception:
            pass
    return ""


def _writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, ".write_test")
        with open(p, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(p)
        return True
    except Exception:
        return False


def check(here, port=None, running=False):
    """返回自检项列表：[{key,label,level,detail,advice}]"""
    items = []

    def add(key, label, level, detail, advice=""):
        items.append({"key": key, "label": label, "level": level,
                      "detail": detail, "advice": advice})

    # WebView2：决定能不能开原生窗口
    ver = webview2_version()
    if ver:
        add("webview2", "WebView2 运行时", "ok", "已装 %s" % ver)
    else:
        add("webview2", "WebView2 运行时", "block",
            "系统里没找到 WebView2，原生窗口打不开",
            "装一次就好（免费，微软官方）：%s" % WEBVIEW2_URL)

    # 内核程序文件：exe 只装壳，源码在旁边
    if os.path.isfile(os.path.join(here, "workbench.py")):
        add("workbench", "内核程序", "ok", "workbench.py 在")
    else:
        add("workbench", "内核程序", "block",
            "找不到 workbench.py",
            "它要和本程序放在同一个文件夹里——是不是只拷了 exe，没拷整个文件夹？")

    # 日志/端口文件要写得进去（程序自己的目录，不碰业务数据）
    rt = os.path.join(here, "runtime")
    if _writable(rt):
        add("runtime", "程序日志目录", "ok", rt)
    else:
        add("runtime", "程序日志目录", "block",
            "写不了日志目录：%s" % rt,
            "把本程序放到有写权限的位置（别放在 C:\\Program Files 下），或给它写权限")

    # 插件目录（缺了功能就少，但能跑）
    if os.path.isdir(os.path.join(here, "plugins")):
        add("plugins", "插件目录", "ok", "plugins 在")
    else:
        add("plugins", "插件目录", "warn",
            "没有 plugins 目录，面板会是空的",
            "把整个文件夹一起拷过来（插件是热插拔的，不放进去就加载不到）")

    # 外部业务目录：不存在只是「还没填」，不是报错
    try:
        import sys
        sys.path.insert(0, here)
        import paths
        miss = []
        for label, key in (("素材", "mat_root"), ("数据", "data"), ("产品", "product"),
                           ("推广一键跑", "tg_root")):
            v = getattr(paths, key.upper(), "")
            if not (v and os.path.isdir(v)):
                miss.append("%s（%s）" % (label, key))
        if not miss:
            add("paths", "外部目录", "ok", "素材 / 数据 / 产品 / 推广一键跑 都在")
        else:
            add("paths", "外部目录", "warn",
                "这些目录还没指对：%s" % "、".join(miss),
                "打开 设置 → 通用 → 路径与目录，把对应一项改成实际位置即可（不影响别的功能）")
    except Exception as e:
        add("paths", "外部目录", "warn", "读路径配置失败：%s" % str(e)[:80],
            "打开 设置 → 通用 看一眼路径项")

    # 插件用的解释器：决定「点下去能不能跑起来」（打包后壳自己不能当解释器）
    try:
        import sys
        sys.path.insert(0, here)
        import paths as _p
        py = str(getattr(_p, "PYTHON", "") or "")
        if py and os.path.isfile(py):
            add("python", "插件用的 Python", "ok", py)
        else:
            add("python", "插件用的 Python", "warn",
                "没找到插件要用的 Python：%s" % (py or "（空）"),
                "面板能开、能看，但点「跑批」起不来——去 设置 → 通用 →「插件用的 Python」"
                "改成本机真实 Python 的路径（本机可用：%s）" % getattr(_p, "TG_PYTHON", ""))
    except Exception as e:
        add("python", "插件用的 Python", "warn", "读解释器配置失败：%s" % str(e)[:80],
            "打开 设置 → 通用 看一眼「插件用的 Python」")

    # 端口 / 已有实例（信息，不是故障）
    if port:
        add("port", "内核端口", "ok",
            ("已有实例在 %s 端口，本窗口连它" % port) if running else ("本次用 %s 端口" % port))

    return items


def blocks(items):
    return [it for it in items if it["level"] == "block"]


def warns(items):
    return [it for it in items if it["level"] == "warn"]


def one_line(it):
    return "%s：%s%s" % (it["label"], it["detail"], ("；" + it["advice"]) if it.get("advice") else "")
