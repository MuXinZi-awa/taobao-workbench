# -*- coding: utf-8 -*-
"""AI 素材生成调度：小云雀(生成 封面/白底图/详情图)
流程：上传参考图 → submit_run（提示词保丝印）→ 轮询 get_thread → 下载 → 按料号落盘
用法：python ai_schedule.py 料号 参考图.jpg 用途 [--out 目录]
      用途: cover / white / detail
依赖：Clash 7897 代理 + XYQ_ACCESS_KEY（读工作区 xyq_key.txt）
"""
import sys
import os
import json
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 找 xyq key
KEY = ""
for cand in [r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\xyq_key.txt",
             r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\工具\xyq_key.txt",
             r"C:\Users\jdt-pty\Desktop\推广一键跑\xyq_key.txt"]:
    if os.path.isfile(cand):
        KEY = open(cand).read().strip()
        break

XYQ_BASE = "https://xyq.jianying.com"
PROXY = "http://127.0.0.1:7897"

PROMPTS = {
    "cover": "构图方式已确定：若参考图含多个产品，仅保留其中一个主体居中，其他视为背景去除。直接按此生成，无需询问或确认构图；把这张参考图的电子连接器产品图制作成淘宝主图白底底图：必须严格照搬参考图中产品的实际结构（插针数量/排列、壳体造型、颜色材质完全一致），禁止自行设计、添加或改变任何结构细节；注意：若参考图为塑料胶壳/壳体（表面是孔格或空腔、无金属端子），输出必须保持同样为空心壳体，严禁自行添加金属针脚、端子、插针等任何金属件（本产品胶壳与端子是分开销售的）；参考图若含多个产品或杂物水印，仅锁定并保留其中与产品主体一致的部分，其余视为背景去除；产品居中、纯白色背景 RGB(255,255,255)、产品外观、结构、颜色、材质必须与参考图完全一致（插针数量/排列、壳体造型等不得改变或增删）。参考图表面文字丝印若无法精确还原可省略不画、保持表面干净，但严禁编造或修改任何文字数字，构图简洁突出产品，带柔和阴影，无任何文字无水印。曝光自然：避免过度曝光/高光过曝（不要闪光灯式亮白高光），金属质感层次清晰。",
    "white": "把这张参考图的电子连接器产品图制作成淘宝白底图：1:1 正方形，纯白色背景 RGB(255,255,255)，只含一个产品主体（参考图若有两个或以上产品，只保留输出其中一个；严禁重复复制产品），产品外观、结构、比例必须与参考图完全一致，主体完整清晰居中，带柔和阴影，不裁切产品边缘。若参考图存在任何水印、品牌标识、商家文字或水印残影（包括产品表面上的浅色水印字），必须彻底清除干净，不留任何痕迹残影。严禁编造或修改任何文字数字；整体无任何文字、无水印、无装饰元素。曝光自然：避免过度曝光/高光过曝（不要闪光灯式亮白高光），金属质感层次清晰。",
    "main2": "参考这张图，生成同一产品的淘宝主图：单只端子居中放置，纯白背景，产品外观结构颜色与参考图完全一致（插针数量/排列不得改变）；表面文字丝印无法精确还原时可省略，严禁编造任何文字数字，构图简洁，1:1。曝光自然不过曝。",
    "main3": "参考这张图，生成同一产品的淘宝主图：单只端子 45 度斜放展示，纯白背景，产品外观结构颜色与参考图完全一致（插针数量/排列不得改变）；表面文字丝印无法精确还原时可省略，严禁编造任何文字数字，1:1。曝光自然不过曝。",
    "main4": "参考这张图，生成同一产品的淘宝主图：两只端子并排展示，纯白背景，产品外观结构颜色与参考图完全一致（插针数量/排列不得改变）；表面文字丝印无法精确还原时可省略，严禁编造任何文字数字，1:1。曝光自然不过曝。",
    "main5": "参考这张图，生成同一产品的淘宝主图：细节特写（端子口/卡榫结构），纯白背景，产品外观结构颜色与参考图完全一致（插针数量/排列不得改变）；表面文字丝印无法精确还原时可省略，严禁编造任何文字数字，1:1。曝光自然不过曝。",
    "clean": "清理这张参考图：移除图上所有文字、水印、Logo、标尺刻度、网格线、标签、测量标注等非产品元素，保留产品本身的原貌；保持产品原有数量、角度、构图与结构完全不变（若多个产品保持多个，不得删减、重排或选取），背景替换为纯白色 RGB(255,255,255)；严禁自行添加、改变或重新设计产品的任何结构细节（插针数量/壳体/端子形状照搬原图）。",
    "detail": "基于这张参考图的电子连接器产品，制作一张详情图：白底，产品主体大图居中，周围可加简洁参数标注（品牌、型号、材质、规格），产品外观结构颜色与参考图完全一致；表面文字丝印无法精确还原时可省略，严禁编造任何文字数字；参数标注只可用确知信息，风格干净专业。曝光自然不过曝。",
}

