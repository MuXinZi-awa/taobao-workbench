# -*- coding: utf-8 -*-
"""推广库存巡检 · 后端

三件事：
  1. pull   —— 拉万相台全部计划单元（**子进程**跑 _worker.py，见下）
  2. check  —— CPU 侧对撞：ERP 在手 vs 单元状态，出「该停 / 该开 / 认不出」三张表
  3. pause / enable —— 对指定单元执行切换（后台子进程 + 进度轮询）

**为什么执行都走子进程**：
  workbench 的 HTTP handler 线程里一碰 asyncio 就炸（`name 'base_events' is not defined`），
  playwright 必死。所以所有浏览器活都在 _worker.py 里跑，插件只负责起进程和读结果。

**账号隔离**：
  数据目录按活动连接的 member_id 分（runtime/<member_id>/），
  状态栏始终显示「连接名 · member_id」，切号后自动读对应目录。

踩过的坑（别再改回去）：
  - **csrfId 只能从「页面自己发 findPage 的请求」里抓**，window.g_config / window.mtop 全是 null
  - **批量提交单批上限 40**，93 个会静默失败（页面返回成功、实际一个没停）
  - **按钮/菜单文本不能用精确匹配**：「批量修改状态」实际带图标字符 \ue646；
    有的计划把「正在推广」「暂停推广」塞进同一个节点
  - **不认 click 返回值**，要数页面真实 checked 数
  - **标签页超过 10 个，页面操作静默失效**
  - **浏览器只连不开**（CDP 9222），自己 launch 会和上品/推广批次抢 profile 锁
"""

import os, sys, json, io, re, csv, time, datetime, subprocess, tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
WB = os.path.dirname(os.path.dirname(BASE))
sys.path.insert(0, WB)
RT_ROOT = os.path.join(BASE, "runtime")
os.makedirs(RT_ROOT, exist_ok=True)
LOG_FP = os.path.join(BASE, "promo_stock_check.log")
WORKER = os.path.join(BASE, "_worker.py")

ERP_CSV = os.environ.get("PSC_ERP_CSV") or \
    r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\数据\ERP入库_全量_1026.csv"

PY = sys.executable


def wlog(m):
    try:
        with io.open(LOG_FP, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), m))
    except Exception:
        pass


def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def active_conn():
    """当前活动淘宝连接（可能为 None）"""
    try:
        import conn_store
        return conn_store.active_of("taobao")
    except Exception:
        return None


def rtdir():
    c = active_conn()
    mid = str(c["member_id"]) if (c and c.get("member_id")) else "shared"
    d = os.path.join(RT_ROOT, mid)
    os.makedirs(d, exist_ok=True)
    return d, mid, c


def _run_worker(args, timeout=240):
    """起 _worker.py 同步跑，返回 (ok, 结果字典)"""
    out_fp = os.path.join(tempfile.gettempdir(), "psc_out_%d.txt" % os.getpid())
    err_fp = out_fp + ".err"
    try:
        with io.open(out_fp, "w", encoding="utf-8") as fo, io.open(err_fp, "w", encoding="utf-8") as fe:
            subprocess.run([PY, "-X", "utf8", WORKER] + list(args),
                           stdin=subprocess.DEVNULL, stdout=fo, stderr=fe,
                           timeout=timeout, cwd=BASE)
        txt = io.open(out_fp, encoding="utf-8", errors="replace").read()
        err = io.open(err_fp, encoding="utf-8", errors="replace").read()
    except subprocess.TimeoutExpired:
        return False, {"error": "worker 超时（%d 秒）" % timeout}
    except Exception as e:
        return False, {"error": "起 worker 失败: %s" % str(e)[:120]}
    m = re.search(r"^RESULT (\{.*\})$", txt, re.M)
    if not m:
        tail = (err or txt)[-400:]
        return False, {"error": "worker 没返回结果", "tail": tail}
    try:
        return True, json.loads(m.group(1))
    except Exception as e:
        return False, {"error": "解析 worker 结果失败: %s" % str(e)[:100]}


# ---------- ERP ----------

