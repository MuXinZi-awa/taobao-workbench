# -*- coding: utf-8 -*-
"""查双百插件 · 后端 v2（handle(action, params)）
单查：/api/plg/dual-check/check?lh=料号（API 料号搜——ID 可选）
返回：dual / result / summary（怎么办）/ login（登录态）
"""
import os
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
TG = r"C:\Users\jdt-pty\Desktop\推广一键跑"
RT = os.path.join(TG, "runtime", "python.exe")


import datetime as _dt
_LOG_FP = r"C:\Users\jdt-pty\Desktop\推广一键跑\runtime\dual_check.log"


_rot_day = None


def _maybe_rotate():
    """0909 按天归档：日志文件日期≠今天 → 改名加日期保留"""
    import datetime as _dt2, os as _os2
    global _rot_day
    try:
        today = _dt2.date.today()
        if _rot_day == today:
            return
        if _os2.path.isfile(_LOG_FP):
            m = _dt2.date.fromtimestamp(_os2.path.getmtime(_LOG_FP))
            if m != today:
                _os2.rename(_LOG_FP, _LOG_FP.replace(".log", "_%s.log" % m.strftime("%Y%m%d")))
        _rot_day = today
    except Exception:
        pass


def _wlog(msg):
    _maybe_rotate()
    try:
        with open(_LOG_FP, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (_dt.datetime.now().strftime("%m-%d %H:%M:%S"), msg))
    except Exception as _e:
        pass


def handle(action, params):
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
        lhs = [x.strip() for x in (params.get("lhs") or "").split(",") if x.strip()]
        if not lhs:
            return {"ok": False, "error": "需要 lhs（逗号分隔料号）"}
        results = []
        try:
            import sys
            sys.path.insert(0, TG)
            from _mtop_api import MtopClient
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
                # 0909：每品结果落日志（工作流日志——老 opt_server 风格）
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
        except Exception as e:
            return {"ok": False, "error": "批量失败: %s" % str(e)[:100]}
    return {"ok": False, "error": "未知 action: %s" % action}
