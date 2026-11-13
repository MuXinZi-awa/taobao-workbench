# -*- coding: utf-8 -*-
"""账户 → 浏览器 profile 目录（一个 profile = 一套登录态，多账号隔离）

规则（按激活店铺连接）：
  1) 连接配了 profile 字段 → 用它（可填绝对路径或相对 推广一键跑 的目录名）
  2) 否则推导 chrome_profile_{安全化的连接名}
     - 推导目录已存在 → 用它
     - 不存在，但连接已绑 member_id（历史就是这个号采的）→ 用默认 chrome_profile（向后兼容，不折腾老号）
     - 否则 → 用推导目录（新建；首次会要登录，登录一次即长期有效）

用法：
    import acct_profile
    pf, conn = acct_profile.resolve()            # 按当前激活连接
    pf, conn = acct_profile.resolve(cli_profile) # 命令行覆盖优先
"""
import os, re, sys

BASE = os.path.dirname(os.path.abspath(__file__))
# 本文件有两份：推广一键跑/（运行用）和 workbench 插件的 scripts/（分发备份）。
# 插件目录下没有 chrome_profile，直接推导会指到 scripts\chrome_profile 去，
# 所以先探测：自身目录下有就用，没有就退回推广一键跑的 profile 根。
def _profile_root():
    if os.path.isdir(os.path.join(BASE, "chrome_profile")):
        return BASE
    try:
        _ev = os.environ.get("WB_TG_ROOT")
        if _ev and os.path.isdir(_ev):
            return _ev
    except Exception:
        pass
    try:
        import paths as _p2
        _tg = _p2.get("tg_root")
        if _tg and os.path.isdir(_tg):
            return _tg
    except Exception:
        pass
    for _c in (r"C:\Users\jdt-pty\Desktop\推广一键跑",):
        if os.path.isdir(_c):
            return _c
    return BASE


DEFAULT_PROFILE = os.path.join(_profile_root(), "chrome_profile")
# 工作台根目录：桌面上可能同时存在"源码"与"打包后的 exe"两套。这里写死过路径，
# 后果是从 exe 里点「刷新容量」时账号按源码那套解析，写出的文件名和面板读的对不上。
# 优先问 paths（机器特有路径的唯一出处），拿不到再退回按位置推导。
try:
    import paths as _paths
    WB = _paths.ROOT
except Exception:
    WB = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\工具\workbench"


def safe_name(name):
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", (name or "").strip())
    return s or "default"


def derive_name(conn):
    return "chrome_profile_" + safe_name((conn or {}).get("name"))


def _default_profile_for(site=None):
    """站点对应的默认 profile 目录。

    关键：1688 没有激活连接时，绝不能回落到淘宝的 DEFAULT_PROFILE——
    那会拿淘宝的登录态去开 1688，不报错、只用错账号。
    """
    s = (site or "").strip().lower()
    if s == "1688":
        return os.path.join(os.path.dirname(DEFAULT_PROFILE), "chrome_profile_1688")
    return DEFAULT_PROFILE


def resolve(profile_hint=None, conn=None, site=None):
    """→ (profile_dir, conn)；conn 为 None 表示没读到连接

    site: "taobao" / "1688"。不传时按 conn_store.current_site()（默认淘宝）。
    淘宝与 1688 各有独立的激活连接（activate() 按 type 隔离），**绝不能互相回退**。
    """
    if profile_hint:
        return profile_hint, conn
    try:
        if WB not in sys.path:
            sys.path.insert(0, WB)
        import conn_store
        if conn is None:
            if hasattr(conn_store, "active_shop"):
                conn = conn_store.active_shop(site)
            else:
                conn = conn_store.active_of("taobao")
    except Exception:
        conn = None
    if not conn:
        # 没配置也不回退到另一家：给站点专属的默认目录
        return _default_profile_for(site), None
    p = (conn.get("profile") or "").strip()
    if p:
        return (p if os.path.isabs(p) else os.path.join(BASE, p)), conn
    d = os.path.join(BASE, derive_name(conn))
    if os.path.isdir(d):
        return d, conn
    if conn.get("member_id"):
        return DEFAULT_PROFILE, conn      # 老号已绑账户 → 沿用现有 profile
    return d, conn                        # 新号 → 新建目录


def remember(profile_dir, conn=None):
    """把解析出的 profile 写回连接（方便查看/手改）。失败静默。"""
    try:
        if not conn:
            return False
        # 默认 profile 不写（保持“未配置”语义）
        if os.path.abspath(profile_dir) == os.path.abspath(DEFAULT_PROFILE):
            return False
        if WB not in sys.path:
            sys.path.insert(0, WB)
        import conn_store
        conn_store.set_profile(conn["id"], os.path.basename(profile_dir) if
                               os.path.dirname(profile_dir) == BASE else profile_dir)
        return True
    except Exception:
        return False


def cred():
    """当前激活店铺连接的登录凭据 → (username, password)
    优先级：连接库(conn_store) > 历史默认值。切号即变，无需改代码。"""
    try:
        if WB not in sys.path:
            sys.path.insert(0, WB)
        import conn_store
        a = conn_store.active_of("taobao")
        if a:
            u = (a.get("username") or "").strip()
            p = (a.get("secret") or "").strip()
            if u and p:
                return u, p
    except Exception:
        pass
    raise RuntimeError(
        "未取到淘宝凭据：请在连接库配置并激活店铺连接（连接导航 / config.json）。"
        "（已不再内置默认账号密码）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    pf, c = resolve()
    print("激活连接:", (c or {}).get("name"), "| member_id:", (c or {}).get("member_id"))
    print("解析 profile:", pf, "| 存在:", os.path.isdir(pf))
