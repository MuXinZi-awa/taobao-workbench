# -*- coding: utf-8 -*-
"""226 品 × 三店在售全量 → 四类分流（只读，纯计算，不开浏览器）

输入（都是本地文件，先由 _onsale_enum.py 抓全量）：
  办公室工作/数据/二店226品品牌分组_1023.csv     226 品（品牌 / 目标计划组 / 历史状态 / 备注）
  办公室工作/数据/二店待上架_1023.csv             补 ERP成本 / ERP分类 / 素材缺项（133 行，按料号左连）
  pipeline/scripts/runtime/onsale_enum.json       三店在售全量（带 total 对账结论）

输出：办公室工作/数据/三店分流_1026.csv

分流（先算 D，再按三店全量判 A/B/C）：
  A 已上过且双百        —— 三店在售 + 双百
  B 已上过但非双百      —— 三店在售 + 不是双百（补必填属性/素材冲双百）
  C 三店查无此品        —— 三店在售清单里没有 → 新品待上架
  D 忽略                —— 源表标不归我们管/非本店经营，注明原因
  待人工                —— 枚举不完整或店铺身份不符，不许猜（严格不并入 C）
"""
import collections
import csv
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

BASE = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace"
DATA = os.path.join(BASE, "办公室工作", "数据")
# 枚举结果在**本脚本旁边**的 runtime/（两份部署各读各的），不能写死开发目录——
# 写死会导致从线上插件跑时读不到文件（1026 踩到）
HERE = os.path.dirname(os.path.abspath(__file__))
ENUM = os.path.join(HERE, "runtime", "onsale_enum.json")
SRC226 = os.path.join(DATA, "二店226品品牌分组_1023.csv")
SRC133 = os.path.join(DATA, "二店待上架_1023.csv")
OUT = os.path.join(DATA, "三店分流_1026.csv")

ALNUM = re.compile(r"[0-9A-Za-z]")


def find_in_title(lh, title):
    """料号是否作为**独立标识**出现在标题里（防姐妹型号串图/误判）。
    前一个字符不能是字母数字（防 967631-1 命中 1-967631-1）；
    后一个字符不能是字母数字或 '-'（防 DT06-3S 命中 DT06-3S-C015）。"""
    t = title or ""
    low, l = t.lower(), (lh or "").lower()
    if not low or not l:
        return False
    start = 0
    while True:
        i = low.find(l, start)
        if i < 0:
            return False
        before_ok = True
        if i > 0:
            cb = low[i - 1]
            if ALNUM.match(cb):
                before_ok = False
            elif cb == "-" and i > 1 and ALNUM.match(low[i - 2]):
                before_ok = False
        j = i + len(l)
        after_ok = True
        if j < len(low):
            ca = low[j]
            if ALNUM.match(ca) or ca == "-":
                after_ok = False
        if before_ok and after_ok:
            return True
        start = i + 1


def _f(v):
    try:
        return float(str(v).strip())
    except Exception:
        return None


