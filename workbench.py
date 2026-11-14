# -*- coding: utf-8 -*-
"""优化管理工作台 · 内核（简单内核 + 插件丰富）
四区布局：顶菜单 / 左插件导航 / 中日志监控 / 右文件书桌（当前品联动）
启动：python workbench.py → http://127.0.0.1:8900/
零第三方依赖（http.server 标准库）——插件各自声明所需包
"""
import http.server
import socketserver
import json
import os
import sys
import glob
import re

import paths            # 机器特有路径的唯一出处（换机/换目录只改 paths.local.json）
import preflight        # 启动自检：缺什么、怎么办（壳与面板共用）


# ---- Windows：绝不允许子进程新开控制台窗口（它会抢走前台焦点，用户打字被打断）----
def _no_console_children():
    """窗口程序（exe 没有控制台）起控制台子进程时，系统会给它【新开一个黑窗】，那个黑窗会抢走
    前台焦点——用户正在打字就被打断。轮询越勤越明显：面板每 10s 拉一次状态，探测里带 tasklist。
    源码方式跑时父进程自己有控制台，子进程共用它，所以不显形（这就是「源码版没事」的原因）。
    这里统一补 CREATE_NO_WINDOW；调用方显式给过 creationflags 的尊重原样（守护进程要 DETACHED）。
    """
    if os.name != "nt":
        return
    import subprocess as _sp
    FLAG = 0x08000000

    def _wrap(f):
        def g(*a, **kw):
            kw.setdefault("creationflags", FLAG)
            return f(*a, **kw)
        g._wb_nowin = True
        return g

    for _n in ("run", "Popen", "call", "check_call", "check_output"):
        _f = getattr(_sp, _n, None)
        if _f is not None and not getattr(_f, "_wb_nowin", False):
            setattr(_sp, _n, _wrap(_f))


_no_console_children()

BASE = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(BASE, "plugins")
try:
    import conn_store  # 连接导航（SQLite+DPAPI）
except Exception:
    conn_store = None
MAT_ROOT = paths.MAT_ROOT
DATA_DIR = paths.DATA
PORT = paths.PORT          # 首选端口（设置-通用或 paths.local.json 可改；被占会自动往后顺延）


def _refresh_path_consts():
    """设置里改了路径后，内核自己捕获过的那几个常量要跟着换——否则得重启才生效。
    其它地方都是用到时才读 paths.X，不用管。"""
    global MAT_ROOT, DATA_DIR
    MAT_ROOT = paths.MAT_ROOT
    DATA_DIR = paths.DATA


def _log_err(where, exc=None, **ctx):
    """错误现场落盘：写程序自己的日志目录（不写业务数据目录）。
    在别人机器上没法现场调试——日志是唯一的眼睛，所以带上时间/位置/上下文/堆栈。"""
    import datetime
    import traceback
    try:
        os.makedirs(paths.RUNTIME, exist_ok=True)
        with open(os.path.join(paths.RUNTIME, "errors.log"), "a", encoding="utf-8") as f:
            f.write("\n%s  [%s] %s\n" % (
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), where,
                "  ".join("%s=%s" % (k, str(v)[:140]) for k, v in ctx.items())))
            if exc is not None:
                f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-1500:])
    except Exception:
        pass


