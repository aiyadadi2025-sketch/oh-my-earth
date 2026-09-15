# -*- coding: utf-8 -*-
"""
无极电影网 TVBox Spider — 兼容 FongMi/TVBox (T3) 与 WebHomeTV / PeekPro (T4)
站点: https://www.yahanof.com/  (MacCMS v10)

本版优化:
  1. 一级分类:电影 → 电视剧 → 动漫 → 综艺(按要求)
  2. 加载速度:
     - Session 连接池扩容(32连接/64最大)、紧凑超时
     - 首页/详情/播放全链路 TTL 缓存
     - 分类页 CF 拦截智能降级(首页板块精准映射)
     - 正则预编译 + 惰性提取(只取必要字段)
  3. 播放速度:
     - 直链直出(parse=0)、播放链接 5 分钟缓存
     - player_aaaa 快速解析(只读前 20KB)
     - 多线路自动去重优选
  4. 搜索功能:
     - 多格式尝试:GET ?wd= / 13段 / 14段 URL
     - 本地兜底:首页全量卡片 + RSS + 简介匹配
     - 空关键词快速返回、结果去重
  5. 图片:兼容 data-original / data-echo / data-src / src-lazy / src
     五种懒加载属性,// 自动补 https,空图回退
"""

import sys
import json
import re
import time
import threading

sys.path.append('..')

# ===== 兼容导入 =====
try:
    from base.spider import Spider
    _HAS_REQUESTS = False
except ImportError:
    import requests
    import urllib3
    urllib3.disable_warnings()
    _HAS_REQUESTS = True

    class Spider:
        def fetch(self, url, headers=None, timeout=(3, 6), **kw):
            r = requests.get(url, headers=headers, timeout=timeout,
                             verify=False, **kw)
            r.encoding = 'utf-8'
            return r

from urllib.parse import quote, urlencode


# ============================================================
# 常量
# ============================================================

HOST = "https://www.yahanof.com"
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

# 紧凑超时:连接 3s,读取 5s,长尾请求直接放弃
_FAST_TIMEOUT = (3, 5)
# 详情页单独超时(HTML较大)
_DETAIL_TIMEOUT = (4, 8)
# 播放页超时(快速取前20KB即可)
_PLAY_TIMEOUT = (3, 6)

# ---- 一级分类(顺序:电影 → 电视剧 → 动漫 → 综艺)----
CLASSES = [
    {"type_id": "1", "type_name": "电影"},
    {"type_id": "2", "type_name": "电视剧"},
    {"type_id": "4", "type_name": "动漫"},
    {"type_id": "3", "type_name": "综艺"},
]

# 分类ID -> 名称
_CLASS_NAME = {c["type_id"]: c["type_name"] for c in CLASSES}

# 首页板块名关键词 -> 分类ID(用于降级映射)
_SECTION_KEYWORDS = {
    "1": ["电影"],
    "2": ["电视剧", "剧"],
    "4": ["动漫", "动画"],
    "3": ["综艺", "真人秀"],
}

# ---- 筛选器 ----
_CLASS_FILTER = {"key": "class", "name": "类型", "value": [
    {"n": "全部", "v": ""},
    {"n": "动作", "v": "动作"}, {"n": "喜剧", "v": "喜剧"},
    {"n": "爱情", "v": "爱情"}, {"n": "科幻", "v": "科幻"},
    {"n": "恐怖", "v": "恐怖"}, {"n": "剧情", "v": "剧情"},
    {"n": "战争", "v": "战争"}, {"n": "纪录", "v": "纪录"},
    {"n": "动漫", "v": "动漫"}, {"n": "悬疑", "v": "悬疑"},
    {"n": "犯罪", "v": "犯罪"}, {"n": "奇幻", "v": "奇幻"},
    {"n": "冒险", "v": "冒险"}, {"n": "武侠", "v": "武侠"},
    {"n": "古装", "v": "古装"}, {"n": "家庭", "v": "家庭"},
]}

_AREA_FILTER = {"key": "area", "name": "地区", "value": [
    {"n": "全部", "v": ""},
    {"n": "大陆", "v": "大陆"}, {"n": "香港", "v": "香港"},
    {"n": "台湾", "v": "台湾"}, {"n": "美国", "v": "美国"},
    {"n": "韩国", "v": "韩国"}, {"n": "日本", "v": "日本"},
    {"n": "泰国", "v": "泰国"}, {"n": "其他", "v": "其他"},
]}

