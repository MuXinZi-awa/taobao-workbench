# -*- coding: utf-8 -*-
"""标题生成（保险丝/电位器口径）：商品标题 ≤30 汉字（60 字节）/ 导购标题 ≤15 汉字（30 字节）
参数全部来自规格书抽取结果与价格表，不编造。
"""
import os, io, csv, json
import pymupdf

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
            return os.path.dirname(os.path.dirname(_p.get("mat_root")))
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



ENG = _ENG
MAT = _MAT
OUT = os.path.join(ENG, "标题_v1.csv")

# 售价（梓帆提供的表，直接用，不再乘倍数）
PRICE = {
    "MF-NSMF020-2": 0.13, "MF-MSMF050-2": 0.17, "MF-SMDF050-2": 0.40,
    "MF-PSMF050X-2": 0.11, "MF-PSMF110X-2": 0.13, "MF-SM150/33-2": 0.50,
    "3314G-1-103LF": 1.70, "3314J-1-103LF": 1.50,
    # 子账号2 新增的 7 个
    "3224W-1-102E": 7.00, "3314J-1-102E": 1.80, "MF-R110": 0.37,
    "TBU-CA065-200-WH": 3.50,
}


def safe(lh):
    return lh.replace("/", "-").replace("\\", "-")


def gbk_len(s):
    return len(s.encode("gbk", errors="replace"))


def extract_pptc(lh):
    """从规格书取 (Ihold, Vmax, 系列)"""
    doc = pymupdf.open(os.path.join(MAT, safe(lh), "规格书.pdf"))
    txt = doc[0].get_text()
    words = doc[0].get_text("words")
    rows = []
    for w in sorted(words, key=lambda t: (round(t[1], 1), t[0])):
        if not rows or abs(rows[-1]["y"] - w[1]) > 3:
            rows.append({"y": w[1], "cells": [w]})
        else:
            rows[-1]["cells"].append(w)
    core = lh[:-2] if lh.endswith("-2") else lh
    vals = None
    for r in rows:
        cells = sorted(r["cells"], key=lambda c: c[0])
        if cells and cells[0][4].strip().startswith(core):
            vals = [c[4].strip() for c in cells]
            break
    import re
    m = re.search(r"([A-Z]{2,}[A-Z0-9\-]*\s+Series)", txt)
    series = m.group(1) if m else ""
    doc.close()
    # MF-R 系列是 16 列（插了空列 + 多一个 Post-Trip）——先去空列再剔 PostTrip
    if vals and len(vals) == 16:
        nz = [v for v in vals if v]
        if len(nz) >= 11 and nz[0].startswith(core):
            vals = [nz[0], nz[1], nz[2], nz[3], nz[4], nz[5], nz[6], nz[8], nz[9], nz[10]]
    if not vals or len(vals) != 10:
        return None
    return {"vmax": vals[1], "ihold": vals[3], "series": series}


def pot_resistance(lh):
    import re
    core = re.sub(r"(LF|E|L)$", "", lh)
    code = core.split("-")[-1]
    sig = int(code[:2]) if len(code) == 3 else int(code[:1])
    exp = int(code[2]) if len(code) == 3 else int(code[1])
    o = sig * (10 ** exp)
    if o >= 1_000_000:
        return "%gMΩ" % (o / 1_000_000)
    if o >= 1_000:
        return "%gkΩ" % (o / 1_000)
    return "%gΩ" % o


CAT_PPTC = "贴片自恢复保险丝"
CAT_POT = "微调电位器"

rows = []
for lh, price in PRICE.items():
    is_pptc = lh.startswith("MF-")
    if is_pptc:
        d = extract_pptc(lh)
        if not d:
            print("✗ %s 参数抽取失败，跳过" % lh)
            continue
        spec = "%sA %sV" % (d["ihold"], d["vmax"])
        # 主标题：伯恩斯 + 品类 + 型号 + 规格
        cands = [
            "伯恩斯%s %s %s" % (CAT_PPTC, lh, spec),          # 首选：品牌+品类+型号+规格
            "伯恩斯%s %s %s Bourns原装" % (CAT_PPTC, lh, spec),
            "伯恩斯PTC%s %s %s" % (CAT_PPTC[2:], lh, spec),
            "%s %s %s Bourns" % (CAT_PPTC, lh, spec),
        ]
        guide_cands = [
            "%s %s" % (CAT_PPTC.replace("贴片", ""), spec),
            "%s %s Bourns" % (CAT_PPTC.replace("贴片", ""), spec),
            "PTC自恢复保险丝 %s Bourns" % spec,
            "PTC自恢复保险丝 %s" % spec,
        ]
    elif lh.upper().startswith("TBU"):
        # TBU 浪涌抑制器：型号自带 650V/200mA 信息（065=650V，200=200mA）
        import re as _re
        m = _re.search(r"CA(\d{3})-(\d{3})", lh.upper())
        vmax = "%dV" % (int(m.group(1)) * 10) if m else ""
        imax = "%dmA" % int(m.group(2)) if m else ""
        spec = (vmax + " " + imax).strip()
        cands = [
            "伯恩斯TBU高速保护器 %s %s" % (lh, spec),
            "伯恩斯TBU高速保护器 %s %s Bourns原装" % (lh, spec),
            "%s %s" % (lh, spec),
        ]
        guide_cands = [
            "高速保护器 %s Bourns原装" % spec,
            "TBU高速保护器 %s" % spec,
            "高速保护器 %s" % spec,
        ]
    else:
        r = pot_resistance(lh)
        spec = r
        cands = [
            "伯恩斯%s %s %s" % (CAT_POT, lh, r),
            "伯恩斯%s %s %s Bourns原装" % (CAT_POT, lh, r),
            "%s %s %s" % (CAT_POT, lh, r),
        ]
        guide_cands = ["%s %s Bourns原装" % (CAT_POT, r),
                       "精密%s %s 可调" % (CAT_POT, r),
                       "%s %s" % (CAT_POT, r)]

    # 梓帆要「原装」：在限内优先选带 Bourns原装 的写法（信息最全）
    cands = sorted(cands, key=lambda c: (0 if '原装' in c else 1, -gbk_len(c)))
    title = next((c for c in cands if gbk_len(c) <= 60), cands[-1])
    # 淘宝：导购标题 20~30 字节（"不能少于10个汉字或20个字符"）
    guide = next((c for c in guide_cands if 20 <= gbk_len(c) <= 30), guide_cands[0])
    rows.append({"料号": lh, "售价": price, "商品标题": title, "标题字节": gbk_len(title),
                 "导购标题": guide, "导购字节": gbk_len(guide),
                 "导购合格": '是' if 20 <= gbk_len(guide) <= 30 else '否'})

with io.open(OUT, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["料号", "售价", "商品标题", "标题字节", "导购标题", "导购字节", "导购合格"])
    w.writeheader()
    for r in rows:
        w.writerow(r)

print("%-20s %-6s %-3s %s" % ("料号", "售价", "字节", "商品标题 / 导购标题"))
print("-" * 96)
for r in rows:
    print("%-20s %-6s %-3d %s" % (r["料号"], r["售价"], r["标题字节"], r["商品标题"]))
    print("%-20s %-6s %-3d   └ %s" % ("", "", r["导购字节"], r["导购标题"]))
print()
print("上限：商品 60 字节 / 导购 30 字节")
print("→", OUT)
