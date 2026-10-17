# -*- coding: utf-8 -*-
"""分流子进程：料号 → 搜商品 + 查双百 → 输出 JSON（给 pipeline 插件当子进程调）
为什么要子进程：playwright 在内核的请求线程里跑不稳（拿不到 _m_h5_tk）
为什么要锁：和推广/采集/送修共用同一把浏览器锁（同一 profile 同时只能一个 Chrome）
用法：python _classify_one.py 料号1,料号2,...   → stdout 最后一行是 JSON
"""
import sys, os, io, json, time, random, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    # line_buffering：被重定向到文件/管道时也能实时看到每一行——否则卡住时
    # 输出全攒在缓冲区里，事后查不到它停在哪一步。
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                  line_buffering=True)
except Exception:
    pass

from _mtop_api import MtopClient, PROFILE_DIR


def _is_ascii_alnum(c):
    """只把 ASCII 字母数字当“粘在一起”——中文的 str.isalnum() 也是 True，
    但“器W3S”里前面那个字不是粘住的标识符字符，不能因此判不中。"""
    return bool(c) and c.isascii() and c.isalnum()


def _exact_in(lh, title):
    """料号是否作为**独立标识**出现在标题里。
    反直觉：不能去掉横线做子串——`W3S` 会命中 `W3S-1939-P012`，
    也会命中 `AW3S`（图库里这确实是五个不同产品，分开存的）。
    规则：后面不能紧跟「-/_ + 字母数字」（那是更长的料号），
    前面不能紧贴 ASCII 字母数字（那是另一个料号的一部分）。
    """
    t = title or ""
    if not lh or not t:
        return False
    pat = re.escape(lh) + r"(?![\-_][A-Za-z0-9])"
    for m in re.finditer(pat, t, re.I):
        s = m.start()
        if s > 0 and _is_ascii_alnum(t[s - 1]):
            continue
        return True
    return False


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False))


_PPID = os.getppid()


def _pid_alive(pid):
    """Windows 上父进程死掉后 os.getppid 不会变——只能拿系统调用问一句。"""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
        if h:
            k.CloseHandle(h)
            return True
    except Exception:
        return True
    return False