def core_version():
    """内核版本 = CHANGELOG 首条 ## vX.Y.Z（单一来源，永不滞后）"""
    try:
        with open(os.path.join(BASE, "CHANGELOG.md"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"##\s*v([0-9][0-9.]*)", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return "0.0.0"


def _acct_profile(tg, site=None):
    """当前站点的激活连接对应浏览器 profile（复用 acct_profile.resolve）。

    —— 这里踩过一个很贵的坑：引导登录和检测登录态用了**不同的 profile**。
       resolve(site=None) 一旦拿不到连接，会回退 _default_profile_for(None)，
       也就是笼统的 chrome_profile；而 _mtop_api 那边按激活连接算出的是
       chrome_profile_<连接名>。两个目录不同 → 你在引导登录里登进去了，
       检测时读的却是另一个空目录 → 自动填账号密码 → 淘宝风控发短信。
       表现就是「每次登录都要短信验证」。

    所以：站点必须显式定；兜底也必须按激活连接名推导，绝不退回 chrome_profile。
    """
    try:
        if tg not in sys.path:
            sys.path.insert(0, tg)
        import acct_profile
        # 显式定站点（resolve 内部对 site=None 的处理不可靠）
        if not site:
            try:
                import conn_store
                site = conn_store.current_site() if hasattr(conn_store, "current_site") else "taobao"
            except Exception:
                site = "taobao"
        try:
            return acct_profile.resolve(site=site)
        except TypeError:
            return acct_profile.resolve()          # 旧版没有 site 参数
    except Exception as e:
        # 兜底：按激活连接名推导，不用笼统的 chrome_profile
        _s = (site or "taobao").strip().lower()
        _conn = None
        try:
            import conn_store
            if hasattr(conn_store, "active_shop"):
                _conn = conn_store.active_shop(_s)
            else:
                _conn = conn_store.active_of("taobao")
        except Exception:
            pass
        if _conn:
            _p = (_conn.get("profile") or "").strip()
            if _p:
                return (os.path.join(tg, _p) if not os.path.isabs(_p) else _p), _conn
            _name = (_conn.get("name") or "").strip()
            if _name:
                return os.path.join(tg, "chrome_profile_" + _name), _conn
        _d = "chrome_profile_1688" if _s == "1688" else "chrome_profile"
        return os.path.join(tg, _d), None


LOGIN_CDP_PORT = 9223          # 引导登录专用调试端口（避开手开的 9222）


def read_cookie_via_cdp(port=None):
    """连**正在运行的** Chrome 读淘宝 cookie（会话 cookie 也能读到）。

    为什么需要这条路：cookie2 / _tb_token_ / unb 是会话级 cookie，
    Chrome 关闭就清；把 restore_on_startup 设成 1 实测也没保住。
    但只要 Chrome 还开着，这些 cookie 就在内存里，CDP 能读出来。

    为什么走子进程：工作台的 HTTP 服务是多线程的，Playwright 的 sync API
    不支持子线程（会报 "function() argument 'code' must be code"）。
    所以交给 _probe_cookie_cdp.py 在独立进程里读。
    """
    import subprocess

    port = int(port or LOGIN_CDP_PORT)
    RT = os.path.join(paths.TG_ROOT, "runtime", "python.exe")
    SC = os.path.join(paths.TG_ROOT, "_probe_cookie_cdp.py")
    if not os.path.isfile(SC):
        return {"ok": False, "cookies": {}, "detail": "找不到 _probe_cookie_cdp.py"}
    if not os.path.isfile(RT):
        RT = sys.executable
    try:
        r = subprocess.run([RT, "-X", "utf8", "-u", SC, str(port)],
                           capture_output=True, timeout=45)
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        if not out:
            err = (r.stderr or b"").decode("utf-8", "replace")[-160:]
            return {"ok": False, "cookies": {}, "detail": "子进程无输出 %s" % err}
        return __import__("json").loads(out.splitlines()[-1])
    except Exception as e:
        return {"ok": False, "cookies": {}, "detail": "调子进程失败: %s" % str(e)[:60]}


def _ensure_session_restore(prof):
    """让 Chrome 正常关闭时**不清会话 cookie**。

    Chrome 的 cookie2 / _tb_token_ / unb 都是会话级 cookie。默认设置下
    （restore_on_startup=5，「打开新标签页」）Chrome 正常退出时会主动清掉它们
    —— 所以你登录成功、关掉窗口，登录态就没了。

    把 session.restore_on_startup 设成 1（继续浏览上次打开的网页），
    Chrome 就不再清会话 cookie。必须在 Chrome 启动前写，开着的时候会被覆盖。
    """
    pf = os.path.join(prof, "Default", "Preferences")
    try:
        os.makedirs(os.path.dirname(pf), exist_ok=True)
        d = {}
        if os.path.isfile(pf):
            try:
                with open(pf, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                d = {}
        if not isinstance(d, dict):
            d = {}
        sess = d.get("session")
        if not isinstance(sess, dict):
            sess = {}
        if sess.get("restore_on_startup") == 1:
            return True, "已是 1（会保留会话 cookie）"
        sess["restore_on_startup"] = 1
        d["session"] = sess
        with open(pf, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        return True, "已设为 1（会保留会话 cookie）"
    except Exception as e:
        return False, "设置失败: %s（Chrome 开着就先关掉）" % str(e)[:60]


def check_login_cookie(prof):
    """读 Chrome profile 的 cookie 数据库判断登录态——**不开浏览器**。

    为什么不开：Playwright 起的浏览器带自动化特征，淘宝会拦在登录页；
    而且 profile 若正被真实 Chrome 占着，launch_persistent_context 会撞目录锁。
    判定登录态只需要知道 cookie 在不在，读 SQLite 就够。

    返回 {login, level, detail, cookies:{name:状态}}
      level: valid   cookie2+sgcookie 都在，且 unb 在 → 真登录过
             partial 有会话 cookie 但缺 unb → 像是没登完整
             none    没有关键 cookie
    """
    import sqlite3
    import shutil
    import tempfile
    import time as _t

    EPOCH_1601 = 11644473600
    now = _t.time()
    cands = [os.path.join(prof, "Default", "Network", "Cookies"),
             os.path.join(prof, "Default", "Cookies")]
    src = next((c for c in cands if os.path.isfile(c)), None)
    if not src:
        return {"login": False, "level": "none", "cookies": {},
                "detail": "profile 里没有 Cookies 文件: %s" % os.path.basename(prof)}

    tmp = os.path.join(tempfile.gettempdir(),
                       "wb_ck_%d_%d.db" % (int(now), os.getpid()))
    try:
        shutil.copy2(src, tmp)          # 复制再读：Chrome 开着会锁原文件
        con = sqlite3.connect(tmp)
        rows = con.execute(
            "SELECT name, expires_utc FROM cookies "
            "WHERE host_key LIKE '%taobao.com%'").fetchall()
        con.close()
    except Exception as e:
        return {"login": False, "level": "none", "cookies": {},
                "detail": "读 Cookies 失败: %s" % str(e)[:60]}
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass

    got = {}
    for name, exp in rows:
        if exp and exp > 0:
            left = exp / 1000000 - EPOCH_1601 - now
            got[name] = ("有效" if left > 0 else "已过期")
        else:
            got[name] = "会话"

    has = lambda k: k in got and got[k] != "已过期"
    core = has("cookie2") and has("sgcookie")
    # unb 是硬判据：实测登录成功的号（保险丝子账号1/2、1688）都有 unb，
    # 而只走到登录页中段的 profile 都缺它。lgc 只有 1688 才有，不能当条件。
    real = has("unb")
    tk = has("_m_h5_tk")

    if core and real:
        return {"login": True, "level": "valid", "cookies": got,
                "detail": "登录态完整（cookie2 + sgcookie + unb）；MTOP票据: %s"
                          % ("在" if tk else "缺（跑一次查询会自刷新）")}
    if core:
        return {"login": False, "level": "partial", "cookies": got,
                "detail": "有 cookie2/sgcookie 但缺 unb——没真正登录成功。"
                          "请在引导登录开的窗口里登录，直到看见卖家中心首页（商品列表），"
                          "停 20 秒再关窗口"}
    return {"login": False, "level": "none", "cookies": got,
            "detail": "缺 cookie2 / sgcookie——没有登录态，点「引导登录」登录"}


def active_token():
    """当前站点激活店铺连接的指纹（插件前端轮询用）——变了 = 切号了，各面板自行重载"""
    try:
        import conn_store
        _site = conn_store.current_site() if hasattr(conn_store, "current_site") else "taobao"
        a = conn_store.active_shop(_site) if hasattr(conn_store, "active_shop") else conn_store.active_of("taobao")
        if not a:
            return {"token": "", "name": "", "site": _site}
        return {"token": "%s:%s:%s" % (_site, a.get("id"), a.get("member_id") or ""),
                "name": a.get("name") or "", "site": _site}
    except Exception as e:
        return {"token": "", "name": "", "error": str(e)[:60]}


def scan_plugins():
    """扫 plugins/*/manifest.json → [{id, name, icon, panel, desc}]"""
    out = []
    for mf in sorted(glob.glob(os.path.join(PLUGINS_DIR, "*", "manifest.json"))):
        try:
            m = json.load(open(mf, encoding="utf-8"))
            if m.get("id"):
                out.append({"id": m["id"], "name": m.get("name", m["id"]), "version": m.get("version", "0.0.0"),
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


def _bslog(line):
    """登录态检测留痕：内核与插件两条路写同一个文件，一眼看出差在哪。
    换行在这里压平——留痕是一行一条，被拆行了 grep 和肉眼比对都难。"""
    import datetime
    line = str(line).replace("\r", " ").replace("\n", " / ")
    try:
        d = paths.RUNTIME
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "browser_status.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass


def _registry_fp():
    return os.path.join(BASE, "registry.json")


def _registry():
    """插件列表（公开的 registry.json：插件名/版本/下载地址/一句话说明）。
    读不到就当空列表——没列表不影响已装插件。"""
    try:
        with open(_registry_fp(), encoding="utf-8") as f:
            d = json.load(f)
        return d.get("plugins") or []
    except FileNotFoundError:
        return []
    except Exception as e:
        _log_err("registry", e, fp=_registry_fp())
        return []


def _registry_entry(pid):
    for it in _registry():
        if str(it.get("id")) == pid:
            return it
    return None


# ── 从仓库目录直接装（不再存 pkgs/zip 的第二份）──────────────────────
# 仓库里的 plugins/<id>/ 源码就是发布源：data API 列清单 → 逐文件拉 → 打成一个 zip 交给
# install_plugin_zip（复用同一套校验/防穿越/落盘，不另写一套）。
_JSD_REPO_DEFAULT = "MuXinZi-awa/taobao-workbench"
_JSD_CDN = ["https://fastly.jsdelivr.net/gh/", "https://cdn.jsdelivr.net/gh/"]
_JSD_DATA = "https://data.jsdelivr.com/v1/packages/gh/"


def _registry_repo():
    """registry 顶层的 _repo（缺省用内置仓）"""
    try:
        with open(_registry_fp(), encoding="utf-8") as f:
            return str(json.load(f).get("_repo") or _JSD_REPO_DEFAULT)
    except Exception:
        return _JSD_REPO_DEFAULT


# 本机网络：python 直连 jsDelivr 常被断（RemoteDisconnected），走 Clash 代理可达（WinINET 系统代理只对 PowerShell 生效，urllib 不认）。
# 顺序：直连 → 环境变量(urllib 自动读 HTTP(S)_PROXY) → Clash 7897 兜底。
_PROXY_FALLBACK = ["http://127.0.0.1:7897"]


def _http_get(url, timeout=60):
    import urllib.request
    last = None
    for px in [None] + _PROXY_FALLBACK:
        try:
            # Request 每轮新建：直连失败过的对象再喂给代理 open 会失效
            req = urllib.request.Request(url, headers={"User-Agent": "workbench"})
            if px:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px}))
            else:
                op = urllib.request.build_opener()
            with op.open(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
    raise last


def _tree_files(node, prefix=""):
    """jsDelivr data API 的文件树节点 → [(相对路径, size)]"""
    for f in node.get("files", []):
        if f.get("type") == "directory":
            for x in _tree_files(f, prefix + f["name"] + "/"):
                yield x
        else:
            yield prefix + f["name"], f.get("size", 0)


def _fetch_plugin_from_dir(ent):
    """从仓库目录装：data API 列清单 → 逐文件拉（多 CDN 兜底）→ 打成一个 zip bytes。
    为什么打 zip：install_plugin_zip 的校验/防路径穿越/落盘是唯一口径，不另写一套。"""
    import io
    import zipfile
    repo = str(ent.get("repo") or _registry_repo())
    ref = str(ent.get("ref") or "master")
    dirp = str(ent.get("dir") or "").strip("/")
    if not dirp:
        raise RuntimeError("该插件没给目录（dir）")
    meta = json.loads(_http_get(_JSD_DATA + "%s@%s" % (repo, ref), 30).decode("utf-8"))
    node = {"files": meta.get("files") or []}
    for seg in dirp.split("/"):
        node = next((x for x in node.get("files", [])
                     if x.get("type") == "directory" and x.get("name") == seg), None)
        if node is None:
            raise RuntimeError("仓库里没有目录 %s（ref=%s）" % (dirp, ref))
    files = list(_tree_files(node))
    if not files:
        raise RuntimeError("目录 %s 是空的" % dirp)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, _sz in files:
            data, err = None, None
            for cdn in _JSD_CDN:
                try:
                    data = _http_get(cdn + "%s@%s/%s/%s" % (repo, ref, dirp, rel))
                    break
                except Exception as e:
                    err = e
            if data is None:
                raise RuntimeError("拉不到文件 %s（%s）" % (rel, str(err)[:60]))
            z.writestr(rel, data)
    return buf.getvalue()


def _fetch_plugin_zip(ent):
    """取插件包：path（本地文件，离线/测试用）→ dir（从仓库目录装，首选）→ url（zip 直链，多源竞速）。
    失败说人话：缺文件/目录不存在/网络断，都在这里抛出具体原因。"""
    import urllib.request
    import threading
    p = str(ent.get("path") or "").strip()
    if p and os.path.isfile(p):
        with open(p, "rb") as f:
            return f.read()
    if str(ent.get("dir") or "").strip():       # 从仓库目录装（新方案：仓库只有一份）
        return _fetch_plugin_from_dir(ent)
    u = str(ent.get("url") or "").strip()
    if not u:
        raise RuntimeError("列表里这一项既没有 url 也没有 path")
    urls = [x.strip() for x in re.split(r"[;,]", u) if x.strip()]
    got = {}
    lock = threading.Lock()

    def _g(i, url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "workbench"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            if data:
                with lock:
                    got[i] = data
        except Exception:
            pass

    ths = [threading.Thread(target=_g, args=(i, url)) for i, url in enumerate(urls)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=90)
    for i in range(len(urls)):
        if got.get(i):
            return got[i]
    raise RuntimeError("所有下载源都取不到（%d 个源）" % len(urls))


def install_plugin_zip(data):
    """装插件：zip bytes → 校验 manifest → 解压到 plugins/<id>。
    「插件列表点装」走这里；与拖入安装同一套校验与落盘规则。"""
    import io
    import shutil
    import zipfile
    if len(data) <= 0 or len(data) > 50 * 1024 * 1024:
        return {"ok": False, "error": "空或过大文件"}
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        return {"ok": False, "error": "不是有效 zip：%s" % str(e)[:60]}
    names = zf.namelist()
    mf_name = None
    for n in names:
        if n.endswith("manifest.json") and n.count("/") <= 1:
            mf_name = n
            break
    if not mf_name:
        return {"ok": False, "error": "zip 内无 manifest.json（需插件根/单层目录）"}
    try:
        m = json.loads(zf.read(mf_name).decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": "manifest 解析失败：%s" % str(e)[:60]}
    pid = m.get("id", "")
    if not pid:
        return {"ok": False, "error": "manifest 缺 id"}
    if not re.match(r"^[a-zA-Z0-9_-]{1,40}$", pid):
        return {"ok": False, "error": "id 含非法字符"}
    base = os.path.join(PLUGINS_DIR, pid)
    prefix = os.path.dirname(mf_name)
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    if os.path.isdir(base):        # 装新的先清旧的（更新就是这条路）
        shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    for n in names:
        if not n.startswith(prefix):
            continue
        rel = n[len(prefix):]
        if not rel or rel.endswith("/"):
            continue
        dest = os.path.normpath(os.path.join(base, rel))   # 防路径穿越
        if not dest.startswith(base):
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(zf.read(n))
    return {"ok": True, "id": pid, "name": m.get("name", pid), "version": m.get("version", ""),
            "desc": m.get("desc", ""),
            "readme": os.path.isfile(os.path.join(base, "README.md"))}


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
        _log_err("plugin", e, pid=pid, action=action)
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
        # 失败也要留现场：所有响应的唯一出口，凡是 4xx/5xx 或 ok=false / 带 error 的，都记一行
        try:
            if code >= 400 or (isinstance(obj, dict) and (obj.get("ok") is False or obj.get("error"))):
                _log_err("resp%d" % code, None, path=str(self.path)[:160], detail=str(obj)[:300])
        except Exception:
            pass
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        p = self.path
        try:
            # 插件后端（POST）：/api/plg/<id>/<action>?k=v
            #   Content-Type: application/json → body 当 JSON 参数
            #   其它（如文件拖入）→ body 原文当字节，附 _raw 供插件读
            if p.startswith("/api/plg/"):
                rest = p[len("/api/plg/"):]
                parts = rest.split("/", 1)
                pid = parts[0]
                tail = parts[1] if len(parts) > 1 else ""
                action = tail.split("?")[0]
                qs = tail.split("?", 1)[1] if "?" in tail else ""
                params = {}
                import urllib.parse as _up
                for kv in qs.split("&") if qs else []:
                    if "=" in kv:
                        k, _, v = kv.partition("=")
                        params[k] = _up.unquote_plus(v)
                ln = int(self.headers.get("Content-Length", 0) or 0)
                if ln > 0 and ln <= 64 * 1024 * 1024:
                    body = self.rfile.read(ln)
                    ct = (self.headers.get("Content-Type") or "").lower()
                    if "application/json" in ct:
                        try:
                            j = json.loads(body.decode("utf-8"))
                            if isinstance(j, dict):
                                params.update(j)
                        except Exception:
                            params["_json_error"] = body[:120].decode("utf-8", "replace")
                    else:
                        params["_raw"] = body
                return self._json(call_plugin(pid, action, params))
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
            if p == "/api/plugin-install":
                # 插件列表点装：registry 里找到 id → 取 zip（本地文件 / 公开直链）→ 同一条安装路
                pid = ""
                try:
                    ln = int(self.headers.get("Content-Length", 0) or 0)
                    q = json.loads(self.rfile.read(ln).decode("utf-8", "replace") or "{}")
                    pid = str(q.get("id") or "").strip()
                    ent = _registry_entry(pid)
                    if not ent:
                        return self._json({"ok": False,
                                           "error": "插件列表里没有「%s」——列表文件：%s" % (pid, _registry_fp())}, 404)
                    r = install_plugin_zip(_fetch_plugin_zip(ent))
                    if r.get("ok"):
                        r["version"] = ent.get("version", r.get("version", ""))
                    return self._json(r, 200 if r.get("ok") else 400)
                except Exception as e:
                    _log_err("plugin-install", e, pid=pid[:40])   # 装不上要留现场
                    return self._json({"ok": False,
                                       "error": "装不上「%s」：%s" % (pid or "?", str(e)[:160])}, 500)
            if p == "/api/conn/save":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    d = json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")
                    cid = conn_store.save(d)
                    return self._json({"ok": True, "id": cid})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:100]})
            if p == "/api/logkeep":
                # 写日志保留天数
                try:
                    import urllib.parse
                    length = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(length).decode("utf-8", "replace")
                    q = urllib.parse.parse_qs(body)
                    days = int(q.get("days", ["30"])[0])
                    days = max(1, min(days, 365))
                    open(paths.LOG_KEEP_DAYS, "w", encoding="utf-8").write(str(days))
                    return self._json({"ok": True, "days": days})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
            if p == "/api/paths":
                # 设置-通用：改路径（写 paths.local.json，内核立即改用新值）
                try:
                    import urllib.parse
                    length = int(self.headers.get("Content-Length") or 0)
                    q = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
                    key = (q.get("key", [""])[0] or "").strip()
                    paths.set_value(key, q.get("value", [""])[0])
                    _refresh_path_consts()
                    return self._json({"ok": True, "item": paths.info(key)})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:120]})
            if p == "/api/paths-reset":
                # 恢复默认：从本地配置里删掉该键（删掉才回到推导）
                try:
                    import urllib.parse
                    length = int(self.headers.get("Content-Length") or 0)
                    q = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
                    key = (q.get("key", [""])[0] or "").strip()
                    paths.reset(key or None)
                    _refresh_path_consts()
                    return self._json({"ok": True, "keys": [paths.info(k) for k in paths.KEYS]})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:120]})
            return self._json({"ok": False, "error": "未知 POST"}, 404)
        except Exception as e:
            _log_err("POST", e, path=p[:160])
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
            if p.startswith("/api/conn/"):
                import urllib.parse as _up
                q = _up.parse_qs(p.split("?", 1)[1]) if "?" in p else {}
                act = p[len("/api/conn/"):].split("?")[0]
                try:
                    if act == "list":
                        return self._json({"ok": True, "items": conn_store.list_all(),
                                           "site": conn_store.current_site() if hasattr(conn_store, "current_site") else "taobao"})
                    if act == "site":
                        # GET ?set=taobao|1688 切换当前流水线站点（POST 也走这里）
                        _s = (q.get("set", [""])[0] or "").strip().lower()
                        if _s:
                            ok = conn_store.set_current_site(_s) if hasattr(conn_store, "set_current_site") else False
                            return self._json({"ok": bool(ok), "site": _s if ok else None,
                                               "error": "" if ok else "站点只能是 taobao / 1688"})
                        return self._json({"ok": True,
                                           "site": conn_store.current_site() if hasattr(conn_store, "current_site") else "taobao"})
                    if act == "save":
                        return self._json({"ok": False, "error": "save 用 POST"})
                    if act == "del":
                        conn_store.delete(int(q.get("id", ["0"])[0]))
                        return self._json({"ok": True})
                    if act == "activate":
                        conn_store.activate(int(q.get("id", ["0"])[0]))
                        return self._json({"ok": True})
                    if act == "reveal":
                        return self._json({"ok": True, "secret": conn_store.get_secret(int(q.get("id", ["0"])[0]))})
                    if act == "test":
                        cid = int(q.get("id", ["0"])[0])
                        row = [x for x in conn_store.list_all() if x["id"] == cid]
                        if not row:
                            return self._json({"ok": False, "error": "连接不存在"})
                        r = row[0]
                        if r["type"] == "odoo":
                            import xmlrpc.client
                            pw = conn_store.get_secret(cid)

                            def _mk(u0, port0, scheme_force=None):
                                u = (u0 or "").strip()
                                if not u:
                                    return ""
                                if not u.lower().startswith(("http://", "https://")):
                                    u = (scheme_force or "https://") + u
                                scheme, rest = u.split("://", 1)
                                if scheme_force:
                                    scheme = scheme_force.rstrip(":/")
                                host, _, path = rest.partition("/")
                                p = (port0 or "").strip()
                                # 默认端口不显式拼（http:80 / https:443）
                                if p and ":" not in host and not (scheme == "https" and p == "443") and not (scheme == "http" and p == "80"):
                                    host = host + ":" + p
                                return "%s://%s%s" % (scheme, host, ("/" + path) if path else "")

                            base = _mk(r.get("url"), r.get("port"))
                            if not base:
                                return self._json({"ok": False, "error": "缺 URL"})
                            # 候选：原样 → 换 https:// → 换 http://（子路径 /odoo 等保留）
                            host_path = base.split("://", 1)[1]
                            cands = [base, "https://" + host_path, "http://" + host_path]
                            seen, last_err = [], ""
                            for cand in cands:
                                if cand in seen:
                                    continue
                                seen.append(cand)
                                ep = cand.rstrip("/") + "/xmlrpc/2/common"
                                # 跟随 301/302 重定向（xmlrpc 默认不跟随——先解析最终地址）
                                try:
                                    import urllib.request as _ur, urllib.error as _ue
                                    _req = _ur.Request(ep, headers={"User-Agent": "Mozilla/5.0"})
                                    try:
                                        _r = _ur.urlopen(_req, timeout=15)
                                        _fin = _r.geturl()
                                    except _ue.HTTPError as _he:
                                        _fin = getattr(_he, "url", ep) or ep
                                    if _fin and _fin != ep:
                                        ep = _fin
                                except Exception:
                                    pass
                                try:
                                    common = xmlrpc.client.ServerProxy(ep)
                                    uid = common.authenticate(r["dbname"], r["username"], pw, {})
                                    if uid:
                                        return self._json({"ok": True, "msg": "Odoo 登录成功：%s（uid=%s）" % (ep, uid)})
                                    last_err = "认证失败（账号/密码/库名 任一不符）——端点可达：%s" % ep
                                except Exception as ee:
                                    last_err = str(ee)[:110]
                            return self._json({"ok": False, "error": "Odoo 测试失败：" + last_err})
                        return self._json({"ok": True, "msg": "已保存（%s 类型测试后续接入）" % r["type"]})
                    return self._json({"ok": False, "error": "未知 conn action"}, 404)
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:120]})
            if p == "/api/account-token":
                return self._json(active_token())

            if p == "/api/plugins":
                return self._json({"plugins": scan_plugins(), "core": core_version()})
            if p == "/api/preflight":
                # 启动自检给面板看：缺什么、怎么办（起不来 vs 还没填，分开说）
                return self._json({"ok": True, "items": preflight.check(BASE,
                                                                       port=self.server.server_address[1])})
            if p == "/api/plugin-registry":
                # 插件列表：公开的 registry.json + 本机已装版本（面板据此算「未装 / 可更新」）
                inst = {}
                try:
                    for pl in scan_plugins():
                        inst[str(pl.get("id"))] = pl.get("version", "")
                except Exception:
                    pass
                return self._json({"ok": True, "file": _registry_fp(), "installed": inst,
                                   "plugins": _registry()})
            if p == "/api/ping":
                # 固定探针：壳/其它工具靠它确认「这个端口上跑的是工作台」（端口可变，所以不能靠端口号认）
                return self._json({"app": "workbench", "pid": os.getpid(),
                                   "port": self.server.server_address[1],
                                   "core": core_version(), "root": BASE})
            if p == "/api/paths":
                # 设置-通用：按归属分组（内核自己的 / 业务数据 / 装了插件才出现的那些）
                inst = []
                try:
                    inst = [pl.get("id") for pl in scan_plugins()]
                except Exception:
                    pass
                return self._json({"ok": True, "config_file": paths._CFG_FP,
                                   "groups": paths.groups(inst),
                                   "keys": [paths.info(k) for k in paths.KEYS]})
            if p == "/api/logkeep":
                # 日志保留天数（设置-通用可改；清理归档超期删除）
                try:
                    v = int(open(paths.LOG_KEEP_DAYS, encoding="utf-8").read().strip())
                except Exception:
                    v = 30
                return self._json({"ok": True, "days": v})
            if p.startswith("/api/logtail"):
                # 日志尾查看（运行日志 Tab——通用：读 推广一键跑/runtime/xxx.log 尾部）
                import urllib.parse
                q = urllib.parse.parse_qs(p.split("?", 1)[1])
                fn = q.get("f", ["tg_panel.log"])[0]
                log_root = paths.TG_RUNTIME
                # 多店铺隔离：流水线日志按活动账号分文件（pipeline_<memberID>.log）。
                # 面板传的仍是插件 manifest 里的 pipeline.log，文件名让插件自己算——
                # 否则切完连接读的还是老账号那个文件。插件不在/算不出来就照旧读 pipeline.log。
                if os.path.basename(fn) == "pipeline.log":
                    try:
                        _lr = call_plugin("pipeline", "logfile", {})
                        if isinstance(_lr, dict) and _lr.get("ok") and _lr.get("name"):
                            fn = os.path.basename(str(_lr["name"]))
                    except Exception:
                        pass
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
                    try:
                        import pymupdf as fitz
                    except ImportError:
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
                    try:
                        import pymupdf as fitz
                    except ImportError:
                        import fitz
                    doc = fitz.open(fp)
                    if n >= doc.page_count:
                        doc.close()
                        return self._json({"error": "页号超界"}, 404)
                    pg = doc.load_page(n)
                    # 转图清晰度 2.5x（规格书长图放大 2x+ 不糊——位图预览天花板后移）
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
                    _log_err("GET", e, path=p[:160])
                    return self._json({"error": str(e)[:80]}, 500)
            if p == "/api/browser-status":
                # 登录态检测：**只读 cookie 数据库，不开浏览器**。
                # 老做法跑 _dual_one.py 探测，但 Playwright 起的浏览器带自动化特征，
                # 淘宝会拦在登录页；profile 若正被真实 Chrome 占着还会撞目录锁。
                # 判登录态根本不需要开浏览器。
                try:
                    prof, conn = _acct_profile(paths.TG_ROOT)
                    # 先试从运行中的 Chrome 读（会话 cookie 只有这条路读得到）
                    cd = read_cookie_via_cdp()
                    if cd.get("ok"):
                        _bslog("来源=内核 方式=CDP直读 profile=%s %s"
                               % (os.path.basename(prof), cd.get("detail")))
                        return self._json({"ok": True, "login": True, "level": "valid",
                                           "profile": os.path.basename(prof),
                                           "account": (conn or {}).get("name") or "",
                                           "cookies": cd.get("cookies") or {},
                                           "msg": cd.get("detail") or ""})
                    _cdp_note = cd.get("detail") or ""
                    ck = check_login_cookie(prof)
                    _bslog("来源=内核 方式=cookie直读 profile=%s level=%s 判定=%s %s"
                           % (os.path.basename(prof), ck.get("level"),
                              "有效" if ck.get("login") else "失效",
                              (ck.get("detail") or "")[:120]))
                    return self._json({"ok": True, "login": ck.get("login"),
                                       "level": ck.get("level"),
                                       "profile": os.path.basename(prof),
                                       "account": (conn or {}).get("name") or "",
                                       "cookies": ck.get("cookies") or {},
                                       "cdp": _cdp_note,
                                       "msg": (ck.get("detail") or "")
                                              + ("｜CDP: " + _cdp_note if _cdp_note else "")})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
            if p == "/api/browser-login":
                # 引导登录：打开【当前激活连接】对应 profile 的淘宝登录页（cookie 存该 profile）
                import subprocess
                try:
                    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
                    TG = paths.TG_ROOT
                    prof, conn = _acct_profile(TG)
                    os.makedirs(prof, exist_ok=True)
                    # 关键：先让 Chrome 保留会话 cookie，否则登录成功、关窗口就丢
                    _sok, _smsg = _ensure_session_restore(prof)
                    # 入口选卖家中心而不是 login.taobao.com：
                    # 专门的登录页是淘宝风控重点，从卖家中心被重定向过去上下文更自然。
                    # 想回旧入口加 ?url=login
                    # 注意：do_GET 里没有 qs 变量（只有 p = self.path），
                    # 这里直接从 URL 串判断，别再引用未定义的名字。
                    _u = "https://myseller.taobao.com/home.htm/SellManage/on_sale"
                    if "url=login" in p:
                        _u = "https://login.taobao.com/member/login.jhtml"
                    # 带调试端口启动：这样登录后能直接从运行中的 Chrome 读 cookie，
                    # 不依赖会话 cookie 落盘（实测落盘靠不住，关窗口就清）
                    subprocess.Popen([chrome, "--user-data-dir=" + prof,
                                      "--remote-debugging-port=%d" % LOGIN_CDP_PORT, _u])
                    nm = (conn or {}).get("name") or "默认"
                    return self._json({"ok": True, "profile": os.path.basename(prof), "account": nm,
                                       "session_restore": _smsg,
                                       "msg": "已打开【%s】（profile: %s）。%s。"
                                              "登录后看见卖家中心商品列表就算成功——正常关窗口不会丢 cookie"
                                              % (nm, os.path.basename(prof), _smsg)})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
            if p == "/api/browser-clear":
                # 清除 Cookie：删 chrome_profile cookie 文件（登出）——重登需引导登录
                try:
                    _tg = paths.TG_ROOT
                    prof = _acct_profile(_tg)[0]      # 只清当前激活连接的 profile
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
                    py = paths.TG_PYTHON
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
            if p.startswith("/api/plugin-uninstall?"):
                # 卸载：删 plugins/<id> 整个目录（本机管理——插件页调，市场页不碰）
                import urllib.parse
                import shutil
                q = urllib.parse.parse_qs(p.split("?", 1)[1])
                pid = (q.get("id", [""])[0] or "").strip()
                if not re.match(r"^[a-zA-Z0-9_-]{1,40}$", pid):
                    return self._json({"ok": False, "error": "非法插件 id"})
                base = os.path.join(PLUGINS_DIR, pid)
                if not os.path.isdir(base):
                    return self._json({"ok": False, "error": "没装「%s」" % pid})
                try:
                    shutil.rmtree(base, ignore_errors=True)
                    return self._json({"ok": True, "id": pid})
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
            if p.startswith("/api/desk-set"):
                # 书桌「工作台」tab 的自设目录 → paths.local.json 的 desk 键（配置进文件、可改可拷）
                import urllib.parse
                q = urllib.parse.parse_qs(p.split("?", 1)[1]) if "?" in p else {}
                nd = (q.get("p", [""])[0] or "").strip()
                if not nd:
                    return self._json({"ok": False, "error": "缺目录 p"})
                # 容错：给个文件就取它的目录
                if os.path.isfile(nd):
                    nd = os.path.dirname(nd)
                if not os.path.isdir(nd):
                    return self._json({"ok": False, "error": "目录不存在：%s" % nd})
                try:
                    paths.set_value("desk", nd)
                    return self._json({"ok": True, "dir": str(getattr(paths, "DESK", nd))})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
            if p.startswith("/api/desk"):
                # 书桌两 tab：chat=当前处理的产品目录（素材根）；work=自设目录
                # 返回该目录的文件列表（前端沿用那一套渲染/过滤/排序）
                import urllib.parse
                q = urllib.parse.parse_qs(p.split("?", 1)[1]) if "?" in p else {}
                tab = (q.get("tab", ["chat"])[0] or "chat").strip()
                sub = (q.get("p", [""])[0] or "").strip()
                if tab == "chat":
                    d = MAT_ROOT
                else:
                    d = str(getattr(paths, "DESK", "") or "")
                if sub:
                    # 允许钻进子目录（相对当前根）
                    cand = sub if os.path.isabs(sub) else os.path.join(d, sub)
                    if os.path.isdir(cand):
                        d = cand
                if not d or not os.path.isdir(d):
                    return self._json({"ok": True, "tab": tab, "dir": d, "files": [], "error": "目录不存在"})
                out = []
                try:
                    for name in sorted(os.listdir(d)):
                        fp = os.path.join(d, name)
                        try:
                            st = os.stat(fp)
                            out.append({"name": name, "path": fp, "is_dir": os.path.isdir(fp),
                                        "size": (0 if os.path.isdir(fp) else st.st_size),
                                        "mtime": int(st.st_mtime)})
                        except Exception:
                            continue
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})
                return self._json({"ok": True, "tab": tab, "dir": d, "files": out})
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
            _log_err("serve_file", e, fp=str(fp)[:160])
            return self._json({"error": str(e)}, 500)
        return super().do_GET()