def main():
    if not os.path.isfile(ENUM):
        print(json.dumps({"ok": 0, "error": "没有 onsale_enum.json——先跑全量枚举"}, ensure_ascii=False))
        return 1
    blob = json.load(io.open(ENUM, encoding="utf-8"))
    meta = blob.get("meta") or {}
    inv = blob.get("items") or []
    complete = bool(meta.get("complete"))

    rows226 = list(csv.DictReader(io.open(SRC226, encoding="utf-8-sig")))
    extra = {}
    if os.path.isfile(SRC133):
        for x in csv.DictReader(io.open(SRC133, encoding="utf-8-sig")):
            extra[(x.get("料号") or "").strip()] = x
    print("[输入] 226 品 %d 行 / 三店在售 %d 件（声明 %s / complete=%s）"
          % (len(rows226), len(inv), meta.get("declared"), complete))

    # 双百判定：基础分 100 且扶优分 100（接口字段是 diagnoseInfoV3.speScore，
    # 早期脚本猜的 advantageScore 根本不存在）；scoreLabel='流量加速中' 是同一回事的口径兜底。
    def _spe(it):
        v = it.get("spe")
        if v is None:
            d = it.get("diag") or {}
            for k in ("speScore", "advantageScore", "supportScore"):
                if d.get(k) not in (None, ""):
                    try:
                        v = float(d[k])
                    except Exception:
                        v = None
                    break
        return v

    def is_dual(it):
        if it.get("label") == "流量加速中":
            return True
        return (it.get("basic") == 100 and _spe(it) == 100)

    def score_txt(it):
        bits = []
        if it.get("basic") is not None:
            bits.append("基础%g" % it["basic"])
        _s = _spe(it)
        if _s is not None:
            bits.append("扶优%g" % _s)
        if it.get("label"):
            bits.append(it["label"])
        return " / ".join(bits) if bits else "（接口未返分项）"

    sister_idx = [(x["id"], x.get("title") or "") for x in inv]

    out = []
    for r in rows226:
        lh = (r.get("料号") or "").strip()
        brand = (r.get("品牌") or "").strip()
        grp = (r.get("目标计划组") or "").strip()
        old_status = (r.get("上架状态") or "").strip()
        old_note = (r.get("备注") or "").strip()
        conflict = (r.get("口径冲突") or "").strip()
        ex = extra.get(lh) or {}

        hits = [x for x in inv if find_in_title(lh, x.get("title"))]
        o = {"料号": lh, "品牌": brand, "目标计划组": grp,
             "三店状态": "", "三店商品ID": "", "基础分": "", "扶优分": "",
             "双百与否": "", "分流分类": "", "原因": "", "备注": "",
             "二店状态1023": old_status, "历史备注": old_note,
             "ERP成本": (ex.get("ERP成本") or "").strip(),
             "ERP分类": (ex.get("ERP分类") or "").strip(),
             "素材缺项": (ex.get("缺项") or "").strip()}
        notes = []

        # ── 三店线上事实（先写，D 类也要如实记录）──────────────────
        if not complete:
            o["三店状态"] = "待人工（清单不完整）"
            o["双百与否"] = "待人工"
        elif hits:
            h = hits[0]
            o["三店状态"] = "在售"
            o["三店商品ID"] = h["id"]
            o["基础分"] = "" if h.get("basic") is None else "%g" % h["basic"]
            _hs = _spe(h)
            o["扶优分"] = "" if _hs is None else "%g" % _hs
            o["双百与否"] = "双百" if is_dual(h) else "非双百"
            if len(hits) > 1:
                notes.append("店内有 %d 条命中（取第一条 id=%s）" % (len(hits), h["id"]))
            notes.append("质量分：" + score_txt(h))
        else:
            o["三店状态"] = "查无此品"
            o["双百与否"] = "—"
            near = [t for _i, t in sister_idx
                    if re.search(r"%s[-A-Za-z0-9]+" % re.escape(lh), t or "", re.I)]
            if near:
                notes.append("⚠ 店内有姐妹型号（不是本料号）：%s" % near[0][:44])

        # ── 分流 ────────────────────────────────────────────────
        if grp == "不推" or "不归我们管" in old_note:
            o["分流分类"] = "D-忽略"
            o["原因"] = "源表标「不归我们管，不参与推广」"
            if brand in ("Bourns", "未知品牌"):
                o["原因"] += "（多为 Bourns 电阻/电感/保险丝等非连接器类目）"
        elif not complete:
            o["分流分类"] = "待人工"
            o["原因"] = "三店在售清单没抓全（%s）——不许据此判「没上架」" % (
                meta.get("error") or "complete=0")
        elif hits:
            if o["双百与否"] == "双百":
                o["分流分类"] = "A-已上过且双百"
                o["原因"] = "三店在售且双百，不用管"
            else:
                o["分流分类"] = "B-已上过非双百"
                o["原因"] = "三店在售但未双百（%s）——补必填属性/素材冲双百" % score_txt(hits[0])
        else:
            o["分流分类"] = "C-新品待上架"
            o["原因"] = "三店在售全量（%d 件，已与声明数对账）里无此料号" % len(inv)
            if conflict or old_status == "未知·待核":
                o["原因"] += "；历史两表曾冲突/待核，已按三店全量枚举为准"
        if conflict and "冲突" not in " ".join(notes):
            notes.append("历史口径冲突：%s" % old_note[:40])
        o["备注"] = "；".join(notes)
        out.append(o)

    order = {"A-已上过且双百": 0, "B-已上过非双百": 1, "C-新品待上架": 2,
             "D-忽略": 3, "待人工": 4}
    out.sort(key=lambda x: (order.get(x["分流分类"], 9), x["品牌"], x["料号"]))

    HEAD = ["料号", "品牌", "目标计划组", "三店状态", "三店商品ID", "基础分", "扶优分",
            "双百与否", "分流分类", "原因", "备注", "二店状态1023", "历史备注",
            "ERP成本", "ERP分类", "素材缺项"]
    with io.open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEAD, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    cnt = collections.Counter(x["分流分类"] for x in out)
    print("[写出] %s  %d 行" % (OUT, len(out)))
    for k in ("A-已上过且双百", "B-已上过非双百", "C-新品待上架", "D-忽略", "待人工"):
        print("   %-14s %3d" % (k, cnt.get(k, 0)))
    for k in ("C-新品待上架", "待人工"):
        ls = [x["料号"] for x in out if x["分流分类"] == k]
        if ls:
            print("   %s：%s" % (k, " ".join(ls)))
    print(json.dumps({"ok": 1, "out": OUT, "total": len(out), "counts": dict(cnt),
                      "complete": complete, "onsale": len(inv)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
