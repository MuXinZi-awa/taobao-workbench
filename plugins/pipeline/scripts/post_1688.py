# -*- coding: utf-8 -*-
"""点 1688 的「去上架」，看真正发布页的必填结构。不提交。不关闭浏览器。"""
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
    page.bring_to_front()
    page.wait_for_timeout(800)

    el = page.evaluate(r"""() => {
        const out = [];
        for (const e of document.querySelectorAll('a, button, span, div, td')) {
            if (e.children.length > 0) continue;
            const t = (e.textContent || '').replace(/\s+/g, '').trim();
            if (t !== '去上架') continue;
            const r = e.getBoundingClientRect();
            if (r.width < 6 || r.height < 6) continue;
            out.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
        }
        return out;
    }""")
    print("[上架] 「去上架」节点:", el)
    if not el:
        print("✗ 没找到")
        sys.exit(1)

    n_before = len(ctx.pages)
    page.mouse.click(el[0]['x'], el[0]['y'])
    print("[上架] 已点击第一个，页面数(前):", n_before)

    for i in range(12):
        page.wait_for_timeout(6000)
        cur = ctx.pages[-1] if len(ctx.pages) > n_before else page
        try:
            cur.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        try:
            t = (cur.title() or "")[:40]
        except Exception:
            t = ""
        print("[上架] 第 %d 轮 url=%s  页面数=%d" % (i + 1, cur.url[:100], len(ctx.pages)))
        if "post" in cur.url or "offer" in cur.url and "publish" in cur.url:
            print("      → 进发布页了:", cur.url[:110])
            break

    cur = ctx.pages[-1]
    cur.bring_to_front()
    print("\n[上架] 最终 URL:", cur.url[:130])
    print("[上架] 标题:", (cur.title() or "")[:60])

    body = cur.evaluate("() => (document.body.innerText || '')") or ""
    print("\n=== 发布页正文（前 2500）===")
    print(body[:2500])

    # 必填星号
    req = cur.evaluate(r"""() => {
        const out = [];
        for (const e of document.querySelectorAll('*')) {
            const t = (e.textContent || '').trim();
            if (t !== '*' && t !== '＊') continue;
            // 往上找最近的 label 容器
            let p = e.parentElement, name = '';
            for (let k = 0; k < 4 && p; k++) {
                const tx = (p.textContent || '').replace(/\s+/g, '').replace(/[*＊]/g, '').trim();
                if (tx && tx.length <= 24) { name = tx; break; }
                p = p.parentElement;
            }
            if (name) out.push(name);
        }
        return [...new Set(out)].slice(0, 60);
    }""")
    print("\n=== 必填项（★）%d 个 ===" % len(req))
    for r in req:
        print("    ★ %s" % r)

    cur.screenshot(path=os.path.join(OUT, "10_post.png"))
    print("\n[上架] 截图 10_post.png")
    print("[上架] 完成，浏览器未关闭")
