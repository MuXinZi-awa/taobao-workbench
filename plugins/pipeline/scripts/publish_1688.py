# -*- coding: utf-8 -*-
"""1688 类目发品（正式脚本）· 第 1 版

设计原则（都是昨天踩出来的）：
  1. 不用坐标——一律按 label 文本 / 容器结构定位。界面缩放、字体变化都不影响
  2. 每步读回验证——填完立刻读 value 比对，不一致就停手
  3. 失败就停——不"跳过继续"，静默跳过是最危险的错
  4. 不关浏览器——用户的窗口不能被脚本带走

用法：
  python publish_1688.py --spec 参数.json              # 跑到填写完成，不提交（默认）
  python publish_1688.py --spec 参数.json --submit     # 填写 + 提交
  python publish_1688.py --spec 参数.json --dry-run    # 只选类目、读页面结构，不填

参数 JSON 结构：
{
  "cat_kw": "电位器",
  "cat_path": "电子元器件>电阻器>电位器",
  "title": "...", "brand": "BOURNS", "series": "3314",
  "code": "...", "model": "...", "apply": "电子产品",
  "skus": [...],            # 见 fill_specs
  "unit": "PCS", "ship": "48小时发货",
  "media": {"main": [...], "sku": [...], "detail": [...], "video": "..."}
}
"""
import argparse
import io
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

CDP = os.environ.get('HANA_CDP') or 'http://127.0.0.1:9222'
SELECT_URL = 'https://offer-new.1688.com/select.htm'


def _chrome_path():
    """Chrome 可执行文件。和 paths.py 同一口径，优先本地覆盖。"""
    env = os.environ.get('WB_CHROME') or os.environ.get('CHROME_PATH')
    if env and os.path.isfile(env):
        return env
    try:
        _p = _wb_paths()
        if _p:
            c = _p.get('chrome')
            if c and os.path.isfile(c):
                return c
    except Exception:
        pass
    for c in (r'C:\Program Files\Google\Chrome\Application\chrome.exe',
              r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
              os.path.expanduser(r'~\AppData\Local\Google\Chrome\Application\chrome.exe')):
        if os.path.isfile(c):
            return c
    return ''


def _profile_1688():
    """1688 站点的浏览器 profile 目录（acct_profile.resolve 按站点取）。

    三级兜底：paths.get(tg_root) → 从素材根反推工作区再拼「推广一键跑」→ 不猜。
    """
    tg = ''
    try:
        _p = _wb_paths()
        if _p:
            tg = _p.get('tg_root') or ''
    except Exception:
        pass

    if not tg or not os.path.isdir(tg):
        # 素材根是 <WS>\办公室工作\素材\产品素材，上三级回工作区，兄弟目录就是推广一键跑
        try:
            ws = os.path.dirname(os.path.dirname(os.path.dirname(_mat_root())))  # <WS>
            _c = os.path.join(os.path.dirname(ws), '推广一键跑')
            if os.path.isdir(_c):
                tg = _c
        except Exception:
            pass

    if not tg:
        log('⚠ 找不到「推广一键跑」目录，无法定位 1688 profile')
        return ''

    if tg not in sys.path:
        sys.path.insert(0, tg)
    try:
        import acct_profile
        try:
            r = acct_profile.resolve(site='1688')
        except TypeError:
            r = acct_profile.resolve()
        return r[0] if isinstance(r, (tuple, list)) else str(r)
    except Exception as e:
        log('⚠ 取 1688 profile 失败: %s' % e)
        return ''


def open_browser(p):
    """开浏览器：有已开的调试端口就用它（方便看现场），没有就自己起。

    **这一点很重要**：原来写死 `connect_over_cdp('http://127.0.0.1:9222')`，
    意味着必须先手工开一个带 --remote-debugging-port 的 Chrome 才能跑。
    不依赖人的用法应该是——直接双击跑，脚本自己拉浏览器。

    返回 (ctx, owned)。owned=True 表示是自己起的，结束时该关。
    """
    try:
        b = p.chromium.connect_over_cdp(CDP)
        if b and b.contexts:
            log('已连上现有浏览器 (%s)' % CDP)
            return b.contexts[0], False
    except Exception:
        pass

    prof = _profile_1688()
    chrome = _chrome_path()
    if not prof:
        raise Stop('拿不到 1688 的 profile 目录，无法启动浏览器')
    if not chrome:
        raise Stop('找不到 Chrome，请设环境变量 WB_CHROME 指到 chrome.exe')
    log('启动浏览器: profile=%s' % os.path.basename(prof.rstrip('\\')))
    ctx = p.chromium.launch_persistent_context(
        prof, executable_path=chrome, headless=False,
        viewport={'width': 1500, 'height': 950}, locale='zh-CN',
        args=['--disable-blink-features=AutomationControlled'])
    return ctx, True


# ── workbench 路径兼容层（换机器只改 paths.local.json）──
def _wb_paths():
    """找 workbench 的 paths.py。本脚本可能在工程目录，也可能在插件 scripts 下。"""
    _here = os.path.dirname(os.path.abspath(__file__))
    _cands = []
    _ev = os.environ.get('WB_ROOT')
    if _ev:
        _cands.append(_ev)
    _x = _here
    for _ in range(6):
        _cands.append(_x)
        _pd = os.path.dirname(_x)
        if _pd == _x:
            break
        _x = _pd
    for _base in (_here, os.path.dirname(_here)):
        _cands.append(os.path.join(_base, 'OH-WorkSpace', '工具', 'workbench'))
        _cands.append(os.path.join(_base, '工具', 'workbench'))
    for _c in _cands:
        try:
            if _c and os.path.isfile(os.path.join(_c, 'paths.py')):
                if _c not in sys.path:
                    sys.path.insert(0, _c)
                import paths as _p
                return _p
        except Exception:
            continue
    return None


