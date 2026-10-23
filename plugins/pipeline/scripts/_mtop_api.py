# -*- coding: utf-8 -*-
"""MTOP API 客户端（skill 自包含版）——不依赖外部项目文件
- 登录逻辑内嵌（不再 import publish_auto）
- chrome profile 用 skill 内 .profile（首次运行自动登录）
- 运行环境：任意装有 playwright 的 python（API 工具不需要 numpy）
用法：
    from _mtop_api import MtopClient
    mc = MtopClient().open()       # 首次运行会弹浏览器登录（自动填账号）
    items = mc.search_items("料号") # [{itemId,title,price}]
    mc.close()
"""
import os
import sys
import json
import time
import hashlib
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
def _profile_dir():
    """登录态 profile：跟随**激活的店铺连接**（多账号隔离）；取不到回退 .profile"""
    try:
        _wb = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\工具\workbench"
        if _wb not in sys.path:
            sys.path.insert(0, _wb)
        # acct_profile.py 在「推广一键跑」——不把它加进 sys.path 会 import 失败并静默回退 .profile（=没登录态）
        for _cand in (r"C:\Users\jdt-pty\Desktop\推广一键跑", BASE):
            if os.path.isdir(_cand) and _cand not in sys.path:
                sys.path.insert(0, _cand)
        import acct_profile
        pf, _c = acct_profile.resolve()          # 连接配了 profile 用它；否则按连接名推导
        if pf:
            return pf
    except Exception:
        pass
    return os.path.join(BASE, ".profile")


PROFILE_DIR = _profile_dir()                 # 运行期求值（每次新进程 = 当前激活账号）
RUNTIME_DIR = os.path.join(BASE, "runtime")
APPKEY = "12574478"
H5API = "https://h5api.m.taobao.com/h5/{api}/{v}/"
SELL_URL = "https://myseller.taobao.com/home.htm/SellManage/on_sale?current=1&pageSize=20"
# 登录账号（千牛卖家中心）——从工作台连接库读，切号即变
def _cred():
    try:
        import sys as _s
        _wb = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\工具\workbench"
        if _wb not in _s.path:
            _s.path.insert(0, _wb)
        import conn_store
        a = conn_store.active_of("taobao")
        if a and a.get("username") and a.get("secret"):
            return a["username"], a["secret"]
    except Exception:
        pass
    raise RuntimeError(
        "未取到淘宝凭据：请在连接库配置并激活店铺连接（连接导航 / config.json）。"
        "（已不再内置默认账号密码）")


ACCOUNT, PASSWORD = _cred()


def find_chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Users\jdt-pty\AppData\Local\Google\Chrome\Application\chrome.exe"):
        if os.path.isfile(p):
            return p
    return None


