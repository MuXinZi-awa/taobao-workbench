# -*- coding: utf-8 -*-
"""桌面壳 · 双击启动（原生窗口，用系统 WebView；不装浏览器壳）

打包后 exe 只装「壳」：workbench.py / paths.py / plugins / index.html 都躺在 exe 旁边，
内核仍按源码方式起（runpy），所以插件热插拔、改面板即可生效的性质原样保留。

端口固定 8900：界面里的接口地址是写死的，换端口会白屏——所以被占用时明确提示，不偷偷换。
"""
import os
import runpy
import socket
import sys
import threading
import time

FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
PORT = 8900
URL = "http://127.0.0.1:%d/" % PORT


def say(title, msg):
    """打包后没有控制台，弹窗才算「明确提示」"""
    try:
        print("[%s] %s" % (title, msg))
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, title, 0x00000040)
    except Exception:
        pass


def port_open():
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def start_server():
    sys.path.insert(0, HERE)
    runpy.run_path(os.path.join(HERE, "workbench.py"), run_name="__main__")


def wait_server(seconds=20):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if port_open():
            return True
        time.sleep(0.25)
    return False


def main():
    wb = os.path.join(HERE, "workbench.py")
    if not os.path.isfile(wb):
        say("无法启动", "找不到 workbench.py。\n它必须和本程序放在同一个文件夹里：\n" + HERE)
        return 2

    if port_open():
        say("工作台已在运行",
            "端口 %d 已被占用——工作台可能已经开着了。\n\n本窗口直接连上去。\n"
            "（界面里的接口地址固定是 %d，所以不会自动换端口）" % (PORT, PORT))
    else:
        threading.Thread(target=start_server, daemon=True).start()
        if not wait_server():
            say("启动失败",
                "内核没能在 %d 端口起来。\n\n常见原因：端口被别的程序占用了。\n"
                "排除办法：先看看浏览器能不能打开 %s；或查任务管理器里占着 %d 的进程。"
                % (PORT, URL, PORT))
            return 3

    try:
        import webview
    except Exception as e:
        # 装不上 pywebview 时的退化：系统浏览器打开（功能不缺，只是没有原生窗口）
        import webbrowser
        webbrowser.open(URL)
        say("已用浏览器打开", "没装 pywebview，改用系统浏览器打开：\n%s\n\n%s" % (URL, str(e)[:90]))
        return 0

    webview.create_window("优化管理工作台", URL, width=1320, height=880, min_size=(980, 620))
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
