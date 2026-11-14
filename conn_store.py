# -*- coding: utf-8 -*-
"""连接导航存储：多连接（淘宝店铺 / Odoo ERP）管理
- 库：data/conn.db（sqlite3 标准库——零下载）
- 密码：Windows DPAPI 加密（ctypes 调 crypt32——可逆、绑当前用户、零依赖；绝不明文/MD5）
- 类型：taobao（店铺账号）/ odoo（ERP：url+dbname+账号+密码）/ custom
"""
import os, io, json, shutil, sqlite3, ctypes, ctypes.wintypes, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
# 连接库位置由 paths 决定（工作区级）——源码方式与打包后的 exe 用【同一个文件】。
# 以前固定在 <程序目录>\data\conn.db：两个壳各存一套账号，exe 那套 member_id 是空的，
# 面板就退回读旧的全局数据（数字对不上就是这么来的）。数据进库、配置留文件，这里就是"进库"。
try:
    import paths as _paths
    DB = str(_paths.CONN_DB)
except Exception:
    DB = os.path.join(BASE, "data", "conn.db")
DATA = os.path.dirname(DB)
if not os.path.isfile(DB):
    # 兜底：新位置还没有库时，把老位置那份拷过来（老文件不删，可回退）
    _old = os.path.join(BASE, "data", "conn.db")
    if os.path.isfile(_old):
        try:
            os.makedirs(DATA, exist_ok=True)
            shutil.copy2(_old, DB)
        except Exception:
            pass

# ---------- DPAPI ----------
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

def dpapi_encrypt(text):
    """明文 → DPAPI 密文 bytes（绑当前 Windows 用户）"""
    if text is None:
        text = ""
    data = text.encode("utf-8")
    bin_b, bout = _blob(data), _BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(bin_b), "wb-conn", None, None, None, 0, ctypes.byref(bout))
    if not ok:
        raise OSError("CryptProtectData 失败")
    try:
        return ctypes.string_at(bout.pbData, bout.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(bout.pbData)

def dpapi_decrypt(blob):
    """DPAPI 密文 → 明文"""
    if not blob:
        return ""
    bin_b, bout = _blob(blob), _BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(bin_b), None, None, None, None, 0, ctypes.byref(bout))
    if not ok:
        return ""
    try:
        return ctypes.string_at(bout.pbData, bout.cbData).decode("utf-8", errors="replace")
    finally:
        ctypes.windll.kernel32.LocalFree(bout.pbData)

def fp4(s):
    """密码指纹前4位（展示用——不用于登录）"""
    import hashlib
    return hashlib.md5((s or "").encode("utf-8")).hexdigest()[:8] if s else ""

