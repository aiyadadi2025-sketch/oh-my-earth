"""
速搜源 v4.1 - 配置驱动版
改动:
  1. 新增官方配置端口 :37668, API端口 :20371, 解析端口 :30691
  2. 支持动态 api_prefix（:20371 需 /2233 前缀）
  3. warm_up 顺序调整：先配置 -> 后首页，更接近 APP 启动时序
  4. _fetch_api headers 补全 X-Requested-With / loginuuid / android_id
  5. 配置路径增加 /bm/xgx.json 等变体
  6. 解析接口优先使用配置下发的官方地址
"""

import base64
import hashlib
import json
import random
import re
import sys
import time
from urllib.parse import parse_qs, quote, urlsplit

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

requests.packages.urllib3.disable_warnings()

try:
    from base.spider import Spider
except ImportError:
    class Spider:
        def init(self, extend=""):
            pass


class Spider(Spider):
    def getName(self):
        return "速搜"

    def init(self, extend=""):
        try:
            super().init(extend)
        except Exception:
            pass
        self.host = "43.248.128.251"
        # API端口(首页/分类/搜索/详情)
        # :20371 是 dy.json 官方推荐主端口（需 /2233 前缀）
        self.api_ports = [20371, 2233, 22868, 18414, 24285, 16551, 21091, 15946]
        # 123Pan解析端口
        self.jx_ports = [30691, 30499, 30462, 36122, 31617, 37763]
        # 配置端口(加载dy.json等, 从中获取动态解析地址)
        self.config_ports = [37668, 32589, 15281, 35673, 18216, 38602, 16426, 39690, 35357, 16113]
        # JQQ硬编码备用端口(配置失效时直接轮询)
        self.jqq_ports = [30691, 38148, 38858, 38465, 36434]
        self.api_prefix = "/api.php/app"
        self.jx_path = "/jx/123pan/10086.php"
        self.ua_dart = "Dart/3.9 (dart:io)"
        self.ua_android = "Mozilla/5.0 (Linux; Android 16; 23046RP50C Build/BP4A.251205.006; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/150.0.7871.47 Safari/537.36"
        self.android_id = "a59aec7097c16a63"
        self.device_id = "39AF593DF90F81DF36AA82877CF1D17E"
        self.token = "5e36258359ced844b96412c7679c8374"
        self.login_uuid = "d2a84a9a3353d670af48b16ce7318840"
        self.app_id = "com.sjz.ss"
        self.jx_m = "kfOgorEp5/chYFZBRzDxRQ=="
        self.config_key = b"ahsp123456789012"
        self.jqq_key = b"opasdfghopasdfgh"
        self.pan_api = "https://api.123278.com/api/share/get"
        self.download_host = "https://download-cdn.cjjd19.com"
        self.vip_host = "https://1135-vip-download-cdn.123295.com"
        self.active_jx_url = ""
        self.active_jx_m = ""
        self.active_jqq_url = ""
        self.config_expires_at = 0
        self.session = requests.Session()
        retry = Retry(total=2, backoff_factor=0.4, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # ===== 限流 + 缓存 =====
        self._last_api_call = 0.0
        self._api_min_interval = 0.5
        self._share_cache = {}
        self._share_ttl = 300
        self._bad_ports = {}
        self._bad_port_ttl = 30          # 异常端口封禁30秒
        self._empty_port_ttl = 5         # 空响应(限流)仅封禁5秒,快速重试
        self._last_session_active = 0
        self._session_active_interval = 30  # 30秒内至少激活一次会话

        # 启动预热: 模拟APP打开, 激活服务端会话标记
        self._warm_up()

    # ---------- 会话激活 ----------
    def _warm_up(self):
        """模拟APP启动流程, 先拉配置再进首页, 激活服务端会话标记"""
        try:
            # 1. 先加载配置（APP启动时通常先读配置）
            self._load_parsers(False)
        except Exception:
            pass
        try:
            # 2. 再请求首页（带token，完成会话标记）
            self._fetch_api("/index_video", auth=True)
        except Exception:
            pass
        self._last_session_active = time.time()

    def _ensure_active(self):
        """关键操作前检查, 若会话太久未活跃则重新预热"""
        if time.time() - self._last_session_active > self._session_active_interval:
            self._warm_up()

    # ---------- 网络请求 ----------
    def _fetch_api(self, path, auth=False):
        headers = {
            "User-Agent": self.ua_dart,
            "X-Requested-With": self.app_id,
            "android_id": self.android_id,
            "loginuuid": self.login_uuid,
        }
        if auth:
            headers["token"] = self.token
        last = None
        now = time.time()

        good_ports = [p for p in self.api_ports if now - self._bad_ports.get(p, 0) > self._bad_port_ttl]
        if not good_ports:
            good_ports = self.api_ports

        for port in good_ports:
            try:
                elapsed = now - self._last_api_call
                if elapsed < self._api_min_interval:
                    time.sleep(self._api_min_interval - elapsed)

                # :20371 需要 /2233 前缀，其他端口不需要
                prefix = "/2233" if port == 20371 else ""
                url = f"http://{self.host}:{port}{prefix}{self.api_prefix}{path}"
                response = self._get(url, headers)
                self._last_api_call = time.time()

                if not response or not response.strip():
                    # 空响应大概率是限流, 短封禁5秒, 不直接拉黑30秒
                    self._bad_ports[port] = time.time() - self._bad_port_ttl + self._empty_port_ttl
                    continue

                # 成功则清除该端口负面记录
                self._bad_ports.pop(port, None)
                return response
            except Exception as error:
                last = error
                self._bad_ports[port] = time.time()
        raise last or RuntimeError("API 请求失败")

    def _get(self, url, headers=None, timeout=18):
        response = self.session.get(url, headers=headers or {}, timeout=timeout, verify=False)
        response.raise_for_status()
        return response.text

    @staticmethod
    def _aes_ecb_decrypt(value, key):
        raw = base64.b64decode(value.strip())
        return unpad(AES.new(key, AES.MODE_ECB).decrypt(raw), AES.block_size).decode("utf-8")

    def _decrypt_detail(self, value):
        if not value or not value.strip():
            raise ValueError("详情接口返回空数据(可能被限流)")
        chars = list(value.strip())
        if len(chars) < 10:
            raise ValueError(f"详情数据过短({len(chars)}字符),可能已被限流")
        password = [""] * 10
        for index in range(9, -1, -1):
            position = max(len(chars) - (3 * (1 << index) + 1), 0)
            password[index] = chars.pop(position)
        key = hashlib.sha256("".join(password).encode("utf-8")).hexdigest()[:16].encode("utf-8")
        return self._aes_ecb_decrypt("".join(chars), key)

    def _decrypt_player(self, html):
        shuffled = re.search(r"const\s+shuffledBase64\s*=\s*\'([^\']+)\'", html, re.S)
        restore = re.search(r"const\s+restoreKey\s*=\s*JSON\.parse\(\'([^\']+)\'\)", html, re.S)
        if not shuffled or not restore:
            raise ValueError("解析页参数缺失")
        source = shuffled.group(1)
        key = json.loads(restore.group(1))
        if len(source) != len(key):
            raise ValueError("解析页恢复表长度不匹配")
        result = [""] * len(source)
        for current, position in enumerate(key):
            result[position] = source[current]
        return json.loads(base64.b64decode("".join(result)).decode("utf-8"))

    # ---------- 配置加载(支持多路径) ----------
    def _load_parsers(self, force=False):
        if not force and self.config_expires_at > time.time() and self.active_jx_url and self.active_jqq_url:
            return
        headers = {
            "User-Agent": self.ua_dart,
            "Accept": "application/json; charset=utf-8",
            "Content-Type": "application/json; charset=utf-8",
            "token": self.token
        }
        last = None
        now = time.time()
        # 支持多种配置路径, 提高命中率
        config_paths = ["/dy.json", "/zy.json", "/xgx.json", "/bm/xgx.json", "/bm/zy.json"]
        # 过滤掉最近失败的配置端口(短封禁10秒, 避免卡死)
        good_config_ports = [p for p in self.config_ports if now - self._bad_ports.get(p, 0) > 10]
        if not good_config_ports:
            good_config_ports = self.config_ports
        for port in good_config_ports:
            for path in config_paths:
                try:
                    # 配置端口用短超时 5 秒, 避免单个端口卡死整个流程
                    encrypted = self._get(f"http://{self.host}:{port}{path}", headers, timeout=5)
                    root = json.loads(self._aes_ecb_decrypt(encrypted, self.config_key))
                    jx_url = ""
                    jx_m = ""
                    jqq_url = ""
                    for group in root.get("jxpath", []):
                        keyword = str(group.get("解析关键词", "")).lower()
                        for config in group.get("解析配置", []):
                            api = str(config.get("jxapi", ""))
                            if "/123pan/10086.php" in api:
                                parsed = urlsplit(api)
                                current_m = parse_qs(parsed.query).get("m", [""])[0]
                                if current_m:
                                    jx_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                                    jx_m = current_m
                            if keyword == "jqq" or ("/jx/api-appjx.php" in api and parse_qs(urlsplit(api).query).get("t", [""])[0] == "2233"):
                                jqq_url = api
                    if jx_url:
                        self.active_jx_url = jx_url
                        self.active_jx_m = jx_m
                    if jqq_url:
                        self.active_jqq_url = jqq_url
                    if self.active_jx_url or self.active_jqq_url:
                        self.config_expires_at = time.time() + 600
                        # 成功清除该端口负面记录
                        self._bad_ports.pop(port, None)
                        return
                except Exception as error:
                    last = error
                    # 配置端口失败短封禁 10 秒
                    self._bad_ports[port] = time.time()
        # 不抛异常: JQQ 有硬编码 fallback 端口兜底, 123Pan 解析也有固定端口
        # 静默失败, 避免详情/播放流程被拖死
        sys.stderr.write(f"[速搜] 配置加载失败(已兜底): {last}\n")

    @staticmethod
    def _video_list(items):
        result = []
        for item in items or []:
            result.append({
                "vod_id": str(item.get("vod_id", "")),
                "vod_name": item.get("vod_name", ""),
                "vod_pic": item.get("vod_pic", ""),
                "vod_remarks": item.get("vod_remarks", "")
            })
        return result

    @staticmethod
    def _values(values):
        return [{"n": name, "v": value} for name, value in values]

    def _filters(self, type_id):
        types = {
            "1": ["剧情", "喜剧", "动作", "爱情", "科幻", "动画", "悬疑", "惊悚", "恐怖", "犯罪", "冒险", "奇幻", "战争", "历史", "传记", "家庭"],
            "2": ["剧情", "喜剧", "动作", "爱情", "玄幻", "科幻", "悬疑", "惊悚", "恐怖", "犯罪", "传记", "历史", "战争"],
            "3": ["真人秀", "脱口秀", "音乐", "歌舞", "喜剧", "竞技", "旅游", "美食", "纪实"],
            "4": ["动画", "动作", "冒险", "奇幻", "科幻", "校园", "恋爱", "搞笑", "热血", "悬疑", "治愈"]
        }
        areas = ["大陆", "美国", "香港", "台湾", "日本", "韩国", "英国", "法国", "德国", "意大利", "西班牙", "印度", "泰国", "俄罗斯"]
        years = [str(year) for year in range(2026, 2009, -1)] + ["2000-2009", "1990-1999", "1980-1989"]
        return [
            {"key": "class", "name": "类型", "init": "", "value": self._values([("全部", "")] + [(item, item) for item in types.get(type_id, [])])},
            {"key": "area", "name": "地区", "init": "", "value": self._values([("全部", "")] + [(item, item) for item in areas])},
            {"key": "year", "name": "年份", "init": "", "value": self._values([("全部", "")] + [(item, item) for item in years])},
            {"key": "by", "name": "排序", "init": "time", "value": self._values([("最新", "time"), ("热度", "hits"), ("评分", "score")])}
        ]

    def homeContent(self, filter):
        classes = [
            {"type_id": "1", "type_name": "电影"},
            {"type_id": "2", "type_name": "电视剧"},
            {"type_id": "4", "type_name": "动漫"},
            {"type_id": "3", "type_name": "综艺"}
        ]
        result = {"class": classes, "list": []}
        result["filters"] = {item["type_id"]: self._filters(item["type_id"]) for item in classes} if filter else {}
        return result

    def homeVideoContent(self):
        try:
            root = json.loads(self._fetch_api("/index_video"))
            data = root.get("list") or root.get("data") or {}
            videos = []
            for category in data.get("categories", []):
                videos.extend(self._video_list(category.get("vlist", [])))
            return {"list": videos}
        except Exception:
            return {"list": []}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = int(pg)
            path = f"/video?tid={quote(str(tid), safe='')}&pg={page}"
            for key in ("class", "area", "year", "by"):
                value = str((extend or {}).get(key, ""))
                if value:
                    path += f"&{key}={quote(value, safe='')}"
            root = json.loads(self._fetch_api(path))
            return {
                "page": root.get("page", page),
                "pagecount": root.get("pagecount", page),
                "limit": root.get("limit", 24),
                "total": root.get("total", 0),
                "list": self._video_list(root.get("list", []))
            }
        except Exception:
            return {"page": int(pg), "pagecount": int(pg), "limit": 24, "total": 0, "list": []}

    @staticmethod
    def _mark_jqq_line(code, name, line):
        if str(code).lower() != "jqq" and "AI" not in str(name).upper():
            return line
        result = []
        for episode in str(line).split("#"):
            if "$" in episode:
                title, play_id = episode.split("$", 1)
                result.append(f"{title}$jqq@@{play_id}")
            else:
                result.append(f"jqq@@{episode}")
        return "#".join(result)

    def detailContent(self, ids):
        # 详情前保活
        self._ensure_active()
        try:
            vod_id = str(ids[0])
            path = f"/video_2345?id2345={quote(vod_id, safe='')}&username={quote(self.device_id, safe='')}"
            raw = self._fetch_api(path, auth=True)
            root = json.loads(self._decrypt_detail(raw))
            data = root.get("data") or {}
            if data.get("msg"):
                return {"list": [{"vod_id": vod_id, "vod_name": data.get("msg"), "vod_play_from": "", "vod_play_url": ""}]}
            play_from = []
            play_urls = []
            for player in data.get("vod_url_with_player", []):
                line = player.get("url", "")
                if not line:
                    continue
                code = player.get("code", "")
                name = player.get("name") or code or "速搜4K"
                play_from.append(name)
                play_urls.append(self._mark_jqq_line(code, name, line))
            if not play_urls and data.get("vod_play_url"):
                play_from.append("速搜4K")
                play_urls.append(data.get("vod_play_url"))
            vod = {key: data.get(key, "") for key in [
                "vod_id", "vod_name", "vod_pic", "vod_remarks", "vod_year", "vod_area",
                "vod_actor", "vod_director", "vod_class"
            ]}
            vod["vod_id"] = vod.get("vod_id") or vod_id
            vod["vod_content"] = data.get("vod_content") or data.get("vod_blurb") or ""
            vod["vod_play_from"] = "$$$".join(play_from)
            vod["vod_play_url"] = "$$$".join(play_urls)
            return {"list": [vod]}
        except Exception as error:
            sys.stderr.write(f"detailContent error: {error}\n")
            return {"list": []}

    def searchContent(self, key, quick, pg=1):
        try:
            page = int(pg)
            root = json.loads(self._fetch_api(f"/search?pg={page}&text={quote(str(key), safe='')}"))
            videos = self._video_list(root.get("list", []))
            return {"page": page, "pagecount": page + 1 if videos else page, "limit": 24, "total": len(videos), "list": videos}
        except Exception:
            return {"list": []}

    def _resolve_share(self, video_id):
        now = time.time()
        cached = self._share_cache.get(video_id)
        if cached and now - cached[0] < self._share_ttl:
            return cached[1]

        headers = {"User-Agent": self.ua_android, "X-Requested-With": self.app_id}
        candidates = []
        try:
            self._load_parsers(False)
            if self.active_jx_url and self.active_jx_m:
                candidates.append((self.active_jx_url, self.active_jx_m))
        except Exception:
            pass
        candidates.extend((f"http://{self.host}:{port}{self.jx_path}", self.jx_m) for port in self.jx_ports)
        last = None
        for base_url, current_m in candidates:
            try:
                url = f"{base_url}?t={quote(self.token, safe='')}&m={quote(current_m, safe='')}&url={quote(str(video_id), safe='')}"
                root = self._decrypt_player(self._get(url, headers))
                data = root.get("data") or {}
                if root.get("code") == 200 and data:
                    self._share_cache[video_id] = (now, data)
                    return data
                raise ValueError(root.get("msg") or "解析失败")
            except Exception as error:
                last = error
        raise last or RuntimeError("123Pan 解析失败")

    def _real_video(self, video_id):
        data = self._resolve_share(video_id)
        timestamp = int(time.time())
        auth_key = f"{timestamp}-{timestamp - 973591068}-{self.login_uuid}"
        params = (
            f"auth-key={quote(auth_key, safe='')}&limit=1&next=1&orderBy=share_id&orderDirection=desc&SharePwd="
            f"&ParentFileId={quote(str(data.get('wjjfxid', '')), safe='')}&shareKey={quote(str(data.get('wjfxurlid', '')), safe='')}"
            "&Page=1&event=homeListFile&operateType=4&OrderId=&superAdmin=null"
        )
        headers = {
            "User-Agent": self.ua_android,
            "platform": "android",
            "app-version": "72",
            "x-app-version": "2.4.10",
            "x-channel": "1002",
            "loginuuid": self.login_uuid,
            "devicename": "Android Device",
            "devicetype": "2510DRK44C",
            "osversion": "Android_16"
        }
        root = json.loads(self._get(f"{self.pan_api}?{params}", headers))
        info = (root.get("data") or {}).get("InfoList") or []
        if root.get("code") != 0 or not info:
            raise ValueError(root.get("message") or "123Pan 返回为空")
        direct = info[0].get("DownloadUrl", "").replace(self.download_host, self.vip_host)
        direct = re.sub(r"(?i)(filename=[^&]*?)\.(jpg|jpeg|png|webp)(?=&|$)", r"\1.mp4", direct)
        if "auto_redirect=" not in direct:
            direct += ("&" if "?" in direct else "?") + "auto_redirect=1"
        if "ndcp=" not in direct:
            direct += "&ndcp=1"
        return direct

    def _jqq_request_url(self, video_id):
        self._load_parsers(False)
        parts = str(video_id).split("&")
        url = self.active_jqq_url + quote(parts[0], safe="")
        for part in parts[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                url += f"&{quote(key, safe='')}={quote(value, safe='')}"
            else:
                url += f"&{quote(part, safe='')}"
        return url

    def _resolve_jqq(self, video_id):
        # 先尝试从配置获取的URL
        last = None
        for attempt in range(2):
            try:
                if attempt:
                    self.config_expires_at = 0
                    self._load_parsers(True)
                encrypted = self._get(self._jqq_request_url(video_id), {
                    "User-Agent": self.ua_android,
                    "Accept": "application/json, text/plain, */*"
                })
                root = json.loads(encrypted) if encrypted.lstrip().startswith("{") else json.loads(self._aes_ecb_decrypt(encrypted, self.jqq_key))
                if root.get("code") == 200 and root.get("url"):
                    return root["url"]
                raise ValueError(root.get("msg") or "AI 解析失败")
            except Exception as error:
                last = error
                self.active_jqq_url = ""

        # 配置失效时, 直接轮询抓包发现的硬编码备用端口
        for port in self.jqq_ports:
            try:
                url = f"http://{self.host}:{port}/jx/api-appjx.php?t=2233&url={quote(str(video_id), safe='')}"
                encrypted = self._get(url, {
                    "User-Agent": self.ua_android,
                    "Accept": "application/json, text/plain, */*"
                })
                root = json.loads(encrypted) if encrypted.lstrip().startswith("{") else json.loads(self._aes_ecb_decrypt(encrypted, self.jqq_key))
                if root.get("code") == 200 and root.get("url"):
                    # 成功后临时提升该端口优先级(可选)
                    return root["url"]
            except Exception as error:
                last = error

        raise last or RuntimeError("AI 解析失败")

    def playerContent(self, flag, id, vipFlags):
        # 播放前确保会话活跃(解决"要打开APP才能播")
        self._ensure_active()
        try:
            is_jqq = str(id).startswith("jqq@@") or "AI" in str(flag).upper() or str(flag).lower() == "jqq"
            play_id = str(id)[5:] if str(id).startswith("jqq@@") else str(id)

            try:
                url = self._resolve_jqq(play_id) if is_jqq else self._real_video(play_id)
                if url:
                    return {"parse": 0, "jx": 0, "url": url, "header": {"User-Agent": self.ua_android}}
                raise ValueError("主线路返回空 URL")
            except Exception as primary_error:
                sys.stderr.write(f"[速搜] 主线路失败: {primary_error}, 尝试 jqq fallback\n")

            try:
                url = self._resolve_jqq(play_id)
                if url:
                    sys.stderr.write(f"[速搜] jqq fallback 成功\n")
                    return {"parse": 0, "jx": 0, "url": url, "header": {"User-Agent": self.ua_android}}
            except Exception as fallback_error:
                sys.stderr.write(f"[速搜] jqq fallback 也失败: {fallback_error}\n")

            return {"parse": 0, "jx": 0, "url": ""}
        except Exception as error:
            sys.stderr.write(f"playerContent error: {error}\n")
            return {"parse": 0, "jx": 0, "url": ""}
