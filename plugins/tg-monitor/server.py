# -*- coding: utf-8 -*-
"""推广监控台 server：聚合状态（读文件/进程——不碰浏览器常驻）
数据源：
  1. 推广记录.csv（新——tuiguang_auto 自动化批，料号为行）
  2. 上品全量汇总.csv「推广状态」列（老——上品+推广一起 done，无记录 csv 时代）
  3. 计划容量：runtime/plan_state.json（tuiguang_auto 每批跑完写——尚未加，先读存在则显示）
  4. 进程/登录态：查 tuiguang_auto 进程 + _dual_one 探测
"""
import os, io, csv, json, subprocess, datetime

import sys as _sys
_d = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_d, "paths.py")) and os.path.dirname(_d) != _d:
    _d = os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import paths

BASE = os.path.dirname(os.path.abspath(__file__))
TG = paths.TG_ROOT
TG_REC = os.path.join(paths.DATA, "推广记录.csv")
ALL_CSV = os.path.join(paths.DATA, "上品全量汇总.csv")
PLAN_STATE = os.path.join(TG, "runtime", "plan_state.json")
# 11 计划组真名（从 campaign/horizontal/findPage.json 抓——存 runtime/plan_names.json）
PLAN_NAMES_FP = os.path.join(TG, "runtime", "plan_names.json")
_PN = {}
try:
    _PN = json.load(io.open(PLAN_NAMES_FP, encoding="utf-8"))
except Exception:
    pass
_PLAN_ORDER = ["72264315693", "83306736168", "72304256352", "83211993350", "83259474708",
               "83212131417", "83212279185", "83259606767", "83212439397", "83259874852", "83162843849"]
PLANS = [{"id": pid, "name": _PN.get(pid, pid)} for pid in _PLAN_ORDER]

def _acct():
    """当前激活店铺连接（多账号：计划/容量按账号取）"""
    try:
        import sys as _s
        _wb = paths.ROOT
        if _wb not in _s.path:
            _s.path.insert(0, _wb)
        import conn_store
        return conn_store.active_of("taobao") or {}
    except Exception:
        return {}


def _acct_fp(name):
    """按账号取 runtime 下的文件（plan_state_<member>.json）
    已绑账号 → 只认自己的（没有就是空，绝不回退全局，防串号）；未绑 → 用全局（历史兼容）"""
    mid = str(_acct().get("member_id") or "")
    base, ext = os.path.splitext(name)
    if not mid:
        return os.path.join(TG, "runtime", name)
    return os.path.join(TG, "runtime", base + "_" + mid + ext)


def plans_cur():
    """当前账号的计划列表（计划ID → 名字）
    已绑账号 → 只读该账号的 plan_names（没有就是空表，不回退默认 11 个，防串号）
    未绑 → 用默认列表（历史兼容）"""
    mid = str(_acct().get("member_id") or "")
    fp = _acct_fp("plan_names.json")
    pn = {}
    if os.path.isfile(fp):
        try:
            pn = json.load(io.open(fp, encoding="utf-8"))
        except Exception:
            pn = {}
    if pn:
        return [{"id": pid, "name": pn.get(pid, pid)} for pid in pn]
    if mid:
        return []
    return [{"id": pid, "name": pid} for pid in _PLAN_ORDER]


def _read_rows(fp):
    if not os.path.isfile(fp):
        return []
    try:
        with io.open(fp, encoding="utf-8-sig", errors="replace") as f:
            return list(csv.reader(f))
    except Exception:
        return []

TG_PID = os.path.join(TG, "runtime", "tg_pid.txt")


def _running():
    """PID 文件检测（绕开 PowerShell 进程查询转义坑）——start-batch 写 PID，查存活"""
    try:
        if not os.path.isfile(TG_PID):
            return False
        pid = io.open(TG_PID, encoding="utf-8").read().strip()
        if not pid:
            return False
        r = subprocess.run(["tasklist", "/FI", "PID eq %s" % pid, "/FO", "CSV", "/NH"],
                           capture_output=True, timeout=15)
        return ("python" in r.stdout.decode("utf-8", "replace").lower())
    except Exception:
        return False

def _units_total(plan_state):
    return sum((v.get("count") or 0) for v in plan_state.values())


def _pool_stats(rec_lh):
    """默认池进度：总料号 / 已跑（在记录） / 剩余"""
    pool = os.path.join(TG, "runtime", "推广池_0905.csv")
    total = done = 0
    try:
        with io.open(pool, encoding="utf-8-sig") as f:
            for r in csv.reader(f):
                if r and r[0].strip() and "料号" not in r[0] and "型号" not in r[0]:
                    total += 1
                    if r[0].strip() in rec_lh:
                        done += 1
    except Exception:
        pass
    return {"total": total, "done": done, "remain": total - done}


