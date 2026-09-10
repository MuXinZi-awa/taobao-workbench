# -*- coding: utf-8 -*-
"""TE 官网产品图抓取（0905）：官方高清图——干净无水印（官方 logo 不算），直接当素材
用法：python _grab_te_img.py 料号1 料号2 ... [--out 目录]
每个品：product-{料号}.html（中文站）→ high-res 产品图 → 下载 {料号}_封面.png / _主图2.png
防货不对板：h1 标题必须含料号（去连字符比对）——404/错页不下载
依赖：系统 Chrome（真实浏览器——urllib 被 TE TLS 反爬拒）
"""
import sys, os, re
sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
DEFAULT_OUT = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\素材\产品素材"


def _norm(s):
    return s.replace("-", "").replace("_", "").replace(" ", "").lower()


def te_fetch_images(lh, outdir):
    """TE 官网抓图 → 下载封面/主图2 → 返回下载张数（货不对板返回 0）"""
    url = "https://www.te.com.cn/chn-zh/product-%s.html" % lh
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="zh-CN", user_agent=UA)
        page = ctx.new_page()
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            # 标题验证（防 404/错页）
            try:
                t = (page.locator("h1").first.inner_text() or "").strip()
            except Exception:
                t = ""
            if not t or _norm(t) != _norm(lh):
                print("[TE图] ⚠ %s 标题[%s]不匹配——跳过（404/错页）" % (lh, t[:40]))
                return 0
            # high-res 产品图（t1/t2…）
            imgs = page.eval_on_selector_all("img", "els => els.map(e => e.src).filter(s => s && s.includes('/content/dam/te-com/catalog/part/') && s.includes('high-res'))")
            if not imgs:
                imgs = page.eval_on_selector_all("img", "els => els.map(e => e.src).filter(s => s && s.includes('/content/dam/te-com/catalog/part/'))")
            os.makedirs(outdir, exist_ok=True)
            ok = 0
            for i, u in enumerate(imgs[:2]):
                if not u.startswith("http"):
                    u = "https:" + u
                resp = ctx.request.get(u, timeout=30000)
                if not resp.ok:
                    continue
                data = resp.body()
                if len(data) < 10000:
                    print("[TE图]   %s 过小(%dB)——跳过" % (lh, len(data)))
                    continue
                name = "%s_封面.png" % lh if i == 0 else "%s_主图2.png" % lh
                dst = os.path.join(outdir, name)
                with open(dst, "wb") as f:
                    f.write(data)
                print("[TE图]   ✓ %s (%dKB)" % (name, len(data) // 1024))
                ok += 1
            return ok
        except Exception as e:
            print("[TE图] %s 异常: %s" % (lh, str(e)[:80]))
            return 0
        finally:
            b.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    lhs = [a for a in args if not a.startswith("--")]
    out = DEFAULT_OUT
    if "--out" in args:
        out = args[args.index("--out") + 1]
    for lh in lhs:
        d = os.path.join(out, lh)
        n = te_fetch_images(lh, d)
        print("%s: TE官网图 %d 张 -> %s" % (lh, n, d))
