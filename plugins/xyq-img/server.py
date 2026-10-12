# -*- coding: utf-8 -*-
"""AI 主图插件 · 调用层

链路：料号 → 模板渲染（品牌/料号/属性自动填）→ 上传参考图 → 小云雀 agent 通道出图 → 落素材目录。
生图走 Seedream 5.0 Lite（免费，0 积分）；想更精细可以切 pro（花积分）。

底层一行没改：客户端和模板引擎都在 pipeline/scripts/ 里（_xyq_api / _xyq_prompt），
这里只做"接线"——参数进来、结果出去。
"""
import os
import sys
import io
import json
import time
import urllib.request

PBASE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.dirname(PBASE)
PIPE_SCRIPTS = os.path.join(PLUGINS, "pipeline", "scripts")
if PIPE_SCRIPTS not in sys.path:
    sys.path.insert(0, PIPE_SCRIPTS)

import paths
from _xyq_api import XyqClient
from _xyq_prompt import fields_of, render

TPL_DIR = os.path.join(PIPE_SCRIPTS, "prompt_templates")
MAT_ROOT = paths.MAT_ROOT
PROXY = "http://127.0.0.1:7897"


def _templates():
    if not os.path.isdir(TPL_DIR):
        return []
    return sorted([f for f in os.listdir(TPL_DIR) if f.endswith(".md")])


def _find_ref(lh):
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


def _upload(ref_path):
    """传参考图拿 pippit_asset_id（skill 通道；agent 通道生成时要用它）"""
    import ai_schedule as A
    up = A.upload_asset(ref_path)
    d = up.get("data", {}) if isinstance(up, dict) else {}
    return d.get("pippit_asset_id", "") or d.get("use_this_in_submit_run", "") or d.get("asset_id", "")


def _download(url, dest):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    with op.open(url, timeout=180) as r:
        data = r.read()
    with io.open(dest, "wb") as f:
        f.write(data)
    return len(data)


def _prompt_of(lh, tpl_name, over=None):
    fp = os.path.join(TPL_DIR, os.path.basename(tpl_name or ""))
    if not os.path.isfile(fp):
        raise RuntimeError("模板不存在: %s" % tpl_name)
    fields = fields_of(lh)
    if over:
        fields.update({k: v for k, v in over.items() if v})
    return render(io.open(fp, encoding="utf-8").read(), fields), fields


def handle(action, params):
    if action == "info":
        return {"ok": True, "templates": _templates(), "mat_root": MAT_ROOT}

    # 只渲染提示词、不生成——给人看一眼"会拿什么去画"
    if action == "render":
        lh = (params.get("lh") or "").strip()
        if not lh:
            return {"ok": False, "error": "缺料号"}
        tpl = (params.get("tpl") or "").strip() or (_templates() or [""])[0]
        try:
            prompt, fields = _prompt_of(lh, tpl, {"标题": params.get("标题"),
                                                  "品牌": params.get("品牌"),
                                                  "品类": params.get("品类")})
        except Exception as e:
            return {"ok": False, "error": str(e)[:120]}
        return {"ok": True, "prompt": prompt, "fields": fields,
                "ref": _find_ref(lh), "tpl": tpl}

    # ── 模板读写：面板里能看、能改、能新建（只允许 prompt_templates 下的 .md）──
    if action == "tpl-get":
        name = os.path.basename((params.get("name") or "").strip())
        fp = os.path.join(TPL_DIR, name)
        if not name.endswith(".md") or not os.path.isfile(fp):
            return {"ok": False, "error": "模板不存在: %s" % name}
        return {"ok": True, "name": name, "text": io.open(fp, encoding="utf-8").read()}

    if action == "tpl-save":
        name = os.path.basename((params.get("name") or "").strip())
        if not name.endswith(".md"):
            return {"ok": False, "error": "模板名要以 .md 结尾"}
        fp = os.path.join(TPL_DIR, name)
        try:
            with io.open(fp, "w", encoding="utf-8") as f:
                f.write(params.get("text") or "")
            return {"ok": True, "name": name}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    # 生成：**必须起子进程**——playwright 在内核的请求线程里跑会报
    # “function() argument 'code' must be code, not str”（和 pipeline 分流一个道理）
    if action == "generate":
        import subprocess
        lh = (params.get("lh") or "").strip()
        if not lh:
            return {"ok": False, "error": "缺料号"}
        tpl = (params.get("tpl") or "").strip() or (_templates() or [""])[0]
        model = (params.get("model") or "seedream_5.0").strip()
        ref = (params.get("ref") or "").strip()
        fp = os.path.join(PBASE, "gen_one.py")
        if not os.path.isfile(fp):
            return {"ok": False, "error": "缺少 gen_one.py"}
        args = [paths.python_exe(), "-X", "utf8", "-u", fp, lh, tpl, model]
        args.append(ref or "")     # 参考图（空串=让子进程自己去素材目录找）
        over = {}
        for k in ("标题", "品牌", "品类"):
            if params.get(k):
                over[k] = params[k]
        args.append(json.dumps(over, ensure_ascii=False) if over else "")
        try:
            r = subprocess.run(args, cwd=PBASE, capture_output=True, timeout=600)
            out = (r.stdout or b"").decode("utf-8", "replace")
            tail = ""
            for line in out.strip().splitlines()[::-1]:
                if line.strip().startswith("{"):
                    tail = line.strip()
                    break
            if not tail:
                err = (r.stderr or b"").decode("utf-8", "replace")
                return {"ok": False,
                        "error": "子进程无结果: " + ((out[-200:] or err[-200:]).strip() or "(空)")}
            d = json.loads(tail)
            if d.get("ok"):
                d["msg"] = "已生成（%s）：%s" % (d.get("model"), os.path.basename(d.get("out", "")))
            return d
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}

    return {"ok": False, "error": "未知 action: %s" % action}
