# -*- coding: utf-8 -*-
"""任务计划（scheduler）· server
- 任务 CRUD（SQLite 落盘：内核热插拔，模块状态不保留 → 一切都写库/文件）
- 调度由独立守护 daemon.py 执行（不依赖工作台常驻）
- 动作注册表：把"报表·全量采集"这类标签映射到真实命令
只读安全：不碰生产库，只读写自己的 data/schedule.db + 启动/停止守护进程
"""
import os, io, json, time, sqlite3, subprocess, datetime, sys

BASE = os.path.dirname(os.path.abspath(__file__))
WB = os.path.dirname(os.path.dirname(BASE))          # workbench 目录
DATA = os.path.join(WB, "data")
DB = os.path.join(DATA, "schedule.db")
TG = r"C:\Users\jdt-pty\Desktop\推广一键跑"
PY = os.path.join(TG, "runtime", "python.exe")
DAEMON = os.path.join(BASE, "daemon.py")
LOCK = os.path.join(DATA, "scheduler.lock")
HB = os.path.join(DATA, "scheduler_heartbeat.json")

# ── 动作注册表：label(显示) / cmd(命令) / args(参数模板，{key} 用 params 填充) / kind
ACTIONS = {
    "report_full": {
        "label": "报表 · 全量采集",
        "kind": "script",
        "script": os.path.join(TG, "_report_fetch.py"),
        "args": [],
        "params": [{"key": "account", "label": "关联账号", "type": "account", "default": ""}],
    },
    "report_today": {
        "label": "报表 · 补今日",
        "kind": "script",
        "script": os.path.join(TG, "_report_today.py"),
        "args": ["--headless"],
        "params": [{"key": "account", "label": "关联账号", "type": "account", "default": ""}],
    },
    "promo_batch": {
        "label": "流水线 · 推广一批",
        "kind": "script",
        "script": os.path.join(TG, "tuiguang_auto.py"),
        "args": ["--excel", "{file}", "--limit", "{limit}", "--no-pop"],
        "params": [
            {"key": "file", "label": "数据文件", "type": "file", "default": "runtime\\推广池_0905.csv"},
            {"key": "limit", "label": "每批数量", "type": "number", "default": "50"},
            {"key": "account", "label": "关联账号", "type": "account", "default": ""},
        ],
    },
    "dual_batch": {
        "label": "查双百 · 批量检测",
        "kind": "script",
        "script": os.path.join(TG, "_dual_scan_file.py"),
        "args": ["{file}"],
        "params": [
            {"key": "file", "label": "数据文件", "type": "file", "default": ""},
            {"key": "account", "label": "关联账号", "type": "account", "default": ""},
        ],
    },
}



def norm_hm(s):
    """时间/列表输入标准化：全角→半角、去空格；支持 '9:00，21:00' 这类中文标点"""
    s = str(s or "")
    for a, b in (("：", ":"), ("，", ","), ("　", " "), ("、", ","), ("；", ";")):
        s = s.replace(a, b)
    return s.strip()


def norm_times(x):
    """把 times 规整成 ['HH:MM', ...]（容忍 '9:00' / '09:00' / 中文标点混入）"""
    if isinstance(x, str):
        parts = norm_hm(x).split(",")
    elif isinstance(x, (list, tuple)):
        parts = []
        for it in x:
            parts += norm_hm(it).split(",")
    else:
        parts = []
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if ":" in p:
            h, _, m = p.partition(":")
            try:
                out.append("%02d:%02d" % (int(h.strip()), int(m.strip()[:2] or 0)))
                continue
            except Exception:
                pass
        out.append(p)          # 解析不了就原样保留（便于排查）
    return out


