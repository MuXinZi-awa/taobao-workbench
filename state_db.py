# -*- coding: utf-8 -*-
"""工作台状态库（sqlite）——数据/状态进库，配置留文件。

为什么：同一个东西两份 json（plan_state.json 与 plan_state_46557383.json）就出过
"刷新脚本写 A、面板读 B"，谁也说不清哪个是真的。库按 member_id 查，天生只有一个真相源。
配置类（paths.local.json / 端口 / 界面偏好）留在文件里：那些要能被人打开来看、来改、拷走。

库地址由 paths.DB 决定（工作区级推导，本地配置可改）——源码方式与打包后的 exe 是同一个文件。
"""
import os
import sqlite3
import time

import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_capacity (
  member_id TEXT NOT NULL,
  plan_id   TEXT NOT NULL,
  name      TEXT,
  count     INTEGER,
  capacity  INTEGER,
  updated_at TEXT,
  PRIMARY KEY (member_id, plan_id)
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

_ready = False


def conn():
    """开库（顺手建表）。WAL：面板和脚本同时读写时，读不挡写——json 没这个保证。"""
    global _ready
    fp = str(paths.DB)
    d = os.path.dirname(fp)
    if d:
        os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(fp, timeout=20)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=15000")
    if not _ready:
        c.executescript(SCHEMA)
        c.commit()
        _ready = True
    return c


def meta_get(k, default=""):
    try:
        r = conn().execute("SELECT v FROM meta WHERE k=?", (str(k),)).fetchone()
        return r[0] if r else default
    except Exception:
        return default


def meta_set(k, v):
    c = conn()
    c.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
              (str(k), str(v)))
    c.commit()


def get_plans(member_id):
    """按账号取计划容量。查不到就是空——**绝不回退别的账号**（回退就是串号/读旧数）。"""
    try:
        rows = conn().execute(
            "SELECT plan_id,name,count,capacity,updated_at FROM plan_capacity WHERE member_id=?",
            (str(member_id or ""),)).fetchall()
    except Exception:
        return {}
    return {r[0]: {"name": r[1] or "", "count": r[2], "capacity": r[3], "time": r[4] or ""}
            for r in rows}


def set_plans(member_id, plans, capacity=500):
    """写某账号的计划容量。只覆盖本次给到的计划行，别的账号的行不动。"""
    mid = str(member_id or "")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    c = conn()
    for pid, v in (plans or {}).items():
        v = v or {}
        c.execute(
            "INSERT INTO plan_capacity(member_id,plan_id,name,count,capacity,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(member_id,plan_id) DO UPDATE SET "
            "name=excluded.name, count=excluded.count, capacity=excluded.capacity, "
            "updated_at=excluded.updated_at",
            (mid, str(pid), str(v.get("name") or ""), v.get("count"),
             int(v.get("capacity") or capacity), str(v.get("time") or now)))
    c.commit()


def total(member_id):
    return sum((v.get("count") or 0) for v in get_plans(member_id).values())


def migrate_from_json(tg_root=None):
    """把老的 plan_state*.json 一次性搬进库。**旧文件不删**（可回退），搬过就只读库。

    文件名带着账号：plan_state_<member>.json → 归那个账号；无后缀的是旧"全局"文件，
    归到空账号（member_id=''）名下——它不对应任何真实账号，面板绑了账号后不会读它。
    幂等：库里那个账号已经有行了就跳过，不会覆盖新数据。
    """
    import glob
    import json
    import re as _re
    tg = tg_root or str(paths.TG_ROOT)
    rt = os.path.join(tg, "runtime")
    moved = []
    for fp in glob.glob(os.path.join(rt, "plan_state*.json")):
        base = os.path.basename(fp)
        m = _re.match(r"plan_state_([^.]+)\.json$", base)
        mid = m.group(1) if m else ""
        try:
            d = json.load(open(fp, encoding="utf-8"))
            if not isinstance(d, dict):
                continue
            if get_plans(mid):
                moved.append("%s：库里已有该账号，跳过" % base)
                continue
            plans = {k: {"count": (v or {}).get("count"), "capacity": 500,
                         "time": (v or {}).get("time")} for k, v in d.items()}
            set_plans(mid, plans)
            moved.append("%s → 账号 '%s'，%d 个计划" % (base, mid or "(空)", len(plans)))
        except Exception as e:
            moved.append("%s：读失败 %s" % (base, str(e)[:60]))
    return moved