def _load_erp():
    if not os.path.isfile(ERP_CSV):
        raise RuntimeError("ERP 数据文件不存在：%s" % ERP_CSV)
    stock, hdr = {}, None
    with io.open(ERP_CSV, encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if hdr is None:
                hdr = r
                i_n = hdr.index("在手数量") if "在手数量" in hdr else 10
                continue
            if not r:
                continue
            v = (r[i_n] if i_n < len(r) else "").strip()
            try:
                n = float(v) if v else None
            except Exception:
                n = None
            k = _norm(r[0])
            if len(k) >= 4:
                prev = stock.get(k)
                if prev is None or (n is not None and (prev[1] is None or n > prev[1])):
                    stock[k] = (r[0].strip(), n)
    return stock


# ---------- 动作 ----------

def pull(qs):
    d, mid, c = rtdir()
    ok, r = _run_worker(["pull"], timeout=300)
    if not ok:
        wlog("pull 失败: %s" % r.get("error"))
        return {"ok": False, "error": r.get("error"), "tail": r.get("tail", "")[:400]}
    wlog("pull: %s 拉到 %d 单元" % (mid, r.get("units", 0)))
    r["connName"] = c["name"] if c else ""
    return r


def check(qs=None):
    d, mid, c = rtdir()
    fp = os.path.join(d, "units.json")
    if not os.path.isfile(fp):
        return {"ok": False,
                "error": "当前账号（%s）还没有单元数据，先点「① 拉数据」" % (c["name"] if c else mid)}
    data = json.load(io.open(fp, encoding="utf-8"))
    units = data.get("units") or []
    stock = _load_erp()
    codes = sorted([k for k in stock if len(k) >= 5], key=len, reverse=True)

    to_pause, to_enable, unknown = [], [], []
    for u in units:
        nt = _norm(u["title"])
        hit = ""
        for cd in codes:
            if cd in nt:
                hit = cd
                break
        if not hit:
            unknown.append(dict(u, code="", stock=None))
            continue
        raw, n = stock[hit]
        rec = {"unitId": u["unitId"], "campaignId": u["campaignId"], "title": u["title"],
               "code": raw, "stock": n, "status": u["status"], "itemId": u.get("itemId", "")}
        if n is None:
            unknown.append(rec)
        elif n <= 0 and u["status"] == "start":
            to_pause.append(rec)
        elif n > 0 and u["status"] == "pause":
            to_enable.append(rec)

    res = {"ok": True, "memberId": mid, "connName": c["name"] if c else "",
           "pulledAt": data.get("ts", ""),
           "total": len(units), "toPause": len(to_pause), "toEnable": len(to_enable),
           "unknown": len(unknown),
           "paused": sum(1 for u in units if u["status"] == "pause"),
           "started": sum(1 for u in units if u["status"] == "start"),
           "detail": {"toPause": to_pause[:800], "toEnable": to_enable[:400], "unknown": unknown[:400]}}

    tag = time.strftime("%m%d_%H%M")
    for name, rows in (("该停", to_pause), ("该开", to_enable), ("认不出", unknown)):
        p = os.path.join(d, "%s_%s.csv" % (name, tag))
        with io.open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["单元ID", "计划ID", "料号", "标题", "ERP在手", "当前状态", "商品ID"])
            for r in rows:
                w.writerow([r.get("unitId"), r.get("campaignId"), r.get("code", ""),
                            r.get("title", ""), r.get("stock", ""), r.get("status", ""),
                            r.get("itemId", "")])
    wlog("check(%s): 共 %d，该停 %d，该开 %d，认不出 %d"
         % (mid, len(units), len(to_pause), len(to_enable), len(unknown)))
    return res


def _start_bg(action, ids):
    """后台起 worker（停投可能十几分钟），返回 pid"""
    d, mid, c = rtdir()
    prog = os.path.join(d, "%s_progress.log" % action)
    fo = io.open(prog, "w", encoding="utf-8")
    cmd = [PY, "-X", "utf8", WORKER, action, "--ids", ",".join(ids)]
    p = subprocess.Popen(cmd, stdout=fo, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, cwd=BASE,
                         creationflags=0x00000008)   # DETACHED_PROCESS
    wlog("%s: 后台起 pid=%d，%d 个单元" % (action, p.pid, len(ids)))
    return {"ok": True, "pid": p.pid, "requested": len(ids), "log": prog, "memberId": mid}


def _switch(action, qs):
    d, mid, c = rtdir()
    ids = (qs.get("ids") or "").strip()
    if ids:
        unit_ids = [x.strip() for x in ids.split(",") if x.strip()]
    else:
        data = check({})
        if not data.get("ok"):
            return data
        unit_ids = [r["unitId"] for r in data["detail"]["toPause" if action == "pause" else "toEnable"]]
    if not unit_ids:
        return {"ok": True, "msg": "没有要处理的", "requested": 0}
    return _start_bg(action, unit_ids)


def progress(qs):
    """读后台进度的尾巴"""
    d, mid, _ = rtdir()
    out = {}
    for action in ("pause", "enable"):
        p = os.path.join(d, "%s_progress.log" % action)
        if os.path.isfile(p):
            lines = [x for x in io.open(p, encoding="utf-8", errors="replace").read().splitlines() if x.strip()]
            out[action] = {"tail": lines[-6:], "lines": len(lines),
                           "mtime": datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%H:%M:%S")}
    return {"ok": True, "memberId": mid, "progress": out}


# ---------- 路由 ----------

def handle(action, qs):
    try:
        if action == "status":
            d, mid, c = rtdir()
            fp = os.path.join(d, "units.json")
            cached = {}
            if os.path.isfile(fp):
                j = json.load(io.open(fp, encoding="utf-8"))
                cached = {"ts": j.get("ts"), "units": len(j.get("units") or [])}
            up, info = False, ""
            try:
                import urllib.request
                with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=5) as r:
                    up = True
                    info = json.loads(r.read().decode("utf-8", "replace")).get("Browser", "")
            except Exception as e:
                info = str(e)[:60]
            return {"ok": True, "cdp": up, "cdpInfo": info, "erpFile": os.path.isfile(ERP_CSV),
                    "connName": c["name"] if c else "（没有活动淘宝连接）",
                    "memberId": mid, "connId": c["id"] if c else None,
                    "cached": cached}
        if action == "pull":
            return pull(qs)
        if action == "check":
            return check(qs)
        if action == "pause":
            return _switch("pause", qs)
        if action == "enable":
            return _switch("enable", qs)
        if action == "progress":
            return progress(qs)
        return {"ok": False, "error": "未知 action: %s" % action}
    except Exception as e:
        wlog("ERR %s: %s" % (action, str(e)[:160]))
        return {"ok": False, "error": str(e)[:220]}
