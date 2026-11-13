# -*- coding: utf-8 -*-
"""1688 上品前查重：搜「标题/货号」+ 退一步搜前缀 + 进详情页核实 SKU。

为什么需要三层：
  1) 精确 token 匹配（防 MF-R010-2 误伤 MF-R110）
  2) 前缀退让（料号写法不一致——库里写 MF-LSMF300/24X-2，手上是 MF-LSMF300-24X-2，
     直接搜后者必定 0 条，会误判成新品）
  3) 详情页 SKU 核实（同系列多 SKU 合并售卖时，料号只出现在「产品规格」里，
     标题和货号都不含它——MF-SM150/33-2 在 MF-SM150-2 商品里、3314J 在 3314G 商品里，
     都因此被漏判成「未上过」）

判定口径：
  精确命中标题/货号        → 已上过（有独立商品）
  详情页属性表含该料号     → 已上过（是别人/自己商品的 SKU，别重复上）
  搜到结果但两处都不含     → 待人工（可能是邻号，也可能是漏读）
  一条都没搜到             → 未上过（可上）

用法：
  python check_dup_1688.py                      # 查默认清单
  python check_dup_1688.py MF-R110 TBU-CA065-200-WH
不提交、不改商品，只读。
"""
import sys, os, re, csv, json, datetime
from playwright.sync_api import sync_playwright

# --- workbench 路径兼容层：换机器只改 paths.local.json / WB_* 环境变量 ---
def _wb_ws_root():
    """工作区根（OH-WorkSpace）。mat_root = <WS>\\办公室工作\\素材\\产品素材，上溯三级。"""
    import sys as _s
    _here = os.path.dirname(os.path.abspath(__file__))
    for _base in (_here, os.path.dirname(_here), os.path.dirname(os.path.dirname(_here)),
                  os.path.dirname(os.path.dirname(os.path.dirname(_here)))):
        if os.path.isfile(os.path.join(_base, "paths.py")):
            if _base not in _s.path:
                _s.path.insert(0, _base)
            import paths as _p
            _mr = _p.get("mat_root")
            return os.path.dirname(os.path.dirname(os.path.dirname(_mr)))
    _ev = os.environ.get("WB_WS_ROOT")
    if _ev and os.path.isdir(_ev):
        return _ev
    _x = _here
    for _ in range(8):
        if os.path.isdir(os.path.join(_x, "办公室工作")):
            return _x
        _nd = os.path.dirname(_x)
        if _nd == _x:
            break
        _x = _nd
    return r"C:\\Users\\jdt-pty\\Desktop\\OH-WorkSpace"


_WS = _wb_ws_root()
# --- 兼容层结束 ---


BASE = _WS
OUT = os.path.join(BASE, '_工程_新品素材', 'dup_' + datetime.datetime.now().strftime('%m%d_%H%M'))
os.makedirs(OUT, exist_ok=True)

# 详情页核 SKU 时最多翻几个商品（多了很慢，通常第一个就中）
MAX_DETAIL_CHECK = 3


def exact_in(lh, text):
    """料号是否为独立 token（不误伤前缀/后缀相同的型号）。斜杠与短横互认。

    MF-LSMF300-24X-2 与 MF-LSMF300/24X-2 视为同一料号——线上写法是后者，本地为了
    过 Windows 路径把斜杠换成了短横。不能整串 replace（那样 MF-LSMF300-24X-2 会变成
    MF/LSMF300/24X/2），正确做法是拆成片段后用 [-/] 当分隔符，两边都能对上。
    """
    if not lh or not text:
        return False
    parts = [p for p in re.split(r'[-/]', lh) if p]
    if not parts:
        return False
    pat = r'(?<![A-Za-z0-9])' + r'[-/]'.join(re.escape(x) for x in parts) + r'(?![A-Za-z0-9])'
    return bool(re.search(pat, text, re.I))


def prefix_of(lh):
    """取可退让的前缀：第一个分隔符（- 或 /）之前的部分。

    MF-LSMF300-24X-2 -> MF-LSMF300
    MF-SM150/33-2    -> MF-SM150
    3314G-1-103LF    -> 3314G
    """
    for sep in ('-', '/'):
        if sep in lh:
            head = lh.split(sep)[0]
            if len(head) >= 4:          # 太短的前缀会拉回一堆无关商品
                return head
    return ''


