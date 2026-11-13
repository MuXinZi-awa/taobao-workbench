# -*- coding: utf-8 -*-
"""上品预演：填到提交前停住，不点提交
复用 publish_auto 的 fill_titles / upload_files / upload_detail / upload_spec（不重写上传逻辑）
"""
import sys, os, io, json, csv, datetime

TG = _TG
ENG = _ENG
MAT = _MAT
sys.path.insert(0, TG)
sys.path.insert(0, os.path.join(TG, "runtime", "Lib", "site-packages"))

import publish_auto as pa
import acct_profile
import browser_lock
from playwright.sync_api import sync_playwright

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



LH = sys.argv[1] if len(sys.argv) > 1 else "MF-MSMF050-2"
KW_CAT, KW_BRAND = "保险丝", "BOURNS"
OUT = os.path.join(ENG, "preflight", datetime.datetime.now().strftime("%m%d_%H%M") + "_" + LH.replace("/", "-"))
os.makedirs(OUT, exist_ok=True)
PROF = acct_profile.resolve()[0]

# ===== 0. 查重（必须在拿浏览器锁之前——MtopClient 自己要起浏览器）=====
# 淘宝同款重复上架会降权。宁可漏发，不可重发：
#   · 查到精确命中 → 直接退出，不发布
#   · 查不到 → 继续
#   · 查重本身出错 → 中止（“查不到”不等于“没有”）
TRIAGE = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\工具\workbench\plugins\pipeline\scripts"
sys.path.insert(0, TRIAGE)


def _is_ascii_alnum(c):
    return bool(c) and c.isascii() and c.isalnum()


def exact_in(lh, title):
    """料号是否作为**独立标识**出现在标题里（同 _classify_one._exact_in）。
    不能拿子串判：W3S 会命中 W3S-1939-P012 和 AW3S。"""
    import re as _re
    t = title or ""
    if not lh or not t:
        return False
    pat = _re.escape(lh) + r"(?![\-_][A-Za-z0-9])"
    for m in _re.finditer(pat, t, _re.I):
        s = m.start()
        if s > 0 and _is_ascii_alnum(t[s - 1]):
            continue
        return True
    return False


def check_already_listed(lh):
    """返回 (是否已上架, 商品信息, 错误信息)"""
    try:
        from _mtop_api import MtopClient
    except Exception as e:
        return False, None, "导入 MtopClient 失败: %s" % str(e)[:80]
    mc = None
    try:
        mc = MtopClient()
        mc.open(headless=True)
        rows, _st = mc.search_and_dual(lh)
        for r in (rows or []):
            if exact_in(lh, str(r.get("title", ""))):
                return True, r, None
        return False, None, None
    except Exception as e:
        return False, None, str(e)[:120]
    finally:
        if mc is not None:
            try:
                mc.close()
            except Exception:
                pass


SKIP_CHECK = os.environ.get("WB_SKIP_CHECK") == "1"
if SKIP_CHECK:
    # 分流表已确认线上没有，跳过实时查重（查重接口偶发卡死会拖死整批）
    print("[查重] 已按 WB_SKIP_CHECK=1 跳过（依据：results_shop2.csv 已确认线上无此料号）")
    already, hit, err = False, None, None
else:
    already, hit, err = check_already_listed(LH)
if err:
    print("[查重] ✗ 查重未完成: %s" % err)
    print("[查重] ⚠ 查不到不等于没有——为防止重复上架，本次中止")
    sys.exit(2)
if already:
    print("[查重] ✗ 线上已有该料号，跳过（不重复发布）")
    print("       商品ID : %s" % hit.get("itemId"))
    print("       标题   : %s" % str(hit.get("title", ""))[:56])
    print("       状态   : %s" % (hit.get("scoreLabel") or "—"))
    sys.exit(0)
print("[查重] ✓ 线上没有 %s，继续发布" % LH)

