# -*- coding: utf-8 -*-
"""本地修图：图库图 → 封面裁剪 + 主图2-5（快速版——不用 AI）
流程：评分选优 → 封面/主图 居中裁剪缩放 → 详情图 → 视频
口径：本地只做 裁剪/尺寸/命名/编排；**白底/去水印交 AI 送修**（ai_schedule，只清理不重绘）
      本地画白（flood fill）是废的（喷枪白漆效果）——别做（梓帆 0923 定）
用法：python make_local.py 料号 [--force]
"""
import sys
import os
import glob
import shutil

# PRODUCT_BASE 在 _wb_paths() 定义之后赋值（见下方）
MIN_SCORE = 60  # 本地图最低评分（低于则交给 AI）


# ---- 工作台 paths 探测（拿到 workbench 根 → 调 pipeline 的四类详情图生成器）----
def _wb_paths():
    r"""找 workbench 根目录（带 paths.py 的那层）。

    本脚本在 Desktop\推广一键跑\，workbench 在 Desktop\OH-WorkSpace\工具\workbench\，
    两者是兄弟目录——只向上递归永远找不到，所以先向上列，再枚举兄弟位置。
    """
    _here = os.path.dirname(os.path.abspath(__file__))
    cands = []
    # 1) 环境变量优先（换机器只改这一条）
    _ev = os.environ.get("WB_ROOT")
    if _ev:
        cands.append(_ev)
    # 2) 自身向上（workbench 插件里的脚本走这条）
    x = _here
    for _ in range(6):
        cands.append(x)
        _pd = os.path.dirname(x)
        if _pd == x:
            break
        x = _pd
    # 3) 兄弟目录（推广一键跑 ↔ OH-WorkSpace\工具\workbench）
    for _base in (_here, os.path.dirname(_here)):
        cands.append(os.path.join(_base, "OH-WorkSpace", "工具", "workbench"))
        cands.append(os.path.join(_base, "工具", "workbench"))
        cands.append(os.path.join(_base, "workbench"))
    for _c in cands:
        try:
            if _c and os.path.isfile(os.path.join(_c, "paths.py")):
                if _c not in sys.path:
                    sys.path.insert(0, _c)
                import paths
                globals()["_WB_PATHS"] = paths
                return paths
        except Exception:
            continue
    return None


_P = _wb_paths()
PRODUCT_BASE = _P.get("mat_root") if _P else r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\素材\产品素材"


def _pick_detail_generator(lh):
    """从工作台 pipeline 插件找四类详情图生成器。

    分类绝不能混：PPTC 每型号一行的 10 列表 / 电位器系列共用段落 /
    TVS 4 列 Min-Typ-Max / TBU 耐压×触发电流交叉表，四套抽取逻辑各不同。
    找不到对应生成器就返回 None，由调用方回退。
    """
    _p = _wb_paths()
    if not _p:
        return None
    try:
        _d = os.path.join(_p.get("root"), "plugins", "pipeline", "scripts")
    except Exception:
        return None
    u = (lh or "").upper()
    if any(u.startswith(x) for x in ("3224", "3296", "3314", "3362", "3590", "TC33")):
        n = "detail_pot.py"
    elif u.startswith("CDSOT"):
        n = "detail_tvs.py"
    elif u.startswith("TBU"):
        n = "detail_tbu.py"
    elif u.startswith("MF-"):
        n = "detail_pptc.py"
    else:
        return None
    f = os.path.join(_d, n)
    return f if os.path.isfile(f) else None


