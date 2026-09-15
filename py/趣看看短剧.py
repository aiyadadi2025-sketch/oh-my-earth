# -*- coding: utf-8 -*-
"""
喜福网 (minidrama.contentchina.com) - TVBox 爬虫源 (JSON API + 阿里云 VOD)
========================================================================
接口：homeContent / categoryContent(含筛选) / detailContent(懒加载) /
      playerContent(阿里云播放凭证换直链) / searchContent(全库模糊匹配)

站点特性（已实测确认）：
1. 前后端分离架构（Nuxt），数据全部来自 JSON API（minidrama-api.contentchina.com），
   无页面爬取，加载极快
2. 播放链路：play_auth 接口下发阿里云点播播放凭证(playAuth) -> 解码出 STS 临时密钥
   -> HMAC-SHA1 签名调用 vod.{region}.aliyuncs.com 的 GetPlayInfo
   -> 返回 mp4 直链（无时效签名，可直接播放）
3. 分类：9 个内容类型标签（categoryId），剧集可挂多个标签
4. 站点无搜索接口，采用全库拉取(3页/100条) + 本地标题模糊匹配兜底

核心优化（加载速度 + 播放速度）：
- 详情页懒加载：detailContent 只返回剧集列表，不预解析任何播放地址，秒开
- playerContent 按需解析 mp4 直链 + 30分钟缓存 + 后台预取下一集
- 多级缓存：首页5分钟 / 分类5分钟 / 详情10分钟(失败30秒) / 搜索10分钟 / 播放30分钟
- 连接池复用(HTTPAdapter) + gzip 自动解压 + 全链路短超时(5s/4s) + 快速重试(0.2s)
- 搜索全库并发拉取（ThreadPoolExecutor 3页并行）
- 筛选：类型(二级分类) / 排序(最新/最热，本地排序零请求)
"""

import re
import json
import time
import uuid
import base64
import hashlib
import hmac
import threading
from urllib.parse import quote, urlencode

import requests
from requests.adapters import HTTPAdapter

try:
    from concurrent.futures import ThreadPoolExecutor
except ImportError:
    ThreadPoolExecutor = None

try:
    import sys
    sys.path.append('..')
    from base.spider import Spider as _BaseSpider
except ImportError:
    _BaseSpider = None


# ============================================================
# 常量
# ============================================================
API = "https://minidrama-api.contentchina.com"
HOST = "https://minidrama.contentchina.com"

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

# 超时（秒）
TIMEOUT_API = 5
TIMEOUT_VOD = 4

# 缓存 TTL（秒）
TTL_HOME = 300
TTL_CAT = 300
TTL_DETAIL_OK = 600
TTL_DETAIL_EMPTY = 30
TTL_SEARCH = 600
TTL_PLAY = 1800

# 分类表（categoryId 来自 /web/v1/home/categoryList，实测稳定）
CATS = [
    {"id": "1",  "name": "现代言情"},
    {"id": "3",  "name": "爽剧"},
    {"id": "4",  "name": "都市"},
    {"id": "5",  "name": "逆袭"},
    {"id": "6",  "name": "甜宠"},
    {"id": "7",  "name": "女强"},
    {"id": "13", "name": "虐恋"},
    {"id": "16", "name": "古代言情"},
    {"id": "23", "name": "玄幻"},
]

# 排序选项（本地排序，无需额外请求）
SORTS = [
    {"n": "综合", "v": ""},
    {"n": "最新", "v": "time"},
    {"n": "最热", "v": "hot"},
]

ALL_CLASSES = [
    {"type_id": "all", "type_name": "全部短剧", "filter": 1},
] + [{"type_id": c["id"], "type_name": c["name"], "filter": 1} for c in CATS]

# 类型筛选（作为二级分类）
_TYPE_FILTER = {
    "key": "type", "name": "类型",
    "value": [{"n": "全部", "v": ""}] + [{"n": c["name"], "v": c["id"]} for c in CATS],
}
_SORT_FILTER = {
    "key": "by", "name": "排序",
    "value": SORTS,
}

# 每个一级分类的筛选器：全部短剧可再选类型，其余分类直接提供排序
ALL_FILTERS = {"all": [_TYPE_FILTER, _SORT_FILTER]}
for c in CATS:
    ALL_FILTERS[c["id"]] = [_SORT_FILTER]


# ============================================================
# 阿里云 VOD：播放凭证 -> 播放直链
# ============================================================
def _aliyun_encode(value):
    """模拟阿里云 AliyunEncodeURI（基于 JS encodeURIComponent）。"""
    v = quote(str(value), safe="!~*'()")
    v = v.replace("+", "%2B").replace("*", "%2A").replace("%7E", "~")
    return v


