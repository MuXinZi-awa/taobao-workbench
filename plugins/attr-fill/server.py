# -*- coding: utf-8 -*-
"""补必填面板 server：解析输入 → 落临时表 → detach 子进程 → 面板轮询进度/结果/留痕

外部系统行为：workbench 是ThreadingTCPServer，长批（每品要开编辑页、真输入、提交）不适合挂在
             HTTP 请求上等——一律 Popen 分离，浏览器锁由子进程（reqfill_api.py）自己拿。
约束：本文件只写「入参临时表 + 结果 JSON」，不碰任何商品；补/提交的动作全在 reqfill_api.py。
"""
import csv
import io
import json
import os
import re
import subprocess
import sys
import time

_d = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_d, "paths.py")) and os.path.dirname(_d) != _d:
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)
import paths

BASE = os.path.dirname(os.path.abspath(__file__))
TG = paths.TG_ROOT
TMP = os.path.join(TG, "runtime", "_pl_tmp")
PROG = os.path.join(TG, "runtime", "progress.json")
LEDGER = os.path.join(TG, "runtime", "reqfill.csv")
LAST = os.path.join(TG, "runtime", "reqfill_last.json")
RUNLOG = os.path.join(TG, "runtime", "reqfill_run.log")
REQFILL_PY = os.path.join(TG, "reqfill_api.py")
STATE = paths.STATE

# 插件脚本用的解释器（设置-通用可改）。外部系统行为：**打包成 exe 后 sys.executable = 壳自己**，
# 壳里没有 playwright/openpyxl——拿它去 spawn 会“面板能开、点下去没反应”。所以解释器一律从 paths 取。
PY = paths.PYTHON
RT = os.path.join(TG, "runtime", "python.exe")
if not os.path.isfile(RT):
    RT = PY


def _known_lhs():
    """流水线里出现过的料号集合——消「纯数字到底是料号还是淘宝ID」的歧义"""
    try:
        with io.open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        return set((st.get("items") or {}).keys())
    except Exception:
        return set()


def parse_rows(text):
    """一行一条：`料号,淘宝ID` / 纯淘宝ID / 纯料号（ID 由子进程/续跑逻辑消化）
    外部系统行为：**纯数字的料号与淘宝ID肉眼难分**（如 1928498059 是 10 位料号，其淘宝ID 是 12 位）——
    先查流水线 state 认过的料号，再按位数兜底：>=12 位当淘宝ID，否则当料号。"""
    known = _known_lhs()
    rows, seen = [], set()
    for line in (text or "").replace("\t", ",").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in re.split(r"[,，;；\s]+", line) if p]
        if not parts:
            continue
        if len(parts) >= 2 and re.match(r"^\d{9,14}$", parts[1]):
            lh, tid = parts[0], parts[1]
        elif parts[0] in known:
            lh, tid = parts[0], ""
        elif re.match(r"^\d{12,14}$", parts[0]):
            lh, tid = "", parts[0]
        else:
            lh, tid = parts[0], ""
        if not tid and not lh:
            continue
        k = tid or lh
        if k in seen:
            continue
        seen.add(k)
        rows.append((lh, tid))
    return rows


def _tail_ledger(n=40):
    try:
        with io.open(LEDGER, encoding="utf-8-sig", errors="replace") as f:
            rows = [r for r in csv.reader(f) if r]
        return rows[-n:]
    except Exception:
        return []


def _tail_runlog(n=10):
    try:
        with io.open(RUNLOG, encoding="utf-8", errors="replace") as f:
            lines = [x for x in f.read().splitlines() if x.strip()]
        return lines[-n:]
    except Exception:
        return []


def handle(action, qs):
    if action == "start":
        rows = parse_rows(qs.get("rows") or "")
        if not rows:
            return {"ok": False, "error": "没解析到任何料号/淘宝ID——一行一个，或直接拖 csv 进来"}
        gap = str(qs.get("gap") or "2.5").strip() or "2.5"
        limit = str(qs.get("limit") or "0").strip() or "0"
        submit = str(qs.get("submit") or "").strip() in ("1", "true", "yes")
        resume = str(qs.get("resume") or "").strip() not in ("0", "false", "no")
        os.makedirs(TMP, exist_ok=True)
        fp = os.path.join(TMP, "reqfill_%s.csv" % time.strftime("%Y%m%d_%H%M%S"))
        with io.open(fp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["料号", "淘宝ID"])
            w.writerows(rows)
        args = [RT, "-X", "utf8", "-u", REQFILL_PY, "--batch", fp, "--gap", gap, "--json-out", LAST]
        if limit not in ("0", ""):
            args += ["--limit", limit]
        if submit:
            args.append("--submit")
        if not resume:
            args.append("--no-resume")
        try:
            os.remove(LAST)
        except Exception:
            pass
        try:
            if not os.path.isfile(RT):
                # 指错了要说人话：不这样的话现象是「点了跑批没反应」，最难查
                return {"ok": False, "error": "找不到插件要用的 Python：%s\n去 设置 → 通用 →「插件用的 Python」改成真实路径（本机可用：%s）"
                                                % (RT, paths.TG_PYTHON)}
            with io.open(RUNLOG, "a", encoding="utf-8") as lg:
                lg.write("\n=== %s 补必填 %d 品 gap=%s limit=%s submit=%s resume=%s ===\n"
                         % (time.strftime("%Y-%m-%d %H:%M:%S"), len(rows), gap, limit, submit, resume))
                subprocess.Popen(args, cwd=TG, stdout=lg, stderr=subprocess.STDOUT)
        except Exception as e:
            return {"ok": False, "error": "起子进程失败：%s" % str(e)[:140]}
        return {"ok": True, "n": len(rows), "submit": submit,
                "msg": "已启动 %d 品（%s%s，间隔 %ss）" % (
                    len(rows), "补完即提交" if submit else "只到错误(0)", "" if resume else "·忽略留痕", gap)}
    if action == "progress":
        try:
            with io.open(PROG, encoding="utf-8") as f:
                d = json.load(f)
            return {"ok": True, "progress": d if d.get("kind") == "补必填" else None}
        except Exception:
            return {"ok": True, "progress": None}
    if action == "result":
        try:
            with io.open(LAST, encoding="utf-8") as f:
                return {"ok": True, "rows": json.load(f)}
        except Exception:
            return {"ok": True, "rows": []}
    if action == "ledger":
        return {"ok": True, "rows": _tail_ledger(40), "runlog": _tail_runlog(10)}
    if action == "runlog":
        return {"ok": True, "lines": _tail_runlog(14)}
    return {"ok": False, "error": "未知 action: %s" % action}
