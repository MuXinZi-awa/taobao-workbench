# -*- coding: utf-8 -*-
"""小云雀（pippit）生图客户端 · 免费通道版

为什么要这么绕：
  小云雀有**两条接口**，模型枚举完全不同——
    · /api/biz/v1/skill/submit_run  → 认 Bearer KEY，但模型只收 seedream_5.0_pro（要积分）
    · /api/biz/v1/agent/submit_run  → 认**网页登录 cookie**，模型收 seedream_5.0（=5.0 Lite，免费）
  所以走 agent 那条：用工作台的浏览器 profile（里面存着扫码登录态），在**页面里**发 fetch
  —— 自动带 cookie、不用自己签名（和 _mtop_api.py 调淘宝是同一个套路）。

取结果不是轮询，是 SSE 订阅：
  /api/biz/v1/agent/subscribe_run?runId=...   → 在页面里读流，事件里挑产物 URL

用法：
    from _xyq_api import XyqClient
    with XyqClient() as c:
        tid, rid = c.submit("一只白色的猫，白底")
        urls = c.wait_result(tid, rid, timeout=300)
"""
import os
import sys
import json
import time
import uuid

_WB = None
for _d in (os.path.dirname(os.path.abspath(__file__)),):
    _x = _d
    while _x and not os.path.isfile(os.path.join(_x, "paths.py")) and os.path.dirname(_x) != _x:
        _x = os.path.dirname(_x)
    if os.path.isfile(os.path.join(_x, "paths.py")):
        _WB = _x
        if _WB not in sys.path:
            sys.path.insert(0, _WB)
        break
import paths

BASE = "https://xyq.jianying.com"

# 提交后在页面里攒事件的容器名
_JS = {
    "hook": """(function(){
  window.__xyqEvents = window.__xyqEvents || [];
  return 'ok';
})()""",
    "submit": """async (a) => {
  const r = await fetch('/api/biz/v1/agent/submit_run', {
      method: 'POST', credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(a.b)});
  return await r.text();
}""",
    # 后台订阅这次 run：把 SSE 的每一段文本攒进 window.__xyqEvents（不阻塞 evaluate）
    "subscribe": """(a) => {
  window.__xyqEvents = [];
  window.__xyqSubDone = false;
  fetch('/api/biz/v1/agent/subscribe_run?runId=' + a.r, {
      method: 'POST', credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({thread_id: a.t, run_id: a.r})})
    .then(async (resp) => {
      const rd = resp.body.getReader();
      const dec = new TextDecoder();
      while (true) {
        const {done, value} = await rd.read();
        if (done) break;
        try { window.__xyqEvents.push(dec.decode(value, {stream: true})); } catch(e) {}
      }
      window.__xyqSubDone = true;
    }).catch((e) => { window.__xyqEvents.push('__ERR__' + e.message); window.__xyqSubDone = true; });
  return 'subscribed';
}""",
    "events": "() => JSON.stringify(window.__xyqEvents || [])",
}