def _aliyun_sign(params, secret):
    """阿里云 RPC 签名（HMAC-SHA1）。"""
    canon = "&".join(
        "%s=%s" % (_aliyun_encode(k), _aliyun_encode(v))
        for k, v in sorted(params.items())
    )
    string_to_sign = "GET&" + _aliyun_encode("/") + "&" + _aliyun_encode(canon)
    digest = hmac.new(
        (secret + "&").encode(), string_to_sign.encode(), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode()


def _decode_playauth(play_auth):
    """解码阿里云播放凭证，返回 {AccessKeyId, AccessKeySecret, SecurityToken,
    AuthInfo, Region, VideoId} 等字段。"""
    try:
        raw = base64.b64decode(play_auth + "=" * (-len(play_auth) % 4))
        data = json.loads(raw)
        return {
            "AccessKeyId": data["AccessKeyId"],
            "AccessKeySecret": data["AccessKeySecret"],
            "SecurityToken": data["SecurityToken"],
            "AuthInfo": data["AuthInfo"],
            "Region": data["Region"],
            "VideoId": data["VideoMeta"]["VideoId"],
        }
    except Exception:
        return None


# ============================================================
# Spider 主类
# ============================================================
_Base = _BaseSpider if _BaseSpider is not None else object


class Spider(_Base):
    siteUrl = HOST
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": HOST + "/",
    }

    # ===== 初始化 =====
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.verify = False
        adapter = HTTPAdapter(
            pool_connections=20, pool_maxsize=40,
            max_retries=0, pool_block=False,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        self._lock = threading.Lock()
        self._home_cache = []
        self._home_cache_time = 0
        self._cat_cache = {}
        self._detail_cache = {}
        self._search_cache = []          # 全库列表（搜索用）
        self._search_cache_time = 0
        self._play_cache = {}            # albumId:seq -> mp4 直链
        self._prefetching = set()
        self._library_loading = False    # 全库拉取进行中标志

    def init(self, extend=""):
        self.extend = extend or ""
        # 后台预热全库，让首次搜索也能秒回
        self._warm_library()

    def _warm_library(self):
        def _job():
            try:
                self._load_full_library()
            except Exception:
                pass
        threading.Thread(target=_job, daemon=True).start()

    # ===== 网络工具 =====
    def _get_json(self, url, params=None, timeout=TIMEOUT_API):
        for attempt in range(2):
            try:
                r = self.session.get(url, params=params, timeout=timeout)
                if r.status_code == 429:
                    time.sleep(1.5)
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("code") == 200:
                    return data.get("data")
                return None
            except Exception:
                if attempt == 0:
                    time.sleep(0.2)
                else:
                    return None
        return None

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

    # ===== 数据规整 =====
    @staticmethod
    def _card(item):
        """把 API 条目规整为 TVBox vod 卡片。"""
        total = item.get("total") or 0
        remarks = "全%s集" % total if total else ""
        return {
            "vod_id": str(item.get("albumId") or ""),
            "vod_name": item.get("title") or "",
            "vod_pic": item.get("coverUrl") or "",
            "vod_remarks": remarks,
        }

    def _cards(self, items, limit=100):
        out = []
        for it in items or []:
            card = self._card(it)
            if card["vod_id"] and card["vod_name"]:
                out.append(card)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _sort_key(by):
        def key(it):
            if by == "hot":
                return int(it.get("hot") or 0)
            if by == "time":
                return int(it.get("updateTime") or 0)
            return 0
        return key

    # ============================================================
    # 首页
    # ============================================================
    def _fetch_home(self):
        data = self._get_json(API + "/web/v1/home/index")
        if not data:
            return []
        merged = {}
        for item in data.get("newSerials") or []:
            merged[str(item.get("albumId"))] = item
        for scene in data.get("scenes") or []:
            for item in scene.get("serials") or []:
                merged[str(item.get("albumId"))] = item
        return self._cards(list(merged.values()), limit=60)

    def homeContent(self, filter=False):
        with self._lock:
            if self._home_cache and time.time() - self._home_cache_time < TTL_HOME:
                return {"class": ALL_CLASSES, "filters": ALL_FILTERS,
                        "list": self._home_cache[:60]}
        vod_list = self._fetch_home()
        if vod_list:
            with self._lock:
                self._home_cache = vod_list
                self._home_cache_time = int(time.time())
        return {"class": ALL_CLASSES, "filters": ALL_FILTERS, "list": vod_list}

    def homeVideoContent(self):
        with self._lock:
            if self._home_cache and time.time() - self._home_cache_time < TTL_HOME:
                return {"list": self._home_cache[:60]}
        vod_list = self._fetch_home()
        if vod_list:
            with self._lock:
                self._home_cache = vod_list
                self._home_cache_time = int(time.time())
        return {"list": vod_list}

    # ============================================================
    # 分类列表（含筛选）
    # ============================================================
    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = max(1, int(pg or 1))
            ext = {}
            if isinstance(extend, dict):
                ext = extend
            elif isinstance(extend, str) and extend:
                try:
                    ext = json.loads(extend)
                except Exception:
                    ext = {}

            # 解析筛选参数
            cid = str(tid or "all")
            type_id = (ext.get("type") or "").strip() or cid
            by = (ext.get("by") or "").strip()

            key = "%s|%d|%s|%s" % (cid, page, type_id, by)
            cached = self._cache_get(self._cat_cache, key, TTL_CAT)
            if cached is not None:
                return cached

            params = {"pageSize": 100, "currentPage": page}
            if type_id and type_id != "all":
                params["filterCategories"] = type_id

            data = self._get_json(API + "/web/v1/drama/list", params=params)
            if not data:
                return {"list": [], "page": page, "pagecount": 1,
                        "limit": 36, "total": 0}

            items = data.get("data") or []
            if by in ("time", "hot"):
                items = sorted(items, key=self._sort_key(by), reverse=True)

            pag = data.get("pagination") or {}
            total = pag.get("total") or len(items)
            pagecount = max(1, pag.get("totalPages") or 1)

            result = {
                "list": self._cards(items, limit=36),
                "page": page,
                "pagecount": pagecount,
                "limit": 36,
                "total": total,
            }
            self._cache_set(self._cat_cache, key, result, TTL_CAT)
            return result
        except Exception:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 36, "total": 0}

    # ============================================================
    # 详情页（懒加载）
    # ============================================================
    def detailContent(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        album_id = str(ids[0]).split(",")[0].strip()
        if not album_id:
            return {"list": []}

        cached = self._cache_get(self._detail_cache, album_id, None)
        if cached is not None:
            return cached

        result = self._fetch_detail(album_id)
        ttl = TTL_DETAIL_OK if result.get("list") else TTL_DETAIL_EMPTY
        self._cache_set(self._detail_cache, album_id, result, ttl)
        return result

    def _fetch_detail(self, album_id):
        data = self._get_json(API + "/web/v1/drama/detail", params={"albumId": album_id})
        if not data:
            return {"list": []}

        serial = data.get("serialData") or {}
        title = serial.get("title") or ""
        total = serial.get("total") or 0

        # 剧集列表（懒加载：只出集数，不解析播放地址）
        eps = []
        for ep in data.get("episodeList") or []:
            seq = ep.get("sequence")
            if not seq:
                continue
            eps.append("第%s集$%s:%s" % (seq, album_id, seq))

        detail = {
            "vod_id": album_id,
            "vod_name": title or "剧集%s" % album_id,
            "vod_pic": serial.get("coverUrl") or "",
            "type_name": ",".join(serial.get("categories") or []),
            "vod_remarks": "全%s集" % total if total else "",
            "vod_year": "",
            "vod_area": "",
            "vod_lang": "",
            "vod_director": "",
            "vod_actor": ",".join(serial.get("leadActor") or []),
            "vod_content": (serial.get("introduction") or "").strip(),
            "vod_play_from": "趣看看短剧",
            "vod_play_url": "#".join(eps),
        }
        return {"list": [detail]}

    # ============================================================
    # 播放解析（阿里云 VOD，核心）
    # ============================================================
    def _resolve_play(self, album_id, seq):
        cache_key = "%s:%s" % (album_id, seq)
        cached = self._cache_get(self._play_cache, cache_key, TTL_PLAY)
        if cached:
            return cached

        play_url = ""
        try:
            auth = self._get_json(
                API + "/web/v1/drama/play_auth",
                params={"albumId": album_id, "seq": seq},
                timeout=TIMEOUT_API,
            )
            if not auth or not auth.get("playAuth"):
                return ""

            info = _decode_playauth(auth["playAuth"])
            if not info:
                return ""

            params = {
                "AccessKeyId": info["AccessKeyId"],
                "Action": "GetPlayInfo",
                "VideoId": info["VideoId"],
                "Formats": "mp4",
                "AuthTimeout": 3600,
                "SecurityToken": info["SecurityToken"],
                "Format": "JSON",
                "Version": "2017-03-21",
                "SignatureMethod": "HMAC-SHA1",
                "SignatureVersion": "1.0",
                "SignatureNonce": str(uuid.uuid4()),
                "Channel": "HTML5",
                "AuthInfo": info["AuthInfo"],
            }
            params["Signature"] = _aliyun_sign(params, info["AccessKeySecret"])
            query = "&".join(
                "%s=%s" % (_aliyun_encode(k), _aliyun_encode(v))
                for k, v in params.items()
            )
            vod_url = "https://vod.%s.aliyuncs.com/?%s" % (info["Region"], query)

            r = self.session.get(vod_url, timeout=TIMEOUT_VOD)
            r.raise_for_status()
            j = r.json()
            plays = j.get("PlayInfoList", {}).get("PlayInfo") or []
            if plays:
                # 选最高清
                plays = sorted(
                    plays, key=lambda p: int(p.get("Height") or 0), reverse=True
                )
                play_url = plays[0].get("PlayURL") or ""
        except Exception:
            play_url = ""

        if play_url:
            self._cache_set(self._play_cache, cache_key, play_url, TTL_PLAY)
        return play_url

    def _parse_play_id(self, play_id):
        """解析 'albumId:seq' -> (album_id, seq)。"""
        parts = str(play_id).split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return parts[0], parts[1]
        return None, None

    def _prefetch_next(self, album_id, seq):
        """后台预取下一集播放地址，实现连续播放秒切。"""
        next_seq = int(seq) + 1
        key = "%s:%d" % (album_id, next_seq)
        with self._lock:
            if self._cache_get(self._play_cache, key, TTL_PLAY) or key in self._prefetching:
                return
            self._prefetching.add(key)

        def _job():
            try:
                self._resolve_play(album_id, next_seq)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._prefetching.discard(key)

        threading.Thread(target=_job, daemon=True).start()

    def playerContent(self, flag, id, vipFlags):
        album_id, seq = self._parse_play_id(id)
        if not album_id:
            return {"parse": 0, "playUrl": "", "url": ""}

        play_url = self._resolve_play(album_id, seq)
        if not play_url:
            return {"parse": 1, "playUrl": "", "url": ""}

        # 命中后预取下一集
        self._prefetch_next(album_id, seq)

        return {
            "parse": 0,
            "playUrl": "",
            "url": play_url,
            "header": {
                "User-Agent": UA,
                "Referer": HOST + "/",
                "Origin": HOST,
            },
        }

    # ============================================================
    # 搜索（站点无搜索接口：全库拉取 + 本地模糊匹配）
    # ============================================================
    def _load_full_library(self):
        """并发拉取全库（约228部 / 3页），带缓存 + 防重复拉取。"""
        with self._lock:
            if self._search_cache and time.time() - self._search_cache_time < TTL_SEARCH:
                return self._search_cache
            if self._library_loading:
                waiting = True
            else:
                waiting = False
                self._library_loading = True

        if waiting:
            # 锁外轮询等待预热线程完成（最多8秒）
            for _ in range(40):
                time.sleep(0.2)
                with self._lock:
                    if self._search_cache and time.time() - self._search_cache_time < TTL_SEARCH:
                        return self._search_cache
                    if not self._library_loading:
                        break
            with self._lock:
                if self._search_cache and time.time() - self._search_cache_time < TTL_SEARCH:
                    return self._search_cache
            # 超时仍未完成：自己也拉取（标记重复由 finally 释放）
            self._library_loading = True

        try:
            def fetch_page(page):
                data = self._get_json(
                    API + "/web/v1/drama/list",
                    params={"pageSize": 100, "currentPage": page},
                )
                return (data or {}).get("data") or []

            # 先单请求拿总页数
            data = self._get_json(
                API + "/web/v1/drama/list",
                params={"pageSize": 1, "currentPage": 1},
            )
            pag = (data or {}).get("pagination") or {}
            total_pages = max(1, pag.get("totalPages") or 1)

            items = []
            if ThreadPoolExecutor is not None and total_pages > 1:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [
                        pool.submit(fetch_page, p) for p in range(1, total_pages + 1)
                    ]
                    for f in futures:
                        items.extend(f.result(timeout=TIMEOUT_API))
            else:
                for p in range(1, total_pages + 1):
                    items.extend(fetch_page(p))

            # 去重（按 albumId）
            merged = {}
            for it in items:
                aid = str(it.get("albumId") or "")
                if aid:
                    merged[aid] = it
            library = list(merged.values())

            with self._lock:
                if not self._search_cache or time.time() - self._search_cache_time >= TTL_SEARCH:
                    self._search_cache = library
                    self._search_cache_time = int(time.time())
            return library
        finally:
            with self._lock:
                self._library_loading = False

    def searchContent(self, key, quick):
        key = (key or "").strip()
        if not key:
            return {"list": []}
        try:
            library = self._load_full_library()
            hits = []
            for it in library:
                title = it.get("title") or ""
                if key in title:
                    hits.append(it)
            # 按热度/更新时间排序，取前 60
            hits.sort(
                key=lambda it: (int(it.get("hot") or 0), int(it.get("updateTime") or 0)),
                reverse=True,
            )
            return {"list": self._cards(hits, limit=60)}
        except Exception:
            return {"list": []}