def _cleanup_probe():
    """起批前清理：杀残留 probe_status.py（它占 chrome_profile——与批并发同 profile 会杀掉批的 Chrome）"""
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*probe_status.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; 'ok'"],
            capture_output=True, timeout=15)
    except Exception:
        pass


_LG = {"v": None, "t": 0.0}


LG_FP = os.path.join(BASE, "login_state.json")   # 登录态持久化（模块每次请求重载——缓存必须落文件）


def _bslog(line):
    """登录态检测留痕：与内核写同一个文件（paths.RUNTIME/browser_status.log），便于比对两条路的差异。
    注意别写 TG\runtime——那是推广一键跑的目录，写了就和内核的不在一个文件里，白留。
    换行在这里压平——留痕是一行一条，被拆行了 grep 和肉眼比对都难。"""
    try:
        import datetime as _dt
        line = str(line).replace("\r", " ").replace("\n", " / ")
        try:
            import paths as _p
            d = _p.RUNTIME
        except Exception:
            d = os.path.join(TG, "runtime")
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "browser_status.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass


def _lg_read():
    try:
        return json.load(io.open(LG_FP, encoding="utf-8"))
    except Exception:
        return {}


def _lg_write(v, msg=""):
    try:
        io.open(LG_FP, "w", encoding="utf-8").write(
            json.dumps({"v": v, "t": __import__("time").time(), "msg": msg}))
    except Exception:
        pass


def _login(force=False):
    """登录态探测：跑 _dual_one.py 一次（**不带 --headless**）+ 90s 缓存。

    为什么不带 --headless：无头起浏览器没有登录态（cookie 不在那个会话里），判定必然失败；
    内核那条 /api/browser-status 就是不带无头跑的，两条路要一样的参数才可能给一样的结论。
    失败不吞：把原因写进 login_state.json 的 msg，"检测"按钮会弹出来。
    """
    import time as _t
    _d = _lg_read()
    if not force and _d.get("v") is not None and _t.time() - _d.get("t", 0) < 90:
        return _d.get("v")
    _py = os.path.join(TG, "runtime", "python.exe")
    _script = os.path.join(TG, "_dual_one.py")
    try:
        r = subprocess.run([_py, "-X", "utf8", "-u", _script, "1379668", ""],
                           capture_output=True, timeout=90)
    except Exception as e:
        _bslog("来源=插件 结果=探测失败 python=%s 原因=%s" % (_py, str(e)[:120]))
        _lg_write(None, "探测失败：%s" % str(e)[:120])
        return None
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    # 判据与内核一致：只认这三个词算"有登录态"；"未搜到"不算（那是没导到数据，不是登录成功）
    ok = any(k in out for k in ("已双百", "未双百", "流量加速中"))
    if ok:
        msg = "登录态有效（cookie 在 chrome_profile）"
    elif "未搜到" in out:
        msg = "未检测到登录态：查询没搜到数据（多半是登录态失效）"
    elif r.returncode != 0:
        msg = "探测失败：脚本退出码 %s；%s" % (r.returncode,
                                              (err or out).strip().replace("\n", " / ")[-140:])
    else:
        msg = "登录态失效（输出里没看到有效的登录态标志）"
    _bslog("来源=插件 脚本=_dual_one.py 参数='1379668 \"\"' headless=否 python=%s cwd=%s rc=%s 判定=%s 结论=%s"
           % (_py, os.getcwd(), r.returncode, "有效" if ok else "失效", msg))
    _lg_write(ok, msg)
    return ok

def progress():
    """跑批进度：脚本侧写的单份 runtime/progress.json 原样返回（单份多任务共用——判活/超时/区分批次由面板算）"""
    try:
        d = json.load(io.open(os.path.join(TG, "runtime", "progress.json"), encoding="utf-8"))
    except Exception:
        return {"ok": True, "progress": None}
    return {"ok": True, "progress": d}


