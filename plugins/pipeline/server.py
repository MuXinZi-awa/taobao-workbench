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
TG_REC = os.path.join(r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\数据", "推广记录.csv")

def load_state():
    try:
        return json.load(io.open(STATE_FP, encoding="utf-8"))
    except Exception:
        return {"items": {}}

def save_state(st):
    io.open(STATE_FP, "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False, indent=1))

def _acct():
    """当前激活店铺连接（多账号：面板显示 + 数据过滤）"""
    try:
        import sys as _s
        _wb = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\工具\workbench"
        if _wb not in _s.path:
            _s.path.insert(0, _wb)
        import conn_store
        return conn_store.active_of("taobao") or {}
    except Exception:
        return {}


def _read_tg_rec():
    """推广记录.csv（料号,状态,时间,账号）→ {料号: 最后状态}；按当前账号过滤
    注：该文件无表头，必须用 csv.reader（用 DictReader 把首行当表头 → 恒返回空）"""
    out = {}
    if not os.path.exists(TG_REC):
        return out
    try:
        _mid = str(_acct().get("member_id") or "")
        with io.open(TG_REC, encoding="utf-8-sig", errors="replace") as f:
            for _r in csv.reader(f):
                if not _r or not _r[0].strip():
                    continue
                _av = (_r[3].strip() if len(_r) > 3 else "") or "<MEMBER_ID>"   # 老数据归店铺1
                if _mid and _av != _mid:
                    continue
                out.setdefault(_r[0].strip(), (_r[1].strip() if len(_r) > 1 else ""))
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
    for k in ("封面", "主图", "规格书", "视频", "详情图"):
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
    """按天归档：日志文件日期≠今天 → 改名加日期保留"""
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
    # 清理：超期归档删除（保留天数 log_keep_days.json 默认 30）
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
    if audit and audit != "放行":
        # 在途品（待复检/送修/换源）都先看产物
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
    # 原图缺失时也认产物（白底/待复检/库源/换源）
    for _pat in ("*_待复检*", "*白底*", "*_库源*", "*_换源*"):
        _fs = sorted(_g.glob(os.path.join(d, _pat)))
        _fs = [x for x in _fs if x.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
        if _fs:
            return _fs[0]
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
    """必须**起子进程**跑（playwright 在内核请求线程里不稳 → 拿不到 _m_h5_tk）
    子进程：scripts/_classify_one.py 料号1,料号2 → stdout 最后一行是 JSON"""
    import subprocess
    fp = os.path.join(SCRIPTS, "_classify_one.py")
    if not os.path.isfile(fp):
        return [], "缺少 _classify_one.py"
    ls = [str(x).strip() for x in lhs if str(x).strip()]
    if not ls:
        return [], "无料号"
    try:
        r = subprocess.run([PY, "-X", "utf8", "-u", fp, ",".join(ls)],
                           cwd=SCRIPTS, capture_output=True, timeout=600)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        tail = ""
        for line in out.strip().splitlines()[::-1]:
            if line.strip().startswith("{"):
                tail = line.strip()
                break
        if not tail:
            return [], ("子进程无结果: " + ((out[-150:] or err[-150:]).strip() or "(空输出)"))
        d = json.loads(tail)
        if not d.get("ok"):
            return [], d.get("error") or "未知错误"
        return d.get("items") or [], ""
    except subprocess.TimeoutExpired:
        return [], "分流子进程超时(600s)"
    except Exception as e:
        return [], "分流异常: %s" % str(e)[:100]

# ── 阶段 → 现成脚本（调用层）────────────────────
#    脚本都吃「表格」不吃参数（表头要有 料号 列）→ 先落临时表，再子进程调（内核线程里别起 playwright）
RT = os.path.join(TG, "runtime", "python.exe")
if not os.path.isfile(RT):
    RT = PY
TMP = os.path.join(TG, "runtime", "_pl_tmp")


def _mid():
    return str(_acct().get("member_id") or "")


def auto_campaigns():
    """取该账号「未满( count<500 )」的计划 ID —— 与 scheduler/server.py 的 auto_campaigns 同源
    （读 runtime/plan_state_<mid>.json；那份由推广批的容量预检实时落盘——不写死计划 ID）"""
    fp = os.path.join(TG, "runtime", ("plan_state_%s.json" % _mid()) if _mid() else "plan_state.json")
    if not os.path.isfile(fp):
        fp = os.path.join(TG, "runtime", "plan_state.json")
    try:
        d = json.load(io.open(fp, encoding="utf-8"))
        return ",".join([k for k, v in d.items()
                         if v.get("count") is not None and int(v.get("count") or 0) < 500])
    except Exception:
        return ""


def _write_tmp(name, rows, header):
    """落临时表（脚本们的统一入参形式）——留在 runtime/_pl_tmp 便于事后查「到底给了什么」"""
    os.makedirs(TMP, exist_ok=True)
    fp = os.path.join(TMP, name)
    with io.open(fp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return fp


def _run_script(script, args, timeout=3600):
    """子进程跑现成脚本（照 _classify_one 范式）→ (ok, 末行输出)"""
    if not os.path.isfile(script):
        return False, "缺少脚本: %s" % os.path.basename(script)
    try:
        r = subprocess.run([RT, "-X", "utf8", "-u", script] + [str(a) for a in args],
                           cwd=os.path.dirname(script), capture_output=True, timeout=timeout)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        # 全量输出落盘——脚本的关键判断（上传/提交）只打在 stdout，不存就没法事后查
        try:
            with io.open(os.path.join(TG, "runtime", "stage_out.log"), "a", encoding="utf-8") as _f:
                _f.write("\n=== %s %s rc=%s ===\n%s\n%s\n" % (
                    _dt.datetime.now().strftime("%m-%d %H:%M:%S"),
                    os.path.basename(script), r.returncode, out[-8000:], err[-2000:]))
        except Exception:
            pass
        txt = out if out.strip() else err
        tail = [x for x in txt.strip().splitlines() if x.strip()]
        return (r.returncode == 0), (tail[-1][:160] if tail else "(无输出)")
    except subprocess.TimeoutExpired:
        return False, "子进程超时（%d 秒）" % timeout
    except Exception as e:
        return False, "子进程异常: %s" % str(e)[:120]


def _types_of(lhs):
    """读 state 里的 type——查询分流落的 type 是后续阶段的路由依据"""
    st = load_state()
    return {lh: str((st["items"].get(lh) or {}).get("type") or "").strip() for lh in lhs}


def _ids_of(lhs):
    st = load_state()
    return [(lh, str((st["items"].get(lh) or {}).get("id") or "").strip()) for lh in lhs]


# ── 流水线阶段定义（数据驱动；改 desc 不影响前端）────────────────────
STAGES = [
    {"key": "query",     "label": "查询",      "ready": True,
     "desc": "按料号搜商品 + 查双百 → 定 type（新品-待上架 / 老品-需优化 / 老品-双百跳过）"},
    {"key": "attrs",     "label": "属性",      "ready": True,
     "desc": "爬属性 fetch_attrs（只读官网 → 写属性清单.csv 累积；不改商品）"},
    {"key": "material",  "label": "素材",      "ready": True,
     "desc": "素材清点：封面/主图/规格书/视频 缺啥补啥（素材_prep / _auto_material）"},
    {"key": "sort",      "label": "整理",      "ready": True,
     "desc": "素材归位整理（organize.py——落位 + 拍前须知）"},
    {"key": "audit",     "label": "人工审核",  "ready": True,
     "desc": "逐张看素材 → 放行 / 水印送修 / 质量差换源（铁律：全人工过）"},
    {"key": "pub",       "label": "上品/优化", "ready": True,
     "desc": "按 type 自动分流：新品-待上架 → _xinpin_shangpin（上品）；老品-需优化 → _batch_optimize（编辑提交+补属性）"},
    {"key": "tuiguang",  "label": "推广",      "ready": True,
     "desc": "tuiguang_auto --excel 临时表 --campaigns 未满计划 --no-pop（计划自动取，不写死）"},
]


def handle(action, qs):
    if action == "acct":
        _a = _acct()
        return {"ok": True, "account": {"name": _a.get("name") or "", "member_id": _a.get("member_id") or ""}}
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
        # 分流结果落 state（type/id/title）——否则前端每次审核都要重查
        try:
            st = load_state()
            for it in items:
                _lh = (it.get("lh") or "").strip()
                if not _lh:
                    continue
                rec = st["items"].setdefault(_lh, {})
                # “查询异常”不写 state——否则一次查询失败就把已分好的类型抹掉
                if it.get("type") and it["type"] != "查询异常":
                    rec["type"] = it["type"]
                if it.get("id"):
                    rec["id"] = it["id"]
                if it.get("title"):
                    rec["title"] = it["title"]
                if it.get("type") and it["type"] != "查询异常":
                    rec["type_time"] = _dt.datetime.now().strftime("%m-%d %H:%M:%S")
            save_state(st)
        except Exception:
            pass
        # _classify_one 只回 {lh,id,title,type,dual,note}，没有素材/审核/推广列——
        # 以 scan_batch 为底补齐，再用分流结果覆盖 type/id/title
        try:
            _base = {x.get("lh"): x for x in scan_batch([(it.get("lh") or "").strip() for it in items])}
            _merged = []
            for it in items:
                _b = dict(_base.get((it.get("lh") or "").strip()) or {})
                _b.update({k: v for k, v in it.items() if v not in (None, "")})
                _merged.append(_b)
            items = _merged
        except Exception:
            pass
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
    if action == "stages":
        # 阶段定义（前端渲染阶段条用）
        return {"ok": True, "stages": STAGES}
    if action == "run":
        # 阶段执行入口：已就绪的转调原 action；未就绪的只提示不动作
        key = (qs.get("stage") or "").strip()
        lhs = [x.strip() for x in (qs.get("lhs") or "").split(",") if x.strip()]
        st = None
        for _s in STAGES:
            if _s["key"] == key:
                st = _s
                break
        if st is None:
            return {"ok": False, "error": "未知阶段: %s" % key}
        if not st["ready"]:
            return {"ok": False, "ready": False,
                    "error": "「%s」还没接血肉（骨架已就位）｜计划调用：%s" % (st["label"], st["desc"])}
        if key == "query":
            return handle("classify", {"lhs": ",".join(lhs)})
        if key == "material":
            if not lhs:
                return {"ok": False, "error": "缺 lhs"}
            # 本地一条龙补齐：make_local 出封面/主图/详情图/视频（已生成自动跳过，--force 才重跑）
            _force = str((qs.get("force") or "")).strip() in ("1", "true", "yes")
            _fextra = ["--force"] if _force else []
            _notes = ["强制重做（不跳过已生成）"] if _force else []
            for _lh in lhs:
                _ok, _tail = _run_script(os.path.join(TG, "make_local.py"), [_lh] + _fextra, timeout=1800)
                _notes.append("%s%s" % (_lh, "✓" if _ok else "✗"))
            _items = scan_batch(lhs)
            _bad = [x for x in _items if x.get("缺列表")]
            return {"ok": True, "items": _items,
                    "msg": "本地一条龙：%s%s" % ("  ".join(_notes),
                            ("｜仍缺：" + "；".join("%s→%s" % (m["lh"], ",".join(m["缺列表"])) for m in _bad[:6]))
                            if _bad else "｜素材齐 ✓")}
        if key == "audit":
            return {"ok": True, "msg": "人工审核走面板「\U0001F4CB 开始审核」（预览 Tab 标记）", "lhs": lhs}
        if key == "attrs":
            if not lhs:
                return {"ok": False, "error": "缺 lhs"}
            fp = _write_tmp("attrs.csv", [[x] for x in lhs], ["料号"])
            ok, tail = _run_script(os.path.join(TG, "fetch_attrs.py"), [fp, len(lhs)], timeout=3600)
            return {"ok": ok, "items": scan_batch(lhs), "msg": tail,
                    "error": None if ok else ("属性爬取失败：" + tail)}
        if key == "sort":
            if not lhs:
                return {"ok": False, "error": "缺 lhs"}
            fp = _write_tmp("sort.csv", [[x] for x in lhs], ["料号"])
            ok, tail = _run_script(os.path.join(TG, "organize.py"), [fp], timeout=1800)
            # 落位后校验齐全：封面/详情图/视频/规格书
            items = scan_batch(lhs)
            _bad = [x for x in items if x.get("缺列表")]
            _chk = "齐全 ✓" if not _bad else "缺：" + "；".join("%s→%s" % (x["lh"], ",".join(x["缺列表"])) for x in _bad[:8])
            return {"ok": ok, "items": items, "msg": "%s｜齐全校验：%s" % (tail, _chk),
                    "error": None if ok else ("整理失败：" + tail)}
        if key == "pub":
            if not lhs:
                return {"ok": False, "error": "缺 lhs"}
            tmap = _types_of(lhs)
            new = [x for x in lhs if "新品" in tmap.get(x, "")]
            old = [x for x in lhs if "需优化" in tmap.get(x, "")]
            skip = [x for x in lhs if (x not in new) and (x not in old)]
            # 预演：勾「预演不提交」→ 透传 --no-submit（两个脚本都支持）
            _dry = str((qs.get("dry") or "")).strip() in ("1", "true", "yes")
            _extra = ["--no-submit"] if _dry else []
            notes = ["预演模式（不提交）"] if _dry else []
            if skip:
                notes.append("跳过 %d 个（type 未定/双百跳过）：%s" % (len(skip), ",".join(skip[:6])))
            if new:
                ok, tail = _run_script(os.path.join(TG, "_xinpin_shangpin.py"),
                                       ["--only", ",".join(new)] + _extra, timeout=7200)
                notes.append("新品上品 %d 个 → %s（%s）" % (len(new), "ok" if ok else "失败", tail))
            if old:
                pairs = _ids_of(old)
                if not [p for p in pairs if p[1]]:
                    notes.append("老品优化 %d 个缺淘宝ID——先跑「查询」" % len(old))
                else:
                    # _batch_optimize.load_targets 读第 1 列=料号、第 4 列=淘宝ID
                    fp = _write_tmp("opt.csv", [[lh, "", "", iid] for lh, iid in pairs],
                                    ["料号", "标题", "类型", "淘宝ID"])
                    ok, tail = _run_script(os.path.join(TG, "_batch_optimize.py"), [fp] + _extra, timeout=7200)
                    notes.append("老品优化 %d 个 → %s（%s）" % (len(old), "ok" if ok else "失败", tail))
            return {"ok": True, "items": scan_batch(lhs), "msg": " ／ ".join(notes) or "无可处理项"}
        if key == "tuiguang":
            if not lhs:
                return {"ok": False, "error": "缺 lhs"}
            pairs = [p for p in _ids_of(lhs) if p[1]]
            if not pairs:
                return {"ok": False, "error": "state 里没有淘宝ID——先跑「查询」分流"}
            camps = auto_campaigns()
            if not camps:
                return {"ok": False,
                        "error": "取不到可推计划——请先在「scheduler」点「刷新容量」（或手填计划组）"}
            fp = _write_tmp("tuiguang.csv", [[lh, iid] for lh, iid in pairs], ["料号", "淘宝ID"])
            ok, tail = _run_script(os.path.join(TG, "tuiguang_auto.py"),
                                   ["--excel", fp, "--limit", len(pairs),
                                    "--campaigns", camps, "--no-pop"], timeout=7200)
            return {"ok": ok, "items": scan_batch(lhs),
                    "msg": "推广 %d 品（计划 %s）：%s" % (len(pairs), camps, tail),
                    "error": None if ok else ("推广失败：" + tail)}
        return {"ok": False, "error": "阶段未实现: %s" % key}
    return {"ok": False, "error": "未知 action: %s" % action}
