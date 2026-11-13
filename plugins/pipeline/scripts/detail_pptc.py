# -*- coding: utf-8 -*-
"""新品素材生成器 · PPTC 分支
输入料号 → 读规格书 PDF → 按坐标抽参数 → 套分类模板 → Chrome 截图 → 落位素材库
原则：参数只从 PDF 抽，抽不到就报错停手，绝不填空/猜值（总纲 §29）
"""
import os, io, sys, re, csv, base64, subprocess, argparse
import pymupdf

# 路径：优先工作台 paths.py（换机器只改 paths.local.json），回退相对推导
_HERE = os.path.dirname(os.path.abspath(__file__))
ENG = os.path.dirname(_HERE)                       # pipeline/ 根（模板在这里）
_WB_ROOT = os.path.dirname(os.path.dirname(ENG))   # workbench/ 根（paths.py 在这里）
if _WB_ROOT not in sys.path:
    sys.path.insert(0, _WB_ROOT)
try:
    import paths as _wb
    MAT = _wb.get("mat_root")
except Exception:
    MAT = os.path.join(os.path.dirname(os.path.dirname(_WB_ROOT)), "办公室工作", "素材", "产品素材")
def _find_chrome():
    """Chrome 候选探测：paths 配置 → Program Files → x86 → 用户目录。取第一个真实存在的。"""
    cands = []
    try:
        cands.append(_wb.get("chrome"))
    except Exception:
        pass
    cands += [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
    ]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return cands[0] if cands else ""


CHROME = _find_chrome()

TPLP = os.path.join(ENG, "templates", "pptc.html")
SHOT_PROF = os.path.join(os.environ.get("TEMP", ENG), "wf_shot_profile")


def safe(lh):
    return lh.replace("/", "-").replace("\\", "-")


def rows_of(page, tol=3):
    words = page.get_text("words")
    rows = []
    for w in sorted(words, key=lambda t: (round(t[1], 1), t[0])):
        y0 = w[1]
        if not rows or abs(rows[-1]["y"] - y0) > tol:
            rows.append({"y": y0, "cells": [w]})
        else:
            rows[-1]["cells"].append(w)
    return rows


def core_model(lh):
    """PDF 表里的型号可能不带包装后缀：MF-MSMF050-2 → MF-MSMF050"""
    for suf in ("-2",):
        if lh.endswith(suf):
            return lh[: -len(suf)]
    return lh


def find_model_row(doc, lh):
    core = core_model(lh)
    pat = re.compile(re.escape(core) + r"[A-Za-z0-9\-/]*$")
    for pi in range(min(doc.page_count, 20)):
        for r in rows_of(doc[pi]):
            cells = sorted(r["cells"], key=lambda c: c[0])
            for c in cells:
                if c[4].strip() == core or pat.fullmatch(c[4].strip()):
                    return pi, cells
    return None, None


BULLET = re.compile(r"^\s*([■n●▪◦])\s+(.+)$")
STOP_HEAD = re.compile(r"^(Environmental|Electrical|Specifications|Applications|Construction|"
                       r"Soldering|Packaging|Typical|Dimensional)")

def features_block(doc):
    """原厂 Features 段：从 "Features" 后开始，认多种项目符号（■ / n / ●）——
    不同系列 PDF 的字形不同（字体映射导致 ■ 被抽成 n），只认一种会漏掉整个系列。
    把续行拼回整句。"""
    txt = "\n".join(doc[i].get_text() for i in range(min(doc.page_count, 2)))
    lines = [l.rstrip() for l in txt.splitlines()]
    out, cur, started = [], None, False
    for l in lines:
        s = l.strip()
        if not started:
            if s.lower().startswith("features"):
                started = True
            continue
        if s and STOP_HEAD.match(s):
            break
        m = BULLET.match(l)
        if m:
            if cur:
                out.append(cur)
            cur = m.group(2).strip()
            continue
        if cur is not None:
            if not s:
                continue
            # 脚注 / 声明 / 版权行到了就收尾
            if s.startswith(("*", "**", "Bourns", "The device", "Users should", "Speci")) or len(s) > 180:
                break
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    clean = []
    for f in out:
        f = f.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff")
        f = re.sub(r"-\s*$", "", f)
        f = re.sub(r"\s{2,}", " ", f).strip()
        if len(f) > 8:
            clean.append(f)
    return clean