# 全套生成顺序（封面→白底→主图2-5）
ALL_USAGES = ["cover", "clean", "main2", "main3", "main4", "main5"]  # white 去掉——白底图用淘宝"从主图生成"；clean=送修去标注保留原样


def _opener():
    proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    return urllib.request.build_opener(proxy)


def api(url, body=None, timeout=120):
    """请求 xyq API（走代理）——自动解包 ret/data"""
    headers = {"Authorization": "Bearer %s" % KEY, "Content-Type": "application/json"}
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body else "GET")
    with _opener().open(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    if resp.get("ret") != "0":
        raise RuntimeError("API 错误 %s: %s" % (resp.get("ret"), resp.get("errmsg")))
    return resp.get("data", {})


def upload_asset(img_path):
    """上传参考图 → asset_id"""
    import mimetypes
    boundary = "----xyq%s" % int(time.time())
    fn = os.path.basename(img_path)
    mime = mimetypes.guess_type(fn)[0] or "image/jpeg"
    with open(img_path, "rb") as f:
        img = f.read()
    parts = []
    parts.append(b"--" + boundary.encode())
    parts.append(b'Content-Disposition: form-data; name="file"; filename="' + fn.encode("utf-8") + b'"')
    parts.append(b"Content-Type: " + mime.encode())
    parts.append(b"")
    parts.append(img)
    parts.append(b"--" + boundary.encode() + b"--\r\n")
    body = b"\r\n".join(parts)
    req = urllib.request.Request(
        XYQ_BASE + "/api/biz/v1/skill/upload_file", data=body,
        headers={"Authorization": "Bearer %s" % KEY, "Content-Type": "multipart/form-data; boundary=%s" % boundary})
    with _opener().open(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def submit(message, asset_ids, model="seedream_4.5"):
    """0903：指定 seedream_4.5（不耗积分——5.0 pro 留给棠溪雾/贵活）——submit_run body 带 model"""
    d = api(XYQ_BASE + "/api/biz/v1/skill/submit_run", {"message": message, "asset_ids": asset_ids, "model": model})
    run = d.get("run", {})
    return run.get("thread_id", ""), run.get("run_id", "")


def poll(thread_id, run_id, timeout=600):
    """轮询直到完成，返回产物 URL 列表"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            d = api(XYQ_BASE + "/api/biz/v1/skill/get_thread", {"thread_id": thread_id, "run_id": run_id}, timeout=60)
            th = d.get("thread", {})
            print("[poll] thread keys:", list(th.keys()) if isinstance(th, dict) else type(th))
            print("[poll] thread前400:", json.dumps(th, ensure_ascii=False)[:400])
            runs = th.get("run_list", []) if isinstance(th, dict) else []
            if not runs:
                # 尝试 data/result 嵌套
                for k in ("data", "result"):
                    v = d.get(k)
                    if isinstance(v, dict) and "run_list" in v:
                        runs = v["run_list"]
                        break
            if not runs:
                print("[poll] 无 run_list，响应前300:", json.dumps(d, ensure_ascii=False)[:300])
            for r in runs:
                st = r.get("state", -1)
                if st == 3:
                    entries = r.get("entry_list") or []
                    print("[poll] 完成！entry_list 数:", len(entries))
                    for i, entry in enumerate(entries):
                        msg = entry.get("message") or {}
                        print("[poll] entry%d msg role=%s content=%s" % (i, msg.get("role"), json.dumps(msg.get("content"), ensure_ascii=False)[:300]))
                    # 产物在 entry.artifact.content[].url 或 data(JSON) 的 image.url
                    # 只取 artifact（产物）——message.content 里的 URL 是参考图引用（上次 100+ 白底图任务的坑：参考图被当产物下载）
                    urls = []
                    for entry in entries:
                        artifact = entry.get("artifact") or {}
                        for c in artifact.get("content") or []:
                            u = c.get("url") or c.get("file_url") or c.get("src")
                            if not u and isinstance(c.get("data"), str):
                                try:
                                    d2 = json.loads(c["data"])
                                    u = (d2.get("image") or {}).get("url") or d2.get("url")
                                except Exception:
                                    pass
                            if u and u not in urls:
                                urls.append(u)
                    # 兜底：artifact 为空时才取最后一条助手消息的 URL（不取用户消息——参考图都在用户消息里）
                    if not urls:
                        for entry in reversed(entries):
                            msg = entry.get("message") or {}
                            if msg.get("role") in ("assistant", "AI", ""):
                                for c in msg.get("content") or []:
                                    u = c.get("url") or c.get("file_url")
                                    if u and u not in urls:
                                        urls.append(u)
                    if not urls:
                        print("[poll] 未提取到 URL——完整响应:", json.dumps(r, ensure_ascii=False)[:600])
                    return urls
                if st in (4, 5):
                    print("任务失败/取消 state=%s %s" % (st, r.get("fail_reason", "")))
                    return []
        except Exception as e:
            print("轮询异常: %s" % str(e)[:60])
        time.sleep(20)
    return []


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _opener().open(req, timeout=120) as r:
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def generate(lh, img, usage, out):
    """生成单个素材（封面/白底/详情）——返回产物文件路径列表（可编程调用）"""
    if not KEY or not os.path.isfile(img):
        print("缺少 KEY 或参考图")
        return []
    os.makedirs(out, exist_ok=True)
    print("[AI] 上传参考图 %s ..." % os.path.basename(img))
    up = upload_asset(img)
    print("[AI] 上传响应:", json.dumps(up, ensure_ascii=False)[:600])
    d = up.get("data", {}) if isinstance(up, dict) else {}
    # 关键：submit 要用 pippit_asset_id（use_this_in_submit_run）——不是 asset_id！
    asset = d.get("pippit_asset_id", "") or d.get("use_this_in_submit_run", "") or d.get("asset_id", "")
    print("[AI] 参考图 asset(pippit):", asset)
    if not asset:
        print("上传失败:", json.dumps(up, ensure_ascii=False)[:200])
        return []
    print("[AI] 提交任务（%s）..." % usage)
    tid, rid = submit(PROMPTS[usage], [asset])
    print("[AI] thread=%s run=%s，轮询中（约1-3分钟）..." % (tid[:12], rid[:12]))
    urls = poll(tid, rid)
    if not urls:
        print("❌ 未拿到产物")
        return []
    names = {"cover": "封面", "clean": "清理图", "white": "白底图", "detail": "详情图",
             "main2": "主图2", "main3": "主图3", "main4": "主图4", "main5": "主图5"}
    saved = []
    for i, u in enumerate(urls, 1):
        dest = os.path.join(out, "%s_%s%s.png" % (lh, names[usage], "_%d" % i if len(urls) > 1 else ""))
        tmp = dest + ".tmp"
        try:
            n = download(u, tmp)
            if n < 5000:
                print("  ✗ 下载过小(%dB)——非正常产物，删除" % n)
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                continue
            # 基本门槛：能打开 + 尺寸正常（防 404/非图片/缩略图）——参考图混入由 poll 只取 artifact 挡住
            # 只检测不处理：绝不本地 flood fill（0826 教训——浅灰背景和浅色金属边缘灰度接近，处理会吃掉产品细节）
            # 非纯白的落盘标"待复核"（原样保留——人工/重跑，不碰像素）
            try:
                import make_white as _mw
                from PIL import Image as _PIL
                _w, _h = _PIL.open(tmp).size
                if max(_w, _h) < 400:
                    print("  ✗ 尺寸过小 %dx%d（缩略图/异常）——删除" % (_w, _h))
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                    continue
                okw, _ = _mw.check_pure_white(tmp)   # 只查角点纯白——不调用 make_pure_white
                subj = _mw.count_subjects(tmp)
                okp = okw and subj == 1
            except Exception as e:
                print("  ✗ 图片异常（非有效图）: %s" % str(e)[:60])
                okp = False
            if not okp:
                # 不删不重跑：落盘待复核（保留原始 AI 产物——人工看一眼）
                dest2 = dest.replace(".png", "_待复核.png")
                os.rename(tmp, dest2)
                saved.append(dest2)
                print("  △ %s 已落盘待复核（背景非纯白/多主体——原样保留，不本地处理）" % os.path.basename(dest2))
                continue
            os.rename(tmp, dest)
            saved.append(dest)
            print("  ✓ %s (%dKB) 纯白单主体验证通过" % (os.path.basename(dest), n // 1024))
        except Exception as e:
            print("  ✗ 下载失败: %s" % str(e)[:60])
            try:
                os.remove(tmp)
            except Exception:
                pass
    return saved


def generate_all(lh, img, out):
    """一键全套：封面(AI白底+标注)/白底图/主图2-5（AI 图生图）——详情图用 HTML/PIL 渲染（AI 生字不准）"""
    import subprocess
    saved = []
    img_real = os.path.abspath(img) if img else ""
    # 生成前清理旧 AI 产物（防堆积——只留本轮定稿；跳过参考图本身——它可能在 out 目录）
    for f in os.listdir(out):
        if f.endswith(("_封面.png", "_封面_标注.png", "_白底图.png", "_主图2.png", "_主图3.png", "_主图4.png", "_主图5.png", "_详情图.png")):
            try:
                if os.path.abspath(os.path.join(out, f)) != img_real:
                    os.remove(os.path.join(out, f))
            except Exception:
                pass
    for usage in ALL_USAGES:
        print("\n[AI] 生成 %s ..." % usage)
        files = generate(lh, img, usage, out)
        saved.extend(files)
    # 白底图：发布页"从主图生成"（官方——审核友好）——AI 不再生成/验证白底图
    # （删除旧白底验证+纯白化+主体检测+重出逻辑——本地白底图已不需要）
    # 封面标注（make_cover.py——PIL 加型号/规格条）
    # 0828：品牌不再写封面（品牌错牵连封面——封面只放型号规格）
    cover_src = os.path.join(out, "%s_封面.png" % lh)
    if os.path.isfile(cover_src):
        try:
            import subprocess
            tagged = os.path.join(out, "%s_封面_标注.png" % lh)
            subprocess.run([sys.executable, os.path.join(BASE_DIR, "..", "办公室工作", "推广工具", "make_cover.py"),
                            "--base", cover_src, "--out", tagged,
                            "--model", lh, "--brand", "", "--spec", "", "--sub", ""],
                           timeout=60, capture_output=True)
            if os.path.isfile(tagged):
                print("[封面] 标注完成 ✓")
                saved.append(tagged)
        except Exception as e:
            print("[封面] 标注失败:", str(e)[:50])
    # 详情图（HTML 渲染统一版式——填爬取数据）
    try:
        detail_py = os.path.join(BASE_DIR, "detail_html.py")
        if os.path.isfile(detail_py):
            import subprocess
            subprocess.run([sys.executable, detail_py, lh, cover_src], timeout=90, capture_output=True)
            detail_file = os.path.join(out, "%s_详情图.png" % lh)
            if os.path.isfile(detail_file):
                print("[详情] HTML 渲染完成 ✓")
                saved.append(detail_file)
        # 视频合成（封面+主图+详情+拍前须知 → 轮播淡入淡出）
        try:
            mv = os.path.join(BASE_DIR, "make_video.py")
            if os.path.isfile(mv):
                subprocess.run([sys.executable, mv, lh], timeout=240, capture_output=True)
                vf = os.path.join(out, "%s_视频.mp4" % lh)
                if os.path.isfile(vf):
                    print("[视频] 合成完成 ✓")
                    saved.append(vf)
        except Exception as e:
            print("[视频] 合成失败:", str(e)[:50])
    except Exception as e:
        print("[详情] 渲染失败:", str(e)[:50])
    return saved


def main():
    lh = sys.argv[1] if len(sys.argv) > 1 else None
    img = sys.argv[2] if len(sys.argv) > 2 else None
    usage = sys.argv[3] if len(sys.argv) > 3 else "cover"
    out = sys.argv[4] if len(sys.argv) > 4 else r"C:\Users\jdt-pty\Desktop\OH-WorkSpace\产品\%s" % lh
    if not lh or not img or not os.path.isfile(img):
        print("用法: python ai_schedule.py 料号 参考图.jpg cover|white|detail|main2-5|all [输出目录]")
        return
    if not KEY:
        print("❌ 找不到 xyq_key.txt")
        return
    if usage == "all":
        generate_all(lh, img, out)
    else:
        generate(lh, img, usage, out)


if __name__ == "__main__":
    main()
