# -*- coding: utf-8 -*-
"""本地图库索引：F:\\连接图图库（13万图，按 品牌/料号_序号.jpg）
find(lh) → 命中图路径列表（自有实拍——最高优先级素材源）
索引缓存 runtime/lib_index.json（重建：build()；过期：库 mtime 变/索引超 7 天）
"""
import os, re, io, json, time

LIB_ROOT = r"F:\连接图图库"
IDX_FP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib_index.json")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

def norm(s):
    return re.sub(r"[\-_/\s]+", "", (s or "")).lower()

def _lh_of(fname):
    """文件名 → 料号：去扩展名、去尾部 _01/-1 序号"""
    b = os.path.splitext(fname)[0]
    b = re.sub(r"[_\-\s]*\d{1,3}$", "", b)  # 去 _01 / -1 / 空格1
    return b

def build(verbose=False):
    idx = {}
    n = 0
    t0 = time.time()
    for root, dirs, files in os.walk(LIB_ROOT):
        for f in files:
            if not f.lower().endswith(IMG_EXT):
                continue
            lh = _lh_of(f)
            k = norm(lh)
            if not k:
                continue
            idx.setdefault(k, []).append(os.path.join(root, f))
            n += 1
    try:
        json.dump({"ts": time.time(), "count": n, "idx": idx}, io.open(IDX_FP, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    if verbose:
        print("图库索引: %d 图 / %d 料号，%.1fs" % (n, len(idx), time.time() - t0))
    return idx

def _load():
    try:
        d = json.load(io.open(IDX_FP, encoding="utf-8"))
        if time.time() - d.get("ts", 0) < 7 * 86400:
            return d["idx"]
    except Exception:
        pass
    return build()

def find(lh):
    """料号 → 图库路径列表（排序：_01 在前）"""
    if not lh:
        return []
    idx = _load()
    return sorted(idx.get(norm(lh), []))

def count(lh):
    return len(find(lh))

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    if "--build" in sys.argv:
        build(verbose=True)
    else:
        for lh in sys.argv[1:] or ["1-480275-0"]:
            fs = find(lh)
            print("%s → %d 张" % (lh, len(fs)))
            for f in fs[:5]:
                print("   ", f)
