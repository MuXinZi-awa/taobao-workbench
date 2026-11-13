# -*- coding: utf-8 -*-
"""新品素材生成器 · TBU 高速保护器分支
TBU 规格书是「按型号分组的竖表」+「耐压 × 触发电流 交叉表」：
  · Vimp / Vrms  → 型号列表与值列表同序排列，按型号在列表里的位置取值
  · Itrigger     → 型号后缀（200 = 200 mA）对应的 min/typ/max 三列
  · Rdevice      → 交叉表：先定位 (Vimp, Itrigger) 条件行，再取型号同位置的值
所有取值都先定位再取，不按行序硬猜。
"""
import os, io, re, sys, base64, subprocess, argparse
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

TPLP = os.path.join(ENG, "templates", "tbu.html")
SHOT_PROF = os.path.join(os.environ.get("TEMP", ENG), "wf_shot_profile")


def safe(lh):
    return lh.replace("/", "-").replace("\\", "-")


def page_lines(doc, p):
    return [l.strip() for l in doc[p].get_text().splitlines() if l.strip()]


def col_value_by_model(L, anchor, code):
    """anchor 之后的「TBU-CAxxx…」型号列表，值列表与之一一对应"""
    try:
        i = L.index(anchor)
    except ValueError:
        i = next((k for k, l in enumerate(L) if anchor in l), None)
        if i is None:
            return None
    j = i
    while j < len(L) and not L[j].startswith("TBU-CA"):
        j += 1
    models = []
    while j < len(L) and L[j].startswith("TBU-CA"):
        models.append(L[j])
        j += 1
    vals = L[j:j + len(models)]
    for k, m in enumerate(models):
        if code in m:
            return vals[k] if k < len(vals) else None
    return None


def model_codes(lh):
    """TBU-CA065-200-WH → (系列 CA, 耐压码 065, 电流码 200)"""
    m = re.match(r"TBU-([A-Z]{2})(\d{3})-(\d{3})", lh)
    if not m:
        raise RuntimeError("型号不符合 TBU-XX###-### 格式：%s" % lh)
    return m.group(1), m.group(2), m.group(3)


def extract(lh):
    s = safe(lh)
    pdf = os.path.join(MAT, s, "规格书.pdf")
    if not os.path.isfile(pdf):
        raise RuntimeError("没有规格书")
    series_code, vcode, icode = model_codes(lh)
    doc = pymupdf.open(pdf)
    P1, P2 = page_lines(doc, 0), page_lines(doc, 1)

    vimp = col_value_by_model(P1, "Vimp", vcode)
    vrms = col_value_by_model(P1, "Vrms", vcode)

    # Itrigger：型号后缀 → 型号列表中的位置 → 对应的 min/typ/max
    it_min = it_typ = it_max = None
    idx = next((k for k, l in enumerate(P2) if ("xxx-%s-WH" % icode) in l), None)
    if idx is not None:
        start = idx
        while start > 0 and P2[start - 1].startswith("TBU-CAxxx"):
            start -= 1
        group = []
        k = start
        while k < len(P2) and P2[k].startswith("TBU-CAxxx"):
            group.append(P2[k])
            k += 1
        pos = next((i for i, g in enumerate(group) if icode in g), None)
        if pos is not None:
            vals = P2[k:k + len(group) * 3]
            if len(vals) >= len(group) * 3:
                it_min = vals[pos]
                it_typ = vals[len(group) + pos]
                it_max = vals[len(group) * 2 + pos]

    # Rdevice：交叉表里型号列表与值列表同序
    rdev = None
    try:
        k = next(i for i, l in enumerate(P2) if l.startswith("TBU-CA%s-%s" % (vcode, icode)))
        start = k
        while start > 0 and P2[start - 1].startswith("TBU-CA"):
            start -= 1
        models = []
        j = start
        while j < len(P2) and P2[j].startswith("TBU-CA"):
            models.append(P2[j])
            j += 1
        vals = P2[j:j + len(models)]
        pos = models.index(P2[k])
        rdev = vals[pos] if pos < len(vals) else None
    except (StopIteration, ValueError):
        rdev = None

    def after(lines, label, nth=1, span=8):
        for i, l in enumerate(lines):
            if l.strip() == label:
                c = 0
                for j in range(i + 1, min(i + 1 + span, len(lines))):
                    if re.fullmatch(r"[-+±]?\d+(\.\d+)?(\s*to\s*[-+]?\d+(\.\d+)?)?", lines[j]):
                        c += 1
                        if c == nth:
                            return lines[j]
        return None

    top = after(P1, "Top")
    tstg = after(P1, "Tstg")
    tjmax = after(P1, "Tjmax")
    esd = after(P1, "ESD")
    tblock = after(P2, "tblock")

    doc.close()

    rows_a = []
    if vimp:
        rows_a.append(("峰值脉冲耐压 Vimp（<10 ms）", "%s V" % vimp))
    if vrms:
        rows_a.append(("连续交流 RMS 电压 Vrms", "%s V" % vrms))
    if top:
        rows_a.append(("工作温度范围", "%s °C" % re.sub(r"\s+to\s+", " ~ ", top)))
    if tstg:
        rows_a.append(("存储温度范围", "%s °C" % re.sub(r"\s+to\s+", " ~ ", tstg)))
    if tjmax:
        rows_a.append(("最大结温 Tjmax", "+%s °C" % tjmax.lstrip("+")))

    rows_e = []
    if it_min and it_typ and it_max:
        rows_e.append(("触发电流 Itrigger（最小 / 典型 / 最大）",
                       "%s / %s / %s mA" % (it_min, it_typ, it_max)))
    if rdev:
        rows_e.append(("器件阻抗 Rdevice", "%s Ω" % rdev))
    if tblock:
        rows_e.append(("动作时间 tblock", "%s µs" % tblock))
    if esd:
        rows_e.append(("ESD 防护（HBM, IEC 61000-4-2）", "±%s kV" % esd.lstrip("±")))

    feats = []
    low = " ".join(P1).lower()
    if "overcurrent and overvoltage" in low:
        feats.append(("双重保护", "过流与过压一体化保护"))
    if "high-speed" in low:
        feats.append(("高速", "微秒级动作，快于传统保险丝"))
    if "small smt" in low or "dfn" in low:
        feats.append(("封装", "小型表面贴装 DFN 封装"))
    if "blocks surges" in low:
        feats.append(("耐涌", "可在额定范围内阻断浪涌"))
    if "ul" in low:
        feats.append(("认证", "UL 认证（File No. E315805）"))

    apps = []
    for kw, name in (("vdsl", "语音 / VDSL 卡"), ("dongles", "保护模块与加密狗"),
                     ("process control", "过程控制设备"), ("measurement", "测试与测量设备"),
                     ("general electronics", "通用电子设备")):
        if kw in low:
            apps.append(name)

    return {"rows_a": rows_a, "rows_e": rows_e, "features": feats[:5], "apps": apps,
            "series": "TBU-CA 系列", "vimp": vimp, "pdf": pdf}