def status():
    # 1. 推广记录.csv
    rec_rows = _read_rows(TG_REC)
    rec_lh = set()
    rec_today = 0
    today = datetime.date.today().strftime("%m-%d")
    # 这份 csv 其实没有表头（第一行就是数据）。只有当第一行真的像表头时才跳过，
    # 否则会把第一条记录漏掉（4756 行数成 4755）。
    _rows = rec_rows
    if _rows and _rows[0] and ("料号" in (_rows[0][0] or "") or "状态" in " ".join(_rows[0])):
        _rows = _rows[1:]
    for r in _rows:
        if len(r) >= 2 and r[0].strip():
            _racct = (r[3].strip() if len(r) > 3 else "") or "<MEMBER_ID>"   # 第4列 account（老数据归店铺1）
            _mid = str(_acct().get("member_id") or "")
            if _mid and _racct and _racct != _mid:
                continue
            rec_lh.add(r[0].strip())
            if len(r) >= 3 and r[2].strip().startswith(today):
                rec_today += 1
    # 2. 计划状态（rec 即全集——已并入全量汇总，不再两源合并）

    plans = []
    plan_state = {}
    _psfp = _acct_fp("plan_state.json")
    if os.path.isfile(_psfp):
        try:
            plan_state = json.load(open(_psfp, encoding="utf-8"))
        except Exception:
            pass
    # 未绑账号时 _acct_fp 会退回读【全局旧文件】（历史兼容）——那就必须在面板上说清楚，
    # 别把过期数据当现状端上来（用户看到的"数字对不上"多半就是这里）。
    _mid_now = str(_acct().get("member_id") or "")
    _stale_note = ""
    if plan_state and not _mid_now:
        _ts = sorted([str(v.get("time") or "") for v in plan_state.values()
                      if isinstance(v, dict) and v.get("time")])
        _stale_note = ("当前没有激活的店铺账号（连接里 member_id 为空）——下面这些是历史全局数据"
                       "（最后更新 %s），可能已过期。先到 设置→连接 激活/保存账号，再点「刷新容量」。"
                       % ((_ts[-1] if _ts else "时间未知")))
    for p in plans_cur():
        pid = p["id"]
        st = plan_state.get(pid, {})
        count = st.get("count")
        ts = st.get("time", "")
        plans.append({"id": pid, "name": p["name"], "count": count, "time": ts,
                      "full_known": pid in ("72264315693", "83306736168")})
    running = _running()
    # login 只读缓存——自动探测已禁（probe headless 抢 chrome_profile → 批/手动 launch 全被顶掉）
    # 登录态用手动「检测」按钮；批跑时状态看 running；容量批自更新+手动刷新
    login = _lg_read().get("v")
    _a = _acct()
    return {
        "ok": True,
        "account": {"name": _a.get("name") or "", "member_id": _a.get("member_id") or ""},
        "login": login,
        "running": running,
        "stats": {"rec": len(rec_lh), "today": rec_today, "units": _units_total(plan_state),
                 "pool": _pool_stats(rec_lh)},
        "stale": bool(_stale_note), "stale_note": _stale_note,
        "state_file": os.path.basename(_psfp) if plan_state else "",
        "plans": plans,
        "time": datetime.datetime.now().strftime("%H:%M:%S"),
    }

