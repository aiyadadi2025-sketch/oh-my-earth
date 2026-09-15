# -*- coding: utf-8 -*-
"""
速影TV (sytvtv.com) - TVBox 爬虫源
================================================
站点：https://w6.sytvtv.com  （主域名 suyingtv.com，w6 为可用线路）

接口：homeContent / categoryContent(含二级分类+筛选) / detailContent(懒加载)
      / playerContent(按需解析 m3u8 直链) / searchContent

站点特性（已实测确认）：
1. 详情页：/movie-detail-id-{id}.html
2. 播放页：/movie-play-id-{id}-src-{sid}-num-{nid}.html
   -> 页面内 player_xxxx JSON 的 url 字段即真实 m3u8 直链
3. 分类页：/movie-type-id-{tid}-pg-{n}.html
4. 筛选页：/movie-list-id-{tid}-pg-{n}-order-desc-by-dayhits-class-{c}-year-{y}-letter--area-{a}-lang-.html
5. 搜索页：/index.php?m=vod-search&wd={kw}&page={n}  （实测可用，返回完整卡片）
6. 站点有多个备用域名（w1~w9 / 主域），域名跟踪模块自动探测可用线路

性能优化：
- 详情页懒加载：不预解析所有集数 m3u8，秒开
- playerContent 按需解析 + 15 分钟缓存 + 后台预取第 1 集与下一集
- 多级缓存：首页 10 分钟 / 分类 5 分钟 / 详情 5 分钟(失败 30 秒) / 搜索 3 分钟 / 播放 15 分钟
- 连接池复用(HTTPAdapter) + gzip 自动解压 + 短超时(8s/5s/4s) + 快速重试
- 搜索多策略：站内搜索页 -> 分类爬取兜底（分词模糊匹配 + 评分排序）
- 域名跟踪：启动时/失败时自动切换可用线路，避免单域名失效导致全站不可用
"""

import re
import json
import time
import threading
from urllib.parse import quote, urlencode

import requests
from requests.adapters import HTTPAdapter

try:
    from concurrent.futures import ThreadPoolExecutor, as_completed
except ImportError:
    ThreadPoolExecutor = None
    as_completed = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass

try:
    import sys
    sys.path.append('..')
    from base.spider import Spider as _BaseSpider
except ImportError:
    _BaseSpider = None


# ============================================================
# 常量
# ============================================================
HOST = "https://w6.sytvtv.com"

# 备用域名池（域名跟踪模块会按顺序探测，命中即用）
HOST_POOL = [
    "https://w6.sytvtv.com",
    "https://w5.sytvtv.com",
    "https://w4.sytvtv.com",
    "https://w3.sytvtv.com",
    "https://w2.sytvtv.com",
    "https://w1.sytvtv.com",
    "https://suyingtv.com",
    "https://www.sytvtv.com",
]

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

# 超时（秒）
TIMEOUT_PAGE = 8
TIMEOUT_API = 5
TIMEOUT_PLAY = 4

# 缓存 TTL（秒）
TTL_HOME = 600
TTL_CAT = 300
TTL_DETAIL_OK = 300
TTL_DETAIL_EMPTY = 30
TTL_SEARCH = 180
TTL_PLAY = 900
TTL_HOST = 1800          # 域名探测结果缓存 30 分钟

# 分页/数量
LIMIT_HOME = 60
LIMIT_PAGE = 36
LIMIT_SEARCH = 24
CACHE_MAX = 512

# 重试
RETRY_SLEEP = 0.2
RATE_LIMIT_SLEEP = 2.0

# 地区
AREAS = [
    "大陆", "香港", "台湾", "美国", "韩国", "日本", "泰国",
    "新加坡", "马来西亚", "印度", "英国", "法国", "加拿大",
    "西班牙", "俄罗斯", "其它",
]

# 年份
YEARS = [str(y) for y in range(2026, 2005, -1)]

# 排序
ORDERS = [
    ("time", "最新"),
    ("hits", "最热"),
    ("score", "评分"),
]

# 分类表：一级分类（首页导航）-> 二级分类（类型筛选）
# 结构：{"id": 一级分类 id, "name": 名称, "subs": [(二级分类 id, 名称), ...]}
# 说明：站点实际只有以下 20 个分类，综艺/动漫无独立二级分类，
#       故其 subs 为空（筛选器只保留 地区/年份/排序）。
CATS = [
    {"id": "1", "name": "电影", "subs": [
        ("5", "动作片"),
        ("6", "喜剧片"),
        ("7", "爱情片"),
        ("8", "科幻片"),
        ("9", "恐怖片"),
        ("10", "剧情片"),
        ("11", "战争片"),
        ("20", "纪录片"),
        ("22", "伦理片"),
    ]},
    {"id": "2", "name": "连续剧", "subs": [
        ("12", "国产剧"),
        ("17", "韩国剧"),
        ("15", "欧美剧"),
        ("16", "日本剧"),
        ("18", "香港剧"),
        ("19", "台湾剧"),
        ("21", "海外剧"),
    ]},
    {"id": "3", "name": "综艺", "subs": []},
    {"id": "4", "name": "动漫", "subs": []},
]


