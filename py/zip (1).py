# coding=utf-8
"""
目标站: ZIP0 (zip0.com)
模板: 影视聚合搜索 / 爬虫播放
站点类型: 综合影视
核心逻辑: 调用 ZIP0 (TanStack Start) 的 server function 接口，提取视频信息与播放链接

说明:
  ZIP0 已从 Next.js 迁移到 TanStack Start，页面不再包含 __NEXT_DATA__，
  所有数据通过 /_serverFn/{hash} 端点以「标签化 JSON (devalue)」协议交互。
  本脚本直接调用这些 server function 接口获取数据。

接口 (server function hash，会随站点构建变化，已内置动态发现兜底):
  - 源目录 catalog : 返回 [{source, sourceName}, ...]
  - 首页 home      : 返回 {sections: {movie,tv,short,variety,anime: [item]}, ...}
  - 搜索 search    : 入参 {query, source, area, type, year} -> {items:[item], health}
  - 详情 detail    : 入参 {source, id} -> {detail:{...,episodes:[{name,url}]}, alternatives, relatedVideos}

数据项字段: id, source, sourceName, title, poster, year, remarks, category,
            area, language, score, episodeCount, updatedAt
vod_id 编码: "source:id"  例如 "bfzy:39605"
"""
import re
import sys
import json
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append('..')
from base.spider import Spider


