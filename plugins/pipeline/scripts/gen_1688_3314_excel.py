# -*- coding: utf-8 -*-
"""用 1688 电位器类目模板，生成 3314G / 3314J 两行 SKU 的 Excel。
只填规格书里能确认的值，不确定的留空——绝不编造。
"""
import os, glob, shutil, datetime
import openpyxl

# --- workbench 路径兼容层：换机器只改 paths.local.json / WB_* 环境变量 ---
def _wb_ws_root():
    """返回工作区根（OH-WorkSpace）。先找 workbench 的 paths.py，再退回自身上溯。"""
    import sys as _s
    _here = os.path.dirname(os.path.abspath(__file__))
    for _base in (_here, os.path.dirname(_here), os.path.dirname(os.path.dirname(_here))):
        _c = os.path.join(_base, "paths.py")
        if os.path.isfile(_c):
            if _base not in _s.path:
                _s.path.insert(0, _base)
            import paths as _p
            _mr = _p.get("mat_root")
            # mat_root = <WS>\\办公室工作\\素材\\产品素材 → 上溯三级才到 <WS>
            return os.path.dirname(os.path.dirname(os.path.dirname(_mr)))
    _x = _here
    for _ in range(6):
        if os.path.isdir(os.path.join(_x, "办公室工作")):
            return _x
        _nd = os.path.dirname(_x)
        if _nd == _x:
            break
        _x = _nd
    return r"C:\Users\jdt-pty\Desktop\OH-WorkSpace"

_WS  = _wb_ws_root()
_MAT = os.path.join(_WS, "办公室工作", "素材", "产品素材")
_ENG = os.path.join(_WS, "_工程_新品素材")
_TG  = os.environ.get("WB_TG_ROOT") or r"C:\Users\jdt-pty\Desktop\推广一键跑"
# --- 兼容层结束 ---



BASE = _WS
PROBE = os.path.join(BASE, '_工程_新品素材', 'probe')
OUTDIR = os.path.join(BASE, '_工程_新品素材', 'ali_excel')
os.makedirs(OUTDIR, exist_ok=True)

tpl = sorted(glob.glob(os.path.join(PROBE, '**', '*电位器*模板*.xlsx'), recursive=True),
             key=os.path.getmtime)[-1]
dst = os.path.join(OUTDIR, '3314_两个SKU.xlsx')
shutil.copy2(tpl, dst)

wb = openpyxl.load_workbook(dst)
ws = wb[wb.sheetnames[0]]

# 行3 是表头，从行4 开始填
headers = {}
for c in range(1, ws.max_column + 1):
    v = ws.cell(3, c).value
    if v:
        headers[str(v).strip()] = c
print('[生成] 表头列:', len(headers))

# 3314 系列共用参数（来自 pot-3314.pdf）
COMMON = {
    '电阻值': '10kΩ',
    '容差': '±20%',
    '额定功率': '0.25W',
    '额定电压': '300V',
    '旋转角度': '210°',
    '温度系数': '±100ppm/°C',
    '绝缘电阻': '≥100MΩ',
    '安装类型': 'SMD',
    '种类': '微调电位器',
}

# 两个 SKU 的差异：端接样式（欧翼 / J 钩）
SKUS = [
    ('3314G-1-103LF', {'端接样式': '鸥翼型'}),
    ('3314J-1-103LF', {'端接样式': 'J 钩型'}),
]

row = 4
for lh, diff in SKUS:
    ws.cell(row, headers['规格型号']).value = lh
    for k, v in COMMON.items():
        if k in headers:
            ws.cell(row, headers[k]).value = v
        else:
            print('    [跳过] 模板无该列:', k)
    for k, v in diff.items():
        if k in headers:
            ws.cell(row, headers[k]).value = v
    filled = [k for k in list(COMMON) + ['规格型号'] + list(diff) if k in headers]
    print('   行%d  %-18s 填了 %d 个字段' % (row, lh, len(filled)))
    row += 1

wb.save(dst)
print('\n[生成] 文件:', dst, '(%.1f KB)' % (os.path.getsize(dst) / 1024))

# 回读
wb2 = openpyxl.load_workbook(dst)
ws2 = wb2[wb2.sheetnames[0]]
print('\n=== 回读 ===')
for r in range(4, 6):
    vals = []
    for c in range(1, ws2.max_column + 1):
        v = ws2.cell(r, c).value
        if v is not None:
            h = ws2.cell(3, c).value
            vals.append('%s=%s' % (h, v))
    print('  行%d: %s' % (r, ' | '.join(vals)))