# ---------- DB ----------
def _conn():
    os.makedirs(DATA, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS conn(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT, name TEXT, url TEXT, port TEXT, dbname TEXT,
        username TEXT, secret BLOB, note TEXT, profile TEXT,
        active INTEGER DEFAULT 0, created TEXT)""")
    # 设置表（当前流水线站点等）
    c.execute("""CREATE TABLE IF NOT EXISTS setting(
        k TEXT PRIMARY KEY, v TEXT)""")
    # 迁移：旧库补列
    cols = [r[1] for r in c.execute("PRAGMA table_info(conn)").fetchall()]
    for col, ddl in (("port", "TEXT"), ("profile", "TEXT"), ("member_id", "TEXT")):
        if col not in cols:
            c.execute("ALTER TABLE conn ADD COLUMN %s %s" % (col, ddl))
    return c


# ---------- 站点 ----------
# 店铺类连接的类型：淘宝 / 1688 / 其他（custom 是历史的“其他”，算店铺）
SHOP_TYPES = ("taobao", "1688", "custom")
SITE_LABEL = {"taobao": "淘宝", "1688": "1688", "custom": "其他"}


def current_site():
    """当前流水线站点（taobao / 1688）。

    为什么需要这个：淘宝和 1688 是两家不同的店，各自有独立的激活连接（activate()
    按 type 隔离）。但流水线脚本原先硬取 active_of("taobao")，跑 1688 时会静默
    拿淘宝的登录态——不报错，只用错账号。所以显式存一个“现在跑哪家”。
    """
    try:
        c = _conn()
        r = c.execute("SELECT v FROM setting WHERE k='current_site'").fetchone()
        c.close()
        v = (r[0] if r else "") or ""
        return v if v in ("taobao", "1688") else "taobao"
    except Exception:
        return "taobao"


def set_current_site(site):
    s = (site or "").strip().lower()
    if s not in ("taobao", "1688"):
        return False
    try:
        c = _conn()
        c.execute("INSERT INTO setting(k,v) VALUES('current_site',?) "
                  "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (s,))
        c.commit()
        c.close()
        bump_rev()  # 站点变更 → 通知各插件清状态
        return True
    except Exception:
        return False


def active_shop(site=None):
    """拿当前站点的激活店铺连接。

    site 不传就用 current_site()。没有激活连接时回 None——**不静默回退到另一家**，
    宁可让调用方看到空，也不要拿错账号跑完整条流水线。
    """
    s = site or current_site()
    if s == "1688":
        return active_of("1688") or active_of("custom")
    return active_of("taobao")


def shops():
    """所有店铺类型连接（不分站点），带 active 标记"""
    return [x for x in list_all() if (x.get("type") or "") in SHOP_TYPES]

def list_all(mask=True):
    c = _conn()
    rows = c.execute("SELECT id,type,name,url,port,dbname,username,note,active,created,profile,member_id FROM conn ORDER BY id").fetchall()
    c.close()
    return [{"id": r[0], "type": r[1], "name": r[2], "url": r[3], "port": r[4], "dbname": r[5],
             "username": r[6], "note": r[7], "active": bool(r[8]), "created": r[9], "profile": r[10],
             "member_id": r[11] if len(r) > 11 else ""} for r in rows]

def get_secret(cid):
    c = _conn()
    r = c.execute("SELECT secret FROM conn WHERE id=?", (cid,)).fetchone()
    c.close()
    return dpapi_decrypt(r[0]) if r else ""

def save(d):
    """新增/更新：d 含 id(可选)/type/name/url/dbname/username/secret(明文可选)/note"""
    c = _conn()
    cid = d.get("id")
    if cid:
        cur = c.execute("SELECT secret FROM conn WHERE id=?", (cid,)).fetchone()
        sec = dpapi_encrypt(d["secret"]) if d.get("secret") else (cur[0] if cur else None)
        c.execute("UPDATE conn SET type=?,name=?,url=?,port=?,dbname=?,username=?,secret=?,note=?,profile=? WHERE id=?",
                  (d.get("type"), d.get("name"), d.get("url"), d.get("port"), d.get("dbname"), d.get("username"), sec, d.get("note"), d.get("profile"), cid))
    else:
        sec = dpapi_encrypt(d.get("secret", ""))
        c.execute("INSERT INTO conn(type,name,url,port,dbname,username,secret,note,profile,active,created) VALUES(?,?,?,?,?,?,?,?,?,0,?)",
                  (d.get("type"), d.get("name"), d.get("url"), d.get("port"), d.get("dbname"), d.get("username"), sec, d.get("note"), d.get("profile"),
                   datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
        cid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit()
    c.close()
    return cid

def delete(cid):
    c = _conn()
    c.execute("DELETE FROM conn WHERE id=?", (cid,))
    c.commit()
    c.close()

def activate(cid):
    """激活连接。

    按类型各自激活（淘宝与 ERP 互不影响）；**店铺类（taobao/1688）激活时同步切站点**——
    否则会出现梓帆碰到的那种情况：两条都亮着「已激活」，但 current_site 还是另一家，
    以为切换了实际没切，功能继续用错账号。激活哪家就是哪家。
    """
    c = _conn()
    row = c.execute("SELECT type FROM conn WHERE id=?", (cid,)).fetchone()
    if row:
        t = row[0]
        c.execute("UPDATE conn SET active=0 WHERE type=?", (t,))
        c.execute("UPDATE conn SET active=1 WHERE id=?", (cid,))
        c.commit()
        c.close()
        if t in ("taobao", "1688"):
            set_current_site(t)
        bump_rev()  # 账户切换 → 通知各插件清状态
        return
    c.commit()
    c.close()

def set_member_id(cid, mid):
    """采集回填：把 memberId 记到连接上（账户 ↔ 连接 绑定）"""
    c = _conn()
    c.execute("UPDATE conn SET member_id=? WHERE id=?", (str(mid or ""), cid))
    c.commit()
    c.close()
    bump_rev()  # member_id 变更 → 插件清状态


def set_profile(cid, p):
    """回填：把该连接用的浏览器 profile 目录名记上"""
    c = _conn()
    c.execute("UPDATE conn SET profile=? WHERE id=?", (str(p or ""), cid))
    c.commit()
    c.close()


# ---------- 账户上下文（所有插件唯一入口）----------
# 背景：各插件原先各自调 active_of("taobao")，member_id 为空时又各自回退到
# 历史/全局数据，导致新店铺（如伯恩斯专卖店）显示别家的计划与容量。
# 统一规则：**未知归属绝不显示别人的数据**。
def acct_rev():
    """账户版本号。激活 / 切站点 / 绑 member_id 都 +1。

    前端轮询这个值，变了就清空本地状态重载——这是「账户广播」的最小实现，
    不需要 WebSocket，也不会漏掉任何一次切换。
    """
    try:
        c = _conn()
        r = c.execute("SELECT v FROM setting WHERE k='acct_rev'").fetchone()
        c.close()
        return int((r[0] if r else 0) or 0)
    except Exception:
        return 0


def bump_rev():
    """账户变更时调用，版本号 +1。失败不影响主流程。"""
    try:
        c = _conn()
        c.execute("INSERT INTO setting(k,v) VALUES('acct_rev','1') "
                  "ON CONFLICT(k) DO UPDATE SET v=CAST(CAST(v AS INTEGER)+1 AS TEXT)")
        c.commit()
        c.close()
        return True
    except Exception:
        return False


def acct_context(site=None):
    """★ 所有插件取账户信息的唯一入口。

    返回:
      site      taobao / 1688
      rev       版本号（变了说明账户切过，前端要清状态）
      conn      连接 dict 或 None
      name      连接名
      member_id 推广账户 ID（可能为空）
      bound     True = 有连接且 member_id 非空，可以正常取数据
      has_conn  是否有激活连接
      reason    "" / "no_conn" / "no_member_id"

    **bound=False 时调用方必须显示空态，绝不回退历史数据。**
    """
    s = site or current_site()
    rev = acct_rev()
    conn = active_shop(s)
    if not conn:
        return {"site": s, "rev": rev, "conn": None, "name": "",
                "member_id": "", "bound": False, "has_conn": False,
                "reason": "no_conn"}
    mid = str(conn.get("member_id") or "").strip()
    return {"site": s, "rev": rev, "conn": conn,
            "name": conn.get("name") or "",
            "member_id": mid, "bound": bool(mid), "has_conn": True,
            "reason": "" if mid else "no_member_id"}


def active_of(ctype):
    """某类型的当前激活连接（含明文密码——仅供脚本调用）"""
    c = _conn()
    r = c.execute("SELECT id,type,name,url,port,dbname,username,secret,note,profile,member_id FROM conn WHERE active=1 AND type=?", (ctype,)).fetchone()
    c.close()
    if not r:
        return None
    return {"id": r[0], "type": r[1], "name": r[2], "url": r[3], "port": r[4], "dbname": r[5],
            "username": r[6], "secret": dpapi_decrypt(r[7]), "note": r[8], "profile": r[9],
            "member_id": r[10] if len(r) > 10 else ""}

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("DB:", DB)
    print("现连接:", json.dumps(list_all(), ensure_ascii=False))
    # DPAPI 自测
    t = "test-密码123"
    enc = dpapi_encrypt(t)
    dec = dpapi_decrypt(enc)
    print("DPAPI 往返:", "OK" if dec == t else "FAIL", "| 密文 %d 字节" % len(enc))
