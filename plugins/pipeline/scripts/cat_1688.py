# -*- coding: utf-8 -*-
"""1688 类目发品：点「类目发品」→ 选「电子元器件」→ 逐层展开子类目，找到 PTC 自恢复保险丝的正确位置。
只读展开，不提交。不关闭浏览器。
"""
import sys, os, datetime
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



OUT = os.path.join(r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\_工程_新品素材\probe",
                   "ali_" + datetime.datetime.now().strftime("%m%d_%H%M"))
os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = browser.contexts[0]

    page = None
    for pg in ctx.pages:
        if "select.htm" in (pg.url or ""):
            page = pg
            break
    if page is None:
        # 没有就导航过去
        page = ctx.pages[0]
        page.goto("https://offer-new.1688.com/select.htm", timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
    page.bring_to_front()
    page.wait_for_timeout(1200)
    print("[类目] 页面:", page.url[:110])

    def click_leaf(label, exact=True):
        els = page.evaluate(r"""([label, exact]) => {
            const out = [];
            for (const e of document.querySelectorAll('a, button, span, div, li, p')) {
                if (e.children.length > 0) continue;
                const t = (e.textContent || '').replace(/\s+/g, '').trim();
                const ok = exact ? (t === label) : (t.indexOf(label) >= 0);
                if (!ok) continue;
                const r = e.getBoundingClientRect();
                if (r.width < 8 || r.height < 8) continue;
                out.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), t: t});
            }
            return out;
        }""", [label, exact])
        return els

    # 1) 切到「类目发品」
    els = click_leaf("类目发品")
    print("[类目] 「类目发品」节点:", els[:3])
    if els:
        page.mouse.click(els[0]['x'], els[0]['y'])
        page.wait_for_timeout(2200)

    body = page.evaluate("() => (document.body.innerText || '').slice(0, 900)") or ""
    print("\n=== 切到类目发品后（前 800）===")
    print(body[:800])

    # 2) 点「电子元器件」
    els = page.evaluate(r"""() => {
        const out = [];
        for (const e of document.querySelectorAll('*')) {
            if (e.children.length > 0) continue;
            const t = (e.textContent || '').replace(/\s+/g, '').trim();
            if (t !== '电子元器件') continue;
            const r = e.getBoundingClientRect();
            if (r.width < 8 || r.height < 8) continue;
            out.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
        }
        return out;
    }""")
    print("\n[类目] 「电子元器件」节点:", els[:3])
    if els:
        page.mouse.click(els[0]['x'], els[0]['y'])
        page.wait_for_timeout(2500)

    # 3) 读展开后的二级/三级类目
    print("\n=== 展开后的类目区文本 ===")
    txt = page.evaluate(r"""() => {
        // 找包含"电路保护"或"保险丝"的容器
        let best = null, bestLen = 0;
        for (const e of document.querySelectorAll('div, ul, section')) {
            const t = e.textContent || '';
            if (t.indexOf('电路保护') < 0) continue;
            if (t.length > bestLen && t.length < 4000) { best = e; bestLen = t.length; }
        }
        if (best) return best.innerText;
        return (document.body.innerText || '').slice(0, 2500);
    }""") or ""
    print(txt[:2500])

    # 4) 全部叶子类目候选（含 保险丝/PTC/自恢复）
    cands = page.evaluate(r"""() => {
        const out = [];
        for (const e of document.querySelectorAll('*')) {
            if (e.children.length > 0) continue;
            const t = (e.textContent || '').replace(/\s+/g, '').trim();
            if (!t || t.length > 20) continue;
            if (!/保险丝|熔断|PTC|自恢复|电路保护|过流/.test(t)) continue;
            const r = e.getBoundingClientRect();
            if (r.width < 8 || r.height < 8) continue;
            out.push({t: t, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
        }
        const seen = new Set(); const u = [];
        for (const o of out) { const k = o.t; if (!seen.has(k)) { seen.add(k); u.push(o); } }
        return u.slice(0, 40);
    }""")
    print("\n=== 「保险丝/PTC/自恢复」相关节点 %d 个 ===" % len(cands))
    for c in cands:
        print("    %-22s (%d,%d)" % (c['t'], c['x'], c['y']))

    page.screenshot(path=os.path.join(OUT, "11_cat_tree.png"), full_page=True)
    print("\n[类目] 截图 11_cat_tree.png")
    print("[类目] 完成，浏览器未关闭")
