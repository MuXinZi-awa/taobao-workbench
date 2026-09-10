# -*- coding: utf-8 -*-
"""ERP 数据插件后端：Odoo 只读查询（绝不写——生产库！）
- 凭据来自内核连接导航（conn_store.active_of("odoo")）
- 仅 authenticate / search_read / search_count / read 等只读方法
"""
import os, sys, json, io, time, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
WB = os.path.dirname(os.path.dirname(BASE))     # workbench 目录（内核）
sys.path.insert(0, WB)
LOG_FP = os.path.join(BASE, "odoo_erp.log")

try:
    import conn_store
except Exception:
    conn_store = None

_MC = {}   # 连接缓存 {url|db|user: (uid, models_proxy, db, pw, ts)}


def wlog(m):
    try:
        with open(LOG_FP, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), m))
    except Exception:
        pass


def _connect():
    """激活的 Odoo 连接 → (uid, models, db, pw, base)"""
    if not conn_store:
        raise RuntimeError("conn_store 不可用")
    c = conn_store.active_of("odoo")
    if not c:
        raise RuntimeError("没有激活的 Odoo 连接（到 设置→连接 激活一个）")
    base = (c["url"] or "").rstrip("/")
    db, user, pw = c["dbname"], c["username"], c["secret"]
    key = "%s|%s|%s" % (base, db, user)
    hit = _MC.get(key)
    if hit and time.time() - hit[4] < 1800:
        return hit[0], hit[1], db, pw, base
    import xmlrpc.client
    common = xmlrpc.client.ServerProxy(base + "/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(db, user, pw, {})
    if not uid:
        raise RuntimeError("Odoo 认证失败（账号/密码/库名）")
    models = xmlrpc.client.ServerProxy(base + "/xmlrpc/2/object", allow_none=True)
    _MC[key] = (uid, models, db, pw, time.time())
    return uid, models, db, pw, base


def _call(model, method, args, kw=None):
    uid, models, db, pw, _ = _connect()
    return models.execute_kw(db, uid, pw, model, method, args, kw or {})


FIELDS = ["id", "name", "default_code", "list_price", "standard_price", "qty_available",
          "free_qty", "incoming_qty", "barcode", "product_information_brand",
          "description_ecommerce", "description_sale", "categ_id", "uom_id", "sale_ok", "active",
          "product_information_description", "product_code_name"]


def _clean(r):
    for k, v in list(r.items()):
        if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[1], str):
            r[k] = v[1]   # many2one → 显示名
    return r


def search_lh(kw, exact=False):
    """按料号搜（料号在 name 字段）——只读"""
    op = "=" if exact else "ilike"
    rows = _call("product.product", "search_read", [[["name", op, kw]]],
                 {"fields": FIELDS, "limit": 20, "context": {"active_test": False}})
    if not rows and not exact:
        rows = _call("product.product", "search_read", [[["barcode", "=", kw]]],
                     {"fields": FIELDS, "limit": 20, "context": {"active_test": False}})
    return [_clean(dict(r)) for r in rows]


def handle(action, qs):
    try:
        if action == "conn":
            c = conn_store.active_of("odoo") if conn_store else None
            if not c:
                return {"ok": False, "error": "没有激活的 Odoo 连接（设置→连接）"}
            return {"ok": True, "name": c["name"], "url": c["url"], "db": c["dbname"], "user": c["username"]}
        if action == "query":
            kw = (qs.get("kw") or "").strip()
            if not kw:
                return {"ok": False, "error": "缺料号"}
            t0 = time.time()
            rows = search_lh(kw, exact=(qs.get("exact") == "1"))
            wlog("查询 %s → %d 条（%.1fs）" % (kw, len(rows), time.time() - t0))
            return {"ok": True, "kw": kw, "items": rows, "ms": int((time.time() - t0) * 1000)}
        if action == "stats":
            n = _call("product.product", "search_count", [[]])
            return {"ok": True, "total": n}
        return {"ok": False, "error": "未知 action"}
    except Exception as e:
        wlog("ERR %s: %s" % (action, str(e)[:120]))
        return {"ok": False, "error": str(e)[:160]}