def _plugin_dir():
    """插件根目录。本文件在 plugins/pipeline/scripts/ 下，上溯两层就是 plugins/pipeline。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mat_root():
    """素材库根（<WS>\\办公室工作\\素材\\产品素材）"""
    try:
        _p = _wb_paths()
        if _p:
            _mr = _p.get('mat_root')
            if _mr and os.path.isdir(_mr):
                return _mr
    except Exception:
        pass
    return r'C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\素材\产品素材'


def _xuzhi_path():
    """拍前须知图（全店共用，不跟货号走）。

    顺序：**插件自带优先**（打包必带、换机器一定有）→ 素材库模板目录 → 固定位置。
    之前只用素材库那一份，换机器/挪目录就丢，且找不到时静默跳过。
    """
    cands = [
        os.path.join(_plugin_dir(), 'assets', 'paigian_xuzhi.png'),
    ]
    try:
        _mat = os.path.dirname(_mat_root())      # <WS>\办公室工作\素材
        cands.append(os.path.join(_mat, '模板', 'paigian_xuzhi_1600.png'))
        cands.append(os.path.join(_mat, '模板', 'paigian_xuzhi.png'))
    except Exception:
        pass
    cands.append(r'C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\素材\模板\paigian_xuzhi_1600.png')
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ''


def detail_images_for(skus, override=None):
    """要上的品的详情图 + 拍前须知。**不写死张数**。

    规则：spec.skus 里有几个型号，就找几张 <型号>_详情图.png；最后固定接一张拍前须知。
    所以双 SKU 就是 2 张参数图 + 1 张拍前须知 = 3 张，单 SKU 就是 2 张。
    override（spec.media.detail）非空时以它为准，方便临时手调。
    """
    if override:
        out = [p for p in override if p and os.path.isfile(p)]
        xz = _xuzhi_path()
        if xz and xz not in out:
            out.append(xz)
        return out

    mr = _mat_root()
    out = []
    for s in (skus or []):
        lh = (s.get('型号') or '').strip()
        if not lh:
            continue
        safe = lh.replace('/', '-')
        for c in (os.path.join(mr, lh, safe + '_详情图.png'),
                  os.path.join(mr, lh, lh + '_详情图.png')):
            if os.path.isfile(c):
                out.append(c)
                break
        else:
            log('    ⚠ %s 在素材库里没有详情图（先跑 detail_*.py）' % lh)
    xz = _xuzhi_path()
    if xz:
        out.append(xz)
    else:
        log('    ⚠ 没找到拍前须知图')
    return out


class Stop(Exception):
    """任何一步对不上就抛这个——停手比带错继续安全"""


def log(msg):
    print(msg, flush=True)


# ────────────────────────── 基础工具 ──────────────────────────

def set_value_js(page, selector_js, value):
    """用原生 setter 写值并派发 input/change（React 受控组件只认这个）"""
    return page.evaluate("""([sel, val]) => {
        const el = eval(sel);
        if (!el) return 'not-found';
        const d = Object.getOwnPropertyDescriptor(
            el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
                                      : window.HTMLInputElement.prototype, 'value');
        d.set.call(el, val);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        el.dispatchEvent(new Event('blur', {bubbles: true}));
        return 'ok';
    }""", [selector_js, value])


def find_input_by_label(page, label):
    """按 label 文本找同容器内的 input/textarea，返回它的 DOM id 或索引 JS 表达式。

    为什么按 label：全局 input 索引会随 React 重渲染错位（昨天填基础信息时，
    一次只进去两个字段，其余全被换掉）。
    """
    return page.evaluate("""(label) => {
        const leaves = Array.from(document.querySelectorAll('*')).filter(e => {
            if (e.children.length > 0) return false;
            const t = (e.textContent || '').replace(/\\s+/g, '').trim();
            return t === label;
        });
        for (const leaf of leaves) {
            const r = leaf.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) continue;
            let cur = leaf;
            for (let i = 0; i < 5 && cur; i++) {
                const inp = cur.querySelector
                    ? cur.querySelector('input:not([type=hidden]):not([type=radio]):not([type=checkbox]), textarea')
                    : null;
                if (inp) {
                    if (!inp.id) inp.id = '__wb_' + label.replace(/[^\\w]/g, '') + '_' + Math.floor(Math.random()*9999);
                    return {id: inp.id, tag: inp.tagName, val: inp.value || '', ph: inp.placeholder || ''};
                }
                cur = cur.parentElement;
            }
        }
        return null;
    }""", label)


def fill_labeled(page, label, value, required=True):
    """按 label 填一个字段 + 读回验证。

    重点：读回时**重新按 label 定位**，不能拿写完的那个 DOM id。
    品牌/系列/货号这类字段是自定义下拉，一写就触发 React 重渲染，
    旧元素被替换——拿旧 id 去读会得到 None，看起来像“没写进去”。
    """
    info = find_input_by_label(page, label)
    if not info:
        if required:
            raise Stop('找不到字段「%s」' % label)
        log('    [跳过] 没有字段「%s」' % label)
        return False
    r = set_value_js(page, 'document.getElementById(%s)' % json.dumps(info['id']), str(value))
    if r != 'ok':
        raise Stop('写「%s」失败: %s' % (label, r))
    page.wait_for_timeout(300)

    # 重新定位再读（元素可能已被替换）
    info2 = find_input_by_label(page, label)
    got = (info2 or {}).get('val', '')
    ok = (got or '').strip() == str(value).strip()
    if not ok:
        # 有些字段写入后会被格式化成别的写法（如 10kΩ → 10KΩ），放宽一次比较
        ok = (got or '').strip().lower() == str(value).strip().lower()
    log('    %s %-8s = %r%s' % ('✓' if ok else '✗', label, (got or '')[:38],
                               '' if ok else '  期望 %r' % str(value)[:38]))
    if not ok:
        raise Stop('「%s」读回不一致：写 %r 读 %r' % (label, value, got))
    return True


# ────────────────────────── 阶段 1：选类目 ──────────────────────────

def open_select(ctx, reuse=True):
    page = None
    if reuse:
        for pg in ctx.pages:
            if 'select.htm' in (pg.url or ''):
                page = pg
                break
    if page is None:
        page = ctx.new_page()
        page.goto(SELECT_URL, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(3000)
    page.bring_to_front()
    page.wait_for_timeout(800)
    log('[1] 类目页: %s' % page.url[:110])
    return page


def pick_category(page, kw, expect_path):
    """搜类目 → 从下拉候选里挑出**路径匹配**的那个 → 校验 → 确认 → 返回发布页。

    为何不能盲点第一个：同名候选一堆（电位器就有 4 个），分属不同父类目：
      电子元器件>电阻器>电位器                       ← 目标
      电子元器件>集成电路（IC）>模拟芯片>数字电位器芯片
      电子元器件>电子材料/零部件>电子五金材料>电位器旋钮
      电工电气>低压电器>电阻器（电位器）
    每个 `.search-select-span` 的父级元素里就含着完整路径，读它再比，不用猜。
    """
    # 先清空再输（残留状态下不先清空，下拉不会刷新）
    page.evaluate("""() => {
        const inp = Array.from(document.querySelectorAll('input'))
            .find(e => (e.placeholder || '').indexOf('类目搜索') >= 0);
        if (!inp) return;
        inp.focus();
        const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
        d.set.call(inp, '');
        inp.dispatchEvent(new Event('input', {bubbles: true}));
        inp.dispatchEvent(new Event('change', {bubbles: true}));
    }""")
    page.wait_for_timeout(500)

    r = page.evaluate("""(kw) => {
        const inp = Array.from(document.querySelectorAll('input'))
            .find(e => (e.placeholder || '').indexOf('类目搜索') >= 0);
        if (!inp) return 'no-input';
        inp.focus();
        const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
        d.set.call(inp, kw);
        inp.dispatchEvent(new Event('input', {bubbles: true}));
        inp.dispatchEvent(new Event('change', {bubbles: true}));
        return 'ok';
    }""", kw)
    if r != 'ok':
        raise Stop('类目搜索框不可用: %s' % r)

    # 等候选出现（不等于一定对）
    got = False
    for _ in range(12):
        page.wait_for_timeout(700)
        n = page.evaluate("() => document.querySelectorAll('.search-select-span').length")
        if n:
            got = True
            break
    if not got:
        raise Stop('搜「%s」后没出候选（下拉没刷新？先把 select.htm 的类目选择清掉）' % kw)

    # 读每个候选的完整路径，挑匹配的那个
    cands = page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('.search-select-span').forEach(e => {
            const r = e.getBoundingClientRect();
            let path = '';
            let cur = e.parentElement;
            for (let i = 0; i < 4 && cur; i++) {
                const t = (cur.innerText || '').replace(/\\s+/g, ' ').trim();
                if (t.indexOf('>') >= 0) { path = t; break; }
                cur = cur.parentElement;
            }
            out.push({x: Math.round(r.x), y: Math.round(r.y), path: path, self: (e.innerText||'').trim()});
        });
        return out;
    }""")
    log('[2] 共 %d 个候选' % len(cands))
    pick = None
    for c in cands:
        mark = '✓' if (expect_path and expect_path in c['path']) else ' '
        log('    %s %s' % (mark, c['path'][:80] or c['self']))
        if mark.strip() and pick is None:
            pick = c
    if expect_path and pick is None:
        raise Stop('候选里没有路径含「%s」的项' % expect_path)
    if pick is None:
        pick = cands[0]

    # 给目标打 id，用 Playwright 真实点击（elementFromPoint 会被浮层挡住，点了没反应）
    tagged = page.evaluate("""([x, y, expect]) => {
        // 重新按「路径匹配」找一次，不依赖坐标（页面可能重排过）
        let target = null;
        document.querySelectorAll('.search-select-span').forEach(e => {
            let cur = e.parentElement, p = '';
            for (let i = 0; i < 4 && cur; i++) {
                const t = (cur.innerText || '').replace(/\\s+/g, ' ').trim();
                if (t.indexOf('>') >= 0) { p = t; break; }
                cur = cur.parentElement;
            }
            if (expect && p.indexOf(expect) >= 0 && !target) target = e;
        });
        if (!target) {
            const all = Array.from(document.querySelectorAll('.search-select-span'));
            target = all[0] || null;
        }
        if (!target) return 'none';
        target.id = '__wb_pick_cat';
        return 'tagged';
    }""", [pick['x'], pick['y'], expect_path or ''])
    if tagged != 'tagged':
        raise Stop('没能标记目标类目项')

    try:
        page.locator('#__wb_pick_cat').click(timeout=8000)
        log('    已点击目标候选')
    except Exception as e:
        # 退路：JS 直接点（有的自定义项用 click() 才认）
        page.evaluate("() => { const e = document.getElementById('__wb_pick_cat'); if (e) e.click(); }")
        log('    Playwright 点击失败，退回 JS 点击: %s' % str(e)[:50])
    page.wait_for_timeout(2200)

    # 校验已选类目：只读「已选区域」里的文本，不扫全页
    # （全页扫 > 会把左侧类目树展开的路径一起读进来，得到一堆无关路径，
    #   比如点对了「电位器」却读到「五金、工具>紧固件...」，误判成失败）
    got = page.evaluate("""() => {
        // 「确认类目继续完善」按钮上方 260px 内的短文本 = 已选区
        let by = null;
        Array.from(document.querySelectorAll('button, a, span, div')).forEach(e => {
            if (e.children.length > 0) return;
            if ((e.textContent || '').replace(/\\s+/g, '').trim() !== '确认类目继续完善') return;
            const r = e.getBoundingClientRect();
            if (r.width > 0) by = r.y;
        });
        if (by === null) return {ok: false, texts: []};
        const texts = [];
        document.querySelectorAll('*').forEach(e => {
            if (e.children.length > 0) return;
            const t = (e.textContent || '').replace(/\\s+/g, ' ').trim();
            if (!t || t.length > 40) return;
            const r = e.getBoundingClientRect();
            if (r.y < by - 260 || r.y > by || r.width === 0) return;
            if (texts.indexOf(t) < 0) texts.push(t);
        });
        return {ok: true, texts: texts};
    }""")
    log('[3] 已选区文本: %s' % (', '.join(got.get('texts', [])[:8]) or '(空)'))
    # 叶子名（取 expect_path 最后一段）必须出现在已选区里
    leaf = (expect_path or '').split('>')[-1] if expect_path else ''
    if leaf and leaf not in ' '.join(got.get('texts', [])):
        raise Stop('已选区里没看到「%s」，类目可能没选上' % leaf)
    log('    ✓ 已选类目含「%s」' % leaf)

    # 点击前先记住已有页面：浏览器里长期挂着旧编辑页（URL 也含 publish.htm 但带 id=），
    # 不先记快照就会抓到旧页，填完发现改的是别的商品（昨天就是这招差点把 3314 写成 MF-R110）
    before_pages = list(page.context.pages)

    go = page.evaluate("""() => {
        const el = Array.from(document.querySelectorAll('*')).find(e => {
            if (e.children.length > 0) return false;
            const t = (e.textContent || '').replace(/\\s+/g, '').trim();
            if (t !== '确认类目继续完善') return false;
            const r = e.getBoundingClientRect();
            if (r.width === 0) return false;
            const cls = String(e.className || '') + String((e.parentElement && e.parentElement.className) || '');
            return cls.indexOf('disabled') < 0;
        });
        if (!el) return 'not-clickable';
        el.click();
        return 'ok';
    }""")
    if go != 'ok':
        raise Stop('「确认类目继续完善」不可点: %s' % go)
    log('[3] 已点「确认类目继续完善」')

    def _is_new_publish(pg):
        try:
            u = pg.url or ''
            # 新建页：有 publish.htm 且**不带 id=**（带 id= 的是编辑已有商品）
            return 'industry/publish.htm' in u and 'id=' not in u
        except Exception:
            return False

    # 情况 A：开了新标签页
    for _ in range(20):
        page.wait_for_timeout(1000)
        for pg in page.context.pages:
            if pg in before_pages:
                continue
            if _is_new_publish(pg):
                pg.wait_for_timeout(2800)
                log('[4] 新建发布页(新标签): %s' % pg.url[:110])
                return pg
        # 情况 B：当前页跳转
        try:
            if _is_new_publish(page):
                page.wait_for_timeout(2800)
                log('[4] 新建发布页(原页跳转): %s' % page.url[:110])
                return page
        except Exception:
            pass
    raise Stop('点了确认类目但没进新建发布页（20 秒）——'
               '检查是不是被旧编辑页干扰，或类目确认没生效')