class XyqClient(object):
    def __init__(self, profile=None, chrome=None, headless=True):
        self.PROFILE = profile or os.path.join(paths.TG_ROOT, "chrome_profile")
        self.CHROME = chrome or next(
            (p for p in getattr(paths, "CHROME_CANDIDATES", []) if os.path.isfile(p)), "chrome")
        self.headless = headless
        self.p = None
        self.ctx = None
        self.page = None

    # ── 起浏览器（挂工作台 profile，页面内 fetch 自然带登录态）──
    def open(self):
        from playwright.sync_api import sync_playwright
        self.p = sync_playwright().start()
        self.ctx = self.p.chromium.launch_persistent_context(
            self.PROFILE, executable_path=self.CHROME, headless=self.headless,
            viewport={"width": 1400, "height": 900}, locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"])
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.goto(BASE, timeout=60000, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2500)
        self.page.evaluate(_JS["hook"])
        return self

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    # ── 提交一次生成 ──
    def submit(self, prompt, image_model="seedream_5.0", resolution="2K",
               video_resolution="720p", user_info=None, ref_asset_id=""):
        """image_model 默认 seedream_5.0（=5.0 Lite，免费）。
        ref_asset_id：参考图的 pippit_asset_id（用 skill 的 upload_file 传图后拿到）。
        元器件这类货必须带参考图——AI 凭空画结构，懂行的一眼就看穿。"""
        tid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        content = [
            {"type": "data", "sub_type": "biz/x_data_prompt_text",
             "data": json.dumps({"content": prompt}, ensure_ascii=False)},
        ]
        # 参考图块：网页版就是这么挂的（sub_type=biz/x_data_upload_image + pippit_asset_id）
        if ref_asset_id:
            _aid = str(ref_asset_id)
            _img = {"url": "",
                    "metadata": {"width": 0, "height": 0, "format": "", "md5": "",
                                 "size": 0, "ratio": "", "name": "", "mime": "", "frame_cnt": 0},
                    "pippit_asset_id": _aid,
                    "security_check_scene": ["pippit_nest_agent"]}
            content.append({
                "type": "data", "sub_type": "biz/x_data_upload_image",
                "data": json.dumps({"image": _img, "pippit_asset_id": _aid,
                                     "security_check_scene": ["pippit_nest_agent"],
                                     "extra": {"security_check_scene": ["pippit_nest_agent"]}},
                                    ensure_ascii=False),
                "pippit_asset_id": _aid,
                "security_check_scene": ["pippit_nest_agent"],
                "extra": {"security_check_scene": ["pippit_nest_agent"]},
            })
        content.append({"type": "data", "sub_type": "biz/general_agent_settings",
                        "data": json.dumps({"video_resolution": video_resolution,
                                            "image_model": image_model,
                                            "resolution": resolution}, ensure_ascii=False)})
        ui = user_info or {"consumer_uid": "4021363950512931",
                           "workspace_id": "7605500086247784498",
                           "app_id": "795647"}
        body = {
            "message": {"message_id": "", "role": "user",
                        "thread_id": tid, "run_id": rid,
                        "created_at": int(time.time() * 1000), "content": content},
            "user_info": ui,
            "agent_name": "pippit_nest_agent",
            "entrance_from": "web",
        }
        res = self.page.evaluate(_JS["submit"], {"b": body})
        try:
            d = json.loads(res)
        except Exception:
            raise RuntimeError("提交响应不是 JSON: %s" % str(res)[:200])
        if str(d.get("ret")) != "0":
            raise RuntimeError("提交失败 ret=%s %s" % (d.get("ret"), d.get("errmsg")))
        return tid, rid

    # ── 订阅这次运行的 SSE，攒事件，挑出产物 URL ──
    def wait_result(self, tid, rid, timeout=300):
        self.page.evaluate(_JS["subscribe"], {"t": tid, "r": rid})
        t0 = time.time()
        seen = []
        while time.time() - t0 < timeout:
            self.page.wait_for_timeout(2500)
            try:
                raw = self.page.evaluate(_JS["events"])
                events = json.loads(raw) if raw else []
            except Exception:
                continue
            text = "\n".join(events)
            # 产物 URL 特征：byteimg / aigc / pippit 域下的图片地址
            for tok in text.replace('\\"', '"').replace('\\/', '/').split('"'):
                if ("byteimg" in tok or "aigc" in tok) and ".image" in tok and tok not in seen:
                    if tok.startswith("http") or tok.startswith("//"):
                        seen.append(tok)
            # 跑完了就再等一拍，把尾包收干净
            done = self.page.evaluate("() => !!window.__xyqSubDone")
            if done:
                break
        # 只回**最后一张**：小云雀的流程是“先抠图 → 再做海报”，事件里会轮流吐出
        # 中间产物（抠好的图）和最终海报——取第一个就会拿到抠图（实测踩过）。
        return seen[-1:] if seen else []

    def close(self):
        for fn in (lambda: self.ctx and self.ctx.close(),
                   lambda: self.p and self.p.stop()):
            try:
                fn()
            except Exception:
                pass