def _tk_state(profile):
    """只看 profile 里的 _m_h5_tk 在不在、过没过期——cookie 值加密，但名字和过期时间是明文。
    返回 (可用?, 原因)；读不了就放行，不挡正常流程。"""
    import sqlite3, shutil, tempfile, datetime
    ck = os.path.join(profile, "Default", "Network", "Cookies")
    if not os.path.isfile(ck):
        return False, "profile 里没有 Cookies 文件"
    try:
        tmp = os.path.join(tempfile.mkdtemp(), "Cookies")
        shutil.copy2(ck, tmp)
        con = sqlite3.connect(tmp)
        row = con.execute("select expires_utc from cookies where name='_m_h5_tk'").fetchone()
        con.close()
    except Exception:
        return True, ""
    if not row:
        return False, "profile 里没有 _m_h5_tk"
    if row[0]:
        dt = datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=row[0])
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        if dt < now:
            return False, "_m_h5_tk 已过期"
    return True, ""


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else ""
    lhs = [x.strip() for x in raw.split(",") if x.strip()]
    if not lhs:
        _out({"ok": False, "error": "无料号"})
        return

    # 先看登录态再决定要不要开浏览器：headless 下的自动登录过不了风控，
    # 会白等好几分钟才报错——用户看到的就是“点了不动”。没令牌就当场说清。
    _ok, _why = _tk_state(PROFILE_DIR)
    if not _ok:
        _out({"ok": False, "error": "%s——没有登录态：请在工作台 设置→浏览器 "
                                    "点「引导登录」，登录完点「检测登录态」再跑" % _why})
        return

    # 浏览器锁——和推广/采集/送修共用同一把（否则和推广批撞 profile）
    _lk = None
    try:
        import browser_lock
        _lk = browser_lock.guard("流水线分流", timeout=1200)
        _lk.__enter__()
        print("[锁] 已取得浏览器锁")
    except Exception as e:
        _out({"ok": False, "error": "等浏览器锁失败: %s" % str(e)[:100]})
        return

    mc = None
    items = []
    try:
        mc = MtopClient()
        try:
            mc.open(headless=True)
        except Exception as e:
            _msg = str(e)[:200]
            # playwright 原文是英文：起不来 99% 是 profile 被别的 Chrome 占着
            # （Chrome 对同一个 user-data-dir 只认一个实例，后来的会被顶掉然后退出）
            if "has been closed" in _msg or "ProcessSingleton" in _msg or "SingletonLock" in _msg:
                _msg = ("浏览器起不来——chrome_profile 正被别的 Chrome 占着（多半是上一个任务留下的"
                        "孤儿浏览器）。先把占用的 Chrome 关掉，或等工作台里别的任务跑完，再试。")
            _out({"ok": False, "error": "Mtop 打开失败: %s" % _msg})
            return
        # 没有 _m_h5_tk = 没登录态：后面每个品都会失败，闷着跑完整批只会让人
        # 以为“卡死了”——“点了半天不动”十有八九就是这个，不如当场喊人。
        if not mc.token:
            _out({"ok": False, "error": "没有登录态（拿不到 _m_h5_tk）——请在工作台 设置→浏览器 "
                                          "点「引导登录」，登录完点「检测登录态」再跑"})
            return

        def _probe(_lh):
            """查一个品 → 结果 dict；异常往上抛（由调用处决定要不要刷新重试）"""
            it = {"lh": _lh, "id": "", "title": "", "type": "", "dual": None, "note": ""}
            rows, _st = mc.search_and_dual(_lh)
            if rows:
                hit = None
                for r in rows:
                    if _exact_in(_lh, str(r.get("title", ""))):
                        hit = r
                        break
                if hit is None:
                    # 搜到结果但没有一条标题写的是这个料号：要么短料号撞上长料号
                    # （W3S vs W3S-1939-P012），要么标题压根不含料号。**不猜**——
                    # 猜错就会把 A 的素材/属性改到 B 的商品上（1016 实锤 W3S）。
                    it["type"] = "查询存疑"
                    it["note"] = "搜到 %d 条但标题无精确命中" % len(rows)
                    return it
                it["id"] = str(hit.get("itemId", ""))
                it["title"] = str(hit.get("title", ""))[:40]
                sl = str(hit.get("scoreLabel") or "")
                if sl:
                    it["dual"] = (sl == "流量加速中")
                    it["type"] = "老品-双百跳过" if it["dual"] else "老品-需优化"
                    it["note"] = sl[:20]
                else:
                    it["type"] = "老品-需优化"
                    it["note"] = "无双百信息"
            else:
                it["type"] = "新品-待上架"
                it["note"] = "店铺搜不到该料号"
            return it

        _bad = 0
        for _i, lh in enumerate(lhs):
            # 父（工作台）已经没了就别当幽灵：占着浏览器锁和 profile 会堵住后面所有人。
            if not _pid_alive(_PPID):
                print("[退出] 工作台已不在，提前收工")
                break
            try:
                it = _probe(lh)
            except Exception as e:
                # 被拦（fetch 超时/页面异常）→ 重载页面再试一次：验证态和软拦常常刷一下
                # 就没了，顺便重拿 _m_h5_tk。刷完还不行，才算这个品真失败。
                print("[刷新] %s 失败（%s），重载页面重试" % (lh, str(e)[:60]))
                it = {"lh": lh, "id": "", "title": "", "type": "查询异常", "dual": None,
                      "note": str(e)[:30]}
                try:
                    if mc.revive():
                        it = _probe(lh)
                except Exception as e2:
                    it = {"lh": lh, "id": "", "title": "", "type": "查询异常", "dual": None,
                          "note": "刷新后仍失败:" + str(e2)[:20]}
            _bad = _bad + 1 if it["type"] == "查询异常" else 0
            items.append(it)
            print("[%s] %s | id=%s | %s" % (lh, it["type"], it["id"], it["note"]))
            # 连着一串异常 = 多半被淘宝拦了（验证码/限流“晾着不回”），再查也是白查：
            # 停下来喊人，比默默跑完一整批“查询异常”有用。
            if _bad >= 3:
                _out({"ok": False, "error": "连续 3 个品查询异常——多半被淘宝拦了（验证码/限流）。"
                                            "人工打开 chrome_profile 看一眼，或过一会儿再跑。"})
                return
            # 品间随机延迟：正常 0.5s/品 跑得太快容易招拦，随机 1-4s 更像人工节奏
            if _i < len(lhs) - 1:
                time.sleep(random.uniform(1, 4))
            # 每 10 品歇一口气：淘宝按“一段时间内的请求量”拦——实测连查 18 品就被晾着
            # 不回，光靠品间延迟只撑到 18，得停下来把计数喘下去。
            if (_i + 1) % 10 == 0 and _i < len(lhs) - 1:
                print("[歇] 已查 %d 品，停 15 秒" % (_i + 1))
                time.sleep(15)
    finally:
        try:
            if mc is not None:
                mc.close()
        except Exception:
            pass
        if _lk is not None:
            try:
                _lk.__exit__(None, None, None)
            except Exception:
                pass

    _out({"ok": True, "items": items})


main()
