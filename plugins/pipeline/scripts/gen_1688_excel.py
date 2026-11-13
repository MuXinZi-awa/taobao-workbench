# -*- coding: utf-8 -*-
"""用 1688 官方模板生成一个测试 Excel（单品行），验证字段能不能对上。
不提交任何东西——只生成文件。
"""
import os, glob, shutil
import openpyxl
from openpyxl.drawing.image import Image as XLImage

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



PROBE = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\_工程_新品素材\probe"
MAT = _MAT
OUTDIR = os.path.join(r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\_工程_新品素材\ali_excel")
os.makedirs(OUTDIR, exist_ok=True)

tpl = sorted(glob.glob(os.path.join(PROBE, "**", "商品批量导入模板.xlsx"), recursive=True))[-1]
dst = os.path.join(OUTDIR, "测试_MF-R110.xlsx")
shutil.copy2(tpl, dst)

wb = openpyxl.load_workbook(dst)
ws = wb["发品明细"]

LH = "MF-R110"
TITLE = "伯恩斯贴片自恢复保险丝 MF-R110 1.10A 30V Bourns原装"
PRICE = 0.37
STOCK = 1000
MORE = ("型号：MF-R110；品牌：BOURNS 伯恩斯；保持电流 1.10A；跳闸电流 2.20A；"
        "额定电压 30V；最大电流 40A；初始电阻 0.10~0.18Ω；耗散功率 0.70W；"
        "插件式径向引线 PTC 自恢复保险丝；工作温度 -40~85°C；原厂正品")

row = 6
ws.cell(row, 1).value = LH          # A 序号
ws.cell(row, 2).value = TITLE       # B 商品标题
ws.cell(row, 3).value = LH          # C 货号/型号
ws.cell(row, 9).value = MORE        # I 更多信息
ws.cell(row, 15).value = PRICE      # O SKU价格
ws.cell(row, 16).value = STOCK      # P SKU库存

# D 列插入封面图（模板要求"直接复制图片"，不是链接）
cover = os.path.join(MAT, LH, "%s_封面.jpg" % LH)
if os.path.isfile(cover):
    img = XLImage(cover)
    img.width, img.height = 200, 200     # 缩到 200px 便于文件体积
    ws.add_image(img, "D%d" % row)
    ws.row_dimensions[row].height = 155
    print("[生成] 已插入封面:", os.path.basename(cover))
else:
    print("[生成] ✗ 没找到封面:", cover)

wb.save(dst)
print("[生成] 文件:", dst, "(%.1f KB)" % (os.path.getsize(dst) / 1024))

# 回读校验
wb2 = openpyxl.load_workbook(dst)
ws2 = wb2["发品明细"]
print("\n=== 回读第 6 行 ===")
for c in range(1, 17):
    v = ws2.cell(6, c).value
    if v is None:
        continue
    col = openpyxl.utils.get_column_letter(c)
    s = str(v).replace("\n", " / ")
    print("   %s: %s" % (col, s[:110]))
print("\n   图片数:", len(ws2._images))