_LANG_FILTER = {"key": "lang", "name": "语言", "value": [
    {"n": "全部", "v": ""},
    {"n": "国语", "v": "国语"}, {"n": "粤语", "v": "粤语"},
    {"n": "英语", "v": "英语"}, {"n": "日语", "v": "日语"},
    {"n": "韩语", "v": "韩语"}, {"n": "其他", "v": "其他"},
]}

_YEAR_FILTER = {"key": "year", "name": "年份", "value": [
    {"n": "全部", "v": ""},
    {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
    {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"},
    {"n": "2022", "v": "2022"}, {"n": "2021", "v": "2021"},
    {"n": "2020", "v": "2020"},
]}

_BY_FILTER = {"key": "by", "name": "排序", "value": [
    {"n": "最新", "v": "time"}, {"n": "最热", "v": "hits"}, {"n": "评分", "v": "score"},
]}

FILTERS = {}
for c in CLASSES:
    FILTERS[c["type_id"]] = [_CLASS_FILTER, _AREA_FILTER, _LANG_FILTER,
                             _YEAR_FILTER, _BY_FILTER]

# 缓存时长(秒)
_HOME_TTL = 900      # 首页板块缓存 15 分钟
_DETAIL_TTL = 600    # 详情页缓存 10 分钟
_PLAY_TTL = 300      # 播放链接缓存 5 分钟
_RSS_TTL = 1800      # RSS 缓存 30 分钟

# ============================================================
# 预编译正则(启动时一次编译,运行时零开销)
# ============================================================

# 列表页卡片
_CARD_RE = re.compile(
    r'<a class="stui-vodlist__thumb[^"]*"(.*?)>(.*?)</a>', re.S)

# 文字榜单
_RANK_RE = re.compile(
    r'<li class="bottom-line-dot"><a href="/voddetail/(\d+)\.html" '
    r'title="([^"]*)"><span class="text-muted pull-right hidden-md">'
    r'([^<]*)</span>', re.S)

# 首页板块标题(兼容有图无图)
_SECTION_RE = re.compile(
    r'<h3 class="title">(?:<img[^>]*/>)?'
    r'<a href="/vodshow/(\d+)-----------\.html">([^<]+)</a></h3>', re.S)

# 详情页线路面板
_PANEL_RE = re.compile(
    r'<h3 class="title">(?:<img[^>]*/>)?([^<]+)</h3>.*?'
    r'<ul class="stui-content__playlist[^"]*">(.*?)</ul>', re.S)

# 播放页 player_aaaa (非贪婪,只取第一个)
_PLAYER_RE = re.compile(r'var\s+player_aaaa\s*=\s*(\{[^}]+\})', re.S)

# 图片懒加载属性(按优先级排列)
_PIC_ATTRS = ['data-original', 'data-echo', 'data-src', 'data-lazy', 'src']
_PIC_ATTR_RE = re.compile(
    r'(?:data-original|data-echo|data-src|data-lazy|src)\s*=\s*"([^"]+)"', re.I)

# href 提取
_HREF_RE = re.compile(r'href="/voddetail/(\d+)\.html"')
_TITLE_RE = re.compile(r'title="([^"]*)"')
_REMARK_RE = re.compile(r'pic-text text-right">([^<]*)<')

# 分页信息
_PAGE_TOTAL_RE = re.compile(r'(?:data-total|mac_total)[^0-9]*(\d+)')

# 播放页剧集
_EP_RE = re.compile(
    r'href="/vodplay/(\d+)-(\d+)-(\d+)\.html"[^>]*>([^<]+)<')


# ============================================================
# Spider 主类
# ============================================================

class Spider(Spider):

    def getName(self):
        return "无极电影"

    # ===== 初始化 =====
    def init(self, extend=""):
        if isinstance(extend, list):
            self.extend = ""
        else:
            self.extend = extend or ""

        self.header = {
            "User-Agent": UA,
            "Referer": HOST + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

        # Session 连接池扩容 + 重试
        if _HAS_REQUESTS:
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            self._session = requests.Session()
            retry = Retry(total=1, connect=1, read=0,
                          backoff_factor=0.1, status_forcelist=[500, 502, 503])
            adapter = HTTPAdapter(pool_connections=32, pool_maxsize=64,
                                  max_retries=retry, pool_block=False)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
        else:
            self._session = None

        # 缓存(用 dict + 时间戳,比 LRU 更轻量)
        self._home_cache = None       # {sections, list, ts}
        self._home_cache_time = 0
        self._rss_cache = []
        self._rss_cache_time = 0
        self._detail_cache = {}       # vod_id -> (ts, vod_dict)
        self._play_cache = {}         # play_url -> (ts, result_dict)
        self._search_cache = {}       # key -> (ts, list)

        self._lock = threading.Lock()
        # 后台预热标记
        self._warmed = False

    # ===== 网络工具 =====
    def _get(self, url, timeout=None):
        """GET 返回文本;异常/超时/CF拦截返回 None"""
        if timeout is None:
            timeout = _FAST_TIMEOUT
        try:
            if self._session is not None:
                r = self._session.get(url, headers=self.header, timeout=timeout)
            else:
                r = self.fetch(url, headers=self.header, timeout=timeout)
            if r.status_code != 200:
                return None
            # 快速检测 CF 拦截(只看前 500 字节)
            try:
                text = r.text
            except Exception:
                text = r.content.decode("utf-8", "ignore")
            if self._blocked(text):
                return None
            return text
        except Exception:
            return None

    def _get_partial(self, url, max_bytes=20480, timeout=None):
        """只取前 max_bytes 字节(用于播放页快速解析 player_aaaa)"""
        if timeout is None:
            timeout = _PLAY_TIMEOUT
        try:
            if self._session is not None:
                r = self._session.get(url, headers=self.header, timeout=timeout, stream=True)
            else:
                r = self.fetch(url, headers=self.header, timeout=timeout)
            if r.status_code != 200:
                return None
            # 流式读取前 N 字节
            chunks = []
            received = 0
            for chunk in r.iter_content(chunk_size=4096):
                chunks.append(chunk)
                received += len(chunk)
                if received >= max_bytes:
                    break
            data = b''.join(chunks)
            text = data.decode("utf-8", "ignore")
            if self._blocked(text):
                return None
            return text
        except Exception:
            return None

    def _blocked(self, text):
        """检测 Cloudflare 拦截(只检查前 800 字符)"""
        if not text:
            return True
        head = text[:800]
        return ("Just a moment" in head or "challenge-platform" in head
                or "cf-chl" in head or "Attention Required" in head
                or "安全验证" in head)

    def _strip(self, s):
        return re.sub(r'<[^>]+>', '', s or "").replace("&nbsp;", " ").strip()

    def _extract_pic(self, *sources):
        """从多个 HTML 片段提取第一张可用图片,自动补 https"""
        for src in sources:
            if not src:
                continue
            m = _PIC_ATTR_RE.search(src)
            if m:
                pic = m.group(1).strip()
                if pic and not pic.startswith("data:") and "loading" not in pic.lower():
                    if pic.startswith("//"):
                        pic = "https:" + pic
                    return pic
        return ""

    # ===== 卡片解析(预编译正则,速度更快)=====
    def _parse_card(self, m):
        open_tag = m.group(1) or ""
        inner = m.group(2) or ""
        hm = _HREF_RE.search(open_tag)
        vid = hm.group(1) if hm else ""
        tm = _TITLE_RE.search(open_tag)
        name = self._strip(tm.group(1)) if tm else ""
        pic = self._extract_pic(open_tag, inner)
        rm = _REMARK_RE.search(inner)
        remarks = self._strip(rm.group(1)) if rm else ""
        return {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_remarks": remarks or "HD",
        }

    def _rank_card(self, m):
        return {
            "vod_id": m.group(1),
            "vod_name": self._strip(m.group(2)),
            "vod_pic": "",
            "vod_remarks": self._strip(m.group(3)) or "HD",
        }

    def _cards_from(self, text):
        return [self._parse_card(m) for m in _CARD_RE.finditer(text)]

    # ===== 首页(单请求 + 缓存 + 预构建全量卡片)=====
    def _fetch_home(self):
        """抓首页并缓存。返回 {sections, all_cards, ts}"""
        now = int(time.time())
        # 快速路径:读缓存(双检锁模式)
        with self._lock:
            if self._home_cache and now - self._home_cache_time < _HOME_TTL:
                return self._home_cache

        text = self._get(HOST + "/")
        if not text:
            # CF 拦截也返回 None,调用方自行降级
            return None

        # 标题位置 -> (tid, name)
        heads = [(m.start(), m.group(1), m.group(2))
                 for m in _SECTION_RE.finditer(text)]

        # 所有卡片(按位置排序)
        cards = [(m.start(), self._parse_card(m)) for m in _CARD_RE.finditer(text)]
        cards += [(m.start(), self._rank_card(m)) for m in _RANK_RE.finditer(text)]
        cards.sort(key=lambda x: x[0])

        # 按标题分板块
        sections = []
        all_cards_dict = {}
        for i, (pos, tid, name) in enumerate(heads):
            end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
            seg_cards = []
            for cpos, c in cards:
                if pos <= cpos < end and c["vod_id"]:
                    if c["vod_id"] not in all_cards_dict:
                        all_cards_dict[c["vod_id"]] = c
                    seg_cards.append(c)
            sections.append((tid, name, seg_cards))

        all_cards = list(all_cards_dict.values())
        result = {"sections": sections, "all_cards": all_cards, "ts": now}

        with self._lock:
            self._home_cache = result
            self._home_cache_time = now
        return result

    def _fetch_rss(self):
        """rss.xml 兜底数据"""
        now = int(time.time())
        with self._lock:
            if self._rss_cache and now - self._rss_cache_time < _RSS_TTL:
                return self._rss_cache

        text = self._get(HOST + "/rss.xml")
        items = []
        if text and not self._blocked(text):
            for m in re.finditer(r'<item>(.*?)</item>', text, re.S):
                seg = m.group(1)
                title = re.search(r'<title>(.*?)</title>', seg, re.S)
                link = re.search(r'<link>(.*?)</link>', seg, re.S)
                desc = re.search(r'<description>(.*?)</description>', seg, re.S)
                if not title or not link:
                    continue
                mid = re.search(r'/voddetail/(\d+)\.html', link.group(1))
                if not mid:
                    continue
                desc_text = desc.group(1) if desc else ""
                pic = self._extract_pic(desc_text) if desc_text else ""
                items.append({
                    "vod_id": mid.group(1),
                    "vod_name": title.group(1).strip(),
                    "vod_pic": pic,
                    "vod_remarks": "最新更新",
                    "_desc": desc_text,
                })
        with self._lock:
            self._rss_cache = items
            self._rss_cache_time = now
        return items

    # ===== 分类 URL 构造 =====
    def _show_url(self, tid, pg=1, cls="", area="", lang="", year="", by=""):
        """MacCMS v10 11段格式: tid-class-area-lang-year-letter-page-by----.html"""
        parts = [
            str(tid),
            cls or "",
            area or "",
            lang or "",
            year or "",
            "",  # letter
            str(pg) if pg and int(pg) > 1 else "",
            by or "",
        ]
        # 基础 8 段 + 尾 3 段 = 11 短横
        return HOST + "/vodshow/" + "-".join(parts) + "---.html"

    def _search_urls(self, key):
        """生成多种搜索URL尝试(按优先级)"""
        wd = quote(key, safe="")
        return [
            # 1. GET 参数方式(最常见)
            HOST + "/vodsearch/-------------.html?wd=" + wd,
            # 2. 13段路径式
            HOST + "/vodsearch/" + wd + "-" * 12 + ".html",
            # 3. 14段路径式
            HOST + "/vodsearch/" + wd + "-" * 13 + ".html",
            # 4. 简化版
            HOST + "/vodsearch/" + wd + ".html",
        ]

    # ===== 分类降级(智能映射首页板块)=====
    def _fallback_category(self, tid):
        """分类页被拦截时,用首页板块精准映射兜底"""
        home = self._fetch_home()

        if not home:
            rss = self._fetch_rss()
            return rss[:60]

        keywords = _SECTION_KEYWORDS.get(tid, [])
        cat_name = _CLASS_NAME.get(tid, "")

        # 策略1:板块名关键词匹配(如"最新电影" → 电影分类)
        matched_cards = []
        seen = set()
        for stid, sname, seg_cards in home["sections"]:
            # 板块分类ID直接匹配
            if stid == tid:
                for c in seg_cards:
                    if c["vod_id"] and c["vod_id"] not in seen:
                        seen.add(c["vod_id"])
                        matched_cards.append(c)
                continue
            # 板块名关键词匹配
            for kw in keywords:
                if kw in sname and "榜" not in sname:
                    for c in seg_cards:
                        if c["vod_id"] and c["vod_id"] not in seen:
                            seen.add(c["vod_id"])
                            matched_cards.append(c)
                    break

        if matched_cards:
            return matched_cards

        # 策略2:返回全部首页卡片
        return home["all_cards"][:80]

    # ============================================================
    # 首页
    # ============================================================

    def homeContent(self, filter):
        return {"class": CLASSES, "filters": FILTERS}

    def homeVideoContent(self):
        """首页推荐:按 电影→电视剧→动漫→综艺 顺序"""
        home = self._fetch_home()
        if home:
            result = []
            seen = set()
            # 按要求顺序的主板块
            main_order = ["最新电影", "最新电视剧", "最新动漫", "最新综艺"]
            # 先收集主板块
            main_sections = {sname: cards for _, sname, cards in home["sections"]}
            for sname in main_order:
                cards = main_sections.get(sname, [])
                for c in cards:
                    if c["vod_id"] and c["vod_id"] not in seen:
                        seen.add(c["vod_id"])
                        result.append(c)
            # 再补其他板块
            for _, sname, seg_cards in home["sections"]:
                if sname not in main_order:
                    for c in seg_cards:
                        if c["vod_id"] and c["vod_id"] not in seen:
                            seen.add(c["vod_id"])
                            result.append(c)
            if result:
                return {"list": result[:80]}

        # 降级:rss
        rss = self._fetch_rss()
        return {"list": rss[:80]}

    # ============================================================
    # 分类列表
    # ============================================================

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = int(pg or 1)
            if page < 1:
                page = 1

            # 解析筛选参数
            ext = {}
            if extend:
                if isinstance(extend, dict):
                    ext = extend
                elif isinstance(extend, str):
                    try:
                        ext = json.loads(extend)
                    except Exception:
                        ext = {}

            cls = ext.get("class", "")
            area = ext.get("area", "")
            lang = ext.get("lang", "")
            year = ext.get("year", "")
            by = ext.get("by", "time")

            # 有筛选条件时,降级返回(因为CF拦截下筛选无效)
            has_filter = bool(cls or area or lang or year or (by and by != "time"))

            # 尝试正常分类页
            if not has_filter and page <= 1:
                url = self._show_url(tid, page, cls, area, lang, year, by)
                text = self._get(url)
                if text and not self._blocked(text):
                    vods = self._cards_from(text)
                    total = len(vods)
                    pm = _PAGE_TOTAL_RE.search(text)
                    pagecount = 1
                    if pm:
                        try:
                            pagecount = max(1, (int(pm.group(1)) + 19) // 20)
                        except Exception:
                            pass
                    return {"list": vods, "page": page, "pagecount": pagecount,
                            "limit": 20, "total": total}

            # 降级:首页板块映射
            cards = self._fallback_category(tid)
            total = len(cards)
            pagecount = max(1, (total + 19) // 20)

            # 简单分页
            start = (page - 1) * 20
            end = start + 20
            page_cards = cards[start:end]

            return {"list": page_cards, "page": page, "pagecount": pagecount,
                    "limit": 20, "total": total}
        except Exception:
            return {"list": [], "page": 1, "pagecount": 1,
                    "limit": 20, "total": 0}

    # ============================================================
    # 详情页
    # ============================================================

    def detailContent(self, ids):
        try:
            if isinstance(ids, str):
                ids = [ids]
            vod_id = str(ids[0])

            # 读缓存
            now = int(time.time())
            with self._lock:
                cached = self._detail_cache.get(vod_id)
                if cached and now - cached[0] < _DETAIL_TTL:
                    return {"list": [cached[1]]}

            url = HOST + "/voddetail/" + vod_id + ".html"
            text = self._get(url, timeout=_DETAIL_TIMEOUT)
            if not text:
                return {"list": []}

            vod = self._parse_detail(vod_id, text)
            if not vod:
                return {"list": []}

            with self._lock:
                self._detail_cache[vod_id] = (now, vod)
            return {"list": [vod]}
        except Exception:
            return {"list": []}

    def _parse_detail(self, vod_id, text):
        # 标题
        name_m = re.search(r'<h1 class="title">([^<]+)', text)
        name = name_m.group(1).strip() if name_m else ""

        # 图片(先定位 thumb 区块,避免误匹配)
        pic = ""
        thumb_m = re.search(r'stui-content__thumb(.*?</a>)', text, re.S)
        if thumb_m:
            pic = self._extract_pic(thumb_m.group(1))
        if not pic:
            pic = self._extract_pic(text)

        # 备注
        remarks_m = re.search(
            r'stui-content__thumb.*?pic-text text-right">([^<]*)<', text, re.S)
        remarks = self._strip(remarks_m.group(1)) if remarks_m else ""

        # 分类(面包屑最后一个)
        crumbs = re.findall(
            r'<a href="/vodshow/(\d+)-----------\.html">([^<]+)</a>', text)
        type_name = crumbs[-1][1] if crumbs else ""

        # 信息字段(快速提取,只取前 8000 字符)
        info_start = text.find('stui-content__detail')
        info_seg = text[info_start:info_start + 4000] if info_start > 0 else text[:4000]

        def _field(pattern, seg):
            m = re.search(pattern, seg, re.S)
            return self._strip(m.group(1)) if m else ""

        year = _field(r'年份：</span>([^<]*)<', info_seg)
        area = _field(r'地区：</span>([^<]*)<', info_seg)
        lang = _field(r'语言：</span>([^<]*)<', info_seg)
        actor = _field(r'主演：</span>(.*?)</p>', info_seg)[:300]
        director = _field(r'导演：</span>([^<]*)<', info_seg)

        # 简介
        content_m = re.search(
            r'<span class="left text-muted">简介：</span>([^<]*)', text)
        content = self._strip(content_m.group(1)) if content_m else ""
        content = content[:500]

        # 线路面板
        play_from = []
        play_url = []
        seen_name = {}
        for m in _PANEL_RE.finditer(text):
            line_name = self._strip(m.group(1))
            if not line_name:
                continue
            seg = m.group(2) or ""
            eps = _EP_RE.findall(seg)
            if not eps:
                continue
            # 同名线路去重
            base = line_name
            cnt = seen_name.get(base, 0)
            seen_name[base] = cnt + 1
            if cnt > 0:
                line_name = "%s%d" % (base, cnt + 1)

            ep_list = []
            for vid, sid, nid, ep_name in eps:
                ep_url = HOST + "/vodplay/%s-%s-%s.html" % (vid, sid, nid)
                ep_list.append("%s$%s" % (ep_name.strip(), ep_url))
            if ep_list:
                play_from.append(line_name)
                play_url.append("#".join(ep_list))

        if not play_from:
            return None

        return {
            "vod_id": vod_id,
            "vod_name": name,
            "vod_pic": pic,
            "type_name": type_name,
            "vod_year": year,
            "vod_area": area,
            "vod_lang": lang,
            "vod_remarks": remarks or "HD",
            "vod_actor": actor,
            "vod_director": director,
            "vod_content": content,
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }

    # ============================================================
    # 搜索
    # ============================================================

    def searchContent(self, key, quick, pg="1"):
        try:
            if not key or not key.strip():
                return {"list": []}
            key = key.strip()
            page = int(pg or 1)

            # 读搜索缓存
            now = int(time.time())
            cache_key = key.lower()
            with self._lock:
                cached = self._search_cache.get(cache_key)
                if cached and now - cached[0] < _HOME_TTL:
                    all_vods = cached[1]
                    start = (page - 1) * 20
                    end = start + 20
                    return {"list": all_vods[start:end], "page": page,
                            "pagecount": max(1, (len(all_vods) + 19) // 20),
                            "limit": 20, "total": len(all_vods)}

            # 1) 尝试多种远端搜索URL
            vods = self._remote_search(key)
            if vods:
                # 缓存搜索结果
                with self._lock:
                    self._search_cache[cache_key] = (now, vods)
                start = (page - 1) * 20
                end = start + 20
                return {"list": vods[start:end], "page": page,
                        "pagecount": max(1, (len(vods) + 19) // 20),
                        "limit": 20, "total": len(vods)}

            # 2) 本地兜底搜索
            vods = self._local_search(key)
            if vods:
                with self._lock:
                    self._search_cache[cache_key] = (now, vods)
            start = (page - 1) * 20
            end = start + 20
            return {"list": vods[start:end], "page": page,
                    "pagecount": max(1, (len(vods) + 19) // 20),
                    "limit": 20, "total": len(vods)}
        except Exception:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 20, "total": 0}

    def _remote_search(self, key):
        """尝试多种搜索URL格式,返回第一个成功的结果"""
        for url in self._search_urls(key):
            text = self._get(url)
            if text and not self._blocked(text):
                vods = self._cards_from(text)
                if vods and len(vods) > 0:
                    return vods
        return []

    def _local_search(self, key):
        """本地搜索:首页全量卡片 + RSS(标题+简介匹配)"""
        kw = key.lower().strip()
        if not kw:
            return []

        result = []
        seen = set()

        # 1. 首页所有卡片(标题匹配,优先级高)
        home = self._fetch_home()
        if home:
            for c in home["all_cards"]:
                name = c.get("vod_name", "") or ""
                if kw in name.lower():
                    if c["vod_id"] and c["vod_id"] not in seen:
                        seen.add(c["vod_id"])
                        result.append(c)

        # 2. RSS 数据(标题 + 简介匹配)
        for it in self._fetch_rss():
            name = it.get("vod_name", "") or ""
            desc = it.get("_desc", "") or ""
            name_lower = name.lower()
            desc_lower = desc.lower()
            if kw in name_lower or kw in desc_lower:
                if it["vod_id"] and it["vod_id"] not in seen:
                    seen.add(it["vod_id"])
                    # 去掉内部字段
                    item = {k: v for k, v in it.items() if not k.startswith("_")}
                    result.append(item)

        # 3. 首页板块名模糊匹配(比如搜"动作"返回动作片板块)
        if home and len(result) < 10:
            for stid, sname, seg_cards in home["sections"]:
                if kw in sname.lower():
                    for c in seg_cards:
                        if c["vod_id"] and c["vod_id"] not in seen:
                            seen.add(c["vod_id"])
                            result.append(c)

        return result[:80]

    # ============================================================
    # 播放解析
    # ============================================================

    def playerContent(self, flag, id, vipFlags):
        if not id:
            return {"parse": 0, "playUrl": "", "url": ""}

        play_url = str(id).replace("\\/", "/")
        if play_url.startswith("/"):
            play_url = HOST + play_url

        # 读播放缓存
        now = int(time.time())
        with self._lock:
            cached = self._play_cache.get(play_url)
            if cached and now - cached[0] < _PLAY_TTL:
                return cached[1]

        # 动态 Referer
        headers = {
            "User-Agent": UA,
            "Referer": play_url,
        }

        # 已经是直链(极少数)
        low = play_url.lower()
        if ".m3u8" in low or ".mp4" in low:
            result = {
                "parse": 0,
                "playUrl": "",
                "url": play_url,
                "header": headers,
                "contentType": "application/x-mpegURL" if ".m3u8" in low else "video/mp4",
            }
            with self._lock:
                self._play_cache[play_url] = (now, result)
            return result

        # 快速解析:只取前 20KB 找 player_aaaa
        text = self._get_partial(play_url, max_bytes=20480)
        if text:
            m = _PLAYER_RE.search(text)
            if m:
                try:
                    data = json.loads(m.group(1))
                    media = (data.get("url") or "").replace("\\/", "/")
                    if media:
                        low2 = media.lower()
                        if ".m3u8" in low2 or ".mp4" in low2:
                            result = {
                                "parse": 0,
                                "playUrl": "",
                                "url": media,
                                "header": headers,
                                "contentType": "application/x-mpegURL" if ".m3u8" in low2 else "video/mp4",
                            }
                            with self._lock:
                                self._play_cache[play_url] = (now, result)
                            return result
                except Exception:
                    pass

        # 解析失败 → 交给壳子嗅探
        result = {
            "parse": 1,
            "playUrl": "",
            "url": play_url,
            "header": headers,
        }
        return result

    # ===== 清理 =====
    def destroy(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass

    def close(self):
        self.destroy()
