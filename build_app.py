# -*- coding: utf-8 -*-
"""打包桌面版（可双击运行）。

产物布局（app 文件夹与源码 `工具\workbench` 同层，因此「上两级=工作区」的路径推导仍然成立）：
    <工具>\workbench-app\
        OHWorkbench.exe      ← 只装「壳」（原生窗口）
        _internal\           ← PyInstaller 运行时
        workbench.py / paths.py / conn_store.py / index.html / CHANGELOG.md / vendor\ / plugins\
                             ← 全在 exe 旁边，内核按源码方式起：插件热插拔、改面板即生效

不进包：conn.db（凭据库）/ chrome profile / paths.local.json / runtime / cache / 日志。
用法：python build_app.py
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "workbench-app")   # 与源码同层（推导才继续成立）
TMP = os.path.join(os.path.dirname(HERE), "_build_wb")       # 构建中间产物（不属于交付物）
NAME = "OHWorkbench"
SKIP_DIRS = {"__pycache__", ".profile", "runtime", "cache"}
SKIP_FILES = {"state.json", "paths.local.json", "conn.db"}
SKIP_EXT = {".pyc", ".log"}
SRC_FILES = ["app.py", "preflight.py", "workbench.py", "paths.py", "conn_store.py",
             "index.html", "CHANGELOG.md"]
SRC_DIRS = ["vendor", "plugins"]


def size_of(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    n = 0
    for r, _d, fs in os.walk(path):
        for f in fs:
            try:
                n += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return n


def copy_tree(src, dst):
    """按排除表拷贝（凭据/缓存/profile 一律不带进包）"""
    for r, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(r, src)
        tgt = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(tgt, exist_ok=True)
        for f in files:
            if f in SKIP_FILES or os.path.splitext(f)[1].lower() in SKIP_EXT:
                continue
            try:
                shutil.copy2(os.path.join(r, f), os.path.join(tgt, f))
            except OSError:
                pass


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    os.makedirs(TMP, exist_ok=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
           "--name", NAME, "--distpath", os.path.join(TMP, "dist"),
           "--workpath", os.path.join(TMP, "build"), "--specpath", TMP, "app.py"]
    print("打包中…", " ".join(cmd[:6]))
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        print("打包失败（退出码 %d）" % r.returncode)
        return 1

    built = os.path.join(TMP, "dist", NAME)
    if not os.path.isfile(os.path.join(built, NAME + ".exe")):
        print("没找到产物 exe：%s" % built)
        return 1

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(built, OUT)

    for f in SRC_FILES:
        src = os.path.join(HERE, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(OUT, f))
    for d in SRC_DIRS:
        src = os.path.join(HERE, d)
        if os.path.isdir(src):
            copy_tree(src, os.path.join(OUT, d))

    print("\n产物：%s" % OUT)
    for name in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, name)
        print("  %-20s %8.1f MB" % (name, size_of(p) / 1024 / 1024))
    print("  合计 %.1f MB" % (size_of(OUT) / 1024 / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
