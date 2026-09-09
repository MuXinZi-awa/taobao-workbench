# -*- coding: utf-8 -*-
"""流水线插件 server：状态读取（只读调用层）"""
import json, os, io, sys, csv

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_FP = os.path.join(ROOT, "state.json")
TG = r"C:\Users\jdt-pty\Desktop\推广一键跑"
SUCAI = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\素材\产品素材"
TG_REC = os.path.join(TG, "推广记录.csv")

def load_state():
    try:
        return json.load(io.open(STATE_FP, encoding="utf-8"))
    except Exception:
        return {"items": {}}

def save_state(st):
    io.open(STATE_FP, "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False, indent=1))

def _read_tg_rec():
    """推广记录.csv → {料号: 最后状态} 只读"""
    out = {}
    if not os.path.exists(TG_REC):
        return out
    try:
        with io.open(TG_REC, encoding="utf-8-sig", errors="replace") as f:
            for row in csv.DictReader(f):
                cols = list(row)
                lh = row.get("料号") or row.get("lh") or ""
                st = row.get("状态") or row.get("结果") or ""
                if lh:
                    out.setdefault(lh.strip(), st.strip())
    except Exception:
        pass
    return out

def scan_material(lh):
    """素材目录清点 → 缺啥补啥看板"""
    d = os.path.join(SUCAI, lh)
    out = {"封面": False, "主图": False, "规格书": False, "视频": False, "详情图": False, "文件": [], "缺失": []}
    if not os.path.isdir(d):
        out["缺失"] = ["整个目录"]
        return out
    names = os.listdir(d)
    out["文件"] = sorted(names)
    for n in names:
        low = n.lower()
        if "封面" in n or "cover" in low: out["封面"] = True
        elif "详情" in n or "detail" in low: out["详情图"] = True
        if any(k in n for k in ("白底", "主图", "cover")) and not any(k in n for k in ("详情", "待复核")):
            out["主图"] = True
        if any(k in low for k in (".pdf", ".doc", ".docx", "规格", "spec")): out["规格书"] = True
        if low.endswith((".mp4", ".mov", ".webm")): out["视频"] = True
    for k in ("封面", "主图", "规格书", "视频"):
        if not out[k]:
            out["缺失"].append(k)
    return out

def scan_batch(lhs):
    """逐品汇总状态（本版：素材清点 + 推广记录 + state 内的人工/阶段标记）"""
    st = load_state()
    tg = _read_tg_rec()
    items = []
    for lh in lhs:
        lh = (lh or "").strip()
        if not lh:
            continue
        m = scan_material(lh)
        rec = st["items"].get(lh, {})
        s = rec.get("stage", "")
        items.append({
            "lh": lh,
            "id": rec.get("id", ""),
            "title": rec.get("title", ""),
            "素材": "缺:" + ",".join(m["缺失"]) if m["缺失"] else "齐",
            "缺列表": m["缺失"],
            "审核": rec.get("audit", ""),
            "上品/优化": rec.get("sp", ""),
            "推广": tg.get(lh, ""),
            "stage": s,
        })
    try:
        _wlog("流水线扫描 %d 品: %s" % (len(items), " | ".join("%s=%s" % (i["lh"], i["素材"][:16]) for i in items[:10])))
    except Exception:
        pass
    return items

import datetime as _dt
_LOG_FP = r"C:\Users\jdt-pty\Desktop\推广一键跑\runtime\pipeline.log"


_rot_day = None


def _maybe_rotate():
    """0909 按天归档：日志文件日期≠今天 → 改名加日期保留"""
    import datetime as _dt2, os as _os2
    global _rot_day
    try:
        today = _dt2.date.today()
        if _rot_day == today:
            return
        if _os2.path.isfile(_LOG_FP):
            m = _dt2.date.fromtimestamp(_os2.path.getmtime(_LOG_FP))
            if m != today:
                _os2.rename(_LOG_FP, _LOG_FP.replace(".log", "_%s.log" % m.strftime("%Y%m%d")))
        _rot_day = today
    except Exception:
        pass


def _wlog(msg):
    _maybe_rotate()
    try:
        with io.open(_LOG_FP, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (_dt.datetime.now().strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def handle(action, qs):
    if action == "scan":
        lhs = qs.get("lhs", "").split(",") if qs.get("lhs") else []
        return {"ok": True, "items": scan_batch(lhs)}
    if action == "material":
        lh = qs.get("lh", "")
        if lh:
            return {"ok": True, "material": scan_material(lh)}
        return {"ok": False, "error": "缺 lh"}
    return {"ok": False, "error": "未知 action: %s" % action}
