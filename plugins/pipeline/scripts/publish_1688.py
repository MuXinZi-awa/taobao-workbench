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
    """搜类目 → 点第一个可点的同名叶子 → 校验已选路径 → 点「确认类目继续完善」→ 返回新发布页"""
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
    page.wait_for_timeout(3000)

    clicked = page.evaluate("""(kw) => {
        const cands = [];
        document.querySelectorAll('*').forEach(e => {
            if (e.children.length > 0) return;
            const t = (e.textContent || '').replace(/\\s+/g, '').trim();
            if (t !== kw) return;
            const r = e.getBoundingClientRect();
            if (r.width < 20 || r.height < 8) return;
            cands.push({y: r.y, el: e});
        });
        if (!cands.length) return 'no-candidate';
        cands.sort((a, b) => a.y - b.y);
        cands[0].el.click();
        return 'clicked';
    }""", kw)
    if clicked != 'clicked':
        raise Stop('类目「%s」没有可点结果: %s' % (kw, clicked))
    page.wait_for_timeout(2000)

    def _path():
        return page.evaluate("""() => {
            const bc = Array.from(document.querySelectorAll('*'))
                .filter(e => e.children.length === 0)
                .map(e => (e.textContent || '').replace(/\\s+/g, '').trim())
                .filter(t => t && t.length < 30 && t.indexOf('>') >= 0);
            return bc.join(' | ');
        }""")

    path = _path()
    log('[2] 已选路径: %s' % (path or '(未读到)'))

    # 不匹配就换下一个候选：页面状态残留时，第一个同名项往往不是目标类目
    if expect_path and expect_path not in (path or ''):
        log('    首个候选路径不符，逐个换候选')
        ok = False
        n = page.evaluate("""(kw) => {
            const cands = [];
            document.querySelectorAll('*').forEach(e => {
                if (e.children.length > 0) return;
                const t = (e.textContent || '').replace(/\\s+/g, '').trim();
                if (t !== kw) return;
                const r = e.getBoundingClientRect();
                if (r.width < 20 || r.height < 8) return;
                cands.push(r.y);
            });
            return cands.length;
        }""", kw)
        for i in range(1, min(n, 6)):
            page.evaluate("""([kw, idx]) => {
                const cands = [];
                document.querySelectorAll('*').forEach(e => {
                    if (e.children.length > 0) return;
                    const t = (e.textContent || '').replace(/\\s+/g, '').trim();
                    if (t !== kw) return;
                    const r = e.getBoundingClientRect();
                    if (r.width < 20 || r.height < 8) return;
                    cands.push({y: r.y, el: e});
                });
                cands.sort((a, b) => a.y - b.y);
                if (cands[idx]) cands[idx].el.click();
            }""", [kw, i])
            page.wait_for_timeout(1500)
            path = _path()
            log('    候选 %d → %s' % (i, (path or '')[:70]))
            if expect_path and expect_path in (path or ''):
                ok = True
                break
        if not ok:
            raise Stop('类目路径不符：期望含「%s」，试了 %d 个候选都不对，最后 %r'
                       % (expect_path, min(n, 6), (path or '')[:80]))
    log('    ✓ 路径匹配')

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

    两个关键教训：
    1. **必须逐行，不能用「批量设置」**——梓帆明确说过：多个 SKU 批量设价格会乱
       （两个型号本来价不同，批量会把它们设成同一个值）。列头那个「批量设置」是给
       “所有 SKU 同价”场景用的，这里不用。
    2. **next-table 把表头和数据拆成两个 <table>**：
         .next-table-header-inner table  → 只有 thead
         .next-table-body table         → 才是 tbody 数据行
       直接遍历 document.querySelectorAll('table') 找含「批发价」的表，拿到的是表头那半，
       tbody tr 永远是 0——看起来像「报价表没生成」，实际行一直在那里。
    """
    if not skus:
        return {}
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
    a = ap.parse_args()

    spec = json.load(io.open(a.spec, encoding='utf-8'))
    log('=== 1688 上品 ===')
    log('  标题: %s' % spec.get('title', '')[:60])
    log('  SKU : %s 个' % len(spec.get('skus') or []))
    log('  提交: %s' % ('是' if a.submit else '否（只填不提交）'))

    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0]
        sel = open_select(ctx)
        pub = pick_category(sel, spec.get('cat_kw', ''), spec.get('cat_path', ''))

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

        log('[10] 诊断: %s' % diagnostics(pub))
        log('')
        log('第 1-4 段完成（类目 / 基础信息 / SKU 导入 / 报价件重尺）。')
        log('待做：发货时间、主图、SKU 图、图文详情、视频、提交。')
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Stop as e:
        log('')
        log('✗ 停手：%s' % e)
        sys.exit(2)
