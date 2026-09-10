# -*- coding: utf-8 -*-
"""RenhotecRF 抓图（renhotecrf.com——RF 连接器厂商，反爬弱，urllib 直抓）
搜索 ?s=料号&post_type=product → 产品页 → 产品主图（wp-content/uploads，排除缩略）
用法：python renhotec.py 料号 [输出目录]
"""
import sys, os, re, urllib.request, urllib.parse, json, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
BASE = "https://www.renhotecrf.com"

def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def norm(s):
    return re.sub(r"[\-_/\s]+", "", (s or "")).lower()

def find_product(lh):
    """搜索料号 → 产品页 URL（标题或 slug 归一化含料号——防货不对板）+ 标题"""
    q = urllib.parse.quote(lh)
    try:
        html = _get("%s/?s=%s&post_type=product" % (BASE, q))
    except Exception:
        return "", ""
    # 产品链接
    links = re.findall(r'href="(https://www\.renhotecrf\.com/product/[^"#?]+)"', html)
    seen, cands = set(), []
    for u in links:
        if u in seen:
            continue
        seen.add(u)
        cands.append(u)
    nk = norm(lh)
    for u in cands:
        slug = urllib.parse.unquote(u.rsplit("/", 1)[-1])
        if nk and nk in norm(slug):
            return u, slug
    # 二次：取第一个结果（标题可能不含料号——返回供人工判断）
    if cands:
        return cands[0], urllib.parse.unquote(cands[0].rsplit("/", 1)[-1])
    return "", ""

def fetch_drawing(url, outdir):
    """产品页 → 抓 Drawing PDF（=规格书，含电气/机械参数）→ 下载返回路径"""
    os.makedirs(outdir, exist_ok=True)
    try:
        html = _get(url)
    except Exception:
        return ""
    m = re.search(r'href="(https://www\.renhotecrf\.com/wp-content/uploads/[^"]*[Dd]rawing[^"]*\.pdf)', html)
    if not m:
        return ""
    u = m.group(1)
    dest = os.path.join(outdir, "drawing.pdf")
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA, "Referer": url})
        with urllib.request.urlopen(req, timeout=40) as r, open(dest, "wb") as f:
            f.write(r.read())
        if os.path.getsize(dest) > 5000:
            return dest
    except Exception:
        pass
    return ""


def fetch_images(url, outdir, max_imgs=3):
    """产品页 → 下载主图（wp-content/uploads，排除 logo/icon/缩略图）"""
    os.makedirs(outdir, exist_ok=True)
    try:
        html = _get(url)
    except Exception as e:
        return 0
    # 主图区图片（woocommerce-product-gallery / wp-post-image）
    urls = []
    for m in re.finditer(r'(?:data-large_image|data-src|src)="(https://www\.renhotecrf\.com/wp-content/uploads/[^"]+\.(?:jpg|jpeg|png|webp))"', html):
        u = m.group(1)
        if re.search(r"-\d{2,4}x\d{2,4}\.(jpg|jpeg|png|webp)$", u):  # 缩略
            continue
        if any(k in u.lower() for k in ("logo", "icon", "favicon", "placeholder", "banner", "catalog", "drawing")):
            continue
        if u not in urls:
            urls.append(u)
    ok = 0
    for u in urls:
        ext = ".png" if ".png" in u.lower() else (".webp" if ".webp" in u.lower() else ".jpg")
        dest = os.path.join(outdir, "renhotec_%d%s" % (ok + 1, ext))
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Referer": url})
            with urllib.request.urlopen(req, timeout=40) as r, open(dest, "wb") as f:
                f.write(r.read())
            if os.path.getsize(dest) > 8000:
                ok += 1
            else:
                os.remove(dest)
        except Exception:
            continue
        if ok >= max_imgs:
            break
    return ok

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    lh = sys.argv[1] if len(sys.argv) > 1 else "RHT-639-7101"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "rh_tmp")
    u, slug = find_product(lh)
    print("产品页:", u or "(无)")
    if u:
        n = fetch_images(u, out, 3)
        print("下载:", n)
        for f in sorted(os.listdir(out)):
            print("   ", f, os.path.getsize(os.path.join(out, f)) // 1024, "KB")