def handle(action, qs):
    if action == "status":
        return status()
    if action == "progress":
        return progress()
    if action == "login-check":
        """手动检测登录态（probe headless 起一次——慎用：会短暂占 profile，别在批跑时点）"""
        import time as _t
        if _running():
            return {"ok": True, "login": None, "msg": "批运行中——跳过探测"}
        _login(force=True)
        _d = _lg_read()
        return {"ok": True, "login": _d.get("v"), "msg": _d.get("msg") or ""}
    if action == "refresh-caps":
        # 刷新容量：subprocess detach 跑 refresh_caps.py（headless 静默——约 25-30s）
        import subprocess as _sp
        rt = os.path.join(TG, "runtime", "python.exe")
        try:
            lg = os.path.join(TG, "runtime", "refresh_caps.log")
            _f = io.open(lg, "a", encoding="utf-8", errors="replace")
            _f.write("\n==== %s 刷新容量开始 ====\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            _f.flush()
            # 输出必须接住：以前是 fire-and-forget，失败也看不见（"点了没反应"就是这么来的）
            _sp.Popen([rt, "-X", "utf8", "-u", os.path.join(TG, "runtime", "refresh_caps.py")],
                      cwd=TG, creationflags=0x08000000, stdout=_f, stderr=_sp.STDOUT)
            return {"ok": True,
                    "msg": "容量刷新已启动（约 30s）。完成后点刷新。日志：runtime\\refresh_caps.log"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "start-batch":
        """面板手动起批：默认池或指定 csv（qs.file=绝对路径）；返回本次待推清单头几个"""
        import subprocess as _sp
        _cleanup_probe()  # 起批前清残留 probe（防同 profile 冲突杀批）
        try:
            limit = int(qs.get("limit", "30") or 30)
        except Exception:
            limit = 30
        limit = max(1, min(limit, 200))
        rt = os.path.join(TG, "runtime", "python.exe")
        pool = qs.get("file", "") or os.path.join(TG, "runtime", "推广池_0905.csv")
        camps = ""    # 留空 → 由 tuiguang_api 自己取「未满」的计划（写死列表会带上已满的）
        out = os.path.join(TG, "runtime", "tg_panel.log")
        err = out.replace(".log", "_err.log")
        # 读取本次待推清单（表头行后的料号，跳过已推记录）
        try:
            rec_done = set()
            if os.path.isfile(TG_REC):
                for _r in csv.reader(io.open(TG_REC, encoding="utf-8-sig")):
                    if _r and _r[0].strip():
                        rec_done.add(_r[0].strip())
            lh_list = []
            if os.path.isfile(pool):
                with io.open(pool, encoding="utf-8-sig") as _pf:
                    _rows = list(csv.reader(_pf))
                head = _rows[0] if _rows else []
                ci = 0
                for _i, _h in enumerate(head):
                    if "料号" in _h or "型号" in _h:
                        ci = _i
                        break
                for _r in _rows[1:]:
                    if _r and len(_r) > ci and _r[ci].strip() and _r[ci].strip() not in rec_done:
                        lh_list.append(_r[ci].strip())
                    if len(lh_list) >= limit:
                        break
        except Exception:
            lh_list = []
        with io.open(out, "a", encoding="utf-8") as _f:
            _f.write("\n[%s] === 面板起批 limit=%d 源=%s ===\n" % (datetime.datetime.now().strftime("%H:%M:%S"), limit, os.path.basename(pool)))
        try:
            proc = _sp.Popen([rt, "-X", "utf8", "-u", os.path.join(TG, "tuiguang_api.py"),
                              "--excel", pool, "--limit", str(limit), "--campaigns", camps, "--no-pop"],
                             cwd=TG, stdout=io.open(out, "a", encoding="utf-8"),
                             stderr=io.open(err, "a", encoding="utf-8"))
            with io.open(TG_PID, "w", encoding="utf-8") as _pf:
                _pf.write(str(proc.pid))
            return {"ok": True, "msg": "批已启动（%d 品）" % limit, "batch": lh_list[:limit], "source": os.path.basename(pool)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "stop-batch":
        """停批：杀 tuiguang_auto 进程树"""
        try:
            pid = ""
            if os.path.isfile(TG_PID):
                pid = io.open(TG_PID, encoding="utf-8").read().strip()
            if pid:
                subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, timeout=30)
                try:
                    os.remove(TG_PID)
                except Exception:
                    pass
            return {"ok": True, "msg": "停止指令已发" + ("（PID %s）" % pid if pid else "")}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "single-run":
        """单品精准推广：lh(料号) + id(淘宝ID 可空——自动从推广池/lh2id 补)"""
        import subprocess as _sp
        lh = qs.get("lh", "").strip()
        if not lh:
            return {"ok": False, "error": "缺料号"}
        iid = qs.get("id", "").strip()
        if not iid:
            # 从推广池补
            pool = os.path.join(TG, "runtime", "推广池_0905.csv")
            if os.path.isfile(pool):
                for _r in csv.reader(io.open(pool, encoding="utf-8-sig")):
                    if _r and len(_r) >= 2 and _r[0].strip() == lh and _r[1].strip():
                        iid = _r[1].strip()
                        break
        if not iid:
            return {"ok": False, "error": "无该料号 ID——先填淘宝ID（或料号输完整）"}
        rt = os.path.join(TG, "runtime", "python.exe")
        camps = ""    # 留空 → 由 tuiguang_api 自己取「未满」的计划（写死列表会带上已满的）
        out = os.path.join(TG, "runtime", "tg_panel.log")
        err = out.replace(".log", "_err.log")
        _cleanup_probe()  # 单品起批前清理
        try:
            proc = _sp.Popen([rt, "-X", "utf8", "-u", os.path.join(TG, "tuiguang_api.py"),
                              "--liaohao", lh, "--taobao_id", iid, "--campaigns", camps, "--no-pop"],
                             cwd=TG, stdout=io.open(out, "a", encoding="utf-8"),
                             stderr=io.open(err, "a", encoding="utf-8"))
            with io.open(TG_PID, "w", encoding="utf-8") as _pf:
                _pf.write(str(proc.pid))
            return {"ok": True, "msg": "单品推广启动：%s (%s)" % (lh, iid), "lh": lh, "id": iid}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "batch-save":
        """批量：接收 csv 文本内容 → 存 runtime/tg_upload_{ts}.csv → 返回路径（面板再 start-batch?file=）"""
        body = qs.get("content", "")
        if not body:
            return {"ok": False, "error": "空内容"}
        import time as _tt
        fn = os.path.join(TG, "runtime", "tg_upload_%s.csv" % _tt.strftime("%H%M%S"))
        io.open(fn, "w", encoding="utf-8").write(body)
        return {"ok": True, "file": fn, "msg": "已存 %s" % os.path.basename(fn)}
    return {"ok": False, "error": "未知 action: %s" % action}