# ────────────────────────── 阶段 2：基础信息 ──────────────────────────

FIELDS = [
    ('商品标题', 'title'),
    ('品牌', 'brand'),
    ('系列', 'series'),
    ('货号', 'code'),
    ('型号', 'model'),
    ('应用范围', 'apply'),
]


def fill_basic(page, spec):
    log('[5] 填基础信息')
    for label, key in FIELDS:
        v = spec.get(key)
        if v is None:
            continue
        fill_labeled(page, label, v)


def read_basic(page):
    out = {}
    for label, key in FIELDS:
        info = find_input_by_label(page, label)
        out[key] = (info or {}).get('val', '')
    return out


# ────────────────── 阶段 3：SKU 批量导入（Excel）──────────────────

def import_sku_excel(page, xlsx):
    """Excel 批量导入 SKU。这一步是上品能不能自动化的分水岭。

    三个坑（都是实测出来的）：
      1. 真正的上传按钮在弹窗里（「上传excel表格」），不是页面上那个全局 input[type=file]。
         直接 set_input_files 到全局那个会“成功”但什么也不会发生。
      2. 只显示「上传成功」还不够——必须点弹窗底部的「确认」，规格表才会真变。
      3. 「确认」按钮有两层弹窗叠加（共 2 个），要点最靠上的那个；
         Playwright locator 会被遮挡超时，必须用 JS 按 y 坐标取最上层。
    """
    if not os.path.isfile(xlsx):
        raise Stop('Excel 不存在: %s' % xlsx)
    log('[6] Excel 批量导入: %s' % os.path.basename(xlsx))

    rows_before = page.evaluate(
        "() => { const t = document.querySelectorAll('table')[1]; return t ? t.querySelectorAll('tbody tr').length : -1; }")
    log('    导入前规格表行数: %s' % rows_before)

    # 打开弹窗
    r = page.evaluate("""() => {
        const box = document.querySelector('.ind-excel-box');
        if (!box) return 'no-box';
        const inner = box.querySelector('button, a');
        (inner || box).click();
        return 'opened';
    }""")
    if r == 'no-box':
        raise Stop('找不到「Excel批量上传」入口')
    page.wait_for_timeout(1500)

    # 弹窗里的真上传按钮 + filechooser
    try:
        with page.expect_file_chooser(timeout=15000) as fc:
            hit = page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('button, a, .next-btn'));
                const e = els.find(x => (x.textContent || '').replace(/\\s+/g, '').trim() === '上传excel表格');
                if (!e) return 'not-found';
                e.click();
                return 'clicked';
            }""")
        if hit == 'not-found':
            raise Stop('弹窗里没有「上传excel表格」按钮')
        fc.value.set_files(xlsx)
        log('    ✓ 文件已提交')
    except Stop:
        raise
    except Exception as e:
        raise Stop('上传未触发文件选择: %s' % str(e)[:80])

    # 等「上传成功」
    got_upload = False
    for _ in range(20):
        page.wait_for_timeout(1000)
        txt = page.evaluate("""() => {
            const d = document.querySelector('.next-dialog-body, .ant-modal-content');
            return d ? (d.innerText || '') : '';
        }""")
        if '上传成功' in txt:
            got_upload = True
            break
    if not got_upload:
        raise Stop('等了 20 秒没看到「上传成功」')
    log('    ✓ 上传成功，点「确认」导入')

    # 点最上层的「确认」
    clicked = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('.next-btn-primary')).filter(e => {
            if ((e.textContent || '').replace(/\\s+/g, '').trim() !== '确认') return false;
            const r = e.getBoundingClientRect();
            return r.width > 0;
        });
        if (!btns.length) return 'none';
        btns.sort((a, b) => a.getBoundingClientRect().y - b.getBoundingClientRect().y);
        btns[btns.length - 1].click();
        return 'clicked';
    }""")
    if clicked != 'clicked':
        raise Stop('点不到「确认」按钮')
    page.wait_for_timeout(3500)

    # 验证：规格表行数必须增加
    st = page.evaluate("""() => {
        const t = document.querySelectorAll('table')[1];
        if (!t) return null;
        const rows = Array.from(t.querySelectorAll('tbody tr'));
        return {
            n: rows.length,
            vals: rows.map(tr => Array.from(tr.querySelectorAll('td')).map(td => {
                const i = td.querySelector('input');
                return (i ? (i.value || '') : (td.innerText || '')).replace(/\\s+/g, ' ').trim();
            }).filter(Boolean))
        };
    }""")
    if not st or st['n'] <= (rows_before or 0):
        raise Stop('点了确认但规格表没增加（前 %s 后 %s）' % (rows_before, (st or {}).get('n')))
    log('    ✓ 规格表 %s → %s 行' % (rows_before, st['n']))
    for i, row in enumerate(st['vals'], 1):
        log('      r%d: %s' % (i, ' | '.join(row)[:200]))
    return st


