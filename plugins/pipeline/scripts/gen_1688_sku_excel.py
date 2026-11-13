# -*- coding: utf-8 -*-
"""按类目模板生成 1688 SKU 批量导入 Excel（通用版）。

要点（梓帆的教训）：
  「下次在 excel 里面填齐了就不会有这种问题了」
  —— 页面上补必填最容易出「值在界面上但校验器没收到」，所以能写进 Excel 的字段
     一个都不要留到页面上去补。尤其：外形、封装/外壳 这两个必填下拉，
     Excel 里必须用**确切文本**（含中文全角逗号，如 `2-SMD，无引线`）。

用法：
  python gen_1688_sku_excel.py --spec 参数.json --out 输出.xlsx [--tpl 模板.xlsx]
"""
import argparse
import glob
import io
import json
import os
import shutil
import sys

import openpyxl

def _ws_root():
    """工作区根。mat_root = <WS>\\办公室工作\\素材\\产品素材，上溯三级。"""
    import sys as _s
    _here = os.path.dirname(os.path.abspath(__file__))
    for _b in (_here, os.path.dirname(_here),
               os.path.dirname(os.path.dirname(_here)),
               os.path.dirname(os.path.dirname(os.path.dirname(_here)))):
        if os.path.isfile(os.path.join(_b, "paths.py")):
            if _b not in _s.path:
                _s.path.insert(0, _b)
            import paths as _p
            _mr = _p.get("mat_root")
            return os.path.dirname(os.path.dirname(os.path.dirname(_mr)))
    _x = _here
    for _ in range(8):
        if os.path.isdir(os.path.join(_x, "办公室工作")):
            return _x
        _n = os.path.dirname(_x)
        if _n == _x:
            break
        _x = _n
    return r"C:\\Users\\jdt-pty\\Desktop\\OH-WorkSpace"


WS = _ws_root()
PROBE = os.path.join(WS, '_工程_新品素材', 'probe')
OUTDIR = os.path.join(WS, '_工程_新品素材', 'ali_excel')

# spec 里 skus[].<key> → 模板列名
# 只要模板里有这一列就填，没有就跳过并报出来（不静默）
FIELD_MAP = [
    ('型号', '规格型号'),
    ('电阻值', '电阻值'),
    ('额定电压', '额定电压'),
    ('外形', '外形'),
    ('封装/外壳', '封装/外壳'),
    ('容差', '容差'),
    ('端接样式', '端接样式'),
    ('安装类型', '安装类型'),
    ('额定功率', '额定功率'),
    ('旋转角度', '旋转角度'),
    ('温度系数', '温度系数'),
    ('种类', '种类'),
    ('绝缘电阻', '绝缘电阻'),
]


def find_tpl(keyword):
    cands = []
    for pat in ('*%s*模板*.xlsx' % keyword, '*%s*.xlsx' % keyword):
        cands += glob.glob(os.path.join(PROBE, '**', pat), recursive=True)
    if not cands:
        return None
    return sorted(set(cands), key=os.path.getmtime)[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--spec', required=True)
    ap.add_argument('--out', default='')
    ap.add_argument('--tpl', default='')
    ap.add_argument('--cat-kw', default='')
    a = ap.parse_args()

    spec = json.load(io.open(a.spec, encoding='utf-8'))
    kw = a.cat_kw or spec.get('cat_kw') or ''
    tpl = a.tpl or find_tpl(kw)
    if not tpl or not os.path.isfile(tpl):
        raise SystemExit('找不到类目模板（关键词 %r），用 --tpl 指定' % kw)
    print('[模板]', tpl)

    os.makedirs(OUTDIR, exist_ok=True)
    dst = a.out or os.path.join(OUTDIR, '%s_SKU.xlsx' % (spec.get('code') or 'out'))
    shutil.copy2(tpl, dst)

    wb = openpyxl.load_workbook(dst)
    ws = wb[wb.sheetnames[0]]

    # 找表头行：含「规格型号」的那一行
    head_row = None
    headers = {}
    for r in range(1, 8):
        tmp = {}
        for c in range(1, ws.max_column + 1):
            v = ws.cell(r, c).value
            if v:
                tmp[str(v).strip()] = c
        if '规格型号' in tmp:
            head_row = r
            headers = tmp
            break
    if head_row is None:
        raise SystemExit('模板里找不到「规格型号」表头行')
    print('[模板] 表头行=%d，共 %d 列' % (head_row, len(headers)))

    skus = spec.get('skus') or []
    if not skus:
        raise SystemExit('spec 里没有 skus')

    row = head_row + 1
    missing_cols = set()
    for sku in skus:
        filled = []
        for key, col in FIELD_MAP:
            v = sku.get(key)
            if v is None or v == '':
                continue
            if col not in headers:
                missing_cols.add(col)
                continue
            ws.cell(row, headers[col]).value = v
            filled.append(col)
        print('  行%-2d %-20s 填 %d 字段: %s' % (row, sku.get('型号', '?'), len(filled), '、'.join(filled)))
        row += 1

    if missing_cols:
        print('  ⚠ 模板里没有这些列（未填）: %s' % '、'.join(sorted(missing_cols)))

    # 必填自检：这 5 个必须有值，否则页面上还得补
    MUST = ['规格型号', '电阻值', '额定电压', '外形', '封装/外壳', '容差']
    bad = []
    for i, sku in enumerate(skus):
        r = head_row + 1 + i
        for col in MUST:
            if col not in headers:
                bad.append('%s 无列「%s」' % (sku.get('型号'), col))
                continue
            v = ws.cell(r, headers[col]).value
            if v is None or str(v).strip() == '':
                bad.append('%s 缺「%s」' % (sku.get('型号'), col))
    if bad:
        print()
        print('  ✗ 必填没齐（页面上还得补，正是要避免的）:')
        for b in bad:
            print('     ', b)
        raise SystemExit(3)

    wb.save(dst)
    print()
    print('[生成]', dst, '(%.1f KB)' % (os.path.getsize(dst) / 1024))

    # 回读验证
    wb2 = openpyxl.load_workbook(dst)
    ws2 = wb2[wb2.sheetnames[0]]
    print('=== 回读 ===')
    for r in range(head_row + 1, head_row + 1 + len(skus)):
        vals = []
        for c in range(1, ws2.max_column + 1):
            v = ws2.cell(r, c).value
            if v is not None:
                vals.append('%s=%s' % (ws2.cell(head_row, c).value, v))
        print('  行%d: %s' % (r, ' | '.join(vals)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
