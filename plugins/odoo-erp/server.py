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


# ===================== 新品入库报表（只读） =====================
# 口径（桦帆定 2026-09-21）：**只算 incoming（收货单）** —— dropship / internal / outgoing 一概不算，
#   “其他不归我们”。本模块只读（search_read / read），一个写接口都不碰。
# 语义（照平台截图原文）：
#   · 每产品取【历史首次入库】的 stock.picking.date_done（Date of Transfer）
#   · 该首入时间落在区间内 → 才列出；按首入时间倒序
#   · 只统计 state = done；入库时间不是计划时间（scheduled_date）也不是 stock.move.date

def _picking_type_incoming():
    rows = _call("stock.picking.type", "search_read", [[["code", "=", "incoming"]]],
                 {"fields": ["id", "name"], "limit": 50})
    return [r["id"] for r in rows]


def _first_inbound(date_to):
    """每个产品的【全历史首入】=(date_done, picking, qty)。只取 date_done < date_to 的单，够算首入。"""
    tids = _picking_type_incoming()
    if not tids:
        return {}
    picks = _call("stock.picking", "search_read",
                  [[["picking_type_id", "in", tids], ["state", "=", "done"],
                    ["date_done", "<", date_to]]],
                  {"fields": ["name", "date_done"], "limit": 5000, "order": "date_done asc"})
    pids = [p["id"] for p in picks]
    if not pids:
        return {}
    pmap = {p["id"]: p for p in picks}
    moves = _call("stock.move", "search_read",
                  [[["picking_id", "in", pids], ["state", "=", "done"]]],
                  {"fields": ["product_id", "picking_id", "quantity"], "limit": 60000})
    first = {}
    for m in moves:
        pid = m["product_id"][0] if m.get("product_id") else None
        pk = pmap.get(m["picking_id"][0]) if m.get("picking_id") else None
        if not pid or not pk or not pk.get("date_done"):
            continue
        d = pk["date_done"]
        q = float(m.get("quantity") or 0)
        cur = first.get(pid)
        if cur is None or d < cur[0]:
            first[pid] = (d, pk["name"], q)
        elif d == cur[0]:
            first[pid] = (d, cur[1], cur[2] + q)     # 同一张单多个 move → 数量相加
    return first


def _categ_chain(cid):
    """产品类别 + 它的所有父类（价表规则绑在父类上也该生效）"""
    out, cur = set(), cid
    for _ in range(10):
        if not cur or cur in out:
            break
        out.add(cur)
        r = _call("product.category", "read", [[cur], ["parent_id"]])
        cur = (r[0].get("parent_id") or [None])[0] if r else None
    return out


def _pricelist_price(plid, prod, qty):
    """按 Odoo 的优先级算价表价：具体产品 > 产品 > 类别(含父类) > 全局；同级取 min_quantity 最大且 ≤ qty。
    只实现 fixed / percentage，以及不带取整与上下限的 formula；其余**留空并说明原因**（不是瞎填）。
    返回 (price|None, note)"""
    items = _call("product.pricelist.item", "search_read", [[["pricelist_id", "=", plid]]],
                  {"fields": ["applied_on", "product_id", "product_tmpl_id", "categ_id",
                              "min_quantity", "compute_price", "fixed_price", "percent_price",
                              "base", "price_surcharge", "price_discount", "price_round",
                              "price_min_margin", "price_max_margin"],
                   "limit": 800})
    tmpl = prod.get("product_tmpl_id")
    tmpl = tmpl[0] if isinstance(tmpl, (list, tuple)) else tmpl
    chain = _categ_chain(prod["categ_id"][0] if isinstance(prod.get("categ_id"), (list, tuple)) else prod.get("categ_id"))
    cands = []
    for it in items:
        if (it.get("min_quantity") or 0) > qty:
            continue
        lvl = None
        if it.get("applied_on") == "0_product_variant" and it.get("product_id") and it["product_id"][0] == prod["id"]:
            lvl = 0
        elif it.get("applied_on") == "1_product" and it.get("product_tmpl_id") and it["product_tmpl_id"][0] == tmpl:
            lvl = 1
        elif it.get("applied_on") == "2_product_category" and it.get("categ_id") and it["categ_id"][0] in chain:
            lvl = 2
        elif it.get("applied_on") == "3_global":
            lvl = 3
        if lvl is None:
            continue
        cands.append((lvl, -(it.get("min_quantity") or 0), it))
    if not cands:
        # 没命中阶梯时 Odoo 会回落用产品自身价（实测：828922-1 qty=5000、阶梯从 30000 起，
        # 销售单行价 = 0.08 = 它的 list_price）——所以跟着回落，并注明
        return prod.get("list_price"), "没命中阶梯，用的是产品自身价（Odoo 同样回落）"
    cands.sort(key=lambda x: (x[0], x[1]))
    it = cands[0][2]
    cp = it.get("compute_price")
    b = it.get("base")
    base = prod.get("list_price") or 0.0
    if b == "standard_price":
        base = prod.get("standard_price") or 0.0
    elif b == "pricelist":
        return None, "规则基于另一张价表，没解析"
    if cp == "fixed":
        return it.get("fixed_price"), ""
    if cp == "percentage":
        return base * (1 - (it.get("percent_price") or 0) / 100.0), ""
    if cp == "formula":
        if (it.get("price_round") or 0) or (it.get("price_min_margin") or 0) or (it.get("price_max_margin") or 0):
            return None, "公式规则带取整/上下限，没解析"
        d = it.get("price_discount") or 0
        return base * ((1 - d / 100.0) if d else 1.0) + (it.get("price_surcharge") or 0), "按公式（基础价×折扣+加价）算，未含取整"
    return None, "没见过的计价方式：%s" % cp