def _bind_server():
    """绑端口：先试配置端口，被占则往后顺延，最后交给系统分配。
    端口不写死——壳靠 runtime/port.json + /api/ping 认出跑着的实例。"""
    tried = [PORT] + [PORT + i for i in range(1, 11)] + [0]
    last = None
    for p in tried:
        try:
            srv = socketserver.ThreadingTCPServer(("127.0.0.1", p), Handler)
        except OSError as e:
            last = e
            continue
        srv.allow_reuse_address = True
        srv.daemon_threads = True      # 请求线程跟着主进程走：窗口一关不留幽灵进程
        return srv, srv.server_address[1]
    raise RuntimeError("端口都占着（%s）：%s" % (tried[:-1], last))


def _write_port_file(port):
    """把实际端口写下来，供桌面壳/其它工具找到这个实例。
    ui=False：**这个内核没有窗口**（源码方式直接跑就是这种）。
    双击的壳只有认到 ui=true 的实例才会去复用它——否则会把窗口开完又自己关掉，
    用户看到的是「双击没反应」。"""
    import datetime
    try:
        os.makedirs(os.path.dirname(paths.PORT_FILE), exist_ok=True)
        with open(paths.PORT_FILE, "w", encoding="utf-8") as f:
            json.dump({"app": "workbench", "port": port, "pid": os.getpid(), "ui": False,
                       "url": "http://127.0.0.1:%d/" % port, "core": core_version(),
                       "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False)
    except Exception:
        pass


def serve():
    """起服务并返回 (srv, 实际端口)；桌面壳也用这个入口"""
    srv, port = _bind_server()
    _write_port_file(port)
    if port != PORT:
        # 换端口要说话：不然用户会以为「怎么打不开」，也不知道到哪里改
        print("提示：%d 被占（可能是另一个工作台实例），本次用 %d —— 可在 设置→通用→「内核端口」改"
              % (PORT, port))
    print("优化管理工作台: http://127.0.0.1:%d/" % port)
    return srv, port


if __name__ == "__main__":
    os.chdir(BASE)
    _srv, _port = serve()
    _srv.serve_forever()