def b64(p):
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


def render(lh, d, out_png):
    s = safe(lh)
    img = ""
    for ext in ("jpg", "png"):
        p = os.path.join(MAT, s, "%s_封面.%s" % (s, ext))
        if os.path.isfile(p):
            img = '<img src="data:image/jpeg;base64,%s" alt="%s">' % (b64(p), lh)
            break

    def rt(rows):
        return "\n".join('<tr><td class="k">%s</td><td class="v">%s</td></tr>' % r for r in rows) \
            or '<tr><td class="k">—</td><td class="v">—</td></tr>'

    feats = "\n".join('<div class="f"><div class="ft">%s</div><div class="fd">%s</div></div>' % f
                      for f in d["features"])
    apps = "".join('<span class="app">%s</span>' % a for a in d["apps"]) or '<span class="app">—</span>'

    headline = ("%s V" % d["vimp"]) if d["vimp"] else ""
    tags = '<span class="tag">TBU 高速保护器</span><span class="tag n">DFN 封装</span>'
    tags += '<span class="tag n">双向</span><span class="tag n">原厂正品</span>'

    tpl = io.open(TPLP, encoding="utf-8").read()
    html = (tpl.replace("__IMG__", img)
               .replace("__BRAND__", "BOURNS · 伯恩斯")
               .replace("__BRAND_FULL__", "BOURNS")
               .replace("__MODEL__", lh)
               .replace("__HEADLINE__", headline)
               .replace("__HEADNOTE__", "耐压" if headline else "")
               .replace("__SERIES__", d["series"])
               .replace("__TAGS__", tags)
               .replace("__ROWS_A__", rt(d["rows_a"]))
               .replace("__ROWS_E__", rt(d["rows_e"]))
               .replace("__FEATS__", feats)
               .replace("__APPS__", apps))

    tmp = os.path.join(ENG, "out", "_%s.html" % s)
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    io.open(tmp, "w", encoding="utf-8").write(html)

    if os.path.isfile(out_png):
        os.remove(out_png)
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--user-data-dir=" + SHOT_PROF, "--window-size=1200,3200",
                    "--screenshot=" + out_png, "file:///" + tmp.replace("\\", "/")],
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
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    bad = 0
    for lh in [x.strip() for x in a.only.split(",") if x.strip()]:
        try:
            d = extract(lh)
            print("\n[%s] 系列=%s Vimp=%s" % (lh, d["series"], d["vimp"]))
            print("  最大额定值 %d 项: %s" % (len(d["rows_a"]), d["rows_a"]))
            print("  保护特性 %d 项: %s" % (len(d["rows_e"]), d["rows_e"]))
            print("  特性: %s" % [t for t, _ in d["features"]])
            print("  应用: %s" % d["apps"])
            if a.probe:
                continue
            png = os.path.join(MAT, safe(lh), "%s_详情图.png" % safe(lh))
            render(lh, d, png)
            from PIL import Image
            print("  → %dx%d" % Image.open(png).size)
        except Exception as e:
            bad += 1
            print("\n[%s] ✗ %s" % (lh, e))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
