# -*- coding: utf-8 -*-
"""换源批 v1：换源品（缺素材目录/图很糟）→ 自动找干净源 → 落 _白底_待复检 → 复检
找图顺序：TE 官网(泰科官方高清无水印——h1 料号防货不对板) → 立创 LCSC(白底干净)
不覆盖原图（原糟糕图留作复检对比 orig）；产物 _白底_待复检{ts}.png 标待复检
用法：python switch_src.py [料号,逗号]；无参=state 标"换源"品
"""
import sys, os, io, json, glob, time, shutil, datetime, threading
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
SD = os.path.dirname(os.path.abspath(__file__))
TG = SD
SUCAI = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\素材\产品素材"
STATE = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\工具\workbench\plugins\pipeline\state.json"
LOG_FP = os.path.join(SD, "repair.log")   # 复用送修日志
ST_FP = os.path.join(SD, "repair_state.json")  # 复用进度条
TMPROOT = os.path.join(SD, "_tmp_src")
CONC = 2

sys.path.insert(0, SD)
_lock = threading.Lock()
STATE_R = {"total": 0, "done": 0, "fail": 0, "cur": "", "msg": "", "mode": "换源"}

def wlog(m):
    try:
        with open(LOG_FP, "a", encoding="utf-8") as f:
            f.write("[换源][%s] %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), m))
    except Exception:
        pass
    print(m, flush=True)

def save_st():
    try:
        with open(ST_FP, "w", encoding="utf-8") as f:
            json.dump(STATE_R, f, ensure_ascii=False)
    except Exception:
        pass

def mark_state(lh, audit, note=""):
    try:
        st = json.load(io.open(STATE, encoding="utf-8"))
        st["items"].setdefault(lh, {})["audit"] = audit
        if note:
            st["items"][lh]["audit_note"] = note
        io.open(STATE, "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False, indent=1))
    except Exception:
        pass

def dl(url, dest):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=40) as r, open(dest, "wb") as f:
        f.write(r.read())
    return os.path.getsize(dest) > 5000

def src_one(lh):
    try:
        with _lock:
            STATE_R["cur"] = lh
            save_st()
        d = os.path.join(SUCAI, lh)
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(TMPROOT, lh.replace("/", "_"))
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        got = ""
        # 0) 本地图库优先（自有实拍——F:\连接图图库，索引 lib_images）
        try:
            sys.path.insert(0, SD)
            import lib_images
            lib = lib_images.find(lh)
            if lib:
                ts0 = datetime.datetime.now().strftime("%H%M%S")
                saved = []
                for i, f in enumerate(lib, 1):
                    ext = os.path.splitext(f)[1].lower() or ".jpg"
                    dest = os.path.join(d, "%s_库源%d_待复检%s%s" % (lh, i, ts0, ext))
                    shutil.copy2(f, dest)
                    saved.append(os.path.basename(dest))
                mark_state(lh, "待复检", "图库换源 %d 张：%s" % (len(saved), ",".join(saved[:3])))
                wlog("✓ %s 图库换源 %d 张 → 待复检" % (lh, len(saved)))
                with _lock:
                    STATE_R["done"] += 1
                    save_st()
                return
        except Exception as e:
            wlog("· %s 图库失败: %s" % (lh, str(e)[:50]))
        # 1) TE 官网（料号含字母/官方泰科——te 抓图本身有 h1 料号防错）
        try:
            from _grab_te_img import te_fetch_images
            n = te_fetch_images(lh, tmp)
            if n:
                cands = glob.glob(os.path.join(tmp, "%s_*.png" % lh)) + glob.glob(os.path.join(tmp, "*." + "png"))
                for c in cands:
                    if c.lower().endswith((".png", ".jpg")) and os.path.getsize(c) > 5000:
                        got = c
                        break
            if got:
                wlog("→ %s TE官网取到 %s" % (lh, os.path.basename(got)))
        except Exception as e:
            wlog("· %s TE失败: %s" % (lh, str(e)[:50]))
        # 2) RenhotecRF（RF 连接器原厂——反爬弱，urllib 直抓，冷门号也有图）
        if not got:
            try:
                from renhotec import find_product, fetch_images as rh_images
                ru, slug = find_product(lh)
                if ru:
                    # 顺手抓规格书（Drawing PDF = 工程规格书）
                    try:
                        from renhotec import fetch_drawing
                        if not any("规格" in n for n in os.listdir(d)):
                            dp = fetch_drawing(ru, tmp)
                            if dp:
                                shutil.copy2(dp, os.path.join(d, "%s_规格书.pdf" % lh))
                                wlog("· %s 规格书已抓（Drawing PDF）" % lh)
                    except Exception:
                        pass
                    rn = rh_images(ru, tmp, max_imgs=3)
                    wlog("· %s Renhotec: %s | 下载 %d" % (lh, (slug or '')[:40], rn))
                    if rn:
                        cs = sorted(glob.glob(os.path.join(tmp, "renhotec_*")))
                        if cs:
                            got = cs[0]
            except Exception as e:
                wlog("· %s Renhotec失败: %s" % (lh, str(e)[:50]))
        # 3) 立创兜底（lcsc_fetch_images(lh, outdir, max_imgs) 下载 {lh}_lcN.jpg）
        if not got:
            try:
                from lcsc import lcsc_fetch_images
                title, cnt = lcsc_fetch_images(lh, tmp, max_imgs=3)
                wlog("· %s LCSC: %s | 下载 %d" % (lh, (title or '')[:30], cnt))
                cands = sorted(glob.glob(os.path.join(tmp, "%s_lc*.jpg" % lh)))
                for c in cands:
                    if os.path.getsize(c) > 5000:
                        got = c
                        break
            except Exception as e:
                wlog("· %s LCSC失败: %s" % (lh, str(e)[:50]))
        if not got:
            wlog("✗ %s 无源可换（TE+LCSC 都没图）——保持换源待人工" % lh)
            mark_state(lh, "换源", "无源可换待人工")
            with _lock:
                STATE_R["fail"] += 1
                save_st()
            return
        ts = datetime.datetime.now().strftime("%H%M%S")
        dest = os.path.join(d, "%s_白底_待复检%s.png" % (lh, ts))
        shutil.copy2(got, dest)
        mark_state(lh, "待复检", "换源完成：" + os.path.basename(dest))
        wlog("✓ %s 换源 → %s（待复检）" % (lh, os.path.basename(dest)))
        with _lock:
            STATE_R["done"] += 1
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
            lhs = [lh for lh, v in st.get("items", {}).items() if v.get("audit") == "换源"]
        except Exception:
            lhs = []
    STATE_R.update({"total": len(lhs), "done": 0, "fail": 0, "cur": "", "msg": "换源启动 %d 品" % len(lhs), "mode": "换源"})
    save_st()
    wlog("换源批：%d 品 %s" % (len(lhs), lhs))
    if not lhs:
        STATE_R["msg"] = "无换源品"
        save_st()
        return
    with ThreadPoolExecutor(max_workers=CONC) as ex:
        list(ex.map(src_one, lhs))
    STATE_R["msg"] = "换源完成 done=%d fail=%d" % (STATE_R["done"], STATE_R["fail"])
    save_st()
    try:
        shutil.rmtree(TMPROOT, ignore_errors=True)
    except Exception:
        pass
    wlog("换源批结束 done=%d fail=%d" % (STATE_R["done"], STATE_R["fail"]))

if __name__ == "__main__":
    main()
