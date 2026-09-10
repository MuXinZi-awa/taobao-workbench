# -*- coding: utf-8 -*-
"""立创（LCSC）找图兜底：搜索料号 → 产品图 → 下载 → 水印标记
水印说明：左上角供应商 logo 保留不去；斜向半透明水印需 AI 去（提示词保丝印）
用法：python lcsc.py 料号 [输出目录]
"""
import sys
import os
import re
import time

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def await_lk_count(lk):
    try:
        return lk.count() > 0
    except Exception:
        return False


def lcsc_fetch_images(lh, max_imgs=5):
    """立创搜索料号 → 返回 (产品标题, [图片URL列表])——Playwright 真实浏览器"""
    from playwright.sync_api import sync_playwright
    urls = []
    title = ""
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="zh-CN", user_agent=UA)
        page = ctx.new_page()
        try:
            page.goto("https://www.lcsc.com/search?q=%s" % lh, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            # 产品标题
            try:
                t = page.locator("h1, [class*='product-name'], [class*='title']").first
                title = (t.inner_text() or "").strip()[:80]
            except Exception:
                pass
            # 抓所有产品图 URL（img 标签，优先大图）
            imgs = page.eval_on_selector_all("img", "els => els.map(e => e.src).filter(s => s && (s.includes('http') || s.startsWith('//')) && !s.includes('logo'))")
            seen = set()
            for s in imgs:
                s = s if s.startswith("http") else "https:" + s
                if s not in seen and not s.lower().endswith((".svg", ".gif")):
                    seen.add(s)
                    urls.append(s)
        except Exception as e:
            print("立创搜索异常: %s" % str(e)[:80])
        finally:
            b.close()
    return title, urls[:max_imgs]


def download(url, dest, timeout=30):
    """下载图片（立创国内——直连，不走代理）"""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.lcsc.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if len(data) < 2000:
        return False
    with open(dest, "wb") as f:
        f.write(data)
    return True


def lcsc_fetch_images(lh, outdir, max_imgs=5):
    """立创搜索料号 → 下载产品图 → 返回 (标题, 下载成功数)
    下载用浏览器会话（带 cookie——绕过 CDN 防盗链）
    """
    from playwright.sync_api import sync_playwright
    ok_cnt = 0
    title = ""
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="zh-CN", user_agent=UA)
        page = ctx.new_page()
        try:
            # 0901：中文立创 so.szlcsc.com（国际站 www.lcsc.com 搜索第一个是分类推荐——货不对板实锤）
            page.goto("https://so.szlcsc.com/global.html?k=%s" % lh, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            try:
                t = page.locator("h1, [class*='product-name'], [class*='title']").first
                title = (t.inner_text() or "").strip()[:80]
            except Exception:
                pass
            # 精确匹配：行文本含料号的 item 链接（不是第一个推荐）
            try:
                item_url = page.evaluate("""(k) => {
                    const links = Array.from(document.querySelectorAll('a[href*="item.szlcsc.com"]'));
                    // 优先标准 item.szlcsc.com/{数字}.html（排除 details/ 合作库存页——无产品主图）
                    const norm = links.filter(a => !a.href.includes('/details/') && /item\.szlcsc\.com\/\d+/.test(a.href));
                    const cands = norm.length ? norm : links;
                    for (const a of cands) {
                        const row = a.closest('tr') || a.closest('li') || a.parentElement;
                        const txt = row ? (row.innerText || '') : (a.innerText || '');
                        if (txt.indexOf(k) !== -1) return a.href.split('?')[0];
                    }
                    return '';
                }""", lh)
                if item_url:
                    page.goto(item_url, timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_timeout(6000)  # 0901：等主图加载（5000 不够——懒加载）
                    try:
                        t = page.locator("h1, [class*='product-name'], [class*='goods-name'], [class*='title']").first
                        title = (t.inner_text() or "").strip()[:80]
                    except Exception:
                        pass
            except Exception:
                pass
            # 0901：只保留产品主图（alimg.szlcsc.com/.../product/source/——evaluate 方式——探测验证有效）
            imgs = page.evaluate("""() => {
                const out = [];
                document.querySelectorAll('img').forEach(e => {
                    const s = e.src || e.getAttribute('data-src') || '';
                    if (s.indexOf('/product/source/') !== -1) out.push(s);
                });
                return out;
            }""")
            print("IMG URLS:")
            for s in imgs[:8]:
                print("  ", s[:130])
            seen = set()
            for s in imgs:
                if not s.startswith("http"):
                    s = "https:" + s
                if s in seen:
                    continue
                seen.add(s)
                try:
                    resp = ctx.request.get(s, timeout=30000)
                    if resp.ok:
                        data = resp.body()
                        if len(data) > 50000:  # 0901：只保留大图（>50KB——缩略图太糊）
                            dest = os.path.join(outdir, "%s_lc%d.jpg" % (lh, ok_cnt + 1))
                            with open(dest, "wb") as f:
                                f.write(data)
                            ok_cnt += 1
                            print("  ✓ %s (%dKB)" % (os.path.basename(dest), len(data) // 1024))
                            if ok_cnt >= max_imgs:
                                break
                except Exception as e:
                    print("  ✗ %s" % str(e)[:50])
        except Exception as e:
            print("立创搜索异常: %s" % str(e)[:80])
        finally:
            b.close()
    return title, ok_cnt


def lcsc_search_parse(lh, outdir, max_imgs=3):
    """立创搜索页 HTML 解析（schema.org 数据——产品名/图片/详情链接）
    打开 so.szlcsc.com/global.html?k=料号 → 正则提取 → 下载图片
    """
    from playwright.sync_api import sync_playwright
    ok_cnt = 0
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="zh-CN", user_agent=UA)
        page = ctx.new_page()
        try:
            page.goto("https://so.szlcsc.com/global.html?k=%s" % lh, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            # 模拟输入搜索（URL 带 k 不触发 Next.js 搜索——要输入框输入+回车）
            try:
                inputs = page.eval_on_selector_all("input", "els => els.map(e => ({ph: e.placeholder||'', tp: e.type, id: e.id||'', vis: !!e.offsetParent})).filter(e => e.vis)")
                print("可见输入框:", inputs[:6])
                inp = page.locator("#global-seach-input, input[placeholder*='搜索'], input[type='search'], input[class*='search'], input[placeholder*='关键词']").first
                await_inp = inp.count()
                if await_inp > 0:
                    inp.click()
                    inp.fill("")
                    inp.type(lh, delay=60)  # 逐字（fill 不触发框架事件）
                    inp.press("Enter")
                    try:  # 再点搜索按钮（双保险）
                        btn = page.locator("button:has-text('搜索'), [class*='search'] button, [class*='Search'] button").first
                        if btn.count() > 0:
                            btn.click()
                    except Exception:
                        pass
                    print("已模拟输入搜索(type+Enter+按钮)")
            except Exception as e:
                print("输入搜索异常: %s" % str(e)[:50])
            page.wait_for_timeout(6000)  # 等 JS 渲染搜索结果
            try:
                page.wait_for_selector("a[href*='item.szlcsc.com']", timeout=15000)
            except Exception:
                pass
            # 渲染后用 locator 拿产品链接（真结果——不再用初始 HTML）
            links = []
            for a in page.locator("a[href*='item.szlcsc.com']").all()[:10]:
                try:
                    href = a.get_attribute("href") or ""
                    if "item.szlcsc.com" in href and href not in links:
                        links.append(href)
                except Exception:
                    continue
            # 产品名/图片（渲染后的卡片文本）
            names = []
            try:
                names = page.locator("[class*='product-name'], [class*='ProductName'], [class*='title']").all_inner_texts()[:8]
            except Exception:
                pass
            print("渲染后链接:", [l[:60] for l in links[:3]])
            print("渲染后产品名:", names[:5])
            # 命中检查：优先含料号的链接/名称
            hit_link = ""
            norm = lh.replace("-", "").replace("_", "").lower()
            for l in links:
                pass  # 链接不含料号文本，交给详情页验证
            if links:
                hit_link = links[0]
            return hit_link, 0
        except Exception as e:
            print("搜索页解析异常: %s" % str(e)[:80])
            return "", ok_cnt
        finally:
            b.close()


def title_match(title, lh):
    """严格匹配：料号完整独立出现（前后不能跟数字/字母——防 1705549-10 误判为 1705549-1）"""
    import re
    norm_t = title.replace("-", "").replace("_", "").replace(" ", "").lower()
    norm_l = lh.replace("-", "").replace("_", "").replace(" ", "").lower()
    return re.search(r"(?<![a-z0-9])" + re.escape(norm_l) + r"(?![a-z0-9])", norm_t) is not None


def lcsc_fetch_detail(url, outdir, lh, max_imgs=5):
    """立创详情页主图抓取（URL 已知）——下载后过质检（标签检测+评分）"""
    from playwright.sync_api import sync_playwright
    from score_images import score_image
    from train_selector import is_label_like
    ok_cnt = 0
    title = ""
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="zh-CN", user_agent=UA)
        page = ctx.new_page()
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            try:
                t = page.locator("h1").first
                title = (t.inner_text() or "").strip()[:80]
            except Exception:
                pass
            # 详情页主图：只抓产品图（source 大图优先，跳过 advertising/sch/pcb/breviary 小图）
            imgs = page.eval_on_selector_all("img", "els => els.map(e => e.src || e.getAttribute('data-src')).filter(s => s && s.includes('alimg.szlcsc.com') && s.includes('/product/source/'))")
            if not imgs:  # 兜底：breviary 图（较小但有）
                imgs = page.eval_on_selector_all("img", "els => els.map(e => e.src || e.getAttribute('data-src')).filter(s => s && s.includes('alimg.szlcsc.com') && s.includes('/product/breviary/'))")
            print("DETAIL IMG URLS:")
            seen = set()
            for s in imgs:
                if s in seen:
                    continue
                seen.add(s)
                print("  ", s[:120])
                try:
                    resp = ctx.request.get(s, timeout=30000)
                    if resp.ok:
                        data = resp.body()
                        if len(data) > 20000:  # 大图（跳过小图标）
                            dest = os.path.join(outdir, "%s_lc%d.jpg" % (lh, ok_cnt + 1))
                            with open(dest, "wb") as f:
                                f.write(data)
                            ok_cnt += 1
                            # 质检：立创图有水印（斜向文字）——标签检测会误判，不删；只评分标记
                            try:
                                sc = score_image(dest)[0]
                                if sc < 50:
                                    print("  ✓ %s (%dKB) 但分低(%.1f)——标记需人工确认" % (os.path.basename(dest), len(data) // 1024, sc))
                                else:
                                    print("  ✓ %s (%dKB) 评分通过(%.1f)" % (os.path.basename(dest), len(data) // 1024, sc))
                            except Exception:
                                print("  ✓ %s (%dKB) 质检跳过" % (os.path.basename(dest), len(data) // 1024))
                            if ok_cnt >= max_imgs:
                                break
                except Exception as e:
                    print("  ✗ %s" % str(e)[:40])
        except Exception as e:
            print("详情页异常: %s" % str(e)[:80])
        finally:
            b.close()
    return title, ok_cnt


def search_engine_url(lh):
    """搜索引擎找立创详情页 URL：bing/百度搜 site:item.szlcsc.com {料号}
    返回 https://item.szlcsc.com/{ID}.html 或空
    """
    import urllib.request
    import urllib.parse
    qu = urllib.parse.quote("site:item.szlcsc.com %s" % lh)
    for engine in [
        "https://cn.bing.com/search?q=%s" % qu,
        "https://www.bing.com/search?q=%s" % qu,
        "https://www.baidu.com/s?wd=%s" % urllib.parse.quote("site:item.szlcsc.com %s" % lh),
    ]:
        try:
            req = urllib.request.Request(engine, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                html = r.read().decode("utf-8", errors="replace")
            m = re.search(r"https?://item\.szlcsc\.com/(\d+)\.html", html)
            if m:
                u = "https://item.szlcsc.com/%s.html" % m.group(1)
                print("搜索引擎命中[%s]: %s" % (engine.split("/")[2], u))
                return u
        except Exception as e:
            continue
    return ""


def find_lcsc_url(lh):
    """找料号的立创详情页 URL：映射表缓存优先 → 搜索引擎自动找（找到回写缓存）"""
    import csv
    mp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "立创映射.csv")
    if os.path.isfile(mp):
        with open(mp, encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                if row and row[0].strip() == lh and len(row) > 1 and row[1].strip():
                    return row[1].strip()
    u = search_engine_url(lh)
    if u:  # 回写缓存
        try:
            with open(mp, "a", encoding="utf-8-sig", newline="") as f:
                f.write("%s,%s\n" % (lh, u))
        except Exception:
            pass
    return u


def main():
    args = sys.argv[1:]
    lh = "1-967628-1"
    outdir = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\产品\%s\立创" % lh
    url = None
    i = 0
    while i < len(args):
        if args[i] == "--url":
            url = args[i + 1]
            i += 2
        elif args[i] == "--out":
            outdir = args[i + 1]
            i += 2
        else:
            lh = args[i]
            i += 1
    outdir = outdir.replace("%s", lh)
    os.makedirs(outdir, exist_ok=True)
    # 优先映射表拿 URL（避免搜索触发人机验证）
    if not url:
        url = find_lcsc_url(lh)
        if url:
            print("映射表命中: %s" % url[:70])
    if url:
        print("立创详情页抓图 %s ..." % lh)
        title, ok = lcsc_fetch_detail(url, outdir, lh)
        # 标题严格匹配验证（料号完整独立出现）——不过则丢弃
        if ok and not title_match(title, lh):
            print("⚠️ 标题[%s]未严格匹配料号[%s]——丢弃" % (title, lh))
            for f in os.listdir(outdir):
                if f.startswith(lh + "_"):
                    os.remove(os.path.join(outdir, f))
            ok = 0
            title = "命中失败(标题不匹配)"
    else:
        print("⚠️ 立创未找到 %s（映射表+搜索引擎均未命中）" % lh)
        title, ok = "立创未找到", 0
    print("标题:", title)
    print("完成: %d 张 -> %s" % (ok, outdir))
    if ok:
        print("⚠️ 立创图需 AI 去斜向水印（提示词必须强调保持丝印/产品细节不变）")


if __name__ == "__main__":
    main()
