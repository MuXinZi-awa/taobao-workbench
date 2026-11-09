# -*- coding: utf-8 -*-
"""全店在售全量枚举（只读）——给「店铺分流」当唯一可信数据源。

为什么必须全量枚举、不许按料号搜：
  卖家中心的 queryTitle 搜索会漏掉明明在售的商品（1016 实测：买家页能打开、
  编辑页双百，搜索却搜不到），按搜索判「本店没有」→ 新建出重复品。

为什么是接口翻页 + 慢节奏（1026 实测三条）：
  1) `mtop.taobao.sell.pc.manage.async` 一次只肯给 50 行：pageSize=100/200 虽然回
     SUCCESS，但 dataSource 是空数组（total 仍报 8072）——先拿 50 当唯一可用值。
  2) 一上来 1.7 请求/秒跑到第 22 页就被限流“晾着不回”，fetch 永不 settle →
     第一版挂死 90 分钟。所以：每页之间睡 3 秒出头、每 20 页歇一次、每页给硬超时。
  3) 分页循环把「失败」和「到底」混成一个 break，是 7738→3050 那次的根因
     （运维经验 59）。这里失败**跳页继续并记账**，最后跟 total 对账，低于 98%
     标 complete=0，下游拒绝据此判「没有」。

产出：
  runtime/onsale_enum.json      {meta:{...}, items:[{id,title,basic,spe,label,dual,diag}]}
  runtime/onsale_progress.json  逐页进度（--resume 续跑用）
  runtime/onsale_run.log        本脚本自身输出（实时，父进程堵着也能看）
  <数据目录>/三店在售全量_MMDD.csv
用法：python _onsale_enum.py [member_id] [pageSize=50] [--resume]
"""
import csv
import io
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.path.dirname(os.path.abspath(__file__))
RT = os.path.join(BASE, "runtime")
OUT = os.path.join(RT, "onsale_enum.json")
PROG = os.path.join(RT, "onsale_progress.json")
RUNLOG = os.path.join(RT, "onsale_run.log")
DATA = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\数据"

PAGE_SIZE_MAX = 50        # 接口实测上限：>50 回 SUCCESS 但给空数组
EVAL_MS = 30000           # 单次请求硬上限（没它会被“晾着不回”挂死）
PAGE_TRIES = 2            # 每页试几次（多了只是在限流里打转）
CONSEC_LIMIT = 2          # 连续几页取不到就停（停下比挂死好）
FRESH_EVERY = 20          # 每这么多页换新 page + 歇一会儿
BREAK_SEC = 25
PACE = (2.6, 4.0)         # 页间随机延迟——1.7 请求/秒会被拦

RISK = ("USER_VALIDATE", "RGV587", "FAIL_SYS_ILLEGAL_ACCESS",
        "x5sec", "punish")
# 注：FAIL_SYS_SESSION_EXPIRED 不算风控——它是令牌轮换/限流的常见表现，
# 换页重取一次多半就好（死当风控只会把可恢复的问题误判成封杀）。


class _Tee(object):
    def __init__(self, *streams):
        self.s = streams

    def write(self, x):
        for f in self.s:
            try:
                f.write(x)
                f.flush()
            except Exception:
                pass
        return len(x)

    def flush(self):
        for f in self.s:
            try:
                f.flush()
            except Exception:
                pass

    def reconfigure(self, **kw):
        pass


def _num(v):
    try:
        return float(v)
    except Exception:
        return None


def _title(r):
    desc = r.get("itemDesc") or {}
    for x in (desc.get("desc") or []):
        if x.get("text"):
            return str(x["text"])
    return ""


def _load_prog():
    try:
        d = json.load(io.open(PROG, encoding="utf-8"))
        if d.get("items"):
            return d
    except Exception:
        pass
    return None


