# -*- coding: utf-8 -*-
"""送修批 v2：并行送修（xyq 支持多任务——并发 submit 各自 thread 收结果，不再串行等）
并发度 3（防限流）；进度写 runtime/repair_state.json（工作台轮询显示）
用法：python repair_batch.py [料号,逗号]；REPAIR_USAGE=clean|cover 环境变量
"""
import os, sys, io, json, glob, time, datetime, shutil, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
SD = os.path.dirname(os.path.abspath(__file__))  # 脚本自身目录（插件内——自包含）
TG = SD
SUCAI = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\素材\产品素材"
STATE = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\工具\workbench\plugins\pipeline\state.json"
LOG_FP = os.path.join(SD, "repair.log")
TMP = os.path.join(SD, "_tmp")
ST_FP = os.path.join(SD, "repair_state.json")
USAGE = os.environ.get("REPAIR_USAGE", "clean")
CONC = 3

sys.path.insert(0, SD)
from ai_schedule import generate, KEY

_lock = threading.Lock()
STATE_R = {"total": 0, "done": 0, "fail": 0, "cur": "", "msg": ""}

def wlog(m):
    try:
        with open(LOG_FP, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), m))
    except Exception:
        pass
    print(m, flush=True)

def save_st():
    try:
        with open(ST_FP, "w", encoding="utf-8") as f:
            json.dump(STATE_R, f, ensure_ascii=False)
    except Exception:
        pass

def ref_img(lh):
    """送修参考 = 审核预览同一张（封面/主图1 原图——审核啥送修啥，勿自己换）"""
    d = os.path.join(SUCAI, lh)
    if not os.path.isdir(d):
        return ""
    names = sorted(os.listdir(d))
    for pat in ("%s_主图1", "%s_封面", "%s_主图"):
        for n in names:
            base = n.split(".")[0]
            if pat % lh == base and all(k not in n for k in ("标注", "白底", "待复核", "待复检")) and n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                return os.path.join(d, n)
    for n in names:
        if all(k not in n for k in ("标注", "白底", "待复核", "待复检")) and n.lower().endswith((".png", ".jpg", ".jpeg")):
            return os.path.join(d, n)
    return ""

def mark_state(lh, audit, note=""):
    try:
        st = json.load(io.open(STATE, encoding="utf-8"))
        st["items"].setdefault(lh, {})["audit"] = audit
        if note:
            st["items"][lh]["audit_note"] = note
        io.open(STATE, "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False, indent=1))
    except Exception:
        pass

def repair_one(lh):
    try:
        with _lock:
            STATE_R["cur"] = lh
            save_st()
        ref = ref_img(lh)
        if not ref:
            wlog("✗ %s 无参考图——跳过" % lh)
            mark_state(lh, "送修", "无参考图待补")
            with _lock:
                STATE_R["fail"] += 1
                save_st()
            return
        wlog("→ %s 送修(%s) 参考 %s ..." % (lh, USAGE, os.path.basename(ref)))
        os.makedirs(TMP, exist_ok=True)
        saved = generate(lh, ref, USAGE, TMP)
        if saved:
            d = os.path.join(SUCAI, lh)
            os.makedirs(d, exist_ok=True)
            ts = datetime.datetime.now().strftime("%H%M%S")
            moved = []
            for sf in saved:
                if os.path.isfile(sf) and os.path.getsize(sf) > 5000:
                    dest = os.path.join(d, "%s_白底_待复检%s.png" % (lh, ts))
                    shutil.move(sf, dest)
                    moved.append(os.path.basename(dest))
            if moved:
                mark_state(lh, "待复检", "送修完成：" + ",".join(moved))
                wlog("✓ %s 完成 → %s（待复检）" % (lh, moved[0]))
                with _lock:
                    STATE_R["done"] += 1
                    save_st()
            else:
                wlog("✗ %s 产物无效" % lh)
                with _lock:
                    STATE_R["fail"] += 1
                    save_st()
        else:
            wlog("✗ %s 送修失败(AI未出图)" % lh)
            with _lock:
                STATE_R["fail"] += 1
                save_st()
    except Exception as e:
        wlog("✗ %s 异常: %s" % (lh, str(e)[:80]))
        with _lock:
            STATE_R["fail"] += 1
            save_st()
    finally:
        with _lock:
            STATE_R["cur"] = ""

def main():
    if len(sys.argv) > 1:
        lhs = [x.strip() for x in sys.argv[1].split(",") if x.strip()]
    else:
        try:
            st = json.load(io.open(STATE, encoding="utf-8"))
            lhs = [lh for lh, v in st.get("items", {}).items() if v.get("audit") == "送修"]
        except Exception:
            lhs = []
    STATE_R.update({"total": len(lhs), "done": 0, "fail": 0, "cur": "", "msg": "启动 %d 品" % len(lhs)})
    save_st()
    wlog("送修批 v2（%s）：%d 品 并发%d %s" % (USAGE, len(lhs), CONC, lhs))
    if not KEY:
        wlog("❌ 缺 XYQ KEY")
        STATE_R["msg"] = "缺 KEY"
        save_st()
        return
    with ThreadPoolExecutor(max_workers=CONC) as ex:
        list(ex.map(repair_one, lhs))
    STATE_R["msg"] = "完成 done=%d fail=%d" % (STATE_R["done"], STATE_R["fail"])
    save_st()
    try:
        shutil.rmtree(TMP, ignore_errors=True)
    except Exception:
        pass
    wlog("送修批结束 done=%d fail=%d" % (STATE_R["done"], STATE_R["fail"]))

if __name__ == "__main__":
    main()
