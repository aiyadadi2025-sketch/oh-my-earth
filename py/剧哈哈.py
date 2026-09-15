# -*- coding: utf-8 -*-
"""
剧哈哈 (www.juhaha.cc) - TVBox 爬虫源（纯标准库版）
================================================================================
maccms + hhplayer 系统。接口：homeContent / categoryContent / detailContent /
playerContent / searchContent，支持二级分类筛选、懒加载、按需解密播放直链、
域名跟踪（发布页动态刷新 + 故障轮换）。

本版改动：
1. 搜索修复（实测根因）：
   a) 接口签名补 pg —— 壳端按 searchContent(key, quick, pg) 三参调用，
      旧签名 searchContent(self,key,quick) 直接 TypeError，搜索整体失效；
   b) 支持翻页 /search/{kw}----------{pg}---.html，并从 <span class="num">p/n</span>
      解析真实 pagecount、"相关的 N 部影片" 解析 total；
   c) 只解析搜索结果主列（col-lg-wide-8 ~ stui-pannel-side），
      排除侧栏「猜你喜欢」混入的无关影片（原本会多出一条不相干结果）；
   d) 站点有 3 秒搜索间隔，命中「请不要频繁操作」页会自动等待后重试，
      仍被拦则用 ajax/suggest 兜底；快搜(quick)直接走 suggest，不触发限流；
   e) 空结果不缓存（原空结果缓存 180 秒导致重试也无效）
2. 二级分类：从站点解析真实子分类(/type/id.html)，筛选面板第一栏「分类」，
   categoryContent 支持 sub 参数翻页
3. 首页提速保留：限时加载 + 筛选后台异步
"""

import re
import os
import sys
import json
import time
import gzip
import base64
import socket
import threading
import concurrent.futures
import ssl
import urllib.request
import urllib.parse
import http.cookiejar

# ============================================================
# 常量
# ============================================================
PUBLISH_PAGE = "https://www.juhaha.vip"
SEED_DOMAINS = [
    "https://www.juhaha.cc",
    "https://www.juhaha.fun",
    "https://www.juhaha.vip",
]

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
      "Mobile/15E148 Safari/604.1")

TIMEOUT_PAGE = 8
TIMEOUT_API = 5
TIMEOUT_PLAY = 4

TTL_HOME = 600
TTL_CAT = 300
TTL_DETAIL_OK = 300
TTL_DETAIL_EMPTY = 30
TTL_SEARCH = 180
TTL_PLAY = 900

SEARCH_MIN_INTERVAL = 3.2  # 搜索页两次请求的最小间隔（站点要求 3 秒，主动错开）
SEARCH_GAP = 3.2           # 命中「请不要频繁操作」后的重试等待（站点要求 3 秒）

HOME_WAIT = 12  # 首页最坏等待时间（秒）

YEARS = [str(y) for y in range(2026, 2005, -1)]
SORTS = [("time", "最新"), ("hits", "最热"), ("id", "推荐")]

# 一级分类（TVBox 协议字段 type_id / type_name）
CATS = [
    {"type_id": "1", "type_name": "电影"},
    {"type_id": "2", "type_name": "剧集"},
    {"type_id": "3", "type_name": "综艺"},
    {"type_id": "4", "type_name": "动漫"},
]

# hhplayer 解密字符表
_DECODE_TABLE = 'PXhw7UT1B0a9kQDKZsjIASmOezxYG4CHo5Jyfg2b8FLpEvRr3WtVnlqMidu6cN'

# 内置 cookie（过期后更新）
BUILTIN_COOKIE = "PHPSESSID=bebqsddeg1ltvusu1v9346gbco; captcha_login_sign=082c3f93e4a59361184f6a122f52071d-d8dd7bc2959620a3efd9ee631b85ad6eb577aa6820a569317020f69a638dd167-1789392039"

_COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'juhaha_cookie.txt')


def hh_decode(s):
    """解密 hhplayer api.php 返回的播放地址。"""
    if not s:
        return ''
    try:
        raw = base64.b64decode(s, validate=False)
    except Exception:
        return ''
    out = []
    for i in range(1, len(raw), 3):
        ch = raw[i]
        try:
            idx = _DECODE_TABLE.find(chr(ch))
        except Exception:
            idx = -1
        if idx == -1:
            out.append(chr(ch))
        else:
            out.append(_DECODE_TABLE[(idx + 59) % 62])
    return ''.join(out)


try:
    import ddddocr
except Exception:
    ddddocr = None


