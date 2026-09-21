# -*- coding: utf-8 -*-
"""报表 server：读 推广一键跑/runtime/report_data.json，输出结构化数据 + 趋势解读。
只读本地 JSON，不碰浏览器（采集由 采集报表数据.bat 单独跑）。
"""
import os, io, json, time
import sys as _sys
_d = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_d, "paths.py")) and os.path.dirname(_d) != _d:
    _d = os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import paths

TG = paths.TG_ROOT
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


def _rdb_fresh():
    """强制按**文件路径**加载最新 report_db。
    为什么：工作台进程会缓存同名模块（sys.modules），插件热更新后拿到的可能是旧版
    （例如缺 get_plan_daily/get_scene_daily）→ 区间表静默退回快照、自定义区间全空。"""
    import sys as _s
    if TG not in _s.path:
        _s.path.insert(0, TG)
    try:
        import report_db as _m
        if hasattr(_m, "get_plan_daily") and hasattr(_m, "get_scene_daily"):
            return _m
    except Exception:
        pass
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("_rdb_hot", os.path.join(TG, "report_db.py"))
        _m2 = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_m2)
        return _m2
    except Exception:
        return None


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


def report(q=None):
    d = _load()
    if not d:
        return {"ok": False, "error": "暂无数据——先双击「推广一键跑\\采集报表数据.bat」采一次"}
    raw = d.get("raw") or {}
    trend_rows = []          # 已弃用：历史一律走库（原为 JSON 回退，会跨账号串台）
    acct = {}                # 已弃用：总计从 days(库) 汇总
    _mid = _resolve_account(raw)   # JSON（上次采集）的账号
    _act = _active_conn()          # 当前激活连接（名称 + 绑定的 memberId）
    # 该显示谁的数：激活连接的绑定优先；未绑定（新号未采集）→ 空 → 面板清空
    _target = str(_act.get("member_id") or "").strip()
    if not _target and not _act.get("name"):
        _target = _mid                      # 根本没有激活连接 → 按 JSON 兼容
    stale = (str(_target) != str(_mid))     # 目标 ≠ 当次采集 → 显示库存档/空（不显示别人数据）
    scene = {"list": []}     # 已弃用：场景走库
    charge = {}              # 已弃用：仅保留返回字段占位

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
    # 库空就是空——不从 JSON 回退（JSON 是单份，跨账号会串台）

    # 总计：从 days（库）汇总——与图表/场景同源，不再取 JSON 的账户汇总
    _tc = sum(x["charge"] for x in days)
    _ta = sum(x["amt"] for x in days)
    _tpv = sum(x["adPv"] for x in days)
    _tk = sum(x["click"] for x in days)
    _tn = sum(x["num"] for x in days)
    total = {
        "charge": round(_tc, 2), "adPv": _tpv, "click": _tk,
        "ctr": round(_tk / _tpv, 4) if _tpv else 0, "amt": round(_ta, 2),
        "num": _tn, "cart": sum(x["cart"] for x in days),
        "ecpc": round(_tc / _tk, 3) if _tk else 0,
        "cvr": round(_tn / _tk, 4) if _tk else 0,
        "roi": round(_ta / _tc, 2) if _tc else 0,
    }

    # ── 区间表（计划/场景）：从**按日明细**按区间聚合 —— 与 KPI/走势**同源**（daily 表） ──
    #    何必改：旧法用「区间快照(7/30/all) + 今日快照合并」，与 KPI/走势（读 daily，T+1）口径不同
    #    （实测：全区间快照 410.98 + 今日 20.1 = 431.08，而 KPI 是 T+1 值）；且「自定义范围」没有对应快照，
    #    前端只能落到 all。按日明细后任意区间可算。
    #    口径：窗口截至【昨天】，与前端 curDays() 的 T+1 对齐；今天未定稿，不进区间表。
    _rng = ("7", "30", "all")
    _sc_db, _pl_db = {}, {}
    _rdb0 = None
    try:
        import sys as _s0
        if TG not in _s0.path:
            _s0.path.insert(0, TG)
        _rdb0 = _rdb_fresh()      # 按文件强制加载最新版（进程里缓存的可能是旧版）
        if _target:
            for _t in _rng + ("today",):
                _sc_db[_t], _ = _rdb0.get_scenes(account=_target, tag=_t)
                _pl_db[_t], _ = _rdb0.get_plans(account=_target, tag=_t)
    except Exception:
        pass

    def _merge_metric(a, b):
        """今日 b 并进 a：数值相加，比率重算"""
        c = dict(a)
        for _f2 in ("charge", "adPv", "click", "amt", "num"):
            c[_f2] = (c.get(_f2) or 0) + (b.get(_f2) or 0)
        _pv = c.get("adPv") or 0
        _ck = c.get("click") or 0
        _ch = c.get("charge") or 0
        c["ctr"] = round(_ck / _pv, 4) if _pv else 0
        c["ecpc"] = round(_ch / _ck, 3) if _ck else 0
        return c

    def _merge_by(cur, today, keyf):
        m = {}
        for r in cur:
            m[keyf(r)] = dict(r)
        for r in today:
            k = keyf(r)
            m[k] = _merge_metric(m[k], r) if k in m else dict(r)
        return list(m.values())

    def _sc_out(rows):
        return [{"name": r.get("name") or "?", "charge": _f(r.get("charge")), "adPv": _i(r.get("adPv")),
                 "click": _i(r.get("click")), "ctr": _f(r.get("ctr"), 4), "amt": _f(r.get("amt")),
                 "num": _i(r.get("num"))} for r in rows]

    def _pl_out(rows):
        o = [{"name": r.get("name") or "?", "campaignId": r.get("campaignId"),
              "charge": _f(r.get("charge")), "adPv": _i(r.get("adPv")), "click": _i(r.get("click")),
              "ctr": _f(r.get("ctr"), 4), "amt": _f(r.get("amt")), "num": _i(r.get("num")),
              "ecpc": _f((r.get("charge") or 0) / r.get("click"), 3) if r.get("click") else 0}
             for r in rows]
        o.sort(key=lambda x: -x["charge"])
        return o

    _today_sc = _sc_db.get("today") or []
    _today_pl = _pl_db.get("today") or []

    import datetime as _dt4
    _yd = _dt4.date.today() - _dt4.timedelta(days=1)

    def _win(tag):
        if tag == "7":
            return (_yd - _dt4.timedelta(days=6)).isoformat(), _yd.isoformat()
        if tag == "30":
            return (_yd - _dt4.timedelta(days=29)).isoformat(), _yd.isoformat()
        return "1970-01-01", _yd.isoformat()   # all：起点钉死（空串会被 report_db 当"不过滤"）

    def _range_plans(_s, _e):
        """按区间从**按日明细**聚合；无明细（老库）返回 None → 调用方回退快照"""
        if not (_rdb0 and _target):
            return None
        try:
            _rows = _rdb0.get_plan_daily(account=_target, start=_s or None, end=_e)
        except Exception:
            return None
        return _pl_out(_rows) if _rows else None

    def _range_scenes(_s, _e):
        if not (_rdb0 and _target):
            return None
        try:
            _rows = _rdb0.get_scene_daily(account=_target, start=_s or None, end=_e)
        except Exception:
            return None
        return _sc_out(_rows) if _rows else None

    plans_by_range, scenes_by_range = {}, {}
    for _t in _rng:
        _ws, _we = _win(_t)
        _pr, _sr = _range_plans(_ws, _we), _range_scenes(_ws, _we)
        if _pr is None:      # 回退：老库尚无按日明细 → 用区间快照（合并今日）
            _pr = _pl_out(_merge_by(_pl_db.get(_t) or [], _today_pl,
                                    lambda r: str(r.get("campaignId") or r.get("name") or "?")))
        if _sr is None:
            _sr = _sc_out(_merge_by(_sc_db.get(_t) or [], _today_sc,
                                    lambda r: r.get("name") or "?"))
        plans_by_range[_t], scenes_by_range[_t] = _pr, _sr
    scenes = scenes_by_range.get("all") or []

    # 自定义区间（前端传 cs/ce）—— 同样走按日明细 → 真·联动（任意 ≤30 天窗口）
    def _qval(_n):
        try:
            if isinstance(q, dict):
                _v = q.get(_n)
            else:
                import urllib.parse as _up
                _qq = _up.parse_qs(str(q or "").lstrip("?"))
                _v = (_qq.get(_n) or [""])[0]
            return str(_v or "").strip()
        except Exception:
            return ""

    _cs, _ce = _qval("cs"), _qval("ce")
    custom_ok = bool(_cs and _ce and _cs <= _ce)
    plans_custom = _range_plans(_cs, _ce) if custom_ok else None
    scenes_custom = _range_scenes(_cs, _ce) if custom_ok else None

    # ── 趋势解读：也跟随所选范围（与区间表同源、同窗口）──
    #    解读必须只读所选区间——否则会让 2 天范围的表底下写「区间内 82 天挂零」。
    def _tot_of(_ds):
        _c = sum(x["charge"] for x in _ds); _a = sum(x["amt"] for x in _ds)
        _pv = sum(x["adPv"] for x in _ds); _k = sum(x["click"] for x in _ds)
        _n = sum(x["num"] for x in _ds)
        return {"charge": round(_c, 2), "adPv": _pv, "click": _k,
                "ctr": round(_k / _pv, 4) if _pv else 0, "amt": round(_a, 2), "num": _n,
                "cart": sum(x["cart"] for x in _ds),
                "ecpc": round(_c / _k, 3) if _k else 0,
                "cvr": round(_n / _k, 4) if _k else 0,
                "roi": round(_a / _c, 2) if _c else 0}

    def _days_win(_s, _e):
        _s = _s or ""; _e = _e or "9999-12-31"
        return [x for x in days if _s <= x["date"] <= _e]

    insight_by_range = {}
    for _t in _rng:
        _ws, _we = _win(_t)
        _dd = _days_win(_ws, _we)
        insight_by_range[_t] = _insight(_dd, _tot_of(_dd), scenes_by_range.get(_t) or [], None)
    insight_custom = None
    if custom_ok:
        _dd = _days_win(_cs, _ce)
        insight_custom = _insight(_dd, _tot_of(_dd), scenes_custom or [], None)

    if stale:
        try:
            import sys as _s2
            if TG not in _s2.path:
                _s2.path.insert(0, TG)
            import report_db as _rdb2
            # 区间表已改由「按日明细」在 report() 里直接算（与 KPI 同源）—— 此处不再用快照覆盖
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

    # 今日：从**库**读（按账户）——JSON 是单份文件，跨账号必串台
    import datetime as _dt3
    _td3 = _dt3.date.today().isoformat()
    _t3 = []
    try:
        import sys as _s3
        if TG not in _s3.path:
            _s3.path.insert(0, TG)
        import report_db as _rdb3
        _t3 = _rdb3.get_daily(account=_target, start=_td3, end=_td3) if _target else []
    except Exception:
        _t3 = []
    ta = {}
    if _t3:
        _t = _t3[0]
        ta = {"charge": _t.get("charge"), "adPv": _t.get("adPv"), "click": _t.get("click"),
              "ctr": _t.get("ctr"), "amt": _t.get("amt"), "num": _t.get("num"),
              "cart": _t.get("cart"), "ecpc": _t.get("ecpc"), "cvr": _t.get("cvr")}
    # 今日 KPI：优先用 tag='today' 场景快照汇总（与下方「今日计划/场景」表**同源**）。
    # 为什么：全量采集(_report_fetch.py)的日粒度接口**不含当天**，daily 表只有 T+1 历史，
    # 今天行仅由「今日采集」(_report_today.py)写入。若只读 daily，全量采集后面板 KPI 会整排 0，
    # 而下方表格读快照有数 → 同一块「今日」两个数据源打架。此处统一为快照源。
    _kpi_sc = _sc_db.get("today") or []
    if _kpi_sc:
        _k_charge = sum(_f(r.get("charge")) for r in _kpi_sc)
        _k_pv = sum(_i(r.get("adPv")) for r in _kpi_sc)
        _k_click = sum(_i(r.get("click")) for r in _kpi_sc)
        _k_amt = sum(_f(r.get("amt")) for r in _kpi_sc)
        _k_num = sum(_i(r.get("num")) for r in _kpi_sc)
    else:
        # 回退：daily 今天行（今日采集会写；全量采集后通常为空）
        _k_charge = _f(ta.get("charge"))
        _k_pv = _i(ta.get("adPv"))
        _k_click = _i(ta.get("click"))
        _k_amt = _f(ta.get("amt"))
        _k_num = _i(ta.get("num"))
    today_total = {
        "charge": round(_k_charge, 2), "adPv": _k_pv, "click": _k_click,
        "ctr": round(_k_click / _k_pv, 4) if _k_pv else 0,
        "amt": round(_k_amt, 2), "num": _k_num,
        # 加购数：快照表无 cart 列 → 取 daily 今天行（今日采集会写；没跑则为 0）
        "cart": _i(ta.get("cart")),
        "ecpc": round(_k_charge / _k_click, 3) if _k_click else 0,
        "cvr": round(_k_num / _k_click, 4) if _k_click else 0,
    }
    today_total["roi"] = round(_k_amt / _k_charge, 2) if _k_charge else 0
    # 今日计划：从库读（tag='today'，已按 account 隔离）——不再碰 JSON
    plans_today = _pl_out(_pl_db.get("today") or [])

    # 今日场景：从库读（tag='today'）——不再碰 JSON
    scenes_today = _sc_out(_sc_db.get("today") or [])

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
            "todayDate": _td3,
            "accountName": _act.get("name") or "", "memberId": _mid,
            "activeMember": _act.get("member_id") or "",
            "accountMatch": ((str(_act.get("member_id")) == str(_mid)) if _act.get("member_id")
                             else (False if _mid else None)),
            "days": days, "total": total, "scenes": scenes, "scenesByRange": scenes_by_range,
            "plansByRange": plans_by_range, "today": today_total, "plansToday": plans_today,
            "plansCustom": plans_custom or [], "scenesCustom": scenes_custom or [],
            "customOk": custom_ok, "customRange": {"cs": _cs, "ce": _ce} if custom_ok else None,
            "insightByRange": insight_by_range, "insightCustom": insight_custom,
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


