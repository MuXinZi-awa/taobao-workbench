# -*- coding: utf-8 -*-
"""路径层 · 机器特有路径的唯一出处。

为什么单列一层：路径写死在业务代码里，换机/换目录就要全局找改；而「素材、推广一键跑的
runtime、Chrome 安装位置」这些东西本来就不在程序目录里，靠相对路径也表达不了。这里只回答
一个问题——**路径从哪来**；业务代码只引用名字，解析逻辑一行不动。

解析优先级：本地配置 > 环境变量 > 相对推导
  · 相对推导不依赖用户名：从本文件位置向上两级得「工作区」，再按布局推出各外部位置
  · 本地配置 paths.local.json 不进版本控制（可能含机器特有的外部路径）
  · 环境变量名 = WB_ + 键名大写（WB_ROOT / WB_MAT_ROOT / WB_CHROME ...）

无第三方依赖。别处（如推广一键跑的脚本）把本目录加进 sys.path 后 import paths 即可。
本文件不写死任何用户名。
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_NAME = "paths.local.json"


def _find_program_root(start):
    """程序根。两种运行方式都要能走：
      · 打包后（exe）：根 = exe 所在目录——打包后没有 workbench.py 这个文件，向上找不到
      · 源码运行：向上找带 workbench.py 的目录（插件脚本跑在子目录里，不能假设层级深度）
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "workbench.py")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return start
        d = up


ROOT = _find_program_root(HERE)
# 布局：工作台在 <工作区>/工具/workbench；外部指向（素材/数据/产品）挂在工作区下，
# 「推广一键跑」与工作区同级。布局若不同，用 paths.local.json 或环境变量覆盖。
WS = os.path.dirname(os.path.dirname(ROOT))
_DESKTOP = os.path.dirname(WS)
_HOME = os.path.expanduser("~")

_CFG_FP = os.path.join(ROOT, CFG_NAME)

_DEFAULTS = {
    "root": ROOT,
    "runtime": os.path.join(ROOT, "runtime"),
    "cache": os.path.join(ROOT, "cache"),
    "port": 8900,
    "state": os.path.join(ROOT, "plugins", "pipeline", "state.json"),
    "mat_root": os.path.join(WS, "办公室工作", "素材", "产品素材"),
    "data": os.path.join(WS, "办公室工作", "数据"),
    "product": os.path.join(WS, "产品"),
    "tg_root": os.path.join(_DESKTOP, "推广一键跑"),
    "tg_runtime": os.path.join(_DESKTOP, "推广一键跑", "runtime"),
    "python": os.path.join(_DESKTOP, "推广一键跑", "runtime", "python.exe"),
    "chrome": os.path.join(_HOME, "AppData", "Local", "Google", "Chrome",
                           "Application", "chrome.exe"),
    "key_files": [os.path.join(WS, "xyq_key.txt"),
                  os.path.join(WS, "工具", "xyq_key.txt"),
                  os.path.join(_DESKTOP, "推广一键跑", "xyq_key.txt")],
}

_KEYS = ["root", "runtime", "cache", "port", "state", "mat_root", "data", "product",
         "tg_root", "tg_runtime", "python", "chrome", "key_files"]


def _write_template(path, values):
    """首次运行落地一份可编辑的模板：把当前生效值写全，改哪条删哪条即可。"""
    body = {
        "_说明": "工作台路径本地覆盖。不进版本控制。只写需要改的键；未写的走默认推导（相对程序位置）。",
        "_环境变量": "也可用环境变量覆盖：WB_ROOT / WB_RUNTIME / WB_CACHE / WB_STATE / "
                     "WB_MAT_ROOT / WB_DATA / WB_PRODUCT / WB_TG_ROOT / WB_TG_RUNTIME / WB_CHROME",
    }
    for k in _KEYS:
        body[k] = values.get(k)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2)
    except Exception:
        pass          # 只读目录/无权限：静默走默认，不因建模板而失败


def _load_cfg():
    if os.path.isfile(_CFG_FP):
        return _read_cfg()
    # 首次运行：落一份带说明的模板供参考；但不把它当成「用户配置」——
    # 否则每个键都显示「来自本地配置」，看不出哪些是默认、哪些是人改过的。
    # 真正的 paths.local.json 只在用户改过某键后才生成，只装被改过的键。
    vals = {}
    for k in _KEYS:
        v = os.environ.get("WB_" + k.upper())
        vals[k] = v if v else _DEFAULTS[k]
    _write_template(_CFG_FP + ".example", vals)
    return {}


