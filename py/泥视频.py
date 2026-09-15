# -*- coding: utf-8 -*-
"""
泥视频 (www.nivod.vip) - TVBox 爬虫源 (maccms)
================================================
接口：homeContent / categoryContent(含二级分类筛选) / detailContent(懒加载)
      / playerContent(按需解析m3u8直链) / searchContent

站点特性（已实测确认）：
1. 播放链路：详情页 /nivod/{id}/ -> 播放入口 /niplay/{vid}-{sid}-{nid}/
   -> 播放页内 player_xxxx JSON 的 url 字段即真实 m3u8 直链（encrypt=0，免二次解析）
2. 列表页：/k/{tid}-----------/（第1页），翻页 /k/{tid}--------{n}---/
3. 筛选 URL 为 12 字段拼接（下划线字段为占位空）：
   /k/{tid}-{地区}--{排序}---{类型}----{语言}-----{字母}-{页码}--{年份}/
4. 搜索：/s/{关键词}-------------/（第1页），翻页 /s/{关键词}----------{n}---/
5. 二级分类为独立 tid（电影=1、动作片=6、国产剧=13…），选择后作为列表 tid
6. 详情页线路 tab（data-dropdown-value）与其后的 module-list 集数块按顺序一一对应

核心优化（加载速度）：
- 详情页懒加载：不预解析所有集数 m3u8，详情秒开
- 多级缓存：首页10分钟 / 分类5分钟 / 详情5分钟(失败30秒) / 搜索3分钟 / 播放15分钟
- 连接池复用(HTTPAdapter 20/40) + keep-alive + gzip 自动解压
- 全链路短超时(8s/5s/4s) + 快速重试(0.2s) + 429限流等待(2s)
- 搜索 1-3 页并行抓取聚合去重（ThreadPoolExecutor）

核心优化（播放速度）：
- playerContent 按需解析 m3u8 直链 + 15分钟缓存
- 后台预取第1集 / 命中缓存后预取下一集
- 首选线路失效时，其余线路播放页并行探测自动切换
- 播放 header 带 UA + Referer，规避 CDN 防盗链

筛选：类型(二级分类独立tid) / 地区 / 年份 / 语言 / 字母 / 排序
图片：data-original/src 二级回退 + HTTPS 归一化
"""

import re
import json
import time
import threading
from urllib.parse import quote, unquote

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
HOST = "https://www.nivod.vip"

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

# 地区（站内 tab 顺序）
AREAS = ["大陆", "香港", "台湾", "日本", "韩国", "欧美", "英国", "泰国", "其它"]

# 语言（站内 tab 顺序）
LANGS = ["国语", "英语", "粤语", "韩语", "日语", "西班牙",
         "法语", "德语", "意大利语", "泰语", "其它"]

# 年份（站内 tab：2026-2011 + 更早）
YEARS = [str(y) for y in range(2026, 2010, -1)] + ["更早"]

# 字母（站内 tab：A-Z + 0-9）
LETTERS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["0-9"]

# 排序（站内 tab）
SORTS = [
    ("time_add", "添加时间"),
    ("time_update", "更新时间"),
    ("hits", "人气排序"),
    ("score", "评分排序"),
]

# 分类表（maccms 独立 tid，硬编码避免广告注入干扰）
# 结构：一级分类（首页导航）-> 二级分类（类型筛选，值为独立 tid）
CATS = [
    {"id": "1", "name": "电影", "subs": [
        ("6", "动作片"), ("7", "喜剧片"), ("8", "爱情片"), ("9", "科幻片"),
        ("10", "奇幻片"), ("11", "恐怖片"), ("12", "剧情片"), ("20", "战争片"),
        ("21", "纪录片"), ("26", "动画片"), ("22", "悬疑片"), ("23", "冒险片"),
        ("24", "犯罪片"), ("45", "惊悚片"), ("46", "歌舞片"), ("47", "灾难片"),
        ("48", "网络片"),
    ]},
    {"id": "2", "name": "剧集", "subs": [
        ("13", "国产剧"), ("14", "港台剧"), ("15", "日剧"), ("33", "韩剧"),
        ("16", "欧美剧"), ("34", "泰剧"), ("35", "新马剧"), ("25", "其他剧"),
    ]},
    {"id": "3", "name": "综艺", "subs": [
        ("27", "大陆综艺"), ("28", "港台综艺"), ("29", "日本综艺"), ("36", "韩国综艺"),
        ("30", "欧美综艺"), ("37", "新马泰综艺"), ("38", "其他综艺"),
    ]},
    {"id": "4", "name": "动漫", "subs": [
        ("31", "国产动漫"), ("32", "日本动漫"), ("39", "韩国动漫"), ("40", "港台动漫"),
        ("41", "新马泰动漫"), ("42", "欧美动漫"), ("43", "其他动漫"),
    ]},
]


