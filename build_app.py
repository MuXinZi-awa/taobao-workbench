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
# 正式入口（构建的默认目标）：构建时拷过去的那份副本就在它里面
ENTRY = os.path.join(os.path.dirname(HERE), "工作台（双击启动）")
OUT = ENTRY                                                     # 默认重建正式入口
if "--dist" in sys.argv:
    OUT = sys.argv[sys.argv.index("--dist") + 1]
NO_PLUGINS = "--no-plugins" in sys.argv
RUNTIME_LINK = "--runtime-link" in sys.argv
sys.path.insert(0, HERE)          # 运行时源路径直接从 paths 取（别自己拼目录）
import paths                       # noqa: E402
TG_RUNTIME_SRC = paths.TG_RUNTIME
TMP = os.path.join(os.path.dirname(HERE), "_build_wb")       # 构建中间产物（不属于交付物）
NAME = "OHWorkbench"

# ── 副本同步闸 ────────────────────────────────
# 为什么要有：源码改了、构建时拷到入口目录的那份副本没跟 → 用户看到旧行为。
# 今天栽过两次：preflight.py（改源码没生效）、index.html（源码修了 exe 那份还是坏的）。
import hashlib                       # noqa: E402


def _digest_file(p):
    """内容摘要（比时间戳可靠：时间戳会因为“碰一下”而撒谎）"""
    try:
        h = hashlib.sha1()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return "%s/%dB" % (h.hexdigest()[:10], os.path.getsize(p))
    except Exception:
        return "缺失"


def _digest_dir(d):
    items = []
    for r, ds, fs in os.walk(d):
        ds[:] = [x for x in ds if x not in SKIP_DIRS]
        for f in fs:
            if f in SKIP_FILES or os.path.splitext(f)[1].lower() in SKIP_EXT:
                continue
            p = os.path.join(r, f)
            try:
                items.append((os.path.relpath(p, d), os.path.getsize(p)))
            except Exception:
                pass
    return "%d个/%dB/%s" % (len(items), sum(s for _, s in items),
                            hashlib.sha1(repr(sorted(items)).encode()).hexdigest()[:8])


def check_sync(target, verbose=True):
    """比「源码」与「目标目录里的副本」。返回不同步的清单（空 = 全一致）。"""
    bad = []
    if not os.path.isdir(target):
        return ["（目标目录不存在：%s）" % target]
    for f in SYNC_FILES:
        da = _digest_file(os.path.join(HERE, f))
        db = _digest_file(os.path.join(target, f))
        ok = (da == db)
        if verbose:
            print("  %-22s %s  源码=%s  副本=%s" % (f, "一致 ✓" if ok else "不一致 ✗", da, db))
        if not ok:
            bad.append(f)
    for d in SYNC_DIRS:
        da = _digest_dir(os.path.join(HERE, d))
        db = _digest_dir(os.path.join(target, d))
        ok = (da == db)
        if verbose:
            print("  %-22s %s  %s / %s" % (d + "/", "一致 ✓" if ok else "不一致 ✗", da, db))
        if not ok:
            bad.append(d + "/")
    return bad


def sync_copies(target):
    """把源码拷到目标目录对齐（轻量，不用重打 exe）"""
    done = []
    for f in SYNC_FILES:
        src = os.path.join(HERE, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(target, f))
            done.append(f)
    for d in SYNC_DIRS:
        src = os.path.join(HERE, d)
        if os.path.isdir(src):
            copy_tree(src, os.path.join(target, d))
            done.append(d + "/")
    return done
SKIP_DIRS = {"__pycache__", ".profile", "runtime", "cache"}
SKIP_FILES = {"state.json", "paths.local.json", "conn.db", ".disabled"}
SKIP_EXT = {".pyc", ".log"}
SRC_FILES = ["app.py", "preflight.py", "workbench.py", "paths.py", "conn_store.py",
             "index.html", "CHANGELOG.md", "registry.json"]