def _read_cfg():
    """只读配置（reload 用：不会去重建模板）"""
    if os.path.isfile(_CFG_FP):
        try:
            with open(_CFG_FP, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


_CFG = _load_cfg()


def get(key, default=None):
    """单键解析：本地配置 > 环境变量 > 默认推导"""
    v = _CFG.get(key)
    if isinstance(v, str) and v.strip():
        return v
    v = os.environ.get("WB_" + key.upper())
    if isinstance(v, str) and v.strip():
        return v
    return _DEFAULTS.get(key, default)


def _as_list(key):
    v = _CFG.get(key)
    if isinstance(v, list) and v:
        return [str(x) for x in v if str(x).strip()]
    ev = os.environ.get("WB_" + key.upper())
    if ev:
        return [x for x in ev.split(os.pathsep) if x.strip()]
    return list(_DEFAULTS.get(key) or [])


# ---- 对外名字（业务代码只引用这些；改配置后由 reload() 刷新）----
def _session_id():
    """会话标识。为什么缓存要按会话分：CACHE 是全台共用一个目录，两个会话同时跑时，
    清缓存的人分不清哪份是自己的，就把别人手上的临时文件删了。分目录后这件事结构上做不到。
    优先 WB_SESSION（同一会话的各条命令靠它归到同一个目录）；没有就用「进程号+启动时刻」
    ——隔离够（不同进程一定不同），但每条命令一份。"""
    v = (os.environ.get("WB_SESSION") or "").strip()
    if v:
        return re.sub(r"[^\w.\-]+", "_", v)[:40]
    return "p%d_%s" % (os.getpid(), time.strftime("%H%M%S"))


_SESSION_ID = _session_id()


def _refresh():
    """把配置解析结果写回本模块的对外名字"""
    g = globals()
    for k in _KEYS:
        g[k.upper()] = _as_list(k) if k == "key_files" else get(k)
    try:
        g["PORT"] = int(str(get("port")).strip())
    except Exception:
        g["PORT"] = 8900
    g["PORT_FILE"] = os.path.join(g["RUNTIME"], "port.json")   # 实际端口写这里，壳靠它找已跑的实例
    # 本会话自己的缓存目录：与其它会话严格分开（别人的目录只读不删）
    g["SESSION"] = _SESSION_ID
    g["CACHE_SESSION"] = os.path.join(g["CACHE"], _SESSION_ID)
    g["LOG_KEEP_DAYS"] = os.path.join(g["TG_RUNTIME"], "log_keep_days.json")
    g["TG_PYTHON"] = os.path.join(g["TG_RUNTIME"], "python.exe")   # 推广一键跑自带的解释器
    # Chrome 候选：配置值优先，其后是系统常见安装位（同一个 Chrome，只是路径不同）
    g["CHROME_CANDIDATES"] = [g["CHROME"]] + [p for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ) if p != g["CHROME"]]


_refresh()

# 面板/设置用元信息：键 → (人话名称, 一句说明, 有几种候选可填)
KEYS = list(_KEYS)
NOTES = {
    "root": ("工作台根目录", "程序所在目录，通常不用改", "目录"),
    "runtime": ("运行目录", "进度、留痕、断点、锁日志都存这里", "目录"),
    "cache": ("缓存·临时目录", "临时文件一律落这里（不再散在脚本旁边）", "目录"),
    "port": ("内核端口", "默认 8900；被占时内核会自动往后找（面板地址栏里看到的才是实际端口）", "端口号"),
    "state": ("流水线状态文件", "选品/送修/换源的进度状态", "文件"),
    "mat_root": ("素材根目录", "产品素材（封面 / 主图 / 详情图）", "目录"),
    "data": ("数据目录", "推广记录、全量汇总、定价表等数据文件", "目录"),
    "product": ("产品目录", "按料号落盘的工作目录", "目录"),
    "tg_root": ("推广一键跑目录", "外部脚本本体（上品/推广/采集）", "目录"),
    "tg_runtime": ("推广一键跑·运行目录", "它的进度、日志都在这里", "目录"),
    "python": ("插件用的 Python", "面板里点下去跑的批处理脚本用这个解释器；打包后壳自己不能当解释器（壳里没有 playwright / openpyxl）", "文件"),
    "chrome": ("Chrome 程序", "自动登录与采集用的浏览器", "文件"),
    "key_files": ("密钥查找顺序", "多个候选路径，按先后顺序找 xyq_key.txt", "列表，用 ; 分隔"),
}


def default_of(key):
    """该项的默认值（相对程序位置/主目录推导出来的那个）"""
    return list(_DEFAULTS.get(key)) if isinstance(_DEFAULTS.get(key), list) else _DEFAULTS.get(key)


