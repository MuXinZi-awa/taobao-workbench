# -*- coding: utf-8 -*-
"""刷新容量（快版）—— 纯 HTTP，不启动浏览器，约 1.5 秒。

对比：
  原 refresh_caps.py   persistent_context + 串行    ~35 秒，且因登录态带不出而静默失败
  本版                 state cookie + 并发 24         ~1.5 秒（27 个计划）

原理：
  ① 从 _工程_workbench\\state\\<店铺>.json 读 cookie（这就是「已经获得的登录态」）
  ② 直接用 HTTP 调 one.alimama.com 的两个接口
  ③ 27 个计划的 count 用线程池并发拉
  ④ 结果写 state_db（按 member_id 分档）

使用:
    py refresh_caps_fast.py                 # 当前激活店铺
    py refresh_caps_fast.py --shop 保险丝子账号2
    py refresh_caps_fast.py --refresh-csrf  # csrf 过期时，起一次浏览器重抓（慢，很少用）
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

WS = r'C:\Users\jdt-pty\Desktop\OH-WorkSpace'
sys.path.insert(0, r'C:\Users\jdt-pty\Desktop\推广一键跑')
sys.path.insert(0, os.path.join(WS, '工具', 'workbench'))

STATE_DIR = os.path.join(WS, '_工程_workbench', 'state')
TG = r'C:\Users\jdt-pty\Desktop\推广一键跑'
RUNTIME = os.path.join(TG, 'runtime')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
BASE = 'https://one.alimama.com'
WORKERS = 24

LIST_URL = BASE + '/campaign/findList.json?csrfId=%s&bizCode=onebpSearch'
CNT_URL = BASE + '/adgroup/horizontal/findPage.json?csrfId=%s&bizCode=onebpSearch'
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ══════════ cookie / csrf ══════════
def export_state_from_profile(shop, state_fp):
    """state 不存在时，从 profile 导一份（headless 也能导出，实测 91 个 cookie 正常）。

    ！注意：持久化上下文本身带不出登录态给页面用，
    但ctx.storage_state() 能把 cookie 写出来，写出来的文件再注入就能用。
    """
    import conn_store
    rows = [c for c in conn_store.list_all() if c.get('name') == shop]
    prof = ''
    if rows:
        prof = (rows[0].get('profile') or '').strip()
    if prof and not os.path.isabs(prof):
        prof = os.path.join(TG, prof)
    if not prof or not os.path.isdir(prof):
        cand = os.path.join(TG, 'chrome_profile_' + shop)
        prof = cand if os.path.isdir(cand) else os.path.join(TG, 'chrome_profile')
    if not os.path.isdir(prof):
        raise FileNotFoundError('找不到 profile：%s' % prof)

    from playwright.sync_api import sync_playwright
    os.makedirs(STATE_DIR, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            prof, executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            headless=True, viewport={'width': 1200, 'height': 800}, locale='zh-CN',
            args=['--disable-blink-features=AutomationControlled'])
        ctx.storage_state(path=state_fp)
        ctx.close()
    return state_fp


def load_cookie(shop):
    fp = os.path.join(STATE_DIR, shop + '.json')
    if not os.path.isfile(fp):
        # 兜底：从 profile 导一份（比直接报错友好，也兼容新店铺第一次用）
        try:
            export_state_from_profile(shop, fp)
        except Exception as e:
            raise FileNotFoundError(
                '没有 state 文件且自动导出失败（%s）：%s\n'
                '先跑 qn_session.py export --shop %s' % (str(e)[:60], fp, shop))
    st = json.load(open(fp, encoding='utf-8'))
    jar = {}
    for c in st.get('cookies') or []:
        d = c.get('domain') or ''
        if 'alimama' in d or 'taobao' in d:
            jar[c['name']] = c['value']
    if not jar:
        raise ValueError('state 里没有 alimama/taobao cookie')
    return '; '.join('%s=%s' % (k, v) for k, v in jar.items()), jar


def csrf_fp(mid):
    return os.path.join(RUNTIME, 'csrf_%s.txt' % (mid or 'default'))


def load_csrf(mid):
    fp = csrf_fp(mid)
    if os.path.isfile(fp):
        return open(fp, encoding='utf-8').read().strip()
    return ''


def save_csrf(mid, v):
    try:
        os.makedirs(RUNTIME, exist_ok=True)
        open(csrf_fp(mid), 'w', encoding='utf-8').write(v)
    except Exception:
        pass


def fetch_csrf_by_browser(shop, state_fp):
    """csrf 过期时的兜底：起一次 headless 浏览器抓（约 12 秒，很少用）。"""
    from playwright.sync_api import sync_playwright
    import re as _re
    found = {'v': ''}
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(storage_state=state_fp, viewport={'width': 1440, 'height': 900},
                            user_agent=UA)
        page = ctx.new_page()

        def on_req(r):
            m = _re.search(r'csrfId=([0-9a-f_]+)', r.url)
            if m and not found['v']:
                found['v'] = m.group(1)

        page.on('request', on_req)
        page.goto(BASE + '/index.html#!/manage/campaign',
                  timeout=60000, wait_until='domcontentloaded')
        page.wait_for_timeout(9000)
        ctx.close()
        b.close()
    return found['v']


# ══════════ HTTP ══════════
def post(url, body, cookie, timeout=15):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method='POST',
        headers={'Content-Type': 'application/json',
                 'x-requested-with': 'XMLHttpRequest',
                 'User-Agent': UA, 'Cookie': cookie,
                 'Referer': BASE + '/index.html',
                 'Origin': BASE})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')[:400]
    except Exception as e:
        return 0, str(e)[:200]


def _is_csrf_err(code, txt):
    return code == 403 or 'csrf' in txt.lower() or '2102001' in txt


def list_campaigns(cookie, csrf):
    c, t = post(LIST_URL % csrf,
                {'statusList': ['start', 'pause'], 'pageSource': 'simplifyQueryList',
                 'bizCode': 'onebpSearch', 'csrfId': csrf}, cookie)
    if c != 200:
        return None, (c, t)
    try:
        j = json.loads(t)
        return ((j.get('data') or {}).get('list') or []), None
    except Exception as e:
        return None, ('parse', str(e))


def one_count(cookie, csrf, cid):
    c, t = post(CNT_URL % csrf,
                {'bizCode': 'onebpSearch', 'campaignIdList': [cid],
                 'offset': 0, 'pageSize': 1, 'csrfId': csrf}, cookie)
    if c != 200:
        return cid, None
    try:
        return cid, ((json.loads(t).get('data') or {}).get('count'))
    except Exception:
        return cid, None


# ══════════ 主流程 ══════════
def run(shop=None, refresh_csrf=False, quiet=False):
    t_all = time.time()
    # 1) 账号
    import conn_store
    ctx = conn_store.acct_context()
    if not ctx['has_conn']:
        return {'ok': False, 'err': '没有激活的店铺连接'}
    if shop and shop != ctx['name']:
        # 指定了别的店：临时查它的连接
        rows = [c for c in conn_store.list_all() if c['name'] == shop]
        if not rows:
            return {'ok': False, 'err': '找不到连接：%s' % shop}
        row = rows[0]
        name, mid = row['name'], str(row['member_id'] or '')
        shop = name
    else:
        name, mid = ctx['name'], ctx['member_id']
        shop = name

    if not mid:
        return {'ok': False,
                'err': '「%s」没绑 member_id。登录万相台看右上角「ID：xxxx」，'
                       '填到 设置→连接 里。' % name}

    if not quiet:
        print('账号     :', name)
        print('member_id:', mid)
        print('state    :', shop + '.json')

    # 2) cookie
    try:
        cookie, jar = load_cookie(shop)
    except Exception as e:
        return {'ok': False, 'err': str(e)}
    if 'cookie2' not in jar:
        return {'ok': False, 'err': 'state 里缺 cookie2，登录态不完整，重新导出 state'}

    # 3) csrf
    csrf = load_csrf(mid)
    state_fp = os.path.join(STATE_DIR, shop + '.json')
    if refresh_csrf or not csrf:
        if not quiet:
            print('csrf     : 没有缓存，起浏览器抓一次（约 12 秒）…')
        csrf = fetch_csrf_by_browser(shop, state_fp)
        if csrf:
            save_csrf(mid, csrf)
    if not csrf:
        return {'ok': False, 'err': 'csrfId 抓不到（可能无万相台权限）'}
    if not quiet:
        print('csrf     :', csrf[:20] + '…')

    # 4) 拉计划
    t0 = time.time()
    plist, err = list_campaigns(cookie, csrf)
    if plist is None and err and _is_csrf_err(err[0], err[1]):
        if not quiet:
            print('csrf 失效，重抓…')
        csrf = fetch_csrf_by_browser(shop, state_fp)
        if csrf:
            save_csrf(mid, csrf)
            plist, err = list_campaigns(cookie, csrf)
    if plist is None:
        return {'ok': False, 'err': '拉计划失败：%s' % str(err)[:150]}
    t_list = (time.time() - t0) * 1000
    if not quiet:
        print('计划数   : %d  （%.0f ms）' % (len(plist), t_list))

    # 5) 并发查容量
    ids = [str(x.get('campaignId')) for x in plist if x.get('campaignId')]
    names = {str(x.get('campaignId')): (x.get('campaignName') or '') for x in plist}
    t1 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        res = list(ex.map(lambda c: one_count(cookie, csrf, c), ids))
    t_cnt = (time.time() - t1) * 1000
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = {}
    okn = 0
    for cid, cnt in res:
        if cnt is None:
            continue
        okn += 1
        rows[cid] = {'name': names.get(cid, ''), 'count': int(cnt),
                     'capacity': 500, 'time': now}
    if not quiet:
        print('容量     : %d/%d 成功（%.0f ms，并发 %d）' % (okn, len(ids), t_cnt, WORKERS))

    # 6) 写库
    try:
        import state_db
        state_db.set_plans(mid, rows)
        if not quiet:
            print('已写入   : 账号 %s 的 %d 个计划' % (mid, len(rows)))
    except Exception as e:
        return {'ok': False, 'err': '写状态库失败：%s' % str(e)[:120]}

    total_ms = (time.time() - t_all) * 1000
    if not quiet:
        print()
        print('总耗时   : %.0f ms' % total_ms)
        print('合计单元 : %d' % sum(r['count'] for r in rows.values()))
    return {'ok': True, 'mid': mid, 'shop': name, 'plans': len(rows),
            'units': sum(r['count'] for r in rows.values()),
            'ms': int(total_ms), 'rows': rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shop', default=None)
    ap.add_argument('--refresh-csrf', action='store_true')
    a = ap.parse_args()
    r = run(a.shop, a.refresh_csrf)
    if not r.get('ok'):
        print('✗', r.get('err'))
        sys.exit(1)


if __name__ == '__main__':
    main()