class Searcher:
    """封装「往搜索框写词 → 点搜索 → 读结果行」这一套"""

    def __init__(self, page, mframe, fbox, btn):
        self.sp = page
        self.mf = mframe
        self.fbox = fbox
        self.sbx = btn['x'] + (fbox['x'] if fbox else 0)
        self.sby = btn['y'] + (fbox['y'] if fbox else 0)

    def run(self, term, wait_ms=5500):
        self.mf.evaluate("""(lh) => {
            const all = Array.from(document.querySelectorAll('input')).filter(e => e.offsetParent !== null && e.getBoundingClientRect().width >= 100);
            const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
            for (let k = 0; k < 2 && k < all.length; k++) {
                d.set.call(all[k], '');
                all[k].dispatchEvent(new Event('input', {bubbles:true}));
                all[k].dispatchEvent(new Event('change', {bubbles:true}));
            }
            if (all.length >= 2) {
                d.set.call(all[1], lh);
                all[1].dispatchEvent(new Event('input', {bubbles:true}));
                all[1].dispatchEvent(new Event('change', {bubbles:true}));
            }
        }""", term)
        self.sp.wait_for_timeout(400)
        self.sp.mouse.click(self.sbx, self.sby)
        self.sp.wait_for_timeout(wait_ms)
        return self.mf.evaluate("""() => {
            const out = [];
            for (const e of document.querySelectorAll('tr, .ant-table-row')) {
                const t = (e.innerText || '').replace(/\\s+/g, ' ');
                if (t.length < 15 || t.length > 500) continue;
                if (!/ID：|货号：/.test(t)) continue;
                out.push(t);
            }
            return out;
        }""")


def row_id(row):
    m = re.search(r'ID[:：]\s*(\d+)', row)
    return m.group(1) if m else ''


def row_title(row):
    return row.split('货号')[0] if '货号' in row else row.split('ID')[0]


def row_code(row):
    m = re.search(r'货号[:：]\s*([^\s]+)', row)
    return m.group(1) if m else ''


def check_detail_sku(ctx, offer_id, lh):
    """打开商品详情页，看料号是否出现在「产品规格 / 属性」里。

    返回 (是否命中, 说明)。这是补上「同系列多 SKU 合并售卖」漏判的关键一步：
    这类商品标题写主力型号，货号也只写主力型号，料号只藏在规格表里。
    """
    if not offer_id:
        return False, ''
    page = None
    try:
        page = ctx.new_page()
        page.goto('https://detail.1688.com/offer/%s.html' % offer_id,
                  wait_until='domcontentloaded', timeout=25000)
        # 固定等 2.5 秒不够：首次开页经常还没渲染出属性表，会把「已上过」误判成「未上过」。
        # 改为等文本够长且连续两次长度不变（属性表是异步渲染的）。
        txt = ''
        prev = -1
        stable = 0
        for _ in range(14):
            page.wait_for_timeout(700)
            txt = page.evaluate("() => (document.body && document.body.innerText) || ''")
            n = len(txt)
            if n == prev:
                stable += 1
                if stable >= 2 and n > 800:
                    break
            else:
                stable = 0
            prev = n
        if exact_in(lh, txt):
            m = re.search(r'[^\n]{0,40}' + re.escape(lh.replace('/', '-')) + r'[^\n]{0,40}', txt, re.I)
            snip = ''
            if m:
                snip = m.group(0).strip()[:80]
            if not snip:
                m2 = re.search(r'[^\n]{0,40}' + re.escape(lh) + r'[^\n]{0,40}', txt, re.I)
                snip = m2.group(0).strip()[:80] if m2 else ''
            return True, snip or ('详情页含 %s' % lh)
        return False, ''
    except Exception as e:
        return False, 'ERR:' + str(e)[:50]
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass


