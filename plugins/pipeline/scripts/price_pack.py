# -*- coding: utf-8 -*-
"""上品准备表：料号 × 成本 × 按规则算的售价 × 素材状态 × 数据冲突
价格源：伯恩斯测试型号.xls（梓帆提供）
规则：售价 = 成本 × 倍数（<1 ×1.8 / 1~20 ×1.5 / >20 ×1.7），两位小数
"""
import os, io, csv, json, re
import xlrd

# --- workbench 路径兼容层：换机器只改 paths.local.json / WB_* 环境变量 ---
def _wb_ws_root():
    """返回工作区根（OH-WorkSpace）。先找 workbench 的 paths.py，再退回自身上溯。"""
    import sys as _s
    _here = os.path.dirname(os.path.abspath(__file__))
    for _base in (_here, os.path.dirname(_here), os.path.dirname(os.path.dirname(_here))):
        _c = os.path.join(_base, "paths.py")
        if os.path.isfile(_c):
            if _base not in _s.path:
                _s.path.insert(0, _base)
            import paths as _p
            _mr = _p.get("mat_root")
            # mat_root = <WS>\\办公室工作\\素材\\产品素材 → 上溯三级才到 <WS>
            return os.path.dirname(os.path.dirname(os.path.dirname(_mr)))
    _x = _here
    for _ in range(6):
        if os.path.isdir(os.path.join(_x, "办公室工作")):
            return _x
        _nd = os.path.dirname(_x)
        if _nd == _x:
            break
        _x = _nd
    return r"C:\Users\jdt-pty\Desktop\OH-WorkSpace"

_WS  = _wb_ws_root()
_MAT = os.path.join(_WS, "办公室工作", "素材", "产品素材")
_ENG = os.path.join(_WS, "_工程_新品素材")
_TG  = os.environ.get("WB_TG_ROOT") or r"C:\Users\jdt-pty\Desktop\推广一键跑"
# --- 兼容层结束 ---



XLS = r"C:\Users\jdt-pty\.hanako\session-files\b31eb343454df77d6b4c19bf\伯恩斯测试型号_mwaemzoy_8b98fb8c.xls"
MAT = _MAT
ENG = _ENG
OUT = os.path.join(ENG, "上品准备表.csv")

# 28 个料号里，要动的是 8 新品 + 3 优化
NEW = ["MF-NSMF020-2", "MF-MSMF050-2", "MF-SMDF050-2", "MF-PSMF050X-2",
       "MF-PSMF110X-2", "MF-SM150/33-2", "3314G-1-103LF", "3314J-1-103LF"]
OPT = ["CDSOT23-T24CAN-Q", "TBU-CA065-200-WH", "MF-LSMF300/24X-2"]


def price_of(cost):
    if cost < 1:
        m = 1.8
    elif cost <= 20:
        m = 1.5
    else:
        m = 1.7
    return round(cost * m, 2), m


# 读价格表（允许同料号多行）
bk = xlrd.open_workbook(XLS)
sh = bk.sheet_by_index(0)
costs = {}
for r in range(sh.nrows):
    lh = str(sh.cell_value(r, 0)).strip()
    try:
        c = float(sh.cell_value(r, 1))
    except Exception:
        continue
    if lh:
        costs.setdefault(lh, []).append(c)


def lookup(lh):
    """先精确，再去包装后缀（-LF/-E/-L）模糊"""
    if lh in costs:
        return costs[lh], "精确"
    core = re.sub(r"(LF|E|L)$", "", lh)
    cand = [k for k in costs if re.sub(r"(LF|E|L)$", "", k) == core]
    if len(cand) == 1:
        return costs[cand[0]], "后缀不同→%s" % cand[0]
    if len(cand) > 1:
        return [], "多重匹配:%s" % "/".join(cand)
    return [], "价格表无此料号"


def safe(lh):
    return lh.replace("/", "-").replace("\\", "-")


def material(lh):
    d = os.path.join(MAT, safe(lh))
    if not os.path.isdir(d):
        return "无目录", ""
    fs = os.listdir(d)
    have = []
    if any("_封面" in f for f in fs):
        have.append("封面")
    if any("_详情图" in f for f in fs):
        have.append("详情图")
    if any("_视频" in f for f in fs):
        have.append("视频")
    if any("规格书" in f for f in fs):
        have.append("规格书")
    return ",".join(have), ""


rows = []
print("%-20s %-6s %-6s %-6s %-8s %s" % ("料号", "成本", "倍数", "售价", "来源", "备注"))
print("-" * 88)
for kind, group in (("新品-上品", NEW), ("老品-补属性", OPT)):
    for lh in group:
        vals, src = lookup(lh)
        note = []
        if len(vals) > 1:
            note.append("⚠价格表两个价:%s" % vals)
        cost = vals[0] if vals else None
        if cost is None:
            p, m = "", ""
            note.append("⚠没成本，无法定价")
        elif kind == "老品-补属性":
            p, m = "", ""     # 优化不需要定价
        else:
            p, m = price_of(cost)
        mt, _ = material(lh)
        rows.append({"类型": kind, "料号": lh, "成本": cost if cost is not None else "",
                     "倍数": m, "售价": p, "价格来源": src, "素材": mt,
                     "备注": "；".join(note)})
        print("%-20s %-6s %-6s %-6s %-8s %s" % (
            lh, cost if cost is not None else "-", m or "-", p or "-", src,
            "；".join(note) + (" 素材:" + mt if mt else "")))

with io.open(OUT, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["类型", "料号", "成本", "倍数", "售价", "价格来源", "素材", "备注"])
    w.writeheader()
    for r in rows:
        w.writerow(r)

print()
print("→ %s" % OUT)