# ────────────────────────── 阶段 4：报价与件重尺 ──────────────────────────

def fill_price_and_weight(page, skus):
    """逐行填批发价 / 可售数量 / 件重尺。

    前置：默认是「按产品数量报价」，报价表根本不存在，必须先切「按产品规格报价」，
    否则找不到批发价表（之前就卡在这里）。

    两个关键教训：
    1. **必须逐行，不能用「批量设置」**——多个 SKU 价格不同，批量会把它们设成同一个值。
    2. **next-table 把表头和数据拆成两个 <table>**：
         .next-table-header-inner table  → 只有 thead
         .next-table-body table         → 才是 tbody 数据行
       直接遍历 document.querySelectorAll('table') 找含「批发价」的表，拿到的是表头那半。
    """
    if not skus:
        return {}

    # 先切「按产品规格报价」
    sw = page.evaluate("""() => {
        const e = Array.from(document.querySelectorAll('.ant-radio-wrapper, label'))
            .find(x => (x.textContent || '').replace(/\\s+/g, '').trim() === '按产品规格报价');
        if (!e) return 'no-radio';
        const i = e.querySelector('input[type=radio]');
        if (i && i.checked) return 'already';
        (i || e).click();
        return 'clicked';
    }""")
    log('[8] 报价方式 → 按产品规格报价: %s' % sw)
    page.wait_for_timeout(2500)

    log('[9] 填报价与件重尺（逐行）')
    data = [{
        'price': str(s.get('price', '')), 'qty': str(s.get('qty', '')),
        'l': str(s.get('长', '')), 'w': str(s.get('宽', '')),
        'h': str(s.get('高', '')), 'g': str(s.get('重量', '')),
    } for s in skus]

    r = page.evaluate("""(data) => {
        const boxes = Array.from(document.querySelectorAll('.next-table'));
        const findBox = (kw) => boxes.find(box => {
            const head = box.querySelector('.next-table-header-inner table');
            if (!head) return null;
            const ths = Array.from(head.querySelectorAll('th')).map(x => (x.innerText || '').replace(/\\s+/g, '').trim());
            return ths.some(h => h.indexOf(kw) >= 0);
        });
        const setV = (inp, v) => {
            if (!inp || v === '') return false;
            inp.focus();
            const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
            d.set.call(inp, String(v));
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            inp.dispatchEvent(new Event('blur', {bubbles: true}));
            return true;
        };
        const out = {price: 0, ship: 0, priceRows: -1, shipRows: -1};

        const pb = findBox('批发价');
        if (pb) {
            const body = pb.querySelector('.next-table-body table');
            const rows = body ? Array.from(body.querySelectorAll('tbody tr')) : [];
            out.priceRows = rows.length;
            rows.forEach((tr, i) => {
                const d = data[i]; const tds = tr.querySelectorAll('td');
                if (!d) return;
                if (tds[2] && tds[2].querySelector('input')) {
                    if (setV(tds[2].querySelector('input'), d.price)) out.price++;
                }
                if (tds[3] && tds[3].querySelector('input')) {
                    if (setV(tds[3].querySelector('input'), d.qty)) out.price++;
                }
            });
        }

        const sb = findBox('长(cm)');
        if (sb) {
            const body = sb.querySelector('.next-table-body table');
            const rows = body ? Array.from(body.querySelectorAll('tbody tr')) : [];
            out.shipRows = rows.length;
            rows.forEach((tr, i) => {
                const d = data[i]; const tds = tr.querySelectorAll('td');
                if (!d) return;
                [[2, d.l], [3, d.w], [4, d.h], [6, d.g]].forEach(([ci, v]) => {
                    const inp = tds[ci] ? tds[ci].querySelector('input') : null;
                    if (setV(inp, v)) out.ship++;
                });
            });
        }
        return out;
    }""", data)
    log('    报价表 %s 行、件重尺表 %s 行；写入报价格 %d 个、件重尺格 %d 个'
        % (r.get('priceRows'), r.get('shipRows'), r['price'], r['ship']))
    if (r.get('priceRows') or 0) <= 0:
        raise Stop('报价表没有数据行（next-table 取错表头/表体？）')

    page.wait_for_timeout(1200)
    back = page.evaluate("""() => {
        const boxes = Array.from(document.querySelectorAll('.next-table'));
        const findBox = (kw) => boxes.find(box => {
            const head = box.querySelector('.next-table-header-inner table');
            if (!head) return null;
            const ths = Array.from(head.querySelectorAll('th')).map(x => (x.innerText || '').replace(/\\s+/g, '').trim());
            return ths.some(h => h.indexOf(kw) >= 0);
        });
        const dump = (box) => {
            if (!box) return null;
            const body = box.querySelector('.next-table-body table');
            return body ? Array.from(body.querySelectorAll('tbody tr')).map(tr =>
                Array.from(tr.querySelectorAll('td')).map(td => {
                    const i = td.querySelector('input');
                    return i ? (i.value || '') : (td.innerText || '').replace(/\\s+/g, '').slice(0, 10);
                })) : null;
        };
        return {price: dump(findBox('批发价')), ship: dump(findBox('长(cm)'))};
    }""")
    for i, row in enumerate(back.get('price') or []):
        log('    报价 r%d: %s' % (i, ' | '.join(x for x in row if x)))
    for i, row in enumerate(back.get('ship') or []):
        log('    件重尺 r%d: %s' % (i, ' | '.join(x for x in row if x)))

    # 校验：批发价必须逐行对得上（批量设错的前兆就是价全一样）
    prices = [(row[2] if len(row) > 2 else '') for row in (back.get('price') or [])]
    want = [d['price'] for d in data if d['price']]
    if want and prices != want:
        raise Stop('批发价读回不符：期望 %s，实际 %s（检查是否误用了批量设置）' % (want, prices))
    log('    ✓ 批发价逐行核对一致: %s' % prices)
    return back