# 特性 → 中文标准化表述（关键词触发，全部来自 PDF 事实，不做自由发挥）
FEAT_RULES = [
    ("AEC-Q200", "车规认证", "符合 AEC-Q200 Rev-C 无源器件应力测试规范"),
    ("halogen", "无卤", "无卤素（Br ≤ 900 ppm、Cl ≤ 900 ppm、Br+Cl ≤ 1500 ppm）"),
    ("RoHS", "环保", "符合 RoHS 指令"),
    ("1812", "封装", "1812（4532 mils）标准贴装面积"),
    ("4532", "封装", "标准 4532 mm 贴装尺寸"),
    ("surface mount", "贴装", "表面贴装结构，适合自动化产线"),
    ("reflow", "焊接", "兼容含铅与无铅回流焊曲线"),
    ("electrically compatible", "兼容", "与前代同封装器件电气兼容"),
]


def feats_cn(features):
    joined = " ".join(features)
    seen, out = set(), []
    for kw, title, desc in FEAT_RULES:
        if kw.lower() in joined.lower() and title not in seen:
            seen.add(title)
            out.append((title, desc))
        if len(out) >= 5:
            break
    return out


def series_name(doc):
    """从 PDF 抬头提取系列名，如 'MF-NSMF Series'"""
    txt = doc[0].get_text()
    m = re.search(r"([A-Z]{2,}[A-Z0-9\-]*\s+Series)", txt)
    return m.group(1) if m else "PTC 系列"


# 列序（坐标还原自三份 PPTC 规格书，结构一致）：
# vals[0]=型号 vals[1]=Vmax vals[2]=Imax vals[3]=Ihold vals[4]=Itrip
# vals[5]=Rmin vals[6]=R1max vals[7]=trip电流 vals[8]=trip时间 vals[9]=Pd
COLS = [
    ("额定电压 V max.",     "{1} V", "23 °C"),
    ("最大电流 I max.",     "{2} A", "23 °C"),
    ("保持电流 I hold",     "{3} A", "23 °C"),
    ("跳闸电流 I trip",     "{4} A", "23 °C"),
    ("初始电阻 R min.",     "{5} Ω", "23 °C"),
    ("跳闸后电阻 R1 max.",  "{6} Ω", "23 °C"),
    ("跳闸时间",            "{8} s", "{7} A 电流下"),
    ("功耗 Pd（典型）",     "{9} W", "23 °C"),
]


def extract(lh):
    s = safe(lh)
    pdf = os.path.join(MAT, s, "规格书.pdf")
    if not os.path.isfile(pdf):
        raise RuntimeError("没有规格书：%s" % pdf)
    doc = pymupdf.open(pdf)
    pi, cells = find_model_row(doc, lh)
    if cells is None:
        doc.close()
        raise RuntimeError("PDF 里定位不到型号行")
    vals = [c[4].strip() for c in cells]

    # MF-R 系列的表格是 16 列：在 10 列版本里插入了空列，另外多一个 Post-Trip Resistance。
    # 先把空列去掉，变成：型号,Vmax,Imax,Ihold,Itrip,Rmin,Rmax,PostTrip,测试电流,跳闸时间,Pd,认证,认证
    if len(vals) == 16 or (len(vals) > 10 and sum(1 for v in vals if v) == 13):
        nz = [v for v in vals if v]
        if len(nz) >= 11 and nz[0].startswith(core_model(lh)):
            # 去掉 PostTrip（nz[7]），保留 10 列语义
            fixed = [nz[0], nz[1], nz[2], nz[3], nz[4], nz[5], nz[6], nz[8], nz[9], nz[10]]
            print("   [列适配] 16列→10列：Post-Trip 已剔除（%s）" % nz[7])
            vals = fixed

    if len(vals) != 10:
        doc.close()
        raise RuntimeError("型号行不是 10 格（实际 %d 格）：%s" % (len(vals), vals))
    if not vals[0].startswith(core_model(lh)):
        doc.close()
        raise RuntimeError("第一格不是型号（列错位？）：%s" % vals[0])
    d = {"vals": vals, "features": features_block(doc),
         "series": series_name(doc), "page": pi + 1, "pdf": pdf}
    doc.close()
    return d