# ============================================================
# Spider 主类
# ============================================================
class Spider(object):

    siteUrl = SEED_DOMAINS[0]

    headers = {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
    }

    def __init__(self):
        self._lock = threading.Lock()
        self._cj = http.cookiejar.CookieJar()
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self._cj),
            urllib.request.HTTPSHandler(context=self._ctx),
            urllib.request.HTTPRedirectHandler())

        # 域名跟踪
        self._domains = list(SEED_DOMAINS)
        self._domain = None
        self._active_idx = 0
        self._domains_refreshed = False

        # 验证码状态
        self._unlocked = False
        self._unlocked_domain = ''
        self._ocr = None
        self._unlocking_lock = threading.Lock()

        # 缓存
        self._home_cache = []
        self._home_cache_time = 0
        self._cat_cache = {}
        self._detail_cache = {}
        self._search_cache = {}
        self._play_cache = {}
        self._prefetching = set()
        self._search_last_req = 0.0  # 上次搜索请求时间戳（限流节流用）

        # 二级分类/筛选
        self._sub_classes = {}   # tid -> [类型名]
        self._sub_areas = {}     # tid -> [地区名]
        self._sub_cats = {}      # tid -> [{'v':子分类id,'n':名称}]（站点真实子分类）
        self._filters = {}
        self._filter_init = False
        self._site_subcats_loaded = False

    def init(self, extend=""):
        self.extend = extend or ""

    # ============================================================
    # 网络核心（urllib 标准库）
    # ============================================================
    def _request(self, url, data=None, referer='', timeout=TIMEOUT_PAGE, raw=False):
        hdrs = dict(self.headers)
        if referer:
            hdrs['Referer'] = referer
        req = urllib.request.Request(url, headers=hdrs)
        if data is not None:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                         headers=hdrs, method='POST')
        try:
            resp = self._opener.open(req, timeout=timeout)
            content = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                return None
            content = e.read()
            resp = e
        except Exception:
            return None
        enc = resp.headers.get('Content-Encoding', '') if resp else ''
        if enc and 'gzip' in enc:
            try:
                content = gzip.decompress(content)
            except Exception:
                pass
        if raw:
            return content
        try:
            return content.decode('utf-8', 'ignore')
        except Exception:
            return content.decode('gbk', 'ignore')

    def _get_text(self, url, referer='', timeout=TIMEOUT_PAGE):
        return self._request(url, referer=referer, timeout=timeout) or ""

    def _post_text(self, url, data=None, referer='', timeout=TIMEOUT_API):
        return self._request(url, data=data or {}, referer=referer, timeout=timeout) or ""

    # ============================================================
    # 域名跟踪（发布页动态刷新 + 故障轮换）
    # ============================================================
    def _refresh_domains(self):
        try:
            self._try_cookie_unlock(PUBLISH_PAGE)
            text = self._request(PUBLISH_PAGE, timeout=TIMEOUT_API) or ''
            if '系统安全验证' not in text[:300] and text:
                doms = re.findall(
                    r'https?://(?:www\.)?juhaha\.(?:cc|fun|me|pro|vip)', text)
                doms = sorted(set(doms))
                if doms:
                    with self._lock:
                        self._domains = doms
                        self._domains_refreshed = True
                    return True
        except Exception:
            pass
        return False

    def _get_domain(self):
        with self._lock:
            if self._domain is not None:
                return self._domain
        self._refresh_domains()
        n = len(self._domains)
        for _ in range(n):
            dom = self._domains[self._active_idx % n]
            self._active_idx += 1
            if self._unlock(dom):
                with self._lock:
                    self._domain = dom
                return dom
        with self._lock:
            self._domain = self._domains[0]
        return self._domain

    def _abs(self, u):
        u = (u or '').strip()
        if not u:
            return ''
        if u.startswith('//'):
            return 'https:' + u
        if u.startswith('http'):
            return u
        return self._get_domain() + (u if u.startswith('/') else '/' + u)

    def _switch_domain(self):
        with self._lock:
            n = len(self._domains)
            self._active_idx = (self._active_idx + 1) % n
            if self._active_idx == 0:
                self._domains_refreshed = False
                self._domain = None
                self._unlocked = False
                self._unlocked_domain = ''
        return self._get_domain()

    # ============================================================
    # 验证码解锁（cookie 优先，ddddocr 可选）
    # ============================================================
    def _load_cookie(self, domain):
        cookie_str = BUILTIN_COOKIE
        try:
            if os.path.exists(_COOKIE_FILE):
                c = open(_COOKIE_FILE, encoding='utf-8').read().strip()
                if c:
                    cookie_str = c
        except Exception:
            pass
        host = urllib.parse.urlparse(domain).netloc
        for pair in cookie_str.split(';'):
            pair = pair.strip()
            if not pair or '=' not in pair:
                continue
            name, value = pair.split('=', 1)
            try:
                ck = http.cookiejar.Cookie(
                    version=0, name=name.strip(), value=value.strip(),
                    port=None, port_specified=False,
                    domain=host, domain_specified=True, domain_initial_dot=False,
                    path='/', path_specified=True, secure=False,
                    expires=None, discard=False,
                    comment=None, comment_url=None, rest={}, rfc2109=False)
                self._cj.set_cookie(ck)
            except Exception:
                pass

    def _is_verify_page(self, text):
        return bool(text and ('系统安全验证' in text[:3000] or 'captcha.php' in text[:3000]))

    def _try_cookie_unlock(self, domain):
        self._load_cookie(domain)
        r = self._request(domain + '/', timeout=TIMEOUT_API)
        if r and not self._is_verify_page(r) and len(r) > 2000:
            self._unlocked = True
            self._unlocked_domain = domain
            return True
        return False

    def _get_ocr(self):
        if self._ocr is None and ddddocr is not None:
            try:
                self._ocr = ddddocr.DdddOcr(show_ad=False)
            except Exception:
                self._ocr = None
        return self._ocr

    def _unlock(self, domain):
        """解锁指定域名：先 cookie，后 OCR。"""
        if self._unlocked and self._unlocked_domain == domain:
            return True
        with self._unlocking_lock:
            if self._unlocked and self._unlocked_domain == domain:
                return True
            if self._try_cookie_unlock(domain):
                return True
            ocr = self._get_ocr()
            if ocr is None:
                return False
            for attempt in range(2):
                try:
                    img = self._request(
                        domain + '/captcha.php?type=code&r=' + str(time.time()),
                        timeout=TIMEOUT_API, raw=True)
                    if not img:
                        time.sleep(0.3)
                        continue
                    code = ocr.classification(img)
                    code = re.sub(r'[^0-9a-zA-Z]', '', code or '')[:4]
                    if not code:
                        time.sleep(0.3)
                        continue
                    resp = self._request(domain + '/captcha.php',
                                         data={'type': 'verify', 'check': code},
                                         timeout=TIMEOUT_API)
                    try:
                        j = json.loads(resp)
                        if j.get('code') == 1:
                            self._unlocked = True
                            self._unlocked_domain = domain
                            return True
                    except Exception:
                        pass
                except Exception:
                    pass
                time.sleep(0.3)
            return False

    def _ensure_unlocked(self):
        domain = self._get_domain()
        if self._unlock(domain):
            return domain
        return None

    # ============================================================
    # HTML 解析（正则，无第三方依赖）
    # ============================================================
    @staticmethod
    def _clean_name(raw):
        if not raw:
            return raw
        return re.sub(r'\s*[（(]\s*\d{4}\s*[）)]\s*$', '', raw.strip())

    def _parse_cards(self, html, limit=36):
        if not html:
            return []
        items = {}
        for m in re.finditer(
                r'<a[^>]*class="[^"]*stui-vodlist__thumb[^"]*"[^>]*'
                r'href="(/video/(\d+)\.html)"[^>]*title="([^"]*)"[^>]*>', html, re.S):
            vid, title = m.group(2), m.group(3).strip()
            if not vid or not title or vid in items:
                continue
            pic = ''
            pm = re.search(r'data-original="([^"]+)"', m.group(0))
            if pm:
                pic = pm.group(1)
            remarks = ''
            seg = html[m.end():m.end() + 500]
            rm = re.search(r'<span class="pic-text[^"]*">([^<]*)</span>', seg)
            if rm:
                remarks = rm.group(1).strip()
            if not remarks:
                m2 = re.search(
                    r'(更新至[^\s]{0,12}|更新到[^\s]{0,12}|全\d+集|全集'
                    r'|已完结|正片|HD中字|HD国语|TC中字|抢先版)', title)
                if m2:
                    remarks = m2.group(1)
            items[vid] = {
                'vod_id': vid,
                'vod_name': title,
                'vod_pic': pic,
                'vod_remarks': remarks,
            }
        return list(items.values())[:limit]

    def _pagecount(self, html, tid):
        m = re.search(r'(\d+)\s*/\s*(\d+)', html)
        if m:
            try:
                return max(1, int(m.group(2)))
            except Exception:
                pass
        nums = [int(x) for x in re.findall(
            r'/list/%s--------(\d+)---\.html' % tid, html)]
        if nums:
            return max(1, max(nums))
        return 1

    # ============================================================
    # 二级分类 / 筛选
    # ============================================================
    def _load_site_subcats(self, html):
        """从首页/分类页解析各一级分类下的真实二级分类（/type/id.html）。"""
        if self._site_subcats_loaded or not html:
            return
        self._site_subcats_loaded = True
        for m in re.finditer(
                r'<a[^>]*href="/type/(\d+)\.html"[^>]*>([^<]{1,12})</a>',
                html, re.S):
            cid, nm = m.group(1), m.group(2).strip()
            if not nm or nm in ('全部', '更多'):
                continue
            parent = self._guess_parent(nm)
            if not parent:
                continue
            lst = self._sub_cats.setdefault(parent, [])
            if all(x['v'] != cid for x in lst):
                lst.append({'v': cid, 'n': nm})

    @staticmethod
    def _guess_parent(name):
        """按子分类名称归入大类：1电影 2剧集 3综艺 4动漫。"""
        if any(k in name for k in ('剧', '连续剧', '短剧')):
            return '2'
        if any(k in name for k in ('片', '电影')):
            return '1'
        if name in ('综艺', '真人秀', '访谈', '脱口秀', '晚会'):
            return '3'
        if any(k in name for k in ('动漫', '动画', '番剧', '漫画')):
            return '4'
        return None

    def _load_filters(self, tid, html):
        if tid in self._sub_classes and self._sub_classes.get(tid):
            return
        from urllib.parse import unquote as _unquote
        types, areas = [], []
        for m in re.finditer(r'/list/%s---([^\-<]+)--------\.html' % tid, html):
            nm = _unquote(m.group(1).strip())
            if nm and nm not in types:
                types.append(nm)
        for m in re.finditer(r'/list/%s-([^\-<]+)----------\.html' % tid, html):
            nm = _unquote(m.group(1).strip())
            if nm and nm not in areas:
                areas.append(nm)
        if types or areas:
            self._sub_classes[tid] = types
            self._sub_areas[tid] = areas

    def _build_filters(self, tid):
        filters = []
        # 二级分类（站点真实子分类，优先展示）
        sub = self._sub_cats.get(tid) or []
        if sub:
            filters.append({
                "key": "sub",
                "name": "分类",
                "value": [{"n": "全部", "v": ""}] +
                         [{"n": x['n'], "v": x['v']} for x in sub],
            })
        # 类型
        types = self._sub_classes.get(tid) or []
        if types:
            filters.append({
                "key": "class",
                "name": "类型",
                "value": [{"n": "全部", "v": ""}] + [{"n": t, "v": t} for t in types],
            })
        # 地区
        areas = self._sub_areas.get(tid) or []
        AREA_ALIAS = {'大陆': '中国大陆'}
        if areas:
            filters.append({
                "key": "area",
                "name": "地区",
                "value": [{"n": "全部", "v": ""}] + [
                    {"n": AREA_ALIAS.get(a, a), "v": AREA_ALIAS.get(a, a)} for a in areas],
            })
        # 年份
        filters.append({
            "key": "year",
            "name": "年份",
            "value": [{"n": "全部", "v": ""}] + [{"n": y, "v": y} for y in YEARS],
        })
        # 排序
        filters.append({
            "key": "by",
            "name": "排序",
            "value": [{"n": "全部", "v": ""}] + [{"n": nm, "v": key} for key, nm in SORTS],
        })
        return filters

    def _init_filters_async(self, domain):
        """后台补齐各分类筛选 + 二级分类，不阻塞首页。"""
        try:
            if not domain:
                return
            for tid in (c['type_id'] for c in CATS):
                h2 = self._get_text(domain + '/list/%s-----------.html' % tid)
                if h2 and not self._site_subcats_loaded:
                    self._load_site_subcats(h2)
                self._load_filters(tid, h2)
            self._filters = {c['type_id']: self._build_filters(c['type_id'])
                             for c in CATS}
        except Exception:
            pass

    # ============================================================
    # 首页
    # ============================================================
    def homeContent(self, filter=False):
        # 解锁 + 抓首页限时执行，超时用缓存兜底
        vod_list = []
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(self._load_home)
                vod_list = fut.result(timeout=HOME_WAIT)
        except Exception:
            with self._lock:
                vod_list = list(self._home_cache[:60])

        # 筛选初始化放后台，不阻塞首页
        if not self._filter_init:
            self._filter_init = True
            threading.Thread(target=self._init_filters_async,
                             args=(self._domain,), daemon=True).start()

        return {
            "class": CATS,
            "filters": self._filters,
            "list": vod_list,
        }

    def _load_home(self):
        domain = self._ensure_unlocked()
        if domain is None:
            return []
        now = int(time.time())
        with self._lock:
            if self._home_cache and now - self._home_cache_time < TTL_HOME:
                return self._home_cache[:60]
        html = self._get_text(domain + '/')
        vod_list = self._parse_cards(html, limit=60)
        # 顺带解析站点真实二级分类
        self._load_site_subcats(html)
        if vod_list:
            with self._lock:
                self._home_cache = vod_list
                self._home_cache_time = int(time.time())
        return vod_list

    def homeVideoContent(self):
        domain = self._ensure_unlocked()
        if domain is None:
            return {"list": []}
        now = int(time.time())
        with self._lock:
            if self._home_cache and now - self._home_cache_time < TTL_HOME:
                return {"list": self._home_cache[:60]}
        html = self._get_text(domain + '/')
        vod_list = self._parse_cards(html, limit=60)
        self._load_site_subcats(html)
        if vod_list:
            with self._lock:
                self._home_cache = vod_list
                self._home_cache_time = int(time.time())
        return {"list": vod_list[:60]}

    # ============================================================
    # 分类列表
    # ============================================================
    def _empty_category(self, page=1):
        return {"list": [], "page": page, "pagecount": 1, "limit": 36, "total": 0}

    def _cat_url(self, tid, pg, ext):
        sub = (ext.get('sub') or '').strip() if ext else ''
        dom = self._get_domain()
        # ===== 二级分类：/type/xx.html，翻页 /type/xx/page/N.html =====
        if sub:
            if pg > 1:
                return "%s/type/%s/page/%d.html" % (dom, sub, pg)
            return "%s/type/%s.html" % (dom, sub)
        cls = (ext.get('class') or '').strip() if ext else ''
        area = (ext.get('area') or '').strip() if ext else ''
        year = (ext.get('year') or '').strip() if ext else ''
        by = (ext.get('by') or '').strip() if ext else ''
        cls = urllib.parse.quote(cls) if cls else ''
        area = urllib.parse.quote(area) if area else ''
        year = urllib.parse.quote(year) if year else ''
        by = urllib.parse.quote(by) if by else ''
        has_filter = bool(cls or area or year or by)
        if has_filter and pg <= 1:
            if cls and year:
                return "%s/list/%s---%s--------%s.html" % (dom, tid, cls, year)
            if cls:
                return "%s/list/%s---%s--------.html" % (dom, tid, cls)
            if area:
                return "%s/list/%s-%s----------.html" % (dom, tid, area)
            if year:
                return "%s/list/%s-----------%s.html" % (dom, tid, year)
            if by:
                return "%s/list/%s--%s---------.html" % (dom, tid, by)
            return "%s/list/%s-----------.html" % (dom, tid)
        if pg > 1:
            return "%s/list/%s--------%s---.html" % (dom, tid, pg)
        return "%s/list/%s-----------.html" % (dom, tid)

    def categoryContent(self, tid, pg, filter, extend):
        page = 1
        try:
            page = max(1, int(pg or 1))
            ext = {}
            if extend:
                if isinstance(extend, dict):
                    ext = extend
                elif isinstance(extend, str):
                    try:
                        ext = json.loads(extend)
                    except Exception:
                        ext = {}
            ckey = "%s|%d|%s" % (tid, page, json.dumps(ext, ensure_ascii=False, sort_keys=True))
            cached = self._cache_get(self._cat_cache, ckey, TTL_CAT)
            if cached is not None:
                return cached
            domain = self._ensure_unlocked()
            if domain is None:
                return self._empty_category(page)
            # 筛选未就绪则按需补一次
            if not self._sub_classes.get(tid) and not self._sub_cats.get(tid):
                h2 = self._get_text(domain + '/list/%s-----------.html' % tid)
                if h2:
                    self._load_site_subcats(h2)
                    self._load_filters(tid, h2)
            url = self._cat_url(tid, page, ext)
            html = self._get_text(url)
            if not html:
                return self._empty_category(page)
            pagecount = self._pagecount(html, tid)
            vod_list = self._parse_cards(html, limit=36)
            result = {
                "list": vod_list,
                "page": page,
                "pagecount": pagecount,
                "limit": 36,
                "total": pagecount * 36,
            }
            self._cache_set(self._cat_cache, ckey, result, TTL_CAT)
            return result
        except Exception:
            return self._empty_category(page)

    # ============================================================
    # 详情页
    # ============================================================
    def detailContent(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        vid = str(ids[0]).split(',')[0].strip()
        if not vid:
            return {"list": []}
        cached = self._cache_get(self._detail_cache, vid, None)
        if cached is not None:
            return cached
        result = self._fetch_detail(vid)
        ttl = TTL_DETAIL_OK if result.get("list") else TTL_DETAIL_EMPTY
        self._cache_set(self._detail_cache, vid, result, ttl)
        if result.get("list"):
            self._prefetch_play(result["list"][0])
        return result

    def _fetch_detail(self, vid):
        domain = self._ensure_unlocked()
        if domain is None:
            return {"list": []}
        html = self._get_text("%s/video/%s.html" % (domain, vid))
        if not html:
            return {"list": []}
        name = ''
        m = re.search(r'<h3 class="title">([^<]+)</h3>', html)
        if m:
            name = self._clean_name(m.group(1).strip())
        pic = ''
        m = re.search(r'class="stui-content__thumb[^"]*".*?<img[^>]*?data-original="([^"]+)"', html, re.S)
        if m:
            pic = m.group(1)
        remarks = ''
        m = re.search(r'remarks-bg">([^<]*)</div>', html)
        if m:
            remarks = m.group(1).strip()
        meta = {'director': '', 'actor': '', 'type': '', 'area': '', 'year': ''}
        key_map = {'导演': 'director', '主演': 'actor', '类型': 'type',
                   '地区': 'area', '年份': 'year'}
        for m in re.finditer(
                r'<span class="text-muted[^"]*">(导演|主演|类型|地区|年份)[：:]?</span>(.*?)</p>',
                html, re.S):
            label, body = m.group(1), m.group(2)
            key = key_map.get(label)
            if not key:
                continue
            vals = re.findall(r'>([^<>]+)</a>', body)
            vals = [v.strip() for v in vals if v.strip()]
            if vals:
                meta[key] = ','.join(vals)
            else:
                txt = re.sub(r'<[^>]+>', '', body)
                txt = txt.strip()
                if txt:
                    meta[key] = txt
        content = ''
        m = re.search(r'<meta name="description" content="([^"]*)"', html)
        if m:
            c = m.group(1)
            c = re.sub(r'^[^,，]+[,，]\s*', '', c)
            content = c[:300]
        play_from, play_url = '', ''
        groups = []
        for m in re.finditer(
                r'<a href="#(playlist\d+)" data-toggle="tab">([^<]+)</a>', html):
            pane_id, src_name = m.group(1), m.group(2).strip() or '线路'
            pane_m = re.search(r'<div id="%s"[^>]*>.*?</div>' % pane_id, html, re.S)
            if not pane_m:
                continue
            eps = []
            for em in re.finditer(
                    r'<a[^>]*href="(/play/\d+-\d+-\d+\.html)"[^>]*>([^<]+)</a>',
                    pane_m.group(0)):
                ep_url, ep_name = em.group(1), em.group(2).strip() or '播放'
                eps.append((ep_name, ep_url))
            if eps:
                groups.append((src_name, eps))
        if not groups:
            eps = []
            for em in re.finditer(
                    r'<a[^>]*href="(/play/\d+-\d+-\d+\.html)"[^>]*>([^<]+)</a>', html):
                eps.append((em.group(2).strip() or '播放', em.group(1)))
            if eps:
                groups.append(('默认线路', eps))
        for src_name, eps in groups:
            ep_parts = ["%s$%s" % (n, u) for n, u in eps]
            play_from = (play_from + '$$$' + src_name) if play_from else src_name
            play_url = (play_url + '$$$' + '#'.join(ep_parts)) if play_url else '#'.join(ep_parts)
        detail = {
            "vod_id": vid,
            "vod_name": name or ("视频%s" % vid),
            "vod_pic": pic or '',
            "type_name": meta['type'],
            "vod_remarks": remarks,
            "vod_year": meta['year'],
            "vod_area": meta['area'],
            "vod_director": meta['director'],
            "vod_actor": meta['actor'],
            "vod_content": content,
            "vod_play_from": play_from or '默认',
            "vod_play_url": play_url,
        }
        return {"list": [detail]}

    # ============================================================
    # 播放解析（双重解密 + 缓存 + 预取）
    # ============================================================
    @staticmethod
    def _is_valid_play_url(url):
        if not url or not url.startswith('http'):
            return False
        for ch in url:
            o = ord(ch)
            if o < 32 or 127 <= o < 160:
                return False
        return True

    def _resolve_play(self, play_url, sid_hint=''):
        cached = self._cache_get(self._play_cache, play_url, TTL_PLAY)
        if cached:
            return cached
        real = ''
        play_url = self._abs(play_url)
        try:
            text = self._get_text(play_url, referer=self._get_domain() + '/',
                                  timeout=TIMEOUT_PLAY)
            if text:
                m = re.search(r'var\s+player_\w+\s*=\s*(\{.*?\})\s*[;<]', text, re.S)
                if m:
                    try:
                        pj = json.loads(m.group(1))
                        enc1 = pj.get('url') or ''
                        enc_next = pj.get('url_next') or ''
                        link_next = str(pj.get('link_next') or '').strip()
                        if enc_next and link_next:
                            next_url = self._abs(link_next)
                            with self._lock:
                                if (next_url not in self._prefetching and
                                        not self._cache_get(self._play_cache, next_url, TTL_PLAY)):
                                    self._prefetching.add(next_url)
                                    self._prefetch_link_next(next_url, enc_next)
                        if enc1:
                            api = self._get_domain() + '/hhplayer/api.php'
                            body = self._post_text(api, data={'vid': enc1},
                                                   referer=play_url, timeout=TIMEOUT_PLAY)
                            if not body:
                                body = self._post_text(api, data={'vid': enc1},
                                                       referer=play_url, timeout=TIMEOUT_PLAY)
                            if body:
                                try:
                                    rj = json.loads(body)
                                    enc2 = rj.get('data', {}).get('url') or ''
                                    real = hh_decode(enc2)
                                except Exception:
                                    pass
                        if not real:
                            m2 = re.search(
                                r'"url"\s*:\s*"(https?://[^"]+\.(?:m3u8|mp4)[^"]*)"', text)
                            if m2:
                                real = m2.group(1).replace('\\/', '/')
                    except Exception:
                        pass
        except Exception:
            real = ''
        if real and self._is_valid_play_url(real):
            self._cache_set(self._play_cache, play_url, real, TTL_PLAY)
            self._prefetch_next(play_url)
            return real
        return ''

    def _first_play_url(self, vod):
        for seg in (vod.get("vod_play_url") or "").split("$$$"):
            for item in seg.split("#"):
                parts = item.split("$", 1)
                if len(parts) == 2 and parts[1]:
                    return parts[1]
        return None

    def _prefetch_play(self, vod):
        target = self._first_play_url(vod)
        if not target:
            return
        with self._lock:
            if self._cache_get(self._play_cache, target, TTL_PLAY) or target in self._prefetching:
                return
            self._prefetching.add(target)

        def _job():
            try:
                self._resolve_play(target)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._prefetching.discard(target)

        threading.Thread(target=_job, daemon=True).start()

    def _prefetch_link_next(self, next_url, enc_next):
        def _job():
            try:
                api = self._get_domain() + '/hhplayer/api.php'
                body = self._post_text(api, data={'vid': enc_next},
                                       referer=next_url, timeout=TIMEOUT_PLAY)
                if body:
                    try:
                        rj = json.loads(body)
                        enc2 = rj.get('data', {}).get('url') or ''
                        real = hh_decode(enc2)
                        if self._is_valid_play_url(real):
                            self._cache_set(self._play_cache, next_url, real, TTL_PLAY)
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                with self._lock:
                    self._prefetching.discard(next_url)

        threading.Thread(target=_job, daemon=True).start()

    def _prefetch_next(self, play_url):
        m = re.search(r'/play/(\d+)-(\d+)-(\d+)\.html', play_url)
        if not m:
            return
        vid, sid, nid = m.group(1), m.group(2), int(m.group(3))
        target = '/play/%s-%s-%d.html' % (vid, sid, nid + 1)
        cached = self._cache_get(self._detail_cache, vid, TTL_DETAIL_OK)
        if not cached or not cached.get("list"):
            return
        vod = cached["list"][0]
        found = False
        for seg in (vod.get("vod_play_url") or "").split("$$$"):
            for item in seg.split("#"):
                parts = item.split("$", 1)
                if len(parts) == 2 and parts[1] and self._abs(parts[1]) == self._abs(target):
                    found = True
                    break
            if found:
                break
        if not found:
            return
        with self._lock:
            if (self._cache_get(self._play_cache, target, TTL_PLAY) or
                    target in self._prefetching):
                return
            self._prefetching.add(target)

        def _job(url=target):
            try:
                self._resolve_play(url)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._prefetching.discard(url)

        threading.Thread(target=_job, daemon=True).start()

    def _alt_play_urls(self, play_url, limit=6):
        m = re.search(r'/play/(\d+)-\d+-\d+\.html', play_url)
        if not m:
            return []
        vid = m.group(1)
        cached = self._cache_get(self._detail_cache, vid, TTL_DETAIL_OK)
        if not cached or not cached.get("list"):
            return []
        vod = cached["list"][0]
        out, seen = [], {play_url}
        for seg in (vod.get("vod_play_url") or "").split("$$$"):
            for item in seg.split("#"):
                parts = item.split("$", 1)
                if len(parts) == 2 and parts[1]:
                    u = parts[1]
                    if u not in seen:
                        seen.add(u)
                        out.append(u)
                    if len(out) >= limit:
                        return out
        return out

    def _play_payload(self, playurl):
        is_m3u8 = '.m3u8' in playurl.lower()
        return {
            "parse": 0,
            "playUrl": "",
            "url": playurl,
            "header": {"User-Agent": UA, "Referer": self._get_domain() + "/"},
            "format": "application/x-mpegURL" if is_m3u8 else "",
            "contentType": "application/x-mpegURL" if is_m3u8 else "",
        }

    def playerContent(self, flag, id, vipFlags):
        if not id:
            return {"parse": 0, "playUrl": "", "url": ""}
        play_url = id if id.startswith('http') else self._abs(id)
        m3u8 = self._resolve_play(play_url)
        if m3u8:
            return self._play_payload(m3u8)
        alts = self._alt_play_urls(play_url)
        for u in alts:
            m = self._resolve_play(u)
            if m:
                self._cache_set(self._play_cache, play_url, m, TTL_PLAY)
                return self._play_payload(m)
        return {
            "parse": 1,
            "playUrl": "",
            "url": play_url,
            "header": {"User-Agent": UA, "Referer": self._get_domain() + "/"},
        }

    # ============================================================
    # 搜索（修复版）
    # ============================================================
    @staticmethod
    def _is_flood_page(text):
        """站点防刷页：'请不要频繁操作，搜索时间间隔为3秒前'。"""
        if not text:
            return False
        head = text[:3000]
        return ('请不要频繁操作' in head) or ('msg_jump_tit' in head)

    def _search_throttle(self, gap=None):
        """搜索页最小请求间隔节流，并对时间戳加锁。"""
        if gap is None:
            gap = SEARCH_MIN_INTERVAL
        with self._lock:
            wait = gap - (time.time() - self._search_last_req)
        if wait > 0:
            time.sleep(wait)
        with self._lock:
            self._search_last_req = time.time()

    @staticmethod
    def _search_region(html):
        """截取搜索结果主列，排除侧栏「猜你喜欢」等无关推荐。"""
        if not html:
            return ''
        start = html.find('col-lg-wide-8')
        start = 0 if start < 0 else start
        end = html.find('stui-pannel-side')
        return html[start:end] if end > start else html[start:]

    def _search_page(self, domain, kw, page):
        """抓搜索页，返回 (cards, pagecount, total, blocked)。"""
        url = "%s/search/%s----------%d---.html" % (domain, kw, page)
        html = self._get_text(url, referer=domain + '/', timeout=TIMEOUT_PAGE)
        if not html:
            return [], 1, 0, False
        if self._is_flood_page(html) or self._is_verify_page(html):
            return [], 1, 0, True
        cards = self._parse_cards(self._search_region(html), limit=40)
        m = re.search(r'class="num">\s*(\d+)\s*/\s*(\d+)', html)
        pagecount = max(1, int(m.group(2))) if m else 1
        total = 0
        mt = re.search(r'相关的\s*(\d+)', html)
        if mt:
            total = int(mt.group(1))
        if not total:
            total = pagecount * len(cards) if pagecount > 1 and cards else len(cards)
        return cards, pagecount, total, False

    def _search_post(self, domain, key, page=1):
        """兜底：站点搜索框本身就是 POST /search/-------------.html。"""
        body = self._post_text("%s/search/-------------.html" % domain,
                               data={'wd': key, 'page': str(page)},
                               referer=domain + '/', timeout=TIMEOUT_PAGE)
        if not body or self._is_flood_page(body) or self._is_verify_page(body):
            return [], 1, 0
        cards = self._parse_cards(self._search_region(body), limit=40)
        m = re.search(r'class="num">\s*(\d+)\s*/\s*(\d+)', body)
        mt = re.search(r'相关的\s*(\d+)', body)
        pagecount = max(1, int(m.group(2))) if m else 1
        total = int(mt.group(1)) if mt else (pagecount * len(cards) if pagecount > 1 else len(cards))
        return cards, pagecount, total

    def _search_suggest(self, domain, key, limit=40):
        """maccms 联想接口：不参与站点 3 秒限流，用于快搜与兜底。"""
        body = self._get_text(
            "%s/index.php/ajax/suggest?mid=1&wd=%s" % (domain, urllib.parse.quote(key)),
            referer=domain + '/', timeout=TIMEOUT_API)
        if not body:
            return []
        try:
            j = json.loads(body)
        except Exception:
            return []
        out, seen = [], set()
        for it in (j.get('list') or []):
            vid = str(it.get('id') or '').strip()
            name = (it.get('name') or '').strip()
            if not vid or not name or vid in seen:
                continue
            seen.add(vid)
            out.append({
                'vod_id': vid,
                'vod_name': name,
                'vod_pic': it.get('pic') or '',
                'vod_remarks': '',
            })
            if len(out) >= limit:
                break
        return out

    def searchContent(self, key, quick=False, pg="1"):
        # 注意：第三参 pg 必须保留，壳端按 searchContent(key, quick, pg) 调用
        key = (key or '').strip()
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        empty = {"list": [], "page": page, "pagecount": 1, "limit": 36, "total": 0}
        if not key:
            return empty
        ckey = "%s|%d" % (key, page)
        cached = self._cache_get(self._search_cache, ckey, TTL_SEARCH)
        if cached is not None and cached.get('list'):
            return cached
        domain = self._ensure_unlocked()
        if domain is None:
            return empty
        kw = urllib.parse.quote(key)

        # 快搜（壳端输入过程实时触发）走联想接口，避免触发 3 秒限流
        if quick and page <= 1:
            cards = self._search_suggest(domain, key)
            if cards:
                result = {"list": cards[:36], "page": 1, "pagecount": 1,
                          "limit": 36, "total": len(cards)}
                self._cache_set(self._search_cache, ckey, result, TTL_SEARCH)
                return result

        self._search_throttle()
        cards, pagecount, total, blocked = self._search_page(domain, kw, page)
        if blocked:
            # 命中防刷页：等站点要求的间隔后重试一次
            time.sleep(SEARCH_GAP)
            self._search_throttle(0)
            cards, pagecount, total, blocked = self._search_page(domain, kw, page)
        if not cards and page <= 1:
            # suggest 不参与限流，优先于 POST 兜底
            cards = self._search_suggest(domain, key)
            if cards:
                pagecount, total = 1, len(cards)
        if not cards and page <= 1 and blocked:
            self._search_throttle()
            cards, pagecount, total = self._search_post(domain, key)

        result = {"list": cards[:36], "page": page,
                  "pagecount": max(1, pagecount),
                  "limit": 36, "total": total or len(cards)}
        # 只在有结果时缓存，避免空结果卡 180 秒
        if cards:
            self._cache_set(self._search_cache, ckey, result, TTL_SEARCH)
        return result

    # ===== 缓存 =====
    @staticmethod
    def _cache_get(cache, key, ttl=None):
        item = cache.get(key)
        if item and time.time() - item[0] < (ttl if ttl is not None else item[2]):
            return item[1]
        return None

    @staticmethod
    def _cache_set(cache, key, value, ttl=TTL_CAT):
        if len(cache) > 512:
            cache.clear()
        cache[key] = (time.time(), value, ttl)


# ============================================================
# 本地测试
# ============================================================
if __name__ == '__main__':
    sp = Spider()
    sp.init()
    print("=== 1. 首页（计时） ===")
    t0 = time.time()
    home = sp.homeContent()
    print("耗时: %.2f 秒" % (time.time() - t0))
    print("分类:", [c['type_name'] for c in home['class']])
    print("首页条数:", len(home['list']))
    print("\n=== 2. 筛选（含二级分类） ===")
    for k, v in home['filters'].items():
        for f in v:
            opts = [x['n'] for x in f['value'][:8]]
            print("  tid=%s %s: %s%s" % (k, f['name'], opts, '...' if len(f['value']) > 8 else ''))
    print("\n=== 3. 搜索(壳端三参调用 + 翻页) ===")
    sea = {"list": []}
    for kw, pg in [('仙逆', '1'), ('仙逆', '1'), ('爱情', '1'), ('爱情', '2')]:
        t0 = time.time()
        sea = sp.searchContent(kw, False, pg)
        print("搜索 %s p%s: %.2f 秒 | %d 条 | page=%s/%s total=%s" % (
            kw, pg, time.time() - t0, len(sea['list']),
            sea.get('page'), sea.get('pagecount'), sea.get('total')))
        if sea['list']:
            print("   首条:", sea['list'][0]['vod_name'])
    print("\n=== 3b. 快搜(quick=True) ===")
    t0 = time.time()
    quick_res = sp.searchContent('仙逆', True, '1')
    print("quick: %.2f 秒 | %d 条" % (time.time() - t0, len(quick_res['list'])))
    print("\n=== 4. 二级分类浏览 ===")
    # 取电影下第一个真实子分类试试
    sub_list = sp._sub_cats.get('1') or []
    if sub_list:
        sub = sub_list[0]['v']
        print("子分类 id=%s (%s)" % (sub, sub_list[0]['n']))
        cat = sp.categoryContent('1', 1, '', {'sub': sub})
        print("条数:", len(cat['list']))
        if cat['list']:
            print("样例:", cat['list'][0]['vod_name'])
    else:
        print("(未解析到子分类，站点结构可能不含 /type/id.html)")
    print("\n=== 5. 详情+播放 ===")
    eps = []
    if sea['list']:
        det = sp.detailContent([sea['list'][0]['vod_id']])
        if det['list']:
            d = det['list'][0]
            print("名称:", d['vod_name'])
            eps = d['vod_play_url'].split('$$$')[0].split('#')
            print("首线路集数:", len(eps))
    if eps:
        pj = sp.playerContent('', eps[0].split('$', 1)[1], '')
        u = str(pj.get('url'))
        print("parse:", pj.get('parse'), "|",
              'm3u8' if '.m3u8' in u else ('mp4' if '.mp4' in u else u[:80]))
