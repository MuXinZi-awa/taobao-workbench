# -*- coding: utf-8 -*-
"""推广监控台 server：聚合状态（读文件/进程——不碰浏览器常驻）
数据源：
  1. 推广记录.csv（新——tuiguang_auto 自动化批，料号为行）
  2. 上品全量汇总.csv「推广状态」列（老——上品+推广一起 done，无记录 csv 时代）
  3. 计划容量：runtime/plan_state.json（tuiguang_auto 每批跑完写——尚未加，先读存在则显示）
  4. 进程/登录态：查 tuiguang_auto 进程 + _dual_one 探测
"""
import os, io, csv, json, subprocess, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
TG = r"C:\Users\jdt-pty\Desktop\推广一键跑"
TG_REC = os.path.join(r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\数据", "推广记录.csv")
ALL_CSV = os.path.join(r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\数据", "上品全量汇总.csv")
PLAN_STATE = os.path.join(TG, "runtime", "plan_state.json")
# 11 计划组真名（0908 从 campaign/horizontal/findPage.json 抓——存 runtime/plan_names.json）
PLAN_NAMES_FP = os.path.join(TG, "runtime", "plan_names.json")
_PN = {}
try:
    _PN = json.load(io.open(PLAN_NAMES_FP, encoding="utf-8"))
except Exception:
    pass
_PLAN_ORDER = ["72264315693", "83306736168", "72304256352", "83211993350", "83259474708",
               "83212131417", "83212279185", "83259606767", "83212439397", "83259874852", "83162843849"]
PLANS = [{"id": pid, "name": _PN.get(pid, pid)} for pid in _PLAN_ORDER]

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
    """0908 v2：PID 文件检测（绕开 PowerShell 进程查询转义坑）——start-batch 写 PID，查存活"""
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


def _login():
    """登录态探测：probe_status.py 一次 headless 启动——登录判定（万相台域）+ 顺手拉 11 计划容量
    （梓帆 0908：拉登录态时连带容量一起拉——一次浏览器启动干两件事）+ 90s 缓存"""
    import time as _t
    if _LG["v"] is not None and _t.time() - _LG["t"] < 90:
        return _LG["v"]
    try:
        # 0908：万相台 headless probe 拿不到 csrf（新 profile 无头不授）——改 _dual_one（淘宝 mtop API headless 可用）
        r = subprocess.run([os.path.join(TG, "runtime", "python.exe"), "-X", "utf8", "-u",
                            os.path.join(TG, "_dual_one.py"), "1379668", "", "--headless"],
                           capture_output=True, timeout=90)
        out = (r.stdout or b"").decode("utf-8", "replace")
        ok = any(k in out for k in ("已双百", "未双百", "流量加速中", "未搜到"))
        _LG["v"] = ok
        _LG["t"] = _t.time()
        return ok
    except Exception:
        return False

def status():
    # 1. 推广记录.csv
    rec_rows = _read_rows(TG_REC)
    rec_lh = set()
    rec_today = 0
    today = datetime.date.today().strftime("%m-%d")
    for r in rec_rows[1:]:
        if len(r) >= 2 and r[0].strip():
            rec_lh.add(r[0].strip())
            if len(r) >= 3 and r[2].strip().startswith(today):
                rec_today += 1
    # 2. 计划状态（rec 即全集——0908 已并入全量汇总，不再两源合并）

    plans = []
    plan_state = {}
    if os.path.isfile(PLAN_STATE):
        try:
            plan_state = json.load(open(PLAN_STATE, encoding="utf-8"))
        except Exception:
            pass
    for p in PLANS:
        pid = p["id"]
        st = plan_state.get(pid, {})
        count = st.get("count")
        ts = st.get("time", "")
        plans.append({"id": pid, "name": p["name"], "count": count, "time": ts,
                      "full_known": pid in ("72264315693", "83306736168")})
    running = _running()
    # 0908：login 只读缓存——自动探测已禁（probe headless 抢 chrome_profile → 批/手动 launch 全被顶掉）
    # 登录态用手动「检测」按钮；批跑时状态看 running；容量批自更新+手动刷新
    login = _LG["v"]
    return {
        "ok": True,
        "login": login,
        "running": running,
        "stats": {"rec": len(rec_lh), "today": rec_today, "units": _units_total(plan_state),
                 "pool": _pool_stats(rec_lh)},
        "plans": plans,
        "time": datetime.datetime.now().strftime("%H:%M:%S"),
    }

def handle(action, qs):
    if action == "status":
        return status()
    if action == "login-check":
        """手动检测登录态（probe headless 起一次——慎用：会短暂占 profile，别在批跑时点）"""
        import time as _t
        if _running():
            return {"ok": True, "login": None, "msg": "批运行中——跳过探测"}
        _LG["v"] = _login()
        return {"ok": True, "login": _LG["v"]}
    if action == "refresh-caps":
        # 刷新容量：subprocess detach 跑 refresh_caps.py（headless 静默——约 25-30s）
        import subprocess as _sp
        rt = os.path.join(TG, "runtime", "python.exe")
        try:
            _sp.Popen([rt, "-X", "utf8", "-u", os.path.join(TG, "runtime", "refresh_caps.py")],
                      cwd=TG, creationflags=0x08000000)  # 0x08000000 = 不弹窗
            return {"ok": True, "msg": "容量刷新已启动（headless 静默，约 30s 完成）"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "start-batch":
        """面板手动起批：默认池或指定 csv（qs.file=绝对路径）；返回本次待推清单头几个"""
        import subprocess as _sp
        _cleanup_probe()  # 0908：起批前清残留 probe（防同 profile 冲突杀批）
        try:
            limit = int(qs.get("limit", "30") or 30)
        except Exception:
            limit = 30
        limit = max(1, min(limit, 200))
        rt = os.path.join(TG, "runtime", "python.exe")
        pool = qs.get("file", "") or os.path.join(TG, "runtime", "推广池_0905.csv")
        camps = ",".join(_PLAN_ORDER[1:])
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
            proc = _sp.Popen([rt, "-X", "utf8", "-u", os.path.join(TG, "tuiguang_auto.py"),
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
        camps = ",".join(_PLAN_ORDER[1:])
        out = os.path.join(TG, "runtime", "tg_panel.log")
        err = out.replace(".log", "_err.log")
        _cleanup_probe()  # 0908：单品起批前清理
        try:
            proc = _sp.Popen([rt, "-X", "utf8", "-u", os.path.join(TG, "tuiguang_auto.py"),
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
