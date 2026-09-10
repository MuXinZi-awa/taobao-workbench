# -*- coding: utf-8 -*-
"""流水线插件 server：状态读取（只读调用层）"""
import json, os, io, sys, csv, subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_FP = os.path.join(ROOT, "state.json")
TG = r"C:\Users\jdt-pty\Desktop\推广一键跑"
PBASE = os.path.dirname(os.path.abspath(__file__))   # 插件目录
SCRIPTS = os.path.join(PBASE, "scripts")             # 插件内脚本（自包含）
PY = sys.executable                                  # 工作台 python（含依赖）
SUCAI = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\素材\产品素材"
TG_REC = os.path.join(TG, "推广记录.csv")

def load_state():
    try:
        return json.load(io.open(STATE_FP, encoding="utf-8"))
    except Exception:
        return {"items": {}}

def save_state(st):
    io.open(STATE_FP, "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False, indent=1))

def _read_tg_rec():
    """推广记录.csv → {料号: 最后状态} 只读"""
    out = {}
    if not os.path.exists(TG_REC):
        return out
    try:
        with io.open(TG_REC, encoding="utf-8-sig", errors="replace") as f:
            for row in csv.DictReader(f):
                cols = list(row)
                lh = row.get("料号") or row.get("lh") or ""
                st = row.get("状态") or row.get("结果") or ""
                if lh:
                    out.setdefault(lh.strip(), st.strip())
    except Exception:
        pass
    return out

try:
    sys.path.insert(0, SCRIPTS)
    import lib_images  # 本地图库索引 F:\连接图图库
except Exception:
    lib_images = None


def scan_material(lh):
    """素材目录清点 → 缺啥补啥看板"""
    d = os.path.join(SUCAI, lh)
    out = {"封面": False, "主图": False, "规格书": False, "视频": False, "详情图": False, "文件": [], "缺失": []}
    lib = []
    try:
        if lib_images:
            lib = lib_images.find(lh)
    except Exception:
        lib = []
    out["图库"] = lib
    out["图库数"] = len(lib)
    if not os.path.isdir(d):
        out["缺失"] = ["整个目录"]
        out["文件路径"] = []
        return out
    names = os.listdir(d)
    out["文件"] = sorted(names)
    out["文件路径"] = [os.path.join(d, n) for n in out["文件"]]
    for n in names:
        low = n.lower()
        if "封面" in n or "cover" in low: out["封面"] = True
        elif "详情" in n or "detail" in low: out["详情图"] = True
        if any(k in n for k in ("白底", "主图", "cover")) and not any(k in n for k in ("详情", "待复核")):
            out["主图"] = True
        if any(k in low for k in (".pdf", ".doc", ".docx", "规格", "spec")): out["规格书"] = True
        if low.endswith((".mp4", ".mov", ".webm")): out["视频"] = True
    for k in ("封面", "主图", "规格书", "视频"):
        if not out[k]:
            out["缺失"].append(k)
            if k in ("封面", "主图") and out["图库数"]:
                out["可换源"] = True  # 图库有图——主图/封面可从库补
    # 参考图：封面/主图1 原图（排除 _标注 加工图 / _白底 AI返图）——默认审核看的图
    ref = ""
    for pat in ("%s_主图1", "%s_封面", "%s_主图"):
        for n in sorted(names):
            if pat % lh == n.split(".")[0] and "标注" not in n and "白底" not in n and n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                ref = os.path.join(d, n)
                break
        if ref:
            break
    if not ref:
        for n in sorted(names):
            if "标注" not in n and "白底" not in n and n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                ref = os.path.join(d, n)
                break
    out["参考图"] = ref
    return out

def scan_batch(lhs):
    """逐品汇总状态（本版：素材清点 + 推广记录 + state 内的人工/阶段标记）"""
    st = load_state()
    tg = _read_tg_rec()
    items = []
    for lh in lhs:
        lh = (lh or "").strip()
        if not lh:
            continue
        m = scan_material(lh)
        rec = st["items"].get(lh, {})
        s = rec.get("stage", "")
        items.append({
            "lh": lh,
            "id": rec.get("id", ""),
            "title": rec.get("title", ""),
            "素材": "缺:" + ",".join(m["缺失"]) if m["缺失"] else "齐",
            "缺列表": m["缺失"],
            "type": rec.get("type", ""),
            "id": rec.get("id", ""),
            "title": rec.get("title", ""),
            "audit": rec.get("audit", ""),
            "ref": _ref_for(lh, rec.get("audit", "")),
            "orig": _ref_for(lh, ""),
            "审核": rec.get("audit", ""),
            "上品/优化": rec.get("sp", ""),
            "推广": tg.get(lh, ""),
            "stage": s,
        })
    try:
        _wlog("流水线扫描 %d 品: %s" % (len(items), " | ".join("%s=%s" % (i["lh"], i["素材"][:16]) for i in items[:10])))
    except Exception:
        pass
    return items

import datetime as _dt
_LOG_FP = r"C:\Users\jdt-pty\Desktop\推广一键跑\runtime\pipeline.log"


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
    # 0909 清理：超期归档删除（保留天数 log_keep_days.json 默认 30）
    try:
        import glob as _g3, time as _t3
        _kd = 30
        try:
            _kd = int(open(r"C:\\Users\\jdt-pty\\Desktop\\推广一键跑\\runtime\\log_keep_days.json", encoding="utf-8").read().strip())
        except Exception:
            pass
        _base = _LOG_FP.replace(".log", "")
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
        with io.open(_LOG_FP, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (_dt.datetime.now().strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass




def _ref_for(lh, audit):
    """审核参考图：待复检品看白底待复检产物；否则封面/主图1 原图"""
    import glob as _g
    d = os.path.join(SUCAI, lh)
    if not os.path.isdir(d):
        return ""
    if audit == "待复检":
        # 待复检产物（白底/库源/换源——最新）
        fs = sorted(_g.glob(os.path.join(d, "*_待复检*.*")))
        if fs:
            return fs[0]  # 库源1 在前（首图）
        fs = sorted(_g.glob(os.path.join(d, "*白底*.*")))
        if fs:
            return fs[-1]
    try:
        names = sorted(os.listdir(d))
    except Exception:
        return ""
    for pat in ("%s_主图1", "%s_封面", "%s_主图"):
        for n in names:
            base = n.split(".")[0]
            if pat % lh == base and all(k not in n for k in ("标注", "白底", "待复核", "待复检")) and n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                return os.path.join(d, n)
    for n in names:
        if all(k not in n for k in ("标注", "白底", "待复核", "待复检")) and n.lower().endswith((".png", ".jpg", ".jpeg")):
            return os.path.join(d, n)
    # 素材目录无图 → 图库兜底（自有实拍——审核仍能看到产品图）
    try:
        if lib_images:
            lib = lib_images.find(lh)
            if lib:
                return lib[0]
    except Exception:
        pass
    return ""

def classify_batch(lhs):
    """0909 查询分流：每料号搜商品(老/新) + 老品查双百 → type（MtopClient 单连循环）"""
    try:
        sys.path.insert(0, SCRIPTS)
        from _mtop_api import MtopClient
    except Exception as e:
        return [], "导入失败: %s" % str(e)[:60]
    mc = MtopClient()
    try:
        mc.open()
    except Exception as e:
        return [], "Mtop 打开失败: %s" % str(e)[:60]
    items = []
    try:
        for lh in lhs:
            lh = (lh or "").strip()
            if not lh:
                continue
            item = {"lh": lh, "id": "", "title": "", "type": "", "dual": None, "素材": "—", "缺列表": [], "note": ""}
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
                    item["id"] = str(hit.get("itemId", ""))
                    item["title"] = str(hit.get("title", ""))[:40]
                    try:
                        out, _ = mc.check_dual(lh)
                        if out:
                            sl = out[0].get("scoreLabel", "")
                            item["dual"] = sl == "流量加速中"
                            item["type"] = "老品-双百跳过" if item["dual"] else "老品-需优化"
                            item["note"] = sl[:20]
                        else:
                            item["type"] = "老品-需优化"
                            item["note"] = "无双百信息"
                    except Exception as e:
                        item["type"] = "老品-需优化"
                        item["note"] = "双百异常:%s" % str(e)[:25]
                else:
                    item["type"] = "新品-待上架"
                    item["note"] = "店铺搜不到该料号"
            except Exception as e:
                item["type"] = "查询异常"
                item["note"] = str(e)[:30]
            try:
                m = scan_material(lh)
                item["素材"] = "缺:" + ",".join(m["缺失"]) if m["缺失"] else "齐"
                item["缺列表"] = m["缺失"]
                item["图库数"] = m.get("图库数", 0)
                if m.get("图库数"):
                    _only_img = [x for x in m["缺失"] if x in ("封面", "主图")]
                    if _only_img and len(_only_img) == len(m["缺失"]):
                        # 只缺图且有库源——标可补（不算硬缺）
                        item["素材"] = "缺:%s[库%d→可补]" % (",".join(m["缺失"]), m["图库数"])
                    else:
                        item["素材"] += "(库%d)" % m["图库数"]
                item["orig"] = _ref_for(lh, "")
                try:
                    _st0 = load_state()
                except Exception:
                    _st0 = {}
                item["audit"] = (_st0.get("items", {}).get(lh, {}) or {}).get("audit", "")
                item["ref"] = _ref_for(lh, item.get("audit", ""))
            except Exception:
                pass
            try:
                st = load_state()
                st["items"].setdefault(lh, {})["type"] = item["type"]
                if item.get("id"):
                    st["items"][lh]["id"] = item["id"]
                if item.get("title"):
                    st["items"][lh]["title"] = item["title"]
                save_state(st)
            except Exception:
                pass
            items.append(item)
    finally:
        try:
            mc.close()
        except Exception:
            pass
    try:
        _wlog("查询分流 %d 品: %s" % (len(items), " | ".join("%s=%s" % (i["lh"], i["type"]) for i in items)))
    except Exception:
        pass
    return items, ""


def handle(action, qs):
    if action == "scan":
        lhs = qs.get("lhs", "").split(",") if qs.get("lhs") else []
        return {"ok": True, "items": scan_batch(lhs)}
    if action == "audit":
        # 审核标记：lh + 结果(放行/送修/换源) + 备注（可反悔改标——覆盖写）
        lh = (qs.get("lh") or "").strip()
        res = (qs.get("res") or "").strip()
        note = (qs.get("note") or "").strip()
        if not lh or not res:
            return {"ok": False, "error": "需要 lh + res"}
        st = load_state()
        it = st["items"].setdefault(lh, {})
        it["audit"] = res
        if note:
            it["audit_note"] = note
        it["audit_time"] = _dt.datetime.now().strftime("%m-%d %H:%M:%S")
        save_state(st)
        try:
            _wlog("审核 %s => %s%s" % (lh, res, ("：" + note[:30]) if note else ""))
        except Exception:
            pass
        return {"ok": True, "lh": lh, "audit": res}
    if action == "classify":
        lhs = qs.get("lhs", "").split(",") if qs.get("lhs") else []
        if not lhs:
            return {"ok": False, "error": "需要 lhs"}
        items, err = classify_batch(lhs)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "items": items}
    def _batch_busy():
        """repair_state.json 有在跑批（total>done+fail）→ True"""
        import json as _j
        try:
            fp = os.path.join(SCRIPTS, "repair_state.json")
            if os.path.isfile(fp):
                st = _j.load(io.open(fp, encoding="utf-8"))
                if st.get("total", 0) > 0 and st.get("done", 0) + st.get("fail", 0) < st.get("total", 0):
                    return st.get("mode", "送修")
        except Exception:
            pass
        return None

    if action == "repair":
        """送修批：读 state 标'送修'的品 → AI 修图（repair_batch.py detach 后台跑——产物白底待复检落素材）"""
        _bs = _batch_busy()
        if _bs:
            return {"ok": False, "error": "已有批在跑（%s）——先等完成或停掉" % _bs}
        rt = PY
        rb = os.path.join(SCRIPTS, "repair_batch.py")
        logf = os.path.join(SCRIPTS, "repair.log")
        try:
            # 读送修品数（提示用）
            n = 0
            try:
                st = load_state()
                n = len([lh for lh, v in st.get("items", {}).items() if v.get("audit") == "送修"])
            except Exception:
                pass
            errf = logf.replace(".log", "_err.log")
            subprocess.Popen([rt, "-X", "utf8", "-u", rb], cwd=SCRIPTS,
                             stdout=open(logf, "a", encoding="utf-8"), stderr=open(errf, "a", encoding="utf-8"))
            return {"ok": True, "msg": "送修批已启动（%d 品标送修）——AI 修图约 2-3 分/品，完成自动落素材并标待复检（日志 runtime/repair.log）" % n}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "src-src":
        """换源批：标'换源'品 → TE/立创找干净源 → 白底待复检落素材（switch_src.py detach）"""
        _bs = _batch_busy()
        if _bs:
            return {"ok": False, "error": "已有批在跑（%s）——先等完成或停掉" % _bs}
        rt = PY
        sb = os.path.join(SCRIPTS, "switch_src.py")
        logf = os.path.join(SCRIPTS, "repair.log")
        try:
            n = 0
            try:
                st = load_state()
                n = len([lh for lh, v in st.get("items", {}).items() if v.get("audit") == "换源"])
            except Exception:
                pass
            errf = logf.replace(".log", "_err.log")
            subprocess.Popen([rt, "-X", "utf8", "-u", sb], cwd=SCRIPTS,
                             stdout=open(logf, "a", encoding="utf-8"), stderr=open(errf, "a", encoding="utf-8"))
            return {"ok": True, "msg": "换源批已启动（%d 品标换源）——TE官网/立创找干净源，完成标待复检（日志 repair.log）" % n}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "audit-ref":
        """审核参考现场重查：lh → {ref, orig}（audit 在途品取白底待复检/换源图）"""
        try:
            lh = q.get("lh", "").strip()
            st = load_state()
            audit = (st.get("items", {}).get(lh, {}) or {}).get("audit", "")
            return {"ok": True, "lh": lh, "audit": audit,
                    "ref": _ref_for(lh, audit), "orig": _ref_for(lh, "")}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "repair-status":
        try:
            import json as _j
            fp = os.path.join(SCRIPTS, "repair_state.json")
            if os.path.isfile(fp):
                st = _j.load(io.open(fp, encoding="utf-8"))
                return {"ok": True, "state": st}
            return {"ok": True, "state": None}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
    if action == "material":
        lh = qs.get("lh", "")
        if lh:
            return {"ok": True, "material": scan_material(lh)}
        return {"ok": False, "error": "缺 lh"}
    return {"ok": False, "error": "未知 action: %s" % action}
