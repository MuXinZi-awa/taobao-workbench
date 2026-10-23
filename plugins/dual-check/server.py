# -*- coding: utf-8 -*-
"""查双百插件 · 后端 v2（handle(action, params)）
单查：/api/plg/dual-check/check?lh=料号（API 料号搜——ID 可选）
返回：dual / result / summary（怎么办）/ login（登录态）
"""
import os
import subprocess

import sys as _sys
_d = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_d, "paths.py")) and os.path.dirname(_d) != _d:
    _d = os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import paths

BASE = os.path.dirname(os.path.abspath(__file__))
TG = paths.TG_ROOT
RT = paths.TG_PYTHON


import datetime as _dt


def _log_fp():
    """当前活动店铺的运行日志路径：`dual_check_<memberID>.log`（多店铺隔离）。

    **必须每次现算，不能在模块级算一次存起来**：插件模块每个请求都重新 exec，
    而面板切连接是在运行中发生的——存成常量的话切完还往老账号的文件里写
    （pipeline 插件 1017 三个店混一个文件是这么来的，这里原来同病）。
    memberID 从连接表拿（conn_store.active_of("taobao")）；取不到（没配连接/库读不到）
    就退回 `dual_check.log`——不报错、不丢日志。
    """
    mid = ""
    try:
        mid = str((_acct() or {}).get("member_id") or "").strip()
    except Exception:
        mid = ""
    mid = "".join(c for c in mid if c.isalnum() or c in "-_")
    if not mid:
        return os.path.join(paths.TG_RUNTIME, "dual_check.log")
    return os.path.join(paths.TG_RUNTIME, "dual_check_%s.log" % mid)


def _log_name():
    """日志文件名（面板/内核读日志用——与 _log_fp() 同源，不许各自拼）"""
    return os.path.basename(_log_fp())


_rot_day = {}


def _maybe_rotate():
    """按天归档：日志文件日期≠今天 → 改名加日期保留（按账号各自归档、各自清理）"""
    import datetime as _dt2, os as _os2
    try:
        fp = _log_fp()
    except Exception:
        return
    try:
        today = _dt2.date.today()
        if _rot_day.get(fp) == today:
            return
        if _os2.path.isfile(fp):
            m = _dt2.date.fromtimestamp(_os2.path.getmtime(fp))
            if m != today:
                _base = fp[:-4] if fp.endswith(".log") else fp
                _os2.rename(fp, "%s_%s.log" % (_base, m.strftime("%Y%m%d")))
        _rot_day[fp] = today
    except Exception:
        pass
    # 清理：超期归档删除（保留天数 log_keep_days.json 默认 30）
    try:
        import glob as _g3, time as _t3
        _kd = 30
        try:
            _kd = int(open(paths.LOG_KEEP_DAYS, encoding="utf-8").read().strip())
        except Exception:
            pass
        _base = fp[:-4] if fp.endswith(".log") else fp
        for _f in _g3.glob(_base + "_*.log"):
            if _t3.time() - os.path.getmtime(_f) > _kd * 86400:
                try:
                    os.remove(_f)
                except Exception:
                    pass
    except Exception:
        pass


def _wlog(msg):
    _maybe_rotate()
    try:
        with open(_log_fp(), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (_dt.datetime.now().strftime("%m-%d %H:%M:%S"), msg))
    except Exception as _e:
        pass


def _acct():
    """当前激活店铺连接（多账号：面板显示 + 数据过滤）"""
    try:
        import sys as _s
        _wb = paths.ROOT
        if _wb not in _s.path:
            _s.path.insert(0, _wb)
        import conn_store
        return conn_store.active_of("taobao") or {}
    except Exception:
        return {}


def _batch_impl(lhs):
    """批量查双百——在独立线程里跑（见 batch action 注释）。"""
    import sys
    sys.path.insert(0, TG)
    from _mtop_api import MtopClient
    results = []
    mc = MtopClient().open()
    try:
        for lh in lhs:
            try:
                out, _ = mc.check_dual(lh)
                if out:
                    f = out[0]
                    dual = f.get("scoreLabel") == "流量加速中"
                    results.append({"lh": lh, "dual": dual,
                                    "result": f.get("scoreLabel", "") or ("已双百" if dual else "未双百")})
                else:
                    results.append({"lh": lh, "dual": False, "result": "未搜到"})
            except Exception as e:
                results.append({"lh": lh, "dual": False, "result": "异常:%s" % str(e)[:40]})
        try:
            _wlog("查双百 共%d品: %s" % (len(lhs), " | ".join("%s=%s" % (r["lh"], r["result"][:20]) for r in results)))
        except Exception:
            pass
    finally:
        try:
            mc.close()
        except Exception:
            pass
    return {"ok": True, "results": results}


