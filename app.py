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


def find_instance():
    """已在跑的工作台：先读端口文件，再扫探测范围"""
    cands = []
    try:
        with open(_port_file(), encoding="utf-8") as f:
            p = json.load(f).get("port")
        if isinstance(p, int):
            cands.append(p)
    except Exception:
        pass
    for p in PROBE_RANGE:
        if p not in cands:
            cands.append(p)
    for p in cands:
        j = ping(p)
        if j:
            return j
    return None


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


def port_open(port):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
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
    """等内核把端口文件写出来并答探针；返回端口或 None"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            with open(_port_file(), encoding="utf-8") as f:
                p = json.load(f).get("port")
            if isinstance(p, int) and ping(p):
                return p
        except Exception:
            pass
        time.sleep(0.3)
    return None


def main():
    _fix_stdio()
    if not os.path.isfile(os.path.join(HERE, "workbench.py")):
        say("无法启动",
            "找不到 workbench.py。\n\n它会和本程序放在同一个文件夹里——"
            "是不是只拷了 exe，没拷整个文件夹？\n\n当前目录：\n" + HERE)
        return 2

    run = find_instance()
    if run:
        pid, port = run.get("pid"), run.get("port")
        # 刚双击完第一下时，对方的窗口可能还没建好（内核先答探针，窗口后出来）——
        # 等一会儿再认，别急着自己开窗（否则快速双击两次就变两个窗口）
        t0 = time.time()
        while time.time() - t0 < 12:
            if pid and focus_window(pid):
                _log("已有实例在 %s 端口，已把它提到前台，不再起第二个" % port)
                return 0
            time.sleep(0.5)
        _log("已有实例在 %s 端口（等了 12 秒仍没找到它的窗口），本窗口直接连上去" % port)
        url = "http://127.0.0.1:%d/" % port
    else:
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
    sys.exit(main() or 0)
