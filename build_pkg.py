# -*- coding: utf-8 -*-
"""插件打包：把 plugins/<id> 打成可安装的 zip（只含代码，排除运行时产物）。

为什么单列：市场/发布要的是"干净包"——不能把 13 万图索引(lib_index.json)、日志、
状态文件、缓存一起打进去（动辄十几 MB，且是每台机器自己生成的，装过去反而错）。

用法：python build_pkg.py <插件id> [<插件id> ...]  /  python build_pkg.py --all
输出：_pkgs/<id>.zip（源码版与 exe 版各自 build 各自的包）
约束：zip 根目录直接放 manifest.json（install_plugin_zip 认「根/单层」）。
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.join(HERE, "plugins")
OUT = os.path.join(HERE, "_pkgs")

# 运行时产物：不进包（每台机器自己生成）。
EXCL_DIRS = {"__pycache__", "runtime", ".profile", "screens", "cache", "logs"}
EXCL_FILES = {"state.json", "lib_index.json", "repair_state.json", "status.json", ".disabled"}
EXCL_EXT = (".log", ".pyc", ".tmp", ".part")


def build(pid):
    src = os.path.join(PLUGINS, pid)
    mf = os.path.join(src, "manifest.json")
    if not os.path.isfile(mf):
        return None, "没有 manifest.json：%s" % src
    os.makedirs(OUT, exist_ok=True)
    dest = os.path.join(OUT, pid + ".zip")
    n = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in EXCL_DIRS]
            for f in files:
                if f in EXCL_FILES or f.lower().endswith(EXCL_EXT):
                    continue
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, src).replace(os.sep, "/")
                z.write(fp, rel)
                n += 1
    return dest, "%d 个文件，%.1f KB" % (n, os.path.getsize(dest) / 1024.0)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--all" in sys.argv:
        args = [d for d in sorted(os.listdir(PLUGINS))
                if os.path.isfile(os.path.join(PLUGINS, d, "manifest.json"))]
    if not args:
        print("用法：python build_pkg.py <插件id> [...] 或 --all")
        return
    for pid in args:
        dest, info = build(pid)
        print(("  ✓ %s → %s（%s）" % (pid, dest, info)) if dest else ("  ✗ %s：%s" % (pid, info)))


if __name__ == "__main__":
    main()
