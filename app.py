# -*- coding: utf-8 -*-
"""桌面壳 · 双击启动（原生窗口，用系统 WebView）

端口不写死：内核自己找可用端口（先试配置端口，被占则往后顺延，再不行交给系统分配），
并把实际端口写进 runtime/port.json。本壳先探这个文件 + /api/ping，认出已在跑的实例就
**复用并把它提到前台**——不抢端口、不起第二个。

窗口关掉就把内核带走（同进程内起的内核会随进程一起没了），不留幽灵进程占端口。

起不来一定说话：找不到 workbench.py / 内核没起来 / WebView2 缺失，都给中文提示 + 写日志。
"""
import ctypes
import json
import os
import runpy
import socket
import sys
import threading
import time
import urllib.request

FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)          # 打包后自己的目录不在默认搜索路径里
import preflight                       # noqa: E402

TITLE = "优化管理工作台"
PROBE_RANGE = [8900 + i for i in range(0, 12)]     # 认实例时的探测范围（端口可变，到这里找）
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def _log(msg):
    try:
        print(msg)
    except Exception:
        pass
    try:
        d = os.path.join(HERE, "runtime")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "startup.log"), "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _fix_stdio():
    """打成 windowed 的 exe 后没有控制台，sys.stdout/stderr 是 None——
    内核里的 print 会直接抛 AttributeError（现场表现就是「内核没起来」）。接管到文件。"""
    try:
        if sys.stdout is not None and sys.stderr is not None:
            return
        d = os.path.join(HERE, "runtime")
        os.makedirs(d, exist_ok=True)
        sink = open(os.path.join(d, "console.log"), "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = sink
        if sys.stderr is None:
            sys.stderr = sink
    except Exception:
        pass


def say(title, msg):
    """打包后没有控制台，弹窗才算「明确提示」；用带超时的弹窗，无人值守时不会一直卡着"""
    _log("[%s] %s" % (title, msg.replace("\n", " / ")))
    try:
        u = ctypes.windll.user32
        try:
            u.MessageBoxTimeoutW(0, msg, title, 0x00000040, 0, 120000)
        except Exception:
            u.MessageBoxW(0, msg, title, 0x00000040)
    except Exception:
        pass


def _port_file():
    """端口文件：优先用 paths 的定义（与内核一致），拿不到就按目录约定拼"""
    try:
        sys.path.insert(0, HERE)
        import paths
        return paths.PORT_FILE
    except Exception:
        return os.path.join(HERE, "runtime", "port.json")


def http_json(url, timeout=1.2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def ping(port):
    """固定探针：确认这个端口上跑的是工作台（不靠端口号认人）"""
    j = http_json("http://127.0.0.1:%d/api/ping" % port)
    return j if (isinstance(j, dict) and j.get("app") == "workbench") else None


def _alive(pid):
    """进程还在吗（pid 已死 / 拿不到句柄 → False）"""
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_uint()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259        # STILL_ACTIVE
    except Exception:
        return False


def find_instance():
    """可复用的实例——**三件套都要成立**：探针应答 ✓ + pid 还活着 ✓ + 它自称有窗口（ui=true）✓。
    缺一件就不算：只有源码方式起的那个（无窗口）不该被双击的壳当成主人
    ——不然壳会把窗口开完又自己关掉，用户看到的是「双击没反应」。"""
    cands = []
    try:
        with open(_port_file(), encoding="utf-8") as f:
            p = json.load(f).get("port")
        if isinstance(p, int):
            cands.append(p)
    except Exception:
        pass
    for p in PROBE_RANGE:
        if p not in cands and port_open(p):
            cands.append(p)
    for p in cands:
        j = ping(p)
        if not j:
            continue
        pid = j.get("pid")
        if not isinstance(pid, int) or not _alive(pid):
            _log("端口 %s 有应答但 pid=%s 已不在 → 忽略" % (p, pid))
            continue
        if j.get("ui") is not True:
            _log("端口 %s 是后台实例（无窗口）→ 不当它存在" % p)
            continue
        return j
    return None


def _mark_ui(port):
    """把自己标成「有窗口的实例」：不标的话，下一次双击会以为这是个后台实例。"""
    try:
        fp = _port_file()
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        d["ui"] = True
        d["ui_pid"] = os.getpid()
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        _log("标记 ui 失败：%s" % str(e)[:80])


def focus_window(pid):
    """把已有实例的窗口提到前台（找不到窗口返回 False）"""
    try:
        u = ctypes.windll.user32
        from ctypes import wintypes
        hits = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(h, _l):
            q = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(q))
            if q.value == pid and u.IsWindowVisible(h):
                n = u.GetWindowTextLengthW(h)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    u.GetWindowTextW(h, buf, n + 1)
                    if "工作台" in buf.value:
                        hits.append(h)
                        return False
            return True

        u.EnumWindows(cb, 0)
        if not hits:
            return False
        h = hits[0]
        u.ShowWindow(h, 9)                 # SW_RESTORE
        u.SetForegroundWindow(h)
        u.BringWindowToTop(h)
        return True
    except Exception as e:
        _log("聚焦失败：%s" % str(e)[:80])
        return False


def port_open(p, timeout=0.12):
    """端口上有人监听吗（阻塞式短超时：关闭的端口会立刻 refused，1 毫秒级）
    注意不能用 settimeout + connect_ex：那会让 socket 变非阻塞，返回的 10035 是「还在连」而不是「没开」。"""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", p))
        return True
    except Exception:
        return False
    finally:
        s.close()


def start_core():
    try:
        sys.path.insert(0, HERE)
        runpy.run_path(os.path.join(HERE, "workbench.py"), run_name="__main__")
    except Exception as e:
        import traceback
        _log("内核异常：%s\n%s" % (str(e)[:200], traceback.format_exc()[-1500:]))


def wait_core(seconds=30):
    """等【自己这个内核】把端口写出来并答探针。
    必须核对 pid == 自己：port.json 是整个程序共用的一个文件，
    要是另一个实例（比如源码方式起的后台实例）已经写了记录，只读文件就会以为
    「内核已起」而把窗口开到别人身上，然后自己关掉——用户看到的就是「双击没反应」。"""
    me = os.getpid()
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            with open(_port_file(), encoding="utf-8") as f:
                rec = json.load(f)
            p = rec.get("port")
            if rec.get("pid") == me and isinstance(p, int) and ping(p):
                return p
        except Exception:
            pass
        time.sleep(0.3)
    return None


def _install_crash_hooks():
    """崩了也要留现场：在别人机器上没人能现场调试，日志是唯一的眼睛。"""
    import traceback

    def hook(et, e, tb):
        _log("崩溃：%s\n%s" % (getattr(et, "__name__", "?"),
                              "".join(traceback.format_exception(et, e, tb))[-1500:]))
    try:
        sys.excepthook = hook
    except Exception:
        pass
    try:
        import threading as _th

        def thook(a):
            try:
                tb = "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback))
            except Exception:
                tb = str(a.exc_value)
            _log("线程崩溃：%s\n%s" % (getattr(a.exc_type, "__name__", "?"), tb[-1500:]))
        _th.excepthook = thook
    except Exception:
        pass


_MARK = {"ok": "✓", "warn": "⚠", "block": "✗"}


def _check_imports(extra=()):
    """打包后的导入自检：把 exe 实际会用到的模块逐个 import 一遍。
    壳是用 runpy 在运行时加载 workbench.py / conn_store.py 的，PyInstaller 静态分析
    看不见那些导入——所以「缺件」只能在打包后自己查（否则表现为：界面能开、一点就报错）。
    结果写 runtime/import_check.log（windowed exe 没有 stdout，构建脚本靠读文件）。"""
    _fix_stdio()
    import importlib
    names = ["sqlite3", "xmlrpc.client", "fitz", "webview",
             "json", "csv", "ctypes", "winreg", "http.server", "socketserver"]
    names += [x for x in extra if x]
    bad = []
    lines = []
    for m in names:
        try:
            importlib.import_module(m)
            lines.append("OK      %s" % m)
        except Exception as e:
            bad.append(m)
            lines.append("MISSING %s  (%s)" % (m, str(e)[:70]))
    lines.append("小计：%d/%d 可用%s" % (len(names) - len(bad), len(names),
                                      ("; 缺：%s" % ", ".join(bad)) if bad else ""))
    try:
        d = os.path.join(HERE, "runtime")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "import_check.log"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass
    for l in lines:
        _log("[导入自检] " + l)
    return 1 if bad else 0


def main():
    _fix_stdio()
    _install_crash_hooks()

    # 首次运行自检：缺什么、怎么办（一项一句中文，同时落盘一份）
    checks = preflight.check(HERE)
    for it in checks:
        _log("[自检] %s %s — %s%s" % (_MARK.get(it["level"], "?"), it["label"], it["detail"],
                                      ("；" + it["advice"]) if it.get("advice") else ""))
    hard = [it for it in preflight.blocks(checks) if it["key"] != "webview2"]
    if hard:
        say("开不起来", "\n\n".join("%s\n→ %s" % (it["detail"], it["advice"]) for it in hard))
        return 5
    no_wv = [it for it in preflight.blocks(checks) if it["key"] == "webview2"]

    run = find_instance()
    if run:
        pid, port = run.get("pid"), run.get("port")
        # 刚双击完第一下时对方的窗口可能还没建好——等一会儿再认，别急着自己开窗
        t0 = time.time()
        while time.time() - t0 < 12:
            if pid and focus_window(pid):
                _log("已有实例在 %s 端口，已把它提到前台，不再起第二个" % port)
                return 0
            time.sleep(0.5)
        # 找不到窗口 = 那个实例用不了（半死/无窗口）→ 当作没有实例，起自己的
        _log("端口 %s 上的实例找不到窗口 → 当作没有实例，起自己的窗口" % port)

    threading.Thread(target=start_core, daemon=True).start()
    port = wait_core()
    if not port:
        say("启动失败",
            "内核没起来，所以窗口没开。\n\n可以试这几步：\n"
            "1. 看看是不是有安全软件拦了（放行本程序）\n"
            "2. 单独跑一下同目录的 workbench.py 看报什么错\n"
            "3. 详情见同目录 runtime\\startup.log\n\n目录：\n" + HERE)
        return 3
    url = "http://127.0.0.1:%d/" % port
    _mark_ui(port)
    _log("内核已起：%s" % url)

    try:
        import webview
    except Exception as e:
        import webbrowser
        webbrowser.open(url)
        say("已用浏览器打开",
            "没装 pywebview，改用系统浏览器打开：\n%s\n\n（功能不缺，只是没有独立窗口）\n\n%s"
            % (url, str(e)[:90]))
        return 0

    if no_wv:
        # 缺 WebView2 不硬撑：先把话说清楚，再用浏览器把功能交到手
        import webbrowser
        webbrowser.open(url)
        say("已用浏览器打开（缺 WebView2）",
            "%s\n\n装完再双击本程序，就会回到独立窗口。\n\n本次已打开：%s"
            % (no_wv[0]["advice"], url))
        return 0

    try:
        webview.create_window(TITLE, url, width=1320, height=880, min_size=(980, 620))
        webview.start()
    except Exception as e:
        say("窗口起不来（多半是缺 WebView2）",
            "系统里没找到 WebView2 运行时，原生窗口打不开。\n\n"
            "装一次就好（免费，微软官方）：\n%s\n\n"
            "装完再双击本程序。\n\n（本次已改用浏览器打开）\n%s" % (WEBVIEW2_URL, str(e)[:120]))
        import webbrowser
        webbrowser.open(url)
        return 4

    # 窗口关闭 → 直接结束进程：同进程里起的内核随之消失，端口释放，不留幽灵进程
    _log("窗口已关闭，退出（内核随进程结束）")
    sys.stdout.flush() if hasattr(sys.stdout, "flush") else None
    os._exit(0)


if __name__ == "__main__":
    if "--check-imports" in sys.argv:
        # 打包后自检（构建脚本会调它）：后面跟的额外模块名也一起查
        sys.exit(_check_imports([a for a in sys.argv[1:] if not a.startswith("--")]))
    sys.exit(main() or 0)
