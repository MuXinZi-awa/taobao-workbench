# -*- coding: utf-8 -*-
"""三店查重（只读，纯计算）：给定料号清单 × 三店在售全量 → 已有 / 可上 / 待人工

判据只有一条：**全量枚举**（runtime/onsale_enum.json）里能不能按「独立标识」匹配到
这个料号。不按料号搜接口（卖家中心搜索会漏已在售商品，1016 实测）；枚举不完整
（complete=0）时一律「待人工」，绝不把「没抓到」说成「没有」。

用法：python _shop_dupcheck.py [输入csv] [输出csv]
默认：办公室工作/数据/三店上品217_1026.csv → 三店查重_217_1026.csv
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
HERE = os.path.dirname(os.path.abspath(__file__))
ENUM = os.path.join(HERE, "runtime", "onsale_enum.json")

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "三店上品217_1026.csv")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DATA, "三店查重_217_1026.csv")
# 老清单：命中过的直接搬结论（同一份枚举，结论必然一致，搬过来是为了留痕）
OLD = os.path.join(DATA, "二店226品品牌分组_1023.csv")

ALNUM = re.compile(r"[0-9A-Za-z]")


def find_in_title(lh, title):
    """料号是否作为独立标识出现在标题里（防姐妹型号串：W3S ≠ W3S-1939-P012）。"""
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


def main():
    if not os.path.isfile(ENUM):
        print(json.dumps({"ok": 0, "error": "没有 onsale_enum.json——先跑全量枚举"},
                         ensure_ascii=False))
        return 1
    blob = json.load(io.open(ENUM, encoding="utf-8"))
    meta = blob.get("meta") or {}
    inv = blob.get("items") or []
    complete = bool(meta.get("complete"))

    rows = list(csv.DictReader(io.open(SRC, encoding="utf-8-sig")))
    old = {}
    if os.path.isfile(OLD):
        for x in csv.DictReader(io.open(OLD, encoding="utf-8-sig")):
            old[(x.get("料号") or "").strip()] = x
    print("[输入] %s：%d 个料号 / 三店在售 %d 件（声明 %s / complete=%s / 店铺=%s）"
          % (os.path.basename(SRC), len(rows), len(inv), meta.get("declared"),
             complete, meta.get("shop") or "?"))

    out, said = [], []
    for r in rows:
        lh = (r.get("料号") or "").strip()
        o = {"料号": lh, "品牌": (r.get("品牌") or "").strip(),
             "三店状态": "", "商品id": "", "双百": "", "结论": "", "备注": ""}
        notes = []

        if not complete:
            o["三店状态"] = "待人工"
            o["结论"] = "待人工"
            notes.append("三店在售清单没抓全（%s / %s 件）——不许据此判「没有」"
                         % (meta.get("count"), meta.get("declared")))
        else:
            hits = [x for x in inv if find_in_title(lh, x.get("title"))]
            if hits:
                h = hits[0]
                o["三店状态"] = "已有商品"
                o["商品id"] = h.get("id") or ""
                dual = (h.get("label") == "流量加速中") or \
                       (h.get("basic") == 100 and _spe(h) == 100)
                o["双百"] = "双百" if dual else "非双百"
                bits = []
                if h.get("basic") is not None:
                    bits.append("基础%g" % h["basic"])
                if _spe(h) is not None:
                    bits.append("扶优%g" % _spe(h))
                if h.get("label"):
                    bits.append(h["label"])
                notes.append("质量分：" + (" / ".join(bits) or "（无分项）"))
                if len(hits) > 1:
                    notes.append("共 %d 条命中（取第一条）" % len(hits))
                o["结论"] = "已有跳过"
            else:
                o["三店状态"] = "查无此品"
                o["双百"] = "—"
                o["结论"] = "可上"
                near = [x.get("title") or "" for x in inv
                        if re.search(r"%s[-A-Za-z0-9]+" % re.escape(lh),
                                     x.get("title") or "", re.I)]
                if near:
                    notes.append("⚠ 店内有姐妹型号（不是本料号）：%s" % near[0][:44])
        if lh in old:
            notes.append("二店 1023 表记「%s」（二店口径，不是三店）"
                         % (old[lh].get("上架状态") or "?"))
            said.append(lh)
        o["备注"] = "；".join(notes)
        out.append(o)

    order = {"已有跳过": 0, "可上": 1, "待人工": 2}
    out.sort(key=lambda x: (order.get(x["结论"], 9), x["料号"]))

    HEAD = ["料号", "品牌", "三店状态", "商品id", "双百", "结论", "备注"]
    with io.open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEAD, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    cnt = collections.Counter(x["结论"] for x in out)
    dual_cnt = collections.Counter(x["双百"] for x in out if x["结论"] == "已有跳过")
    print("[写出] %s  %d 行" % (OUT, len(out)))
    for k in ("已有跳过", "可上", "待人工"):
        print("   %-8s %3d" % (k, cnt.get(k, 0)))
    if dual_cnt:
        print("   已有里双百分布：%s" % dict(dual_cnt))
    have = [x["料号"] for x in out if x["结论"] == "已有跳过"]
    if have:
        print("   已有：%s" % " ".join(have))
    print(json.dumps({"ok": 1, "out": OUT, "total": len(out), "counts": dict(cnt),
                      "dual": dict(dual_cnt), "onsale": len(inv), "complete": complete,
                      "shop": meta.get("shop"), "declared": meta.get("declared"),
                      "reused": len(said)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