def smart_refresh(headless=True):
    """智能刷新：库里缺该账户历史 → 全量采集；只缺今日 → 只补今日（省时间）"""
    import datetime as _dt
    a = _active_conn()
    tgt = str(a.get("member_id") or "").strip()
    last = ""
    if tgt:
        try:
            import sys as _s3
            if TG not in _s3.path:
                _s3.path.insert(0, TG)
            import report_db as _rdb3
            rows = _rdb3.get_daily(account=tgt)
            if rows:
                last = rows[-1]["date"] or ""
        except Exception:
            pass
    yest = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    # 缺口检测（近30天）：不能只看最后一天——中间断档（如死机没采）也得全量补
    _gaps = []
    _stale = []
    if tgt:
        try:
            import sys as _s4
            if TG not in _s4.path:
                _s4.path.insert(0, TG)
            import report_db as _rdb4
            _rows4 = _rdb4.get_daily(account=tgt)
            if _rows4:
                _ds = set(x["date"] for x in _rows4)
                _earliest = min(_ds)
                _d0 = _dt.date.today()
                for _i in range(30):
                    _k = (_d0 - _dt.timedelta(days=_i)).isoformat()
                    if _k < _earliest:
                        break
                    if _k not in _ds:
                        _gaps.append(_k)
                # 新鲜度：最近 7 天里，"在当天就采了的"记录 = 那天还没过完 → 不是最终值
                # （ts 查询需要 ts 字段，report_db.get_daily 不返回它 → 直接查库）
                import sqlite3 as _sq
                _cc = _sq.connect(_rdb4.DB)
                _tsm = {r0: t0 for r0, t0 in _cc.execute(
                    "SELECT date, ts FROM daily WHERE account=?", (tgt,)).fetchall()}
                _cc.close()
                _today_s = _dt.date.today().isoformat()
                for _r0 in _rows4[-7:]:
                    _dd = _r0["date"]
                    if _dd >= _today_s:
                        continue                     # 今天跳过：今天本就该实时采，不算"未定稿"
                    _tt = _tsm.get(_dd) or ""
                    if _tt and _tt[:10] <= _dd:      # 过去某天"当天就采了" → 没等到定稿 → 重采
                        _stale.append(_dd)
        except Exception:
            _gaps = []
    need_full = (not last) or (last < yest) or bool(_gaps) or bool(_stale)
    if need_full:
        r = refresh(headless=headless)
        r["mode"] = "full"
        if _stale:
            r["reason"] = "有 %d 天是当天采的（未定稿：%s）→ 全量重采" % (
                len(_stale), "、".join(sorted(_stale)[-3:]))
        elif _gaps:
            r["reason"] = "近30天缺 %d 天（%s）→ 全量补齐" % (len(_gaps), "、".join(sorted(_gaps)[:5]))
        else:
            r["reason"] = ("该账户暂无历史存档" if not last else "存档止于 %s，需补全" % last)
        return r
    r = refresh_today(headless=headless)
    r["mode"] = "today"
    r["reason"] = "历史已齐（至 %s），只补今日" % last
    return r


def handle(action, qs):
    if action == "report":
        return report(qs)
    if action == "refresh":
        return smart_refresh(headless=True)      # 智能分流：缺历史走全量，否则只补今日
    if action == "refresh-all":
        return refresh(headless=True)            # 强制全量
    return {"ok": False, "error": "unknown action: " + str(action)}