def inbound_query(qs):
    """新品入库报表（只读）：date_from / date_to / kw（型号，可空）/ plid（价格表，可空）"""
    d1 = (qs.get("from") or "").strip()
    d2 = (qs.get("to") or "").strip()
    if not d1 or not d2:
        return {"ok": False, "error": "要给开始和结束日期"}
    kw = (qs.get("kw") or "").strip()
    plid = (qs.get("plid") or "").strip()
    t0 = time.time()
    first = _first_inbound(d2)
    inwin = {k: v for k, v in first.items() if d1 <= v[0] < d2}
    if not inwin:
        return {"ok": True, "rows": [], "n": 0, "from": d1, "to": d2,
                "ms": int((time.time() - t0) * 1000), "note": "该区间内没有首次入库的产品"}
    prods = _call("product.product", "read",
                  [sorted(inwin.keys()),
                   ["name", "default_code", "categ_id", "product_tmpl_id", "list_price", "standard_price", "uom_id"]])
    pmap = {p["id"]: p for p in prods}
    rows = []
    for pid, (d, pkname, qty) in inwin.items():
        p = pmap.get(pid)
        if not p:
            continue
        name = p.get("name") or ""
        if kw and kw.lower() not in name.lower():
            continue
        rows.append({"date": d, "name": name, "disp": (p.get("display_name") or name), "code": p.get("default_code") or "",
                     "categ": (p.get("categ_id") or [None, ""])[1],
                     "qty": qty, "picking": pkname, "pid": pid})
    rows.sort(key=lambda r: r["date"], reverse=True)
    note = ""
    if plid:
        plname = ""
        for r in _call("product.pricelist", "read", [[int(plid)], ["name"]]):
            plname = r["name"]
        for r in rows:
            p = pmap.get(r["pid"])
            price, why = _pricelist_price(int(plid), p, r["qty"] or 1)
            r["price"] = price
            r["price_note"] = why
        note = "单价按价格表「%s」算（按该品本次入库数量定阶梯）；算不出的留空并注明原因" % plname
    else:
        for r in rows:
            r["price"] = None
            r["price_note"] = ""
        note = "没选价格表 → 不填单价"
    return {"ok": True, "rows": rows, "n": len(rows), "from": d1, "to": d2,
            "ms": int((time.time() - t0) * 1000), "note": note, "scope": "只算 incoming（收货单）"}


def _xlsx_blob(header, rows):
    """极简 XLSX（zip + 三段 XML；内核里没有 openpyxl，手写反而稳）"""
    import zipfile
    import io as _io
    def esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))
    def col(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    body = []
    allrows = [header] + rows
    for ri, row in enumerate(allrows, 1):
        cells = []
        for ci, v in enumerate(row):
            ref = "%s%d" % (col(ci), ri)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cells.append('<c r="%s"><v>%s</v></c>' % (ref, v))
            else:
                cells.append('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, esc(v)))
        body.append('<row r="%d">%s</row>' % (ri, "".join(cells)))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>%s</sheetData></worksheet>') % "".join(body)
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="新品入库" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbrels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
              '</Relationships>')
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def inbound_export(qs):
    """导出：CSV + XLSX 直接落文件（落盘位置走 paths，不写死）"""
    import csv as _csv
    import io as _io
    r = inbound_query(qs)
    if not r.get("ok"):
        return r
    rows = r["rows"]
    if not rows:
        return {"ok": False, "error": "这个区间没有数据，不用导"}
    header = ["首次入库日期", "型号", "名称", "编码", "类别", "数量", "单价", "单价备注", "收货单"]
    data = [[(x["date"] or "")[:19], x["name"], x["name"], x["code"], x["categ"], x["qty"],
             ("" if x.get("price") is None else x["price"]), x.get("price_note") or "", x["picking"]]
            for x in rows]
    try:
        import paths
        outdir = os.path.join(paths.DATA, "新品入库报表")
    except Exception:
        outdir = os.path.join(BASE, "out")
    os.makedirs(outdir, exist_ok=True)
    tag = (r["from"][:10] + "_" + r["to"][:10])
    fp_csv = os.path.join(outdir, "新品入库_%s.csv" % tag)
    fp_xlsx = os.path.join(outdir, "新品入库_%s.xlsx" % tag)
    with _io.open(fp_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.writer(f)
        w.writerow(header)
        w.writerows(data)
    with open(fp_xlsx, "wb") as f:
        f.write(_xlsx_blob(header, data))
    wlog("导出 %s → %d 行" % (tag, len(data)))
    return {"ok": True, "n": len(data), "csv": fp_csv, "xlsx": fp_xlsx, "dir": outdir,
            "from": r["from"], "to": r["to"], "scope": r.get("scope")}


def _pricelists():
    return _call("product.pricelist", "search_read", [[]], {"fields": ["id", "name"], "limit": 200})



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
        if action == "inbound":
            return inbound_query(qs)
        if action == "inbound-export":
            return inbound_export(qs)
        if action == "pricelists":
            return {"ok": True, "items": _pricelists()}
        return {"ok": False, "error": "未知 action"}
    except Exception as e:
        wlog("ERR %s: %s" % (action, str(e)[:120]))
        return {"ok": False, "error": str(e)[:160]}