def _conn():
    os.makedirs(DATA, exist_ok=True)
    c = sqlite3.connect(DB, timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS task(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, enabled INTEGER DEFAULT 1,
        trig TEXT, act TEXT, params TEXT DEFAULT '{}',
        created TEXT, last_run TEXT, last_result TEXT, last_note TEXT,
        progress TEXT DEFAULT '{}')""")
    return c


def _row2dict(r):
    return {
        "id": r[0], "name": r[1], "enabled": bool(r[2]),
        "trigger": json.loads(r[3] or "{}"),
        "action": r[4], "params": json.loads(r[5] or "{}"),
        "created": r[6], "last_run": r[7], "last_result": r[8], "last_note": r[9],
        "progress": json.loads(r[10] or "{}"),
    }


def list_tasks():
    c = _conn()
    rows = c.execute("SELECT id,name,enabled,trig,act,params,created,last_run,last_result,last_note,progress"
                     " FROM task ORDER BY id").fetchall()
    c.close()
    return [_row2dict(r) for r in rows]


def save_task(d):
    c = _conn()
    tid = d.get("id")
    _trig = dict(d.get("trigger") or {})
    if _trig.get("times") is not None:
        _trig["times"] = norm_times(_trig["times"])          # 存前规整（全角冒号/逗号/空格）
    if _trig.get("days") is not None:
        _trig["days"] = [int(str(x).strip()) for x in str(_trig["days"]).replace("，", ",").split(",") if str(x).strip().isdigit()]
    args = (d.get("name") or "未命名", 1 if d.get("enabled", True) else 0,
            json.dumps(_trig, ensure_ascii=False),
            d.get("action") or "", json.dumps(d.get("params") or {}, ensure_ascii=False))
    if tid:
        c.execute("UPDATE task SET name=?,enabled=?,trig=?,act=?,params=? WHERE id=?", args + (tid,))
    else:
        c.execute("INSERT INTO task(name,enabled,trig,act,params,created) VALUES(?,?,?,?,?,?)",
                  args + (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),))
        tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit(); c.close()
    return tid


def delete_task(tid):
    c = _conn()
    c.execute("DELETE FROM task WHERE id=?", (tid,))
    c.commit(); c.close()
    return True


def toggle_task(tid, on):
    c = _conn()
    c.execute("UPDATE task SET enabled=? WHERE id=?", (1 if on else 0, tid))
    c.commit(); c.close()
    return True


# ── 守护进程管理 ──
def _daemon_alive():
    try:
        with io.open(HB, encoding="utf-8") as f:
            hb = json.load(f)
        return (time.time() - float(hb.get("ts") or 0)) < 180      # 3 分钟内有心跳 = 活着
    except Exception:
        return False


def daemon_status():
    hb = {}
    try:
        with io.open(HB, encoding="utf-8") as f:
            hb = json.load(f)
    except Exception:
        pass
    return {"alive": _daemon_alive(), "pid": hb.get("pid"), "last_tick": hb.get("last_tick"),
            "started": hb.get("started")}


def daemon_start():
    if _daemon_alive():
        return {"ok": True, "msg": "守护已在运行", **daemon_status()}
    if not os.path.isfile(DAEMON):
        return {"ok": False, "error": "缺少 daemon.py"}
    try:
        subprocess.Popen([PY, "-X", "utf8", "-u", DAEMON],
                         cwd=BASE, creationflags=0x08000000)   # CREATE_NO_WINDOW（无窗口常驻）
        time.sleep(1.5)
        return {"ok": True, "msg": "守护已启动", **daemon_status()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def daemon_stop():
    try:
        with io.open(HB, encoding="utf-8") as f:
            pid = int(json.load(f).get("pid") or 0)
        if pid:
            subprocess.run(["taskkill", "/f", "/pid", str(pid)], capture_output=True)
            time.sleep(0.5)
            return {"ok": True, "msg": "守护已停止"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
    return {"ok": True, "msg": "守护未在运行"}


UPLOAD_DIR = os.path.join(DATA, "uploads")


def upload(params):
    """接收拖入的文件（POST 原文）→ 存到 data/uploads/ → 返回完整路径"""
    name = os.path.basename(str(params.get("name") or "").strip()) or "upload.csv"
    raw = params.get("_raw")
    if not raw:
        return {"ok": False, "error": "没收到文件内容"}
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        fp = os.path.join(UPLOAD_DIR, name)
        base, ext = os.path.splitext(name)
        i = 1
        while os.path.isfile(fp):
            fp = os.path.join(UPLOAD_DIR, "%s_%d%s" % (base, i, ext))
            i += 1
        with io.open(fp, "wb") as f:
            f.write(raw)
        return {"ok": True, "path": fp, "name": os.path.basename(fp), "size": len(raw)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:140]}


def list_files():
    """可选的批处理数据文件（推广一键跑 下常见位置）——给前端下拉/拖拽用"""
    out = []
    for sub in ("runtime", "", "办公室工作"):
        d = os.path.join(TG, sub) if sub else TG
        if not os.path.isdir(d):
            continue
        try:
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith((".csv", ".xlsx", ".xls")) and not fn.startswith("~$"):
                    fp = os.path.join(d, fn)
                    try:
                        st = os.stat(fp)
                    except Exception:
                        continue
                    out.append({"name": fn, "path": fp, "size": st.st_size, "mtime": st.st_mtime})
        except Exception:
            pass
    out.sort(key=lambda x: -x["mtime"])
    return out[:80]


def resolve_name(name):
    """拖拽只给文件名 → 在常见目录里找完整路径（找不到返回 None）"""
    name = (name or "").strip()
    if not name:
        return None
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    for sub in ("runtime", "", "办公室工作", os.path.join("办公室工作", "数据")):
        d = os.path.join(TG, sub) if sub else TG
        fp = os.path.join(d, name)
        if os.path.isfile(fp):
            return fp
    return None


def actions_meta():
    """给前端：动作列表 + 每个动作的参数定义"""
    return [{"key": k, "label": v["label"], "params": v.get("params", [])} for k, v in ACTIONS.items()]


def logs(limit=40):
    fp = os.path.join(DATA, "scheduler.log")
    if not os.path.isfile(fp):
        return []
    try:
        with io.open(fp, encoding="utf-8") as f:
            return f.read().splitlines()[-limit:][::-1]
    except Exception:
        return []


def handle(action, qs):
    a = (action or "").strip()
    if a == "upload":
        return upload(qs)
    if a == "files":
        return {"ok": True, "files": list_files()}
    if a == "resolve":
        p = resolve_name(qs.get("name"))
        return {"ok": bool(p), "path": p or "", "error": "" if p else "没找到该文件"}
    if a == "list":
        return {"ok": True, "tasks": list_tasks(), "actions": actions_meta(),
                "daemon": daemon_status(), "logs": logs(30), "files": list_files()}
    if a == "save":
        d = qs.get("data")
        if isinstance(d, str):
            d = json.loads(d)
        tid = save_task(d or {})
        return {"ok": True, "id": tid}
    if a == "delete":
        return {"ok": delete_task(int(qs.get("id") or 0))}
    if a == "toggle":
        return {"ok": toggle_task(int(qs.get("id") or 0), str(qs.get("on")) in ("1", "true", "True"))}
    if a == "daemon":
        op = qs.get("op") or "status"
        if op == "start":
            return daemon_start()
        if op == "stop":
            return daemon_stop()
        return {"ok": True, **daemon_status()}
    if a == "run_now":
        tid = int(qs.get("id") or 0)
        return run_task_now(tid)
    return {"ok": False, "error": "unknown action: " + a}


def run_task_now(tid):
    """立即执行一次（不等到点）——交给 daemon 的同一套执行逻辑"""
    try:
        sys.path.insert(0, BASE)
        import daemon as dm
        r = dm.run_once(tid)
        return r
    except Exception as e:
        return {"ok": False, "error": str(e)[:140]}
