# -*- coding: utf-8 -*-
"""上传 1688 Excel 发品文件，等 AI 识别，读结果。不提交。不关闭浏览器。"""
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



XLSX = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\_工程_新品素材\ali_excel\测试_两个商品.xlsx"
OUT = os.path.join(r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\_工程_新品素材\probe",
                   "ali_" + datetime.datetime.now().strftime("%m%d_%H%M"))
os.makedirs(OUT, exist_ok=True)
print("[传] 文件:", XLSX, "存在:", os.path.isfile(XLSX))

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = browser.contexts[0]
    page = None
    for pg in ctx.pages:
        if "select.htm" in (pg.url or ""):
            page = pg
            break
    if page is None:
        print("✗ 类目页不在了，现有页面：")
        for i, pg in enumerate(ctx.pages):
            print("   [%d] %s" % (i, (pg.url or "")[:110]))
        sys.exit(1)
    page.bring_to_front()
    page.wait_for_timeout(1000)
    print("[传] 页面:", page.url[:110])

    # 优先切到「Excel发品」再点「上传表格」
    for label in ("Excel发品", "上传表格"):
        el = page.evaluate(r"""(label) => {
            const out = [];
            for (const e of document.querySelectorAll('a, button, span, div, li')) {
                if (e.children.length > 0) continue;
                const t = (e.textContent || '').replace(/\s+/g, '').trim();
                if (t !== label) continue;
                const r = e.getBoundingClientRect();
                if (r.width < 8 || r.height < 8) continue;
                out.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
            }
            return out;
        }""", label)
        print("[传] 「%s」叶子节点 %d 个: %s" % (label, len(el), el[:3]))
        if not el:
            continue
        if label == "Excel发品":
            page.mouse.click(el[0]['x'], el[0]['y'])
            page.wait_for_timeout(1800)
        else:
            # 点「上传表格」→ 应触发 filechooser
            try:
                with page.expect_file_chooser(timeout=15000) as fc:
                    page.mouse.click(el[0]['x'], el[0]['y'])
                chooser = fc.value
                chooser.set_files(XLSX)
                print("[传] ✓ 已选文件，等 AI 识别…")
            except Exception as e:
                print("[传] 上传表格点击未触发 filechooser:", str(e)[:100])
                # 退路：直接找 input[type=file]
                pages_before = len(ctx.pages)
                try:
                    fi = page.locator('input[type="file"]').first
                    fi.set_input_files(XLSX, timeout=8000)
                    print("[传] ✓ 通过 input[type=file] 设置文件成功")
                except Exception as e2:
                    print("[传] input[type=file] 也不行:", str(e2)[:100])

    # 等识别
    for i in range(12):
        page.wait_for_timeout(5000)
        txt = ""
        try:
            txt = page.evaluate("() => (document.body.innerText || '')") or ""
        except Exception:
            pass
        hit = any(k in txt for k in ("识别中", "解析中", "上传成功", "正在识别", "识别完成", "解析完成", "请确认", "校验"))
        print("[传] 第 %d 轮 (%ds) 页面变化: %s" % (i + 1, (i + 1) * 5, hit))
        if hit and i >= 1:
            break

    print("\n[传] 当前 URL:", page.url[:130])
    print("[传] 页面数:", len(ctx.pages))
    for i, pg in enumerate(ctx.pages):
        print("   [%d] %s" % (i, (pg.url or "")[:115]))

    body = page.evaluate("() => (document.body.innerText || '').slice(0, 1500)") or ""
    print("\n=== 页面正文（前 1200）===")
    print(body[:1200])

    page.screenshot(path=os.path.join(OUT, "7_upload.png"), full_page=True)
    print("\n[传] 截图 7_upload.png")
    print("[传] 完成，浏览器未关闭")
