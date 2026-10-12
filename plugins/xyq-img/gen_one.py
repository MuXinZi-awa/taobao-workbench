# -*- coding: utf-8 -*-
"""单料号生图 · 子进程版

为什么必须起子进程：playwright 在内核的**请求线程**里跑不稳，会报
`TypeError: function() argument 'code' must be code, not str`。
pipeline 的分流当初也是为这个才走子进程。

用法：python gen_one.py <料号> <模板名> <模型> [参考图] [字段覆盖JSON]
stdout 最后一行是 JSON。
"""
import os
import sys
import io
import json
import time
import urllib.request

PBASE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.dirname(PBASE)
WB = os.path.dirname(PLUGINS)          # 工作台根（paths.py 在这）
PIPE = os.path.join(PLUGINS, "pipeline", "scripts")
for _p in (WB, PIPE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import paths
from _xyq_api import XyqClient
from _xyq_prompt import fields_of, render
import ai_schedule as A

TPL_DIR = os.path.join(PIPE, "prompt_templates")
MAT_ROOT = paths.MAT_ROOT
PROXY = "http://127.0.0.1:7897"


def find_ref(lh):
    """素材目录里挑一张当参考图：换源待复检 > 封面 > 第一张图"""
    d = os.path.join(MAT_ROOT, lh)
    if not os.path.isdir(d):
        return ""
    imgs = [f for f in os.listdir(d)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))]
    if not imgs:
        return ""
    for key in ("换源_待复检", "封面", "cover"):
        for f in imgs:
            if key in f:
                return os.path.join(d, f)
    return os.path.join(d, imgs[0])


def main():
    lh = sys.argv[1] if len(sys.argv) > 1 else ""
    tpl = sys.argv[2] if len(sys.argv) > 2 else ""
    model = sys.argv[3] if len(sys.argv) > 3 else "seedream_5.0"
    ref = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else find_ref(lh)
    over = {}
    if len(sys.argv) > 5 and sys.argv[5]:
        try:
            over = json.loads(sys.argv[5])
        except Exception:
            over = {}
    try:
        if not lh or not tpl:
            print(json.dumps({"ok": False, "error": "缺参数"}, ensure_ascii=False))
            return
        fp = os.path.join(TPL_DIR, os.path.basename(tpl))
        if not os.path.isfile(fp):
            print(json.dumps({"ok": False, "error": "模板不存在: %s" % tpl}, ensure_ascii=False))
            return
        if not ref or not os.path.isfile(ref):
            print(json.dumps({"ok": False, "error": "没找到参考图"}, ensure_ascii=False))
            return
        fields = fields_of(lh, over)
        prompt = render(io.open(fp, encoding="utf-8").read(), fields)
        print("[1] 模板=%s 参考图=%s" % (tpl, os.path.basename(ref)))
        if over:
            print("[1b] 字段覆盖: %s" % json.dumps(over, ensure_ascii=False))
        up = A.upload_asset(ref)
        d = up.get("data", {}) if isinstance(up, dict) else {}
        aid = d.get("pippit_asset_id") or d.get("use_this_in_submit_run") or d.get("asset_id")
        print("[2] asset=%s" % aid)
        if not aid:
            print(json.dumps({"ok": False, "error": "参考图上传失败"}, ensure_ascii=False))
            return
        with XyqClient(headless=True) as c:
            tid, rid = c.submit(prompt, image_model=model, ref_asset_id=aid)
            print("[3] 提交 %s" % tid)
            urls = c.wait_result(tid, rid, timeout=420)
        print("[4] 产物数 %d" % len(urls or []))
        if not urls:
            print(json.dumps({"ok": False, "error": "没拿到产物（模型没出图）"}, ensure_ascii=False))
            return
        tag = os.path.splitext(os.path.basename(tpl))[0]
        dest = os.path.join(os.path.dirname(ref),
                            "%s_AI_%s_%s.png" % (lh, tag, time.strftime("%m%d%H%M")))
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
        with op.open(urls[0], timeout=180) as r:
            data = r.read()
        with io.open(dest, "wb") as f:
            f.write(data)
        print(json.dumps({"ok": True, "out": dest, "bytes": len(data), "model": model,
                          "fields": {k: v for k, v in fields.items() if k in ("标题", "品类")},
                          "n_urls": len(urls)}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])},
                         ensure_ascii=False))


main()
