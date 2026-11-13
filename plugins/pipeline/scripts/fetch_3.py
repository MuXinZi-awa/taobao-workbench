# -*- coding: utf-8 -*-
"""给 3 个缺素材的料号下载封面 + 规格书
环境有坏代理（HTTP_PROXY 缺端口），必须 ProxyHandler({}) 绕开
"""
import json, os, ssl, urllib.request, hashlib

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
            _mr = _p.get("mat_root")
            # mat_root = <WS>\\办公室工作\\素材\\产品素材 → 上溯三级才到 <WS>
            return os.path.dirname(os.path.dirname(os.path.dirname(_mr)))
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



MAT = _MAT
WANT = ["3224W-1-102E", "3314J-1-102E", "MF-R110"]

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
op.add_handler(urllib.request.HTTPSHandler(context=ctx))

data = json.loads(op.open("https://www.joincircuit.com/u_file/taobao/index.json", timeout=25).read().decode("utf-8"))
idx = {it["model"]: it for it in data["items"]}


def grab(url, dst):
    raw = op.open(url, timeout=40).read()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(raw)
    return len(raw), hashlib.md5(raw).hexdigest()


for lh in WANT:
    it = idx.get(lh)
    if not it:
        print("✗ %s 清单里没有" % lh)
        continue
    d = os.path.join(MAT, lh)
    os.makedirs(d, exist_ok=True)
    print("\n[%s]" % lh)

    # 封面
    dst = os.path.join(d, "%s_封面.jpg" % lh)
    try:
        n, md5 = grab(it["url"], dst)
        exp = (it.get("md5") or "").lower()
        ok = (not exp) or (md5 == exp)
        print("  封面 %.1f KB  md5=%s  清单=%s  %s" % (n / 1024, md5[:10], exp[:10] or "-", "✓" if ok else "⚠不一致"))
    except Exception as e:
        print("  封面 ✗", str(e)[:80])

    # 规格书
    ds = it.get("datasheet")
    if ds:
        dst2 = os.path.join(d, "规格书.pdf")
        try:
            n2, _ = grab(ds, dst2)
            print("  规格书 %.0f KB  (%s)" % (n2 / 1024, os.path.basename(ds)))
        except Exception as e:
            print("  规格书 ✗", str(e)[:80])
    else:
        print("  规格书：清单无 datasheet 字段")