def pick_best(lh, need=5):
    """从图库选评分最高的 need 张产品图（800×800 优先 + 原图兜底；跳过标签/污染图）"""
    import train_selector as ts
    scored = []
    # 0. 审核产物最优先：人工换源/送检回来的图（*_待复检* / *_换源* / *_库源*）是已确认的干净产品图，
    #    优于图库兜底（图库兜底连“带水印但可用”都收）
    try:
        _md = os.path.join(PRODUCT_BASE, lh)
        if os.path.isdir(_md):
            for f in sorted(os.listdir(_md)):
                if not f.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                if "标注" in f:
                    continue
                if ("可用" in f or "待复检" in f or "待复核" in f or "换源" in f):
                    scored.append((os.path.join(_md, f), 999))
                elif "库源" in f:
                    scored.append((os.path.join(_md, f), 998))
    except Exception:
        pass
    # 有审核产物 → **只用产物**：这类品的其余素材多半仍是带水印的脏图，
    # 混进来会把成品带脏（梓帆 0918 定：只送一张、不混入其他）
    _clean = [p for p, s in scored if s == 999] or [p for p, s in scored if s == 998]
    if _clean:
        return _clean[:need]
    # 0b. 素材目录已有图优先（用户手动放的图库图/找的图——直接可用）
    try:
        mat_dir = os.path.join(PRODUCT_BASE, lh)
        if os.path.isdir(mat_dir):
            for f in sorted(os.listdir(mat_dir)):
                if not f.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                if "_封面" in f or "_主图" in f or "_白底" in f or "_详情" in f:
                    continue  # 跳过生成产物（避免循环）
                fp = os.path.join(mat_dir, f)
                try:
                    if ts.is_label_like(fp):
                        continue
                    s = ts.score_image(fp)
                    if s[0] >= MIN_SCORE:
                        scored.append((fp, s[0]))
                except Exception:
                    continue
    except Exception:
        pass
    d2, src = ts.find_images(lh)
    cands = []
    if d2:
        cands.append(d2)  # 800×800 优先（大部分没污染）
        org = os.path.join(os.path.dirname(d2), "%s原图" % lh)
        if os.path.isdir(org):
            cands.append(org)  # 原图兜底（未污染高分辨率）
    for d in cands:
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            fp = os.path.join(d, f)
            try:
                if ts.is_label_like(fp):
                    continue
                if "原图" not in d and _looks_polluted(fp):
                    continue
                s = ts.score_image(fp)
                if s[0] >= MIN_SCORE:
                    scored.append((fp, s[0]))
            except Exception:
                continue
    # 800/原图无高分图 → 其他文件夹（千金/CY）兜底——用户确认有些图可用
    if len(scored) < 3:
        try:
            from 素材_prep import scan_other_folders
            found, src = scan_other_folders(lh)
            for fp in found:
                try:
                    if ts.is_label_like(fp):
                        continue
                    s = ts.score_image(fp)
                    if s[0] >= 40:  # 千金/CY 图门槛（带水印/背景杂但可用）
                        scored.append((fp, s[0]))
                except Exception:
                    continue
        except Exception:
            pass
    scored.sort(key=lambda x: x[1], reverse=True)
    # 去重（素材目录图可能和图库同源）
    seen = set()
    uniq = []
    for p, s in scored:
        if p not in seen:
            seen.add(p)
            uniq.append((p, s))
    return [p for p, _ in uniq[:need]]


def _has_low_score(lh):
    """检测图库/素材目录有低分图（25-59）——广告/logo/排版被扣分但图可能可用"""
    import train_selector as ts
    try:
        # 素材目录已有图优先（用户手动放图——低分也可用）
        mat_d = os.path.join(PRODUCT_BASE, lh)
        if os.path.isdir(mat_d):
            for f in sorted(os.listdir(mat_d)):
                if not f.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                if "_封面" in f or "_主图" in f or "_白底" in f or "_详情" in f:
                    continue
                fp = os.path.join(mat_d, f)
                try:
                    s = ts.score_image(fp)
                    if 25 <= s[0] < MIN_SCORE:
                        return int(s[0])
                except Exception:
                    continue
        d2, src = ts.find_images(lh)
        cands = []
        if d2:
            cands.append(d2)
            org = os.path.join(os.path.dirname(d2), "%s原图" % lh)
            if os.path.isdir(org):
                cands.append(org)
        for d in cands:
            for f in sorted(os.listdir(d)):
                if not f.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                fp = os.path.join(d, f)
                try:
                    s = ts.score_image(fp)
                    if 25 <= s[0] < MIN_SCORE:
                        return int(s[0])
                except Exception:
                    continue
        # 千金/CY 兜底
        try:
            from 素材_prep import scan_other_folders
            found, src2 = scan_other_folders(lh)
            for fp in found:
                s = ts.score_image(fp)
                if 25 <= s[0] < 60:  # 有图就算（25 以下才真废）——广告/logo 扣分不误判缺图
                    return int(s[0])
        except Exception:
            pass
    except Exception:
        pass
    return 0


def _looks_polluted(path):
    """污染检测：四角全纯白(255)且内容占比高——疑似被白底化处理过（评分失真）"""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        w, h = im.size
        for xy in [(3, 3), (w - 4, 3), (3, h - 4), (w - 4, h - 4)]:
            r, g, b = im.getpixel(xy)
            if (r, g, b) != (255, 255, 255):
                return False
        return True
    except Exception:
        return False


