# -*- coding: utf-8 -*-
"""新品素材生成器 · TVS 分支（瞬态电压抑制器）
TVS 规格书是「4 列表格」：Rating / Symbol / Value / Unit，且 Min-Typ-Max 分列。
抽法：先按行切开，找标签行，再取其后第 n 个纯数字行——不猜列序，每项规则都写明取哪一个。
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

TPLP = os.path.join(ENG, "templates", "tvs.html")
SHOT_PROF = os.path.join(os.environ.get("TEMP", ENG), "wf_shot_profile")

NUM_LINE = re.compile(r"^-?\d+(\.\d+)?(\s*(to|~)\s*\+?-?\d+(\.\d+)?)?$")


def safe(lh):
    return lh.replace("/", "-").replace("\\", "-")


def lines_of(doc, page=None):
    """取文本行；page 给定时只取该页（参数表在第一页，全局搜会先命中曲线图标题）"""
    out = []
    rng_ = range(doc.page_count) if page is None else [page]
    for i in rng_:
        for l in doc[i].get_text().splitlines():
            if l.strip():
                out.append(l.strip())
    return out


def pick(lines, label, nth=1, span=16):
    """label 之后第 nth 个「纯数字行」。
    先试**整行相等**——参数表里标签独占一行；回退了再用包含匹配。
    不这样做会命中正文描述句（如“a Minimum Breakdown Voltage of 26.2 V”）或曲线图标题。"""
    lab = label.strip().lower()
    hits = [i for i, l in enumerate(lines) if l.strip().lower() == lab]
    if not hits:
        hits = [i for i, l in enumerate(lines) if lab in l.strip().lower()]
    for i in hits:
        cnt = 0
        for j in range(i + 1, min(i + 1 + span, len(lines))):
            if NUM_LINE.match(lines[j]):
                cnt += 1
                if cnt == nth:
                    return lines[j]
    return None


def rng(v):
    """-40 to +150 → -40 ~ +150"""
    return re.sub(r"\s+to\s+", " ~ ", v).strip() if v else v


def series_of(lh):
    m = re.match(r"^(CDSOT\d+)", lh)
    return (m.group(1) if m else lh) + " 系列"


def extract(lh):
    s = safe(lh)
    pdf = os.path.join(MAT, s, "规格书.pdf")
    if not os.path.isfile(pdf):
        raise RuntimeError("没有规格书")
    doc = pymupdf.open(pdf)
    L = lines_of(doc, 0)                 # 参数表所在页
    FULL = " ".join(lines_of(doc))       # 全文，仅供特性关键词用

    miss = []

    def g(label, nth=1, span=16):
        v = pick(L, label, nth, span)
        if v is None:
            miss.append(label)
        return v

    vdrm = g("Repetitive Peak Off-state Voltage")
    ipp8 = g("Impulse Current, 8/20")
    ipp12 = g("Impulse Current, 1.2/50")
    esd = g("ESD (IEC 61000-4-2")
    tj = g("Junction Temperature")
    tstg = g("Storage Temperature")
    idc = g("Leakage Current")
    vbr_min = g("Breakdown Voltage", 1)
    vbr_max = g("Breakdown Voltage", 2)
    # 两个钳位电压：靠测试条件区分（5 A / 8 A）——不能靠“第几个”猜
    vc5 = pick(L, "= 5 A", 1, 10) or pick(L, "Clamping Voltage", 1, 10)
    vc8 = pick(L, "= 8 A", 1, 10) or pick(L, "Clamping voltage", 1, 10)
    ppp = g("Peak Pulse Power")
    cj_t = g("Capacitance", 1, 12)
    cj_m = g("Capacitance", 2, 12)
    doc.close()

    # 一致性校验：击穿电压必须高于关断电压（防“取到曲线图刻度”这类错）
    try:
        if vdrm and vbr_min and float(vbr_min) <= float(vdrm):
            raise RuntimeError("击穿电压 %s 不大于关断电压 %s——抽到的不是参数表" % (vbr_min, vdrm))
    except ValueError:
        pass
    # 电容应在 1~10000 pF 量级（防取到坐标轴）
    try:
        if cj_t and not (1 <= float(cj_t) <= 10000):
            raise RuntimeError("结电容 %s pF 不合理" % cj_t)
    except ValueError:
        pass

    rows_a = []
    if vdrm:
        rows_a.append(("关断电压 VDRM", "%s V" % vdrm))
    if ipp8:
        rows_a.append(("峰值脉冲电流（8/20 µs）", "%s A" % ipp8))
    if ipp12:
        rows_a.append(("峰值脉冲电流（1.2/50 µs）", "%s A" % ipp12))
    if esd:
        rows_a.append(("ESD 防护（IEC 61000-4-2 接触）", "%s kV" % esd))
    if tj:
        rows_a.append(("工作结温 TJ", "%s °C" % rng(tj)))
    if tstg:
        rows_a.append(("存储温度", "%s °C" % rng(tstg)))

    rows_e = []
    if idc:
        rows_e.append(("漏电流 ID", "≤ %s nA" % idc))
    if vbr_min and vbr_max:
        rows_e.append(("击穿电压 VBR", "%s ~ %s V" % (vbr_min, vbr_max)))
    elif vbr_min:
        rows_e.append(("击穿电压 VBR", "%s V" % vbr_min))
    if vc5:
        rows_e.append(("钳位电压 VC（5 A）", "%s V" % vc5))
    if vc8:
        rows_e.append(("钳位电压 VC（8 A）", "%s V" % vc8))
    if ppp:
        rows_e.append(("峰值脉冲功率 PPP", "%s W" % ppp))
    if cj_t:
        rows_e.append(("结电容 C", "%s / %s pF（典型 / 最大）" % (cj_t, cj_m or cj_t)))

    low = FULL.lower()
    feats = []
    if "sot-23" in low:
        feats.append(("封装", "SOT-23-3L 标准贴装封装"))
    if "can" in low:
        feats.append(("应用", "CAN 总线瞬态保护"))
    if "aec-q101" in low or "aec-q" in low:
        feats.append(("车规", "符合 AEC-Q 车规认证要求"))
    if "esd" in low:
        feats.append(("抗扰", "高等级 ESD 防护（IEC 61000-4-2）"))
    if "capacitance" in low:
        feats.append(("低容", "低结电容，不影响信号完整性"))
    if len(feats) < 3:
        feats.append(("保护", "双向瞬态电压抑制，快速钳位"))

    return {"rows_a": rows_a, "rows_e": rows_e, "features": feats[:5],
            "missing": miss, "series": series_of(lh), "vdrm": vdrm, "pdf": pdf}


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

    headline = ("%s V" % d["vdrm"]) if d["vdrm"] else ""
    tags = ""
    if "SOT" in lh.upper():
        tags += '<span class="tag">SOT-23 封装</span>'
    if "CAN" in lh.upper():
        tags += '<span class="tag n">CAN 总线</span>'
    if "-Q" in lh.upper():
        tags += '<span class="tag n">车规 -Q</span>'
    tags += '<span class="tag n">原厂正品</span>'

    tpl = io.open(TPLP, encoding="utf-8").read()
    html = (tpl.replace("__IMG__", img)
               .replace("__BRAND__", "BOURNS · 伯恩斯")
               .replace("__BRAND_FULL__", "BOURNS")
               .replace("__MODEL__", lh)
               .replace("__HEADLINE__", headline)
               .replace("__HEADNOTE__", "关断电压" if headline else "")
               .replace("__SERIES__", d["series"])
               .replace("__TAGS__", tags)
               .replace("__ROWS_A__", rt(d["rows_a"]))
               .replace("__ROWS_E__", rt(d["rows_e"]))
               .replace("__FEATS__", feats))

    tmp = os.path.join(ENG, "out", "_%s.html" % s)
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    io.open(tmp, "w", encoding="utf-8").write(html)

    if os.path.isfile(out_png):
        os.remove(out_png)
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--user-data-dir=" + SHOT_PROF, "--window-size=1200,3000",
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
            print("\n[%s] 系列=%s  VDRM=%s" % (lh, d["series"], d["vdrm"]))
            print("  最大额定值 %d 项: %s" % (len(d["rows_a"]), d["rows_a"]))
            print("  电气特性 %d 项: %s" % (len(d["rows_e"]), d["rows_e"]))
            if d["missing"]:
                print("  ⚠ 未提取: %s" % d["missing"])
            print("  特性: %s" % [t for t, _ in d["features"]])
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
