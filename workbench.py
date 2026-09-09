# -*- coding: utf-8 -*-
"""优化管理工作台 · 内核（0907 起——简单内核 + 插件丰富）
四区布局：顶菜单 / 左插件导航 / 中日志监控 / 右文件书桌（当前品联动）
启动：python workbench.py → http://127.0.0.1:8900/
零第三方依赖（http.server 标准库）——插件各自声明所需包
"""
import http.server
import socketserver
import json
import os
import glob
import re

BASE = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(BASE, "plugins")
MAT_ROOT = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\素材\产品素材"
DATA_DIR = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\数据"
PORT = 8900


def scan_plugins():
    """扫 plugins/*/manifest.json → [{id, name, icon, panel, desc}]"""
    out = []
    for mf in sorted(glob.glob(os.path.join(PLUGINS_DIR, "*", "manifest.json"))):
        try:
            m = json.load(open(mf, encoding="utf-8"))
            if m.get("id"):
                out.append({"id": m["id"], "name": m.get("name", m["id"]),
                            "icon": m.get("icon", ""), "panel": m.get("panel", ""),
                    "log": m.get("log", ""),
                            "desc": m.get("desc", ""),
                            "readme": os.path.isfile(os.path.join(PLUGINS_DIR, m["id"], "README.md")),
                            "enabled": not os.path.isfile(os.path.join(PLUGINS_DIR, m["id"], ".disabled"))})
        except Exception:
            pass
    return out


def manifest_full(pid):
    """读指定插件的完整 manifest"""
    mf = os.path.join(PLUGINS_DIR, pid, "manifest.json")
    if os.path.isfile(mf):
        try:
            return json.load(open(mf, encoding="utf-8"))
        except Exception:
            pass
    return None


def call_plugin(pid, action, params):
    """动态加载插件 server.py → handle(action, params)——每次重新加载（热插拔：改代码刷新生效）"""
    srv = os.path.join(PLUGINS_DIR, pid, "server.py")
    if not os.path.isfile(srv):
        return {"error": "插件无 server.py"}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("plg_%s" % pid.replace("-", "_"), srv)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "handle"):
            return mod.handle(action, params)
        return {"error": "server.py 未定义 handle(action, params)"}
    except Exception as e:
        return {"error": "插件执行异常: %s" % str(e)[:100]}


