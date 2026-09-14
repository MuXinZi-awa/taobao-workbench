# -*- coding: utf-8 -*-
"""调度守护 · daemon.py（零依赖 · 独立进程 · 不依赖工作台）
职责：每分钟对齐 tick → 读任务 → 判断该不该跑 → 执行动作 → 写日志/心跳/进度
单例：锁文件 + pid 校验（防重复启动）
用法：python daemon.py            （常驻）
      python daemon.py --once     （跑一轮就退出，调试用）
"""
import os, io, sys, json, time, sqlite3, subprocess, datetime, calendar

BASE = os.path.dirname(os.path.abspath(__file__))
WB = os.path.dirname(os.path.dirname(BASE))
DATA = os.path.join(WB, "data")
DB = os.path.join(DATA, "schedule.db")
HB = os.path.join(DATA, "scheduler_heartbeat.json")
LOG = os.path.join(DATA, "scheduler.log")
TG = r"C:\Users\jdt-pty\Desktop\推广一键跑"
PY = os.path.join(TG, "runtime", "python.exe")

sys.path.insert(0, BASE)
import server as S          # 复用 ACTIONS 注册表与库连接


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(DATA, exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass


# ── 单例 ──
def _alive():
    try:
        with io.open(HB, encoding="utf-8") as f:
            hb = json.load(f)
        return (time.time() - float(hb.get("ts") or 0)) < 180
    except Exception:
        return False


def _write_hb(extra=None):
    d = {"ts": time.time(), "pid": os.getpid(),
         "started": _STARTED, "last_tick": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    if extra:
        d.update(extra)
    try:
        os.makedirs(DATA, exist_ok=True)
        with io.open(HB, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


# ── cron/trigger 解析 ──
def _f(pat, v):
    if pat == "*":
        return True
    for p in pat.split(","):
        p = p.strip()
        if "-" in p:
            try:
                a, b = p.split("-")
                if int(a) <= v <= int(b):
                    return True
            except Exception:
                continue
        elif p.isdigit() and int(p) == v:
            return True
    return False


def cron_match(expr, now):
    parts = (expr or "").split()
    if len(parts) != 5:
        return False
    mi, ho, dom, mon, dow = parts
    return (_f(mi, now.minute) and _f(ho, now.hour) and _f(dom, now.day)
            and _f(mon, now.month) and _f(dow, now.isoweekday()))


def _last_min_run(last_run):
    """上一分钟是否已经跑过（防同一分钟重复触发）"""
    if not last_run:
        return False
    try:
        t = datetime.datetime.strptime(last_run, "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            t = datetime.datetime.strptime(last_run, "%Y-%m-%d %H:%M")
        except Exception:
            return False
    return t.strftime("%Y-%m-%d %H:%M") == datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def should_run(trig, now, last_run):
    t = (trig or {}).get("type")
    if t == "interval":
        try:
            mins = max(1, int((trig.get("minutes") or "30")))
        except Exception:
            mins = 30
        if not last_run:
            return True
        try:
            lt = datetime.datetime.strptime(last_run, "%Y-%m-%d %H:%M:%S")
            return (now - lt).total_seconds() >= mins * 60
        except Exception:
            return True
    if t == "once":
        at = (trig.get("at") or "").replace("T", " ")
        if not at:
            return False
        if _last_min_run(last_run) or last_run:
            return False                       # 跑过就不再跑
        try:
            return now >= datetime.datetime.strptime(at[:16], "%Y-%m-%d %H:%M")
        except Exception:
            return False
    if t == "daily":
        hm = now.strftime("%H:%M")
        return hm in S.norm_times(trig.get("times")) and not _last_min_run(last_run)
    if t == "weekly":
        hm = now.strftime("%H:%M")
        days = [int(x) for x in (trig.get("days") or [])]
        return now.isoweekday() in days and hm in S.norm_times(trig.get("times")) and not _last_min_run(last_run)
    if t == "monthly":
        hm = now.strftime("%H:%M")
        if hm not in S.norm_times(trig.get("times")) or _last_min_run(last_run):
            return False
        if trig.get("last_day"):
            lastd = calendar.monthrange(now.year, now.month)[1]
            return now.day == lastd
        return now.day in [int(x) for x in (trig.get("days") or [])]
    if t == "cron":
        return cron_match(trig.get("expr") or "", now) and not _last_min_run(last_run)
    return False


# ── 执行 ──
def _build_cmd(act_key, params):
    a = S.ACTIONS.get(act_key)
    if not a:
        return None, "未知动作: %s" % act_key
    cmd = [PY, "-X", "utf8", "-u", a["script"]]
    for x in a.get("args", []):
        v = x
        for k, pv in (params or {}).items():
            v = v.replace("{%s}" % k, str(pv))
        if "{" in v:                            # 还有没替换的占位符
            return None, "参数缺失: %s" % v
        cmd.append(v)
    return cmd, None


def run_once(tid, manual=False):
    """执行一个任务（daemon 与面板'立即执行'共用）"""
    c = S._conn()
    r = c.execute("SELECT id,name,enabled,trig,act,params,progress FROM task WHERE id=?", (tid,)).fetchone()
    c.close()
    if not r:
        return {"ok": False, "error": "任务不存在"}
    _, name, en, trig, act, params, prog = r
    params = json.loads(params or "{}")
    cmd, err = _build_cmd(act, params)
    if err:
        _mark(tid, "fail", err)
        log("[%s] 跳过：%s" % (name, err))
        return {"ok": False, "error": err}
    try:
        out = os.path.join(DATA, "scheduler_run_%d.log" % tid)
        f = io.open(out, "w", encoding="utf-8")
        p = subprocess.Popen(cmd, cwd=TG, stdout=f, stderr=subprocess.STDOUT,
                             creationflags=0x08000000)
        _mark(tid, "running", "pid=%d %s" % (p.pid, "手动" if manual else "定时"))
        log("[%s] 已启动 pid=%d  cmd=%s" % (name, p.pid, " ".join(os.path.basename(x) for x in cmd)))
        return {"ok": True, "pid": p.pid, "cmd": " ".join(cmd)}
    except Exception as e:
        _mark(tid, "fail", str(e)[:120])
        log("[%s] 启动失败: %s" % (name, str(e)[:100]))
        return {"ok": False, "error": str(e)[:120]}


def _mark(tid, result, note):
    c = S._conn()
    c.execute("UPDATE task SET last_run=?,last_result=?,last_note=? WHERE id=?",
              (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), result, note, tid))
    c.commit(); c.close()


def tick(now=None):
    now = now or datetime.datetime.now()
    c = S._conn()
    rows = c.execute("SELECT id,name,enabled,trig,last_run FROM task WHERE enabled=1").fetchall()
    c.close()
    fired = []
    for tid, name, en, trig_s, last_run in rows:
        try:
            trig = json.loads(trig_s or "{}")
        except Exception:
            continue
        if should_run(trig, now, last_run):
            log("--- 触发 [%s] ---" % name)
            run_once(tid)
            fired.append(tid)
            if trig.get("type") == "once":      # 一次性跑完自动停用
                c = S._conn()
                c.execute("UPDATE task SET enabled=0 WHERE id=?", (tid,))
                c.commit(); c.close()
    return fired


_STARTED = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    once = "--once" in sys.argv
    if not once and _alive():
        log("已有守护在运行（心跳存在），本进程退出")
        return
    log("=== 调度守护启动 pid=%d ===" % os.getpid())
    _write_hb()
    while True:
        try:
            fired = tick()
            _write_hb({"last_fired": fired})
        except Exception as e:
            log("tick 异常: %s" % str(e)[:120])
        if once:
            break
        # 对齐到下一分钟
        nxt = (datetime.datetime.now() + datetime.timedelta(minutes=1)).replace(second=0, microsecond=0)
        time.sleep(max(1, (nxt - datetime.datetime.now()).total_seconds()))


if __name__ == "__main__":
    main()
