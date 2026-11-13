# -*- coding: utf-8 -*-
"""商品视频合成：封面+主图+详情图+拍前须知 → 轮播+淡入淡出切片
顺序：封面_标注 → 主图2-5 → 详情图 → 拍前须知
每张停留 dur 秒（按张数反推，保证总长 >= TOTAL_MIN），xfade 交叉淡化 FADE 秒，
输出 3:4 mp4（淘宝 5s-5min；1688 要 >=8s）。标准素材是封面+详情图+拍前须知共 3 张 → 9.0 秒
用法：python make_video.py 料号 [--out 输出.mp4]
"""
import sys
import os
import glob
import re
import subprocess

W, H = 1080, 1440   # 3:4（详情图比例）
DUR_MIN = 3.0       # 每张最少停留秒（宁可长一点）
TOTAL_MIN = 9.0     # 视频总时长下限：1688 诊断线是 8s（淘宝 5s-5min），留 1 秒余量
                    # 实际每张时长按张数反推：dur = max(DUR_MIN, (TOTAL_MIN + (n-1)*FADE) / n)
FADE = 0.6          # 淡入淡出秒
BASE = os.path.dirname(os.path.abspath(__file__))

# --- workbench 路径兼容层：换机器只改 paths.local.json / WB_* 环境变量 ---
def _wb_paths():
    """找 workbench 的 paths.py。本脚本可能在推广一键跑，也可能在插件 scripts 下。"""
    _here = os.path.dirname(os.path.abspath(__file__))
    _cands = []
    _ev = os.environ.get("WB_ROOT")
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
        _cands.append(os.path.join(_base, "OH-WorkSpace", "工具", "workbench"))
        _cands.append(os.path.join(_base, "工具", "workbench"))
    for _c in _cands:
        try:
            if _c and os.path.isfile(os.path.join(_c, "paths.py")):
                if _c not in sys.path:
                    sys.path.insert(0, _c)
                import paths as _p
                return _p
        except Exception:
            continue
    return None


_P = _wb_paths()
_WS = os.path.dirname(os.path.dirname(os.path.dirname(_P.get("mat_root")))) if _P else r"C:\Users\jdt-pty\Desktop\OH-WorkSpace"
PRODUCT_BASE = _P.get("mat_root") if _P else os.path.join(_WS, "办公室工作", "素材", "产品素材")
XUZHI = os.path.join(_WS, "办公室工作", "素材", "模板", "paigian_xuzhi_1600.png")


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def collect_images(lh):
    """视频素材：封面批注 → 封面(jpg 图库图没批注——原图也可) → 主图2-5 → 白底图 → 详情图 → 拍前须知（主图全进——视频够长）
    扩展名（jpg/png 都收——图库补的图是 jpg，只配 .png 会收集不到）
    """
    folder = os.path.join(PRODUCT_BASE, lh)
    order = []
    for pat in ["*_封面_标注.*", "*_封面.*", "*_主图2.*", "*_主图3.*", "*_主图4.*", "*_主图5.*",
                "*_白底.*", "*_详情图.*"]:
        m = sorted(glob.glob(os.path.join(folder, pat)))
        if m:
            order.append(m[0])
    if os.path.isfile(XUZHI):
        order.append(XUZHI)
    return [f for f in order if os.path.isfile(f)]