def _serve_file(self, fp, ctype="text/html; charset=utf-8"):
    try:
        data = open(fp, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True
    except Exception:
        return False


def list_current_files(lh):
    """当前品素材文件（书桌联动）——素材/{料号}/"""
    d = os.path.join(MAT_ROOT, lh) if lh else ""
    if not d or not os.path.isdir(d):
        return {"lh": lh, "dir": d, "files": []}
    files = []
    for f in sorted(os.listdir(d)):
        fp = os.path.join(d, f)
        try:
            st = os.stat(fp)
            files.append({"name": f, "size": st.st_size,
                          "is_dir": os.path.isdir(fp),
                          "mtime": st.st_mtime})
        except Exception:
            pass
    return {"lh": lh, "dir": d, "files": files}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=BASE, **k)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        p = self.path
        try:
            if p == "/api/install-plugin":
                # 插件安装：接收 zip bytes → 校验 manifest → 解压到 plugins/<id>/
                import io, zipfile
                ln = int(self.headers.get("Content-Length", 0))
                if ln <= 0 or ln > 50 * 1024 * 1024:
                    return self._json({"ok": False, "error": "空或过大文件"}, 400)
                data = self.rfile.read(ln)
                try:
                    zf = zipfile.ZipFile(io.BytesIO(data))
                except Exception as e:
                    return self._json({"ok": False, "error": "不是有效 zip: %s" % str(e)[:60]}, 400)
                # 找 manifest.json（根或单层目录内）
                names = zf.namelist()
                mf_name = None
                for n in names:
                    if n.endswith("manifest.json") and n.count("/") <= 1:
                        mf_name = n
                        break
                if not mf_name:
                    return self._json({"ok": False, "error": "zip 内无 manifest.json（需插件根/单层目录）"}, 400)
                try:
                    m = json.loads(zf.read(mf_name).decode("utf-8"))
                except Exception as e:
                    return self._json({"ok": False, "error": "manifest 解析失败: %s" % str(e)[:60]}, 400)
                pid = m.get("id", "")
                if not pid:
                    return self._json({"ok": False, "error": "manifest 缺 id"}, 400)
                if not re.match(r"^[a-zA-Z0-9_-]{1,40}$", pid):
                    return self._json({"ok": False, "error": "id 含非法字符"}, 400)
                base = os.path.join(PLUGINS_DIR, pid)
                prefix = os.path.dirname(mf_name)
                if prefix and not prefix.endswith("/"):
                    prefix += "/"
                # 清旧装新
                import shutil
                if os.path.isdir(base):
                    shutil.rmtree(base, ignore_errors=True)
                os.makedirs(base, exist_ok=True)
                for n in names:
                    if not n.startswith(prefix):
                        continue
                    rel = n[len(prefix):]
                    if not rel or rel.endswith("/"):
                        continue
                    # 防路径穿越
                    dest = os.path.normpath(os.path.join(base, rel))
                    if not dest.startswith(base):
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as f:
                        f.write(zf.read(n))
                return self._json({"ok": True, "id": pid, "name": m.get("name", pid),
                                   "desc": m.get("desc", ""), "readme": os.path.isfile(os.path.join(base, "README.md"))})
            if p == "/api/logkeep":
                # 写日志保留天数
                try:
                    import urllib.parse
                    length = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(length).decode("utf-8", "replace")
                    q = urllib.parse.parse_qs(body)
                    days = int(q.get("days", ["30"])[0])
                    days = max(1, min(days, 365))
                    open(r"C:\\Users\\jdt-pty\\Desktop\\推广一键跑\\runtime\\log_keep_days.json", "w", encoding="utf-8").write(str(days))
                    return self._json({"ok": True, "days": days})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
            return self._json({"ok": False, "error": "未知 POST"}, 404)
        except Exception as e:
            return self._json({"error": str(e)[:100]}, 500)

    def do_GET(self):
        p = self.path
        try:
            # 内核 vendor 静态（开源库本地化——SheetJS 等）
            if p.startswith("/vendor/"):
                rel = p[len("/vendor/"):]
                fp = os.path.join(BASE, "vendor", rel)
                if os.path.isfile(fp):
                    if _serve_file(self, fp, "application/javascript" if fp.endswith(".js") else "text/plain"):
                        return
                return self._json({"error": "vendor 资源不存在"}, 404)
            # 插件面板路由：/panel/<插件id> → 渲染插件 panel
            if p.startswith("/panel/"):
                pid = p.split("/")[2].split("?")[0]
                m = manifest_full(pid)
                if m:
                    fp = os.path.join(PLUGINS_DIR, pid, m.get("panel", "index.html"))
                    if os.path.isfile(fp):
                        if _serve_file(self, fp):
                            return
                return self._json({"error": "插件面板不存在"}, 404)
            # 插件后端 action：/api/plg/<插件id>/<action>?k=v
            if p.startswith("/api/plg/"):
                import urllib.parse
                rest = p[len("/api/plg/"):]
                parts = rest.split("?", 1)
                seg = [s for s in parts[0].split("/") if s]
                pid = seg[0] if seg else ""
                action = seg[1] if len(seg) > 1 else ""
                params = {}
                if len(parts) > 1:
                    params = {k: v[0] for k, v in urllib.parse.parse_qs(parts[1]).items()}
                # 内置 action：readme（返回插件 README.md 文本）
                if action == "readme":
                    rp = os.path.join(PLUGINS_DIR, pid, "README.md")
                    if os.path.isfile(rp):
                        txt = open(rp, encoding="utf-8", errors="replace").read()
                        return self._json({"ok": True, "content": txt[:50000]})
                    return self._json({"ok": False, "content": "该插件无 README.md"})
                return self._json(call_plugin(pid, action, params))
            # 插件静态资源：/plug-assets/<插件id>/<文件>
            if p.startswith("/plug-assets/"):
                seg = p[len("/plug-assets/"):].split("/", 1)
                pid, rel = seg[0], seg[1] if len(seg) > 1 else ""
                if not rel:
                    return self._json({"error": "缺文件"}, 404)
                fp = os.path.join(PLUGINS_DIR, pid, rel)
                if os.path.isfile(fp):
                    ext = os.path.splitext(fp)[1].lower()
                    ct = "image/png" if ext == ".png" else "image/svg+xml" if ext == ".svg" else \
                         "application/javascript" if ext == ".js" else "text/css" if ext == ".css" else "text/plain"
                    if _serve_file(self, fp, ct):
                        return
                return self._json({"error": "资源不存在"}, 404)
            if p == "/api/plugins":
                return self._json({"plugins": scan_plugins()})
            if p == "/api/logkeep":
                # 日志保留天数（0909——设置-通用可改；清理归档超期删除）
                try:
                    v = int(open(r"C:\\Users\\jdt-pty\\Desktop\\推广一键跑\\runtime\\log_keep_days.json", encoding="utf-8").read().strip())
                except Exception:
                    v = 30
                return self._json({"ok": True, "days": v})
            if p.startswith("/api/logtail"):
                # 日志尾查看（运行日志 Tab——通用：读 推广一键跑/runtime/xxx.log 尾部）
                import urllib.parse
                q = urllib.parse.parse_qs(p.split("?", 1)[1])
                fn = q.get("f", ["tg_panel.log"])[0]
                log_root = r"C:\Users\jdt-pty\Desktop\推广一键跑\runtime"
                fp2 = os.path.join(log_root, os.path.basename(fn))
                all_mode = q.get("all", ["0"])[0] == "1"
                if os.path.isfile(fp2):
                    try:
                        raw = open(fp2, "rb").read()
                        # 编码自适应：utf-8 BOM / utf-8 合法 → utf-8；否则 gbk（tuiguang 控制台输出是 gbk，dual_check/pipeline 是 utf-8）
                        txt = None
                        for enc in ("utf-8-sig", "utf-8", "gbk"):
                            try:
                                t2 = raw.decode(enc)
                                if "\ufffd" not in t2:
                                    txt = t2
                                    break
                            except Exception:
                                continue
                        if txt is None:
                            txt = raw.decode("gbk", errors="replace")
                        lines = txt.splitlines()
                        tail = lines if all_mode else lines[-300:]
                        return self._json({"ok": True, "lines": tail, "name": fn, "all": all_mode})
                    except Exception as e:
                        return self._json({"ok": False, "error": str(e)[:80]})
                return self._json({"ok": False, "error": "日志不存在"})
            if p.startswith("/api/current/"):
                lh = p[len("/api/current/"):].strip("/")
                import urllib.parse
                lh = urllib.parse.unquote(lh)
                return self._json(list_current_files(lh))
            if p.startswith("/api/pdf-pages"):
                # pdf 页数（预览转图用）
                import urllib.parse
                q = urllib.parse.parse_qs(p.split("?", 1)[1])
                fp = q.get("p", [""])[0]
                try:
                    import fitz
                    doc = fitz.open(fp)
                    n = doc.page_count
                    doc.close()
                    return self._json({"ok": True, "pages": n})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
            if p.startswith("/api/pdf-page?"):
                # pdf 单页转 png（长图/规格书预览——fitz 渲染）
                import urllib.parse
                q = urllib.parse.parse_qs(p.split("?", 1)[1])
                fp = q.get("p", [""])[0]
                n = int(q.get("n", ["0"])[0] or 0)
                try:
                    import fitz
                    doc = fitz.open(fp)
                    if n >= doc.page_count:
                        doc.close()
                        return self._json({"error": "页号超界"}, 404)
                    pg = doc.load_page(n)
                    # 0907：转图清晰度 1.6x → 2.5x（规格书长图放大 2x+ 不糊——位图预览天花板后移）
                    pix = pg.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
                    doc.close()
                    data = pix.tobytes("png")
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except Exception as e:
                    return self._json({"error": str(e)[:80]}, 500)
            if p == "/api/browser-status":
                # 浏览器登录态检测（chrome_profile cookie 有效性——跑一次查询探测）
                try:
                    import subprocess
                    TG = r"C:\Users\jdt-pty\Desktop\推广一键跑"
                    RT = os.path.join(TG, "runtime", "python.exe")
                    r = subprocess.run([RT, "-X", "utf8", "-u", os.path.join(TG, "_dual_one.py"), "1379668", ""],
                                       capture_output=True, timeout=90)
                    out = (r.stdout or b"").decode("utf-8", "replace")
                    ok = "已双百" in out or "未双百" in out or "流量加速中" in out
                    return self._json({"ok": True, "login": ok,
                                       "msg": "登录态有效（cookie 在 chrome_profile）" if ok else "登录态失效"})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
            if p == "/api/browser-login":
                # 引导登录：打开 chrome_profile 淘宝登录页（人登录一次——cookie 存 profile）
                import subprocess
                try:
                    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
                    prof = os.path.join(r"C:\Users\jdt-pty\Desktop\推广一键跑", "chrome_profile")
                    subprocess.Popen([chrome, "--user-data-dir=" + prof,
                                      "https://login.taobao.com/member/login.jhtml"])
                    return self._json({"ok": True, "msg": "已打开登录页——登录完成后点「检测」"})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
            if p == "/api/browser-clear":
                # 清除 Cookie：删 chrome_profile cookie 文件（登出）——重登需引导登录
                try:
                    prof = os.path.join(r"C:\Users\jdt-pty\Desktop\推广一键跑", "chrome_profile")
                    removed = []
                    for cand in [os.path.join(prof, "Default", "Cookies"),
                                 os.path.join(prof, "Default", "Network", "Cookies")]:
                        if os.path.isfile(cand):
                            try:
                                os.remove(cand)
                                removed.append(os.path.basename(cand))
                            except Exception:
                                pass
                    if removed:
                        return self._json({"ok": True, "msg": "已清除 Cookie（%s）——请关闭已开的 Chrome 后点「引导登录」重新登录" % ",".join(removed)})
                    return self._json({"ok": True, "msg": "无 Cookie 文件或已被占用（Chrome 开着？先关掉再清）"})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
            if p == "/api/autostart":
                # 开机自启：注册表 HKCU Run 加工作台启动项
                try:
                    import winreg
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                         r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
                    exe = os.path.join(BASE, "workbench.py")
                    py = r"C:\Users\jdt-pty\Desktop\推广一键跑\runtime\python.exe"
                    cmd = '"%s" "%s"' % (py, exe)
                    winreg.SetValueEx(key, "OHWorkbench", 0, winreg.REG_SZ, cmd)
                    winreg.CloseKey(key)
                    return self._json({"ok": True, "msg": "已设置开机自启：%s" % cmd})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:100]})
            if p.startswith("/api/plugin-toggle?"):
                import urllib.parse
                q = urllib.parse.parse_qs(p.split("?", 1)[1])
                pid = q.get("id", [""])[0]
                on = q.get("on", ["1"])[0] == "1"
                df = os.path.join(PLUGINS_DIR, pid, ".disabled")
                try:
                    if on and os.path.isfile(df):
                        os.remove(df)
                    elif not on and not os.path.isfile(df):
                        open(df, "w").close()
                    return self._json({"ok": True, "id": pid, "enabled": on})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
            if p.startswith("/api/open?"):


                # 系统打开文件夹（书桌"打开文件夹"按钮）
                import urllib.parse
                q = urllib.parse.parse_qs(p.split("?", 1)[1])
                fp = q.get("p", [""])[0] or MAT_ROOT
                if not os.path.isdir(fp):
                    fp = os.path.dirname(fp)
                if os.path.isdir(fp):
                    os.startfile(fp)  # Windows 资源管理器打开
                    return self._json({"ok": True, "opened": fp})
                return self._json({"error": "目录不存在"}, 404)
            if p.startswith("/api/search?"):
                # 工作台搜索：料号目录名 + 文件名联想（搜素材根 + 数据 + 报告）
                import urllib.parse
                q = urllib.parse.parse_qs(p.split("?", 1)[1])
                kw = (q.get("q", [""])[0] or "").strip().lower()
                if not kw:
                    return self._json({"hits": []})
                hits = []
                # 料号目录匹配（素材根下）
                if os.path.isdir(MAT_ROOT):
                    for d in sorted(os.listdir(MAT_ROOT)):
                        if kw in d.lower():
                            hits.append({"type": "dir", "name": d, "path": os.path.join(MAT_ROOT, d)})
                # 文件匹配（数据目录 csv + 报告）
                for base, label in [(DATA_DIR, "数据")]:
                    if os.path.isdir(base):
                        for f in sorted(os.listdir(base)):
                            if kw in f.lower():
                                hits.append({"type": "file", "name": f, "label": label,
                                            "path": os.path.join(base, f)})
                return self._json({"hits": hits[:30]})
            if p.startswith("/api/file?"):
                # 文件读取（书桌预览——图片返回字节，文本返回内容）
                import urllib.parse
                q = urllib.parse.parse_qs(p.split("?", 1)[1])
                fp = q.get("p", [""])[0]
                if not fp or not os.path.isfile(fp):
                    return self._json({"error": "文件不存在"}, 404)
                ext = os.path.splitext(fp)[1].lower()
                if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    data = open(fp, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/%s" % ext.lstrip(".").replace("jpeg", "jpeg"))
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if ext in (".mp4", ".webm", ".mov"):
                    # 视频字节流（书桌预览——html5 video 可播）
                    data = open(fp, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Type", "video/%s" % ("mp4" if ext == ".mp4" else ext.lstrip(".")))
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if ext == ".pdf":
                    data = open(fp, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                try:
                    txt = open(fp, encoding="utf-8", errors="replace").read()
                except Exception:
                    txt = ""
                return self._json({"content": txt[:200000], "name": os.path.basename(fp)})
        except Exception as e:
            return self._json({"error": str(e)}, 500)
        return super().do_GET()


if __name__ == "__main__":
    os.chdir(BASE)
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler)
    srv.allow_reuse_address = True
    print("优化管理工作台: http://127.0.0.1:%d/" % PORT)
    srv.serve_forever()