def get_brand_raw(lh):
    """品牌原始属性值（产品变体——TE Connectivity/APTIV/MOLEX——发布页匹配用）"""
    try:
        bm = os.path.join(r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\数据", "brand_map.csv")
        if os.path.isfile(bm):
            import csv as _csv
            with open(bm, encoding="utf-8-sig") as f:
                for row in _csv.reader(f):
                    if row and row[0].strip() == lh and len(row) > 1:
                        return row[1].strip()
    except Exception:
        pass
    return ""


def get_brand(lh):
    """品牌：brand_map.csv 权威（产品变体导出）→ 中文显示映射
    约束（铁律）：品牌缺失时**不默认泰科**——宁可不填/停批标记，也不默认（历史事故：全默认 TE 导致大规模错填）
    get_brand_raw 返回原始值（发布页匹配）；get_brand 返回中文（封面显示）
    """
    # 品牌中文显示映射
    DISPLAY = {
        "TE Connectivity": "泰科", "TE": "泰科", "APTIV": "德尔福", "Delphi": "德尔福",
        "MOLEX": "莫仕", "Molex": "莫仕", "SUMITOMO": "住友", "Sumitomo": "住友",
        "YAZAKI": "矢崎", "Amphenol": "安费诺", "Hirose": "广濑", "HRS": "广濑",
        "KET": "KET", "JST": "JST", "LEAR": "李尔", "BOSCH": "博世", "HL": "HL",
        "LOCTITE": "乐泰",
    }
    raw = get_brand_raw(lh)
    if raw:
        return DISPLAY.get(raw, raw)
    # ② 表格标题关键词（参考）
    try:
        import openpyxl
        wb = openpyxl.load_workbook(r"C:\Users\jdt-pty\Desktop\榜单_重点优化.xlsx", data_only=True)
        ws = wb.active
        for r in range(2, ws.max_row + 1):
            if str(ws.cell(row=r, column=1).value or "").strip() == lh:
                title = str(ws.cell(row=r, column=5).value or "")
                for kw, b in [("德尔福", "德尔福"), ("住友", "住友"), ("莫仕", "莫仕"),
                              ("泰科", "泰科"), ("矢崎", "矢崎"), ("安费诺", "安费诺")]:
                    if kw in title:
                        return b
                break
    except Exception:
        pass
    # ③ 属性清单
    try:
        import csv as _csv
        attrs_csv = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\数据\属性清单.csv"
        if os.path.isfile(attrs_csv):
            with open(attrs_csv, encoding="utf-8-sig") as f:
                for row in _csv.reader(f):
                    if row and row[0].strip() == lh and len(row) > 2:
                        b = (row[2] or "").strip()
                        if b and b not in ("", "泰科"):
                            return b
                        break
    except Exception:
        pass
    # ④ 千金图库：按**文件名**找料号（库本身就是按品牌分好目录的台账——目录名即品牌）
    #    文件名格式 `<料号>_序号.jpg`；别拿料号去匹配目录名（那样永远不命中）。
    try:
        import glob as _glob
        base = r"F:\连接图图库\千金有货图片"  # 本地图库（原 UNC 已拷贝本地——快 10 倍）
        if os.path.isdir(base):
            disp = {"APTIV(Delphi)": "德尔福", "HRS": "广濑", "JAE": "日本航空电子",
                    "JST": "JST", "KET": "KET", "MOLEX": "莫仕", "TE": "泰科",
                    "YAZAKI": "矢崎", "住友": "住友", "安费诺": "安费诺",
                    "KUM": "KUM", "SWB": "SWB", "VISHAY": "VISHAY",
                    "NEXPERIA": "NEXPERIA", "ONSEMI": "ONSEMI"}
            for fp in _glob.glob(os.path.join(base, "*", "%s_*" % lh)):
                sub = os.path.basename(os.path.dirname(fp))
                for k, v in disp.items():
                    if k == sub:
                        return v
                return sub          # 目录名不在表里就原样返回——让人看得见、而不是默默编一个
    except Exception:
        pass
    # 四个来源均无——返回空，由调用方停批标记（**绝不默认泰科**——历史事故：全默认 TE 导致大规模错填）
    return ""


def make_cover_local(lh, out_dir, img):
    """本地封面：图库图 → make_cover 标注（品牌动态；图自带 logo 则不叠加）"""
    try:
        tagged = os.path.join(out_dir, "%s_封面_标注.png" % lh)
        import subprocess
        mc = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\推广工具\make_cover.py"
        if not os.path.isfile(mc):
            mc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_cover.py")
        brand = get_brand(lh)
        # 图自带 logo（Delphi/APTIV/TE/MOLEX 等文字）→ 不打品牌（避免重复）
        img_text = _img_brand_text(img)
        if img_text:
            brand = ""
            print("[封面] %s 图自带品牌(%s)——封面不打品牌" % (lh, img_text))
        else:
            print("[封面] 品牌: %s" % brand)
        subprocess.run([sys.executable, mc, "--base", img, "--out", tagged,
                        "--model", lh, "--brand", brand, "--spec", "", "--sub", ""],
                       timeout=60, capture_output=True)
        return os.path.isfile(tagged)
    except Exception:
        return False


def _img_brand_text(path):
    """检测图内品牌文字（Delphi/APTIV/TE/MOLEX 等水印/logo）——无 OCR，用文件名+粗略启发"""
    name = os.path.basename(path)
    for kw in ["Delphi", "APTIV", "aptiv", "Molex", "MOLEX", "Sumitomo", "Hirose", "KET"]:
        if kw in name:
            return kw
    return ""


def process(lh, force=False):
    """本地修图主流程：评分选图 → 封面/主图 裁剪缩放 → 详情/视频（不做本地白底化——白底交 AI）"""
    lh_safe = lh.replace("/", "-")  # 料号带 / 时产物命名用安全名（防路径错乱）
    out = os.path.join(PRODUCT_BASE, lh)
    os.makedirs(out, exist_ok=True)
    # 已生成且不强制 → 跳过
    if not force:
        need = ["%s_封面.png" % lh_safe, "%s_主图2.png" % lh_safe]
        if all(os.path.isfile(os.path.join(out, f)) for f in need):
            print("[本地修图] %s 已生成——跳过" % lh)
            return True
    imgs = pick_best(lh)
    if not imgs:
        # 有低分图（<门槛）但没高分——标记待人工确认（用户看过图可行才 force 上）
        low = _has_low_score(lh)
        if low:
            if force:
                # force（用户已确认）→ 用低分图生成（不是缺图——只是加 logo/广告扣分）
                try:
                    from 素材_prep import scan_other_folders
                    import train_selector as _ts
                    found, src = scan_other_folders(lh)
                    # 素材目录已有图也收（用户下载/复制的——不在图库但确认可用）
                    mat_d = os.path.join(PRODUCT_BASE, lh)
                    if os.path.isdir(mat_d):
                        for f in sorted(os.listdir(mat_d)):
                            if not f.lower().endswith((".jpg", ".jpeg", ".png")):
                                continue
                            if "_封面" in f or "_主图" in f or "_白底" in f or "_详情" in f:
                                continue
                            fp2 = os.path.join(mat_d, f)
                            if fp2 not in found:
                                found.append(fp2)
                    scored2 = []
                    for fp in found:
                        try:
                            # force（用户确认）→ 低分图可用，但标签图（纯标签/铭牌）不当主图
                            if _ts.is_label_like(fp):
                                continue
                            s = _ts.score_image(fp)
                            if s[0] >= 25:
                                scored2.append((fp, s[0]))
                        except Exception:
                            continue
                    scored2.sort(key=lambda x: x[1], reverse=True)
                    imgs = [p for p, _ in scored2[:5]]
                except Exception:
                    pass
            if not imgs:
                # 铁律（梓帆 0923）：低分图必须人看，**不许自动降级挑次一档先用**——
                # 自动降级会把差图（水印/货不对板）放出去，那正是人工审核存在的意义。
                # 往下走两条路：① 确认图可用 → 勾「强制重做」用它；② 去审核标「换源/送修」。
                print("[本地修图] %s 图库有低分图（%d 分）——需人工确认（广告/logo/排版被扣分——确认可用就 force 上）" % (lh, low))
                try:
                    import issue_log
                    issue_log.add_issue(lh, "素材待确认", "图库有低分图（%d分）——广告/logo/排版扣分——人工确认可用后 force 上" % low)
                except Exception:
                    pass
                return False
        else:
            print("[本地修图] %s 图库无可用图（全 <30 废图）——需 AI 兜底" % lh)
            return False
    print("[本地修图] %s 图库选 %d 张（最高 %.0f 分）" % (lh, len(imgs), ts_score(imgs[0])))
    # 品牌一致性验证：图来源 vs 表格标题品牌——不匹配标记（防贴错）
    try:
        brand = get_brand(lh)
        # 品牌检测已移除（用户决定：品牌只看 map 权威——图源路径无可靠品牌，不瞎判）
        # 千金目录按品牌分（TE/APTIV(Delphi) 等）——仅供 map 缺失时兜底（get_brand 内处理）
    except Exception:
        pass
    # ① 封面（第一张高分图居中裁剪 1:1——仅作详情图 HTML 渲染基础，不上主图）
    # 封面_标注已退役：营销角标干扰白底图生成/主图质量——主图1 直接用 AI 白底图
    cover_src = imgs[0]
    cover_base = os.path.join(out, "%s_封面.png" % lh_safe)
    try:
        from PIL import Image
        im = Image.open(cover_src).convert("RGB")
        w, h = im.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        im = im.crop((left, top, left + side, top + side))
        im = im.resize((1200, 1200), Image.LANCZOS)
        im.save(cover_base)
    except Exception:
        shutil.copy(cover_src, cover_base)
    # ② 主图2-5（图库多角度直接落位——居中裁剪 1:1 再缩放，不拉伸变形）
    for i, img in enumerate(imgs[1:5], 2):
        dst = os.path.join(out, "%s_主图%d.png" % (lh_safe, i))
        try:
            from PIL import Image
            im = Image.open(img).convert("RGB")
            w, h = im.size
            side = min(w, h)
            left, top = (w - side) // 2, (h - side) // 2
            im = im.crop((left, top, left + side, top + side))  # 居中裁剪 1:1
            im = im.resize((800, 800), Image.LANCZOS)
            im.save(dst)
        except Exception:
            shutil.copy(img, dst)
    print("[本地修图] %s 完成：封面(基础) + 主图2-%d（主图1 用白底图）" % (lh, min(5, len(imgs))))
    # ③ 详情图：优先 pipeline 四类生成器（PPTC/电位器/TVS/TBU 按品类分派），回退旧连接器版式
    try:
        import subprocess
        _dp = _pick_detail_generator(lh)
        if _dp:
            subprocess.run([sys.executable, _dp, "--only", lh], timeout=300, capture_output=True)
            if os.path.isfile(os.path.join(out, "%s_详情图.png" % lh_safe)):
                print("[本地修图] 详情图（%s）完成" % os.path.basename(_dp))
            else:
                print("[本地修图] 详情图未产出（%s 抽不到参数，看它自己的输出）" % os.path.basename(_dp))
        else:
            dh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "detail_html.py")
            if os.path.isfile(dh):
                cover_png = os.path.join(out, "%s_封面.png" % lh_safe)
                subprocess.run([sys.executable, dh, lh, cover_png], timeout=90, capture_output=True)
                if os.path.isfile(os.path.join(out, "%s_详情图.png" % lh_safe)):
                    print("[本地修图] 详情图（HTML）完成")
    except Exception as e:
        print("[本地修图] 详情图失败:", str(e)[:60])
    # ④ 视频（ffmpeg——详情图+主图+拍前须知轮播）
    try:
        import subprocess
        mv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_video.py")
        if os.path.isfile(mv):
            subprocess.run([sys.executable, mv, lh], timeout=240, capture_output=True)
            if os.path.isfile(os.path.join(out, "%s_视频.mp4" % lh_safe)):
                print("[本地修图] 视频（ffmpeg）完成")
    except Exception:
        pass
    return True