def _build_filters(cat):
    """构建筛选器：类型(二级分类) / 地区 / 年份 / 排序"""
    subs = [{"n": name, "v": slug} for slug, name in cat["subs"]]
    return [
        {
            "key": "class", "name": "类型",
            "value": [{"n": "全部", "v": ""}] + subs,
        },
        {
            "key": "area", "name": "地区",
            "value": [{"n": "全部", "v": ""}] + [{"n": a, "v": a} for a in AREAS],
        },
        {
            "key": "year", "name": "年份",
            "value": [{"n": "全部", "v": ""}] + [{"n": y, "v": y} for y in YEARS],
        },
        {
            "key": "by", "name": "排序",
            "value": [{"n": n, "v": v} for v, n in ORDERS],
        },
    ]


# 全部分类 + 筛选器
ALL_CLASSES = [{"type_id": c["id"], "type_name": c["name"], "filter": 1} for c in CATS]
ALL_FILTERS = {c["id"]: _build_filters(c) for c in CATS}


# ============================================================
# Spider 主类
# ============================================================
_Base = _BaseSpider if _BaseSpider is not None else object


class Spider(_Base):
    siteUrl = HOST
    headers = {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'Referer': HOST + '/',
    }

    # ===== 初始化 =====
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.headers['Connection'] = 'keep-alive'
        self.session.verify = False
        adapter = HTTPAdapter(
            pool_connections=20, pool_maxsize=40,
            max_retries=0, pool_block=False,
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

        # 域名跟踪
        self._host = HOST
        self._host_time = 0
        self._host_lock = threading.Lock()

        # 缓存容器 + 锁
        self._lock = threading.Lock()
        self._home_cache = []
        self._home_cache_time = 0
        self._cat_cache = {}
        self._detail_cache = {}
        self._search_cache = {}
        self._play_cache = {}
        self._prefetching = set()

    def init(self, extend=""):
        self.extend = extend or ""

    # ============================================================
    # 域名跟踪
    # ============================================================
    def _probe_host(self, host):
        """探测单个域名是否可用（返回 True/False）"""
        try:
            r = self.session.get(
                host + '/', timeout=TIMEOUT_API,
                headers={'Connection': 'keep-alive'},
            )
            if r.status_code != 200:
                return False
            text = r.text or ''
            # 站点特征校验，避免被劫持到无关页面
            return ('movie-type-id' in text) or ('速影' in text)
        except Exception:
            return False

    def _get_host(self, force=False):
        """获取当前可用域名（带缓存 + 自动切换）"""
        now = time.time()
        with self._host_lock:
            if not force and self._host and now - self._host_time < TTL_HOST:
                return self._host

        # 优先复用当前域名
        candidates = [self._host] + [h for h in HOST_POOL if h != self._host]
        for host in candidates:
            if not host:
                continue
            if self._probe_host(host):
                with self._host_lock:
                    self._host = host
                    self._host_time = time.time()
                return host

        # 全部失败则退回默认域名
        with self._host_lock:
            self._host = HOST
            self._host_time = time.time()
        return HOST

    def _switch_host(self):
        """当前域名疑似失效，强制重新探测"""
        return self._get_host(force=True)

    # ===== 网络工具 =====
    def _request(self, method, url, referer='', timeout=TIMEOUT_PAGE, data=None):
        """统一请求：重试 + 429 限流等待 + 编码修正"""
        headers = {'Connection': 'keep-alive'}
        if referer:
            headers['Referer'] = referer
        for attempt in range(2):
            try:
                if method == 'POST':
                    r = self.session.post(url, data=data, timeout=timeout, headers=headers)
                else:
                    r = self.session.get(url, timeout=timeout, headers=headers)
                if r.status_code == 429:
                    time.sleep(RATE_LIMIT_SLEEP)
                    continue
                r.raise_for_status()
                r.encoding = r.apparent_encoding or 'utf-8'
                return r
            except Exception:
                if attempt == 0:
                    time.sleep(RETRY_SLEEP)
                else:
                    return None
        return None

    def _get(self, url, referer='', timeout=TIMEOUT_PAGE):
        return self._request('GET', url, referer, timeout)

    def _get_text(self, url, referer='', timeout=TIMEOUT_PAGE):
        r = self._get(url, referer, timeout)
        return r.text if r is not None else ""

    def _post_text(self, url, data=None, referer='', timeout=TIMEOUT_API):
        r = self._request('POST', url, referer, timeout, data)
        return r.text if r is not None else ""

    def _get_text_retry_host(self, path, referer='', timeout=TIMEOUT_PAGE):
        """带域名切换的取文本：失败后换线路重试一次"""
        host = self._get_host()
        text = self._get_text(host + path, referer, timeout)
        if text:
            return text
        host = self._switch_host()
        return self._get_text(host + path, referer, timeout)

    # ===== 缓存 =====
    @staticmethod
    def _cache_get(cache, key, ttl=None):
        item = cache.get(key)
        if item and time.time() - item[0] < (ttl if ttl is not None else item[2]):
            return item[1]
        return None

    @staticmethod
    def _cache_set(cache, key, value, ttl=TTL_CAT):
        if len(cache) > CACHE_MAX:
            cache.clear()
        cache[key] = (time.time(), value, ttl)

    # ===== HTML 解析 =====
    @staticmethod
    def _soup(html):
        if not html or BeautifulSoup is None:
            return None
        try:
            return BeautifulSoup(html, 'lxml')
        except Exception:
            try:
                return BeautifulSoup(html, 'html.parser')
            except Exception:
                return None

    @staticmethod
    def _abs(u, host=None):
        u = (u or '').strip()
        if not u:
            return ''
        if u.startswith('//'):
            return 'https:' + u
        if u.startswith('/'):
            return (host or HOST) + u
        if not u.startswith('http'):
            return (host or HOST) + '/' + u
        return u

    @staticmethod
    def _extract_id(href):
        """从 /movie-detail-id-212652.html 提取 212652"""
        m = re.search(r'/movie-detail-id-(\d+)\.html', (href or '').strip())
        if m:
            return m.group(1)
        m = re.search(r'/(\d{4,10})\.html$', (href or '').strip())
        return m.group(1) if m else None

    @staticmethod
    def _clean_name(raw):
        if not raw:
            return raw
        return re.sub(r'\s*[（(]\s*\d{4}\s*[）)]\s*$', '', raw.strip())

    @staticmethod
    def _pick_pic(el):
        if el is None:
            return ''
        for attr in ('data-original', 'data-src', 'data-echo', 'src'):
            val = str(el.get(attr) or '').strip()
            if val and not val.startswith('data:'):
                return Spider._abs(val)
        style = str(el.get('style') or '')
        m = re.search(r'background-image:\s*url\(([^)]+)\)', style)
        if m:
            return Spider._abs(m.group(1).strip().strip('"').strip("'"))
        return ''

    @staticmethod
    def _pick_remarks(title, box=None):
        """从卡片文本/标题中提取更新状态"""
        if box is not None:
            st = box.select_one('.pic-text') or box.select_one('.pic-tag')
            if st:
                txt = st.get_text(strip=True)
                if txt:
                    return txt
        m = re.search(
            r'(更新至[^\s]{0,12}|更新到[^\s]{0,12}|更新第[^\s]{0,8}|全\d+集|全集'
            r'|已完结|正片|HD中字|HD国语|HD粤语|TC中字|DVD国语|抢先版)',
            title or ''
        )
        return m.group(1) if m else ''

    def _card_from_anchor(self, a):
        """从 <a> 节点构建一个 vod 卡片（统一去重前的单条）"""
        href = str(a.get('href') or '')
        vid = self._extract_id(href)
        if not vid:
            return None
        title = str(a.get('title') or '').strip()
        if not title:
            title = a.get_text(strip=True)
        if not title:
            return None
        box = a.find_parent('li') or a.find_parent('div')
        return {
            'vod_id': vid,
            'vod_name': self._clean_name(title),
            'vod_pic': self._pick_pic(a),
            'vod_remarks': self._pick_remarks(title, box),
        }

    def _parse_cards(self, html, limit=LIMIT_PAGE):
        """解析列表卡片（首页/分类/搜索通用）"""
        if not html:
            return []
        soup = self._soup(html)
        if soup is None:
            return []
        items = {}
        selectors = [
            'a[href*="/movie-detail-id-"]',
            'a.myui-vodlist__thumb',
            'a.stui-vodlist__thumb',
        ]
        for selector in selectors:
            for a in soup.select(selector):
                card = self._card_from_anchor(a)
                if card and card['vod_id'] not in items:
                    items[card['vod_id']] = card
            if items:
                break
        if not items:
            # 正则兜底
            pattern = re.compile(
                r'<a[^>]*href="/movie-detail-id-(\d+)\.html"[^>]*'
                r'title="([^"]*)"', re.S
            )
            for vid, title in pattern.findall(html):
                if vid not in items and title:
                    items[vid] = {
                        'vod_id': vid,
                        'vod_name': self._clean_name(title),
                        'vod_pic': '',
                        'vod_remarks': self._pick_remarks(title),
                    }
        return list(items.values())[:limit]

    # ============================================================
    # 首页
    # ============================================================
    def homeContent(self, filter=False):
        vod_list = []
        now = int(time.time())
        with self._lock:
            if self._home_cache and now - self._home_cache_time < TTL_HOME:
                vod_list = self._home_cache[:LIMIT_HOME]
        if not vod_list:
            try:
                html = self._get_text_retry_host('/')
                vod_list = self._parse_cards(html, limit=LIMIT_HOME)
                if vod_list:
                    with self._lock:
                        self._home_cache = vod_list
                        self._home_cache_time = int(time.time())
            except Exception:
                pass
        return {
            "class": ALL_CLASSES,
            "filters": ALL_FILTERS,
            "list": vod_list,
        }

    def homeVideoContent(self):
        now = int(time.time())
        with self._lock:
            if self._home_cache and now - self._home_cache_time < TTL_HOME:
                return {"list": self._home_cache[:LIMIT_HOME]}
        try:
            html = self._get_text_retry_host('/')
            vod_list = self._parse_cards(html, limit=LIMIT_HOME)
            if vod_list:
                with self._lock:
                    self._home_cache = vod_list
                    self._home_cache_time = int(time.time())
            return {"list": vod_list[:LIMIT_HOME]}
        except Exception:
            return {"list": []}

    # ============================================================
    # 分类列表（含二级分类 + 筛选）
    # ============================================================
    @staticmethod
    def _empty_category(page=1):
        return {"list": [], "page": page, "pagecount": 1, "limit": LIMIT_PAGE, "total": 0}

    def _build_category_url(self, tid, page, ext):
        """
        构建分类 URL（实测结论）：
        - 二级分类：/movie-type-id-{tid}-pg-{n}.html
          （站点 list 路由的 class- 参数无效，二级分类必须走 type-id 路由）
        - 仅地区/年份筛选：/movie-list-id-{tid}-pg-{n}-order-desc-by-{by}-class--year-{y}-letter--area-{a}-lang-.html
        - 无筛选：/movie-type-id-{tid}-pg-{n}.html
        """
        cls = (ext.get('class') or '').strip()
        area = (ext.get('area') or '').strip()
        year = (ext.get('year') or '').strip()
        by = (ext.get('by') or '').strip() or 'time'

        # 二级分类优先走 type-id 路由（class 参数在 list 路由下无效）
        if cls:
            return f"/movie-type-id-{cls}-pg-{page}.html"

        if not (area or year):
            return f"/movie-type-id-{tid}-pg-{page}.html"

        return (
            f"/movie-list-id-{tid}-pg-{page}-order-desc-by-{by}"
            f"-class--year-{year}-letter--area-{quote(area)}-lang-.html"
        )

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

            # 二级分类优先：extend.class 覆盖一级 tid
            cls = (ext.get('class') or '').strip()
            real_tid = cls or str(tid)

            ckey = "%s|%d|%s" % (
                real_tid, page,
                json.dumps(ext, ensure_ascii=False, sort_keys=True),
            )
            cached = self._cache_get(self._cat_cache, ckey, TTL_CAT)
            if cached is not None:
                return cached

            path = self._build_category_url(real_tid, page, ext)
            html = self._get_text_retry_host(path)
            if not html:
                return self._empty_category(page)

            # 总页数：优先取 "当前第“1”页" 附近的 "1/2010" 形式
            pagecount = 1
            m = re.search(r'(\d+)\s*/\s*(\d+)\s*<', html)
            if m:
                pagecount = int(m.group(2))
            else:
                nums = [int(x) for x in re.findall(r'-pg-(\d+)\.html', html)]
                if nums:
                    pagecount = max(nums)

            vod_list = self._parse_cards(html, limit=LIMIT_PAGE)

            result = {
                "list": vod_list,
                "page": page,
                "pagecount": pagecount,
                "limit": LIMIT_PAGE,
                "total": pagecount * LIMIT_PAGE,
            }
            self._cache_set(self._cat_cache, ckey, result, TTL_CAT)
            return result
        except Exception:
            return self._empty_category(page)

    # ============================================================
    # 详情页（懒加载）
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
        html = self._get_text_retry_host(f"/movie-detail-id-{vid}.html")
        if not html:
            return {"list": []}
        soup = self._soup(html)
        if soup is None:
            return {"list": []}

        # --- 基本信息 ---
        # 站点结构：<div class="stui-content__detail"><h3 class="title">片名</h3>
        name = ''
        detail_box = soup.select_one('.stui-content__detail')
        if detail_box is not None:
            h3 = detail_box.select_one('h3.title') or detail_box.select_one('h3')
            if h3:
                name = self._clean_name(h3.get_text(strip=True))
        if not name:
            h3 = soup.select_one('h3.title')
            if h3:
                name = self._clean_name(h3.get_text(strip=True))
        if not name:
            # 最后兜底：从 <title> 截取（去掉站点后缀）
            t = soup.title
            if t:
                raw = t.get_text(strip=True)
                name = self._clean_name(re.split(r'[-_|]', raw)[0].strip())

        pic = ''
        img = (soup.select_one('.stui-content__thumb img')
               or soup.select_one('.myui-content__thumb img')
               or soup.select_one('.stui-player__info img'))
        if img:
            pic = self._pick_pic(img)
        if not pic:
            # 缩略图用 background-image 承载封面
            thumb = soup.select_one('.stui-content__thumb .stui-vodlist__thumb')
            if thumb is not None:
                pic = self._pick_pic(thumb)

        # --- 元信息（主演/导演/类型/地区/年份）---
        meta_fields = self._parse_meta_fields(soup)
        director = meta_fields.get('director', '')
        actor = meta_fields.get('actor', '')
        area = meta_fields.get('area', '')
        lang = meta_fields.get('lang', '')
        year = meta_fields.get('year', '') or self._pick_year(soup.get_text(' ', strip=True))

        # --- 简介 ---
        content = ''
        meta = soup.find('meta', attrs={'name': 'description'})
        if meta:
            c = str(meta.get('content') or '')
            m = re.search(r'剧情[:：](.{10,})', c)
            if m:
                content = m.group(1).strip()
            if not content and c:
                content = c[:200]

        remarks = self._pick_remarks(name or '')

        # --- 类型名（面包屑）---
        type_name = ''
        for a in soup.select('a[href*="/movie-type-id-"]'):
            nm = a.get_text(strip=True)
            if nm and nm not in ('电影', '连续剧', '综艺', '动漫'):
                type_name = nm
                break

        # --- 播放源与集数 ---
        play_groups = self._parse_play_groups(soup)

        play_from, play_url = '', ''
        for src_name, eps in play_groups:
            ep_parts = [f"{n}${u}" for n, u in eps]
            play_from = (play_from + '$$$' + src_name) if play_from else src_name
            play_url = (play_url + '$$$' + '#'.join(ep_parts)) if play_url else '#'.join(ep_parts)

        detail = {
            "vod_id": vid,
            "vod_name": name or f"视频{vid}",
            "vod_pic": pic or HOST,
            "type_name": type_name,
            "vod_remarks": remarks or '',
            "vod_year": year,
            "vod_area": area,
            "vod_lang": lang,
            "vod_director": director,
            "vod_actor": actor,
            "vod_content": content,
            "vod_play_from": play_from or '默认',
            "vod_play_url": play_url or '',
        }
        return {"list": [detail]}

    def _parse_play_groups(self, soup):
        """解析播放源分组：[(源名, [(集名, 播放页URL), ...]), ...]

        站点结构：
            <span class="playtitle" tag="1">红牛云</span>
            <ul class="m3u8" id="playlist_1">
                <li><a title='正片' href='/movie-play-id-{id}-src-1-num-1.html'>正片</a></li>
            </ul>
        """
        groups = []

        # 方式1：playtitle + playlist_{tag} 配对（本站主结构）
        titles = soup.select('span.playtitle')
        if titles:
            for span in titles:
                src_name = span.get_text(strip=True) or '线路'
                tag = str(span.get('tag') or '').strip()
                pane = soup.find(id=f"playlist_{tag}") if tag else None
                if pane is None:
                    # 无 tag 时按顺序取下一个 m3u8 列表
                    pane = soup.select_one('ul.m3u8')
                eps = self._collect_episodes(pane)
                if eps:
                    groups.append((src_name, eps))

        # 方式2：nav-tabs 分线路
        if not groups:
            tabs = soup.select('ul.nav-tabs a[href^="#playlist"]')
            for tab in tabs:
                src_name = tab.get_text(strip=True) or '线路'
                pane_id = str(tab.get('href') or '').lstrip('#')
                eps = self._collect_episodes(soup.find(id=pane_id))
                if eps:
                    groups.append((src_name, eps))

        # 方式3：直接抓所有播放链接，按 src 分组
        if not groups:
            by_src, order = {}, []
            for a in soup.select('a[href*="/movie-play-id-"]'):
                href = str(a.get('href') or '')
                m = re.search(r'/movie-play-id-(\d+)-src-(\d+)-num-(\d+)\.html', href)
                if not m:
                    continue
                sid = m.group(2)
                ep_name = a.get_text(strip=True) or f"第{m.group(3)}集"
                if sid not in by_src:
                    by_src[sid] = []
                    order.append(sid)
                by_src[sid].append((ep_name, self._abs(href)))
            for sid in order:
                groups.append((f"线路{sid}", by_src[sid]))

        return groups

    def _collect_episodes(self, pane):
        """从播放列表容器中收集 (集名, 播放页URL)，自动去重"""
        if pane is None:
            return []
        eps, seen = [], set()
        for a in pane.select('a[href*="/movie-play-id-"]'):
            href = str(a.get('href') or '')
            if not re.search(r'/movie-play-id-\d+-src-\d+-num-\d+\.html', href):
                continue
            ep_url = self._abs(href)
            if not ep_url or ep_url in seen:
                continue
            seen.add(ep_url)
            eps.append((a.get_text(strip=True) or '播放', ep_url))
        return eps

    # ============================================================
    # 播放解析
    # ============================================================
    def _resolve_play(self, play_url):
        """解析播放页，提取真实 m3u8 直链

        站点结构（实测）：
            <script>var mac_flag='play',mac_link='...',mac_name='...',
                    mac_from='hnm3u8',mac_server='0',mac_note='正片',
                    mac_url=unescape('%u6b63%u7247%24https%3A%2F%2Fhn.bfvvs.com%2Fplay%2Fxxx%2Findex.m3u8');
            </script>
        其中 mac_url 为 unescape 编码的 "集名$直链" 形式。
        """
        cached = self._cache_get(self._play_cache, play_url, TTL_PLAY)
        if cached:
            return cached
        real = ''
        try:
            text = self._get_text(play_url, referer=self._get_host() + '/', timeout=TIMEOUT_PLAY)
            if text:
                real = self._extract_mac_url(text)
                # 兜底1：player_xxxx = {...} JSON
                if not real:
                    m = re.search(r'var\s+player_\w+\s*=\s*(\{.*?\})\s*[;<]', text, re.S)
                    if m:
                        try:
                            data = json.loads(m.group(1))
                            u = (data.get('url') or '').replace('\\/', '/').strip()
                            if u:
                                real = self._abs(u)
                        except Exception:
                            pass
                # 兜底2：直接找 m3u8
                if not real:
                    m2 = re.search(r'["\']url["\']\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', text)
                    if m2:
                        real = self._abs(m2.group(1).replace('\\/', '/'))
                # 兜底3：任意 http 视频地址
                if not real:
                    m3 = re.search(r'["\']url["\']\s*:\s*["\'](https?://[^"\']+)["\']', text)
                    if m3:
                        u = m3.group(1).replace('\\/', '/').strip()
                        if '.m3u8' in u or '.mp4' in u:
                            real = self._abs(u)
        except Exception:
            real = ''
        if real:
            self._cache_set(self._play_cache, play_url, real, TTL_PLAY)
        return real

    @staticmethod
    def _extract_mac_url(text):
        """从 mac_url=unescape('...') 中提取真实播放直链"""
        m = re.search(r"mac_url\s*=\s*unescape\(\s*['\"]([^'\"]+)['\"]\s*\)", text)
        if not m:
            return ''
        raw = m.group(1)
        # unescape 解码（%uXXXX 与 %XX 两种形式）
        try:
            decoded = Spider._unescape(raw)
        except Exception:
            return ''
        # 形如 "正片$https://xxx/index.m3u8"，取分隔符后的直链
        if '$' in decoded:
            decoded = decoded.split('$', 1)[1]
        # mac_url 可能含多集（用 # 分隔），只取当前这一集
        decoded = decoded.split('#', 1)[0]
        decoded = decoded.strip()
        if decoded.startswith('http'):
            return Spider._abs(decoded)
        return ''

    @staticmethod
    def _unescape(s):
        """实现 JS unescape：先解 %uXXXX，再解 %XX"""
        def _u(m):
            return chr(int(m.group(1), 16))

        s = re.sub(r'%u([0-9a-fA-F]{4})', _u, s)

        def _x(m):
            return chr(int(m.group(1), 16))

        s = re.sub(r'%([0-9a-fA-F]{2})', _x, s)
        return s

    @staticmethod
    def _first_play_url(vod):
        for seg in (vod.get("vod_play_url") or "").split("$$$"):
            for item in seg.split("$"):
                parts = item.split("$", 1)
                if len(parts) == 2 and parts[1]:
                    return parts[1]
        return None

    def _spawn_prefetch(self, url):
        """后台预取一个播放地址（去重 + 线程安全）"""
        if not url:
            return
        with self._lock:
            if self._cache_get(self._play_cache, url, TTL_PLAY) or url in self._prefetching:
                return
            self._prefetching.add(url)

        def _job():
            try:
                self._resolve_play(url)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._prefetching.discard(url)

        threading.Thread(target=_job, daemon=True).start()

    def _prefetch_play(self, vod):
        """详情页打开后，后台预取第 1 集"""
        self._spawn_prefetch(self._first_play_url(vod))

    def _prefetch_next(self, play_url, vod_id):
        """播放时后台预取下一集"""
        cached = self._cache_get(self._detail_cache, vod_id, TTL_DETAIL_OK)
        if not cached or not cached.get("list"):
            return
        vod = cached["list"][0]
        for seg in (vod.get("vod_play_url") or "").split("$$$"):
            items = seg.split("#")
            for i, item in enumerate(items):
                parts = item.split("$", 1)
                if len(parts) == 2 and self._abs(parts[1]) == play_url:
                    if i + 1 < len(items):
                        nxt = items[i + 1].split("$", 1)
                        if len(nxt) == 2 and nxt[1]:
                            self._spawn_prefetch(self._abs(nxt[1]))
                    return

    @staticmethod
    def _play_payload(playurl):
        is_m3u8 = '.m3u8' in playurl.lower()
        return {
            "parse": 0,
            "playUrl": "",
            "url": playurl,
            "header": {
                "User-Agent": UA,
                "Referer": HOST + "/",
                "Origin": HOST,
            },
            "format": "application/x-mpegURL" if is_m3u8 else "",
            "contentType": "application/x-mpegURL" if is_m3u8 else "",
        }

    def playerContent(self, flag, id, vipFlags):
        if not id:
            return {"parse": 0, "playUrl": "", "url": ""}
        play_url = self._abs(str(id))

        cached = self._cache_get(self._play_cache, play_url, TTL_PLAY)
        if cached:
            self._prefetch_next(play_url, self._vid_from_play_url(play_url))
            return self._play_payload(cached)

        m3u8 = self._resolve_play(play_url)
        if m3u8:
            self._prefetch_next(play_url, self._vid_from_play_url(play_url))
            return self._play_payload(m3u8)

        # 兜底：并行尝试同片其他线路
        alts = self._alt_play_urls(play_url)
        if alts and ThreadPoolExecutor is not None:
            try:
                with ThreadPoolExecutor(max_workers=3) as ex:
                    futs = [ex.submit(self._resolve_play, u) for u in alts]
                    for f in as_completed(futs, timeout=TIMEOUT_PLAY):
                        u = f.result(timeout=TIMEOUT_PLAY)
                        if u:
                            self._cache_set(self._play_cache, play_url, u, TTL_PLAY)
                            return self._play_payload(u)
            except Exception:
                pass
        else:
            for u in alts:
                m = self._resolve_play(u)
                if m:
                    self._cache_set(self._play_cache, play_url, m, TTL_PLAY)
                    return self._play_payload(m)

        return {
            "parse": 1,
            "playUrl": "",
            "url": play_url,
            "header": {"User-Agent": UA, "Referer": HOST + "/"},
        }

    @staticmethod
    def _vid_from_play_url(play_url):
        """从播放页 URL 反推视频 id"""
        m = re.search(r'/movie-play-id-(\d+)-', play_url or '')
        return m.group(1) if m else ''

    def _alt_play_urls(self, play_url, limit=6):
        """取同片其他线路的播放页地址"""
        vid = self._vid_from_play_url(play_url)
        if not vid:
            return []
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

    # ============================================================
    # 工具：元信息提取
    # ============================================================
    @staticmethod
    def _parse_meta_fields(soup):
        """解析 p.data 中的 主演/导演/类型/地区/年份/语言 等字段"""
        key_map = {
            '导演': 'director', '编剧': 'writer', '主演': 'actor',
            '类型': 'type', '地区': 'area', '语言': 'lang',
            '年份': 'year', '又名': 'aka', '评分': 'score',
        }
        out = {}
        for p in soup.select('p.data'):
            # 同一 <p> 内可能含多个字段，按 text-muted 标签逐个切分
            labels = p.find_all('span', class_='text-muted')
            if not labels:
                continue
            for label_el in labels:
                label = (label_el.get_text(strip=True) or '').rstrip(':：').strip()
                if label not in key_map:
                    continue
                # 值 = 本标签之后、下一个 text-muted 标签之前的所有文本
                parts = []
                for node in label_el.next_siblings:
                    if getattr(node, 'name', None) == 'span' and \
                            'text-muted' in (node.get('class') or []):
                        break
                    txt = node.get_text(' ', strip=True) if hasattr(node, 'get_text') \
                        else str(node).strip()
                    if txt:
                        parts.append(txt)
                val = ' '.join(parts).strip().lstrip(':： ').strip()
                val = re.sub(r'\s+', ' ', val).strip()
                if val:
                    out.setdefault(key_map[label], val)
        return out

    @staticmethod
    def _pick_year(text):
        m = re.search(r'年份[:：]\s*(\d{4})', text)
        if m:
            return m.group(1)
        m = re.search(r'[（(]\s*(\d{4})\s*[）)]', text)
        if m:
            return m.group(1)
        m = re.search(r'\b(19\d{2}|20\d{2})\b', text)
        return m.group(1) if m else ''

    # ============================================================
    # 搜索（站内搜索页 -> 分类爬取兜底）
    # ============================================================
    @staticmethod
    def _filter_by_keyword(cards, raw, limit=LIMIT_SEARCH):
        """分词模糊匹配 + 评分排序

        评分规则（避免无关结果）：
        - 整体命中：100 分
        - 2 字滑窗命中：每命中 1 个 +10 分
        - 单字命中：每命中 1 个 +1 分
        仅保留达到最低分阈值的结果，防止"随便凑几个字"就返回无关影片。
        """
        if not raw:
            return cards[:limit]
        raw = raw.lower().replace(' ', '').strip()
        if not raw:
            return cards[:limit]

        # 分词：整体 > 2字滑窗 > 单字
        if len(raw) <= 2:
            bigrams = [raw]
        else:
            bigrams = [raw[i:i + 2] for i in range(0, len(raw) - 1)]
        singles = list(raw)

        def score(name):
            name = (name or '').lower().replace(' ', '')
            if not name:
                return 0
            if raw in name:
                return 100
            s = sum(10 for t in bigrams if t in name)
            s += sum(1 for c in singles if c in name)
            return s

        # 最低分阈值：短词要求整体命中，长词要求至少命中一个 2 字片段
        if len(raw) <= 2:
            min_score = 100
        else:
            min_score = 10

        matched = [(score(c.get('vod_name') or ''), c) for c in cards]
        matched = [c for s, c in matched if s >= min_score]
        matched.sort(key=lambda c: score(c.get('vod_name') or ''), reverse=True)
        return matched[:limit]

    def _search_site(self, kw, raw, page):
        """策略1：站内搜索页（实测 /index.php?m=vod-search&wd= 可用）"""
        urls = [
            f"/index.php?m=vod-search&wd={kw}&page={page}",
            f"/index.php/vod/search.html?wd={kw}&page={page}",
            f"/vod/search.html?wd={kw}&page={page}",
        ]
        for path in urls:
            try:
                html = self._get_text_retry_host(path, referer=self._get_host() + '/',
                                                 timeout=TIMEOUT_API)
                if not html:
                    continue
                if '搜索功能关闭' in html or '搜索功能暂停' in html:
                    continue
                cards = self._parse_cards(html, limit=LIMIT_PAGE)
                if not cards:
                    continue
                matched = self._filter_by_keyword(cards, raw)
                if matched:
                    return {"list": matched}
            except Exception:
                continue

        # POST 兜底
        try:
            host = self._get_host()
            html = self._post_text(
                host + "/index.php?m=vod-search",
                data={'wd': kw, 'page': str(page)},
                referer=host + '/',
                timeout=TIMEOUT_API,
            )
            if html and '搜索功能关闭' not in html:
                cards = self._parse_cards(html, limit=LIMIT_PAGE)
                if cards:
                    matched = self._filter_by_keyword(cards, raw)
                    if matched:
                        return {"list": matched}
        except Exception:
            pass
        return None

    def _search_by_scrape(self, raw, page):
        """策略2：分类爬取兜底（并行抓取 + 分词评分过滤）"""
        if page > 1:
            return {"list": []}

        pages = (1, 2, 3) if len(raw) >= 3 else (1, 2)

        def _fetch_cat(args):
            cat_id, p = args
            path = f"/movie-type-id-{cat_id}-pg-{p}.html"
            html = self._get_text_retry_host(path, timeout=TIMEOUT_API)
            return self._parse_cards(html, limit=LIMIT_PAGE)

        all_cards = []
        if ThreadPoolExecutor is not None:
            tasks = [(c['id'], p) for c in CATS for p in pages]
            with ThreadPoolExecutor(max_workers=5) as ex:
                futs = [ex.submit(_fetch_cat, t) for t in tasks]
                for f in as_completed(futs, timeout=TIMEOUT_API * 5):
                    try:
                        all_cards.extend(f.result(timeout=TIMEOUT_API))
                    except Exception:
                        continue
        else:
            for c in CATS:
                for p in pages:
                    try:
                        all_cards.extend(_fetch_cat((c['id'], p)))
                    except Exception:
                        continue

        matched = self._filter_by_keyword(all_cards, raw, limit=LIMIT_SEARCH)
        return {"list": matched} if matched else None

    def searchContent(self, keyword, quick=False, pg=1):
        """多策略搜索：站内搜索页 -> 分类爬取兜底"""
        kw = quote((keyword or '').strip())
        raw = (keyword or '').strip().lower()
        if not kw:
            return {"list": [], "msg": "请输入搜索关键词"}

        page = int(pg or 1)
        ckey = "%s|%s" % (page, raw)
        cached = self._cache_get(self._search_cache, ckey, TTL_SEARCH)
        if cached is not None:
            return cached

        result = self._search_site(kw, raw, page)
        if not result:
            result = self._search_by_scrape(raw, page)

        if result and result.get("list"):
            self._cache_set(self._search_cache, ckey, result, TTL_SEARCH)
            return result

        result = {"list": [], "msg": "未找到相关内容，请尝试其他关键词或通过分类浏览"}
        self._cache_set(self._search_cache, ckey, result, TTL_SEARCH)
        return result

    # ============================================================
    # 本地代理 & 清理
    # ============================================================
    def localProxy(self, param):
        return [200, "video/MP2T", b"", ""]

    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass

    def close(self):
        self.destroy()


# ============================================================
# 本地测试
# ============================================================
if __name__ == '__main__':
    import sys as _sys

    s = Spider()
    action = _sys.argv[1] if len(_sys.argv) > 1 else 'home'

    if action == 'host':
        print("当前可用域名:", s._get_host(force=True))
    elif action == 'home':
        r = s.homeContent()
        print("分类数:", len(r['class']), "首页条数:", len(r['list']))
        print(json.dumps(r['list'][:2], ensure_ascii=False)[:600])
    elif action == 'category':
        tid = _sys.argv[2] if len(_sys.argv) > 2 else '1'
        pg = _sys.argv[3] if len(_sys.argv) > 3 else '1'
        cl = _sys.argv[4] if len(_sys.argv) > 4 else ''
        r = s.categoryContent(tid, pg, False, {'class': cl} if cl else {})
        print("页数:", r['pagecount'], "条数:", len(r['list']))
        print(json.dumps(r['list'][:2], ensure_ascii=False)[:600])
    elif action == 'detail':
        vid = _sys.argv[2] if len(_sys.argv) > 2 else '212652'
        r = s.detailContent(vid)
        d = r['list'][0] if r.get('list') else {}
        print("名称:", d.get('vod_name'), "| 线路:", d.get('vod_play_from'))
        print("播放串:", (d.get('vod_play_url') or '')[:300])
    elif action == 'play':
        pid = _sys.argv[2] if len(_sys.argv) > 2 else ''
        print(json.dumps(s.playerContent('', pid, []), ensure_ascii=False)[:500])
    elif action == 'search':
        kw = _sys.argv[2] if len(_sys.argv) > 2 else '深渊'
        r = s.searchContent(kw)
        print("结果数:", len(r.get('list', [])))
        print(json.dumps(r.get('list', [])[:3], ensure_ascii=False)[:600])