def _save_prog(done_page, declared, items, shop):
    try:
        json.dump({"done_page": done_page, "declared": declared, "shop": shop,
                   "items": items, "ts": time.time()},
                  io.open(PROG, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def main():
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    resume = "--resume" in sys.argv
    arg_mid = pos[0].strip() if len(pos) > 0 else ""
    page_size = int(pos[1]) if len(pos) > 1 else 50
    page_size = max(10, min(PAGE_SIZE_MAX, page_size))
    # 一次调用最多跑多少页：淘宝大约 20 来次请求就把会话打成 SESSION_EXPIRED，
    # 所以按批跑（每批存进度，下一批 --resume），而不是硬蕎一整批。
    max_pages = int(pos[2]) if len(pos) > 2 else 999

    os.makedirs(RT, exist_ok=True)
    _fh = io.open(RUNLOG, "a", encoding="utf-8")
    _fh.write("\n===== %s 全量枚举(API) 开始（mid=%s ps=%d resume=%s）=====\n"
              % (time.strftime("%m-%d %H:%M:%S"), arg_mid or "-", page_size, resume))
    _fh.flush()
    sys.stdout = _Tee(_fh, sys.stdout)

    from _mtop_api import MtopClient  # noqa: E402

    _lk = None
    mc = None
    items, seen = [], set()
    start_page, declared_prev = 1, 0
    meta = {"ok": 0, "member_id": arg_mid, "shop": "", "declared": 0, "pages": 0,
            "count": 0, "complete": 0, "fails": [], "error": "", "mode": "api",
            "page_size": page_size, "ts": time.time()}
    try:
        import browser_lock
        _lk = browser_lock.guard("三店在售全量枚举", timeout=1800)
        _lk.__enter__()
        print("[锁] 已取得浏览器锁")
    except Exception as e:
        meta["error"] = "等浏览器锁失败: %s" % str(e)[:100]
        _dump(meta, items)
        return 1

    try:
        mc = MtopClient()
        try:
            mc.open(headless=True)
        except Exception as e:
            _m = str(e)[:200]
            if any(k in _m for k in ("has been closed", "ProcessSingleton", "SingletonLock")):
                _m = "浏览器起不来——chrome_profile 正被别的 Chrome 占着（孤儿浏览器？先关掉再试）"
            meta["error"] = "Mtop 打开失败: %s" % _m
            _dump(meta, items)
            return 1
        if not mc.token:
            meta["error"] = "没有登录态（拿不到 _m_h5_tk）——请在工作台 设置→浏览器 引导登录后重试"
            _dump(meta, items)
            return 1
        try:
            mc.page.set_default_timeout(EVAL_MS)
        except Exception:
            pass

        try:
            import shop_guard
            meta["user_id"] = shop_guard.read_member_id(mc.page) or ""
            meta["shop"] = (shop_guard.read_shop_name(mc.page) or "").strip()
        except Exception as e:
            print("[店铺] 身份读取失败（%s）——继续" % str(e)[:60])
        print("[店铺] 店铺名=%s user_id=%s（连接库 member_id=%s）"
              % (meta.get("shop") or "?", meta.get("user_id") or "?", arg_mid or "?"))

        if resume:
            p = _load_prog()
            if p:
                items = p.get("items") or []
                seen = {x["id"] for x in items}
                start_page = int(p.get("done_page") or 0) + 1
                declared_prev = int(p.get("declared") or 0)
                print("[续跑] 已有 %d 件 / 从第 %d 页继续（上次声明 %d）"
                      % (len(items), start_page, declared_prev))
            else:
                print("[续跑] 没有可用进度，从头开始")

        def fresh(reason="换页"):
            try:
                mc.page.close()
            except Exception:
                pass
            mc.page = mc.ctx.new_page()
            mc.page.set_default_timeout(EVAL_MS)
            mc.page.goto("https://myseller.taobao.com/home.htm/SellManage/on_sale",
                         timeout=60000, wait_until="domcontentloaded")
            mc.page.wait_for_timeout(2500)
            mc._refresh_token()
            print("[全量] %s：已重开页面（token=%s）" % (reason, (mc.token or "")[:12]))

        def call(data_obj):
            """带硬超时的 MTOP 直调：25s 不回就 abort（否则被晾着时永久挂死）"""
            import urllib.parse
            t = str(int(time.time() * 1000))
            data_str = json.dumps(data_obj, separators=(",", ":"), ensure_ascii=False)
            sign = mc._sign(t, data_str)
            qs = urllib.parse.urlencode({
                "jsv": "2.6.1", "appKey": "12574478", "t": t, "sign": sign,
                "api": "mtop.taobao.sell.pc.manage.async", "v": "1.0",
                "ttid": "11320@taobao_WEB_9.9.99", "type": "originaljson",
                "dataType": "json"})
            url = ("https://h5api.m.taobao.com/h5/mtop.taobao.sell.pc.manage.async/1.0/?"
                   + qs)
            body = urllib.parse.urlencode({"data": data_str})
            mc._refresh_token()
            if not mc.token:
                raise RuntimeError("无 _m_h5_tk——需重新登录")
            txt = mc.page.evaluate(
                """async (a) => {
                    const ctl = new AbortController();
                    const tid = setTimeout(() => ctl.abort(), a.ms);
                    try {
                        const r = await fetch(a.u, {method: 'POST', credentials: 'include',
                            signal: ctl.signal,
                            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                            body: a.b});
                        return await r.text();
                    } finally { clearTimeout(tid); }
                }""", {"u": url, "b": body, "ms": EVAL_MS - 5000})
            return json.loads(txt)

        def fetch(pg):
            data = {"url": "/taobao/manager/table.htm",
                    "jsonBody": json.dumps(
                        {"tab": "on_sale",
                         "pagination": {"current": pg, "pageSize": page_size},
                         "filtertab": "", "filter": {"queryTitle": ""}, "table": {}},
                        separators=(",", ":"), ensure_ascii=False)}
            d = call(data)
            ret = (d.get("ret") or [""])[0]
            if "SUCCESS" not in ret:
                raise RuntimeError("RET " + str(ret)[:90])
            inner = json.loads(d["data"]["result"])
            dd = inner.get("data") or {}
            pag = dd.get("pagination") or {}
            rows = ((dd.get("table") or {}).get("dataSource")) or []
            return pag, rows

        declared, pg, fails, consec = declared_prev, start_page, [], 0
        _base = start_page
        while pg <= 400:
            if pg > _base and (pg - _base) % FRESH_EVERY == 0:
                print("[全量] 已跑 %d 页，休息 %d 秒后换页" % (pg - _base, BREAK_SEC))
                time.sleep(BREAK_SEC)
                try:
                    fresh("第 %d 页前" % pg)
                except Exception as e:
                    print("[全量] 重开页面失败：%s" % str(e)[:80])
            pag, rows, ok = {}, [], False
            for attempt in range(PAGE_TRIES):
                try:
                    pag, rows = fetch(pg)
                    ok = True
                    break
                except Exception as e:
                    msg = str(e)[:130]
                    print("[全量] 第 %d 页第 %d 次失败：%s" % (pg, attempt + 1, msg))
                    if any(k in msg for k in RISK):
                        meta["error"] = "撞风控/验证码：%s——停下，别硬撞" % msg
                        meta.update({"declared": declared, "count": len(items), "pages": pg})
                        _dump(meta, items)
                        print("⛔ " + meta["error"])
                        return 9
                    time.sleep(3 + attempt * 4)
                    if attempt == 1:
                        try:
                            fresh("第 %d 页重试前" % pg)
                        except Exception as e2:
                            print("[全量] 重建页面失败：%s" % str(e2)[:80])
            if ok and not rows:
                # SUCCESS 给空数组：可能是限流软拦，也可能是真的到底（用 total 判）
                try:
                    _t = int((pag or {}).get("total") or declared or 0)
                except Exception:
                    _t = declared
                if _t and (pg - 1) * page_size < _t:
                    ok = False
                    print("[全量] 第 %d 页回 SUCCESS 但空数组（声明 %d 件）——按失败处理"
                          % (pg, _t))
            if not ok:
                fails.append(pg)
                consec += 1
                print("[全量] 第 %d 页取不到，跳过——不当成到底" % pg)
                if consec >= CONSEC_LIMIT:
                    meta["error"] = ("连续 %d 页取不到（疑似限流）——停下，可用 --resume 过一阵再续"
                                     % consec)
                    break
                pg += 1
                continue
            consec = 0
            try:
                declared = int(pag.get("total") or declared or 0)
            except Exception:
                pass
            new = 0
            for r in rows:
                tid = str(r.get("itemId") or "")
                if not tid or tid in seen:
                    continue
                seen.add(tid)
                new += 1
                diag = r.get("diagnoseInfoV3") or {}
                label = str(diag.get("scoreLabel") or "")
                spe = None
                for _k in ("advantageScore", "supportScore", "fuyouScore", "premiumScore"):
                    if diag.get(_k) not in (None, ""):
                        spe = _num(diag.get(_k))
                        break
                items.append({"id": tid, "title": _title(r),
                              "basic": _num(diag.get("basicScore")), "spe": spe,
                              "label": label, "dual": (label == "流量加速中"),
                              "diag": diag})
            _save_prog(pg, declared, items, meta.get("shop") or "")
            if pg % 5 == 0 or pg == 1:
                print("[全量] 第 %d 页完成，本页 %d 新增 %d，累计 %d / 声明 %d，失败页 %d"
                      % (pg, len(rows), new, len(items), declared, len(fails)))
            if len(rows) < page_size:
                break
            pg += 1
            if (pg - _base) >= max_pages:
                meta.update({"declared": declared, "count": len(items), "pages": pg,
                             "partial": 1, "count_fails": len(fails), "fails": fails})
                print("[全量] 本批 %d 页跑满（上限 %d），先存进度：已 %d 件 / 声明 %d"
                      % (pg - _base, max_pages, len(items), declared))
                _dump(meta, items)
                return 0
            time.sleep(PACE[0] + random.uniform(0, PACE[1] - PACE[0]))

        meta.update({"declared": declared, "pages": pg, "count": len(items),
                     "count_fails": len(fails), "fails": fails})
        if not meta["error"]:
            if not declared:
                meta["ok"] = 1
                meta["complete"] = 0
                meta["error"] = "接口没给 total——无法对账，清单按「不完整」处理"
            elif len(items) >= declared * 0.98:
                meta["ok"] = 1
                meta["complete"] = 1
            else:
                meta["complete"] = 0
                meta["error"] = ("清单不完整：抓到 %d / 声明 %d（%.0f%%）——拒绝据此判「没有」"
                                 % (len(items), declared, 100.0 * len(items) / declared))
        print("[全量] 完成：抓到 %d / 声明 %d，页 %d，失败页 %s，complete=%s"
              % (len(items), declared, pg, fails or "无", meta["complete"]))
        if meta.get("error"):
            print("[全量] ⚠ " + meta["error"])
        _dump(meta, items)
        return 0 if meta["ok"] else 1
    finally:
        try:
            if mc is not None:
                mc.close()
        except Exception:
            pass
        if _lk is not None:
            try:
                _lk.__exit__(None, None, None)
            except Exception:
                pass


def _dump(meta, items):
    os.makedirs(RT, exist_ok=True)
    try:
        json.dump({"meta": meta, "items": items}, io.open(OUT, "w", encoding="utf-8"),
                  ensure_ascii=False)
    except Exception as e:
        print("[落盘] JSON 写失败：%s" % e)
    try:
        os.makedirs(DATA, exist_ok=True)
        fp = os.path.join(DATA, "三店在售全量_%s.csv" % time.strftime("%m%d"))
        with io.open(fp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["商品id", "标题", "基础分", "扶优分", "质量分标签", "流量加速中"])
            for x in items:
                w.writerow([x["id"], x["title"],
                            "" if x.get("basic") is None else "%g" % x["basic"],
                            "" if x.get("spe") is None else "%g" % x["spe"],
                            x.get("label") or "", x.get("dual")])
        print("[落盘] %s（%d 行）" % (fp, len(items)))
    except Exception as e:
        print("[落盘] CSV 写失败：%s" % e)
    print("RESULT " + json.dumps(
        {"ok": meta["ok"], "member_id": meta["member_id"], "shop": meta["shop"],
         "declared": meta["declared"], "count": meta["count"],
         "complete": meta["complete"], "error": meta["error"]}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