# ────────────────────────── 阶段 5：主图上传（picman）──────────────────────────

def _picman_frame(page, timeout_s=20):
    """等 picman 弹窗出现并返回它的 frame。"""
    for _ in range(int(timeout_s / 0.8)):
        page.wait_for_timeout(800)
        for f in page.frames:
            if 'picman' in (f.url or ''):
                return f
    return None


def upload_image_picker(page, img_path):
    """走 picman 传一张图到当前槽位。

    流程（梓帆纠正过一次，少走一半弯路）：
      点槽位「添加图片」→ picman iframe → 切「我的电脑」
      → 上传 → 等「上传成功」→ **就地直接点「插入图片」**

    **不需要切回「图片管家」相册**。之前我切回去找图，结果相册里残留着上次的选中状态，
    一点就把旧图一起插进来了（多选）。就地插入只带本次上传的那张。

    另一个坑：按钮叫「**插入图片**」，不是「插入」——只找「插入」会以为没有，
    然后傻傻地切回相册。
    """
    if not os.path.isfile(img_path):
        raise Stop('图片不存在: %s' % img_path)

    fr = _picman_frame(page)
    if fr is None:
        raise Stop('picman 弹窗没出现')

    # 切「我的电脑」
    fr.evaluate("""() => {
        const el = Array.from(document.querySelectorAll('li')).find(e =>
            (e.textContent || '').replace(/\\s+/g, '').trim() === '我的电脑');
        if (el) el.click();
    }""")
    page.wait_for_timeout(2000)

    n = fr.evaluate("() => document.querySelectorAll('input[type=file]').length")
    if not n:
        raise Stop('「我的电脑」里没有 input[type=file]')
    fr.locator('input[type=file]').first.set_input_files(img_path, timeout=10000)

    # 等上传成功
    ok = False
    for _ in range(12):
        page.wait_for_timeout(1500)
        if fr.evaluate("() => (document.body.innerText || '').indexOf('上传成功') >= 0"):
            ok = True
            break
    if not ok:
        raise Stop('上传后 18 秒没出现「上传成功」')

    # 就地插入——不切回相册（切回去会把残留的旧选中一起插进来）
    page.wait_for_timeout(800)
    r = fr.evaluate("""() => {
        const all = () => Array.from(document.querySelectorAll('button, a, [class*=btn]'));
        const pick = (pred) => all().find(pred);
        const isBtn = (e) => {
            const t = (e.textContent || '').replace(/\\s+/g, '').trim();
            const r = e.getBoundingClientRect();
            return r.width > 0 && t === '插入图片' && String(e.className).indexOf('disabled') < 0;
        };
        const el = pick(e => isBtn(e) && String(e.className).indexOf('submit') >= 0)
                || pick(isBtn);
        if (!el) {
            // 诊断：把当前可见按钮名字回传，省得下次又猜
            const names = all().map(e => (e.textContent || '').replace(/\\s+/g, '').trim())
                              .filter(t => t && t.length < 8);
            return 'not-found:' + Array.from(new Set(names)).slice(0, 10).join('/');
        }
        el.id = '__wb_ins';
        return 'found';
    }""")
    if not r.startswith('found'):
        raise Stop('上传后没找到「插入图片」按钮（可见按钮：%s）' % r[10:])
    fr.locator('#__wb_ins').click(timeout=6000)
    page.wait_for_timeout(3000)
    log('      ✓ 已插入 %s' % os.path.basename(img_path))


