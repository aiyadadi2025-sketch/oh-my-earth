# -*- coding: utf-8 -*-
"""
看片狂人 (kpkuang.sbs) TVBox Python 爬虫
=========================================
站点: https://kpkuang.sbs/   (vfed 3.1.48 主题 / maccms 内核)

功能:
  - homeContent        首页分类 + 首页推荐
  - homeVideoContent   首页视频
  - categoryContent    分类列表 + 二级分类筛选(类型/地区/年代/排序)
  - detailContent      详情页(多播放源 + 分集列表, 懒加载不预解析)
  - playerContent      播放(按需解析, 支持 base64 加密地址 + 解析接口)
  - searchContent      搜索(多策略兜底)
  - localProxy         本地代理(解决跨域 / 防盗链)
  - destroy / close    资源释放

站点关键结构(已实测确认):
  1. 分类页: /vodtype/{tid}/            -> 200 OK
     分页:   /vodtype/{tid}/page/{n}/   -> 200 OK
     (注意: /vodshow/ 路径对爬虫返回 403, 必须用 /vodtype/)
  2. 二级分类筛选(从 /vodtype/{tid}/ 页面 fed-scre-list 区块提取):
     分类: /vodshow/{sub_tid}-------------.html
     年代: /vodshow/{tid}-----------{year}--.html
     地区: /vodshow/{tid}-{area}------------.html
  3. 详情页: /voddetail/{id}/
     分集:   /vodplay/{id}-{sid}-{nid}.html
  4. 播放页: iframe#fed-play-iframe
     data-play = 3位前缀 + base64(真实播放地址)
     data-pars = 解析接口前缀(如 https://jx.xmflv.com/?url=)
     data-word = base64 加密的站点标识

性能优化:
  - 多级 TTL 缓存(首页/分类/详情/搜索/播放地址)
  - 详情页懒加载: 只解析分集链接, 不预解析真实地址
  - 播放地址按需解析 + 后台线程预取下一集
  - requests.Session 连接池复用
  - 短超时 + 429 限流退避
  - 移动端 UA 伪装(站点对桌面 UA 返回 403)
"""

import re
import sys
import json
import time
import base64
import threading
from urllib.parse import quote, unquote, urljoin

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except Exception:
    pass

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except Exception:
    _HAS_BS4 = False

# TVBox 基类(不同版本路径不同, 做兼容)
# 注意: 部分版本的 base.spider.Spider 是 ABCMeta 抽象类, 声明了 init 等抽象方法。
# 若直接继承而子类未实现全部抽象方法, 实例化时会抛
#   TypeError: Can't instantiate abstract class Spider with abstract method init
# 因此这里做两层保护: 1) 子类实现 init; 2) 兜底基类不继承 ABCMeta。
try:
    from base.spider import Spider as BaseSpider
except Exception:
    try:
        from spider import Spider as BaseSpider
    except Exception:
        class BaseSpider(object):
            def __init__(self):
                pass

# 若基类存在未实现的抽象方法, 用空实现补齐, 避免实例化失败
try:
    _abstracts = getattr(BaseSpider, "__abstractmethods__", None)
    if _abstracts:
        for _name in list(_abstracts):
            if not hasattr(BaseSpider, _name) or _name == "init":
                continue
            if not callable(getattr(BaseSpider, _name, None)):
                continue
            setattr(BaseSpider, _name,
                    (lambda self, *a, **kw: None))
        BaseSpider.__abstractmethods__ = frozenset()
except Exception:
    pass


# ============================================================
# 常量配置
# ============================================================
HOST = "https://kpkuang.sbs"

# 站点搜索实际调用外部 JSONP API(绕过 Cloudflare 对 /vodsearch/ 的 403 拦截)
# 首页 JS: $.ajax({url: "https://kpdata.flixfiend.top/esearch/index?kw=...&ts=...", dataType:'jsonp'})
# 返回: callback_name({"code":1,"js":"<base64 编码的 JSON 结果数组>"})
SEARCH_API = "https://kpdata.flixfiend.top/esearch/index"

# 站点对桌面 UA 返回 403, 必须使用移动端 UA
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
      "Mobile/15E148 Safari/604.1")

HEADERS_BASE = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# 超时(秒) —— 尽量短, 提升响应速度
TIMEOUT_PAGE = 8
TIMEOUT_API = 5
TIMEOUT_PLAY = 5

# 缓存 TTL(秒)
TTL_HOME = 600
TTL_CAT = 300
TTL_DETAIL_OK = 300
TTL_DETAIL_EMPTY = 30
TTL_SEARCH = 180
TTL_PLAY = 900

# 首页导航分类
NAV_CATES = [
    {"type_id": "1", "type_name": "电影"},
    {"type_id": "2", "type_name": "连续剧"},
    {"type_id": "3", "type_name": "综艺"},
    {"type_id": "4", "type_name": "动漫"},
    {"type_id": "37", "type_name": "短剧"},
]

# 二级分类兜底(当页面抓取失败时使用)
# 注意: 电影/连续剧/短剧用数字子分类 id, 综艺/动漫用关键词筛选
FALLBACK_FILTERS = {
    "1": [("动作片", "6"), ("喜剧片", "7"), ("爱情片", "8"), ("科幻片", "9"),
          ("恐怖片", "10"), ("剧情片", "11"), ("战争片", "12"),
          ("纪录片", "29")],
    "2": [("国产剧", "13"), ("港剧", "14"), ("日剧", "15"), ("欧美剧", "16"),
          ("台剧", "20"), ("泰剧", "21"), ("越南剧", "22"), ("韩剧", "23")],
    "3": [("选秀", "3---选秀"), ("情感", "3---情感"), ("访谈", "3---访谈"),
          ("播报", "3---播报"), ("旅游", "3---旅游"), ("音乐", "3---音乐"),
          ("美食", "3---美食"), ("纪实", "3---纪实"), ("曲艺", "3---曲艺"),
          ("生活", "3---生活"), ("游戏互动", "3---游戏互动"),
          ("财经", "3---财经"), ("求职", "3---求职")],
    "4": [("情感", "4---情感"), ("科幻", "4---科幻"), ("热血", "4---热血"),
          ("推理", "4---推理"), ("搞笑", "4---搞笑"), ("冒险", "4---冒险"),
          ("萝莉", "4---萝莉"), ("校园", "4---校园"), ("动作", "4---动作"),
          ("机战", "4---机战"), ("运动", "4---运动"), ("战争", "4---战争"),
          ("少年", "4---少年"), ("少女", "4---少女"), ("社会", "4---社会"),
          ("原创", "4---原创"), ("亲子", "4---亲子"), ("益智", "4---益智"),
          ("励志", "4---励志"), ("其他", "4---其他")],
    "37": [("爽文短剧", "36"), ("现代都市", "39"), ("脑洞悬疑", "40"),
           ("年代穿越", "41"), ("古装仙侠", "42")],
}