# 壳用 runpy 在**运行时**加载 workbench.py / conn_store.py，PyInstaller 的静态分析看不见它们
# 里面的导入 —— 这些模块不显式带上，exe 就会「界面能开、一点就报 No module named」。
HIDDEN_IMPORTS = ["sqlite3", "xmlrpc.client", "fitz"]
SRC_DIRS = ["vendor", "plugins"]
SYNC_FILES = list(SRC_FILES)     # 同步闸要查的清单（构建时拷过去的那些）
SYNC_DIRS = list(SRC_DIRS)


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
    # 构建前先看一眼现状：有不同步就说明上一次改完源码没重建
    if os.path.isdir(OUT):
        _pre = check_sync(OUT, verbose=False)
        _pre = [x for x in _pre if not x.startswith("（")]
        if _pre:
            print("构建前发现 %d 项副本与源码不一致（重建后会拷齐）：%s" % (len(_pre), ", ".join(_pre)))
        else:
            print("构建前：副本与源码一致 ✓")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
           "--name", NAME, "--distpath", os.path.join(TMP, "dist"),
           "--workpath", os.path.join(TMP, "build"), "--specpath", TMP]
    for h in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", h]
    cmd += ["app.py"]
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
        # 包里 runtime/ 是目录链接时，必须只拆链接——直接 rmtree 会顺着链接把他真实的 runtime 删了
        rt0 = os.path.join(OUT, "runtime")
        try:
            is_j = getattr(os.path, "isjunction", lambda p: False)(rt0) or os.path.islink(rt0)
            if os.path.isdir(rt0) and is_j:
                subprocess.run(["cmd", "/c", "rmdir", rt0], capture_output=True)
                print("  拆掉旧的 runtime 链接（不碰源目录）")
        except Exception:
            pass
        shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(built, OUT)

    for f in SRC_FILES:
        src = os.path.join(HERE, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(OUT, f))
    for d in SRC_DIRS:
        if NO_PLUGINS and d == "plugins":
            continue
        src = os.path.join(HERE, d)
        if os.path.isdir(src):
            copy_tree(src, os.path.join(OUT, d))
    if NO_PLUGINS:
        shutil.rmtree(os.path.join(OUT, "plugins"), ignore_errors=True)
        os.makedirs(os.path.join(OUT, "plugins"), exist_ok=True)   # 空目录：装插件时往里放
        print("\n（--no-plugins：包里不带插件，插件由【插件列表】下载安装）")
    if RUNTIME_LINK:
        # 运行时放独立目录（跟 plugins 一样在程序旁边）：加依赖只更新它，不用重打 exe。
        # 本机测试直接链接到现有那份；正式分发放精简运行时。
        rt = os.path.join(OUT, "runtime")
        if os.path.isdir(TG_RUNTIME_SRC) and not os.path.exists(rt):
            r = subprocess.run(["cmd", "/c", "mklink", "/J", rt, TG_RUNTIME_SRC],
                               capture_output=True)
            print("  runtime/ → %s（%s）" % (TG_RUNTIME_SRC, "OK" if r.returncode == 0 else "失败"))
        elif os.path.exists(rt):
            print("  runtime/ 已存在，跳过")

    print("\n产物：%s" % OUT)
    for name in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, name)
        print("  %-20s %8.1f MB" % (name, size_of(p) / 1024 / 1024))
    print("  合计 %.1f MB" % (size_of(OUT) / 1024 / 1024))

    # 打包后导入自检：让 exe 自己把会用到的模块逐个 import 一遍，缺什么当场列出来。
    # 为什么不让用户撞：exe 里缺件表现为「界面能开、一点就报错」，梓帆那边没法调试。
    check_log = os.path.join(OUT, "runtime", "import_check.log")
    try:
        os.remove(check_log)
    except Exception:
        pass
    print("\n打包后导入自检…")
    r2 = subprocess.run([os.path.join(OUT, NAME + ".exe"), "--check-imports"],
                        cwd=OUT, capture_output=True, timeout=240)
    try:
        for line in open(check_log, encoding="utf-8").read().splitlines():
            print("  " + line)
    except Exception:
        print("  （没读到 %s）" % check_log)
    if r2.returncode != 0:
        print("！！自检发现缺失模块 —— 把缺的加进 build_app.py 的 HIDDEN_IMPORTS 再打一次")
        return 2

    # 构建后**副本同步**闸：源码改了但副本没跟 → 用户看到旧行为（今天栽过两次）
    print("\n源码 vs 副本 比对：")
    bad = check_sync(OUT, verbose=False)
    bad = [x for x in bad if not x.startswith("（")]
    if bad:
        print("  ！！不同步 %d 项：%s" % (len(bad), ", ".join(bad)))
        print("  （重建应自动拷齐；若仍不同步，说明这批文件不在 SRC_FILES / SRC_DIRS 里）")
        return 3
    print("  全部一致 ✓（%d 个文件 + %d 个目录）" % (len(SYNC_FILES), len(SYNC_DIRS)))
    return 0


if __name__ == "__main__":
    if "--check-sync" in sys.argv:
        # 随时能跑：列出「源码 vs 入口目录里的副本」的差异（改了源码没重建，当场可见）
        print("比对：\n  源码  %s\n  副本  %s" % (HERE, OUT))
        bad = check_sync(OUT, verbose=True)
        print("\n结论：%s" % ("全部一致 ✓" if not bad else "不同步 %d 项 → %s" % (len(bad), ", ".join(bad))))
        if bad:
            print("对齐办法：python build_app.py --sync（只拷源码，不重打 exe）或直接重建")
        sys.exit(1 if bad else 0)
    if "--sync" in sys.argv:
        print("把源码同步到：%s" % OUT)
        for x in sync_copies(OUT):
            print("  拷 " + x)
        bad = check_sync(OUT, verbose=False)
        print("同步后：%s" % ("全部一致 ✓" if not bad else "还有 %s" % bad))
        sys.exit(1 if bad else 0)
    sys.exit(main())
