# coding=utf-8
"""
目标站: ZIP0 (zip0.com)
模板: 影视聚合搜索 / 爬虫播放
站点类型: 综合影视
核心逻辑: 自适应调用 ZIP0 (TanStack Start) 的 server function 接口

自适应机制:
  1. 全量扫描 HTML 引用的所有 JS bundle，提取 server function hash
  2. 通过返回数据结构特征自动识别 catalog/home/search/detail
  3. 成功识别的 hash 持久化缓存到本地文件，避免每次重启重新探测
  4. 缓存 hash 调用失败时自动触发重新发现
  5. 请求头自动适配 CORS 校验

稳定性优化:
  1. 聚合结果本地持久化缓存，减少重复网络请求
  2. 分类页采用"缓存优先 + 增量更新"策略
  3. 搜索支持分页（本地分页，基于聚合结果）
  4. 减少并发数避免触发限流
"""
import os
import re
import sys
import json
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append('..')
from base.spider import Spider


class Spider(Spider):

    # 缓存文件路径（相对当前文件）
    CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zip0_hashes.json")
    AGG_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zip0_aggregate.json")

    def init(self, extend=""):
        self.site_url = "https://zip0.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': self.site_url + "/",
        }
        self._fn_headers_base = {
            'User-Agent': self.headers['User-Agent'],
            'Accept': 'application/x-ndjson, application/json',
            'x-tsr-serverFn': 'true',
            'Referer': self.site_url + "/",
        }
        self.fn_headers = {
            **self._fn_headers_base,
            'Origin': self.site_url,
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
        }
        self.default_pic = "https://pic.rmb.bdstatic.com/bjh/user/default.png"

        self.categories = {
            "movie": "电影",
            "tv": "长剧",
            "short": "短剧",
            "variety": "综艺",
            "anime": "动漫",
        }

        # 内置兜底 hash
        self._built_in_hashes = {
            "catalog": "8fa43bc249007c84c4782b4237f5181449e18cd1723d5656f5f3c45c42e2daab",
            "home":    "b42ee085174093515a801f91174a8e544266b579c62db3cecc6d13f99a17dd1d",
            "search":  "924908a6328d92c97055b1d048defe7b4f8102dee6907ac36be30470204c7535",
            "detail":  "85c7a6614d6048dc4b2243f27d66e8e342c417ea471b66856fcb1cbb9f8c9ee3",
        }
        self._hashes = {}
        self._sources = None
        self._discovery_done = False

        # 聚合缓存（内存）
        self._agg_cache = {}    # tid -> (videos, timestamp)
        self._agg_cache_ttl = 7200  # 2小时

        self._load_cached_hashes()
        self._load_agg_cache()

    # ==================== 持久化缓存 ====================

    def _load_cached_hashes(self):
        try:
            if os.path.exists(self.CACHE_FILE):
                with open(self.CACHE_FILE, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                if isinstance(cached, dict) and cached.get("catalog") and cached.get("home"):
                    self._hashes = {k: v for k, v in cached.items() if k in ("catalog", "home", "search", "detail")}
                    return
        except Exception:
            pass
        self._hashes = dict(self._built_in_hashes)

    def _save_cached_hashes(self):
        try:
            with open(self.CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._hashes, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_agg_cache(self):
        """加载聚合缓存"""
        try:
            if os.path.exists(self.AGG_CACHE_FILE):
                with open(self.AGG_CACHE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    now = time.time()
                    for tid, val in data.items():
                        if isinstance(val, dict) and 'videos' in val and 'time' in val:
                            if now - val['time'] < self._agg_cache_ttl:
                                self._agg_cache[tid] = (val['videos'], val['time'])
        except Exception:
            pass

    def _save_agg_cache(self):
        """持久化聚合缓存"""
        try:
            now = time.time()
            data = {}
            for tid, (videos, ts) in self._agg_cache.items():
                data[tid] = {'videos': videos, 'time': ts}
            with open(self.AGG_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ==================== 标签化 JSON (devalue) ====================

    def _enc(self, v):
        if v is None:
            return {"t": 0}
        if isinstance(v, bool):
            return {"t": 3, "b": v}
        if isinstance(v, str):
            return {"t": 1, "s": v}
        if isinstance(v, (int, float)):
            return {"t": 2, "n": v}
        if isinstance(v, dict):
            ks = list(v.keys())
            return {"t": 10, "i": 1, "p": {"k": ks, "v": [self._enc(v[k]) for k in ks]}, "o": 0}
        if isinstance(v, list):
            return {"t": 9, "i": 1, "a": [self._enc(x) for x in v], "o": 0}
        return {"t": 1, "s": str(v)}

    def _dec(self, node):
        if not isinstance(node, dict):
            return node
        t = node.get("t")
        if t == 1:
            return node.get("s")
        if t == 0:
            return None
        if t == 2:
            return node.get("n")
        if t == 3:
            return node.get("b")
        if t == 10:
            p = node.get("p", {}) or {}
            ks = p.get("k", []) or []
            vs = p.get("v", []) or []
            return {k: self._dec(v) for k, v in zip(ks, vs)}
        if t == 9:
            return [self._dec(x) for x in (node.get("a", []) or [])]
        return node

    def _fetch_text(self, url, headers=None, timeout=30):
        try:
            resp = self.fetch(url, headers=headers or self.headers)
            if not resp:
                return ""
            content = resp.text
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='ignore')
            return content or ""
        except Exception:
            return ""

    def _call_fn_raw(self, fn_hash, data, use_cors=True, timeout=15):
        """原始 server function 调用，返回解码后的数据或 None"""
        hdr = self.fn_headers if use_cors else self._fn_headers_base
        try:
            payload = {"t": {"t": 10, "i": 0, "p": {"k": ["data"], "v": [self._enc(data)]}, "o": 0},
                        "f": 127, "m": []}
            encoded = json.dumps(payload, ensure_ascii=False)
            url = "{}/_serverFn/{}?payload={}".format(
                self.site_url, fn_hash, urllib.parse.quote(encoded, safe=''))
            resp = self.fetch(url, headers=hdr)
            if not resp:
                return None
            text = resp.text
            if isinstance(text, bytes):
                text = text.decode('utf-8', errors='ignore')
            if not text:
                return None
            obj = json.loads(text)
            if isinstance(obj, dict) and obj.get("error"):
                return {"__error__": obj.get("error")}
            decoded = self._dec(obj)
            if isinstance(decoded, dict):
                return decoded.get("result", decoded)
            return decoded
        except Exception:
            return None

    def _call_fn(self, name, data, timeout=30):
        """带缓存失效重探的 server function 调用"""
        fn_hash = self._get_hash(name)
        if not fn_hash:
            return None
        result = self._call_fn_raw(fn_hash, data, use_cors=True)
        if result is None or (isinstance(result, dict) and result.get("__error__")):
            if not self._discovery_done:
                self._discover_hashes(force=True)
                new_hash = self._hashes.get(name)
                if new_hash and new_hash != fn_hash:
                    result = self._call_fn_raw(new_hash, data, use_cors=True)
        return result

    def _get_hash(self, name):
        h = self._hashes.get(name)
        if not h and not self._discovery_done:
            self._discover_hashes()
            h = self._hashes.get(name)
        return h

    # ==================== 全量动态发现 ====================

    def _discover_hashes(self, force=False):
        if self._discovery_done and not force:
            return
        self._discovery_done = True
        try:
            html = self._fetch_text(self.site_url + "/")
            if not html:
                return
            js_paths = list(dict.fromkeys(re.findall(r"/assets/[^\"\\'>\s]+\.js", html)))
            priority = ['video', 'function', 'route', 'index']
            js_paths.sort(key=lambda p: (
                next((i for i, k in enumerate(priority) if k in p.lower()), 99),
                p
            ))
            all_hashes = []
            for path in js_paths:
                bundle = self._fetch_text(self.site_url + path)
                if not bundle:
                    continue
                hashes = re.findall(r'\w\(\{method:`GET`\}\).handler\(\w\(`([0-9a-f]{64})`\)\)', bundle)
                if hashes:
                    all_hashes.extend(hashes)
            all_hashes = list(dict.fromkeys(all_hashes))
            if not all_hashes:
                return
            found = {}
            for h in all_hashes:
                if "catalog" in found and "home" in found:
                    break
                data = self._call_fn_raw(h, {})
                if data is None or (isinstance(data, dict) and data.get("__error__")):
                    continue
                if isinstance(data, list) and data and isinstance(data[0], dict) \
                        and "source" in data[0] and "sourceName" in data[0]:
                    found["catalog"] = h
                elif isinstance(data, dict) and "sections" in data:
                    found["home"] = h
            if "search" not in found:
                probe_args = {"query": "test", "source": "bfzy", "area": "all", "type": "all", "year": "all"}
                for h in all_hashes:
                    if h in found.values():
                        continue
                    data = self._call_fn_raw(h, probe_args)
                    if not isinstance(data, dict) or data.get("__error__"):
                        continue
                    if "items" in data:
                        items = data.get("items") or []
                        if items and isinstance(items[0], dict) and "source" in items[0] and "title" in items[0]:
                            found["search"] = h
                            break
            if "detail" not in found:
                probe_item = None
                sh = found.get("search") or self._hashes.get("search")
                if sh:
                    sd = self._call_fn_raw(sh, {"query": "流浪地球", "source": "bfzy",
                                                "area": "all", "type": "all", "year": "all"})
                    if isinstance(sd, dict):
                        items = sd.get("items") or []
                        if items and isinstance(items[0], dict) and items[0].get("source") and items[0].get("id"):
                            probe_item = items[0]
                for h in all_hashes:
                    if h in found.values():
                        continue
                    args = {"source": "bfzy", "id": "39605"}
                    if probe_item:
                        args = {"source": probe_item["source"], "id": str(probe_item["id"])}
                    data = self._call_fn_raw(h, args)
                    if isinstance(data, dict) and data.get("__error__"):
                        continue
                    if isinstance(data, dict) and "detail" in data:
                        detail = data["detail"]
                        if isinstance(detail, dict) and "episodes" in detail:
                            found["detail"] = h
                            break
            if found:
                self._hashes.update(found)
                self._save_cached_hashes()
        except Exception:
            pass

    # ==================== 源列表 ====================

    def get_sources(self):
        if self._sources is not None:
            return self._sources
        data = self._call_fn("catalog", {})
        if isinstance(data, list) and data:
            self._sources = data
        else:
            self._sources = [
                {"source": "dyttzy", "sourceName": "线路 1"},
                {"source": "ruyi",   "sourceName": "线路 2"},
                {"source": "bfzy",   "sourceName": "线路 3"},
                {"source": "ffzy",   "sourceName": "线路 4"},
                {"source": "zy360",  "sourceName": "线路 5"},
                {"source": "jisu",   "sourceName": "线路 6"},
                {"source": "mdzy",   "sourceName": "线路 7"},
                {"source": "zuid",   "sourceName": "线路 8"},
                {"source": "ikun",   "sourceName": "线路 9"},
                {"source": "lzi",    "sourceName": "线路 10"},
            ]
        return self._sources

    # ==================== 卡片转换 ====================

    def _item_to_vod(self, item):
        if not isinstance(item, dict):
            return None
        source = item.get("source") or ""
        vid = item.get("id") or ""
        if not source or not vid:
            return None
        vod_id = "{}:{}".format(source, vid)
        name = item.get("title") or ""
        pic = item.get("poster") or item.get("cover") or ""
        if pic and not pic.startswith("http"):
            pic = ""
        remarks = item.get("remarks") or ""
        if not remarks and item.get("score") and item.get("score") not in ("0", "0.0"):
            remarks = item.get("score")
        year = str(item.get("year") or "")
        area = item.get("area") or ""
        return {
            "vod_id": vod_id,
            "vod_name": name,
            "vod_pic": pic or self.default_pic,
            "vod_remarks": remarks,
            "vod_year": year,
            "vod_area": area,
        }

    # ==================== 聚合搜索（分类页/首页补充） ====================

    # 分类关键词配置
    _category_keywords = {
        "movie":  [("爱情", ["bfzy", "dyttzy", "ruyi", "ffzy"]),
                   ("喜剧", ["bfzy", "dyttzy", "ruyi"]),
                   ("动作", ["bfzy", "dyttzy"]),
                   ("科幻", ["bfzy", "dyttzy"]),
                   ("悬疑", ["bfzy"]),
                   ("剧情", ["bfzy", "dyttzy"]),
                   ("冒险", ["bfzy"]),
                   ("奇幻", ["bfzy"])],
        "tv":     [("爱情", ["bfzy", "dyttzy", "ruyi", "ffzy"]),
                   ("喜剧", ["bfzy", "dyttzy", "ruyi"]),
                   ("悬疑", ["bfzy", "dyttzy"]),
                   ("剧情", ["bfzy", "dyttzy", "ruyi"]),
                   ("冒险", ["bfzy"]),
                   ("科幻", ["bfzy"])],
        "short":  [("爱情", ["bfzy", "dyttzy"]),
                   ("喜剧", ["bfzy", "dyttzy"]),
                   ("剧情", ["bfzy"]),
                   ("悬疑", ["bfzy"])],
        "variety":[("喜剧", ["bfzy", "dyttzy", "ruyi"]),
                   ("爱情", ["bfzy"]),
                   ("综艺", ["bfzy"])],
        "anime":  [("动画", ["bfzy", "dyttzy", "ruyi"]),
                   ("科幻", ["bfzy"]),
                   ("冒险", ["bfzy"]),
                   ("喜剧", ["bfzy"])],
    }

    def _aggregate_by_category(self, tid, limit=90, use_cache=True):
        """根据分类聚合搜索，返回去重后的视频列表。优先使用缓存。"""
        # 检查内存缓存
        if use_cache and tid in self._agg_cache:
            videos, ts = self._agg_cache[tid]
            if time.time() - ts < self._agg_cache_ttl:
                return videos

        items_config = self._category_keywords.get(tid, [])
        if not items_config:
            return []

        videos = []
        seen = set()
        sources = self.get_sources()
        source_codes = [s.get("source") for s in sources if s.get("source")]

        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {}
                for kw, pri_sources in items_config:
                    used_sources = list(pri_sources)
                    for sc in source_codes:
                        if sc not in used_sources:
                            used_sources.append(sc)
                    for sc in used_sources:
                        if len(videos) >= limit:
                            break
                        futures[pool.submit(
                            self._search_one_source_safe, kw, sc
                        )] = (kw, sc)

                for fut in as_completed(futures, timeout=60):
                    if len(videos) >= limit:
                        break
                    try:
                        items = fut.result(timeout=10)
                    except Exception:
                        continue
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        vod = self._item_to_vod(item)
                        if vod and vod["vod_id"] not in seen:
                            seen.add(vod["vod_id"])
                            videos.append(vod)
                        if len(videos) >= limit:
                            break
        except Exception:
            pass

        # 存入内存缓存
        if videos:
            self._agg_cache[tid] = (videos, time.time())
            # 异步持久化
            try:
                cache_data = {}
                for t, (vs, ts) in self._agg_cache.items():
                    cache_data[t] = {'videos': vs, 'time': ts}
                with open(self.AGG_CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        return videos

    def _search_one_source_safe(self, keyword, source_code):
        """安全版单源搜索，吞掉所有异常"""
        try:
            data = self._call_fn("search", {
                "query": keyword, "source": source_code,
                "area": "all", "type": "all", "year": "all",
            })
            if isinstance(data, dict):
                return data.get("items", []) or []
        except Exception:
            pass
        return []

    # ==================== 首页 ====================

    def _home_sections(self):
        data = self._call_fn("home", {})
        if isinstance(data, dict):
            return data.get("sections", {}) or {}
        return {}

    def homeContent(self, filter):
        categories = [{"type_id": k, "type_name": v} for k, v in self.categories.items()]
        sections = self._home_sections()

        videos = []
        seen = set()
        for cat, items in sections.items():
            if not isinstance(items, list):
                continue
            for item in items:
                vod = self._item_to_vod(item)
                if vod and vod["vod_id"] not in seen:
                    seen.add(vod["vod_id"])
                    videos.append(vod)

        # 用各分类聚合搜索补充到60条
        if len(videos) < 60:
            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = {}
                    for cat_id in self.categories:
                        if len(videos) >= 60:
                            break
                        futures[pool.submit(self._aggregate_by_category, cat_id, 60 - len(videos), False)] = cat_id
                    for fut in as_completed(futures, timeout=50):
                        if len(videos) >= 60:
                            break
                        try:
                            extra = fut.result(timeout=15)
                        except Exception:
                            continue
                        if isinstance(extra, list):
                            for vod in extra:
                                if vod["vod_id"] not in seen:
                                    seen.add(vod["vod_id"])
                                    videos.append(vod)
                                if len(videos) >= 60:
                                    break
            except Exception:
                pass

        return {"class": categories, "list": videos[:60], "filters": self._build_filters()}

    def homeVideoContent(self):
        sections = self._home_sections()
        videos = []
        seen = set()
        for cat, items in sections.items():
            if not isinstance(items, list):
                continue
            for item in items:
                vod = self._item_to_vod(item)
                if vod and vod["vod_id"] not in seen:
                    seen.add(vod["vod_id"])
                    videos.append(vod)
        return {"list": videos[:30]}

    # ==================== 筛选器 ====================

    def _build_filters(self):
        return {
            "movie": [
                {"key": "area", "name": "地区", "value": [
                    {"n": "全部", "v": "all"},
                    {"n": "内地", "v": "内地"}, {"n": "香港", "v": "香港"},
                    {"n": "台湾", "v": "台湾"}, {"n": "美国", "v": "美国"},
                    {"n": "日本", "v": "日本"}, {"n": "韩国", "v": "韩国"},
                    {"n": "印度", "v": "印度"}, {"n": "欧洲", "v": "欧洲"},
                ]},
                {"key": "year", "name": "年份", "value": [
                    {"n": "全部", "v": "all"},
                    {"n": "2025", "v": "2025"}, {"n": "2024", "v": "2024"},
                    {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
                    {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"},
                    {"n": "经典", "v": "classic"},
                ]},
                {"key": "letter", "name": "字母", "value": [
                    {"n": "全部", "v": "all"}
                ] + [{"n": chr(i), "v": chr(i)} for i in range(65, 91)]},
            ],
            "tv": [
                {"key": "area", "name": "地区", "value": [
                    {"n": "全部", "v": "all"},
                    {"n": "内地", "v": "内地"}, {"n": "香港", "v": "香港"},
                    {"n": "台湾", "v": "台湾"}, {"n": "美国", "v": "美国"},
                    {"n": "日本", "v": "日本"}, {"n": "韩国", "v": "韩国"},
                    {"n": "印度", "v": "印度"},
                ]},
                {"key": "year", "name": "年份", "value": [
                    {"n": "全部", "v": "all"},
                    {"n": "2025", "v": "2025"}, {"n": "2024", "v": "2024"},
                    {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
                    {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"},
                    {"n": "经典", "v": "classic"},
                ]},
            ],
            "short": [
                {"key": "year", "name": "年份", "value": [
                    {"n": "全部", "v": "all"},
                    {"n": "2025", "v": "2025"}, {"n": "2024", "v": "2024"},
                    {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
                    {"n": "经典", "v": "classic"},
                ]},
            ],
            "variety": [
                {"key": "year", "name": "年份", "value": [
                    {"n": "全部", "v": "all"},
                    {"n": "2025", "v": "2025"}, {"n": "2024", "v": "2024"},
                    {"n": "2023", "v": "2023"}, {"n": "经典", "v": "classic"},
                ]},
            ],
            "anime": [
                {"key": "area", "name": "地区", "value": [
                    {"n": "全部", "v": "all"},
                    {"n": "日本", "v": "日本"}, {"n": "美国", "v": "美国"},
                    {"n": "内地", "v": "内地"},
                ]},
                {"key": "year", "name": "年份", "value": [
                    {"n": "全部", "v": "all"},
                    {"n": "2025", "v": "2025"}, {"n": "2024", "v": "2024"},
                    {"n": "2023", "v": "2023"}, {"n": "经典", "v": "classic"},
                ]},
            ],
        }

    # ==================== 分类 ====================

    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg) if pg else 1
        limit = 30
        videos = []
        seen = set()

        # 1. 从首页 sections 取该分类的固定内容（快速，不触发网络）
        sections = self._home_sections()
        items = sections.get(tid, []) if isinstance(sections, dict) else []
        if isinstance(items, list):
            for item in items:
                vod = self._item_to_vod(item)
                if vod and vod["vod_id"] not in seen:
                    seen.add(vod["vod_id"])
                    videos.append(vod)

        # 2. 使用缓存的聚合结果（有缓存直接分页，无缓存则触发聚合）
        #    先检查内存缓存
        cached_videos = None
        if tid in self._agg_cache:
            cached_videos, _ = self._agg_cache[tid]
            if time.time() - _ < self._agg_cache_ttl:
                pass  # 使用缓存
            else:
                cached_videos = None

        if cached_videos is None:
            # 缓存未命中，触发聚合（最多取 limit*3 条）
            extra_videos = self._aggregate_by_category(tid, limit * 3, use_cache=True)
            for vod in extra_videos:
                if vod["vod_id"] not in seen:
                    seen.add(vod["vod_id"])
                    videos.append(vod)
        else:
            # 使用缓存
            for vod in cached_videos:
                if vod["vod_id"] not in seen:
                    seen.add(vod["vod_id"])
                    videos.append(vod)

        # 3. 应用筛选器
        if extend:
            area = extend.get("area", "all")
            year = extend.get("year", "all")
            letter = extend.get("letter", "all")
            if area != "all" or year != "all" or letter != "all":
                filtered = []
                for vod in videos:
                    match = True
                    if area != "all" and vod.get("vod_area"):
                        if area not in vod.get("vod_area", ""):
                            match = False
                    if year != "all" and year != "classic":
                        vy = vod.get("vod_year", "")
                        if vy and vy != year:
                            match = False
                    if letter != "all":
                        vn = vod.get("vod_name", "")
                        if vn and vn[0].upper() != letter:
                            match = False
                    if match:
                        filtered.append(vod)
                videos = filtered

        total = len(videos)
        start = (page - 1) * limit
        end = start + limit
        return {
            "list": videos[start:end],
            "page": page,
            "pagecount": max(1, (total + limit - 1) // limit),
            "limit": limit,
            "total": total,
        }

    # ==================== 搜索 ====================

    def _search_one_source(self, source_code, key, area="all", typ="all", year="all"):
        data = self._call_fn("search", {
            "query": key, "source": source_code,
            "area": area, "type": typ, "year": year,
        })
        if isinstance(data, dict):
            return data.get("items", []) or []
        return []

    def searchContent(self, key, quick, pg="1"):
        page = int(pg) if pg else 1
        limit = 30
        if not key:
            return {"list": []}

        # 逐源聚合搜索（每次搜索都重新请求，因为关键词不同）
        sources = self.get_sources()
        source_codes = [s.get("source") for s in sources if s.get("source")]
        all_items = []
        seen_keys = set()

        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {}
                for sc in source_codes:
                    futures[pool.submit(self._search_one_source, sc, key)] = sc
                for fut in as_completed(futures, timeout=25):
                    try:
                        items = fut.result(timeout=10)
                    except Exception:
                        continue
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        t = (item.get("title") or "").strip()
                        y = str(item.get("year") or "").strip()
                        k = (t, y)
                        if not t or k in seen_keys:
                            continue
                        seen_keys.add(k)
                        all_items.append(item)
        except Exception:
            for sc in source_codes:
                try:
                    items = self._search_one_source(sc, key)
                    if items:
                        all_items.extend(items)
                except Exception:
                    continue

        # 排序：有海报的优先，按评分排序
        all_items.sort(key=lambda it: (
            0 if (isinstance(it, dict) and it.get("poster")) else 1,
            -float(it["score"]) if isinstance(it, dict) and self._safe_float(it.get("score")) else 0,
        ))

        # 本地分页：收集所有结果再切片
        videos = []
        for item in all_items:
            vod = self._item_to_vod(item)
            if vod:
                videos.append(vod)

        total = len(videos)
        start = (page - 1) * limit
        end = start + limit
        return {
            "list": videos[start:end],
            "page": page,
            "pagecount": max(1, (total + limit - 1) // limit),
            "limit": limit,
            "total": total,
        }

    @staticmethod
    def _safe_float(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    # ==================== 详情 ====================

    def _parse_vod_id(self, vid):
        if not vid:
            return "", ""
        if ":" in vid:
            source, _, idv = vid.partition(":")
            return source, idv
        return "", vid

    def _build_play_line(self, source, source_name, episodes):
        if not isinstance(episodes, list) or not episodes:
            return None
        parts = []
        for ep in episodes:
            if not isinstance(ep, dict):
                continue
            name = ep.get("name") or ep.get("title") or ""
            url = ep.get("url") or ep.get("link") or ep.get("play_url") or ""
            if not url:
                continue
            if not name:
                name = "播放"
            parts.append("{}${}".format(name, url))
        if not parts:
            return None
        return (source_name or source or "默认线路", "#".join(parts))

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        vid = ids[0]
        source, idv = self._parse_vod_id(vid)
        if not source or not idv:
            return {"list": []}

        data = self._call_fn("detail", {"source": source, "id": idv})
        if not isinstance(data, dict):
            return {"list": []}

        detail = data.get("detail") or {}
        if not isinstance(detail, dict) or not detail:
            return {"list": []}

        title = detail.get("title") or vid
        pic = detail.get("poster") or ""
        if pic and not pic.startswith("http"):
            pic = ""
        content = detail.get("description") or detail.get("intro") or ""
        actors = detail.get("actors") or ""
        director = detail.get("director") or ""
        year = str(detail.get("year") or "")
        area = detail.get("area") or ""
        category = detail.get("category") or ""
        remarks = detail.get("remarks") or ""

        play_lines = []
        main_line = self._build_play_line(source, detail.get("sourceName"), detail.get("episodes"))
        if main_line:
            play_lines.append(main_line)

        alternatives = data.get("alternatives") or []
        if isinstance(alternatives, list) and alternatives:
            try:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futs = {}
                    for alt in alternatives:
                        if not isinstance(alt, dict):
                            continue
                        a_src = alt.get("source")
                        a_id = alt.get("id")
                        a_name = alt.get("sourceName")
                        if not a_src or not a_id or a_src == source:
                            continue
                        futs[pool.submit(self._call_fn, "detail",
                                         {"source": a_src, "id": str(a_id)})] = (a_src, a_name)
                    for fut in as_completed(futs, timeout=15):
                        a_src, a_name = futs[fut]
                        try:
                            a_data = fut.result(timeout=8)
                        except Exception:
                            a_data = None
                        if not isinstance(a_data, dict):
                            continue
                        a_detail = a_data.get("detail") or {}
                        line = self._build_play_line(a_src, a_name or a_detail.get("sourceName"),
                                                      a_detail.get("episodes"))
                        if line:
                            play_lines.append(line)
            except Exception:
                pass

        if not play_lines:
            play_lines.append(("默认线路", "播放${}/watch?source={}&id={}&episode=1".format(
                self.site_url, source, idv)))

        play_from = "$$$".join(ln[0] for ln in play_lines)
        play_url = "$$$".join(ln[1] for ln in play_lines)

        return {"list": [{
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": pic or self.default_pic,
            "vod_content": content,
            "vod_actor": actors,
            "vod_director": director,
            "vod_year": year,
            "vod_area": area,
            "vod_type": category,
            "vod_remarks": remarks,
            "vod_play_from": play_from,
            "vod_play_url": play_url,
        }]}

    # ==================== 播放 ====================

    def playerContent(self, flag, id, vipFlags):
        play_url = id
        if "$" in id:
            play_url = id.split("$")[-1]
        play_url = (play_url or "").strip()

        header = {
            'User-Agent': self.headers['User-Agent'],
            'Referer': self.site_url + "/",
        }

        if not play_url:
            return {"parse": 1, "url": id, "header": header}

        if '.m3u8' in play_url or '.mp4' in play_url or 'm3u8' in play_url.lower():
            return {"parse": 0, "url": play_url, "header": header}

        if self.site_url in play_url:
            try:
                m = re.search(r'source=([^&]+)&id=([^&]+)', play_url)
                if m:
                    a_src, a_id = m.group(1), m.group(2)
                    data = self._call_fn("detail", {"source": a_src, "id": a_id})
                    if isinstance(data, dict):
                        d = data.get("detail") or {}
                        eps = d.get("episodes") or []
                        if eps and isinstance(eps[0], dict):
                            u = eps[0].get("url") or ""
                            if u:
                                return {"parse": 0, "url": u, "header": header}
            except Exception:
                pass

        return {"parse": 1, "url": play_url, "header": header}
