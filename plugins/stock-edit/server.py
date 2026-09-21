# -*- coding: utf-8 -*-
"""改库存面板 server：解析输入 → 落临时表 → detach 子进程 → 面板轮询进度/留痕

外部系统行为：workbench 用 ThreadingTCPServer，但长批不适合挂在 HTTP 请求上等——
             一律 Popen 分离，跑批的浏览器锁由子进程（stock_api.py）自己拿，本文件不碰浏览器。
约束：只写「入参临时表 + 日志」，不碰任何商品；改商品的动作全在 stock_api.py（且只到照镜子）。
"""
import csv
import io
import json
import os
import re
import subprocess
import sys
import time

import sys as _sys
_d = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_d, "paths.py")) and os.path.dirname(_d) != _d:
    _d = os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import paths

BASE = os.path.dirname(os.path.abspath(__file__))
TG = paths.TG_ROOT
TMP = os.path.join(TG, "runtime", "_pl_tmp")
PROG = os.path.join(TG, "runtime", "progress.json")
LEDGER = os.path.join(TG, "runtime", "stock_edit.csv")
LAST = os.path.join(TG, "runtime", "stock_last.json")
RUNLOG = os.path.join(TG, "runtime", "stock_edit_run.log")
STOCK_PY = os.path.join(TG, "stock_api.py")
STATE = paths.STATE
DEFAULT_QTY = "open"        # 默认敞开卖（50000）；手填时才传数字

PY = paths.PYTHON          # 插件脚本用的解释器（设置-通用可改；打包后壳自己不能当解释器）
RT = os.path.join(TG, "runtime", "python.exe")
if not os.path.isfile(RT):
    RT = PY


def _known_lhs():
    """流水线里出现过的料号集合——用来消「纯数字到底是料号还是淘宝ID」的歧义"""
    try:
        with io.open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        return set((st.get("items") or {}).keys())
    except Exception:
        return set()


def parse_rows(text):
    """一行一条，三种写法都收：`料号,淘宝ID` / 纯 `淘宝ID` / 纯 `料号`（ID 交给子进程解析）
    外部系统行为：**纯数字的料号和淘宝ID肉眼难分**——例：`1928498059` 是 10 位料号，
    而该商品的淘宝ID是 12 位 `836159698166`。所以先查流水线 state 认过的料号，
    再按位数：>=12 位当淘宝ID，否则当料号交给子进程解析（解析不出会明确报“没有淘宝ID”）。"""
    known = _known_lhs()
    rows = []
    for line in (text or "").replace("\t", ",").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in re.split(r"[,，;；\s]+", line) if p]
        if not parts:
            continue
        if len(parts) >= 2 and re.match(r"^\d{9,14}$", parts[1]):
            rows.append((parts[0], parts[1]))          # 两列：料号,淘宝ID
        elif parts[0] in known:
            rows.append((parts[0], ""))                # state 认得的料号
        elif re.match(r"^\d{12,14}$", parts[0]):
            rows.append(("", parts[0]))                # 位数够长，当淘宝ID
        else:
            rows.append((parts[0], ""))                # 其余当料号，交子进程解析
    seen, out = set(), []
    for lh, tid in rows:
        k = tid or lh
        if k in seen:
            continue
        seen.add(k)
        out.append((lh, tid))
    return out


def _tail_ledger(n=30):
    try:
        with io.open(LEDGER, encoding="utf-8-sig", errors="replace") as f:
            rows = [r for r in csv.reader(f) if r]
        return rows[-n:]
    except Exception:
        return []


def _tail_runlog(n=8):
    """子进程崩溃时 progress.json 可能根本没被写——面板得能看到跑批日志尾，
    否则只会看到“无返回”这种糊话"""
    try:
        with io.open(RUNLOG, encoding="utf-8", errors="replace") as f:
            txt = f.read()
        lines = [x for x in txt.splitlines() if x.strip()]
        return lines[-n:]
    except Exception:
        return []


def handle(action, qs):
    if action == "start":
        mode = (qs.get("mode") or "shop").strip()          # shop=全店（主） / rows=只跑粘进来的
        qty = str(qs.get("qty") or DEFAULT_QTY).strip() or DEFAULT_QTY
        what = (qs.get("what") or "stock").strip()
        gap = str(qs.get("gap") or "2.5").strip() or "2.5"
        limit = str(qs.get("limit") or "0").strip() or "0"
        mirror = str(qs.get("mirror") or "").strip() in ("1", "true", "yes")
        if qty != "open":
            try:
                int(qty)
            except Exception:
                return {"ok": False, "error": "库存值要么是数字，要么走「敞开卖」：%s" % qty}
        rows = parse_rows(qs.get("rows") or "")
        if mode == "rows" and not rows:
            return {"ok": False, "error": "没解析到任何料号/淘宝ID——一行一个，或直接拖 csv 进来"}
        args = [RT, "-X", "utf8", "-u", STOCK_PY]
        if mode == "shop":
            args.append("--shop")
        else:
            os.makedirs(TMP, exist_ok=True)
            fp = os.path.join(TMP, "stock_%s.csv" % time.strftime("%Y%m%d_%H%M%S"))
            with io.open(fp, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["料号", "淘宝ID"])
                w.writerows(rows)
            args += ["--batch", fp]
        args += ["--qty", qty, "--what", what, "--gap", gap, "--json-out", LAST]
        if limit not in ("0", ""):
            args += ["--limit", limit]
        if mirror:
            args.append("--mirror")
        try:      # 清掉上一次的结果，免得面板拿旧数据当新结果
            os.remove(LAST)
        except Exception:
            pass
        try:
            if not os.path.isfile(RT):
                # 指错了要说人话：不这样的话现象是「点了没反应」
                return {"ok": False, "error": "找不到插件要用的 Python：%s\n去 设置 → 通用 →「插件用的 Python」改成真实路径（本机可用：%s）"
                                                % (RT, paths.TG_PYTHON)}
            with io.open(RUNLOG, "a", encoding="utf-8") as lg:
                lg.write("\n=== %s %s %s 品 qty=%s what=%s gap=%s limit=%s mirror=%s ===\n"
                         % (time.strftime("%Y-%m-%d %H:%M:%S"),
                            "全店" if mode == "shop" else "指定", len(rows), qty, what, gap, limit, mirror))
                subprocess.Popen(args, cwd=TG, stdout=lg, stderr=subprocess.STDOUT)
        except Exception as e:
            return {"ok": False, "error": "起子进程失败：%s" % str(e)[:140]}
        return {"ok": True, "n": len(rows), "qty": qty, "mirror": mirror, "mode": mode,
                "msg": ("已启动：全店（库存 %s%s）" if mode == "shop" else "已启动 %d 品（库存 %s%s）")
                       % ((qty, "，带照镜子" if mirror else "") if mode == "shop"
                          else (len(rows), qty, "，带照镜子" if mirror else ""))}
    if action == "progress":
        try:
            with io.open(PROG, encoding="utf-8") as f:
                d = json.load(f)
            return {"ok": True, "progress": d if d.get("kind") == "库存" else None}
        except Exception:
            return {"ok": True, "progress": None}
    if action == "result":
        try:
            with io.open(LAST, encoding="utf-8") as f:
                return {"ok": True, "rows": json.load(f)}
        except Exception:
            return {"ok": True, "rows": []}
    if action == "ledger":
        return {"ok": True, "rows": _tail_ledger(30), "runlog": _tail_runlog(8)}
    if action == "runlog":
        return {"ok": True, "lines": _tail_runlog(12)}
    return {"ok": False, "error": "未知 action: %s" % action}