def b64(p):
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


def render(lh, d, out_png):
    s = safe(lh)
    img = os.path.join(MAT, s, "%s_封面.jpg" % s)
    if not os.path.isfile(img):
        img = os.path.join(MAT, s, "%s_封面.png" % s)
    img_html = '<img src="data:image/jpeg;base64,%s" alt="%s">' % (b64(img), lh) if os.path.isfile(img) else ""

    v = d["vals"]
    rows = "\n".join(
        '<tr><td class="k">%s</td><td class="v">%s</td><td class="u">%s</td></tr>'
        % (name, fmt.format(*v), cond.format(*v)) for name, fmt, cond in COLS
    )

    fc = feats_cn(d["features"])
    if fc:
        feats = "\n".join(
            '<div class="f"><div class="ft">%s</div><div class="fd">%s</div></div>' % (t, ds)
            for t, ds in fc
        )
    else:
        feats = '<div class="f"><div class="ft">—</div><div class="fd">规格书未列出可归纳的特性条目</div></div>'

    pkg = "1812 贴片" if "1812" in " ".join(d["features"]) else "贴片封装"
    tags = '<span class="tag">%s</span>' % pkg
    if "RoHS" in " ".join(d["features"]):
        tags += '<span class="tag n">RoHS · 无卤</span>'
    tags += '<span class="tag n">原厂正品</span>'

    tpl = io.open(TPLP, encoding="utf-8").read()
    html = (tpl.replace("__IMG__", img_html)
               .replace("__BRAND__", "BOURNS · 伯恩斯")
               .replace("__BRAND_FULL__", "BOURNS")
               .replace("__MODEL__", lh)
               .replace("__SERIES__", d["series"])
               .replace("__CATNAME__", "PTC 自恢复保险丝")
               .replace("__TAGS__", tags)
               .replace("__ROWS__", rows)
               .replace("__FEATS__", feats))

    tmp_html = os.path.join(ENG, "out", "_%s.html" % s)
    os.makedirs(os.path.dirname(tmp_html), exist_ok=True)
    io.open(tmp_html, "w", encoding="utf-8").write(html)

    if os.path.isfile(out_png):
        os.remove(out_png)
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--user-data-dir=" + SHOT_PROF, "--window-size=1200,2600",
                    "--screenshot=" + out_png, "file:///" + tmp_html.replace("\\", "/")],
                   capture_output=True, timeout=120)
    if not os.path.isfile(out_png):
        raise RuntimeError("截图失败")
    from PIL import Image
    im = Image.open(out_png).convert("RGB")
    w, h = im.size
    last = h - 1
    for y in range(h - 1, -1, -1):
        if any(min(im.getpixel((x, y))) < 250 for x in range(0, w, 24)):
            last = y
            break
    im.crop((0, 0, w, min(h, last + 1))).save(out_png)
    return out_png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--list", default="")
    a = ap.parse_args()

    lhs = []
    if a.list and os.path.isfile(a.list):
        with io.open(a.list, encoding="utf-8-sig") as f:
            lhs = [r[0].strip() for r in csv.reader(f) if r and r[0].strip()]
    if a.only:
        lhs = [x.strip() for x in a.only.split(",") if x.strip()]

    print("待处理 %d 个" % len(lhs))
    bad = 0
    for lh in lhs:
        try:
            d = extract(lh)
            print("\n[%s] PDF第%d页 系列=%s" % (lh, d["page"], d["series"]))
            print("  参数:", d["vals"])
            print("  特性:", [t for t, _ in feats_cn(d["features"])])
            png = os.path.join(MAT, safe(lh), "%s_详情图.png" % safe(lh))
            render(lh, d, png)
            from PIL import Image
            print("  → %s  %dx%d" % (os.path.basename(png), *Image.open(png).size))
        except Exception as e:
            bad += 1
            print("\n[%s] ✗ %s" % (lh, e))
    print("\n完成，失败 %d" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