def _write_cfg(d):
    body = dict(d)
    body.setdefault("_说明", "工作台路径本地覆盖。不进版本控制。只写需要改的键；未写的走默认推导（相对程序位置）。")
    body.setdefault("_环境变量", "也可用环境变量覆盖：WB_ROOT / WB_RUNTIME / WB_CACHE / WB_STATE / "
                                "WB_MAT_ROOT / WB_DATA / WB_PRODUCT / WB_TG_ROOT / WB_TG_RUNTIME / WB_CHROME")
    with open(_CFG_FP, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)


def set_value(key, value):
    """写一项到 paths.local.json 并即时刷新（面板保存走这里）"""
    if key not in _KEYS:
        raise KeyError("未知键: %s" % key)
    d = _read_cfg()
    if key == "key_files":
        items = [x.strip() for x in str(value).split(";") if x.strip()]
        if not items:
            d.pop(key, None)
        else:
            d[key] = items
    else:
        v = str(value).strip()
        if v:
            d[key] = v
        else:
            d.pop(key, None)          # 清空 = 回默认（不把空串写进配置）
    _write_cfg(d)
    reload()
    return globals().get(key.upper())


def reset(key=None):
    """恢复默认：从本地配置里删掉该键（删掉才回到推导，写死默认值又变成硬编码）"""
    d = _read_cfg()
    if key:
        d.pop(key, None)
    else:
        for k in _KEYS:
            d.pop(k, None)
    _write_cfg(d)
    reload()
    return globals().get(key.upper()) if key else None


def reload():
    """重读配置并刷新本模块（面板改完路径后调用，让同一进程内立即生效）"""
    global _CFG
    _CFG = _read_cfg()
    _refresh()


def info(key):
    """面板展示用：当前生效值 / 默认值 / 来源"""
    label, note, kind = NOTES.get(key, (key, "", ""))
    v = globals().get(key.upper())
    if isinstance(v, list):
        shown = ";".join(v)
    else:
        shown = v or ""
    if isinstance(_CFG.get(key), (str, list)) and _CFG.get(key):
        src = "本地配置"
    elif os.environ.get("WB_" + key.upper()):
        src = "环境变量"
    else:
        src = "默认"
    dv = default_of(key)
    return {"key": key, "label": label, "note": note, "kind": kind,
            "value": shown, "default": ";".join(dv) if isinstance(dv, list) else (dv or ""),
            "source": src}


def under(base, *parts):
    """拼路径（统一入口，避免各处自己拼字符串风格不一）"""
    return os.path.join(base, *parts)


def runtime(*parts):
    """运行目录下的文件；首次使用时建目录"""
    os.makedirs(RUNTIME, exist_ok=True)
    return os.path.join(RUNTIME, *parts)


def cache(*parts):
    """本会话的缓存/临时文件路径（自动建目录）。
    约定：CACHE 下按会话分目录，跨会话只读不删——要清只清自己这一份（clean_cache）。"""
    os.makedirs(CACHE_SESSION, exist_ok=True)
    return os.path.join(CACHE_SESSION, *parts)


def own_cache_dir():
    """本会话的缓存目录（给别人看的：这个目录属于我）"""
    return CACHE_SESSION


def clean_cache():
    """清本会话自己的缓存目录。别人的目录一个都不碰（哪怕对方已经不在跑了）。"""
    import shutil
    if os.path.basename(CACHE_SESSION.rstrip("\\/")) != SESSION:   # 兜底：路径被改坏时宁可不删
        raise RuntimeError("拒绝清理：目标不是本会话目录（%s）" % CACHE_SESSION)
    if os.path.isdir(CACHE_SESSION):
        shutil.rmtree(CACHE_SESSION, ignore_errors=True)
    return CACHE_SESSION


def tg(*parts):
    """推广一键跑目录下的文件"""
    return os.path.join(TG_ROOT, *parts)


def python_exe():
    """插件/脚本要用的解释器。
    指错了要**说人话**——否则现象是「点了跑批没反应」，最难查。"""
    p = str(PYTHON or "").strip()
    if p and os.path.isfile(p):
        return p
    raise RuntimeError(
        "找不到可用的 Python 解释器：%s\n"
        "去 设置 → 通用 →「插件用的 Python」改成本机真实 Python 的路径（本机可用：%s）"
        % (p or "（空）", TG_PYTHON))


def summary():
    """供自检/面板展示：当前每个键解析到哪、来源是什么"""
    out = {}
    for k in _KEYS:
        v = _CFG.get(k)
        src = "config" if (isinstance(v, str) and v.strip()) else (
            "env" if os.environ.get("WB_" + k.upper()) else "default")
        out[k] = {"path": globals().get(k.upper()) or _DEFAULTS.get(k), "source": src}
    return out


if __name__ == "__main__":
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("程序根:", ROOT)
    for k, v in summary().items():
        print("  %-12s %-8s %s" % (k, v["source"], v["path"]))