class MtopClient:
    def __init__(self, profile=None, chrome=None):
        self.PROFILE = profile or PROFILE_DIR
        self.CHROME = chrome or find_chrome() or "chrome"
        self.p = None
        self.ctx = None
        self.page = None
        self.token = ""
        os.makedirs(RUNTIME_DIR, exist_ok=True)

    def open(self, headless=False):
        """headless=True：无头启动（登录态探测等静默场景——不弹窗）
        CDP 优先：梓帆手开的 Chrome（--remote-debugging-port）持着登录态，连上去用它；
        连不上才自起浏览器。不靠环境变量传递——工作台起子进程不传 env。
        """
        from playwright.sync_api import sync_playwright
        self.p = sync_playwright().start()
        self._cdp = (os.environ.get('HANA_CDP') or '').strip() or 'http://127.0.0.1:9222'
        self._by_cdp = False
        try:
            _br = self.p.chromium.connect_over_cdp(self._cdp)
            self.ctx = _br.contexts[0] if _br.contexts else _br.new_context()
            self._by_cdp = True
            print("[mtop] 已连 CDP %s（复用现有窗口）" % self._cdp)
        except Exception as _e:
            print("[mtop] CDP 不可用（%s），退回自起浏览器" % str(_e)[:50])
            try:
                self.ctx = self.p.chromium.launch_persistent_context(
                    self.PROFILE, executable_path=self.CHROME, headless=headless,
                    viewport={"width": 1400, "height": 900}, locale="zh-CN",
                    ignore_default_args=['--password-store=basic', '--use-mock-keychain'],
                    args=["--disable-blink-features=AutomationControlled"])
            except Exception:
                # 起不来必须把 playwright 收干净——否则同进程后续 sync_playwright() 会被污染
                try:
                    self.p.stop()
                except Exception:
                    pass
                raise
        # CDP 模式下别碰梓帆在看的标签，新开一个
        if self._by_cdp:
            self.page = self.ctx.new_page()
        else:
            self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.goto(SELL_URL, timeout=60000, wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)
        self._login()
        self.page.wait_for_timeout(2500)
        self._refresh_token()
        print("[mtop] 就绪（token=%s...）" % self.token[:16])
        return self

    def _login(self):
        """内嵌登录：检测登录框 → 填账号密码 → 登录（已登录直接过）"""
        try:
            if self.page.locator("span.next-btn-helper:has-text('发布商品')").count() > 0:
                print("[登录] 已登录（发布商品按钮在）")
                return
        except Exception:
            pass
        for _ in range(2):
            need, lf = False, None
            for fr in self.page.frames:
                try:
                    if fr.locator("#fm-login-id").count() > 0:
                        need, lf = True, fr
                        break
                except Exception:
                    continue
            if not need:
                print("[登录] 已登录")
                return
            try:
                lf.fill("#fm-login-id", ACCOUNT)
                lf.fill("#fm-login-password", PASSWORD)
                try:
                    cb = lf.locator("input[type='checkbox']").first
                    if cb.count() > 0 and not cb.is_checked():
                        cb.click()
                except Exception:
                    pass
                lf.click("button:has-text('登录')")
                print("[登录] 已填账号密码并登录")
                time.sleep(4)
                try:
                    self.page.goto(SELL_URL, timeout=60000, wait_until="domcontentloaded")
                    self.page.wait_for_timeout(2000)
                except Exception:
                    pass
                return
            except Exception as e:
                print("[登录] 异常:", str(e)[:60])
                return

    def _refresh_token(self):
        for c in self.ctx.cookies():
            if c["name"] == "_m_h5_tk":
                self.token = c["value"]
                break

    def _sign(self, t, data_str):
        tk = self.token.split("_")[0] if self.token else ""
        return hashlib.md5(("%s&%s&%s&%s" % (tk, t, APPKEY, data_str)).encode("utf-8")).hexdigest()

    def call(self, api, data_obj, v="1.0"):
        """页面内 fetch 直调 MTOP——返回完整响应 dict（绕过 RGV587）"""
        t = str(int(time.time() * 1000))
        data_str = json.dumps(data_obj, separators=(",", ":"), ensure_ascii=False)
        sign = self._sign(t, data_str)
        qs = urllib.parse.urlencode({
            "jsv": "2.6.1", "appKey": APPKEY, "t": t, "sign": sign,
            "api": api, "v": v, "ttid": "11320@taobao_WEB_9.9.99",
            "type": "originaljson", "dataType": "json"})
        url = H5API.format(api=api, v=v) + "?" + qs
        body = urllib.parse.urlencode({"data": data_str})
        self._refresh_token()
        if not self.token:
            raise RuntimeError("无 _m_h5_tk——需重新登录")
        resp = self.page.evaluate(
            """async (a) => {
                const r = await fetch(a.u, {method: 'POST', credentials: 'include',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: a.b});
                return await r.text();
            }""", {"u": url, "b": body})
        return json.loads(resp)

    def search_items(self, kw, page=1, page_size=20):
        """商品管理搜索：料号/标题 → [{itemId,title,price,link}]"""
        data = {
            "url": "/taobao/manager/table.htm",
            "jsonBody": json.dumps({"tab": "on_sale", "pagination": {"current": page, "pageSize": page_size},
                                    "filtertab": "", "filter": {"queryTitle": kw}, "table": {}},
                                   separators=(",", ":"), ensure_ascii=False),
        }
        d = self.call("mtop.taobao.sell.pc.manage.async", data)
        ret = (d.get("ret") or [""])[0]
        if "SUCCESS" not in ret:
            return [], ret
        inner = json.loads(d["data"]["result"])
        rows = (inner.get("data") or {}).get("table", {}).get("dataSource") or []
        out = []
        for r in rows:
            desc = r.get("itemDesc") or {}
            title = ""
            for x in desc.get("desc") or []:
                if x.get("text"):
                    title = x["text"]
                    break
            price = ""
            pv = r.get("managerPrice") or {}
            if isinstance(pv, dict):
                price = pv.get("currentPrice", "")
            out.append({"itemId": str(r.get("itemId", "")), "title": str(title),
                        "price": str(price), "link": "https://item.taobao.com/item.htm?id=%s" % r.get("itemId", "")})
        return out, "SUCCESS"

    def check_dual(self, kw):
        """双百检测：搜索商品 → diagnoseInfoV3.scoreLabel == '流量加速中' 即双百
        返回 [{itemId,title,dual,basicScore,scoreLabel}]——未搜到返回 []"""
        data = {
            "url": "/taobao/manager/table.htm",
            "jsonBody": json.dumps({"tab": "on_sale", "pagination": {"current": 1, "pageSize": 20},
                                    "filtertab": "", "filter": {"queryTitle": kw}, "table": {}},
                                   separators=(",", ":"), ensure_ascii=False),
        }
        d = self.call("mtop.taobao.sell.pc.manage.async", data)
        ret = (d.get("ret") or [""])[0]
        if "SUCCESS" not in ret:
            return [], ret
        inner = json.loads(d["data"]["result"])
        rows = (inner.get("data") or {}).get("table", {}).get("dataSource") or []
        out = []
        for r in rows:
            diag = r.get("diagnoseInfoV3") or {}
            desc = r.get("itemDesc") or {}
            title = ""
            for x in (desc.get("desc") or []):
                if x.get("text"):
                    title = x["text"]
                    break
            out.append({
                "itemId": str(r.get("itemId", "")), "title": str(title),
                "dual": diag.get("scoreLabel") == "流量加速中",
                "basicScore": diag.get("basicScore"),
                "scoreLabel": diag.get("scoreLabel"),
            })
        return out, "SUCCESS"

    def search_and_dual(self, kw, page_size=20):
        """一次搜索同时拿到：搜到的商品 + 双百信息。
        search_items 与 check_dual 打的是同一个接口、同一份数据，分两次是白花一次请求——
        淘宝正是按“一段时间内的请求量”拦人的。_classify_one 依赖这个方法。"""
        data = {
            "url": "/taobao/manager/table.htm",
            "jsonBody": json.dumps({"tab": "on_sale", "pagination": {"current": 1, "pageSize": page_size},
                                    "filtertab": "", "filter": {"queryTitle": kw}, "table": {}},
                                   separators=(",", ":"), ensure_ascii=False),
        }
        d = self.call("mtop.taobao.sell.pc.manage.async", data)
        ret = (d.get("ret") or [""])[0]
        if "SUCCESS" not in ret:
            return [], ret
        inner = json.loads(d["data"]["result"])
        rows = (inner.get("data") or {}).get("table", {}).get("dataSource") or []
        out = []
        for r in rows:
            diag = r.get("diagnoseInfoV3") or {}
            desc = r.get("itemDesc") or {}
            title = ""
            for x in (desc.get("desc") or []):
                if x.get("text"):
                    title = x["text"]
                    break
            price = ""
            pv = r.get("managerPrice") or {}
            if isinstance(pv, dict):
                price = pv.get("currentPrice", "")
            out.append({
                "itemId": str(r.get("itemId", "")), "title": str(title), "price": str(price),
                "link": "https://item.taobao.com/item.htm?id=%s" % r.get("itemId", ""),
                "dual": diag.get("scoreLabel") == "流量加速中",
                "basicScore": diag.get("basicScore"),
                "scoreLabel": diag.get("scoreLabel"),
            })
        return out, "SUCCESS"

    def revive(self):
        """被拦/页面被关时救活。
        CDP 模式下只重载自己开的标签页——**绝不 close() 梓帆的浏览器**（那是他在用的窗口）。"""
        if getattr(self, "_by_cdp", False):
            try:
                self.page.goto(SELL_URL, timeout=60000, wait_until="domcontentloaded")
                self.page.wait_for_timeout(2500)
                self._refresh_token()
                return bool(self.token)
            except Exception:
                return False
        try:
            self.page.goto(SELL_URL, timeout=60000, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            self._login()
            self.page.wait_for_timeout(1500)
            self._refresh_token()
            return bool(self.token)
        except Exception:
            pass
        # 页面/context 真没了才整体重来
        try:
            self.close()
        except Exception:
            pass
        try:
            self.open(headless=True)
            return bool(self.token)
        except Exception:
            return False

    def close(self):
        # CDP 模式下 ctx 是梓帆的浏览器——关它等于关他的窗口，只关自己开的标签
        try:
            if getattr(self, '_by_cdp', False):
                if self.page:
                    self.page.close()
            elif self.ctx:
                self.ctx.close()
        except Exception:
            pass
        try:
            if self.p:
                self.p.stop()
        except Exception:
            pass


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else "39-01-2060"
    mc = MtopClient().open()
    items, ret = mc.search_items(kw)
    print("ret:", ret)
    for it in items:
        print("  itemId:", it["itemId"], "|", it["title"][:40], "|", it["price"])
    mc.close()
