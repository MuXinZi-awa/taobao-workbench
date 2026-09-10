# -*- coding: utf-8 -*-
"""连接导航存储：多连接（淘宝店铺 / Odoo ERP）管理
- 库：data/conn.db（sqlite3 标准库——零下载）
- 密码：Windows DPAPI 加密（ctypes 调 crypt32——可逆、绑当前用户、零依赖；绝不明文/MD5）
- 类型：taobao（店铺账号）/ odoo（ERP：url+dbname+账号+密码）/ custom
"""
import os, io, json, sqlite3, ctypes, ctypes.wintypes, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
DB = os.path.join(DATA, "conn.db")

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
    # 迁移：旧库补列
    cols = [r[1] for r in c.execute("PRAGMA table_info(conn)").fetchall()]
    for col, ddl in (("port", "TEXT"), ("profile", "TEXT")):
        if col not in cols:
            c.execute("ALTER TABLE conn ADD COLUMN %s %s" % (col, ddl))
    return c

def list_all(mask=True):
    c = _conn()
    rows = c.execute("SELECT id,type,name,url,port,dbname,username,note,active,created,profile FROM conn ORDER BY id").fetchall()
    c.close()
    return [{"id": r[0], "type": r[1], "name": r[2], "url": r[3], "port": r[4], "dbname": r[5],
             "username": r[6], "note": r[7], "active": bool(r[8]), "created": r[9], "profile": r[10]} for r in rows]

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
    """按类型各自激活（淘宝与 ERP 互不影响——不同类各有一个当前）"""
    c = _conn()
    row = c.execute("SELECT type FROM conn WHERE id=?", (cid,)).fetchone()
    if row:
        c.execute("UPDATE conn SET active=0 WHERE type=?", (row[0],))
        c.execute("UPDATE conn SET active=1 WHERE id=?", (cid,))
    c.commit()
    c.close()

def active_of(ctype):
    """某类型的当前激活连接（含明文密码——仅供脚本调用）"""
    c = _conn()
    r = c.execute("SELECT id,type,name,url,port,dbname,username,secret,note,profile FROM conn WHERE active=1 AND type=?", (ctype,)).fetchone()
    c.close()
    if not r:
        return None
    return {"id": r[0], "type": r[1], "name": r[2], "url": r[3], "port": r[4], "dbname": r[5],
            "username": r[6], "secret": dpapi_decrypt(r[7]), "note": r[8], "profile": r[9]}

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
