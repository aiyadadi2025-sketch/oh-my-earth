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
"""
import os
import re
import sys
import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append('..')
from base.spider import Spider


class Spider(Spider):

    # 缓存文件路径（相对当前文件）
    CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zip0_hashes.json")

    def init(self, extend=""):
        self.site_url = "https://zip0.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': self.site_url + "/",
        }
        # 基础 server function 头（不含 CORS，先试探）
        self._fn_headers_base = {
            'User-Agent': self.headers['User-Agent'],
            'Accept': 'application/x-ndjson, application/json',
            'x-tsr-serverFn': 'true',
            'Referer': self.site_url + "/",
        }
        # 完整 CORS 头（Cloudflare 校验需要）
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

        # 内置兜底 hash（最后一次确认有效的值，作为极速冷启动）
        self._built_in_hashes = {
            "catalog": "8fa43bc249007c84c4782b4237f5181449e18cd1723d5656f5f3c45c42e2daab",
            "home":    "b42ee085174093515a801f91174a8e544266b579c62db3cecc6d13f99a17dd1d",
            "search":  "924908a6328d92c97055b1d048defe7b4f8102dee6907ac36be30470204c7535",
            "detail":  "85c7a6614d6048dc4b2243f27d66e8e342c417ea471b66856fcb1cbb9f8c9ee3",
        }
        self._hashes = {}
        self._sources = None
        self._discovery_done = False  # 本次生命周期内是否已完成发现

        # 启动时加载缓存或内置 hash
        self._load_cached_hashes()

    # ==================== 持久化缓存 ====================

    def _load_cached_hashes(self):
        """从本地文件加载缓存的 hash"""
        try:
            if os.path.exists(self.CACHE_FILE):
                with open(self.CACHE_FILE, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                if isinstance(cached, dict) and cached.get("catalog") and cached.get("home"):
                    self._hashes = {k: v for k, v in cached.items() if k in ("catalog", "home", "search", "detail")}
                    # 验证缓存是否仍有效（异步延迟验证，不阻塞启动）
                    return
        except Exception:
            pass
        # 无缓存或缓存损坏，使用内置兜底
        self._hashes = dict(self._built_in_hashes)

    def _save_cached_hashes(self):
        """将发现的 hash 持久化到本地"""
        try:
            with open(self.CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._hashes, f, ensure_ascii=False, indent=2)
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
            # 先整体 devalue 解码，再取 result 字段
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
            # 可能是缓存 hash 失效，触发重新发现后重试一次
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
        """从所有 JS bundle 中自动发现并识别 server function hash"""
        if self._discovery_done and not force:
            return
        self._discovery_done = True

        try:
            # 1. 获取首页 HTML，提取所有 JS bundle 路径
            html = self._fetch_text(self.site_url + "/")
            if not html:
                return

            # 提取所有 .js 资源（去重，优先包含 video/functions 关键字的）
            js_paths = list(dict.fromkeys(re.findall(r"/assets/[^\"\\'>\s]+\.js", html)))
            # 排序：把疑似视频相关的放前面
            priority = ['video', 'function', 'route', 'index']
            js_paths.sort(key=lambda p: (
                next((i for i, k in enumerate(priority) if k in p.lower()), 99),
                p
            ))

            # 2. 从所有 bundle 中提取 hash
            all_hashes = []
            for path in js_paths:
                bundle = self._fetch_text(self.site_url + path)
                if not bundle:
                    continue
                # 兼容多种 minifier 生成的变量名：_({method:`GET`}).handler(d(`HASH`)) 或 u({method:`GET`}).handler(o(`HASH`))
                hashes = re.findall(r'\w\(\{method:`GET`\}\)\.handler\(\w\(`([0-9a-f]{64})`\)\)', bundle)
                if hashes:
                    all_hashes.extend(hashes)

            all_hashes = list(dict.fromkeys(all_hashes))  # 去重保持顺序
            if not all_hashes:
                return

            # 3. 智能识别各 hash 功能
            found = {}  # name -> hash

            # 3.1 探测 catalog / home（空参数即可）
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

            # 3.2 探测 search（需要带参数）
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

            # 3.3 探测 detail（需要真实 source + id）
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

            # 4. 更新缓存
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
            # 兜底默认源
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
        return {"class": categories, "list": videos[:30], "filters": {}}

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

    # ==================== 分类 ====================

    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg) if pg else 1
        limit = 30
        sections = self._home_sections()
        items = sections.get(tid, []) if isinstance(sections, dict) else []
        if not isinstance(items, list):
            items = []
        videos = []
        seen = set()
        for item in items:
            vod = self._item_to_vod(item)
            if vod and vod["vod_id"] not in seen:
                seen.add(vod["vod_id"])
                videos.append(vod)
        return {
            "list": videos[:limit],
            "page": page,
            "pagecount": 1,
            "limit": limit,
            "total": len(videos),
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
        # 尝试聚合搜索（source=all）
        data = self._call_fn("search", {"query": key, "source": "all", "area": "all", "type": "all", "year": "all"})
        all_items = []
        if isinstance(data, dict):
            items = data.get("items", []) or []
            if isinstance(items, list) and items:
                all_items.extend(items)
        # 如果结果为空，fallback 到逐源搜索
        if not all_items:
            sources = self.get_sources()
            source_codes = [s.get("source") for s in sources if s.get("source")]
            try:
                with ThreadPoolExecutor(max_workers=5) as pool:
                    futures = {pool.submit(self._search_one_source, sc, key): sc for sc in source_codes}
                    for fut in as_completed(futures, timeout=20):
                        try:
                            items = fut.result(timeout=8)
                        except Exception:
                            items = []
                        if items:
                            all_items.extend(items)
            except Exception:
                for sc in source_codes:
                    try:
                        items = self._search_one_source(sc, key)
                        if items:
                            all_items.extend(items)
                    except Exception:
                        continue

        videos = []
        seen = set()
        all_items.sort(key=lambda it: (
            0 if (isinstance(it, dict) and it.get("poster")) else 1,
            -float(it["score"]) if isinstance(it, dict) and self._safe_float(it.get("score")) else 0,
        ))
        for item in all_items:
            if not isinstance(item, dict):
                continue
            t = (item.get("title") or "").strip()
            y = str(item.get("year") or "").strip()
            k = (t, y)
            if not t or k in seen:
                continue
            seen.add(k)
            vod = self._item_to_vod(item)
            if vod:
                videos.append(vod)
            if len(videos) >= limit:
                break

        return {
            "list": videos,
            "page": page,
            "pagecount": 1,
            "limit": limit,
            "total": len(videos),
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
                with ThreadPoolExecutor(max_workers=5) as pool:
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