def main(lhs):
    print('[查重] 待查 %d 个料号' % len(lhs))
    print('[查重] 输出目录:', OUT)

    results = []
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp('http://127.0.0.1:9222')
        ctx = b.contexts[0]
        sp = None
        for pg in ctx.pages:
            u = pg.url or ''
            if 'sellerbase_offer' in u or 'shasngpinguanline' in u:
                sp = pg
                break
        if sp is None:
            print('✗ 商品管理页没打开，请先导航到 商品管理（新）')
            return
        sp.bring_to_front()
        sp.wait_for_timeout(500)

        mf = None
        for f in sp.frames:
            if 'manage-home' in (f.url or ''):
                mf = f
                break
        if mf is None:
            print('✗ 找不到 manage-home frame')
            return
        fbox = mf.frame_element().bounding_box()

        info = mf.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('input')).filter(e => e.offsetParent !== null && e.getBoundingClientRect().width >= 100);
            let searchBtn = null;
            for (const e of document.querySelectorAll('button, a')) {
                if ((e.textContent||'').replace(/\\s+/g,'').trim() === '搜索') {
                    const r = e.getBoundingClientRect();
                    if (r.width > 20) { searchBtn = {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)}; break; }
                }
            }
            return {inputCount: all.length, searchBtn: searchBtn};
        }""")
        print('[查重] 输入框数:', info['inputCount'], ' 搜索按钮:', info['searchBtn'])
        if info['inputCount'] < 2 or not info['searchBtn']:
            print('✗ 页面结构不符，停手')
            return

        sr = Searcher(sp, mf, fbox, info['searchBtn'])

        for n, lh in enumerate(lhs, 1):
            try:
                # ── 第 1 层：搜原料号
                rows = sr.run(lh)
                hit = None
                for row in rows:
                    if exact_in(lh, row_code(row)) or exact_in(lh, row_title(row)):
                        hit = row
                        break

                if hit:
                    status, note, oid = '已上过', '标题/货号精确命中', row_id(hit)
                    print('  [%2d/%d] %-24s ✗ 已上过 (%s) ← 标题/货号' % (n, len(lhs), lh, oid))
                else:
                    # ── 第 2 层：前缀退让（写法不一致时救回来）
                    pfx = prefix_of(lh)
                    if pfx and pfx.upper() != lh.upper():
                        rows2 = sr.run(pfx)
                        for row in rows2:
                            if exact_in(lh, row_code(row)) or exact_in(lh, row_title(row)):
                                hit = row
                                break
                        if hit:
                            status, note, oid = '已上过', '前缀重搜命中', row_id(hit)
                            print('  [%2d/%d] %-24s ✗ 已上过 (%s) ← 前缀 %s' % (n, len(lhs), lh, oid, pfx))
                        rows = rows if len(rows) >= len(rows2) else rows2

                    if not hit:
                        # ── 第 3 层：进详情页核实 SKU
                        ids = []
                        for row in rows[:MAX_DETAIL_CHECK]:
                            _i = row_id(row)
                            if _i and _i not in ids:
                                ids.append(_i)
                        sku_hit, snip = False, ''
                        for _i in ids:
                            sku_hit, snip = check_detail_sku(ctx, _i, lh)
                            if sku_hit:
                                oid = _i
                                break
                        if sku_hit:
                            status, note = '已上过', '详情页属性表含该料号（同商品多 SKU）'
                            print('  [%2d/%d] %-24s ✗ 已上过 (%s) ← SKU: %s' % (n, len(lhs), lh, oid, snip[:50]))
                        elif rows:
                            status, note, oid = '待人工', '搜到 %d 条但标题/货号/详情均无该料号' % len(rows)
                            print('  [%2d/%d] %-24s ? 待人工  (搜到 %d 条，需人工看一眼)' % (n, len(lhs), lh, len(rows)))
                        else:
                            status, note, oid = '未上过', '三层都没命中'
                            print('  [%2d/%d] %-24s ✓ 未上过' % (n, len(lhs), lh))

                results.append({'lianhao': lh, 'status': status, 'note': note,
                                'count': len(rows), 'hit_id': oid,
                                'hit_row': (hit[:150] if hit else '')})
            except Exception as e:
                print('  [%2d/%d] %-24s ! 异常: %s' % (n, len(lhs), lh, str(e)[:60]))
                results.append({'lianhao': lh, 'status': '查询异常', 'note': str(e)[:80],
                                'count': 0, 'hit_id': '', 'hit_row': ''})
            sp.wait_for_timeout(1200)

    out_csv = os.path.join(OUT, 'dup_result.csv')
    with open(out_csv, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['lianhao', 'status', 'note', 'count', 'hit_id', 'hit_row'])
        w.writeheader()
        w.writerows(results)

    print('\n===== 汇总 =====')
    from collections import Counter
    c = Counter(r['status'] for r in results)
    for k, v in c.items():
        print('  %s: %d' % (k, v))
    for tag in ('已上过', '待人工', '未上过'):
        sel = [r for r in results if r['status'] == tag]
        if sel:
            print('\n%s:' % tag)
            for r in sel:
                print('    %-24s %s %s' % (r['lianhao'], ('ID=' + r['hit_id']) if r['hit_id'] else '', r['note']))
    print('\n结果文件:', out_csv)

    # 面板靠 JSON 取结果。同时写文件：stdout 可能被截断（_run_script 只留尾部），
    # 而查重结果里 items 一长串，单靠一行 stdout 解析会静默失败。
    _payload = {
        "ok": True,
        "site": "1688",
        "counts": dict(c),
        "items": results,
        "csv": out_csv,
    }
    out_json = os.path.join(OUT, 'dup_result.json')
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(_payload, f, ensure_ascii=False, indent=1)
    print(json.dumps({"ok": True, "site": "1688", "counts": dict(c),
                      "json": out_json, "total": len(results)}, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) > 1:
        lhs = sys.argv[1:]
    else:
        src = os.path.join(BASE, '_agent_tmp', 'triage_1109', 'results_shop2.csv')
        lhs = []
        if os.path.isfile(src):
            with open(src, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    lh = (row.get('料号') or row.get('lianhao') or '').strip()
                    if lh:
                        lhs.append(lh)
        if not lhs:
            print('没有料号清单，请直接传参：python check_dup_1688.py MF-R110 ...')
            sys.exit(1)
    main(lhs)
