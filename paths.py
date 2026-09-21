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

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_NAME = "paths.local.json"


def _find_program_root(start):
    """向上找程序根：插件脚本跑在子目录里，不能假设层级深度，认标志文件更稳。"""
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
    "state": os.path.join(ROOT, "plugins", "pipeline", "state.json"),
    "mat_root": os.path.join(WS, "办公室工作", "素材", "产品素材"),
    "data": os.path.join(WS, "办公室工作", "数据"),
    "product": os.path.join(WS, "产品"),
    "tg_root": os.path.join(_DESKTOP, "推广一键跑"),
    "tg_runtime": os.path.join(_DESKTOP, "推广一键跑", "runtime"),
    "chrome": os.path.join(_HOME, "AppData", "Local", "Google", "Chrome",
                           "Application", "chrome.exe"),
    "key_files": [os.path.join(WS, "xyq_key.txt"),
                  os.path.join(WS, "工具", "xyq_key.txt"),
                  os.path.join(_DESKTOP, "推广一键跑", "xyq_key.txt")],
}

_KEYS = ["root", "runtime", "cache", "state", "mat_root", "data", "product",
         "tg_root", "tg_runtime", "chrome", "key_files"]


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
        try:
            with open(_CFG_FP, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    cfg = {}
    for k in _KEYS:
        v = os.environ.get("WB_" + k.upper())
        cfg[k] = v if v else _DEFAULTS[k]
    _write_template(_CFG_FP, cfg)
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


# ---- 对外名字（业务代码只引用这些）----
ROOT = get("root")
RUNTIME = get("runtime")          # 运行目录：进度、留痕、断点、锁日志
CACHE = get("cache")              # 缓存/临时目录：临时文件一律落这里
STATE = get("state")
MAT_ROOT = get("mat_root")
DATA = get("data")
PRODUCT = get("product")
TG_ROOT = get("tg_root")
TG_RUNTIME = get("tg_runtime")
CHROME = get("chrome")
KEY_FILES = _as_list("key_files")

# 跨脚本复用的具体文件（同一定义，避免多处各写一遍字面量）
LOG_KEEP_DAYS = os.path.join(TG_RUNTIME, "log_keep_days.json")
TG_PYTHON = os.path.join(TG_RUNTIME, "python.exe")      # 推广一键跑自带的解释器

# Chrome 候选：配置值优先，其后是系统常见安装位（同一个 Chrome，只是路径不同）
CHROME_CANDIDATES = [CHROME] + [p for p in (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
) if p != CHROME]


def under(base, *parts):
    """拼路径（统一入口，避免各处自己拼字符串风格不一）"""
    return os.path.join(base, *parts)


def runtime(*parts):
    """运行目录下的文件；首次使用时建目录"""
    os.makedirs(RUNTIME, exist_ok=True)
    return os.path.join(RUNTIME, *parts)


def cache(*parts):
    """缓存/临时目录下的文件；临时文件都应该走这里"""
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, *parts)


def tg(*parts):
    """推广一键跑目录下的文件"""
    return os.path.join(TG_ROOT, *parts)


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
