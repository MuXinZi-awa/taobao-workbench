# -*- coding: utf-8 -*-
"""报表 server：读 推广一键跑/runtime/report_data.json，输出结构化数据 + 趋势解读。
只读本地 JSON，不碰浏览器（采集由 采集报表数据.bat 单独跑）。
"""
import os, io, json, time

TG = r"C:\Users\jdt-pty\Desktop\推广一键跑"
DATA = os.path.join(TG, "runtime", "report_data.json")


def _load():
    if not os.path.isfile(DATA):
        return None
    try:
        with io.open(DATA, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _f(x, nd=2):
    try:
        return round(float(x), nd)
    except Exception:
        return 0.0


def _i(x):
    try:
        return int(float(x))
    except Exception:
        return 0


def _insight(days, total, scenes, charge):
    """规则化趋势解读——把数字讲成人话"""
    lines = []
    if not days:
        return lines
    peak = max(days, key=lambda x: x["amt"])
    if peak["amt"] > 0:
        lines.append("区间高点在 %s：花费 %.2f、成交 %.0f（%d 笔）——量给对了就出货。"
                     % (peak["date"][5:], peak["charge"], peak["amt"], peak["num"]))
    zero = [x for x in days if x["amt"] <= 0]
    if zero:
        spend0 = sum(x["charge"] for x in zero)
        if len(zero) <= 3:
            lines.append("%s 成交挂零，共花掉 %.2f——那几天的钱没听见回声。"
                         % ("/".join(x["date"][5:] for x in zero), spend0))
        else:
            lines.append("区间内 %d 天成交挂零（占 %d%%），共花掉 %.2f——那些天的钱没听见回声。"
                         % (len(zero), round(len(zero) * 100.0 / len(days)), spend0))
    if total["charge"]:
        lines.append("整体投产比约 %.1f：每花 1 元带回 %.1f 元成交。" % (total["roi"], total["roi"]))
    if total["ctr"]:
        lines.append("点击率 %.2f%%，平均点击花费 %.2f 元。" % (total["ctr"] * 100, total["ecpc"]))
    tried = [s for s in scenes if s["charge"] > 0]
    if len(tried) == 1 and total["charge"]:
        lines.append("预算 100%% 压在「%s」上，其它场景尚未开火。" % tried[0]["name"])
    return lines


def _resolve_account(raw):
    """本次采集对应的账号（memberId）——两级回退，与采集侧 _report_fetch.py 一致；取不到返回空串"""
    tr = ((raw.get("trend") or {}).get("data") or {}).get("list") or []
    if tr:
        a = str(tr[0].get("memberId") or "")
        if a:
            return a
    al = ((raw.get("account") or {}).get("data") or {}).get("list") or []
    if al:
        return str(al[0].get("memberId") or "")
    return ""


def _active_conn():
    """当前激活的店铺连接 → {"name","member_id"}；取不到返回 {}（动态，切号即变）"""
    try:
        import sys as _s, os as _o
        WB = _o.path.dirname(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
        if WB not in _s.path:
            _s.path.insert(0, WB)
        import conn_store
        return conn_store.active_of("taobao") or {}
    except Exception:
        return {}


def report():
    d = _load()
    if not d:
        return {"ok": False, "error": "暂无数据——先双击「推广一键跑\\采集报表数据.bat」采一次"}
    raw = d.get("raw") or {}
    trend_rows = ((raw.get("trend") or {}).get("data") or {}).get("list") or []
    acct_l = ((raw.get("account") or {}).get("data") or {}).get("list") or [{}]
    acct = acct_l[0] if acct_l else {}
    _mid = _resolve_account(raw)   # JSON（上次采集）的账号
    _act = _active_conn()          # 当前激活连接（名称 + 绑定的 memberId）
    # 该显示谁的数：激活连接的绑定优先；未绑定（新号未采集）→ 空 → 面板清空
    _target = str(_act.get("member_id") or "").strip()
    if not _target and not _act.get("name"):
        _target = _mid                      # 根本没有激活连接 → 按 JSON 兼容
    stale = (str(_target) != str(_mid))     # 目标 ≠ 当次采集 → 显示库存档/空（不显示别人数据）
    scene = ((raw.get("scene") or {}).get("data") or {})
    charge = (raw.get("chargeSum") or {}).get("data") or {}

    # 账户按日：优先读本地库（历史累积、防删），库空则回退 JSON
    days = []
    try:
        import sys as _sys
        if TG not in _sys.path:
            _sys.path.insert(0, TG)
        import report_db as _rdb
        # 取不到账号 → 不查库（绝不放开全库，防跨账号串台）
        _rows = _rdb.get_daily(account=_target) if _target else []
        for _r in _rows:
            days.append({
                "date": _r.get("date") or "",
                "charge": _f(_r.get("charge")), "adPv": _i(_r.get("adPv")), "click": _i(_r.get("click")),
                "ctr": _f(_r.get("ctr"), 4), "amt": _f(_r.get("amt")),
                "num": _i(_r.get("num")), "cart": _i(_r.get("cart")),
                "ecpc": _f(_r.get("ecpc"), 3), "cvr": _f(_r.get("cvr"), 4),
            })
    except Exception:
        days = []
    if not days and not stale:
        for r in trend_rows:
            days.append({
                "date": r.get("thedate") or "",
                "charge": _f(r.get("charge")), "adPv": _i(r.get("adPv")), "click": _i(r.get("click")),
                "ctr": _f(r.get("ctr"), 4), "amt": _f(r.get("alipayInshopAmt")),
                "num": _i(r.get("alipayInshopNum")), "cart": _i(r.get("cartInshopNum")),
                "ecpc": _f(r.get("ecpc"), 3), "cvr": _f(r.get("cvr"), 4),
            })

    tot_charge = _f(acct.get("charge"))
    tot_amt = _f(acct.get("alipayInshopAmt"))
    total = {
        "charge": tot_charge, "adPv": _i(acct.get("adPv")), "click": _i(acct.get("click")),
        "ctr": _f(acct.get("ctr"), 4), "amt": tot_amt, "num": _i(acct.get("alipayInshopNum")),
        "cart": _i(acct.get("cartInshopNum")), "ecpc": _f(acct.get("ecpc"), 3),
        "cvr": _f(acct.get("cvr"), 4),
        "roi": _f(tot_amt / tot_charge, 2) if tot_charge else 0,
    }

    scenes = []
    for s in (scene.get("list") or []):
        scenes.append({
            "name": s.get("scene1Name") or s.get("bizCode") or "?",
            "charge": _f(s.get("charge")), "adPv": _i(s.get("adPv")),
            "click": _i(s.get("click")), "amt": _f(s.get("alipayInshopAmt")),
            "ctr": _f(s.get("ctr"), 4), "num": _i(s.get("alipayInshopNum")),
        })

    def _scenes(tag):
        node = (raw.get("sceneMatrix") or {}).get(tag) or {}
        lst = (node.get("data") or {}).get("list") or []
        out = []
        for s in lst:
            out.append({
                "name": s.get("scene1Name") or s.get("bizCode") or "?",
                "charge": _f(s.get("charge")), "adPv": _i(s.get("adPv")),
                "click": _i(s.get("click")), "amt": _f(s.get("alipayInshopAmt")),
                "ctr": _f(s.get("ctr"), 4), "num": _i(s.get("alipayInshopNum")),
            })
        return out

    scenes_by_range = {"7": _scenes("7"), "30": _scenes("30"), "all": _scenes("all")}

    def _plans(tag):
        rows = (raw.get("planMatrix") or {}).get(tag) or []
        out = []
        for p in rows:
            out.append({
                "name": p.get("name") or "?",
                "campaignId": p.get("campaignId"),
                "charge": _f(p.get("charge")), "adPv": _i(p.get("adPv")), "click": _i(p.get("click")),
                "ctr": _f(p.get("ctr"), 4), "amt": _f(p.get("alipayInshopAmt")),
                "num": _i(p.get("alipayInshopNum")), "ecpc": _f(p.get("ecpc"), 3),
            })
        out.sort(key=lambda x: -x["charge"])
        return out

    plans_by_range = {"7": _plans("7"), "30": _plans("30"), "all": _plans("all")}

    # ── 账号已切换：改显**激活连接账户的本地库存档**（秒刷新；没存档就是一空）──
    if stale:
        try:
            import sys as _s2
            if TG not in _s2.path:
                _s2.path.insert(0, TG)
            import report_db as _rdb2
            for _tag in ("7", "30", "all"):
                _pr, _ = _rdb2.get_plans(account=_target, tag=_tag) if _target else ([], None)
                plans_by_range[_tag] = [{"name": p.get("name") or "?", "campaignId": p.get("campaignId"),
                    "charge": _f(p.get("charge")), "adPv": _i(p.get("adPv")), "click": _i(p.get("click")),
                    "ctr": _f(p.get("ctr"), 4), "amt": _f(p.get("amt")), "num": _i(p.get("num")),
                    "ecpc": _f((p.get("charge") or 0) / p.get("click"), 3) if p.get("click") else 0} for p in _pr]
                _sr, _ = _rdb2.get_scenes(account=_target, tag=_tag) if _target else ([], None)
                scenes_by_range[_tag] = [{"name": s.get("name") or "?", "charge": _f(s.get("charge")),
                    "adPv": _i(s.get("adPv")), "click": _i(s.get("click")), "ctr": _f(s.get("ctr"), 4),
                    "amt": _f(s.get("amt")), "num": _i(s.get("num"))} for s in _sr]
        except Exception:
            pass
        scenes = scenes_by_range.get("all") or []
        _sc = sum(x["charge"] for x in days); _sa = sum(x["amt"] for x in days)
        _spv = sum(x["adPv"] for x in days); _sk = sum(x["click"] for x in days)
        _sn = sum(x["num"] for x in days)
        total = {"charge": round(_sc, 2), "adPv": _spv, "click": _sk,
                 "ctr": round(_sk / _spv, 4) if _spv else 0, "amt": round(_sa, 2), "num": _sn,
                 "cart": sum(x["cart"] for x in days),
                 "ecpc": round(_sc / _sk, 3) if _sk else 0,
                 "cvr": round(_sn / _sk, 4) if _sk else 0,
                 "roi": round(_sa / _sc, 2) if _sc else 0}
        today_total = {"charge": 0, "adPv": 0, "click": 0, "ctr": 0, "amt": 0, "num": 0,
                       "cart": 0, "ecpc": 0, "cvr": 0, "roi": 0}
        plans_today = []
        scenes_today = []

    # 今日实时
    ta = ((raw.get("todayAccount") or {}).get("data") or {}).get("list") or []
    ta = ta[0] if ta else {}
    today_total = {
        "charge": _f(ta.get("charge")), "adPv": _i(ta.get("adPv")), "click": _i(ta.get("click")),
        "ctr": _f(ta.get("ctr"), 4), "amt": _f(ta.get("alipayInshopAmt")),
        "num": _i(ta.get("alipayInshopNum")), "cart": _i(ta.get("cartInshopNum")),
        "ecpc": _f(ta.get("ecpc"), 3), "cvr": _f(ta.get("cvr"), 4),
    }
    today_total["roi"] = _f(today_total["amt"] / today_total["charge"], 2) if today_total["charge"] else 0
    plans_today = []
    for p in (raw.get("planToday") or []):
        plans_today.append({
            "name": p.get("name") or "?", "campaignId": p.get("campaignId"),
            "charge": _f(p.get("charge")), "adPv": _i(p.get("adPv")), "click": _i(p.get("click")),
            "ctr": _f(p.get("ctr"), 4), "amt": _f(p.get("alipayInshopAmt")), "num": _i(p.get("alipayInshopNum")),
        })
    plans_today.sort(key=lambda x: -x["charge"])

    scenes_today = []
    for s in ((raw.get("sceneToday") or {}).get("data") or {}).get("list") or []:
        scenes_today.append({
            "name": s.get("scene1Name") or s.get("bizCode") or "?",
            "charge": _f(s.get("charge")), "adPv": _i(s.get("adPv")),
            "click": _i(s.get("click")), "amt": _f(s.get("alipayInshopAmt")),
            "ctr": _f(s.get("ctr"), 4), "num": _i(s.get("alipayInshopNum")),
        })

    # 是否正在采集
    st = _status()
    refreshing = bool(st.get("started")) and (not os.path.isfile(DATA)
                                             or os.path.getmtime(DATA) < st.get("started", 0))
    if not refreshing and _today_stale():
        if refresh_today(headless=True).get("ok"):
            refreshing = True
    elif not refreshing and _should_auto():
        if refresh(headless=True).get("ok"):
            refreshing = True

    return {"ok": True, "fetchedAt": d.get("fetchedAt"), "range": d.get("range"),
            "stale": stale,
            "accountName": _act.get("name") or "", "memberId": _mid,
            "activeMember": _act.get("member_id") or "",
            "accountMatch": ((str(_act.get("member_id")) == str(_mid)) if _act.get("member_id")
                             else (False if _mid else None)),
            "days": days, "total": total, "scenes": scenes, "scenesByRange": scenes_by_range,
            "plansByRange": plans_by_range, "today": today_total, "plansToday": plans_today,
            "scenesToday": scenes_today, "refreshing": refreshing,
            "chargeSum": charge, "insight": _insight(days, total, scenes, charge)}


def _status():
    try:
        with io.open(os.path.join(TG, "runtime", "wb_fetch_status.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


SCHEDULE_HOURS = (9, 21)   # 每天 9:00 / 21:00 自动巡检（惰性：打开面板时检查）


def _should_auto():
    """已过调度点、且该点之后未采过 → 视为该采了"""
    import datetime as _dt
    try:
        last = os.path.getmtime(DATA)
    except Exception:
        return True
    now = time.time()
    today = _dt.date.today()
    for h in SCHEDULE_HOURS:
        pt = _dt.datetime.combine(today, _dt.time(h, 0)).timestamp()
        if now >= pt and last < pt:
            return True
    return False


def _today_stale():
    """今日数据是否过期（JSON 的 todayDate != 今天）"""
    import datetime as _dt
    d = {}
    try:
        with io.open(DATA, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        pass
    return (d.get("todayDate") or "") != _dt.date.today().isoformat()


def refresh_today(headless=True):
    """轻量采集：只采今日（~15s）"""
    import subprocess
    tf = os.path.join(TG, "_report_today.py")
    if not os.path.isfile(tf):
        return {"ok": False, "error": "缺少 _report_today.py"}
    started = time.time()
    try:
        with io.open(os.path.join(TG, "runtime", "wb_fetch_status.json"), "w", encoding="utf-8") as f:
            json.dump({"started": started, "headless": bool(headless), "kind": "today"}, f)
        args = [r"F:\Python314\python.exe", tf]
        if headless:
            args.append("--headless")
        subprocess.Popen(args, cwd=TG, creationflags=0x08000000)
        return {"ok": True, "started": started}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def refresh(headless=False):
    import subprocess
    started = time.time()
    tf = os.path.join(TG, "_report_fetch.py")
    if not os.path.isfile(tf):
        return {"ok": False, "error": "找不到采集脚本：" + tf}
    try:
        with io.open(os.path.join(TG, "runtime", "wb_fetch_status.json"), "w", encoding="utf-8") as f:
            json.dump({"started": started, "headless": bool(headless)}, f)
        args = [r"F:\Python314\python.exe", tf]
        if headless:
            args.append("--headless")
        subprocess.Popen(args, cwd=TG, creationflags=0x08000000)   # CREATE_NO_WINDOW
        return {"ok": True, "msg": "已启动采集（约 1-2 分钟）", "started": started}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def handle(action, qs):
    if action == "report":
        return report()
    if action == "refresh":
        return refresh_today(headless=True)      # 刷新 = 读库 + 只补今日（~15s）
    if action == "refresh-all":
        return refresh(headless=True)            # 全量（历史入库存档）
    return {"ok": False, "error": "unknown action: " + str(action)}
