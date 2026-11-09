# -*- coding: utf-8 -*-
"""推广库存巡检 · 执行进程

**为什么单独一个进程**：workbench 的 HTTP handler 线程里跑 asyncio 会炸
（`name 'base_events' is not defined`）。playwright 一碰就死。
子进程跑是最稳的，也是 order-report 那套成熟做法。

用法：
  python _worker.py pull                  → 拉全量单元，结果写 runtime/<mid>/units.json
  python _worker.py pause --ids 1,2,3     → 停指定单元，结果写 runtime/<mid>/pause_result.json
  python _worker.py enable --ids 1,2,3    → 启用指定单元（试验）

所有输出写 stdout，调用方读文件。
"""

import os, sys, json, re, io, csv, time, datetime, asyncio

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
WB = os.path.dirname(os.path.dirname(BASE))
sys.path.insert(0, WB)
RT_ROOT = os.path.join(BASE, "runtime")

CDP = "http://127.0.0.1:9222"
API_PAGE = "https://one.alimama.com/adgroup/horizontal/findPage.json"
API_CAMPAIGN = "https://one.alimama.com/campaign/horizontal/findPage.json"
API_BASE = "https://one.alimama.com"
REFERER = "https://one.alimama.com/index.html"
HDR_JSON = {"content-type": "application/json", "x-requested-with": "XMLHttpRequest", "referer": REFERER}

PER_BATCH = 40   # 实测 40 稳，93 会静默失败