# 排序选项
BY_OPTIONS = [("最新", "time"), ("最热", "hits"), ("评分", "score")]


# ============================================================
# 主类
# ============================================================
class Spider(BaseSpider):

    def __init__(self):
        try:
            super(Spider, self).__init__()
        except Exception:
            pass

        self.host = HOST
        self.session = requests.Session()
        # 连接池复用, 显著降低握手开销
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=40,
                              max_retries=0)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(HEADERS_BASE)

        # 多级缓存
        self._cache_home = {}
        self._cache_cat = {}
        self._cache_detail = {}
        self._cache_search = {}
        self._cache_play = {}
        self._cache_filter = {}
        self._lock = threading.Lock()

        # 后台预取标记
        self._prefetching = set()

    # --------------------------------------------------------
    # 基础工具
    # --------------------------------------------------------
    def init(self, extend=""):
        """
        TVBox 基类 base.spider.Spider 声明了抽象方法 init,
        子类必须实现, 否则实例化时报
        "Can't instantiate abstract class Spider with abstract method init"。
        """
        self.extend = extend or ""
        return True

    def getName(self):
        return "看片狂人"

    def isVideoFormat(self, url):
        if not url:
            return False
        return any(k in url.lower() for k in (".m3u8", ".mp4", ".flv", ".mkv"))

    def manualVideoCheck(self):
        return False

    @staticmethod
    def _cache_get(cache, key, ttl=None):
        item = cache.get(key)
        if not item:
            return None
        ts, val, item_ttl = item
        if time.time() - ts < (ttl if ttl is not None else item_ttl):
            return val
        return None

    @staticmethod
    def _cache_set(cache, key, value, ttl=TTL_CAT):
        if len(cache) > 512:
            cache.clear()
        cache[key] = (time.time(), value, ttl)

    def _get(self, url, referer="", timeout=TIMEOUT_PAGE, retry=2):
        """带重试 + 429 退避的 GET"""
        headers = {}
        if referer:
            headers["Referer"] = referer
        for attempt in range(retry):
            try:
                r = self.session.get(url, timeout=timeout, headers=headers,
                                     verify=False, allow_redirects=True)
                if r.status_code == 429:
                    time.sleep(1.5 + attempt)
                    continue
                if r.status_code >= 400:
                    if attempt == retry - 1:
                        return None
                    time.sleep(0.2)
                    continue
                if not r.encoding or r.encoding.lower() in ("iso-8859-1",):
                    r.encoding = r.apparent_encoding or "utf-8"
                return r
            except Exception:
                if attempt == retry - 1:
                    return None
                time.sleep(0.2)
        return None

    def _post(self, url, data, referer="", timeout=TIMEOUT_API):
        headers = {}
        if referer:
            headers["Referer"] = referer
        try:
            r = self.session.post(url, data=data, timeout=timeout,
                                  headers=headers, verify=False)
            if r.status_code >= 400:
                return None
            if not r.encoding or r.encoding.lower() in ("iso-8859-1",):
                r.encoding = r.apparent_encoding or "utf-8"
            return r
        except Exception:
            return None

    def _soup(self, html):
        if not html:
            return None
        if _HAS_BS4:
            for parser in ("lxml", "html.parser"):
                try:
                    return BeautifulSoup(html, parser)
                except Exception:
                    continue
        return None

    @staticmethod
    def _abs(url):
        if not url:
            return ""
        url = url.strip()
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("http"):
            return url
        if url.startswith("/"):
            return HOST + url
        return urljoin(HOST + "/", url)

    @staticmethod
    def _extract_id(url):
        if not url:
            return ""
        m = re.search(r"/voddetail/(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"/(\d{4,10})\.(?:html|shtml)", url)
        if m:
            return m.group(1)
        m = re.search(r"/(\d{4,10})/?$", url)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _pick_pic(tag):
        """从 img / a 标签中挑选真实图片地址"""
        if tag is None:
            return ""
        for attr in ("data-original", "data-src", "data-echo", "src"):
            v = tag.get(attr)
            if v and not v.startswith("data:image"):
                return v.strip()
        return ""

    @staticmethod
    def _clean_text(t):
        if not t:
            return ""
        return re.sub(r"\s+", " ", t).strip()

    @staticmethod
    def _b64_decode(s):
        """站点使用 3 位前缀 + base64 的混淆方式"""
        if not s:
            return ""
        for off in (3, 0, 1, 2, 4):
            try:
                sub = s[off:]
                pad = sub + "=" * (-len(sub) % 4)
                dec = base64.b64decode(pad).decode("utf-8", "ignore")
                if dec.startswith("http") or dec.startswith("//"):
                    return dec
            except Exception:
                continue
        return ""

    # --------------------------------------------------------
    # 首页
    # --------------------------------------------------------
    def homeContent(self, filter=False):
        cached = self._cache_get(self._cache_home, "nav")
        if cached:
            return cached

        result = {"class": [], "filters": {}, "list": []}

        r = self._get(HOST + "/", timeout=TIMEOUT_PAGE)
        classes = []
        if r is not None:
            soup = self._soup(r.text)
            if soup is not None:
                seen = set()
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    m = re.match(r"^/vodtype/(\d+)/?$", href)
                    if not m:
                        continue
                    tid = m.group(1)
                    name = self._clean_text(a.get_text())
                    if not name or tid in seen:
                        continue
                    seen.add(tid)
                    classes.append({"type_id": tid, "type_name": name})

        if not classes:
            classes = [dict(c) for c in NAV_CATES]

        result["class"] = classes

        # 二级分类筛选
        if filter:
            filters = {}
            for c in classes:
                filters[c["type_id"]] = self._build_filters(c["type_id"])
            result["filters"] = filters

        result["list"] = [self._public(it)
                          for it in self._parse_list_page(
                              r.text if r is not None else "")]

        self._cache_set(self._cache_home, "nav", result, TTL_HOME)
        return result

    def homeVideoContent(self):
        cached = self._cache_get(self._cache_home, "video")
        if cached:
            return {"list": cached}

        r = self._get(HOST + "/", timeout=TIMEOUT_PAGE)
        vlist = [self._public(it)
                 for it in self._parse_list_page(
                     r.text if r is not None else "")]
        self._cache_set(self._cache_home, "video", vlist, TTL_HOME)
        return {"list": vlist}

    # --------------------------------------------------------
    # 二级分类筛选
    # --------------------------------------------------------
    def _build_filters(self, tid):
        """
        构造二级分类筛选项。
        优先从 /vodtype/{tid}/ 页面的 fed-scre-list 区块实时抓取,
        失败则使用内置兜底数据。
        """
        cached = self._cache_get(self._cache_filter, tid)
        if cached:
            return cached

        sub_classes = []
        years = []
        areas = []

        r = self._get("%s/vodtype/%s/" % (HOST, tid), referer=HOST + "/",
                      timeout=TIMEOUT_PAGE)
        if r is not None:
            html = r.text
            i = html.find("fed-scre-list")
            if i > 0:
                block = html[i:i + 20000]
                for m in re.finditer(r"<dt>(.*?)</dt>(.*?)</dl>", block, re.S):
                    title = self._clean_text(m.group(1))
                    body = m.group(2)
                    items = re.findall(
                        r'<a href="([^"]*)"[^>]*>(.*?)</a>', body, re.S)
                    parsed = []
                    for href, txt in items:
                        name = self._clean_text(re.sub(r"<[^>]+>", "", txt))
                        if not name:
                            continue
                        parsed.append((name, unquote(href)))
                    if "分类" in title or "类型" in title:
                        sub_classes = parsed
                    elif "年代" in title or "年份" in title:
                        years = parsed
                    elif "地区" in title:
                        areas = parsed

        # 兜底
        if not sub_classes:
            sub_classes = [(n, "/vodshow/%s-------------.html" % sid)
                           for n, sid in FALLBACK_FILTERS.get(str(tid), [])]
        if not years:
            years = [(str(y), "/vodshow/%s-----------%s--.html" % (tid, y))
                     for y in range(2026, 2014, -1)]
        if not areas:
            areas = [(a, "/vodshow/%s-%s------------.html" % (tid, a))
                     for a in ("中国大陆", "香港", "台湾", "美国", "韩国",
                               "日本", "泰国", "英国", "新加坡", "其他")]

        f_class = [{"n": "全部", "v": ""}] + \
            [{"n": n, "v": u} for n, u in sub_classes]
        f_year = [{"n": "全部", "v": ""}] + \
            [{"n": n, "v": u} for n, u in years]
        f_area = [{"n": "全部", "v": ""}] + \
            [{"n": n, "v": u} for n, u in areas]
        f_by = [{"n": n, "v": v} for n, v in BY_OPTIONS]

        filters = [
            {"key": "class", "name": "类型", "value": f_class},
            {"key": "area", "name": "地区", "value": f_area},
            {"key": "year", "name": "年代", "value": f_year},
            {"key": "by", "name": "排序", "value": f_by},
        ]
        self._cache_set(self._cache_filter, tid, filters, TTL_HOME)
        return filters

    # --------------------------------------------------------
    # 分类列表
    # --------------------------------------------------------
    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        extend = extend or {}

        # 筛选项的 v 值可能是完整 URL(从页面抓取), 也可能是关键词
        cls = extend.get("class", "") or ""
        area = extend.get("area", "") or ""
        year = extend.get("year", "") or ""
        by = extend.get("by", "") or ""

        ck = "%s|%s|%s|%s|%s|%s" % (tid, cls, area, year, by, pg)
        cached = self._cache_get(self._cache_cat, ck)
        if cached:
            return cached

        # 站点特性: /vodshow/ 筛选路径对爬虫返回 403,
        # 而 /vodtype/{tid}/ 一次性返回该分类全部数据(无分页控件)。
        # 因此策略为: 抓取 /vodtype/{tid}/ 全量数据, 在本地做分页切片。
        url = "%s/vodtype/%s/" % (HOST, tid)
        r = self._get(url, referer=HOST + "/", timeout=TIMEOUT_PAGE)

        all_items = []
        if r is not None:
            all_items = self._parse_list_page(r.text)

        # 本地筛选(类型/地区/年代)
        if all_items and (cls or area or year):
            all_items = self._filter_items(all_items, cls, area, year)

        # 本地排序
        if by == "hits":
            pass  # 站点未提供热度字段, 保持原序
        elif by == "score":
            all_items = sorted(
                all_items,
                key=lambda x: self._score_of(x.get("vod_remarks", "")),
                reverse=True)

        # 本地分页
        page_size = 60
        total = len(all_items)
        pagecount = max(1, (total + page_size - 1) // page_size)
        if pg > pagecount:
            pg = pagecount
        start = (pg - 1) * page_size
        vlist = all_items[start:start + page_size]

        result = {
            "page": pg,
            "pagecount": pagecount,
            "limit": len(vlist),
            "total": total,
            "list": [self._public(it) for it in vlist],
        }
        self._cache_set(self._cache_cat, ck, result, TTL_CAT)
        return result

    def _build_cat_url(self, tid, cls, area, year, by, pg):
        """
        构造分类 URL。
        cls/area/year 若已是完整 URL(以 /vodshow/ 开头)则直接使用并追加分页。
        """
        # 若筛选项直接给了 URL
        if cls.startswith("/vodshow/"):
            base = cls
            if pg > 1:
                return HOST + base.replace(".html", "-%s---.html" % pg)
            return HOST + base

        # 否则按 maccms 规则拼装
        # /vodshow/{tid}-{class}-{area}-{year}-{by}---{pg}---.html
        if pg > 1:
            return "%s/vodshow/%s-%s-%s-%s-%s---%s---.html" % (
                HOST, tid, quote(cls), quote(area), quote(year), by, pg)
        return "%s/vodshow/%s-%s-%s-%s-%s----.html" % (
            HOST, tid, quote(cls), quote(area), quote(year), by)

    def _filter_items(self, items, cls, area, year):
        """
        本地筛选: 依据卡片上的标签(如 "2026国产剧" / "澳大利亚")匹配。
        cls/area/year 可能是完整 URL 或纯文本, 统一提取关键词。
        """
        def _kw(v):
            if not v:
                return ""
            if v.startswith("/vodshow/"):
                # /vodshow/13-------------.html -> 13 (子分类 id)
                m = re.match(r"/vodshow/(\d+)------------", v)
                if m:
                    return m.group(1)
                # /vodshow/4---科幻----------.html -> 科幻 (关键词筛选)
                m = re.match(r"/vodshow/\d+---([^-]+)-", v)
                if m:
                    return unquote(m.group(1))
                # /vodshow/2-中国大陆------------.html -> 中国大陆
                m = re.match(r"/vodshow/\d+-([^-]*)", v)
                if m:
                    return unquote(m.group(1))
                # /vodshow/2-----------2026--.html -> 2026
                m = re.search(r"-(\d{4})--\.html", v)
                if m:
                    return m.group(1)
                return ""
            return v

        cls_kw = _kw(cls)
        area_kw = _kw(area)
        year_kw = _kw(year)

        # 子分类 id -> 名称 映射
        sub_name = ""
        if cls_kw.isdigit():
            for tid, subs in FALLBACK_FILTERS.items():
                for n, sid in subs:
                    if sid == cls_kw:
                        sub_name = n
                        break
                if sub_name:
                    break

        out = []
        for it in items:
            tags = it.get("_tags", "")
            name = it.get("vod_name", "")

            if year_kw and year_kw not in tags and year_kw not in name:
                continue
            if area_kw and area_kw not in tags:
                continue
            if cls_kw:
                if sub_name:
                    if sub_name not in tags:
                        continue
                elif cls_kw not in tags:
                    continue
            out.append(it)
        return out

    @staticmethod
    def _score_of(remark):
        """从备注里提取评分(用于排序)"""
        if not remark:
            return 0.0
        m = re.search(r"(\d+(?:\.\d+)?)", remark)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                return 0.0
        return 0.0

    def _parse_list_page(self, html):
        """解析列表页(首页/分类/搜索通用)"""
        vlist = []
        if not html:
            return vlist
        soup = self._soup(html)
        if soup is None:
            return vlist

        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/voddetail/" not in href:
                continue
            vid = self._extract_id(href)
            if not vid or vid in seen:
                continue

            # 名称: title 属性优先
            name = self._clean_text(a.get("title") or "")
            if not name:
                h4 = a.find("h4")
                if h4:
                    name = self._clean_text(h4.get_text())
            if not name:
                name = self._clean_text(a.get_text())
            if not name:
                continue

            # 卡片容器
            parent = a
            for _ in range(3):
                if parent.parent is None:
                    break
                parent = parent.parent

            remark = ""
            pic = ""
            tags = ""

            # 图片: a 自身的 data-original
            pic = self._abs(self._pick_pic(a))

            if parent is not None:
                if not pic:
                    img = parent.find("img")
                    if img is not None:
                        pic = self._abs(self._pick_pic(img))
                # 备注: fed-list-remarks
                for cls in ("fed-list-remarks", "fed-list-score2",
                            "fed-list-note", "module-item-note", "note",
                            "remarks", "pic-text"):
                    node = parent.find(class_=re.compile(cls))
                    if node:
                        t = self._clean_text(node.get_text())
                        if t:
                            remark = t
                            break
                # 标签(分类/年代/地区): uk-label 或 fed-list-name
                labels = []
                for node in parent.find_all(class_=re.compile("uk-label")):
                    t = self._clean_text(node.get_text())
                    if t:
                        labels.append(t)
                for node in parent.find_all(class_=re.compile("fed-list-name")):
                    t = self._clean_text(node.get_text())
                    if t:
                        labels.append(t)
                tags = " ".join(labels)

            # 名称里可能带 "第24集", 拆出来做备注
            m = re.search(
                r"\s+(第\d+集|更新至\d+集?|全\d+集|完结|HD|BD|正片)$", name)
            if m:
                if not remark:
                    remark = m.group(1)
                name = name[:m.start()].strip()

            seen.add(vid)
            vlist.append({
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": remark,
                "_tags": tags,
            })

        return vlist

    @staticmethod
    def _public(item):
        """去掉内部字段, 只返回 TVBox 需要的字段"""
        return {
            "vod_id": item.get("vod_id", ""),
            "vod_name": item.get("vod_name", ""),
            "vod_pic": item.get("vod_pic", ""),
            "vod_remarks": item.get("vod_remarks", ""),
        }

    def _parse_pager(self, html, cur_pg):
        """解析分页信息"""
        pagecount = 1
        total = 0
        if not html:
            return pagecount, total

        # /vodtype/{tid}/page/{n}/
        nums = re.findall(r"/vodtype/\d+/page/(\d+)/", html)
        if nums:
            try:
                pagecount = max(int(n) for n in nums)
            except Exception:
                pagecount = 1

        # /vodshow/...-{n}---.html
        if pagecount <= 1:
            nums = re.findall(r"/vodshow/[^\"']*?-(\d+)---\.html", html)
            if nums:
                try:
                    pagecount = max(int(n) for n in nums)
                except Exception:
                    pagecount = 1

        m = re.search(r"共\s*(\d+)\s*[条个]", html)
        if m:
            try:
                total = int(m.group(1))
            except Exception:
                total = 0

        if pagecount < cur_pg:
            pagecount = cur_pg
        return pagecount, total

    # --------------------------------------------------------
    # 详情页(懒加载: 只解析分集链接, 不解析真实地址)
    # --------------------------------------------------------
    def detailContent(self, ids):
        if isinstance(ids, (list, tuple)):
            vid = str(ids[0]) if ids else ""
        else:
            vid = str(ids)
        vid = self._extract_id(vid) or vid

        if not vid:
            return {"list": []}

        cached = self._cache_get(self._cache_detail, vid)
        if cached:
            return cached

        url = "%s/voddetail/%s/" % (HOST, vid)
        r = self._get(url, referer=HOST + "/", timeout=TIMEOUT_PAGE)

        if r is None:
            empty = {"list": []}
            self._cache_set(self._cache_detail, vid, empty, TTL_DETAIL_EMPTY)
            return empty

        html = r.text
        soup = self._soup(html)

        vod = {
            "vod_id": vid,
            "vod_name": "",
            "vod_pic": "",
            "vod_remarks": "",
            "vod_year": "",
            "vod_area": "",
            "vod_actor": "",
            "vod_director": "",
            "vod_content": "",
            "vod_play_from": "",
            "vod_play_url": "",
        }

        if soup is not None:
            h1 = soup.find("h1")
            if h1:
                vod["vod_name"] = self._clean_text(h1.get_text())
            if not vod["vod_name"]:
                t = soup.find("title")
                if t:
                    vod["vod_name"] = self._clean_text(
                        t.get_text()).split("-")[0].split("_")[0].strip()

            img = soup.find("img", class_=re.compile("lazyload|poster|pic|cover"))
            if img is None:
                img = soup.find("img")
            if img is not None:
                vod["vod_pic"] = self._abs(self._pick_pic(img))

            for cls in ("vod_content", "content", "detail-content", "desc",
                        "introduction", "module-info-introduction"):
                node = soup.find(class_=re.compile(cls))
                if node:
                    txt = self._clean_text(node.get_text())
                    if len(txt) > 10:
                        vod["vod_content"] = txt
                        break

            meta_text = self._clean_text(soup.get_text())
            m = re.search(r"地区[:：]\s*([^\s]+)", meta_text)
            if m:
                vod["vod_area"] = m.group(1)
            m = re.search(r"年份[:：]\s*(\d{4})", meta_text)
            if m:
                vod["vod_year"] = m.group(1)
            m = re.search(r"更新[:：]\s*([\d.]+)", meta_text)
            if m:
                vod["vod_remarks"] = m.group(1)
            m = re.search(r"导演[:：]\s*([^\s]+)", meta_text)
            if m:
                vod["vod_director"] = m.group(1)
            m = re.search(r"主演[:：]\s*([^\n]{0,80})", meta_text)
            if m:
                vod["vod_actor"] = m.group(1).strip()

        play_from, play_url = self._parse_play_sources(html, vid)
        vod["vod_play_from"] = play_from
        vod["vod_play_url"] = play_url

        result = {"list": [vod]}
        ttl = TTL_DETAIL_OK if play_url else TTL_DETAIL_EMPTY
        self._cache_set(self._cache_detail, vid, result, ttl)
        return result

    def _parse_play_sources(self, html, vid):
        """
        解析播放源与分集列表。
        返回 (vod_play_from, vod_play_url)
        分集 URL 使用 vodplay 页面地址, 播放时再按需解析真实地址。
        """
        if not html:
            return "", ""

        pattern = re.compile(
            r'href="(/vodplay/%s-(\d+)-(\d+)\.html)"[^>]*?(?:title="([^"]*)")?[^>]*>'
            r'\s*([^<]*)' % re.escape(vid))

        sources = {}
        order = []

        for m in pattern.finditer(html):
            href, sid, nid = m.group(1), m.group(2), m.group(3)
            title = m.group(4) or ""
            text = m.group(5) or ""
            name = self._clean_text(text) or self._clean_text(title)
            # 去掉 "在线观看《xxx》第N集" 这类说明文字, 只保留集名
            name = re.sub(r"^在线观看[《][^》]*[》]", "", name).strip()
            if not name:
                name = "第%s集" % nid

            if sid not in sources:
                sources[sid] = []
                order.append(sid)
            if not any(u == href for _, u in sources[sid]):
                sources[sid].append((name, href))

        if not sources:
            return "", ""

        from_names = self._parse_source_names(html, order)

        play_from_list = []
        play_url_list = []
        for idx, sid in enumerate(order):
            eps = sources[sid]
            if not eps:
                continue

            def _key(item):
                mm = re.search(r"-(\d+)\.html", item[1])
                return int(mm.group(1)) if mm else 0
            eps = sorted(eps, key=_key)

            fname = from_names[idx] if idx < len(from_names) else ("线路%s" % sid)
            play_from_list.append(fname)
            play_url_list.append("#".join(
                "%s$%s%s" % (n, HOST, u) for n, u in eps))

        return "$$$".join(play_from_list), "$$$".join(play_url_list)

    def _parse_source_names(self, html, order):
        """
        解析播放源名称列表。
        站点结构: <a ... data-lineid="youku" data-linename="优酷-VIP解析">优酷-VIP解析</a>
        播放源顺序与分集 sid 顺序一致, 按出现顺序对齐。
        """
        names = []

        # 主路径: 提取所有 data-linename
        for m in re.finditer(r'data-linename="([^"]+)"', html):
            n = self._clean_text(m.group(1))
            if n and n not in names:
                names.append(n)

        # 兜底: 从 tab 文本提取
        if not names:
            for m in re.finditer(
                    r'class="[^"]*line-select[^"]*"[^>]*>\s*([^<]{1,24})\s*<',
                    html):
                n = self._clean_text(m.group(1))
                if n and n not in names:
                    names.append(n)

        # 数量对齐: 若源名数量与 sid 数量不一致, 按顺序补齐
        if len(names) != len(order):
            if len(names) > len(order):
                names = names[:len(order)]
            else:
                while len(names) < len(order):
                    names.append("线路%s" % order[len(names)])

        return names

    # --------------------------------------------------------
    # 播放(按需解析)
    # --------------------------------------------------------
    def playerContent(self, flag, id, vipFlags):
        """
        flag: 播放源名称
        id:   分集页面地址(或直接是播放地址)
        """
        if not id:
            return {"parse": 0, "playUrl": "", "url": "", "header": ""}

        # 已经是直链
        if self.isVideoFormat(id) and "/vodplay/" not in id:
            return self._play_payload(id)

        cached = self._cache_get(self._cache_play, id)
        if cached:
            self._prefetch_next(id)
            return cached

        payload = self._resolve_play(id)

        if payload:
            self._cache_set(self._cache_play, id, payload, TTL_PLAY)
            self._prefetch_next(id)
            return payload

        # 解析失败: 交给 TVBox 内置嗅探
        return {
            "parse": 1,
            "playUrl": "",
            "url": id,
            "header": json.dumps({
                "User-Agent": UA,
                "Referer": HOST + "/",
            }, ensure_ascii=False),
            "format": "application/x-mpegURL",
        }

    def _resolve_play(self, page_url):
        """
        从 vodplay 页面解析播放信息。
        站点结构: iframe#fed-play-iframe
          data-play = 3位前缀 + base64(真实播放地址)
          data-pars = 解析接口前缀
        返回 TVBox 播放 payload, 失败返回 None。
        """
        if not page_url.startswith("http"):
            page_url = self._abs(page_url)

        r = self._get(page_url, referer=HOST + "/", timeout=TIMEOUT_PLAY)
        if r is None:
            return None
        html = r.text

        # 1) 主路径: iframe 的 data-play / data-pars
        m = re.search(r"<iframe[^>]*id=\"fed-play-iframe\"[^>]*>", html, re.S)
        if m:
            tag = m.group(0)
            dp = re.search(r'data-play="([^"]*)"', tag)
            dpars = re.search(r'data-pars="([^"]*)"', tag)

            real_url = self._b64_decode(dp.group(1)) if dp else ""
            pars = dpars.group(1).strip() if dpars else ""

            if real_url:
                # 直链 m3u8/mp4 -> 直接播放
                if self.isVideoFormat(real_url):
                    return self._play_payload(real_url)
                # 官方线路(优酷/爱奇艺等): 真实流地址在解析接口的 JS 运行时才生成,
                # 静态抓取拿不到。必须交给 TVBox 内置嗅探(parse=1)去解析。
                if pars:
                    return self._sniff_payload(pars + real_url, real_url)
                # 无解析接口, 同样交给 TVBox 嗅探
                return self._sniff_payload(real_url, real_url)

        # 2) 兜底: 通用 data-play
        m = re.search(r'data-play="([^"]+)"', html)
        if m:
            real_url = self._b64_decode(m.group(1))
            if real_url:
                if self.isVideoFormat(real_url):
                    return self._play_payload(real_url)
                dpars = re.search(r'data-pars="([^"]*)"', html)
                if dpars and dpars.group(1).strip():
                    return self._sniff_payload(
                        dpars.group(1).strip() + real_url, real_url)
                return self._sniff_payload(real_url, real_url)

        # 3) 兜底: var player_xxx = {...}
        for mm in re.finditer(r"var\s+player_\w+\s*=\s*(\{.*?\})\s*[;<]",
                              html, re.S):
            try:
                data = json.loads(mm.group(1))
            except Exception:
                continue
            u = data.get("url") or data.get("Url") or ""
            if u and self.isVideoFormat(u):
                return self._play_payload(self._abs(u))

        # 4) 兜底: 直接找 m3u8
        mm = re.search(r"""["'](https?://[^"']+?\.m3u8[^"']*)["']""", html)
        if mm:
            return self._play_payload(self._abs(mm.group(1).replace("\\/", "/")))

        # 5) 兜底: 通用 url 字段
        mm = re.search(r"""["']url["']\s*:\s*["'](https?://[^"']+)["']""", html)
        if mm:
            u = mm.group(1).replace("\\/", "/")
            if self.isVideoFormat(u):
                return self._play_payload(self._abs(u))

        return None

    def _play_payload(self, url):
        """直链播放"""
        return {
            "parse": 0,
            "playUrl": "",
            "url": url,
            "header": json.dumps({
                "User-Agent": UA,
                "Referer": HOST + "/",
                "Origin": HOST,
            }, ensure_ascii=False),
            "format": "application/x-mpegURL",
        }

    def _sniff_payload(self, sniff_url, real_url=""):
        """
        交给 TVBox 内置嗅探解析(parse=1)。
        官方线路(优酷/爱奇艺/腾讯等)的真实流地址由解析接口的 JS 运行时生成,
        静态抓取无法获得, 必须让 TVBox 用 WebView 打开解析页并嗅探出 m3u8。
        sniff_url: 交给 TVBox 打开的地址(通常是 解析接口前缀 + 真实页面地址)
        real_url:  原始页面地址(仅作参考)
        """
        return {
            "parse": 1,
            "playUrl": "",
            "url": sniff_url,
            "header": json.dumps({
                "User-Agent": UA,
                "Referer": HOST + "/",
            }, ensure_ascii=False),
        }

    def _prefetch_next(self, page_url):
        """后台预取下一集, 提升连播速度"""
        try:
            m = re.search(r"/vodplay/(\d+)-(\d+)-(\d+)\.html", page_url)
            if not m:
                return
            vid, sid, nid = m.group(1), m.group(2), int(m.group(3))
            next_url = "%s/vodplay/%s-%s-%s.html" % (HOST, vid, sid, nid + 1)

            with self._lock:
                if next_url in self._prefetching:
                    return
                if self._cache_get(self._cache_play, next_url):
                    return
                self._prefetching.add(next_url)

            def _work():
                try:
                    payload = self._resolve_play(next_url)
                    if payload:
                        self._cache_set(self._cache_play, next_url, payload,
                                        TTL_PLAY)
                except Exception:
                    pass
                finally:
                    with self._lock:
                        self._prefetching.discard(next_url)

            t = threading.Thread(target=_work)
            t.daemon = True
            t.start()
        except Exception:
            pass

    # --------------------------------------------------------
    # 搜索
    # --------------------------------------------------------
    def searchContent(self, keyword, quick=False, pg="1"):
        keyword = (keyword or "").strip()
        if not keyword:
            return {"list": []}

        pg = int(pg) if str(pg).isdigit() else 1
        ck = "%s|%s" % (keyword, pg)
        cached = self._cache_get(self._cache_search, ck)
        if cached:
            return cached

        vlist = []

        # 主策略: 站点外部搜索 API (JSONP + base64, 绕过 Cloudflare)
        vlist = self._search_api(keyword)

        # 兜底: 分类页抓取 + 关键词模糊匹配
        if not vlist:
            vlist = self._search_by_scrape(keyword)

        result = {"list": [self._public(it) for it in vlist]}
        self._cache_set(self._cache_search, ck, result, TTL_SEARCH)
        return result

    def _search_api(self, keyword):
        """
        策略1: 站点外部搜索 API。
        站点首页搜索 JS 实际调用 kpdata.flixfiend.top/esearch/index,
        返回 JSONP 回调包裹的 JSON, 其中 js 字段为 base64 编码的结果数组。
        该接口不受 Cloudflare 403 拦截影响。
        注意: API 存在短时速率限制, 首次请求可能返回 code:0,
        需要重试一次(间隔 1 秒)。
        """
        for attempt in range(2):
            try:
                if attempt > 0:
                    time.sleep(1.0)

                ts = int(time.time() * 1000)
                cb = "jsonp_cb_%d" % ts
                url = "%s?kw=%s&ts=%s&callback=%s" % (
                    SEARCH_API, quote(keyword), ts, cb)

                r = self._get(url, referer=HOST + "/", timeout=TIMEOUT_API)
                if r is None:
                    continue

                text = r.text
                # 解析 JSONP: callback_name({...})
                if text.startswith(cb + "("):
                    json_str = text[len(cb) + 1:].rstrip(")")
                    if json_str.endswith(";"):
                        json_str = json_str[:-1]
                else:
                    # 通用 JSONP 解析兜底
                    m = re.search(r"\((\{.*\})\)", text, re.S)
                    if not m:
                        continue
                    json_str = m.group(1)

                data = json.loads(json_str)
                if data.get("code") != 1:
                    continue

                js_b64 = data.get("js", "")
                if not js_b64:
                    continue

                js_decoded = base64.b64decode(js_b64).decode(
                    "utf-8", "ignore")
                results = json.loads(js_decoded)
                if not isinstance(results, list):
                    continue

                vlist = []
                for item in results:
                    vod = item.get("data", {})
                    vid = str(item.get("id", ""))
                    name = vod.get("vod_name", "")
                    if not vid or not name:
                        continue

                    # vod_pic 是相对路径, 拼接为绝对地址;
                    # 优先用豆瓣/IMDB 封面(不受站点防盗链影响)
                    pic = vod.get("vod_douban_cover", "")
                    if not pic:
                        pic = vod.get("vod_imdb_poster", "")
                    if not pic:
                        pic_path = vod.get("vod_pic", "")
                        if pic_path:
                            pic = self._abs(pic_path)

                    # API 无 vod_remarks, 用年份代替
                    remark = vod.get("vod_year", "")

                    vlist.append({
                        "vod_id": vid,
                        "vod_name": name,
                        "vod_pic": pic,
                        "vod_remarks": remark,
                    })
                return vlist
            except Exception:
                continue
        return []

    def _search_by_scrape(self, keyword):
        """
        策略2(兜底): 抓取各分类页, 做关键词模糊匹配。
        当外部搜索 API 不可用时使用此策略。
        优化: 并发抓取各分类, 扩大候选池, 提升匹配精度。
        注意: 必须限制总耗时, 否则 TVBox 端会一直转圈。
        """
        tokens = self._tokenize(keyword)
        if not tokens:
            return []

        # 并发抓取各分类页 + 首页(扩大候选池)
        pool = []
        targets = [c["type_id"] for c in NAV_CATES]
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _fetch(tid):
                # 复用分类缓存: 若该分类刚被抓过, 直接命中, 不再发请求
                ck = "scrape|%s" % tid
                hit = self._cache_get(self._cache_cat, ck)
                if hit is not None:
                    return hit
                if tid == "home":
                    url = HOST + "/"
                else:
                    url = "%s/vodtype/%s/" % (HOST, tid)
                r = self._get(url, referer=HOST + "/", timeout=TIMEOUT_API)
                if r is None:
                    return []
                items = self._parse_list_page(r.text)
                self._cache_set(self._cache_cat, ck, items, TTL_CAT)
                return items

            # 不使用 with 上下文管理器, 避免 __exit__ 阻塞等待所有线程完成
            ex = ThreadPoolExecutor(max_workers=6)
            futs = [ex.submit(_fetch, t) for t in targets + ["home"]]
            try:
                for f in as_completed(futs, timeout=6.0):
                    try:
                        pool.extend(f.result())
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                # wait=False: 不阻塞, 未完成的线程在后台继续运行
                ex.shutdown(wait=False)
        except Exception:
            # 并发不可用/超时: 退化为串行, 但只抓前 3 个分类, 控制耗时
            for t in targets[:3]:
                url = "%s/vodtype/%s/" % (HOST, t)
                r = self._get(url, referer=HOST + "/", timeout=TIMEOUT_API)
                if r is not None:
                    pool.extend(self._parse_list_page(r.text))

        if not pool:
            return []

        # 去重
        seen = set()
        uniq = []
        for it in pool:
            vid = it.get("vod_id")
            if vid and vid not in seen:
                seen.add(vid)
                uniq.append(it)

        # 打分排序
        scored = []
        for item in uniq:
            score = self._score(item["vod_name"], tokens)
            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda x: -x[0])
        return [it for _, it in scored[:40]]

    @staticmethod
    def _tokenize(keyword):
        """中文按字 + 英文数字按词切分"""
        kw = re.sub(r"\s+", "", keyword or "")
        if not kw:
            return []
        tokens = []
        for w in re.findall(r"[a-zA-Z0-9]+", kw):
            if len(w) >= 2:
                tokens.append(w.lower())
        for ch in re.findall(r"[\u4e00-\u9fa5]", kw):
            tokens.append(ch)
        return tokens

    @staticmethod
    def _score(name, tokens):
        """
        打分: 连续子串命中权重最高, 单字命中权重最低。
        避免"庆余年"被拆成单字后匹配到大量无关结果。
        """
        if not name:
            return 0
        low = name.lower()
        score = 0

        # 完整关键词命中(最高优先级)
        full = "".join(tokens)
        if len(full) >= 2 and full in low:
            score += 100

        # 多字词命中
        for t in tokens:
            if len(t) > 1 and t in low:
                score += 20

        # 单字命中(仅当命中数达到一定比例才计分)
        single_hits = sum(1 for t in tokens if len(t) == 1 and t in low)
        single_total = sum(1 for t in tokens if len(t) == 1)
        if single_total and single_hits == single_total:
            score += 10
        elif single_total and single_hits >= max(2, single_total - 1):
            score += 3

        return score

    # --------------------------------------------------------
    # 本地代理
    # --------------------------------------------------------
    def localProxy(self, param):
        """代理 m3u8 / ts 请求, 解决防盗链与跨域"""
        try:
            if isinstance(param, str):
                try:
                    param = json.loads(param)
                except Exception:
                    param = {"url": param}
            url = param.get("url") or param.get("u") or ""
            if not url:
                return [404, "text/plain", "no url"]

            headers = {
                "User-Agent": UA,
                "Referer": HOST + "/",
                "Origin": HOST,
            }
            r = self.session.get(url, headers=headers, timeout=TIMEOUT_PAGE,
                                 verify=False, stream=True)
            if r.status_code >= 400:
                return [r.status_code, "text/plain", "upstream error"]

            ctype = r.headers.get("Content-Type", "")
            if ".m3u8" in url or "mpegurl" in ctype.lower():
                body = r.content.decode("utf-8", "ignore")
                base = url.rsplit("/", 1)[0] + "/"
                lines = []
                for line in body.splitlines():
                    s = line.strip()
                    if s and not s.startswith("#") and not s.startswith("http"):
                        line = urljoin(base, s)
                    lines.append(line)
                return [200, "application/vnd.apple.mpegurl", "\n".join(lines)]

            return [200, ctype or "application/octet-stream", r.content]
        except Exception as e:
            return [500, "text/plain", str(e)]

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
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
if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "home"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    sp = Spider()

    if action == "home":
        res = sp.homeContent(True)
        print("分类:", json.dumps(res.get("class"), ensure_ascii=False))
        print("筛选项 keys:", list(res.get("filters", {}).keys()))
        f = res.get("filters", {}).get("2", [])
        for g in f:
            print("  [%s] %s" % (g["name"],
                                 " / ".join(x["n"] for x in g["value"][:12])))
        print("首页条数:", len(res.get("list", [])))
        for it in res.get("list", [])[:5]:
            print("  ", it["vod_id"], it["vod_name"], it["vod_remarks"])

    elif action == "category":
        tid = arg or "2"
        res = sp.categoryContent(tid, 1, True, {"class": "", "area": "",
                                                "year": "", "by": ""})
        print("分类 %s 第1页: pagecount=%s total=%s 条数=%s" % (
            tid, res["pagecount"], res["total"], len(res["list"])))
        for it in res["list"][:5]:
            print("  ", it["vod_id"], it["vod_name"], it["vod_remarks"])

    elif action == "filter":
        # 测试二级分类筛选
        tid = arg or "2"
        res = sp.categoryContent(
            tid, 1, True,
            {"class": "/vodshow/13-------------.html", "area": "", "year": "",
             "by": ""})
        print("二级分类(国产剧) 条数=%s" % len(res["list"]))
        for it in res["list"][:5]:
            print("  ", it["vod_id"], it["vod_name"], it["vod_remarks"])

    elif action == "detail":
        vid = arg or "1127035"
        res = sp.detailContent([vid])
        v = res["list"][0]
        print("名称:", v["vod_name"])
        print("地区/年份:", v["vod_area"], v["vod_year"])
        print("播放源:", v["vod_play_from"])
        print("分集(前150字):", v["vod_play_url"][:150])

    elif action == "play":
        res = sp.playerContent("", arg or "/vodplay/1127035-6-1.html", [])
        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif action == "search":
        kw = arg or "早春"
        res = sp.searchContent(kw, False, "1")
        print("搜索 '%s' 结果数: %d" % (kw, len(res["list"])))
        for it in res["list"][:10]:
            print("  ", it["vod_id"], it["vod_name"], it["vod_remarks"])

    else:
        print("用法: python kpkuang.py "
              "[home|category|filter|detail|play|search] [参数]")

    sp.destroy()