def check_bars(path):
    """抽帧验四边黑边（像素采样——cropdetect 之外的第二道，防暗灰边漏检）
    返回 (上,下,左,右) 像素；检测失败返回 None
    淘宝审核会判「画面四周出现黑白边」驳回——传前必过这道。
    """
    try:
        import tempfile
        from PIL import Image
        ff = ffmpeg()
        png = os.path.join(tempfile.gettempdir(), "_mv_bar_check.png")
        subprocess.run([ff, "-y", "-ss", "1.5", "-i", path, "-frames:v", "1", "-update", "1", png],
                       capture_output=True, timeout=60)
        im = Image.open(png).convert("RGB")
        W, H = im.size
        px = im.load()

        def dark_row(y):
            step = max(1, W // 60)
            n = sum(1 for x in range(0, W, step) if sum(px[x, y]) < 60)
            return n >= (W // step) * 0.9

        def dark_col(x):
            step = max(1, H // 60)
            n = sum(1 for y in range(0, H, step) if sum(px[x, y]) < 60)
            return n >= (H // step) * 0.9

        t = b = l = r = 0
        while t < H and dark_row(t):
            t += 1
        while b < H and dark_row(H - 1 - b):
            b += 1
        while l < W and dark_col(l):
            l += 1
        while r < W and dark_col(W - 1 - r):
            r += 1
        return (t, b, l, r)
    except Exception as e:
        print("[视频] 黑边像素检测异常:", str(e)[:50])
        return None


def auto_crop(path):
    """生成后自动裁黑边（ffmpeg cropdetect 检测——有黑边裁掉——无跳过）
    替代一键过审的黑白边裁切——自己裁更准
    """
    try:
        ff = ffmpeg()
        r = subprocess.run([ff, "-i", path, "-vf", "cropdetect=limit=24:round=2", "-f", "null", "-"],
                           capture_output=True, timeout=90)
        out = r.stderr.decode("utf-8", errors="replace")
        crops = [l for l in out.splitlines() if "crop=" in l and "Parsed_cropdetect" in l]
        if not crops:
            return False
        crop = crops[-1].split("crop=")[-1].strip().split()[0]
        w, h, x, y = crop.split(":")
        res_m = [l for l in out.splitlines() if "Video:" in l]
        res = ""
        if res_m:
            mm = re.search(r"(\d{3,5})x(\d{3,5})", res_m[0])
            if mm:
                res = mm.group(0)
        if res:
            rw, rh = res.split("x")
            if int(w) < int(rw) or int(h) < int(rh):
                tmp = path + ".tmp.mp4"
                r2 = subprocess.run([ff, "-y", "-i", path, "-vf", "crop=%s" % crop,
                                     "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-c:a", "copy", tmp],
                                    capture_output=True, timeout=300)
                if r2.returncode == 0 and os.path.isfile(tmp):
                    os.replace(tmp, path)
                    print("[视频] 自动裁黑边: %s（crop=%s）" % (os.path.basename(path), crop))
                    # 裁完复验（cropdetect 会漏暗灰边——像素级再过一道）
                    bars = check_bars(path)
                    if bars and sum(bars) > 0:
                        print("[视频] ★裁完仍有黑边 %s——%s 别上传，先查素材" % (bars, os.path.basename(path)))
                        return False
                    return True
    except Exception as e:
        print("[视频] 裁黑边异常:", str(e)[:50])
        return False
    # cropdetect 判定无黑边（或没出结果）——像素级再过一道（漏检就静悄悄放行了）
    bars = check_bars(path)
    if bars and sum(bars) > 0:
        print("[视频] ★仍有黑边 %s——%s 别上传，先查素材" % (bars, os.path.basename(path)))
        return False
    return False


def main():
    lh = sys.argv[1] if len(sys.argv) > 1 else "1-967628-1"
    out = None
    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            out = sys.argv[i + 1]
    if not out:
        out = os.path.join(PRODUCT_BASE, lh, "%s_视频.mp4" % lh)
    imgs = collect_images(lh)
    if len(imgs) < 2:
        print("素材不足（需≥2张）:", imgs)
        return 1
    print("[视频] %d 张图：%s" % (len(imgs), [os.path.basename(i) for i in imgs]))
    ff = ffmpeg()
    # 每张图转统一尺寸 + 时长（loop）+ 淡出尾部；中间用 xfade 交叉淡化
    # 命令：N 个 -loop 1 -t T 输入 → scale/pad → 前 N-1 个尾部 fade out → xfade 链
    n = len(imgs)
    # 按张数反推每张时长，保证总长 >= TOTAL_MIN（写死 DUR 盖不住张数变化：
    # 3314 只有 3 张图，DUR=3.0 时总长 7.8 秒，仍不到 1688 的 8 秒线）
    dur = max(DUR_MIN, (TOTAL_MIN + (n - 1) * FADE) / n)
    t_total = n * dur - (n - 1) * FADE  # 交叉淡化重叠
    cmd = [ff, "-y"]
    for im in imgs:
        cmd += ["-loop", "1", "-t", "%.2f" % dur, "-i", im]
    # 滤镜：每个输入 scale+pad；第 1..n-1 个尾部 fade out；xfade 链
    vf = []
    for i in range(n):
        # 满幅无边框——scale=increase 填满 + crop 居中（不留黑白边；产品图居中裁边缘安全）
        vf.append("[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,"
                  "crop=%d:%d,setsar=1,"
                  "format=yuv420p[v%d]" % (i, W, H, W, H, i))
    prev = "v0"
    for i in range(1, n):
        off = i * dur - i * FADE  # 前 i 个片段总时长减去重叠
        vf.append("[%s][v%d]xfade=transition=fade:duration=%.2f:offset=%.2f[vx%d]" % (prev, i, FADE, off, i))
        prev = "vx%d" % i
    cmd += ["-filter_complex", ";".join(vf), "-map", "[%s]" % prev,
            "-t", "%.2f" % t_total, "-r", "25", "-c:v", "libx264", "-b:v", "1500k",
            "-minrate", "1000k", "-maxrate", "2500k", "-bufsize", "3000k", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-an", out]
    print("[视频] 运行 ffmpeg（%d 张 × %.2fs，总长 %.1fs）..." % (n, dur, t_total))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if os.path.isfile(out) and os.path.getsize(out) > 10000:
        print("[视频] -> %s（%.1f MB）" % (out, os.path.getsize(out) / 1e6))
        auto_crop(out)  # 生成后自动裁黑边（替代一键过审的裁切）
        return 0
    print("[视频] 失败:", r.stderr[-600:])
    return 1


if __name__ == "__main__":
    sys.exit(main())
