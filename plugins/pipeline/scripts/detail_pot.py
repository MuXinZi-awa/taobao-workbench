# -*- coding: utf-8 -*-
"""新品素材生成器 · 微调电位器分支
与 PPTC 的根本差异：电位器规格书里**没有每型号一行的参数表**，
电气参数是系列共用的段落文字，型号只决定「阻值 + 端子形式」。
所以：参数靠正则从原文提取（提不到就跳过并在日志里说），阻值靠型号编码换算。
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

TPLP = os.path.join(ENG, "templates", "pot.html")
SHOT_PROF = os.path.join(os.environ.get("TEMP", ENG), "wf_shot_profile")


def safe(lh):
    return lh.replace("/", "-").replace("\\", "-")


def norm_text(t):
    for a, b in (("ﬁ", "fi"), ("ﬂ", "fl"), ("ﬀ", "ff"), ("ﬃ", "ffi")):
        t = t.replace(a, b)
    t = re.sub(r"\.{3,}", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t


# 型号 → 阻值（Bourns 三位码：前两位有效数字 + 第三位 10 的幂）
def resistance_from_model(lh):
    core = re.sub(r"(LF|E|L)$", "", lh.strip())     # 去包装/无铅后缀
    parts = core.split("-")
    code = parts[-1]
    if not re.fullmatch(r"\d{2,3}", code):
        raise RuntimeError("型号里取不到阻值码：%s（尾段=%s）" % (lh, code))
    sig = int(code[:2]) if len(code) == 3 else int(code[:1])
    exp = int(code[2]) if len(code) == 3 else int(code[1])
    ohms = sig * (10 ** exp)
    if ohms >= 1_000_000:
        return "%g MΩ" % (ohms / 1_000_000), ohms
    if ohms >= 1_000:
        return "%g kΩ" % (ohms / 1_000), ohms
    return "%g Ω" % ohms, ohms


# (显示名, 正则, 分组)
PATTERNS = [
    ("阻值范围",   r"Standard Resistance Range\s*\.{0,}\s*([\d.]+\s*ohms\s*to\s*[\d.]+\s*\w*ohms)", "E"),
    ("阻值公差",   r"Resistance\s*Tolerance\s*\.{0,}\s*(±\s*\d+\s*%)", "E"),
    ("端电阻",     r"End\s*Resistance\s*\.{0,}\s*([\d]+\s*%\s*or\s*[\d]+\s*ohms\s*max)", "E"),
    ("分辨率",     r"Resolution\s*\.{0,}\s*(Infi\s*nite|Infinite)", "E"),
    ("绝缘电阻",   r"([\d]+\s*megohms\s*min)", "E"),
    ("耐压",       r"Sea\s*Level\s*([\d]+\s*vac\s*\([\d]+\s*minute\))", "E"),
    ("调整角度",   r"Adjustment\s*Angle\s*([\d]+\s*(?:°\s*nom|turns?\s*nom))", "E"),
    ("额定功率",   r"(?:70|85)\s*°C\s*\.{0,}\s*([\d.]+\s*wat\w*)", "M"),
    ("最高电压",   r"\(([\d]+\s*volts\s*max)", "M"),
    ("温度范围",   r"Temperature\s*Range\s*(-?\d+\s*°C\s*to\s*\+?\d+\s*°C)", "M"),
    ("温度系数",   r"Temperature\s*Coe\w*\s*cient\s*(±\s*[\d]+\s*ppm/°C)", "M"),
    ("旋转寿命",   r"Rotational\s*(?:Life|Cycling)\s*\.{0,}\s*([\d]+\s*cycles)", "M"),
    ("机械角度",   r"Mechanical\s*Angle\s*([\d]+\s*°\s*nom)", "M"),
    ("起动力矩",   r"Torque\s*([\d]+\s*g-cm\s*max)", "M"),
]

FEAT_KW = [
    ("封装形式", ("single turn",), "单向单圈微调 · 表面贴装"),
    ("材质", ("cermet",), "金属陶瓷（Cermet）电阻体"),
    ("密封", ("sealed",), "工业级密封结构"),
    ("贴装兼容", ("surface mount manufacturing",), "兼容标准 SMT 贴装工艺"),
    ("贴片机兼容", ("pick-and-place",), "兼容主流真空吸嘴贴片机"),
    ("端子形式", ("j-hook",), "可选 J-hook / 鸥翼 / 直插三种端子"),
    ("调节方向", ("side adjust",), "支持侧面调节"),
    ("标准", ("EIA", "SMD standard"), "符合 EIA/EIAJ/IPC/VECI 表面贴装微调器标准"),
]


def unit_norm(v):
    """单位汉化（只换符号，不改数值）——详情图给国内买家看，英文单位很跳"""
    v = v.replace("megohms", "MΩ").replace("ohms", "Ω")
    v = v.replace("watts", "W").replace("watt", "W")
    v = v.replace("volts", "V").replace("volt", "V")
    v = v.replace("cycles", "次")
    v = re.sub(r"\s*turns?\b", " 圈", v)
    v = v.replace("vac", "VAC").replace("vdc", "VDC")
    v = v.replace("minute", "分钟")
    v = re.sub(r"\bto\b", "~", v)
    v = re.sub(r"\s*max\b", " 最大", v)
    v = re.sub(r"\s*min\b", " 最小", v)
    v = re.sub(r"\s+or\s+", " 或 ", v)
    v = re.sub(r"\s*nom\.?\b", " 标称", v)
    return re.sub(r"\s+", " ", v).strip()


def series_name(doc):
    t = norm_text(doc[0].get_text())
    m = re.search(r"(\d{4})\s*-\s*([^\n]{3,60}Trimpot[^\n]*)", t)
    if m:
        return "%s 系列 · %s" % (m.group(1), m.group(2).strip()[:46])
    return "微调电位器系列"


def extract(lh):
    s = safe(lh)
    pdf = os.path.join(MAT, s, "规格书.pdf")
    if not os.path.isfile(pdf):
        raise RuntimeError("没有规格书：%s" % pdf)
    doc = pymupdf.open(pdf)
    txt = norm_text("\n".join(doc[i].get_text() for i in range(doc.page_count)))
    resist_str, resist_ohm = resistance_from_model(lh)

    rows = {"E": [], "M": []}
    missing = []
    for name, pat, grp in PATTERNS:
        m = re.search(pat, txt, re.I)
        if not m:
            missing.append(name)
            continue
        val = re.sub(r"\s+", " ", m.group(1)).strip()
        val = re.sub(r"Infi\s*nite", "无限", val)
        if name == "绝缘电阻":
            val = val + "（500 VDC）"
        val = unit_norm(val)
        rows[grp].append((name, val))

    feats = []
    low = txt.lower()
    for title, kws, desc in FEAT_KW:
        if any(k.lower() in low for k in kws):
            feats.append((title, desc))
        if len(feats) >= 5:
            break

    series = series_name(doc)
    doc.close()
    return {"rows": rows, "missing": missing, "features": feats,
            "series": series, "resistance": resist_str, "pdf": pdf}


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

    def rows_html(grp):
        return "\n".join('<tr><td class="k">%s</td><td class="v">%s</td></tr>' % r
                         for r in d["rows"][grp]) or '<tr><td class="k">—</td><td class="v">—</td></tr>'

    feats = "\n".join('<div class="f"><div class="ft">%s</div><div class="fd">%s</div></div>' % f
                      for f in d["features"]) or \
            '<div class="f"><div class="ft">—</div><div class="fd">规格书未列出可归纳的特性条目</div></div>'

    tags = '<span class="tag">%s</span>' % d["resistance"]
    tags += '<span class="tag n">单圈微调</span><span class="tag n">表面贴装</span><span class="tag n">原厂正品</span>'

    tpl = io.open(TPLP, encoding="utf-8").read()
    html = (tpl.replace("__IMG__", img)
               .replace("__BRAND__", "BOURNS · 伯恩斯")
               .replace("__BRAND_FULL__", "BOURNS")
               .replace("__MODEL__", lh)
               .replace("__RESISTANCE__", d["resistance"])
               .replace("__SERIES__", d["series"])
               .replace("__TAGS__", tags)
               .replace("__ROWS_E__", rows_html("E"))
               .replace("__ROWS_M__", rows_html("M"))
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

    # 淘宝详情图单张限制：高/宽 ≤ 2（页面原话「宝贝详情单张图片尺寸【高/宽≤ 2】」）
    # 超了平台会弹「需裁切」确认框，不点就遮罩挡住提交按钮——不报错、也不跳转（3314G 就是卡在这）
    im2 = Image.open(out_png)
    w2, h2 = im2.size
    if h2 > w2 * 2:
        new_h = int(w2 * 2)
        im2.resize((w2, new_h), Image.LANCZOS).save(out_png)
        print("   [压高] %dx%d (%.2f) -> %dx%d (2.00) 满足淘宝高/宽≤2" % (w2, h2, h2 / w2, w2, new_h))
    return out_png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    lhs = [x.strip() for x in a.only.split(",") if x.strip()]

    bad = 0
    for lh in lhs:
        try:
            d = extract(lh)
            print("\n[%s] 阻值=%s  系列=%s" % (lh, d["resistance"], d["series"]))
            print("  电气 %d 项: %s" % (len(d["rows"]["E"]), d["rows"]["E"]))
            print("  环境机械 %d 项: %s" % (len(d["rows"]["M"]), d["rows"]["M"]))
            if d["missing"]:
                print("  ⚠ 未提取到: %s" % d["missing"])
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