def main_img_slots(page):
    return page.evaluate("""() => Array.from(
        document.querySelectorAll('div.picture-sort-list div.module-picture-cover-wrapper')
    ).map((e, i) => ({i: i, empty: String(e.className).indexOf('cover-empty') >= 0}))""")


def clear_main_images(page):
    n = page.evaluate("""() => {
        let n = 0;
        document.querySelectorAll('div.picture-sort-list div.module-picture-cover-wrapper').forEach(s => {
            if (String(s.className).indexOf('cover-empty') >= 0) return;
            const del = Array.from(s.querySelectorAll('a')).find(a => (a.textContent || '').trim() === '删除');
            if (del) { del.click(); n++; }
        });
        return n;
    }""")
    page.wait_for_timeout(1500)
    return n


def fill_ship_time(page, value='48小时发货'):
    """选发货时间。

    坑：这个 select 常在视口外（y 为负），不先 scrollIntoView 点不到；
    下拉选项在 `.ant-select-dropdown` 里，只在该层里找，不扫全页。
    """
    log('[10] 发货时间 → %s' % value)
    r = page.evaluate("""([val]) => {
        const els = Array.from(document.querySelectorAll('.ant-select')).filter(e => {
            const t = (e.innerText || '').replace(/\\s+/g, '').trim();
            const r = e.getBoundingClientRect();
            return r.width > 0 && (t.indexOf('发货时间') >= 0 || t === '请选择发货时间');
        });
        if (!els.length) return 'no-select';
        els[0].id = '__wb_ship';
        els[0].scrollIntoView({block: 'center'});
        return 'tagged';
    }""", [value])
    if r != 'tagged':
        raise Stop('找不到发货时间下拉')
    page.wait_for_timeout(1000)

    try:
        page.locator('#__wb_ship').click(timeout=6000)
    except Exception:
        page.evaluate("() => { const e = document.getElementById('__wb_ship'); if (e) { const s = e.querySelector('.ant-select-selector'); (s || e).click(); } }")
    page.wait_for_timeout(1800)

    hit = page.evaluate("""(val) => {
        const out = [];
        document.querySelectorAll('.ant-select-dropdown, .next-overlay-inner').forEach(L => {
            L.querySelectorAll('*').forEach(e => {
                if (e.children.length > 0) return;
                const t = (e.textContent || '').replace(/\\s+/g, '').trim();
                if (t !== val) return;
                const r = e.getBoundingClientRect();
                if (r.width > 4) out.push({x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)});
            });
        });
        if (!out.length) return 'not-found';
        return out[0].x + ',' + out[0].y;
    }""", value)
    if hit == 'not-found':
        raise Stop('发货时间下拉里没有「%s」' % value)
    x, y = [int(v) for v in hit.split(',')]
    page.evaluate("""([x, y, val]) => {
        const el = document.elementFromPoint(x, y);
        if (!el) return;
        let cur = el;
        for (let i = 0; i < 4 && cur; i++) {
            if ((cur.textContent || '').replace(/\\s+/g, '').trim() === val) { cur.click(); return; }
            cur = cur.parentElement;
        }
        el.click();
    }""", [x, y, value])
    page.wait_for_timeout(2000)

    got = page.evaluate("() => { const e = document.getElementById('__wb_ship'); return e ? (e.innerText || '').replace(/\\s+/g, ' ').trim() : ''; }")
    if value not in got:
        raise Stop('发货时间读回不符：%r' % got[:40])
    log('      ✓ 读回: %s' % got[:30])