def ts_score(p):
    import train_selector as ts
    try:
        return ts.score_image(p)[0]
    except Exception:
        return 0


if __name__ == "__main__":
    lh = sys.argv[1] if len(sys.argv) > 1 else "1-967640-1"
    force = "--force" in sys.argv
    # --detail-only：只重出详情图（不动封面/主图/视频）
    if "--detail-only" in sys.argv:
        import subprocess
        _dp = _pick_detail_generator(lh)
        if not _dp:
            print("[本地修图] %s 无匹配品类生成器（连接器走 detail_html）" % lh)
            sys.exit(2)
        _safe = lh.replace("/", "-")
        _outpng = os.path.join(PRODUCT_BASE, lh, "%s_详情图.png" % _safe)
        print("[本地修图] 详情图生成器: %s" % os.path.basename(_dp))
        _r = subprocess.run([sys.executable, _dp, "--only", lh], timeout=300,
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        for _ln in (_r.stdout or "").splitlines():
            if _ln.strip():
                print("    " + _ln.rstrip())
        if _r.returncode != 0:
            print("    stderr: %s" % (_r.stderr or "")[:400])
        _ok = os.path.isfile(_outpng)
        print("[本地修图] 详情图 %s: %s" % ("完成" if _ok else "未产出", _outpng))
        sys.exit(0 if _ok else 1)
    sys.exit(0 if process(lh, force) else 1)