def _build_filters(cat):
    """构建筛选器：类型(二级分类tid) / 地区 / 年份 / 语言 / 字母 / 排序"""
    subs = [{"n": name, "v": slug} for slug, name in cat["subs"]]
    filters = [{
        "key": "class", "name": "类型",
        "value": [{"n": "全部", "v": ""}] + subs,
    }]
    filters.append({
        "key": "area", "name": "地区",
        "value": [{"n": "全部", "v": ""}] + [{"n": a, "v": a} for a in AREAS],
    })
    filters.append({
        "key": "year", "name": "年份",
        "value": [{"n": "全部", "v": ""}] + [{"n": y, "v": y} for y in YEARS],
    })
    filters.append({
        "key": "lang", "name": "语言",
        "value": [{"n": "全部", "v": ""}] + [{"n": l, "v": l} for l in LANGS],
    })
    filters.append({
        "key": "letter", "name": "字母",
        "value": [{"n": "全部", "v": ""}] + [{"n": l, "v": l} for l in LETTERS],
    })
    filters.append({
        "key": "by", "name": "排序",
        "value": [{"n": "全部", "v": ""}] + [{"n": n, "v": v} for v, n in SORTS],
    })
    return filters


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

        self._warm_thread = None

        # 缓存容器 + 锁（线程安全）
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

    # ===== 网络工具（短超时 + 快速重试 + 429 限流等待） =====
    def _get(self, url, referer='', timeout=TIMEOUT_PAGE):
        headers = {'Connection': 'keep-alive'}
        if referer:
            headers['Referer'] = referer
        for attempt in range(2):
            try:
                r = self.session.get(url, timeout=timeout, headers=headers)
                if r.status_code == 429:
                    time.sleep(2.0)
                    continue
                r.raise_for_status()
                r.encoding = r.apparent_encoding or 'utf-8'
                return r
            except Exception:
                if attempt == 0:
                    time.sleep(0.2)
                else:
                    return None
        return None

    def _get_text(self, url, referer='', timeout=TIMEOUT_PAGE):
        r = self._get(url, referer, timeout)
        return r.text if r is not None else ""

    # ===== 缓存（超过 512 项整体清空，防止内存膨胀） =====
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

    # ===== HTML 解析工具 =====
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
    def _abs(u):
        u = (u or '').strip()
        if not u:
            return ''
        if u.startswith('//'):
            return 'https:' + u
        if u.startswith('/'):
            return HOST + u
        if not u.startswith('http'):
            return HOST + '/' + u
        return u

    @staticmethod
    def _extract_id(href):
        m = re.search(r'/(\d{4,10})(?:/|\.html)', (href or '').strip())
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
        for attr in ('data-original', 'data-src', 'src'):
            val = str(el.get(attr) or '').strip()
            if val and '/loading' not in val:
                return Spider._abs(val)
        return ''

    # ===== 列表页 URL 构造（12 字段拼接） =====
    @staticmethod
    def _cat_url(tid, page, ext):
        parts = [''] * 12
        parts[0] = str(tid)
        parts[1] = (ext.get('area') or '').strip()
        parts[2] = (ext.get('by') or '').strip()
        parts[4] = (ext.get('lang') or '').strip()
        parts[5] = (ext.get('letter') or '').strip()
        if page > 1:
            parts[8] = str(page)
        parts[11] = (ext.get('year') or '').strip()
        return HOST + '/k/' + quote('-'.join(parts)) + '/'

    # ===== 卡片解析（列表页 module-poster-item） =====
    def _parse_cards(self, html, limit=36):
        if not html:
            return []
        soup = self._soup(html)
        if soup is None:
            return []
        items = {}
        for a in soup.select('a.module-poster-item'):
            href = str(a.get('href') or '')
            vid = self._extract_id(href)
            if not vid:
                continue
            title = str(a.get('title') or '').strip()
            if not title:
                t = a.select_one('.module-poster-item-title')
                if t:
                    title = t.get_text(strip=True)
            if not title:
                continue
            img = a.select_one('img')
            pic = self._pick_pic(img)
            remarks = ''
            note = a.select_one('.module-item-note')
            if note:
                remarks = note.get_text(strip=True)
            if vid not in items:
                items[vid] = {
                    'vod_id': vid,
                    'vod_name': self._clean_name(title),
                    'vod_pic': pic,
                    'vod_remarks': remarks,
                }
        return list(items.values())[:limit]

    # ============================================================
    # 首页
    # ============================================================
    def homeContent(self, filter=False):
        vod_list = self._home_vodlist()
        return {
            "class": ALL_CLASSES,
            "filters": ALL_FILTERS,
            "list": vod_list,
        }

    def homeVideoContent(self):
        return {"list": self._home_vodlist()}

    def _home_vodlist(self):
        now = int(time.time())
        with self._lock:
            if self._home_cache and now - self._home_cache_time < TTL_HOME:
                return self._home_cache[:60]
        try:
            html = self._get_text(HOST)
            vod_list = self._parse_cards(html, limit=60)
            if vod_list:
                with self._lock:
                    self._home_cache = vod_list
                    self._home_cache_time = int(time.time())
            return vod_list[:60]
        except Exception:
            return []

    # ============================================================
    # 分类列表（含二级分类筛选）
    # ============================================================
    def _empty_category(self, page=1):
        return {"list": [], "page": page, "pagecount": 1, "limit": 36, "total": 0}

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

            # 二级分类：选中类型(tid) 时以子 tid 作为列表 tid
            ctid = (ext.get('class') or '').strip() or str(tid)

            ckey = "%s|%d|%s" % (ctid, page, json.dumps(ext, ensure_ascii=False, sort_keys=True))
            cached = self._cache_get(self._cat_cache, ckey, TTL_CAT)
            if cached is not None:
                return cached

            url = self._cat_url(ctid, page, ext)
            html = self._get_text(url)
            if not html:
                return self._empty_category(page)

            # 分页数：从 #page 的页码链接取最大值
            pagecount = 1
            soup = self._soup(html)
            if soup is not None:
                nums = []
                for el in soup.select('#page .page-number'):
                    try:
                        nums.append(int(el.get_text(strip=True)))
                    except Exception:
                        pass
                if nums:
                    pagecount = max(nums)
                vod_list = self._parse_cards(html, limit=36)
            else:
                vod_list = []

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
    # 详情页（懒加载：只返回播放链接，不解析 m3u8）
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

        # 后台预取第1集 m3u8（播放秒开）
        if result.get("list"):
            self._prefetch_play(result["list"][0])
        return result

    def _fetch_detail(self, vid):
        html = self._get_text(f"{HOST}/nivod/{vid}/")
        if not html:
            return {"list": []}
        soup = self._soup(html)
        if soup is None:
            return {"list": []}

        # --- 基本信息 ---
        name = ''
        h1 = soup.select_one('h1')
        if h1:
            name = self._clean_name(h1.get_text(strip=True))
        if not name:
            t1 = soup.select_one('.module-info-heading h1') or soup.title
            if t1:
                name = self._clean_name(t1.get_text(strip=True))

        pic = ''
        img = soup.select_one('.module-info-poster img')
        if img:
            pic = self._pick_pic(img)

        # 导演 / 主演
        director = ''
        actor = ''
        for item in soup.select('.module-info-item'):
            t = item.select_one('.module-info-item-title')
            if t is None:
                continue
            label = t.get_text(strip=True)
            if label == '导演：':
                director = ' '.join(a.get_text(strip=True) for a in item.select('a'))
            elif label == '主演：':
                actor = ' '.join(a.get_text(strip=True) for a in item.select('a'))

        # 标签：年份 / 地区 / 类型
        # 地区与类型直接解析标签 href 的 12 字段（比匹配固定列表更通用，
        # 例如地区“墨西哥”不在筛选 tab 的 AREAS 中）
        year = ''
        area = ''
        type_names = []
        for a in soup.select('.module-info-tag-link a'):
            tag = str(a.get('title') or '').strip() or a.get_text(strip=True)
            href = str(a.get('href') or '')
            if not tag:
                continue
            fields = []
            m = re.match(r'/k/([^/]+)/', href)
            if m:
                try:
                    fields = unquote(m.group(1)).split('-')
                except Exception:
                    fields = []
            if re.fullmatch(r'\d{4}', tag):
                if not year:
                    year = tag
            elif len(fields) > 1 and fields[1]:
                if not area:
                    area = fields[1]
            elif len(fields) > 3 and fields[3]:
                type_names.append(fields[3])
        type_name = '/'.join(type_names[:3])

        # 简介
        content = ''
        intro = soup.select_one('.module-info-introduction-content')
        if intro:
            content = intro.get_text(strip=True)

        # 备注：从卡片样式提取（无则留空由客户端显示集数）
        remarks = ''

        # --- 播放源与集数（线路 tab 与其后的 module-list 按顺序对应） ---
        tabs = soup.select('.module-tab-item[data-dropdown-value]')
        groups = []
        if tabs:
            tab_div = tabs[0]
            while tab_div is not None and tab_div is not soup \
                    and 'module-tab' not in (tab_div.get('class') or []):
                tab_div = tab_div.parent
            outer = tab_div.parent if tab_div is not None else None
            if outer is not None:
                node = outer.find_next_sibling()
                while node is not None:
                    if node.name == 'div' and 'module-list' in (node.get('class') or []):
                        links = node.select('.module-play-list-link')
                        if not links:
                            break
                        eps = []
                        for a in links:
                            ep_url = self._abs(str(a.get('href') or ''))
                            if ep_url:
                                eps.append((a.get_text(strip=True) or '播放', ep_url))
                        if eps:
                            groups.append(eps)
                    node = node.find_next_sibling()

        play_from, play_url = '', ''
        for idx, eps in enumerate(groups):
            src_name = tabs[idx].get('data-dropdown-value') if idx < len(tabs) else f'线路{idx + 1}'
            ep_parts = [f"{n}${u}" for n, u in eps]
            play_from = (play_from + '$$$' + src_name) if play_from else src_name
            play_url = (play_url + '$$$' + '#'.join(ep_parts)) if play_url else '#'.join(ep_parts)

        detail = {
            "vod_id": vid,
            "vod_name": name or f"视频{vid}",
            "vod_pic": pic or HOST,
            "type_name": type_name,
            "vod_year": year,
            "vod_area": area,
            "vod_remarks": remarks or '',
            "vod_director": director,
            "vod_actor": actor,
            "vod_content": content,
            "vod_play_from": play_from or '默认',
            "vod_play_url": play_url or '',
        }
        return {"list": [detail]}

    # ============================================================
    # 播放解析（m3u8 直链 + 缓存 + 预取 + 多线路并行兜底）
    # ============================================================
    def _resolve_play(self, play_url):
        cached = self._cache_get(self._play_cache, play_url, TTL_PLAY)
        if cached:
            return cached
        real = ''
        try:
            text = self._get_text(play_url, referer=HOST + '/', timeout=TIMEOUT_PLAY)
            if text:
                # 主：player_xxxx JSON 的 url 字段
                m = re.search(r'var\s+player_\w+\s*=\s*(\{.*?\})\s*[;<]', text, re.S)
                if m:
                    try:
                        data = json.loads(m.group(1))
                        u = (data.get('url') or '').replace('\\/', '/').strip()
                        if u:
                            real = self._abs(u)
                    except Exception:
                        pass
                # 备选1：显式 m3u8 链接
                if not real:
                    m2 = re.search(r'"url"\s*:\s*"([^"]+\.m3u8[^"]*)"', text)
                    if m2:
                        real = self._abs(m2.group(1).replace('\\/', '/'))
                # 备选2：https 视频链接（m3u8/mp4）
                if not real:
                    m3 = re.search(r'"url"\s*:\s*"(https?://[^"]+)"', text)
                    if m3:
                        u = m3.group(1).replace('\\/', '/').strip()
                        if '.m3u8' in u or '.mp4' in u:
                            real = self._abs(u)
        except Exception:
            real = ''
        if real:
            self._cache_set(self._play_cache, play_url, real, TTL_PLAY)
        return real

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

    def _prefetch_next(self, play_url, vod_id):
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
                        next_parts = items[i + 1].split("$", 1)
                        if len(next_parts) == 2 and next_parts[1]:
                            next_url = self._abs(next_parts[1])
                            with self._lock:
                                if (self._cache_get(self._play_cache, next_url, TTL_PLAY)
                                        or next_url in self._prefetching):
                                    return
                                self._prefetching.add(next_url)

                            def _job(url=next_url):
                                try:
                                    self._resolve_play(url)
                                except Exception:
                                    pass
                                finally:
                                    with self._lock:
                                        self._prefetching.discard(url)

                            threading.Thread(target=_job, daemon=True).start()
                    return

    def _play_payload(self, playurl):
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
            self._prefetch_next(play_url, play_url)
            return self._play_payload(cached)

        m3u8 = self._resolve_play(play_url)
        if m3u8:
            self._prefetch_next(play_url, play_url)
            return self._play_payload(m3u8)

        # 首选线路失效：其余线路播放页并行探测（限时）
        alts = self._alt_play_urls(play_url)
        if alts and ThreadPoolExecutor is not None:
            try:
                with ThreadPoolExecutor(max_workers=3) as ex:
                    futs = [ex.submit(self._resolve_play, u) for u in alts]
                    for f in as_completed(futs, timeout=TIMEOUT_PLAY + 1):
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

        # 全部失败：交回客户端走官方播放页（parse=1）
        return {
            "parse": 1,
            "playUrl": "",
            "url": play_url,
            "header": {"User-Agent": UA, "Referer": HOST + "/"},
        }

    def _alt_play_urls(self, play_url, limit=6):
        m = re.search(r'/niplay/(\d+)-', play_url)
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

    # ============================================================
    # 搜索（独立 session 绕过限流 + 按页序聚合 + 相关性排序）
    # 站点限制“搜索时间间隔为 3 秒”（按 cookie 维度限流）：
    # 同一 session 第二次搜索即返回空页（提示“频繁操作”）。
    # 因此每次搜索使用全新 session（独立 cookie），实测可连续成功。
    # ============================================================
    def _search_page(self, url, timeout=TIMEOUT_PAGE):
        """用全新 session 抓取搜索页（绕过 cookie 维度限流）"""
        sess = requests.Session()
        sess.headers.update(self.headers)
        sess.verify = False
        try:
            r = sess.get(url, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2.0)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or 'utf-8'
            return r.text
        except Exception:
            return ""

    def _parse_search_cards(self, html, limit=36):
        if not html:
            return []
        soup = self._soup(html)
        if soup is None:
            return []
        items = {}
        for card in soup.select('.module-card-item'):
            a = card.select_one('.module-card-item-poster') or card.select_one('a[href*="/nivod/"]')
            if a is None:
                continue
            href = str(a.get('href') or '')
            vid = self._extract_id(href)
            if not vid:
                continue
            img = a.select_one('img')
            title = str(a.get('title') or '').strip()
            if not title and img:
                title = str(img.get('alt') or '').strip()
            if not title:
                t = card.select_one('.module-card-item-title')
                if t:
                    title = t.get_text(strip=True)
            if not title:
                continue
            pic = self._pick_pic(img)
            remarks = ''
            note = card.select_one('.module-item-note')
            if note:
                remarks = note.get_text(strip=True)
            if vid not in items:
                items[vid] = {
                    'vod_id': vid,
                    'vod_name': self._clean_name(title),
                    'vod_pic': pic,
                    'vod_remarks': remarks,
                }
        return list(items.values())[:limit]

    def _relevance_key(self, name, key):
        """相关性加权：完全等于 > 起始包含 > 包含 > 其他"""
        name = (name or '').strip()
        key = (key or '').strip()
        if not name or not key:
            return 10
        if name == key:
            return 0
        if name.startswith(key):
            return 1
        if key in name:
            return 2
        # 关键词拆字匹配（如“仙逆”命中“逆仙而上”）
        if len(key) >= 2 and all(ch in name for ch in key):
            return 3
        return 10

    def searchContent(self, key, quick):
        key = (key or '').strip()
        if not key:
            return {"list": []}
        cache_key = 's:' + key
        cached = self._cache_get(self._search_cache, cache_key, TTL_SEARCH)
        if cached is not None:
            return cached

        wd = quote(key)
        urls = [
            f"{HOST}/s/{wd}-------------/",
            f"{HOST}/s/{wd}----------2---/",
            f"{HOST}/s/{wd}----------3---/",
        ]

        # 并行抓取（每页独立 session），但按页顺序收集
        texts = [''] * len(urls)
        if ThreadPoolExecutor is not None:
            try:
                with ThreadPoolExecutor(max_workers=3) as ex:
                    futs = {i: ex.submit(self._search_page, u) for i, u in enumerate(urls)}
                    for i, f in futs.items():
                        try:
                            texts[i] = f.result(timeout=TIMEOUT_PAGE)
                        except Exception:
                            texts[i] = ''
            except Exception:
                pass
        else:
            texts = [self._search_page(u) for u in urls]

        # 检测是否被限流（空页 + 频繁提示），等待 3 秒重试第 1 页
        if texts and not texts[0]:
            pass
        if (texts and texts[0]
                and 'module-card-item' not in texts[0]
                and '频繁' in texts[0]):
            time.sleep(3.0)
            texts[0] = self._search_page(urls[0])

        # 按页序合并去重 + 相关性排序
        items = {}
        for t in texts:
            for v in self._parse_search_cards(t, limit=36):
                items.setdefault(v['vod_id'], v)
        ranked = sorted(
            items.values(),
            key=lambda v: (self._relevance_key(v['vod_name'], key), int(v['vod_id'] or 0)),
        )
        result = {"list": ranked[:36]}
        self._cache_set(self._search_cache, cache_key, result, TTL_SEARCH)
        return result


