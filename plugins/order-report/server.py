# -*- coding: utf-8 -*-
"""订单看板 · 后端：读「已卖出宝贝」页的状态分布，只读不改单。

设计要点：
- 采集放子进程跑（playwright 起浏览器，不污染插件进程）
- 只读：一个按钮都不点（发货/改价/退款/翻页/导出/搜索一律不碰），只读 DOM 与只读查询接口
- 浏览器锁与上品/推广共用一把，避免并发互顶

踩过的坑（别再改回去）：
1. 子进程**不能用 capture_output/管道**接 playwright 的输出。
   Chrome 用 --remote-debugging-pipe 起，父子进程的 stdio 被管道接管时
   Chrome 会在启动阶段直接退出（exitCode=21），playwright 报
   "Target page, context or browser has been closed"，看起来像锁/登录问题，其实不是。
   改为写临时文件再读，问题消失。
2. **headless=True 会被识别**。headless 起浏览器直接开订单页，必然落 loginmyseller；
   headful 才是正常路径。要 headless 就等于放弃这个插件。
3. **判断登录态不能只看 title，也不能只看第一次落地 URL**。
   title 在未登录时是「千牛工作台 - 商家一站式经营阵地」，不含「登录」二字；
   而 goto 之后头几秒 URL 常常短暂落在 loginmyseller.taobao.com，随后自己跳回订单页。
   所以要「等到真实 tab 文案出现」才叫成功，中途的 login 域名只是过程，不是结论。
4. **冷启动直接深链订单页会被登录墙拦**，同一个 page 里先访问商品页
   （SellManage/on_sale，上品天天用、历史上通）把 session 串起来，再进订单页，
   通过率明显高。这就是 warm-up 那两步存在的原因。
5. 页面上的状态计数**只有非零才显示**（「待发货 1」），没有数字的 tab 不能当 0 读，
   这里如实标 null，面板显示「—」。
6. 状态 tab 与「近3个月 / 3个月前」这种时间窗 tab 混在同一个 li.next-tabs-tab 里，要按名字剔除。
7. **exitCode=21 有两种成因，别只看一种**。除了坑 #1 的 stdio 管道，
   另一个是「这个 profile 已经有一个 chrome 在用」——此时 playwright 起的进程
   一启动就退（exitCode=21），日志里只有 <launched> pid=… <process did exit: exitCode=21>，
   看起来跟坑 #1 一模一样。**判断方法**：先看 browser_lock.log 最近谁拿了锁、
   `Win32_Process` 里有没有 chrome.exe 带 `--user-data-dir=<本 profile>`。
   有就说明不是代码问题，是**锁外有人**（别的批次没走 guard 直接起了浏览器）——
   这时要排队等，不能为了自测去 taskkill：杀掉的是别人手上的那台浏览器。
8. 读订单列表**不靠猜列下标**：先用表头文字建字段→列映射，映射不到再用整行正则兜底，
   兜底也拿不到的字段留空并在 meta 里报出来（面板显示「读不到」），不编值。
   一行多商品（rowspan 合并单元格）时，订单号/时间/买家向下继承。
9. **订单列表的真实结构**（2026-10-20 实测）：表头只有 宝贝/单价/数量/履约售后状态/交易状态/
   实收款/操作 7 列，**订单号、创建时间、买家昵称都不在表头里**，而在每个订单的
   `tr.next-table-group-header` 行；一个订单 = 1 个 group-header + N 个商品行，每个 group 是
   独立一张无 thead 的 table。所以不能按「一张表一行一单」读，要按 group-header 分组。
   另外「交易状态」格子里塞着按钮文字、「实收款」格子里塞着快递费和支付方式，取到原文还得再切一刀。
10. **页面有两个「下一页」按钮**：顶部那个属于别的组件、永远 disabled；订单列表的在页面最底部。
    `query_selector` 只会拿到第一个（禁用的），必须 `query_selector_all` 后挑未禁用且可见的那个。
11. **订单详情在 qn.taobao.com 域**（从列表行的「详情」链接里读到的），不是 myseller：
    `https://qn.taobao.com/home.htm/trade-platform/tp/detail?bizOrderId=<订单号>`。
    详情页是「标签：值」的文本流，而且**标签跟值经常不在同一行**（“收货地址：”一行、值在下一行），
    「商家实收」前面还会先出现一行「包含快递:￥18.00」——实收要取窗口里最后一个 ￥，不能取第一个。
12. 买家昵称页面上是 `e**` / `t**` 这种脱敏名，**照实显示，不展开不猜**；收货人/电话/地址同理。
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile

import sys as _sys

_d = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_d, "paths.py")) and os.path.dirname(_d) != _d:
    _d = os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import paths

BASE = os.path.dirname(os.path.abspath(__file__))
TG = paths.TG_ROOT
RT = paths.TG_PYTHON
LOG = os.path.join(paths.TG_RUNTIME, "order_report.log")

# 页面上的状态词：采集端与面板排序都认这一份，避免两处各写一套
STATUS_WORDS = ["待付款", "确认中", "待发货", "即将超时", "发货已超时",
                "已发货", "退款中", "已成功", "已完成", "已关闭"]

COLLECT = r'''
# -*- coding: utf-8 -*-
import json, re, sys, os, datetime
sys.path.insert(0, r"__TG__")
from browser_lock import guard
from playwright.sync_api import sync_playwright

PROFILE = os.path.join(r"__TG__", "chrome_profile")
CHROME = r"__CHROME__"
DB = os.path.join(r"__TG__", "runtime", "report.db")
ON_SALE = "https://myseller.taobao.com/home.htm/SellManage/on_sale"
SOLD = "https://myseller.taobao.com/home.htm/trade-platform/tp/sold"
API = "https://trade.taobao.com/trade/itemlist/asyncSold.htm"

WANT = (sys.argv[1] if len(sys.argv) > 1 else "current").strip() or "current"

STATUS_WORDS = ["待付款", "确认中", "待发货", "即将超时", "发货已超时",
                "已发货", "退款中", "已成功", "已完成", "已关闭"]
# 同一排 li.next-tabs-tab 里还混着「近3个月 / 3个月前」这种时间窗 tab，
# 不单独列黑名单：只认 STATUS_WORDS 白名单，不在名单里的直接丢。

RESULT = {"ok": False, "logged": False, "needLogin": False,
          "tabs": [], "counts": [], "url": "", "title": "", "bodyHead": "",
          "orderLine": "", "date": {"requested": WANT, "applied": False,
                                    "mode": "range-picker", "note": "", "total": None},
          "dateProbe": [], "warmup": {"url": "", "ok": False},
          "stages": [], "note": "", "fetchedAt": "",
          "orders": [], "orderMeta": {}, "ordersWanted": 50}


def stage(x):
    RESULT["stages"].append("%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), x))


def body_text(pg):
    try:
        return pg.evaluate("() => (document.body.innerText || '')")
    except Exception:
        return ""


def wait_tabs(pg, budget):
    """等到状态 tab 真正出现（不是看到 login 域名就下结论）。"""
    t0 = datetime.datetime.now()
    while (datetime.datetime.now() - t0).total_seconds() < budget:
        pg.wait_for_timeout(1500)
        if "login" in (pg.url or "").lower():
            continue
        try:
            n = pg.evaluate("() => document.querySelectorAll('li.next-tabs-tab').length")
        except Exception:
            n = 0
        if n >= 5:
            return True, n, (datetime.datetime.now() - t0).total_seconds()
    return False, 0, (datetime.datetime.now() - t0).total_seconds()


def read_tabs(pg):
    """读状态 tab：名字 + 计数。计数只有非零才显示，没有数字 → null（不当 0）。"""
    raw = pg.evaluate("""() => [...document.querySelectorAll('li.next-tabs-tab')]
        .map(li => ({txt: (li.innerText || '').replace(/\\s+/g, ' ').trim(),
                     active: /active/.test(li.className || '')}))""")
    out, seen = [], set()
    for it in raw or []:
        t = (it.get("txt") or "").strip()
        if not t:
            continue
        name = None
        for w in STATUS_WORDS:
            if t.startswith(w):
                name = w
                break
        if not name or name in seen:
            continue
        seen.add(name)
        rest = t[len(name):].strip()
        m = re.match(r"^[（(]?\s*(\d+)\s*[)）]?$", rest) if rest else None
        out.append({"name": name, "n": int(m.group(1)) if m else None,
                    "active": bool(it.get("active"))})
    return out, [it.get("txt") for it in (raw or [])]


def probe_dates(pg):
    """把真实的日期控件报出来（只读，不点）。返回表单项 label + 输入框占位符。"""
    return pg.evaluate("""() => {
      const out = [];
      document.querySelectorAll('.next-form-item').forEach(fi => {
        const lb = fi.querySelector('label');
        const lt = lb ? (lb.innerText || '').replace(/\\s+/g,' ').trim() : '';
        if (!lt || !/时间|日期/.test(lt)) return;
        const ins = [];
        fi.querySelectorAll('input').forEach(i => {
          const r = i.getBoundingClientRect();
          ins.push({ph: i.getAttribute('placeholder') || '', ro: !!i.readOnly,
                    vis: r.width > 2, val: i.value || ''});
        });
        out.push({label: lt, inputs: ins});
      });
      return out.slice(0, 10);
    }""")


FETCH_JS = """
async ({api, form}) => {
  const body = Object.entries(form).map(([k, v]) =>
      encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');
  const r = await fetch(api, {method: 'POST', credentials: 'include',
      headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest'}, body});
  const t = await r.text();
  let j = null;
  try { j = JSON.parse(t); } catch (e) {}
  if (!j) return {ok: false, status: r.status, len: t.length, head: t.slice(0, 160)};
  const pg = j.page || {};
  const pgFlat = {};
  for (const [k, v] of Object.entries(pg)) {
    pgFlat[k] = (v === null || typeof v !== 'object') ? v : '<obj>';
  }
  const orders = Array.isArray(j.mainOrders) ? j.mainOrders : null;
  return {ok: true, status: r.status, topKeys: Object.keys(j).slice(0, 40),
          pageFlat: pgFlat, pageKeys: Object.keys(pg),
          ordersLen: orders ? orders.length : null,
          queryEcho: j.query ? {db: j.query.dateBegin, de: j.query.dateEnd,
                                ps: j.query.pageSize, pn: j.query.pageNum,
                                tab: j.query.tabCode, os: j.query.orderStatus} : null,
          tabCodes: (j.tabs || []).map(x => x.code + '=' + x.text)};
}
"""


# ==== 订单列表读取 ====
# 页面结构（2026-10-20 实测，别凭想当然改回去）：
#   · 表头只有 7 列：宝贝 | 单价 | 数量 | 履约/售后状态 | 交易状态 | 实收款 | 操作
#   · **订单号 / 创建时间 / 买家昵称 不在表头里**，在每个订单的 tr.next-table-group-header 行
#   · 一个订单 = 1 个 group-header 行 + N 个商品行；每个 group 是独立的一张 table（无 thead）
#   · 「交易状态」格子里塞着按钮文字（卖家已发货 补发货 详情 打单 延长收货时间）
#   · 「实收款」格子里塞着快递费与支付方式（￥32.00 (含快递:￥18.00) 微信支付）
#   所以按列取到原文后还得再切一刀，不能整格当字段。
ORDERS_JS = """
() => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const hdr = [...document.querySelectorAll('table')].find(t => {
    const h = [...t.querySelectorAll('thead th')].map(x => clean(x.innerText)).join('|');
    return h.indexOf('宝贝') >= 0 && h.indexOf('状态') >= 0;
  });
  const head = hdr ? [...hdr.querySelectorAll('thead th')].map(x => clean(x.innerText)) : [];
  const orders = [];
  let cur = null;
  [...document.querySelectorAll('table')].filter(t => t !== hdr).forEach(t => {
    [...t.querySelectorAll('tbody tr')].forEach(tr => {
      const cls = tr.className || '';
      if (/next-table-group-header/.test(cls)) {
        cur = {headTxt: clean(tr.innerText), rows: []};
        orders.push(cur);
        return;
      }
      const cells = [...tr.children].map(td => clean(td.innerText));
      if (!cells.length || !cur) return;
      const links = [...tr.querySelectorAll('a')].map(a => ({t: clean(a.innerText),
                                                            h: a.getAttribute('href') || ''}));
      cur.rows.push({cells: cells, links: links.slice(0, 12)});
    });
  });
  return {head: head, orders: orders};
}
"""

# 列表可能懒加载：把所有能滚的容器都滚到底，再数一遍
SCROLL_JS = """
() => {
  let n = 0;
  document.querySelectorAll('*').forEach(e => {
    if (e.scrollHeight > e.clientHeight + 60) { e.scrollTop = e.scrollHeight; n++; }
  });
  window.scrollTo(0, document.body.scrollHeight);
  return n;
}
"""

# 下一页按钮的候选选择器（next 组件的类名组合不止一种）
# 页面上有**两个**「下一页」按钮：顶上一个（别的组件，永远 disabled）、列表底下一个（真的）。
# 所以不能 query_selector 拿第一个，要全拿回来、挑可用的那个。
NEXT_SELS = [".next-pagination-item.next-next",
             "button.next-pagination-item.next-next",
             ".next-pagination button.next-next",
             ".next-pagination .next-next",
             "li.next-next"]

TRADE_WORDS = ["等待买家付款", "买家已付款", "卖家已发货", "部分发货", "买家已确认收货",
               "交易成功", "交易关闭", "退款中", "退款成功", "售后中", "已退款"]
FULFILL_WORDS = ["发货已超时", "发货未超时", "即将超时", "未确认收货", "已确认收货",
                 "退款中", "售后中", "退款成功"]
PAY_WORDS = ["微信支付", "支付宝", "花呗", "信用卡", "余额宝", "网银", "货到付款", "余额支付"]


def _pick_word(text, words):
    """从一段混杂文字里挑状态词：按**出现位置**取最靠前的那个，而不是按列表顺序。"""
    best, pos = "", 10 ** 6
    t = text or ""
    for w in words:
        i = t.find(w)
        if i >= 0 and i < pos:
            best, pos = w, i
    return best


def _money(s):
    m = re.search(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)", (s or "").replace(",", ""))
    if not m:
        m = re.search(r"(\d+(?:\.\d{1,2})?)", (s or "").replace(",", ""))
    return m.group(1) if m else ""


def _num(s):
    m = re.search(r"(\d+)", (s or "").replace(",", ""))
    return m.group(1) if m else ""


def stage_of(*blobs):
    """环节判定。退款进行中才算「退款中」；**退款成功是已完结**，按交易状态归
    （否则 15 单退款完毕的会被当成 15 单正在退款，数字就假了）。读不到就留空。"""
    blob = " ".join([b for b in blobs if b])
    if not blob:
        return ""
    refunded = bool(re.search(r"退款成功|已退款", blob))
    if not refunded and re.search(r"退款中|售后中|退款申请|维权|退换", blob):
        return "退款中"
    if "交易关闭" in blob or "已关闭" in blob:
        return "已关闭"
    if "交易成功" in blob:
        return "已完成"
    if "已确认收货" in blob or "已签收" in blob:
        return "已签收"
    if "卖家已发货" in blob or "部分发货" in blob:
        return "已发货"
    if "买家已付款" in blob:
        return "待发货"
    if "等待买家付款" in blob:
        return "待付款"
    return ""


def parse_item(cells):
    """商品行 → 明细。列：0 宝贝 1 单价 2 数量 3 履约售后 4 交易状态+按钮 5 实收款+支付 6 操作"""
    c = lambda i: cells[i] if len(cells) > i else ""
    g = c(0)
    title = re.split(r"商家编码|颜色分类|发货时间|规格[：:]", g)[0].strip()
    return {"goods": title[:120], "goodsRaw": g[:200],
            "price": _money(c(1)), "qty": _num(c(2)),
            "fulfill": _pick_word(c(3), FULFILL_WORDS) or c(3)[:24],
            "trade": _pick_word(c(4), TRADE_WORDS) or (c(4).split(" ")[0][:24] if c(4) else ""),
            "paid": _money(c(5)), "pay": _pick_word(c(5), PAY_WORDS)}


def parse_order(od):
    """group-header + 商品行 → 一条订单。读不到的字段留空，面板显示「—」。"""
    ht = od.get("headTxt") or ""
    m = re.search(r"订单号[：:]\s*(\d{6,26})", ht)
    no = m.group(1) if m else ""
    m2 = re.search(r"创建时间[：:]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)", ht)
    created = m2.group(1) if m2 else ""
    buyer = ""
    if m2:
        for tok in ht[m2.end():].split():
            if "号码保护" in tok:
                continue
            buyer = tok
            break
    items = [parse_item(r.get("cells") or []) for r in (od.get("rows") or [])]
    detail_url = ""
    for r in (od.get("rows") or []):
        for lk in (r.get("links") or []):
            h = lk.get("h") or ""
            if "tp/detail" in h or (lk.get("t") == "详情" and h):
                detail_url = ("https:" + h) if h.startswith("//") else h
                break
        if detail_url:
            break
    if not detail_url and no:
        detail_url = ("https://qn.taobao.com/home.htm/trade-platform/tp/detail"
                      "?bizOrderId=%s" % no)
    trades = " ".join(i.get("trade") or "" for i in items)
    fulfills = " ".join(i.get("fulfill") or "" for i in items)
    qty = 0
    for i in items:
        try:
            qty += int(i.get("qty") or 0)
        except Exception:
            pass
    first = items[0] if items else {}
    paid = ""
    for i in items:
        if i.get("paid"):
            paid = i["paid"]
            break
    return {"orderNo": no, "createdAt": created, "buyer": buyer,
            "goods": first.get("goods") or "", "goodsRaw": first.get("goodsRaw") or "",
            "goodsCount": len(items), "items": items,
            "price": first.get("price") or "", "qty": str(qty) if qty else "",
            "paid": paid or first.get("paid") or "",
            "pay": first.get("pay") or "",
            "tradeStatus": _pick_word(trades, TRADE_WORDS) or (first.get("trade") or ""),
            "fulfillStatus": _pick_word(fulfills, FULFILL_WORDS) or (first.get("fulfill") or ""),
            "stage": stage_of(trades, fulfills),
            "detailUrl": detail_url}


def read_orders(pg, limit=50):
    """读订单列表（只读）：先读一屏；不够就滚到底；还不动就点「下一页」。
    整个过程只发滚动与翻页两种动作，不碰发货/改价/退款/导出。"""
    out, seen, meta = [], set(), {}

    def absorb(raw):
        head = raw.get("head") or []
        if head and not meta.get("head"):
            meta["head"] = head
        n = 0
        for od in (raw.get("orders") or []):
            o = parse_order(od)
            key = o.get("orderNo") or (o.get("createdAt") or "") + "|" + (o.get("goods") or "")[:20]
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(o)
            n += 1
        return n

    try:
        absorb(pg.evaluate(ORDERS_JS))
    except Exception as e:
        meta["note"] = "读订单异常 %s" % str(e)[:120]
        return [], [], meta
    if not out:
        meta["note"] = "页面上没读到订单行（表头=%s）" % (meta.get("head"),)
        return [], meta.get("head") or [], meta
    stage("首屏 %d 单" % len(out))

    rounds = 0
    while len(out) < limit and rounds < 8:
        rounds += 1
        try:
            pg.evaluate(SCROLL_JS)
        except Exception:
            pass
        pg.wait_for_timeout(2200)
        grew = absorb(pg.evaluate(ORDERS_JS))
        if grew:
            stage("滚动后 +%d（累计 %d）" % (grew, len(out)))
            continue
        clicked = False
        try:
            els = pg.query_selector_all(", ".join(NEXT_SELS))
        except Exception:
            els = []
        # 倒序：列表分页在页面底部，通常排在后面
        for el in reversed(els):
            try:
                cls = (el.get_attribute("class") or "")
                if "disabled" in cls or el.get_attribute("disabled") is not None:
                    continue
                if not el.is_visible() or not el.bounding_box():
                    continue
                el.scroll_into_view_if_needed()
                el.click(timeout=8000)
                pg.wait_for_timeout(3200)
                clicked = True
                stage("点了第 %d 个下一页（%s）" % (len(els), cls[:36]))
                break
            except Exception:
                continue
        if not clicked:
            meta["note"] = "到最后一页（%d 单，%d 个下一页候选都不可用）" % (len(out), len(els))
            break
        if absorb(pg.evaluate(ORDERS_JS)):
            continue
        meta["note"] = "翻页没带来新单（%d 单）" % len(out)
        break
    meta.setdefault("note", "取到 %d 单" % len(out))
    meta["rounds"] = rounds
    meta["requested"] = limit
    return out[:limit], meta.get("head") or [], meta


def _pick_total(page_flat, top_keys=None):
    """总数在响应里叫什么不稳定：先按已知名找，再找任何带 total/count 的数字字段。
    都找不到就返回 (None, 说明)——不编一个数出来。"""
    pf = page_flat or {}
    for k in ("totalNum", "total", "totalCount", "count", "orderCount", "mainOrderCount"):
        v = pf.get(k)
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
            return int(v), k
    for k, v in pf.items():
        if re.search(r"total|count", k, re.I) and (isinstance(v, int) or
                                                   (isinstance(v, str) and v.isdigit())):
            return int(v), k
    return None, "page 里没有 total 类字段，page 键=%s" % (list(pf.keys())[:12])


def ms(d, end=False):
    return int(datetime.datetime(d.year, d.month, d.day,
                                 23 if end else 0, 59 if end else 0, 59 if end else 0)
               .timestamp() * 1000)


def date_range(key):
    today = datetime.date.today()
    if key == "today":
        return today, today
    if key == "yesterday":
        d = today - datetime.timedelta(days=1)
        return d, d
    if key == "7d":
        return today - datetime.timedelta(days=6), today
    return None


def query_by_date(pg, key):
    """日期筛选走页面自己的只读查询（不点日期控件、不点搜索按钮、不翻页）。
    口径 = 「创建时间」区间；拿到的是这个区间的单量。"""
    rng = date_range(key)
    if not rng:
        return False, "不支持的日期选项: %s" % key
    a, b = rng
    form = {"event_submit_do_query": "1", "_input_charset": "utf8", "isQnNew": "true",
            "isHideNick": "true", "prePageNo": "1", "sifg": "0",
            "action": "itemlist/SoldQueryAction", "close": "0", "pageNum": "1",
            "orderStatus": "", "rateStatus": "", "queryMore": "false",
            "dateBegin": str(ms(a)), "dateEnd": str(ms(b, True))}
    try:
        r = pg.evaluate(FETCH_JS, {"api": API, "form": form})
    except Exception as e:
        return False, "查询异常: %s" % str(e)[:160]
    if not r.get("ok"):
        return False, "查询没拿到 JSON（http=%s len=%s）：%s" % (
            r.get("status"), r.get("len"), (r.get("head") or "")[:80])
    total, why = _pick_total(r.get("pageFlat"), r.get("topKeys"))
    if total is None:
        return False, why[:180]
    RESULT["date"]["total"] = total
    echo = r.get("queryEcho") or {}
    RESULT["date"]["echo"] = echo
    if echo.get("db") not in (None, 0, "0"):
        return True, "创建时间 %s ~ %s（口径 dateBegin=%s，接口已回显）→ %s 单" % (
            a, b, echo.get("db"), total)
    return False, "接口没回显 dateBegin（echo=%s），日期可能没生效" % (echo,)


def kill_stale_chrome():
    """清掉用本 profile 残留的 chrome。
    为什么必须有：上一次采集的 chrome 没退干净时，新进程启动会变成
    「已在现有的浏览器会话中打开」然后 playwright 报 TargetClosed ——
    现场看起来像锁或登录问题，其实是残留进程占着 profile。
    安全前提：调用时已持 browser_lock，此刻用这个 profile 的 chrome 只可能是残留。
    """
    import subprocess
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='chrome.exe'",
             "get", "ProcessId,CommandLine", "/format:list"],
            capture_output=True, timeout=30, encoding="mbcs", errors="replace")
    except Exception:
        return 0
    rows, cur = [], {}
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if not line:
            if cur.get("ProcessId"):
                rows.append(cur)
                cur = {}
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            cur[k] = v
    if cur.get("ProcessId"):
        rows.append(cur)
    n = 0
    for d in rows:
        if PROFILE.lower() not in (d.get("CommandLine") or "").lower():
            continue
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", d["ProcessId"]],
                           capture_output=True, timeout=20)
            n += 1
        except Exception:
            pass
    return n


try:
    with guard("订单看板", timeout=400):
        killed = kill_stale_chrome()
        if killed:
            stage("清了 %d 个残留 chrome 进程" % killed)
        with sync_playwright() as p:
            ctx = None
            try:
                # 启动参数对齐「推广一键跑/_mtop_api.py 的 MtopClient.open()」：
                # 同一个 profile、headless=False、viewport 1400x900、locale zh-CN、
                # 只加 --disable-blink-features=AutomationControlled。一行不多改。
                ctx = p.chromium.launch_persistent_context(
                    PROFILE, executable_path=CHROME, headless=False,
                    viewport={"width": 1400, "height": 900}, locale="zh-CN",
                    args=["--disable-blink-features=AutomationControlled"])
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()

                # ① warm-up：先商品页（同域先访问一次，把 session 串起来）
                try:
                    pg.goto(ON_SALE, timeout=60000, wait_until="domcontentloaded")
                    pg.wait_for_timeout(5000)
                    RESULT["warmup"] = {"url": pg.url, "ok": "login" not in (pg.url or "").lower()}
                    stage("商品页 %s" % ("通" if RESULT["warmup"]["ok"] else "落在登录页"))
                except Exception as e:
                    stage("商品页异常 %s" % str(e)[:120])

                # ② 同一个 page 进订单页
                pg.goto(SOLD, timeout=60000, wait_until="domcontentloaded")
                ok, n, sec = wait_tabs(pg, 45)
                stage("订单页 ok=%s tabs=%d %.0fs" % (ok, n, sec))

                # ③ 还是没 tab → reload 一次再等（淘宝登录墙经常第一次拦、第二次放行）
                if not ok:
                    try:
                        pg.reload(timeout=60000, wait_until="domcontentloaded")
                        stage("reload 兜底")
                    except Exception as e:
                        stage("reload 异常 %s" % str(e)[:100])
                    ok, n, sec = wait_tabs(pg, 30)
                    stage("reload 后 ok=%s tabs=%d %.0fs" % (ok, n, sec))

                RESULT["url"] = pg.url
                RESULT["title"] = (pg.title() or "")[:80]
                bt = body_text(pg)
                RESULT["bodyHead"] = " ".join(bt.split())[:600]
                m = re.search(r"共查询到\s*(\d+)\s*个订单", bt)
                RESULT["orderLine"] = m.group(0) if m else ""

                if not ok:
                    RESULT["needLogin"] = True
                    RESULT["note"] = ("订单页没等到状态 tab：落地 %s。"
                                      "先看过商品页=%s，reload 也试过。" % (
                                          pg.url[:120], "通" if RESULT["warmup"]["ok"] else "也落登录页"))
                else:
                    RESULT["logged"] = True
                    RESULT["dateProbe"] = probe_dates(pg)
                    counts, all_tabs = read_tabs(pg)
                    RESULT["counts"] = counts
                    RESULT["tabs"] = all_tabs

                    # 订单列表（只读）。读失败也不影响上面的状态计数。
                    try:
                        orders, ohead, ometa = read_orders(pg, 50)
                        RESULT["orders"] = orders
                        RESULT["orderMeta"] = ometa
                        stage("订单 %d 单（表头 %d 列）" % (len(orders), len(ohead)))
                    except Exception as e:
                        RESULT["orderMeta"] = {"note": "读订单列表异常 %s" % str(e)[:160]}
                        stage("订单列表异常 %s" % str(e)[:120])

                    if WANT != "current":
                        applied, why = query_by_date(pg, WANT)
                        RESULT["date"]["applied"] = applied
                        RESULT["date"]["note"] = why
                        stage("日期[%s] %s：%s" % (WANT, "已应用" if applied else "未应用", why))

                    RESULT["ok"] = True
                    RESULT["note"] = "ok counts=%d" % len(counts)
            finally:
                try:
                    if ctx:
                        ctx.close()
                except Exception:
                    pass
except Exception as e:
    RESULT["note"] = "采集异常: %s" % str(e)[:400]

RESULT["fetchedAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
print("__RESULT__" + json.dumps(RESULT, ensure_ascii=False))
'''


COLLECT_DETAIL = r'''
# -*- coding: utf-8 -*-
"""订单详情采集（只读）：不点任何按钮，只靠 URL 导航 + 读 DOM。
详情页地址版本多，不写死一个：按 CANDIDATES 依次试，第一个看起来像详情页的就收。
四个都不像，就回列表页把那一行的链接报出来（rowProbe），不硬猜。
"""
import json, re, sys, os, datetime
sys.path.insert(0, r"__TG__")
from browser_lock import guard
from playwright.sync_api import sync_playwright

PROFILE = os.path.join(r"__TG__", "chrome_profile")
CHROME = r"__CHROME__"
ON_SALE = "https://myseller.taobao.com/home.htm/SellManage/on_sale"
SOLD = "https://myseller.taobao.com/home.htm/trade-platform/tp/sold"
ORDER_NO = (sys.argv[1] or "").strip()
GIVEN_URL = (sys.argv[2] if len(sys.argv) > 2 else "").strip()

RESULT = {"ok": False, "orderNo": ORDER_NO, "url": "", "title": "",
          "stages": [], "pairs": [], "lines": [], "raw": "", "note": "",
          "fetchedAt": ""}

# 详情页在 **qn.taobao.com** 下（从列表行的「详情」链接实测得到的，不是 myseller）
CANDIDATES = ([GIVEN_URL] if GIVEN_URL else []) + [
    "https://qn.taobao.com/home.htm/trade-platform/tp/detail?bizOrderId=%s",
    "https://myseller.taobao.com/home.htm/trade-platform/tp/detail?bizOrderId=%s",
    "https://trade.taobao.com/trade/detail/trade_item_detail.htm?bizOrderId=%s",
    "https://trade.taobao.com/trade/detail/trade_order_detail.htm?bizOrderId=%s",
]


def stage(x):
    RESULT["stages"].append("%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), x))


def free_profile():
    """把用本 profile 的残留 chrome 清掉。
    别的任务刚用完浏览器、chrome 还没退完时，新起的进程会 exitCode=21 直接死
    （日志里只有 process did exit），看着像登录失效/锁问题，其实是 profile 被占着。
    安全前提：调用时已持 browser_lock，此刻用这个 profile 的 chrome 只可能是残留。
    """
    import subprocess
    n = 0
    try:
        out = subprocess.run(["wmic", "process", "where", "name='chrome.exe'",
                              "get", "ProcessId,CommandLine", "/format:list"],
                             capture_output=True, timeout=30, encoding="mbcs", errors="replace")
    except Exception:
        return 0
    cur, rows = {}, []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if not line:
            if cur.get("ProcessId"):
                rows.append(cur)
                cur = {}
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            cur[k] = v
    if cur.get("ProcessId"):
        rows.append(cur)
    for d in rows:
        if PROFILE.lower() not in (d.get("CommandLine") or "").lower():
            continue
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", d["ProcessId"]],
                           capture_output=True, timeout=20)
            n += 1
        except Exception:
            pass
    return n


def body(pg):
    try:
        return pg.evaluate("() => (document.body.innerText || '')")
    except Exception:
        return ""


def looks_detail(t):
    hits = [k for k in ("收货", "订单信息", "实付", "支付方式", "买家", "物流") if k in t]
    return len(hits) >= 2


PAIRS_JS = """
() => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const out = [];
  document.querySelectorAll('.next-form-item, .detail-item, .trade-detail-item').forEach(el => {
    const lb = el.querySelector('label, .next-form-item-label, .label, dt');
    const vl = el.querySelector('.next-form-item-control, .value, dd');
    if (!lb || !vl) return;
    const k = clean(lb.innerText), v = clean(vl.innerText);
    if (!k || !v || k === v || k.length > 30) return;
    out.push({k: k.slice(0, 40), v: v.slice(0, 240)});
  });
  const seen = {}, uniq = [];
  for (const it of out) { const key = it.k + '|' + it.v; if (!seen[key]) { seen[key] = 1; uniq.push(it); } }
  return uniq.slice(0, 120);
}
"""

try:
    with guard("订单详情", timeout=300):
        killed = free_profile()
        if killed:
            stage("清了 %d 个残留 chrome" % killed)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, executable_path=CHROME, headless=False,
                viewport={"width": 1400, "height": 900}, locale="zh-CN",
                args=["--disable-blink-features=AutomationControlled"])
            try:
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                pg.goto(ON_SALE, timeout=60000, wait_until="domcontentloaded")
                pg.wait_for_timeout(5000)
                stage("预热商品页 %s" % pg.url[:80])
                got = False
                for tpl in CANDIDATES:
                    u = tpl % ORDER_NO if "%s" in tpl else tpl
                    try:
                        pg.goto(u, timeout=45000, wait_until="domcontentloaded")
                    except Exception as e:
                        stage("打不开 %s：%s" % (u[:70], str(e)[:80]))
                        continue
                    pg.wait_for_timeout(5000)
                    t = body(pg)
                    okd = looks_detail(t) and "login" not in (pg.url or "").lower()
                    stage("%s → %s len=%d" % (u.split("?")[0][-30:],
                                           "像详情" if okd else "不像", len(t)))
                    if okd:
                        RESULT["url"], RESULT["title"] = pg.url, (pg.title() or "")[:80]
                        RESULT["raw"] = t[:6000]
                        RESULT["ok"] = True
                        got = True
                        break
                if not got:
                    pg.goto(SOLD, timeout=60000, wait_until="domcontentloaded")
                    pg.wait_for_timeout(6000)
                    probe = pg.evaluate("""(no) => {
                        const rs = [...document.querySelectorAll('tbody tr')];
                        for (const tr of rs) {
                          if ((tr.innerText || '').indexOf(no) < 0) continue;
                          const a = [...tr.querySelectorAll('a')]
                            .map(x => x.getAttribute('href') || '').filter(h => h);
                          const d = [...tr.querySelectorAll('a,button,span')]
                            .find(e => /详情|查看/.test(e.innerText || ''));
                          return {hrefs: a.slice(0, 6),
                                  detailHref: d ? (d.getAttribute('href') || '') : ''};
                        }
                        return null;
                    }""", ORDER_NO)
                    RESULT["rowProbe"] = probe
                    stage("兜底：列表行链接 %s" % (str(probe)[:160],))
                    RESULT["note"] = "四个详情地址都不像详情页；列表行链接见 rowProbe"
                else:
                    RESULT["pairs"] = pg.evaluate(PAIRS_JS)
                    RESULT["lines"] = [x.strip() for x in (RESULT["raw"] or "").splitlines()
                                       if x.strip()][:200]
                    RESULT["note"] = "ok pairs=%d" % len(RESULT["pairs"])
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
except Exception as e:
    RESULT["note"] = "详情采集异常: %s" % str(e)[:300]

RESULT["fetchedAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
print("__RESULT__" + json.dumps(RESULT, ensure_ascii=False))
'''


def _run_child(code, args, timeout=420):
    """起子进程跑采集代码。stdout/stderr 落文件——不能走管道，见文件头坑 #1。"""
    py = RT if os.path.isfile(RT) else sys.executable
    out_fp = os.path.join(tempfile.gettempdir(), "order_report_out_%d.txt" % os.getpid())
    err_fp = out_fp + ".err"
    try:
        with io.open(out_fp, "w+", encoding="utf-8") as fo, \
                io.open(err_fp, "w+", encoding="utf-8") as fe:
            p = subprocess.run([py, "-X", "utf8", "-c", code] + list(args or []),
                               stdin=subprocess.DEVNULL, stdout=fo, stderr=fe, timeout=timeout)
            fo.flush()
            fe.flush()
        try:
            with io.open(out_fp, encoding="utf-8", errors="replace") as f:
                stdout = f.read()
        except Exception:
            stdout = ""
        for line in reversed(stdout.splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__"):])
        with io.open(err_fp, encoding="utf-8", errors="replace") as f:
            err = f.read().strip()
        return {"ok": False, "note": "无结果输出 rc=%s：%s" % (p.returncode, (err or stdout)[-300:])}
    except subprocess.TimeoutExpired:
        return {"ok": False, "note": "采集超时（%ss）" % timeout}
    except Exception as e:
        return {"ok": False, "note": "采集失败: %s" % str(e)[:200]}
    finally:
        for fp in (out_fp, err_fp):
            try:
                os.remove(fp)
            except Exception:
                pass


def _collect(date_key="current"):
    code = COLLECT.replace("__TG__", TG).replace("__CHROME__", _chrome_exe())
    return _run_child(code, [date_key], 480)


def _collect_detail(order_no, url=""):
    code = COLLECT_DETAIL.replace("__TG__", TG).replace("__CHROME__", _chrome_exe())
    return _run_child(code, [order_no, url or ""], 300)


# ---- 订单列表缓存：面板先看缓存、详情页拿列表做兑底 ----
def _order_cache_fp():
    d = os.path.join(TG, "runtime")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "order_list.json")


def _save_orders(orders, meta=None):
    import datetime
    try:
        with io.open(_order_cache_fp(), "w", encoding="utf-8") as f:
            json.dump({"fetchedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "orders": orders, "meta": meta or {}}, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def _load_orders():
    fp = _order_cache_fp()
    if not os.path.isfile(fp):
        return {"orders": [], "fetchedAt": "", "meta": {}}
    try:
        with io.open(fp, encoding="utf-8") as f:
            d = json.load(f)
        return {"orders": d.get("orders") or [], "fetchedAt": d.get("fetchedAt") or "",
                "meta": d.get("meta") or {}}
    except Exception:
        return {"orders": [], "fetchedAt": "", "meta": {}}


# 详情页是「标签：值」的文本流，而且**标签跟值常常不在同一行**
# （“收货地址：” 一行，值在下一行）。所以按标签定位：有同行值取同行，没有就取下一行。
DETAIL_LABELS = [
    ("orderNo", ("订单编号", "订单号")),
    ("orderState", ("订单状态",)),
    ("pay", ("支付方式", "付款方式")),
    ("createdAt", ("创建时间",)),
    ("payTime", ("付款时间",)),
    ("buyer", ("昵称",)),
    ("phone", ("联系电话", "手机号码")),
    ("addressRaw", ("收货地址",)),
    ("shipWay", ("运送方式",)),
    ("sellerPaid", ("商家实收",)),
    ("userPaid", ("用户实付",)),
    ("logiCo", ("物流公司名称",)),
    ("waybill", ("运单号",)),
    ("note", ("买家留言",)),
    ("tradeNo", ("微信交易号", "支付宝交易号", "交易号")),
]

ITEM_STATES = ["未发货", "已发货", "已签收", "未确认收货", "已确认收货",
               "退款中", "退款成功", "交易关闭"]


def _split_label(line):
    """把「标签：值」拆成 (标签, 值)。中英文冒号都认。"""
    for sep in ("：", ":"):
        if sep in line:
            k, _, v = line.partition(sep)
            return k.strip(), v.strip()
    return line.strip(), ""


def _parse_detail(r):
    """详情页原文 → 字段。读不到的留空（面板显示「读不到」），原文行留在 rawLines 里核对。"""
    lines = [x for x in (r.get("lines") or [])]
    out = {k: "" for k, _ in DETAIL_LABELS}
    out.update({"receiver": "", "phone": "", "address": "", "ship": "",
                "itemState": "", "paid": ""})
    for i, ln in enumerate(lines):
        k, v = _split_label(ln)
        if not k or len(k) > 12:
            continue
        for field, keys in DETAIL_LABELS:
            if out.get(field):
                continue
            if any(k == kw or k.endswith(kw) for kw in keys):
                if not v:
                    for j in range(i + 1, min(i + 3, len(lines))):
                        if lines[j].strip():
                            v = lines[j].strip()
                            break
                # 金额类：标签后面可能隔几行才是 ￥xx；商家实收还要绕开「包含快递」
                # ——那里先出现快递费，真正的实收在它后面，所以取窗口里**最后一个** ￥
                if field in ("sellerPaid", "userPaid"):
                    cand = []
                    if "￥" in v or "¥" in v:
                        cand.append(v)
                    for j in range(i + 1, min(i + 8, len(lines))):
                        lj = lines[j] or ""
                        if ("￥" in lj or "¥" in lj) and "包含" not in lj:
                            cand.append(lj.strip())
                        if field == "userPaid" and cand:
                            break
                    if cand:
                        v = cand[-1] if field == "sellerPaid" else cand[0]
                out[field] = v[:200]
                break
    # 标签值后面常粘着按钮文字（「复制」），统一切掉
    for k2 in list(out.keys()):
        if out.get(k2):
            out[k2] = re.sub(r"(复制|Copy)\s*$", "", out[k2]).strip()
    # 收货地址那一串：「收货人，电话，地址」——脱敏的也照实拆，不展开
    raw = out.get("addressRaw") or ""
    if raw:
        parts = [p.strip() for p in re.split(r"[，,]", raw) if p.strip()]
        if parts:
            out["receiver"] = parts[0][:40]
        if len(parts) > 1 and re.search(r"[*\d]{6,}", parts[1]):
            out["phone"] = out.get("phone") or parts[1][:24]
        out["address"] = (", ".join(parts[1:]) if len(parts) > 1 else raw)[:200]
    # 商品/发货状态：只从「宝贝」表头往后找，不然会把顶部进度条的「交易成功」当成商品状态
    start = 0
    for i, ln in enumerate(lines):
        t = ln.strip()
        if t.startswith("宝贝") and ("属性" in t or "数量" in t):
            start = i
            break
    for ln in lines[start:]:
        t = ln.strip()
        if t in ITEM_STATES:
            out["itemState"] = out["itemState"] or t
    ship_bits = [b for b in (out.get("shipWay"), out.get("logiCo"),
                            out.get("waybill"), out.get("itemState")) if b]
    out["ship"] = " / ".join(ship_bits)
    # 实付：优先商家实收/用户实付后面的 ￥
    if not out.get("paid"):
        m = re.search(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)", r.get("raw") or "")
        if m:
            out["paid"] = m.group(1)
    return out


def _log(msg):
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            import datetime
            f.write("%s  %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _chrome_exe():
    """Chrome 可执行文件：按 paths 的候选顺序取第一个真实存在的。
    不能直接用 paths.CHROME——它的默认值指向 %LOCALAPPDATA%，本机 Chrome 其实装在
    Program Files 下，写死一个会导致「executable doesn't exist」而看起来像登录/锁的问题。"""
    for c in (getattr(paths, "CHROME_CANDIDATES", None) or [paths.CHROME]):
        if c and os.path.isfile(c):
            return c
    return str(paths.CHROME)


DATE_KEYS = ("current", "today", "yesterday", "7d")


def _db():
    """订单日汇总库。跟报表用同一个 report.db，口径统一。

    T+1 设计：今天的数还在变，只给面板看；**昨天的数据定稿后才入库存档**。
    """
    import sqlite3
    db = os.path.join(TG, "runtime", "report.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE IF NOT EXISTS order_daily(
        date TEXT NOT NULL, account TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL, cnt INTEGER NOT NULL DEFAULT 0,
        fetched_at TEXT, PRIMARY KEY(date, account, status))""")
    con.commit()
    return con


def _account():
    try:
        import acct_profile as _ap
        a = _ap.resolve() or {}
        return str(a.get("member_id") or a.get("name") or "")
    except Exception:
        return ""


def _save_day(date_str, counts, account):
    """把某天的状态分布写进库（同一天重复采只覆盖数字，不重复插行）。"""
    import datetime
    if not date_str or not counts:
        return 0
    con = _db()
    n = 0
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for c in counts:
        try:
            con.execute(
                "INSERT INTO order_daily(date,account,status,cnt,fetched_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(date,account,status) DO UPDATE SET cnt=excluded.cnt, "
                "fetched_at=excluded.fetched_at",
                (date_str, account, str(c.get("name") or ""), int(c.get("n") or 0), now))
            n += 1
        except Exception:
            continue
    con.commit()
    con.close()
    return n


def _history(account, days=30):
    """读历史：按日期升序返回，面板画趋势用。"""
    con = _db()
    try:
        rows = con.execute(
            "SELECT date,status,cnt FROM order_daily WHERE account=? "
            "ORDER BY date DESC LIMIT ?", (account, days * 12)).fetchall()
    finally:
        con.close()
    by_date = {}
    for d, s, n in rows:
        by_date.setdefault(d, {})[s] = n
    return [{"date": d, "counts": v} for d, v in sorted(by_date.items())]


def handle(action, params):
    if action in ("overview", "status"):
        date_key = str((params or {}).get("date") or "current").strip()
        if date_key not in DATE_KEYS:
            date_key = "current"
        r = _collect(date_key)
        _log("采集[%s] %s：counts=%d ok=%s date=%s %s" % (
            date_key, "成功" if r.get("ok") else "失败",
            len(r.get("counts") or []), r.get("ok"),
            "已应用" if (r.get("date") or {}).get("applied") else "未应用",
            r.get("note") or ""))
        if r.get("ok") and date_key == "current":
            try:
                if r.get("orders"):
                    _save_orders(r.get("orders"), r.get("orderMeta"))
                    _log("订单列表入缓存 %d 单" % len(r.get("orders") or []))
            except Exception as e:
                _log("订单缓存写入失败 %s" % str(e)[:100])
        # 采展失败但有缓存：把上次的列表带上，面板不至于变空白（标 stale）
        if not r.get("ok") and not r.get("orders"):
            c = _load_orders()
            if c.get("orders"):
                r["orders"] = c["orders"]
                r["ordersStale"] = c.get("fetchedAt") or ""
        # T+1：只把「昨天」定稿的数据入库；今天还在变，不落库
        if r.get("ok") and date_key in ("yesterday",) and r.get("counts"):
            try:
                import datetime
                y = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
                n = _save_day(y, r.get("counts") or [], _account())
                r["saved"] = {"date": y, "rows": n}
            except Exception as e:
                r["saved"] = {"error": str(e)[:80]}
        return {"ok": bool(r.get("ok")), "data": r,
                "error": None if r.get("ok") else (r.get("note") or "采集失败")}
    if action == "snapshot":
        # 定时任务入口：采昨天并入库。scheduler 每天调一次。
        r = _collect("yesterday")
        saved = None
        if r.get("ok") and r.get("counts"):
            try:
                import datetime
                y = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
                saved = {"date": y, "rows": _save_day(y, r.get("counts") or [], _account())}
            except Exception as e:
                saved = {"error": str(e)[:80]}
        _log("定时采集[昨天] %s saved=%s %s" % (
            "成功" if r.get("ok") else "失败", saved, r.get("note") or ""))
        return {"ok": bool(r.get("ok")), "data": r, "saved": saved,
                "error": None if r.get("ok") else (r.get("note") or "采集失败")}
    if action == "history":
        try:
            days = int((params or {}).get("days") or 30)
        except Exception:
            days = 30
        return {"ok": True, "data": _history(_account(), days)}
    if action == "orders":
        c = _load_orders()
        return {"ok": True, "data": {"orders": c.get("orders") or [],
                                    "fetchedAt": c.get("fetchedAt") or "",
                                    "orderMeta": c.get("meta") or {}}}
    if action == "detail":
        no = str((params or {}).get("orderNo") or (params or {}).get("order_no") or "").strip()
        no = re.sub(r"\D", "", no)
        if not (10 <= len(no) <= 24):
            return {"ok": False, "error": "订单号不像订单号：%s" % (no or "（空）")}
        # 先看缓存里有没有这一单：有就把它的 detailUrl 带上（直达，少猜一个地址）
        row = {}
        for o in (_load_orders().get("orders") or []):
            if o.get("orderNo") == no:
                row = o
                break
        r = _collect_detail(no, row.get("detailUrl") or "")
        fields = _parse_detail(r) if r.get("ok") else {}
        missing = [k for k in ("receiver", "phone", "address", "pay", "ship") if not fields.get(k)]
        _log("详情[%s] %s pairs=%d 缺=%s %s" % (
            no, "成功" if r.get("ok") else "失败", len(fields), ",".join(missing) or "无",
            r.get("note") or ""))
        return {"ok": bool(r.get("ok")),
                "data": {"orderNo": no, "fields": fields, "row": row,
                         "missing": missing, "url": r.get("url") or "",
                         "title": r.get("title") or "",
                         "stages": r.get("stages") or [],
                         "rawLines": (r.get("lines") or [])[:120],
                         "rowProbe": r.get("rowProbe"),
                         "fetchedAt": r.get("fetchedAt") or "",
                         "note": r.get("note") or ""},
                "error": None if r.get("ok") else (r.get("note") or "详情采集失败")}
    return {"ok": False, "error": "未知动作: %s" % action}
