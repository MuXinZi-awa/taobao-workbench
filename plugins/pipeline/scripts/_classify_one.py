# -*- coding: utf-8 -*-
"""分流子进程：料号 → 搜商品 + 查双百 → 输出 JSON（给 pipeline 插件当子进程调）
为什么要子进程：playwright 在内核的请求线程里跑不稳（拿不到 _m_h5_tk）
为什么要锁：和推广/采集/送修共用同一把浏览器锁（同一 profile 同时只能一个 Chrome）
用法：python _classify_one.py 料号1,料号2,...   → stdout 最后一行是 JSON
"""
import sys, os, io, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

from _mtop_api import MtopClient


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else ""
    lhs = [x.strip() for x in raw.split(",") if x.strip()]
    if not lhs:
        _out({"ok": False, "error": "无料号"})
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
            _out({"ok": False, "error": "Mtop 打开失败: %s" % str(e)[:120]})
            return

        for lh in lhs:
            it = {"lh": lh, "id": "", "title": "", "type": "", "dual": None, "note": ""}
            try:
                rows, st = mc.search_items(lh, page=1, page_size=3)
                if rows:
                    hit = None
                    for r in rows:
                        t = str(r.get("title", ""))
                        if lh.replace("-", "").lower() in t.replace("-", "").lower():
                            hit = r
                            break
                    hit = hit or rows[0]
                    it["id"] = str(hit.get("itemId", ""))
                    it["title"] = str(hit.get("title", ""))[:40]
                    try:
                        out, _ = mc.check_dual(lh)
                        if out:
                            sl = out[0].get("scoreLabel", "")
                            it["dual"] = (sl == "流量加速中")
                            it["type"] = "老品-双百跳过" if it["dual"] else "老品-需优化"
                            it["note"] = sl[:20]
                        else:
                            it["type"] = "老品-需优化"
                            it["note"] = "无双百信息"
                    except Exception as e:
                        it["type"] = "老品-需优化"
                        it["note"] = "双百异常:%s" % str(e)[:25]
                else:
                    it["type"] = "新品-待上架"
                    it["note"] = "店铺搜不到该料号"
            except Exception as e:
                it["type"] = "查询异常"
                it["note"] = str(e)[:30]
            items.append(it)
            print("[%s] %s | id=%s | %s" % (lh, it["type"], it["id"], it["note"]))
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