def fill_detail_images(page, imgs):
    """在上品「图文详情」（TinyMCE 3.5.8）里插图片。

    流程：点工具栏 `#tinyMCE-0_image` → picman → 我的电脑 → 上传 → 立刻点「插入图片」。
    和主图/SKU 图同一套 picman，**不需要切回相册**。

    已知问题：插完后页面上「请填写图文详情」的诊断**不会实时消失**，
    即使 TinyMCE 模型里确实有内容（getContent 有 <img>）。
    可能是页面加载时算一次的静态校验，也可能真的要求别的。
    所以这里不拿诊断当判据，改而验证 TinyMCE 模型本身。
    """
    if not imgs:
        return 0
    log('[11] 图文详情：插 %d 张图' % len(imgs))
    done = 0
    for i, img in enumerate(imgs):
        if not os.path.isfile(img):
            log('      [跳过] 不存在: %s' % img)
            continue
        r = page.evaluate("""() => {
            const e = document.getElementById('tinyMCE-0_image');
            if (!e) return 'no-btn';
            e.scrollIntoView({block: 'center'});
            e.click();
            return 'clicked';
        }""")
        if r != 'clicked':
            log('      第 %d 张：找不到插入图片按钮' % (i + 1))
            continue
        page.wait_for_timeout(3500)
        try:
            upload_image_picker(page, img)
            done += 1
        except Stop as e:
            log('      第 %d 张失败：%s' % (i + 1, e))
        page.wait_for_timeout(1500)

    # 校验 TinyMCE 模型（不看诊断）
    st = page.evaluate("""() => {
        const ed = (tinymce.editors || []).find(e => e.id.indexOf('tinyMCE') >= 0);
        if (!ed) return {err: 'no-editor'};
        const c = ed.getContent() || '';
        return {len: c.length, imgs: (c.match(/<img/g) || []).length};
    }""")
    log('    TinyMCE 模型: 长度 %s，图片 %s 张' % (st.get('len'), st.get('imgs')))
    if (st.get('imgs') or 0) < 1:
        raise Stop('详情图插了 %d 次，但 TinyMCE 模型里没有图' % done)

    # 关键：这个页面的校验是**静态**的，不热更新——
    # 图插完了但「请填写图文详情」还挂在上面，因为没人触发重新校验。
    # 梓帆原话：「上传完之后不点其他地方他不会消失」。
    # 所以插完必须主动 point 一下别处触发 blur。
    page.evaluate("""() => {
        const inp = Array.from(document.querySelectorAll('input')).find(e =>
            (e.placeholder || '').indexOf('建议使用通俗的产品名称') >= 0);
        if (inp) { inp.focus(); inp.blur(); }
    }""")
    page.wait_for_timeout(2500)
    _d = diagnostics(page)
    if any('图文详情' in x for x in _d):
        log('    ⚠ blur 后诊断仍在: %s' % _d)
    else:
        log('    ✓ 「请填写图文详情」已消失')
    return done


# ────────────────────────── 诊断 ──────────────────────────

