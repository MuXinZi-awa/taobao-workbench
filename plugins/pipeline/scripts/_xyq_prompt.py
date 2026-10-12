# -*- coding: utf-8 -*-
"""生图提示词模板：{字段} → 值

为什么要模板：版面（构图/风格/排版）是所有产品共用的，会变的只有品牌、料号、
类别这些字段。写死一份提示词＝每换一个产品就得手改一遍。

字段来源（优先级从高到低）：
  1) 面板/调用方直接给的 fields
  2) 属性清单.csv（fetch_attrs 爬的：品牌/接口类型/类别/额定电流/适用场景…）
  3) 空着——并在结果里明确写「缺：xxx」，不静默

用法：
    from _xyq_prompt import render, fields_of
    f = fields_of("0462-201-16141")          # 自动补齐字段
    f["标题"] = "TE CONNECTORS"              # 手动覆盖
    prompt = render(open("tpl.md", encoding="utf-8").read(), f)
"""
import os
import io
import csv
import re
import json

TAG_RE = re.compile(r"\{([^{}]+)\}")

ATTR_CSV = r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\办公室工作\数据\属性清单.csv"
POINTS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "prompt_templates", "品类要点.json")


def _load_points():
    """品类 → “哪些必须一致”那一段（连接器/芯片/保险丝/服装…各自不同）"""
    try:
        with io.open(POINTS_JSON, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"_默认": "外形轮廓、表面字符与标识、结构细节、材质与颜色，必须与参考图完全一致。"}

# 属性清单列名 → 模板占位符
COL2FIELD = {
    "品牌": "品牌",
    "类别": "品类",
    "接口类型": "接口类型",
    "额定电流": "额定电流",
    "电源电压": "电压",
    "认证标准": "认证",
    "适用场景": "场景",
    "接触件材质": "材质",
}

# 品牌中文名 → 主图上常用的英文写法（没有就原样）
BRAND_EN = {
    "泰科": "TE CONNECTORS",
    "te": "TE CONNECTORS",
    "deutsch": "DEUTSCH",
    "矢崎": "YAZAKI",
    "莫仕": "MOLEX",
    "安费诺": "AMPHENOL",
    "德尔福": "DELPHI",
    "住友": "SUMITOMO",
}


def _read_attr(lh):
    """从属性清单取一行（按料号列匹配）"""
    if not os.path.isfile(ATTR_CSV):
        return {}
    try:
        with io.open(ATTR_CSV, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("料号") or "").strip() == lh:
                    return row
    except Exception:
        pass
    return {}


def fields_of(lh, extra=None):
    """给一个料号 → 模板字段表（属性清单能填的自动填）"""
    row = _read_attr(lh)
    f = {"料号": lh}
    for col, key in COL2FIELD.items():
        v = (row.get(col) or "").strip()
        if v:
            f[key] = v
    # 属性清单的「类别」常是空的，而「接口类型」往往写着品类本身（如“连接器”）——
    # 品类空了，提示词里就会变成“把参考图里的【（此处留空）】抠出”，模型直接懵。
    if not f.get("品类"):
        f["品类"] = f.get("接口类型") or ""
    # 调用方给的字段先合并——后面的派生字段（标题/规格/要点）必须按**最终**品类算，
    # 否则 override 了品类、要点却还是清单里那个（刚踩的）。
    if extra:
        f.update(extra)
    b = f.get("品牌", "")
    f["品牌英文"] = BRAND_EN.get(b.lower(), BRAND_EN.get(b, b))
    f["标题"] = f["品牌英文"] or f.get("品类", "") or lh
    bits = [x for x in (f.get("品类"), f.get("额定电流"), f.get("电压"),
                        f.get("接口类型"), f.get("场景")) if x]
    f["规格行"] = " / ".join(bits[:4])
    f["尺寸"] = "3:4 竖图"
    pts = _load_points()
    cat = f.get("品类", "")
    hit = None
    # 注意：cat 为空时绝不能用 `cat in k`——空串在任意字符串里都成立，会错配第一个品类
    if cat:
        for k in pts:
            if k.startswith("_") or not k:
                continue
            if k in cat or cat in k:
                hit = k
                break
    f["品类要点"] = pts.get(hit) or pts.get("_默认", "")
    return f


def render(tpl, fields, mark_missing=True):
    """把 {字段} 换成值。没值的字段：留空，并在末尾列出「缺」
    —— 宁可让提示词里缺一块，也不能让模型自己编一个品牌上去。"""
    missing = []

    def _sub(m):
        k = m.group(1).strip()
        v = fields.get(k, "")
        if v in (None, ""):
            missing.append(k)
            return "" if not mark_missing else "（此处留空）"
        return str(v)

    out = TAG_RE.sub(_sub, tpl)
    if missing:
        out += "\n\n【注意】以下字段无数据、已在正文留空，不得自行编造：" + "、".join(sorted(set(missing)))
    return out