class Spider(Spider):

    def init(self, extend=""):
        self.site_url = "https://zip0.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': self.site_url + "/",
        }
        # server function 调用专用头（修复：补充 CORS/Origin 头，避免 403）
        self.fn_headers = {
            'User-Agent': self.headers['User-Agent'],
            'Accept': 'application/x-ndjson, application/json',
            'x-tsr-serverFn': 'true',
            'Referer': self.site_url + "/",
            'Origin': self.site_url,
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
        }
        self.default_pic = "https://pic.rmb.bdstatic.com/bjh/user/default.png"

        # 分类 (type_id 与首页 sections 的 key 一致)
        self.categories = {
            "movie": "电影",
            "tv": "长剧",
            "short": "短剧",
            "variety": "综艺",
            "anime": "动漫",
        }

        # 当前构建的 server function hash (硬编码，作为主用；失效时自动动态发现)
        # 注意：以下 hash 已随站点重新构建而失效，首次运行时会通过动态发现自动更新
        self._hashes = {
            "catalog": "8fa43bc249007c84c4782b4237f5181449e18cd1723d5656f5f3c45c42e2daab",
            "home":    "b42ee085174093515a801f91174a8e544266b579c62db3cecc6d13f99a17dd1d",
            "search":  "924908a6328d92c97055b1d048defe7b4f8102dee6907ac36be30470204c7535",
            "detail":  "85c7a6614d6048dc4b2243f27d66e8e342c417ea471b66856fcb1cbb9f8c9ee3",
        }
        self._discovered = False  # 是否已尝试过动态发现

        # 默认源列表 (动态获取失败时的兜底)
        self.default_sources = [
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
        self._sources = None

    # ==================== 标签化 JSON (devalue) 编解码 ====================

    def _enc(self, v):
        """将 Python 值编码为 devalue 标签节点 (用于构造请求 payload)"""
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
        """将 devalue 标签节点解码为 Python 值 (用于解析响应)"""
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
        # 引用节点或未知类型: 直接返回原始
        return node

    @staticmethod
    def _result(obj):
        """从响应中取出 result 字段"""
        if isinstance(obj, dict) and "result" in obj:
            return obj["result"]
        return obj

    # ==================== HTTP / server function 调用 ====================

    def _fetch_text(self, url, headers=None, timeout=30):
        """获取文本内容"""
        try:
            h = headers or self.headers
            resp = self.fetch(url, headers=h)
            if not resp:
                return ""
            content = resp.text
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='ignore')
            return content or ""
        except Exception:
            return ""

    def _call_fn(self, name, data, timeout=30):
        """调用 server function 并返回解码后的 result

        name: catalog / home / search / detail
        data: 传入参数 dict
        """
        fn_hash = self._get_hash(name)
        if not fn_hash:
            return None
        payload = {"t": {"t": 10, "i": 0, "p": {"k": ["data"], "v": [self._enc(data)]}, "o": 0},
                    "f": 127, "m": []}
        encoded = json.dumps(payload, ensure_ascii=False)
        url = "{}/_serverFn/{}?payload={}".format(
            self.site_url, fn_hash, urllib.parse.quote(encoded, safe=''))
        try:
            resp = self.fetch(url, headers=self.fn_headers)
            if not resp:
                return None
            text = resp.text
            if isinstance(text, bytes):
                text = text.decode('utf-8', errors='ignore')
            if not text:
                return None
            obj = json.loads(text)
            # 如果返回错误，尝试触发一次动态发现后重试
            if isinstance(obj, dict) and obj.get("error"):
                if not self._discovered:
                    self._discover_hashes()
                    return self._call_fn(name, data, timeout)
                return None
            return self._result(self._dec(obj))
        except Exception:
            # 接口异常: 首次触发动态发现后重试一次
            if not self._discovered:
                self._discover_hashes()
                try:
                    return self._call_fn(name, data, timeout)
                except Exception:
                    return None
            return None

    # ==================== hash 动态发现 (兜底) ====================

    def _get_hash(self, name):
        h = self._hashes.get(name)
        if not h and not self._discovered:
            self._discover_hashes()
            h = self._hashes.get(name)
        return h

    def _discover_hashes(self):
        """从当前 JS bundle 中解析所有 server function hash，并探测识别各功能

        当硬编码 hash 因站点重新构建而失效时自动触发。
        """
        if self._discovered:
            return
        self._discovered = True
        found = set()  # 本次发现已识别的功能名
        try:
            html = self._fetch_text(self.site_url + "/")
            m = re.search(r'/assets/index-[A-Za-z0-9_-]+\.js', html)
            if not m:
                return
            bundle = self._fetch_text(self.site_url + m.group(0))
            if not bundle:
                return
            # 修复：提取所有 GET server function hash（兼容新旧 bundle 变量名）
            # 旧格式: _({method:`GET`}).handler(d(`HASH`))
            # 新格式: u({method:`GET`}).handler(o(`HASH`))
            hashes = re.findall(r'\w\(\{method:`GET`\}\)\.handler\(\w\(`([0-9a-f]{64})`\)\)', bundle)
            if not hashes:
                return

            need_args = []  # 用 {} 探测无结果、需带参再探的 hash

            # 1) 用空参数探测: catalog / home 可直接返回数据
            for h in hashes:
                if "catalog" in found and "home" in found:
                    break
                data = self._probe(h, {})
                if data is None:
                    need_args.append(h)
                    continue
                if isinstance(data, list) and data and isinstance(data[0], dict) and "source" in data[0] \
                        and "sourceName" in data[0]:
                    if "catalog" not in found:
                        self._hashes["catalog"] = h
                        found.add("catalog")
                elif isinstance(data, dict) and "sections" in data:
                    if "home" not in found:
                        self._hashes["home"] = h
                        found.add("home")
                else:
                    need_args.append(h)

            # home 可能因响应较大偶发超时, 重试一次未命中的 hash
            if "home" not in found:
                for h in hashes:
                    if "home" in found:
                        break
                    data = self._probe(h, {})
                    if isinstance(data, dict) and "sections" in data:
                        self._hashes["home"] = h
                        found.add("home")

            # 2) 探测 search: 入参含 query/source, 且返回的 items 必须是视频项(含 source+title)
            if "search" not in found:
                s_args = {"query": "test", "source": "bfzy",
                          "area": "all", "type": "all", "year": "all"}
                cands = need_args + [h for h in hashes if h not in need_args]
                for h in cands:
                    if h == self._hashes.get("catalog") or h == self._hashes.get("home"):
                        continue
                    data = self._probe(h, s_args)
                    if not isinstance(data, dict) or "items" not in data:
                        continue
                    items = data.get("items") or []
                    if items and isinstance(items[0], dict) and "source" in items[0] \
                            and "title" in items[0]:
                        self._hashes["search"] = h
                        found.add("search")
                        break

            # 3) 探测 detail: 先用 search 拿一个真实视频 item，再用其 source:id 探测
            if "detail" not in found:
                probe_item = None
                sh = self._hashes.get("search")
                if sh:
                    sd = self._probe(sh, {"query": "流浪地球", "source": "bfzy",
                                          "area": "all", "type": "all", "year": "all"})
                    if isinstance(sd, dict):
                        items = sd.get("items") or []
                        if items and isinstance(items[0], dict) and items[0].get("source") \
                                and items[0].get("id"):
                            probe_item = items[0]
                for h in hashes:
                    if h in (self._hashes.get("catalog"), self._hashes.get("home"),
                             self._hashes.get("search")):
                        continue
                    args = {"source": "bfzy", "id": "39605"}
                    if probe_item:
                        args = {"source": probe_item["source"], "id": str(probe_item["id"])}
                    data = self._probe(h, args)
                    if isinstance(data, dict) and "detail" in data:
                        self._hashes["detail"] = h
                        found.add("detail")
                        break
        except Exception:
            pass

    def _probe(self, fn_hash, data):
        """用指定 hash 调用一次并返回 result (探测用，出错返回 None)"""
        try:
            payload = {"t": {"t": 10, "i": 0, "p": {"k": ["data"], "v": [self._enc(data)]}, "o": 0},
                        "f": 127, "m": []}
            encoded = json.dumps(payload, ensure_ascii=False)
            url = "{}/_serverFn/{}?payload={}".format(
                self.site_url, fn_hash, urllib.parse.quote(encoded, safe=''))
            resp = self.fetch(url, headers=self.fn_headers)
            if not resp:
                return None
            text = resp.text
            if isinstance(text, bytes):
                text = text.decode('utf-8', errors='ignore')
            if not text:
                return None
            obj = json.loads(text)
            if isinstance(obj, dict) and obj.get("error"):
                return None
            return self._result(self._dec(obj))
        except Exception:
            return None

    # ==================== 源列表 ====================

    def get_sources(self):
        """获取可用源列表 (动态获取，失败用默认)"""
        if self._sources is not None:
            return self._sources
        data = self._call_fn("catalog", {})
        if isinstance(data, list) and data:
            self._sources = data
        else:
            self._sources = self.default_sources
        return self._sources

    # ==================== 卡片转换 ====================

    def _item_to_vod(self, item, with_remarks=True):
        """将接口返回的 item 转为 TVBox 卡片"""
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
        vod = {
            "vod_id": vod_id,
            "vod_name": name,
            "vod_pic": pic or self.default_pic,
            "vod_remarks": remarks,
            "vod_year": year,
            "vod_area": area,
        }
        return vod

    # ==================== 首页 ====================

    def _home_sections(self):
        """获取首页 sections 数据 {movie:[], tv:[], ...}"""
        data = self._call_fn("home", {})
        if isinstance(data, dict):
            return data.get("sections", {}) or {}
        return {}

    def homeContent(self, filter):
        """首页: 分类列表 + 推荐视频"""
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
        """首页推荐视频列表"""
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
        """分类列表 (取首页对应分类的近期更新)

        注: ZIP0 的分类页本身即为近期更新聚合，接口不支持翻页，故仅返回第 1 页。
        """
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

    # ==================== 搜索（修改后） ====================
    def searchContent(self, key, quick, pg="1"):
        """搜索: 顺序查询各源，合并去重，失败时自动重新发现 hash"""
        page = int(pg) if pg else 1
        limit = 30
        if not key:
            return {"list": []}
        
        sources = self.get_sources()
        source_codes = [s.get("source") for s in sources if s.get("source")]
        
        all_items = []
        # 尝试调用搜索，若失败则重新发现hash后重试
        for attempt in range(2):
            for sc in source_codes:
                try:
                    data = self._call_fn("search", {
                        "query": key, "source": sc,
                        "area": "all", "type": "all", "year": "all",
                    })
                    if isinstance(data, dict):
                        items = data.get("items", []) or []
                        if items:
                            all_items.extend(items)
                except Exception:
                    continue
            if all_items:
                break
            # 若没有结果，可能hash失效，触发重新发现（_discover_hashes已在_call_fn内部尝试，但再主动调用一次）
            if attempt == 0:
                self._discover_hashes()
        
        # 按 title 去重，保留首个
        videos = []
        seen = set()
        for item in all_items:
            if not isinstance(item, dict):
                continue
            t = (item.get("title") or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
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

    # ==================== 详情 ====================

    def _parse_vod_id(self, vid):
        """从 'source:id' 解析出 (source, id)"""
        if not vid:
            return "", ""
        if ":" in vid:
            source, _, idv = vid.partition(":")
            return source, idv
        return "", vid

    def _build_play_line(self, source, source_name, episodes):
        """由 episodes 构建一条播放线路 (name$url#name$url)"""
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
        """获取视频详情 (含播放选集 + 多线路)"""
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

        # ---- 构建多线路播放 ----
        play_lines = []
        # 主线路 (当前源)
        main_line = self._build_play_line(source, detail.get("sourceName"), detail.get("episodes"))
        if main_line:
            play_lines.append(main_line)

        # 备用线路: 对 alternatives 逐个取其 episodes (并发)
        alternatives = data.get("alternatives") or []
        if isinstance(alternatives, list) and alternatives:
            alt_tasks = []
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

        # 兜底: 若无任何可用线路
        if not play_lines:
            play_lines.append(("默认线路", "播放${}/watch?source={}&id={}&episode=1".format(
                self.site_url, source, idv)))

        play_from = "$$$".join(ln[0] for ln in play_lines)
        play_url = "$$$".join(ln[1] for ln in play_lines)

        result = {
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
        }
        return {"list": [result]}

    # ==================== 播放 ====================

    def playerContent(self, flag, id, vipFlags):
        """获取播放链接

        id 格式: ep_title$url (从 vod_play_url 拆分而来)，url 一般为直链 m3u8
        """
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

        # m3u8 / mp4 直链直接播放
        if '.m3u8' in play_url or '.mp4' in play_url or 'm3u8' in play_url.lower():
            return {
                "parse": 0,
                "url": play_url,
                "header": header,
            }

        # 本站 watch 链接: 尝试解析出 m3u8
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

        # 其他情况交给 webview 嗅探
        return {
            "parse": 1,
            "url": play_url,
            "header": header,
        }