def handle(action, params):
    if action == "acct":
        _a = _acct()
        return {"ok": True, "account": {"name": _a.get("name") or "", "member_id": _a.get("member_id") or ""}}
    if action == "logfile":
        # 当前活动账号的日志文件名（面板/内核读日志用）——动态算，切连接后立刻跟着变
        try:
            return {"ok": True, "name": _log_name(), "path": _log_fp(),
                    "member_id": str((_acct() or {}).get("member_id") or "")}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "check":
        lh = (params.get("lh") or "").strip()
        tid = (params.get("tid") or "").strip()
        if not lh:
            return {"ok": False, "error": "需要料号（API 按料号搜；淘宝ID 可留空）"}
        try:
            r = subprocess.run([RT, "-X", "utf8", "-u", os.path.join(TG, "_dual_one.py"), lh, tid],
                               capture_output=True, timeout=90)
            out = (r.stdout or b"").decode("utf-8", "replace")
            tail = (out.strip().splitlines()[-1] if out.strip() else "").strip()
            login = not ("未登录" in out or "登录过期" in out or "请手动登录" in out)
            dual = "已双百" in tail or ("流量加速中" in out and "未" not in tail)
            try:
                _wlog("单查 %s => %s (%s)" % (lh, "双百" if dual else "未双百", tail[:40]))
            except Exception:
                pass
            if dual:
                return {"ok": True, "lh": lh, "dual": True,
                        "summary": "已双百（基础分100 + 扶优分100 满）——无需处理，可放心推广投放。",
                        "result": tail[:100], "login": login}
            reason = "缺必填属性（提示'提扶优分'）" if "提扶优分" in out else "未达标"
            hints = []
            if "提扶优分" in out:
                hints.append("缺必填属性——发布页核对并补全（接口类型/品牌/型号/类别/认证标准/适用场景等）")
            hints.append("检查素材是否齐全：主图（封面/白底）、视频已上传——无视频不双百，主图缺失也会拦")
            hints.append("属性数据可从 TE 官网 / renhotecrf 爬；素材可走图库 / AI 送修 / TE官网图")
            return {"ok": True, "lh": lh, "dual": False,
                    "summary": "未双百：" + reason + "。可能原因与处理：\n1. " + hints[0] + "\n2. " + hints[1] + "\n3. " + hints[2],
                    "result": tail[:100], "login": login}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "查询超时（90s）——可能登录态失效"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}
    if action == "login-open":
        # 引导登录：打开 chrome_profile 淘宝千牛页（人手动登录一次——cookie 存 profile）
        try:
            chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            prof = os.path.join(TG, "chrome_profile")
            url = "https://login.taobao.com/member/login.jhtml"
            subprocess.Popen([chrome, "--user-data-dir=" + prof, url])
            return {"ok": True, "msg": "已打开登录页（chrome_profile）——请登录淘宝/千牛，完成后回面板点「检测登录态」"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "login-check":
        # 检测登录态：用已知料号跑一次查（探测 cookie 是否有效）
        try:
            r = subprocess.run([RT, "-X", "utf8", "-u", os.path.join(TG, "_dual_one.py"), "1379668", ""],
                               capture_output=True, timeout=90)
            out = (r.stdout or b"").decode("utf-8", "replace")
            ok = "已双百" in out or "未双百" in out or "流量加速中" in out
            return {"ok": True, "login": ok, "msg": "登录态有效" if ok else "登录态失效——点「引导登录」重新登录"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "batch":
        # 单 client 循环查多料号（省每料号起浏览器——_mtop_api 一次 open）
        # 1022：必须跑在独立线程。工作台是 async 框架，插件在事件循环线程里直接
        # 调同步 Playwright 会报 `name 'base_events' is not defined`（playwright 内部
        # 也要起 asyncio 事件循环，与宿主冲突），整批 74 品会全报“批量失败”。
        lhs = [x.strip() for x in (params.get("lhs") or "").split(",") if x.strip()]
        if not lhs:
            return {"ok": False, "error": "需要 lhs（逗号分隔料号）"}
        try:
            import threading
            _box = {"r": None, "e": None}

            def _runner():
                try:
                    _box["r"] = _batch_impl(lhs)
                except Exception as _e:
                    _box["e"] = _e

            _t = threading.Thread(target=_runner, daemon=True)
            _t.start()
            _t.join(timeout=1800)
            if _t.is_alive():
                return {"ok": False, "error": "批量超时（1800s）——可能登录态或搜索被拦"}
            if _box["e"] is not None:
                return {"ok": False, "error": "批量失败: %s" % str(_box["e"])[:100]}
            return _box["r"]
        except Exception as e:
            return {"ok": False, "error": "批量失败: %s" % str(e)[:100]}
    return {"ok": False, "error": "未知 action: %s" % action}