# ============================================================
# 本地自测（TVBox 环境外运行）
# ============================================================
if __name__ == '__main__':
    import json as _json

    s = Spider()
    print("=== homeContent ===")
    h = s.homeContent()
    print("分类数:", len(h['class']), "| 首页卡片:", len(h['list']))
    if h['list']:
        print("  示例:", h['list'][0]['vod_name'], h['list'][0]['vod_remarks'])

    print("\n=== categoryContent(电影, 第1页) ===")
    c = s.categoryContent('1', '1', 1, {})
    print("列表数:", len(c['list']), "| pagecount:", c['pagecount'])
    if c['list']:
        print("  示例:", c['list'][0]['vod_name'], c['list'][0]['vod_remarks'])

    print("\n=== categoryContent(电影+大陆+2026, 筛选) ===")
    c2 = s.categoryContent('1', '1', 1, {'area': '大陆', 'year': '2026'})
    print("列表数:", len(c2['list']))
    if c2['list']:
        print("  示例:", c2['list'][0]['vod_name'])

    print("\n=== categoryContent(二级分类: 动作片 tid=6) ===")
    c3 = s.categoryContent('1', '1', 1, {'class': '6'})
    print("列表数:", len(c3['list']))

    print("\n=== detailContent ===")
    if c['list']:
        d = s.detailContent([c['list'][0]['vod_id']])
        v = d['list'][0]
        print("名称:", v['vod_name'], "| 年份:", v['vod_year'], "| 地区:", v['vod_area'])
        print("导演:", v['vod_director'][:30], "| 主演:", v['vod_actor'][:30])
        print("线路:", v['vod_play_from'])
        pu = v['vod_play_url']
        print("集数示例:", pu.split('$$$')[0].split('#')[:3])

    print("\n=== playerContent（解析 m3u8 直链） ===")
    if c['list']:
        v0 = s.detailContent([c['list'][0]['vod_id']])['list'][0]
        first = v0['vod_play_url'].split('$$$')[0].split('#')[0].split('$')[-1]
        p = s.playerContent('', first, [])
        print("parse:", p['parse'], "| url:", p['url'][:80])

    print("\n=== searchContent('仙逆') ===")
    r = s.searchContent('仙逆', 0)
    print("结果数:", len(r['list']))
    for v in r['list'][:3]:
        print("  ", v['vod_name'], v['vod_remarks'])