# 标题 / 售价从清单读（不在脚本里写死）
titles = {}
tp = os.path.join(ENG, "标题_v1.csv")
if os.path.isfile(tp):
    with io.open(tp, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            titles[r["料号"]] = r
t = titles.get(LH)
if not t:
    print("✗ 标题表里没有 %s" % LH); sys.exit(1)
BT, GT, PRICE = t["商品标题"], t["导购标题"], t["售价"]
print("[准备] %s  售价=%s" % (LH, PRICE))
print("[标题]", BT)

s = LH.replace("/", "-")
cover = os.path.join(MAT, s, "%s_封面.jpg" % s)
detail = os.path.join(MAT, s, "%s_详情图.png" % s)
spec = os.path.join(MAT, s, "规格书.pdf")
for label, p in (("封面", cover), ("详情图", detail), ("规格书", spec)):
    print("[素材] %-4s %s %s" % (label, "✓" if os.path.isfile(p) else "✗", os.path.basename(p)))

with browser_lock.guard("上品预演", timeout=1200), sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROF, executable_path=pa.CHROME, headless=False,
        viewport={"width": 1600, "height": 1000}, locale="zh-CN",
        ignore_default_args=["--password-store=basic", "--use-mock-keychain"],
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(pa.SELL_URL, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    pa.login_qianniu(page)

    # ---- 发布商品 → 类目 → 品牌 → 型号 → 确认 ----
    cands = []
    for sel in ("span.next-btn-helper:has-text('发布商品')", "a:has-text('发布商品')",
                "button:has-text('发布商品')", "[class*=btn]:has-text('发布商品')"):
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 4)):
                e = loc.nth(i)
                if e.is_visible():
                    cands.append(e)
        except Exception:
            continue
    pg = None
    for b in cands:
        try:
            with page.context.expect_page(timeout=9000) as info:
                b.click()
            pg = info.value
            break
        except Exception:
            continue
    if pg is None:
        print("✗ 发布商品点不开"); ctx.close(); sys.exit(1)
    pg.wait_for_load_state("domcontentloaded"); pg.wait_for_timeout(2500)

    # 新版「AI 发品」引导页（item.upload.taobao.com/sell/ai/category.htm）：
    # 必须先点左栏「搜索发品 → 开启」，才会出现类目搜索框。
    if "ai/category" in pg.url:
        opened = pg.evaluate(r"""() => {
            const btns = [...document.querySelectorAll('button, span, a, div')]
                .filter(e => (e.textContent || '').replace(/\s+/g, '') === '开启')
                .filter(e => { const r = e.getBoundingClientRect(); return r.width > 10 && r.height > 10; });
            if (!btns.length) return 'no-button';
            btns.sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
            const b = btns[0];
            b.scrollIntoView({block: 'center'});
            b.click();
            return 'clicked-left(' + Math.round(b.getBoundingClientRect().x) + ')';
        }""")
        print("[步骤] AI 引导页 → 搜索发品·开启:", opened)
        pg.wait_for_timeout(3500)

    # 类目输入框：不同店铺/版本 placeholder 不一样，先扫一遍实际有什么
    SEL = "input[placeholder*='类目关键词'], input[placeholder*='商品名称'], input[placeholder*='条码'], input[placeholder*='关键词'], input[placeholder*='搜索']"
    if pg.locator(SEL).count() == 0:
        pg.screenshot(path=os.path.join(OUT, "0_no_catbox.png"), full_page=True)
        info = pg.evaluate(r"""() => {
            const ins = [...document.querySelectorAll('input')].filter(e => e.offsetParent !== null);
            return ins.slice(0, 20).map(e => ({ph: e.placeholder || '', type: e.type || '', cls: (e.className||'').slice(0,40)}));
        }""")
        print("[诊断] 当前页 URL:", pg.url[:110])
        print("[诊断] 可见 input 共 %d 个:" % len(info))
        for i in info:
            print("    ph=%-24r type=%-8s cls=%s" % (i["ph"], i["type"], i["cls"]))
        body = pg.evaluate("() => (document.body.innerText||'').slice(0,300)") or ''
        print("[诊断] 正文前 300:", body.replace(chr(10), ' | ')[:300])
        print("✗ 找不到类目输入框，已截图 %s" % OUT)
        ctx.close(); sys.exit(3)
    inp = pg.locator(SEL).first
    try:
        x = pg.locator("span.next-btn-helper:has-text('选择类目'), button:has-text('选择类目')").first
        if x.count() > 0:
            x.click(); pg.wait_for_timeout(800)
    except Exception:
        pass
    inp.fill(KW_CAT); pg.wait_for_timeout(500)
    pg.locator("span.next-btn-helper:has-text('搜索'), button:has-text('搜索')").first.click()
    pg.wait_for_timeout(3500)

    box = pg.evaluate(r"""() => {
        const norm = s => (s || '').replace(/\s+/g, '');
        let best = null, area = 1e9;
        for (const e of document.querySelectorAll('.path-text, a, span, div')) {
            const t = norm(e.textContent);
            if (!t.startsWith('电子元器件市场') || !t.includes('熔丝/保险丝座') || t.includes('电动车')) continue;
            const r = e.getBoundingClientRect();
            if (r.width < 5 || r.height < 5) continue;
            const a = r.width * r.height;
            if (a < area) { area = a; best = r; }
        }
        return best ? {x: best.x + best.width / 2, y: best.y + best.height / 2} : null;
    }""")
    pg.mouse.click(box["x"], box["y"]); pg.wait_for_timeout(2500)
    print("[步骤] 类目已选")

    el = None
    loc = pg.locator('input[placeholder="请输入"], input[placeholder*="请输入"]')
    for i in range(min(loc.count(), 6)):
        e = loc.nth(i)
        try:
            if e.is_visible():
                el = e; break
        except Exception:
            continue
    if el is None:
        print("✗ 品牌框找不到"); ctx.close(); sys.exit(1)
    el.click(); el.fill(KW_BRAND); pg.wait_for_timeout(300); el.press("Enter")
    pg.wait_for_timeout(2200)
    hit = pg.evaluate(r"""(kw) => {
        const ins = [...document.querySelectorAll('input')].filter(e => e.offsetParent !== null);
        const a = ins.find(e => e.placeholder === '请输入');
        if (!a) return null;
        const r0 = a.getBoundingClientRect();
        let h = null;
        for (const e of document.querySelectorAll('*')) {
            if (e.children.length) continue;
            const t = (e.textContent || '').replace(/\s+/g, ' ').trim();
            if (t !== kw) continue;
            const r = e.getBoundingClientRect();
            if (r.width < 10 || r.height < 8) continue;
            if (r.top > r0.bottom - 12 && r.top < r0.bottom + 560 &&
                r.left > r0.left - 40 && r.left < r0.right + 300)
                h = {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }
        return h;
    }""", KW_BRAND)
    if hit:
        pg.mouse.click(hit["x"], hit["y"])
    pg.wait_for_timeout(1500)
    print("[步骤] 品牌 BOURNS 已选")

    mbox = pg.evaluate(r"""() => {
        const ins = [...document.querySelectorAll('input')]
            .filter(e => e.offsetParent !== null && (e.placeholder || '').indexOf('请输入') >= 0);
        if (!ins.length) return null;
        ins.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
        const r = ins[0].getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    }""")
    if mbox:
        pg.mouse.click(mbox["x"], mbox["y"]); pg.keyboard.press("Control+A"); pg.keyboard.type(LH, delay=40)
    pg.wait_for_timeout(1200)

    btn = pg.evaluate(r"""() => {
        const b = [...document.querySelectorAll('button, a, span')]
            .find(e => (e.textContent || '').replace(/\s+/g, '') === '确认，下一步' && e.offsetParent !== null);
        if (!b) return null;
        const r = b.getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    }""")
    if btn:
        pg.mouse.click(btn["x"], btn["y"])
    pg.wait_for_timeout(12000)
    print("[步骤] 已进发布页:", pg.url[:100])
    pg.screenshot(path=os.path.join(OUT, "1_publish_page.png"), full_page=True)

    # ---- 标题 ----
    try:
        pa.fill_titles(pg, BT, GT)
        print("[步骤] 标题已填")
    except Exception as e:
        print("✗ 标题填充失败:", str(e)[:80])
    pg.wait_for_timeout(1200)

    # ---- 主图（封面）----
    try:
        n = pa.upload_files(pg, [cover])
        print("[步骤] 主图上传返回:", n)
    except Exception as e:
        print("✗ 主图上传失败:", str(e)[:100])
    pg.wait_for_timeout(3000)
    pg.screenshot(path=os.path.join(OUT, "2_after_cover.png"), full_page=True)

    # ---- 一口价 ----
    try:
        done = pg.evaluate(r"""(price) => {
            const clean = s => (s || '').replace(/\s+/g, '');
            // 找 label 文本含「一口价」的行，取其 input
            let hit = null;
            for (const row of document.querySelectorAll('.sell-component-info-wrapper-wrap')) {
                const lb = row.querySelector('.sell-component-info-wrapper-label');
                if (!lb) continue;
                if (clean(lb.textContent).indexOf('一口价') >= 0) {
                    const inp = row.querySelector('input:not([type=hidden])');
                    if (inp) { hit = inp; break; }
                }
            }
            if (!hit) return false;
            hit.focus();
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(hit, price);
            hit.dispatchEvent(new Event('input', {bubbles: true}));
            hit.dispatchEvent(new Event('change', {bubbles: true}));
            hit.blur();
            return hit.value;
        }""", str(PRICE))
        print("[步骤] 一口价写入:", done)
    except Exception as e:
        print("✗ 一口价失败:", str(e)[:80])
    pg.wait_for_timeout(1200)

    # ---- 物流服务：提取方式 + 发货地（这两项不填，提交必被拦）----
    def _rowbox(name):
        return pg.evaluate("""(name) => {
            const clean = s => (s||'').replace(/\s+/g,'');
            for (const row of document.querySelectorAll('.sell-component-info-wrapper-wrap')) {
                const lb = row.querySelector('.sell-component-info-wrapper-label');
                if (lb && clean(lb.textContent).indexOf(name) >= 0) {
                    const r = row.getBoundingClientRect();
                    return {x:r.x, y:r.y, w:r.width, h:r.height};
                }
            }
            return null;
        }""", name)

    def _click_text_in(box, texts):
        return pg.evaluate("""([box, texts]) => {
            const clean = s => (s||'').replace(/\s+/g,'').trim();
            for (const t of texts) {
                for (const e of document.querySelectorAll('label, span, div, li')) {
                    if (e.children.length > 2) continue;
                    if (clean(e.textContent) !== clean(t)) continue;
                    const r = e.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) continue;
                    if (r.x < box.x - 5 || r.x > box.x + box.w + 5) continue;
                    if (r.y < box.y - 5 || r.y > box.y + box.h + 80) continue;
                    return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
            }
            return null;
        }""", [box, texts])

    # 提取方式 + 发货地：用 Playwright 直接 click 元素本身
    # 坐标点击点不中——淘宝的 input 是隐藏的（opacity:0），mouse.click 会落在空白上
    def _click_text(txt, label):
        try:
            loc = pg.get_by_text(txt, exact=True)
            n = loc.count()
            if n == 0:
                print('[物流] 找不到「%s」' % txt)
                return False
            loc.first.scroll_into_view_if_needed(timeout=8000)
            pg.wait_for_timeout(400)
            loc.first.click(timeout=8000)
            pg.wait_for_timeout(1500)
            print('[物流] %s = %s（locator click，命中%d处）' % (label, txt, n))
            return True
        except Exception as e:
            print('[物流] %s 点击异常: %s' % (label, str(e)[:70]))
            return False

    def _click_radio(txt, label, dx=-24):
        """勾选 radio/checkbox：优先原生 DOM click，失败才退回坐标点圆圈。
        淘宝 next-radio/next-checkbox 的真 input 是 opacity:0，坐标点容易落空。"""
        # ① 原生 DOM click（触发 React 的 onClick）
        try:
            r = pg.evaluate("""(txt) => {
                const want = (txt||'').replace(/\s+/g,'');
                let target = null;
                for (const e of document.querySelectorAll('label, span, div')) {
                    if (e.children.length > 2) continue;
                    if ((e.textContent||'').replace(/\s+/g,'').trim() !== want) continue;
                    target = e; break;
                }
                if (!target) return 'text-not-found';
                let box = target.closest('[class*="radio"], [class*="checkbox"], label') || target.parentElement;
                if (!box) return 'no-container';
                box.scrollIntoView({block: 'center'});
                box.click();
                return 'ok:' + (box.className || '').slice(0, 50);
            }""", txt)
            print('[物流] %s 原生click → %s' % (label, str(r)[:70]))
            if str(r).startswith('ok:'):
                pg.wait_for_timeout(1400)
                return True
        except Exception as e:
            print('[物流] %s 原生click异常: %s' % (label, str(e)[:60]))
        # ② 退回：坐标点文字左侧的圆圈
        try:
            loc = pg.get_by_text(txt, exact=True)
            if loc.count() == 0:
                print('[物流] 找不到「%s」' % txt)
                return False
            loc.first.scroll_into_view_if_needed(timeout=8000)
            pg.wait_for_timeout(500)
            bb = loc.first.bounding_box()
            if not bb:
                print('[物流] %s 文字无边界框' % label)
                return False
            x = bb['x'] + dx
            y = bb['y'] + bb['height'] / 2
            pg.mouse.click(x, y)
            pg.wait_for_timeout(1800)
            print('[物流] %s 退回点圆圈 (%.0f, %.0f)' % (label, x, y))
            return True
        except Exception as e:
            print('[物流] %s 异常: %s' % (label, str(e)[:70]))
            return False

    _click_radio('使用物流配送', '提取方式', -24)

    ok_area = _click_radio('大陆及港澳台', '发货地', -24)
    b = True
    if b:
        if ok_area:
            pg.wait_for_timeout(1200)
            trig = pg.evaluate("""() => {
                for (const e of document.querySelectorAll('[class*=select], [class*=cascader], [class*=trigger]')) {
                    const r = e.getBoundingClientRect();
                    if (r.width < 60 || r.height < 16) continue;
                    const t = (e.textContent||'').replace(/\s+/g,'');
                    if (/请选择|省份|北京|广东/.test(t) && t.length < 24)
                        return {x: r.x + 30, y: r.y + r.height/2, t: t};
                }
                return null;
            }""")
            if trig:
                # 抓到的多是「运费模板 请选择」，不是省市——不要点它，只记录
                print('[物流] 探测到控件（不点）:', trig.get('t'))
            if False:
                def _pick(names):
                    return pg.evaluate("""(names) => {
                        for (const e of document.querySelectorAll('li, [class*=menu-item], [class*=option]')) {
                            const t = (e.textContent||'').replace(/\s+/g,'');
                            if (names.indexOf(t) >= 0) {
                                const r = e.getBoundingClientRect();
                                if (r.width > 5 && r.height > 5) return {x: r.x+r.width/2, y: r.y+r.height/2};
                            }
                        }
                        return null;
                    }""", names)
                gd = _pick(['广东', '广东省'])
                if gd:
                    pg.mouse.click(gd['x'], gd['y']); pg.wait_for_timeout(1600)
                    sz = _pick(['深圳', '深圳市'])
                    if sz:
                        pg.mouse.click(sz['x'], sz['y']); pg.wait_for_timeout(800)
                        print('[物流] 省市=广东/深圳')
                    else:
                        print('[物流] 深圳未找到')
                else:
                    print('[物流] 广东未找到')
            else:
                print('[物流] 无省市下拉（可能不需要）')
        else:
            print('[物流] 发货地 未找到「大陆及港澳台」')
    else:
        print('[物流] 未找到发货地行')

    # ---- 物流重量（部分店铺/类目要求必填；子账号1 没有这一项）----
    w = pg.evaluate(r"""() => {
        for (const row of document.querySelectorAll('.sell-component-info-wrapper-wrap')) {
            const lb = row.querySelector('.sell-component-info-wrapper-label');
            if (!lb) continue;
            if ((lb.textContent || '').replace(/\s+/g, '').indexOf('物流重量') < 0) continue;
            const inps = [...row.querySelectorAll('input:not([type=hidden])')];
            const sel = row.querySelector('[class*="selection-item"]');
            return {
                visible: row.getBoundingClientRect().height > 0,
                inputs: inps.map(e => ({ph: e.placeholder || '', val: e.value || '', cls: (e.className||'').slice(0,30)})),
                hasSelect: !!sel,
                text: (row.innerText || '').replace(/\s+/g, ' ').slice(0, 120)
            };
        }
        return null;
    }""")
    print("[探测] 物流重量字段:", w)

    # 运费模板选「顺丰」——梓帆确认：顺丰不需填物流重量，中通才要。
    # 不选就会默认中通，于是被要求必填物流重量。
    try:
        tm = pg.evaluate(r"""() => {
            for (const e of document.querySelectorAll('[class*="select"], [class*="trigger"], [class*="cascader"]')) {
                const r = e.getBoundingClientRect();
                if (r.width < 80 || r.height < 16) continue;
                const t = (e.textContent || '').replace(/\s+/g, '');
                if (t.indexOf('模板') >= 0 && t.indexOf('请选择') >= 0) {
                    e.scrollIntoView({block: 'center'});
                    const r2 = e.getBoundingClientRect();
                    return {x: r2.x + 30, y: r2.y + r2.height / 2};
                }
            }
            return null;
        }""")
        if tm:
            pg.mouse.click(tm['x'], tm['y'])
            pg.wait_for_timeout(1600)
            sf = pg.evaluate(r"""() => {
                for (const e of document.querySelectorAll('li, [class*="menu-item"], [class*="option"]')) {
                    const t = (e.textContent || '').replace(/\s+/g, '');
                    if (t.indexOf('顺丰') >= 0) {
                        const r = e.getBoundingClientRect();
                        if (r.width > 10 && r.height > 8) return {x: r.x + r.width / 2, y: r.y + r.height / 2, t: t.slice(0, 24)};
                    }
                }
                return null;
            }""")
            if sf:
                pg.mouse.click(sf['x'], sf['y'])
                pg.wait_for_timeout(1500)
                print("[物流] 运费模板=%s" % sf['t'])
            else:
                print("[物流] 下拉里没有顺丰——保留默认模板")
                pg.keyboard.press("Escape")
        else:
            print("[物流] 没找到运费模板下拉")
    except Exception as e:
        print("[物流] 运费模板异常:", str(e)[:80])

    # 物流重量（kg）：选了中通之类的模板才会要求。选了顺丰则不需要。
    WEIGHT = os.environ.get("WB_WEIGHT", "0.01")
    try:
        wfill = pg.evaluate(r"""(v) => {
            for (const row of document.querySelectorAll('.sell-component-info-wrapper-wrap')) {
                const lb = row.querySelector('.sell-component-info-wrapper-label');
                if (!lb) continue;
                if ((lb.textContent || '').replace(/\s+/g, '').indexOf('物流重量') < 0) continue;
                const inp = row.querySelector('input:not([type=hidden])');
                if (!inp) return 'no-input';
                inp.focus();
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(inp, v);
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                inp.blur();
                return inp.value;
            }
            return 'no-row';
        }""", WEIGHT)
        print("[物流] 物流重量写入 %s kg → %s" % (WEIGHT, wfill))
    except Exception as e:
        print("[物流] 物流重量异常:", str(e)[:80])
    pg.wait_for_timeout(800)

    # ---- 详情图 ----
    try:
        pa.upload_detail(pg, [detail])
        print("[步骤] 详情图已上传")
    except Exception as e:
        print("✗ 详情图失败:", str(e)[:100])
    pg.wait_for_timeout(3000)

    # ---- 素材库探针（--probe-lib）：只列清单，不选、不传、不提交 ----
    if "--probe-lib" in sys.argv:
        print("\n[探库] 打开视频素材库...")
        try:
            r = pg.evaluate(r"""() => {
                // 视频区是个带「▶ 上传视频」的方块，文字里夹了图标——用包含匹配
                for (const e of document.querySelectorAll('span, div, button, p')) {
                    const t = (e.textContent || '').replace(/\s+/g, '');
                    if (t.indexOf('上传视频') < 0 || t.length > 12) continue;
                    const rc = e.getBoundingClientRect();
                    if (rc.width < 5 || rc.height < 5) continue;
                    e.scrollIntoView({block: 'center'});
                    const r2 = e.getBoundingClientRect();
                    // 点它的矩形中心（比 e.click() 更接近真实鼠标，能触发 iframe 弹层）
                    return {x: r2.x + r2.width / 2, y: r2.y + r2.height / 2, t: t};
                }
                return null;
            }""")
            print("[探库] 定位上传视频方块:", r)
            if r:
                pg.mouse.click(r['x'], r['y'])
                pg.wait_for_timeout(3000)
        except Exception as e:
            print("[探库] 点击异常:", str(e)[:60])
        vs = None
        for _ in range(25):
            for fr in pg.frames:
                try:
                    if 'videoSelector' in (fr.url or ''):
                        vs = fr
                        break
                except Exception:
                    continue
            if vs:
                break
            pg.wait_for_timeout(1000)
        if not vs:
            print("[探库] 素材库 iframe 没出现（videoSelector）")
        else:
            items = vs.evaluate(r"""() => {
                const out = [];
                for (const img of document.querySelectorAll('img')) {
                    const t = img.getAttribute('title') || img.getAttribute('alt') || '';
                    if (t) out.push(t);
                }
                return [...new Set(out)].slice(0, 80);
            }""")
            print("[探库] 素材库带名项目 %d 个：" % len(items))
            for it in items:
                print("    %s" % it)
        pg.screenshot(path=os.path.join(OUT, "probe_lib.png"), full_page=True)
        print("[探库] 截图:", OUT)
        ctx.close()
        sys.exit(0)

    # ---- 视频（必传：无视频不双百）----
    vid = os.path.join(MAT, s, "%s_视频.mp4" % s)
    vid_ok = False
    if os.path.isfile(vid):
        try:
            vid_ok = bool(pa.upload_video(pg, vid))
            print("[步骤] 视频上传:", vid_ok)
        except Exception as e:
            print("✗ 视频失败:", str(e)[:100])
    else:
        print("✗ 无视频文件:", vid)
    pg.wait_for_timeout(3000)

    # ---- 规格书（选填）----
    try:
        if os.path.isfile(spec):
            ok = pa.upload_spec(pg, spec)
            print("[步骤] 规格书上传:", ok)
    except Exception as e:
        print("✗ 规格书失败:", str(e)[:80])

    pg.wait_for_timeout(3000)
    pg.screenshot(path=os.path.join(OUT, "3_final.png"), full_page=True)

    # ---- 复核：必填项还剩哪些空 ----
    left = pg.evaluate(r"""() => {
        const clean = s => (s || '').replace(/\s+/g, ' ').trim();
        const out = [];
        document.querySelectorAll('.sell-component-info-wrapper-wrap').forEach(row => {
            const lb = row.querySelector('.sell-component-info-wrapper-label');
            if (!lb) return;
            const name = clean(lb.textContent).replace(/^[*＊]\s*/, '');
            if (!name || name.length > 20) return;
            const req = !!row.querySelector('.sell-component-info-wrapper-required');
            if (!req) return;
            const inp = row.querySelector('input:not([type=hidden]), textarea');
            const sel = row.querySelector('[class*="selection-item"], [class*="selection-renderer"]');
            let v = sel ? clean(sel.textContent) : (inp && inp.value && inp.value !== 'on' ? inp.value : '');
            const imgs = row.querySelectorAll('img').length;
            if (!v && imgs > 0) v = '[已有图' + imgs + '张]';
            out.push({name, value: v, filled: !!v});
        });
        return out;
    }""")
    print("\n=== 必填项现状 ===")
    for r in left:
        print("  %s %-16s %s" % ("✓" if r["filled"] else "✗", r["name"][:16], r["value"][:30] or "(空)"))
    io.open(os.path.join(OUT, "required_state.json"), "w", encoding="utf-8").write(
        json.dumps(left, ensure_ascii=False, indent=2))

    print("\n★ 已停在提交前，未点提交。截图：%s" % OUT)

    # ---- 提交（仅当显式 --submit）----
    if "--submit" in sys.argv:
        # 提交前硬预检：这两项必须真的有值（上一次就是扫描误判“on”把它当成已填）
        chk = pg.evaluate("""() => {
            const clean = s => (s||'').replace(/\s+/g,' ').trim();
            const get = name => {
                for (const row of document.querySelectorAll('.sell-component-info-wrapper-wrap')) {
                    const lb = row.querySelector('.sell-component-info-wrapper-label');
                    if (!lb || clean(lb.textContent).replace(/^[*＊]\s*/,'').indexOf(name) < 0) continue;
                    // ① 淘宝的选中态用 class 表示（input.checked 读不到）
                    const on = [...row.querySelectorAll('[class*="radio-checked"], [class*="checkbox-checked"]')];
                    if (on.length) {
                        return on.map(e => {
                            const w = e.closest('[class*="radio-wrapper"], [class*="checkbox-wrapper"]') || e.parentElement;
                            return clean((w && w.textContent) || '');
                        }).join(',');
                    }
                    // ①b 退路：原生 input
                    const ck = [...row.querySelectorAll('input[type=radio], input[type=checkbox]')].filter(e => e.checked);
                    if (ck.length) {
                        return ck.map(e => {
                            const l2 = e.closest('label') || e.parentElement;
                            return clean((l2 && l2.textContent) || e.value);
                        }).join(',');
                    }
                    // ② 下拉选中项
                    const sel = row.querySelector('[class*="selection-item"], [class*="selection-renderer"]');
                    if (sel && clean(sel.textContent)) return clean(sel.textContent);
                    // ③ 文本/数字框（排除 checkbox 默认值 on）
                    const inps = [...row.querySelectorAll('input:not([type=hidden])')]
                        .map(e => e.value || '').filter(v => v && v !== 'on');
                    return inps.join(',');
                }
                return '';
            };
            const area = get('发货地'), way = get('提取方式');
            return {area, way, ok: !!area && !!way};
        }""")
        print("[预检] 发货地=%r  提取方式=%r" % (chk.get('area'), chk.get('way')))
        if not chk.get('ok'):
            # 淘宝的选中态用 CSS class 表示，脚本读不准——不拿它当闸。
            # 平台自己的提交校验才是权威：没填/填错它会在提交时拦下，不会错误上架。
            print("⚠ 预检读不准这两项（选中态用 CSS class），交给平台校验")
        if True:
            print("\n★ 收到 --submit，准备提交")
            try:
                # 先清弹窗：淘宝会把营销/升级弹窗盖在提交按钮上，点了没反应也不报错
                # （3314G 两次失败就是中了这个：截图上是一个带货广告弹窗盖住了按钮）
                for _f in ("dismiss_upgrade_dialog", "close_ai_popup"):
                    try:
                        getattr(pa, _f)(pg)
                    except Exception:
                        pass
                try:
                    pg.evaluate("""() => {
                        let n = 0;
                        for (const b of document.querySelectorAll('[class*="close"], [aria-label*="关闭"], [title="关闭"]')) {
                            const r = b.getBoundingClientRect();
                            if (r.width > 5 && r.width < 60 && r.height > 5 && r.height < 60) {
                                try { b.click(); n++; } catch (e) {}
                            }
                        }
                        return n;
                    }""")
                    pg.wait_for_timeout(900)
                except Exception:
                    pass
                pa.submit(pg)
                pg.wait_for_timeout(8000)
                pg.screenshot(path=os.path.join(OUT, "4_after_submit.png"), full_page=True)

                # 提交失败 → 把错误明细抖出来（不靠猜哪项）
                if "success.htm" not in pg.url:
                    errs = pg.evaluate(r"""() => {
                        const out = [];
                        for (const e of document.querySelectorAll('[class*="error"], [class*="Error"], [class*="help"]')) {
                            const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
                            if (!t || t.length > 90) continue;
                            if (/必填|不能为空|未填|请填|请选|需要/.test(t)) out.push(t);
                        }
                        for (const row of document.querySelectorAll('.sell-component-info-wrapper-wrap')) {
                            const lb = row.querySelector('.sell-component-info-wrapper-label');
                            if (!lb) continue;
                            const col = getComputedStyle(lb).color || '';
                            const m = col.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
                            if (m && +m[1] > 180 && +m[2] < 120 && +m[3] < 120) {
                                out.push('[红label] ' + (lb.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 30));
                            }
                        }
                        return [...new Set(out)].slice(0, 20);
                    }""")
                    print("\n=== 提交被拦，错误明细 ===")
                    for e in (errs or [])[:20]:
                        print("  ●", e)
                    if not errs:
                        print("  （未捕获到错误文本，需人工看 4_after_submit.png）")
                body = pg.evaluate("() => (document.body.innerText||'').slice(0,400)") or ''
                ok = ('发布成功' in body) or ('基础分' in body) or ('双百' in body) or ('流量加速中' in body)
                print("[提交] 结果页命中成功标志=%s" % ok)
                print("[提交] 页面 URL:", pg.url[:120])
                if not ok:
                    print("[提交] 页面正文前300字:", body[:300].replace(chr(10), ' | '))
                try:
                    iid = pa._record_item_id(LH, pg)
                    print("[提交] 商品ID记录:", iid)
                except Exception as e:
                    print("[提交] ID 记录失败:", str(e)[:80])
            except Exception as e:
                print("✗ 提交异常:", str(e)[:200])
    else:
        print("浏览器保持打开，你可以自己看一眼。关掉窗口即结束。")
        # 只在预演模式留窗口；提交模式直接关，否则批量时每个都空等 1 分钟
        try:
            pg.wait_for_timeout(60000)
        except Exception:
            pass
    ctx.close()