def wlog(m):
    """写文件 + 打 stdout。
    只打 stdout 的话，日志会落在 server 起的临时文件里，面板上看不见——
    就很难确认「到底关没关遮罩」。所以同时写一份到插件目录。"""
    line = "[%s] %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), m)
    print(line, end="", flush=True)
    try:
        with io.open(os.path.join(BASE, "worker.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def rtdir():
    """按活动连接 member_id 分目录。没 member_id 就落 shared。"""
    mid = "shared"
    try:
        import conn_store
        c = conn_store.active_of("taobao")
        if c and c.get("member_id"):
            mid = str(c["member_id"])
    except Exception:
        pass
    d = os.path.join(RT_ROOT, mid)
    os.makedirs(d, exist_ok=True)
    return d, mid


# 兜底计划表：只在自动发现失败时用（主店 2026-10 的 11 个已满计划）
FALLBACK_PLANS = [
    "72264315693", "83306736168", "72304256352", "83211993350", "83259474708",
    "83212131417", "83212279185", "83259606767", "83212439397", "83259874852", "83162843849",
]


async def discover_campaigns(page, csrf):
    """拉全部推广计划。别再硬编码计划 ID——主店实际 23 个，写死 11 个会漏掉一半盘子。
    返回 [{"campaignId", "name", "status"}]"""
    body = {"bizCode": "onebpSearch", "offset": 0, "pageSize": 200}
    try:
        r = await page.request.post("%s?csrfId=%s&bizCode=onebpSearch" % (API_CAMPAIGN, csrf),
                                    data=json.dumps(body), headers=HDR_JSON)
        js = json.loads(await r.text())
    except Exception as e:
        wlog("  发现计划失败: %s" % str(e)[:80])
        return []
    d = js.get("data") or {}
    out = []
    for x in (d.get("list") or []):
        cid = str(x.get("campaignId") or "")
        if cid:
            out.append({"campaignId": cid,
                        "name": (x.get("campaignName") or "").strip(),
                        "status": str(x.get("status") or x.get("displayStatus") or "")})
    wlog("  发现 %d 个计划（接口声明 %s）" % (len(out), d.get("count")))
    return out


# ---------- 与页面/接口交互 ----------

async def open_page(p):
    """连 CDP，清多余标签，返回 (browser, ctx, page)"""
    b = await p.chromium.connect_over_cdp(CDP)
    ctx = b.contexts[0]
    pages = list(ctx.pages)
    for pg in pages[1:]:
        try:
            await pg.close()
        except Exception:
            pass
    page = pages[0] if pages else await ctx.new_page()
    return b, ctx, page


async def grab_csrf(page, seed_plan):
    """抓 csrfId。只能从「页面自己发 findPage 的请求」里拿——
    window.g_config / window.mtop 在这站全是 null。"""
    box = {"csrf": None}

    def on_req(req):
        if "findPage" in req.url and box["csrf"] is None:
            m = re.search(r"[?&]csrfId=([0-9A-Za-z_]+)", req.url)
            if m:
                box["csrf"] = m.group(1)

    page.on("request", on_req)
    try:
        await page.goto("%s/index.html#!/manage/search?campaignId=%s" % (API_BASE, seed_plan),
                        wait_until="domcontentloaded", timeout=60000)
        await page.reload(wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        wlog("导航异常: %s" % str(e)[:80])
    for _ in range(30):
        if box["csrf"]:
            break
        await asyncio.sleep(1)
    return box["csrf"]


async def fetch_units(page, csrf, cid, page_size=100):
    """拉一个计划的全部单元"""
    got, offset, total = {}, 0, None
    while True:
        body = {"bizCode": "onebpSearch", "campaignIdList": [cid], "offset": offset,
                "pageSize": page_size, "statusList": ["start", "pause"], "csrfId": csrf}
        try:
            r = await page.request.post(
                "%s?csrfId=%s&bizCode=onebpSearch" % (API_PAGE, csrf),
                data=json.dumps(body),
                headers={"content-type": "application/json",
                         "x-requested-with": "XMLHttpRequest", "referer": REFERER})
            js = json.loads(await r.text())
        except Exception as e:
            wlog("  %s offset=%d 失败: %s" % (cid, offset, str(e)[:60]))
            break
        d = js.get("data") or {}
        if total is None:
            total = d.get("count")
        lst = d.get("list") or []
        new = 0
        for it in lst:
            uid = str(it.get("adgroupId") or "")
            if not uid or uid in got:
                continue
            got[uid] = {"unitId": uid, "campaignId": str(it.get("campaignId") or cid),
                        "title": (it.get("adgroupName") or "").strip(),
                        "status": str(it.get("displayStatus") or ""),
                        "itemId": str(it.get("itemId") or "")}
            new += 1
        if not lst or new == 0:
            break
        offset += page_size
        if total and len(got) >= total:
            break
        await asyncio.sleep(0.6)
    return list(got.values()), total


async def read_states(page, csrf, cid):
    """读某计划全部单元的当前状态 {unitId: status}"""
    body = {"bizCode": "onebpSearch", "campaignIdList": [cid], "offset": 0, "pageSize": 500,
            "statusList": ["start", "pause"], "csrfId": csrf}
    try:
        r = await page.request.post("%s?csrfId=%s&bizCode=onebpSearch" % (API_PAGE, csrf),
                                    data=json.dumps(body),
                                    headers={"content-type": "application/json",
                                             "x-requested-with": "XMLHttpRequest", "referer": REFERER})
        js = json.loads(await r.text())
        return {str(x.get("adgroupId")): x.get("displayStatus")
                for x in ((js.get("data") or {}).get("list") or [])}
    except Exception:
        return {}


# ---------- 停投 / 启用（DOM 操作） ----------

async def pick_units(page, uids):
    """勾选指定单元，返回页面真实 checked 数（不认 click 返回值）"""
    n = 0
    for uid in uids:
        try:
            ok = await page.evaluate(r"""(uid) => {
                const trs = [...document.querySelectorAll('tr')].filter(tr => /单元ID/.test(tr.innerText || ''));
                for (const tr of trs) {
                    const m = (tr.innerText || '').match(/单元ID\s*[:：]\s*(\d{6,})/);
                    if (!m || m[1] !== uid) continue;
                    const cb = tr.querySelector('input[type=checkbox]');
                    if (cb) { cb.scrollIntoView({block:'center'}); if (!cb.checked) cb.click(); return true; }
                }
                return false;
            }""", uid)
            if ok:
                n += 1
            await asyncio.sleep(0.22)
        except Exception:
            pass
    real = await page.evaluate("() => document.querySelectorAll('input[type=checkbox]:checked').length")
    return real or 0


async def click_batch_menu(page):
    """点「批量修改状态」。文本实际是 '批量修改状态\\n\\ue646'（带图标字符），
    精确正则永远匹配不到 —— 按 DOM 找最窄可见元素的坐标点。"""
    pos = await page.evaluate(r"""() => {
        const out = [];
        document.querySelectorAll('button, span, div').forEach(e => {
            const t = (e.innerText || '').trim();
            if (!t.startsWith('批量修改状态')) return;
            const b = e.getBoundingClientRect();
            if (b.width < 20 || b.height < 10) return;
            out.push({x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2), w: Math.round(b.width)});
        });
        return out;
    }""")
    if not pos:
        return False
    tgt = sorted(pos, key=lambda c: c["w"])[0]
    await page.mouse.click(tgt["x"], tgt["y"])
    await asyncio.sleep(1.6)
    return True


async def click_menu_item(page, texts):
    """点菜单项。有的计划把两项塞一个节点（'正在推广\\n\\n暂停推广'），
    所以只能用「包含」，不能用「等于」。"""
    items = await page.evaluate(r"""(texts) => {
        const out = [];
        document.querySelectorAll('li, div, span, a, button').forEach(e => {
            const t = (e.innerText || '').trim();
            if (t.length > 30) return;
            if (!texts.some(x => t.includes(x))) return;
            const b = e.getBoundingClientRect();
            if (b.width < 8 || b.height < 8 || b.y < 0) return;
            out.push({x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2), w: Math.round(b.width), t: t});
        });
        return out;
    }""", texts)
    if not items:
        return None
    it = sorted(items, key=lambda c: c["w"])[0]
    await page.mouse.click(it["x"], it["y"])
    await asyncio.sleep(2.2)
    return it["t"]


async def click_confirm(page):
    for pat in (r"^确\s*定$", r"^确认$", r"^确认停用$", r"^确认暂停$"):
        loc = page.locator("button, .next-btn").filter(has_text=re.compile(pat))
        try:
            if await loc.count() > 0:
                await loc.last.click(timeout=3000)
                await asyncio.sleep(1.5)
                return True
        except Exception:
            pass
    return False


async def dismiss_overlays(page, rounds=4):
    """关掉遮罩式广告/活动弹窗（万圣节、双11、AI 小万升级、万相天成这类）。

    为什么必须有这个：平台经常弹全屏遮罩，DOM 就在那里但鼠标事件被遮罩吃掉，
    脚本「点了但没反应」却一句错都不报。不处理的话所有批量操作都是空跑。

    判据（2026-10-29 实测 + 梓帆提供真实 DOM）：
      结构：<button mxv="popData,popOffset"><span><i class="mx-iconfont"></i></span></button>
      - class 后半段是每次构建随机的 hash（bXMILLdiFx / icQRQlpkc / asiYysiabu…），
        按 hash 类名找必然扑空
      - 文本是空的（图标靠字体渲染），按 ×/✕ 匹配也扑空；ESC 关不掉
      - 位置也不能靠：关闭键有时右上角、有时屏幕中部右侧

    只认三个稳定特征，缺一不可：
      1. `i.mx-iconfont`（组件库稳定类）+ 空文本
      2. **必须在地层内**：祖先链上存在 z-index >= 1000 的元素。
         不加这条会误选页面常驻图标（表格行的「更多」也是 button>i.mx-iconfont）——实测踩过。
      3. 面积最小的优先（关闭键永远是最小那个）

    兜底链：图标候选 → 点视口四角（点遮罩外面等于取消）→ pointer-events 穿透。
    """
    tried = set()
    for _ in range(rounds):
        try:
            info = await page.evaluate(r"""(tried) => {
                const KEY = (c) => c.x + ',' + c.y;
                const skip = new Set(tried);
                const vw = innerWidth, vh = innerHeight;

                let hasMask = false;
                document.querySelectorAll('div, section').forEach(e => {
                    const cs = getComputedStyle(e);
                    if (cs.position !== 'fixed') return;
                    if (cs.display === 'none' || cs.visibility === 'hidden') return;
                    const z = parseInt(cs.zIndex || '0', 10) || 0;
                    if (z < 1000) return;
                    const b = e.getBoundingClientRect();
                    if (b.width > vw * 0.8 && b.height > vh * 0.8) hasMask = true;
                });
                if (!hasMask) return {mask: null};

                // 必须在「全屏的高 z-index 层」里。只看 z-index 不够——
                // 侧边栏容器自己也带高 z-index（实测误选过 (92,84) 的导航图标）。
                // 最高优先级：mx-click 里写着 cancel/close/dismiss。
                // 阿里组件把「这是取消按钮」直接写在 mx-click 属性上
                // （<span mx-click="cnt_dlg_2165cancel()"><i class="mx-iconfont">），
                // 它不叫 aria-label、不叫 class —— 但这是最直白的语义，优先用它。
                const cancelCand = [];
                document.querySelectorAll('[mx-click]').forEach(e => {
                    const mc = (e.getAttribute('mx-click') || '').toLowerCase();
                    if (!/cancel|close|dismiss|\\bquit\\b/.test(mc)) return;
                    const cs = getComputedStyle(e);
                    if (cs.display === 'none' || cs.visibility === 'hidden') return;
                    if (cs.pointerEvents === 'none') return;
                    const b = e.getBoundingClientRect();
                    if (b.width < 6 || b.width > 90 || b.height < 6 || b.height > 90) return;
                    if (b.x < 0 || b.y < 0 || b.right > vw + 1 || b.bottom > vh + 1) return;
                    const c = {x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2),
                               area: b.width * b.height, tag: e.tagName,
                               cls: mc.slice(0, 26)};
                    if (!skip.has(KEY(c))) cancelCand.push(c);
                });
                if (cancelCand.length) {
                    cancelCand.sort((a, b_) => (a.area - b_.area) || (a.y - b_.y) || (b_.x - a.x));
                    return {mask: true, cand: cancelCand.slice(0,3), why: 'mx-click'};
                }

                const inOverlay = (el) => {
                    let p = el.parentElement;
                    while (p && p !== document.documentElement) {
                        const cs = getComputedStyle(p);
                        const z = parseInt(cs.zIndex || '0', 10) || 0;
                        if (z >= 1000) {
                            const pb = p.getBoundingClientRect();
                            if (pb.width > vw * 0.8 && pb.height > vh * 0.8) return true;
                        }
                        p = p.parentElement;
                    }
                    return false;
                };

                const collect = (nodes) => {
                    const out = [];
                    nodes.forEach(e => {
                        const cs = getComputedStyle(e);
                        if (cs.display === 'none' || cs.visibility === 'hidden') return;
                        if (cs.pointerEvents === 'none') return;
                        const b = e.getBoundingClientRect();
                        if (b.width < 8 || b.width > 48 || b.height < 8 || b.height > 48) return;
                        if (b.x < 0 || b.y < 0 || b.right > vw + 1 || b.bottom > vh + 1) return;
                        if (!inOverlay(e)) return;
                        const icon = e.matches('i') ? e : e.querySelector('i');
                        if (!icon) return;
                        const icls = (icon.className || '').toString();
                        if (!/mx-iconfont|next-icon|iconfont/i.test(icls)) return;
                        const t = (icon.innerText || icon.textContent || '').trim();
                        if (t.length > 2) return;
                        const c = {x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2),
                                   area: b.width * b.height, tag: e.tagName,
                                   cls: icls.slice(0,26)};
                        if (!skip.has(KEY(c))) out.push(c);
                    });
                    out.sort((a, b_) => (a.area - b_.area) || (a.y - b_.y) || (b_.x - a.x));
                    return out;
                };

                let cand = collect(document.querySelectorAll('button'));
                if (cand.length) return {mask: true, cand: cand.slice(0,3), why: 'btn-icon'};

                cand = collect(document.querySelectorAll('i'));
                if (cand.length) return {mask: true, cand: cand.slice(0,3), why: 'bare-icon'};

                return {mask: true, cand: [], why: 'none'};
            }""", list(tried))

            if not info.get("mask"):
                return 0

            cand = info.get("cand") or []
            if cand:
                c = cand[0]
                tried.add("%d,%d" % (c["x"], c["y"]))
                await page.mouse.click(c["x"], c["y"])
                await asyncio.sleep(1.2)
                wlog("    关遮罩[%s]：点 (%d,%d) %s" % (info.get("why"), c["x"], c["y"], c["cls"]))
                if not await _still_masked(page):
                    return 1
                continue

            # 兜底一：点视口四角（弹窗在中间，四角必然是遮罩外 → 等于取消）
            dim = await page.evaluate("() => [innerWidth, innerHeight]")
            for cx, cy in [(12, 12), (dim[0]-14, 12), (12, dim[1]-14), (dim[0]-14, dim[1]-14)]:
                key = "%d,%d" % (cx, cy)
                if key in tried:
                    continue
                tried.add(key)
                await page.mouse.click(cx, cy)
                await asyncio.sleep(1.0)
                wlog("    关遮罩[点外部]：点 (%d,%d)" % (cx, cy))
                if not await _still_masked(page):
                    return 1
                break

            # 兜底二：鼠标穿透（不隐藏，用户仍可见）
            wlog("    关遮罩失败（已试 %d 处），改 pointer-events 穿透" % len(tried))
            await _pierce_overlays(page)
            return 1

        except Exception as e:
            wlog("    关遮罩异常: %s" % str(e)[:80])
            return 0
    return 1


async def _still_masked(page):
    """回读：当前是否还有全屏遮罩"""
    try:
        return await page.evaluate(r"""() => {
            let found = false;
            document.querySelectorAll('div, section').forEach(e => {
                const cs = getComputedStyle(e);
                if (cs.position !== 'fixed' || cs.display === 'none') return;
                const z = parseInt(cs.zIndex || '0', 10) || 0;
                if (z < 1000) return;
                const b = e.getBoundingClientRect();
                if (b.width > innerWidth * 0.8 && b.height > innerHeight * 0.8) found = true;
            });
            return found;
        }""")
    except Exception:
        return False


async def _pierce_overlays(page):
    """遮罩关不掉时的兜底：抬 pointer-events:none。
    只对全屏 fixed 高 z-index 层做，且不隐藏（用户仍可见）。"""
    try:
        n = await page.evaluate(r"""() => {
            let n = 0;
            document.querySelectorAll('div, section').forEach(e => {
                const cs = getComputedStyle(e);
                if (cs.position !== 'fixed') return;
                const z = parseInt(cs.zIndex || '0', 10) || 0;
                if (z < 1000) return;
                const b = e.getBoundingClientRect();
                if (b.width > innerWidth * 0.8 && b.height > innerHeight * 0.8) {
                    e.style.pointerEvents = 'none';
                    n++;
                }
            });
            return n;
        }""")
        if n:
            wlog("    遮罩 %d 层改为鼠标穿透（仍可见）" % n)
    except Exception as e:
        wlog("    穿透失败: %s" % str(e)[:50])


async def do_switch(page, csrf, cid, uids, action):
    """在已打开的某计划页上，把 uids 制到 action（pause / start）"""
    label = "暂停推广" if action == "pause" else "正在推广"
    done = 0
    for i in range(0, len(uids), PER_BATCH):
        batch = uids[i:i + PER_BATCH]
        await dismiss_overlays(page)          # 遮罩会吃掉鼠标事件，先清
        checked = await pick_units(page, batch)
        if not checked:
            wlog("    batch %d: 一个都没勾上" % (i // PER_BATCH + 1))
            continue
        if not await click_batch_menu(page):
            wlog("    batch %d: 找不到「批量修改状态」" % (i // PER_BATCH + 1))
            continue
        hit = await click_menu_item(page, [label])
        if not hit:
            wlog("    batch %d: 菜单里找不到「%s」" % (i // PER_BATCH + 1, label))
            continue
        await click_confirm(page)
        await asyncio.sleep(1.2)
        st = await read_states(page, csrf, cid)
        ok = sum(1 for u in batch if st.get(u) == action)
        done += ok
        wlog("    batch %d: 勾 %d | 读回 %s %d/%d | 累计 %d" %
             (i // PER_BATCH + 1, checked, action, ok, len(batch), done))
        await asyncio.sleep(2)
    return done


# ---------- 三个动作 ----------

async def cmd_pull():
    d, mid = rtdir()
    wlog("pull: member_id=%s" % mid)

    async with __import__("playwright.async_api", fromlist=["async_playwright"]).async_playwright() as p:
        b, ctx, page = await open_page(p)
        csrf = await grab_csrf(page, FALLBACK_PLANS[0])
        if not csrf:
            wlog("❌ 拿不到 csrfId")
            return {"ok": False, "error": "拿不到 csrfId（页面自己没发 findPage）"}
        await dismiss_overlays(page, rounds=5)   # 进来先把广告弹窗清掉，否则拉一次弹一次

        camps = await discover_campaigns(page, csrf)
        if not camps:
            wlog("  自动发现失败，退回兜底 %d 个计划" % len(FALLBACK_PLANS))
            camps = [{"campaignId": c, "name": "", "status": ""} for c in FALLBACK_PLANS]
        json.dump({"discoveredAt": time.strftime("%Y-%m-%d %H:%M:%S"), "campaigns": camps},
                  io.open(os.path.join(d, "plans.json"), "w", encoding="utf-8"), ensure_ascii=False)

        units, meta = [], {}
        for i, c in enumerate(camps, 1):
            cid = c["campaignId"]
            u, total = await fetch_units(page, csrf, cid)
            units.extend(u)
            meta[cid] = {"count": len(u), "declared": total, "name": c["name"]}
            wlog("  [%d/%d] %s %s : %d 个（声明 %s）"
                 % (i, len(camps), cid, c["name"], len(u), total))
            await asyncio.sleep(0.25)
        fp = os.path.join(d, "units.json")
        json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "memberId": mid,
                   "campaigns": camps, "units": units, "meta": meta},
                  io.open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        wlog("落盘 %d 个单元 / %d 个计划 -> %s" % (len(units), len(camps), fp))
        return {"ok": True, "units": len(units), "campaigns": len(camps),
                "memberId": mid, "meta": meta, "file": fp}


async def cmd_switch(action, ids):
    d, mid = rtdir()
    fp = os.path.join(d, "units.json")
    if not os.path.isfile(fp):
        return {"ok": False, "error": "还没有单元数据，先点「拉数据」"}
    units = json.load(io.open(fp, encoding="utf-8")).get("units") or []
    cid_of = {u["unitId"]: u["campaignId"] for u in units}
    by_cid = {}
    for uid in ids:
        cid = cid_of.get(uid)
        if cid:
            by_cid.setdefault(cid, []).append(uid)
        else:
            wlog("  跳过 %s（缓存里没有这个单元，重新拉数据）" % uid)

    total = 0
    async with __import__("playwright.async_api", fromlist=["async_playwright"]).async_playwright() as p:
        b, ctx, page = await open_page(p)
        seed = list(by_cid.keys())[0] if by_cid else FALLBACK_PLANS[0]
        csrf = await grab_csrf(page, seed)
        if not csrf:
            return {"ok": False, "error": "拿不到 csrfId"}
        await dismiss_overlays(page, rounds=5)   # 切换前也先清一轮
        for cid, uids in by_cid.items():
            wlog("== 计划 %s（%d 个）==" % (cid, len(uids)))
            try:
                await page.goto("%s/index.html#!/manage/search-detail?bizCode=onebpSearch&campaignId=%s&tab=adgroup"
                                % (API_BASE, cid), wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(12)
                await dismiss_overlays(page, rounds=4)   # 进页面先清一轮广告弹窗
            except Exception as e:
                wlog("  进页面失败: %s" % str(e)[:60])
                continue
            total += await do_switch(page, csrf, cid, uids, action)
    out = {"ok": True, "action": action, "requested": len(ids), "done": total, "memberId": mid}
    json.dump(out, io.open(os.path.join(d, "%s_result.json" % action), "w", encoding="utf-8"),
              ensure_ascii=False)
    wlog("完成: %s %d / %d" % (action, total, len(ids)))
    return out


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: pull | pause --ids 1,2 | enable --ids 1,2")
        return
    cmd = args[0]
    ids = []
    for i, a in enumerate(args):
        if a == "--ids" and i + 1 < len(args):
            ids = [x.strip() for x in args[i + 1].split(",") if x.strip()]

    if cmd == "pull":
        r = asyncio.run(cmd_pull())
    elif cmd in ("pause", "enable"):
        r = asyncio.run(cmd_switch("pause" if cmd == "pause" else "start", ids))
    else:
        r = {"ok": False, "error": "未知命令 %s" % cmd}
    print("RESULT " + json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