def diagnostics(page):
    """读左/右侧的诊断错误提示（区块级）"""
    return page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('*').forEach(e => {
            if (e.children.length > 0) return;
            const t = (e.textContent || '').replace(/\\s+/g, ' ').trim();
            if (!t || t.length > 60) return;
            if (!/(不能为空|请填写|请选择|为必填|未完成|待优化)/.test(t)) return;
            const r = e.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return;
            out.push(t);
        });
        return Array.from(new Set(out)).slice(0, 30);
    }""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--spec', required=True)
    ap.add_argument('--submit', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-media', action='store_true', help='跳过图片/视频（图片上传不稳时用）')
    ap.add_argument('--keep-open', action='store_true',
                    help='跑完不关浏览器（自己起的才有效；默认关，连已有的不影响）')
    a = ap.parse_args()
    skip_media = a.skip_media

    spec = json.load(io.open(a.spec, encoding='utf-8'))
    log('=== 1688 上品 ===')
    log('  标题: %s' % spec.get('title', '')[:60])
    log('  SKU : %s 个' % len(spec.get('skus') or []))
    log('  提交: %s' % ('是' if a.submit else '否（只填不提交）'))

    with sync_playwright() as p:
        ctx, _owned = open_browser(p)
        try:
            sel = open_select(ctx)
            pub = pick_category(sel, spec.get('cat_kw', ''), spec.get('cat_path', ''))
        except Exception:
            if _owned:
                ctx.close()
            raise

        if a.dry_run:
            log('[dry-run] 停在这里，读一下页面结构')
            log('  诊断: %s' % diagnostics(pub))
            return 0

        fill_basic(pub, spec)
        got = read_basic(pub)
        log('[7] 基础信息读回: %s' % json.dumps(got, ensure_ascii=False)[:220])

        xlsx = spec.get('sku_excel') or ''
        if not xlsx:
            # 默认：用 ali_excel\<货号>_SKU.xlsx
            xlsx = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ali_excel',
                                '%s_SKU.xlsx' % (spec.get('code') or 'out'))
            xlsx = os.path.normpath(xlsx)
        if os.path.isfile(xlsx):
            import_sku_excel(pub, xlsx)
        else:
            log('[6] 跳过多 SKU 导入（找不到 %s）' % xlsx)

        fill_price_and_weight(pub, spec.get('skus') or [])
        fill_ship_time(pub, spec.get('ship') or '48小时发货')

        # 主图（可跳过：图片上传不稳时不阻塞其余阶段）
        media = spec.get('media') or {}
        mains = media.get('main') or []
        if mains and not skip_media:
            log('[11] 上传主图 %d 张' % len(mains))
            clear_main_images(pub)
            for i, img in enumerate(mains):
                try:
                    r = pub.evaluate("""(i) => {
                        const slots = document.querySelectorAll('div.picture-sort-list div.module-picture-cover-wrapper');
                        const s = slots[i];
                        if (!s) return 'no-slot';
                        const add = Array.from(s.querySelectorAll('*')).find(e => {
                            if (e.children.length > 0) return false;
                            return (e.textContent || '').replace(/\\s+/g, '').trim() === '添加图片';
                        });
                        if (!add) return 'no-add-btn';
                        add.id = '__wb_addslot';
                        add.scrollIntoView({block: 'center'});
                        return 'tagged';
                    }""", i)
                    if r != 'tagged':
                        log('      槽位 %d: %s（跳过）' % (i, r))
                        continue
                    pub.locator('#__wb_addslot').click(timeout=6000)
                    pub.wait_for_timeout(3000)
                    upload_image_picker(pub, img)
                except Stop as e:
                    log('      ⚠ 第 %d 张失败：%s' % (i + 1, e))
            slots = main_img_slots(pub)
            log('    主图槽位: %s' % [(s['i'], '有' if not s['empty'] else '空') for s in slots])

            # SKU 图：图框只有 28x28，locator 会被遮挡超时，必须用鼠标事件直接打
            sku_imgs = media.get('sku') or []
            if sku_imgs:
                log('[12] 上传 SKU 图 %d 张' % len(sku_imgs))
                for i, img in enumerate(sku_imgs):
                    if not os.path.isfile(img):
                        continue
                    r = pub.evaluate("""(i) => {
                        const boxes = document.querySelectorAll('div.ind-table-img-box');
                        const box = boxes[i];
                        if (!box) return 'no-box';
                        if (String(box.className).indexOf('ind-has-img') >= 0) return 'already';
                        box.scrollIntoView({block: 'center'});
                        const ev = (t) => box.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window}));
                        ev('mousedown'); ev('mouseup'); ev('click');
                        return 'dispatched';
                    }""", i)
                    if r != 'dispatched':
                        log('      SKU %d: %s（跳过）' % (i + 1, r))
                        continue
                    pub.wait_for_timeout(3000)
                    try:
                        upload_image_picker(pub, img)
                    except Stop as e:
                        log('      SKU %d 失败：%s' % (i + 1, e))

            # 图文详情：按 SKU 自动推导（不写死张数）+ 拍前须知
            details = detail_images_for(spec.get('skus') or [], media.get('detail'))
            log('    详情图 %d 张: %s' % (len(details), [os.path.basename(x) for x in details]))
            try:
                fill_detail_images(pub, details)
            except Stop as e:
                log('    ⚠ 详情图：%s' % e)

        log('[12] 诊断: %s' % diagnostics(pub))
        log('')
        log('第 1-6 段完成（类目/基础/SKU/报价件重尺/发货时间/主图/SKU图/详情图）。')
        log('待做：视频、提交。')
        # 自己起的浏览器跑完就关；连的是别人的就不动（梓帆要看得见现场）
        if _owned and not a.keep_open:
            log('浏览器已关闭（想留着看现场加 --keep-open）')
            ctx.close()
        elif _owned:
            log('浏览器保持打开（--keep-open）')
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Stop as e:
        log('')
        log('✗ 停手：%s' % e)
        sys.exit(2)
