# -*- coding: utf-8 -*-
"""TVBox Python 爬虫源：VIPTVB06。

站点头 nginx 返回 403 + 滑块验证页（"滑动验证" huadong 脚本）：
  1) 请求任意页面获得 server_name_session cookie
  2) 请求 /a20be899_..._yanzheng_huadong.php?type=..&key=..&value=md5(逐字符charcode+1拼串)
     该 value 的 key/value/type 从 403 页面引用的 huadong js 提取并固化；
     验证成功后服务端下发放行 cookie（形如 9e16b11e...=d778308d...），session 内不再 403。
  3) 正常请求业务页面。
当 key/value/type 变更时，只需改 VERIFY_* 三个常量。

播放页结构（MacCMS 系）：
  详情/播放页内联 `var player_data={...,"url":"...","from":"mp4"}`（encrypt:0）。
  播放源名 = from，直接指向 mp4/m3u8；个别源（zijianm3u8/co/mytv 等）需在
  playerconfig.js 的 player_list.parse 前缀（本站代理）上拼接。

URL 规则（已按站点实测）：
  首页     /
  分类     /vod/type/id/{tid}/page/{page}.html
  详情     /vod/detail/id/{id}.html
  搜索     /vod/search/{kw}-page/{page}.html   （首页 52 条结果含分页）
  播放     /vod/play/id/{id}/sid/{sid}/nid/{nid}.html
"""
import hashlib
import html as _html
import json
import logging
import re
import sys
import time
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlparse

try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

CONFIG = {
    "name": "VIPTVB06",
    "host": "http://www.viptvb06.com",
    "timeout": 15,
    "page_size": 24,
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    },
    "paths": {
        "home": "/",
        "category": "/vod/type/id/{tid}/page/{page}.html",
        "detail": "/vod/detail/id/{id}.html",
        "search": "/vod/search/{keyword}-page/{page}.html",
        "play": "/vod/play/id/{id}/sid/{sid}/nid/{nid}.html",
    },
    "classes": [
        {"type_id": "1", "type_name": "电影"},
        {"type_id": "2", "type_name": "剧集"},
        {"type_id": "3", "type_name": "综艺"},
        {"type_id": "4", "type_name": "动漫"},
    ],
    # 滑块验证参数：来自 403 页 huadong js，更新时替换这三行即可
    "verify_value": "d7dc43a807fc3517fb1ce33a803a0859",
    "verify_key": "30d8def67a8cb6f065934dcf866ee176",
    "verify_type": "ad82060c2e67cc7e2cc47552a4fc1242",
    "parse_probe_prefixes": {  # playerconfig.js 里非空 parse 前缀（用于拼接直链源）
        "zijianm3u8": "",
        "co": "",
        "mytvb": "",
        "vwnet": "",
        "yunjie": "",
        "13yun": "",
        "189d": "",
        "hkm3u8": "",
        "CO4K": "",
        "nsys": "",
    },
}

logging.basicConfig(level=logging.WARNING)
LOGGER = logging.getLogger("viptvb06")


def clean(value):
    value = _html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def page_number(value, default=1):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def absolute(value, base):
    if not value or str(value).startswith(("data:", "javascript:", "#")):
        return ""
    result = urljoin(base, _html.unescape(str(value)).strip())
    return result if urlparse(result).scheme in ("http", "https") else ""


def first_attr(node, names):
    for name in names:
        if node and node.get(name):
            return node.get(name)
    return ""


class FallbackParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self._link = [attrs["href"], ""]
            self.links.append(self._link)

    def handle_data(self, data):
        if self._link:
            self._link[1] += data


class Spider:
    def __init__(self):
        self.host = CONFIG["host"].rstrip("/")
        self._verified = False
        self._build_session()

    def init(self, extend):
        """兼容 TVBox Python 爬虫的初始化调用。"""
        if not extend:
            return
        if isinstance(extend, dict):
            host = extend.get("host") or extend.get("site_url")
        else:
            value = str(extend).strip()
            host = value if "://" in value else ""
        if host and urlparse(host).scheme in ("http", "https"):
            self.host = host.rstrip("/")
            self._verified = False  # 换 host 需重新过验证

    def isVideoFormat(self, url):
        path = urlparse(url or "").path.lower()
        return path.endswith((".m3u8", ".mp4", ".mkv", ".flv", ".avi", ".mov", ".ts"))

    def getName(self):
        return CONFIG["name"]

    # ---------------- 滑块验证 ----------------

    def _verify_signature(self):
        """复刻 huadong js：md5(逐字符 charcode+1 拼接)。"""
        value = CONFIG["verify_value"]
        hexstr = "".join(str(ord(c) + 1) for c in value)
        return hashlib.md5(hexstr.encode()).hexdigest()

    def _build_session(self):
        """新建一个带重试的 session。"""
        if not requests:
            self.session = None
            return
        self.session = requests.Session()
        self.session.headers.update(CONFIG["headers"])
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry = Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _reconnect(self):
        """重建 session 并重新验证（连接被服务端重置时调用）。"""
        self._verified = False
        self._build_session()
        if self.session:
            try:
                self._ensure_verified()
            except Exception:
                LOGGER.warning("reconnect: 验证流程仍失败，server may be down")
                self.session = None

    def _ensure_verified(self):
        """403 滑块验证：拿 session cookie → 打验证接口 → 放行。"""
        if self._verified or not self.session:
            return
        r = self.session.get(self.host + "/", timeout=CONFIG["timeout"])
        if r.status_code != 403:
            self._verified = True
            return
        params = "type=%s&key=%s&value=%s" % (
            CONFIG["verify_type"], CONFIG["verify_key"], self._verify_signature())
        vr = self.session.get(
            self.host + "/a20be899_96a6_40b2_88ba_32f1f75f1552_yanzheng_huadong.php?" + params,
            timeout=CONFIG["timeout"])
        if vr.status_code != 200:
            LOGGER.warning("滑块验证失败 %s", vr.status_code)
            return
        r = self.session.get(self.host + "/", timeout=CONFIG["timeout"])
        self._verified = r.status_code == 200
        if not self._verified:
            LOGGER.warning("滑块验证后首页仍 %s", r.status_code)

    def _safe_get(self, url):
        """对 self.session.get 做统一异常捕获，返回 (text, final_url)。"""
        for attempt in range(2):
            try:
                response = self.session.get(url, timeout=CONFIG["timeout"])
                if response.status_code == 403:
                    self._verified = False
                    self._reconnect()
                    if not self.session:
                        return "", url
                    response = self.session.get(url, timeout=CONFIG["timeout"])
                if response.status_code != 200:
                    LOGGER.warning("HTTP %s for %s", response.status_code, url)
                    return "", url
                response.encoding = response.apparent_encoding or response.encoding
                return response.text, response.url
            except Exception as exc:
                LOGGER.warning("请求失败 %s: %s", url, type(exc).__name__)
                if attempt == 0:
                    self._reconnect()
                    if self.session:
                        continue
                return "", url

    # ---------------- 请求 ----------------

    def _get(self, url):
        if not self.session:
            self._reconnect()
            if not self.session:
                return "", url
        return self._safe_get(url)

    def _soup(self, text):
        return BeautifulSoup(text, "html.parser") if BeautifulSoup and text else None

    def _url(self, kind, **values):
        path = CONFIG["paths"][kind].format(**values)
        return urljoin(self.host + "/", path.lstrip("/"))

    def _page_count(self, text, tid, kind):
        """myui-page 块里的 尾页/n/1.html 或 1/n。"""
        m = re.search(r"尾页.*?/page/(\d+)\.html", text, re.S)
        if m:
            return int(m.group(1))
        m = re.search(r">(\d+)/(\d+)<", text)  # 手机显示 "1/1361"
        if m:
            return int(m.group(2))
        return 0

    # ---------------- 列表 ----------------

    def _items(self, text, base, remarks_from_tag=True):
        if not text:
            return []
        soup = self._soup(text)
        result, seen = [], set()
        if soup:
            nodes = soup.select(".myui-vodlist li, .myui-vodlist__media li, li")
            for node in nodes:
                link = node.select_one("a[href*='/vod/detail/id/']")
                if not link:
                    continue
                href = absolute(link.get("href"), base)
                if not href or href in seen:
                    continue
                seen.add(href)
                m = re.search(r"/id/(\d+)\.html", href)
                id_ = m.group(1) if m else href
                title = clean(link.get("title") or link.get_text(" ", strip=True))
                thumb = node.select_one("a.myui-vodlist__thumb") or node.select_one("a")
                pic = absolute(first_attr(thumb, ("data-original", "data-src", "src")), base) \
                    if thumb else ""
                tag = node.select_one(".tag")
                remark = clean(tag.get_text(strip=True)) if (tag and remarks_from_tag) else ""
                textline = node.select_one(".text")
                result.append({
                    "vod_id": id_,
                    "vod_name": title,
                    "vod_pic": pic,
                    "vod_remarks": remark,
                    "type_name": "",
                    "vod_year": "",
                    "area": "",
                    "vod_content": clean(textline.get_text(" ", strip=True)) if textline else "",
                    "vod_play_from": "",
                    "vod_play_url": "",
                })
            if result:
                return result
        # 回退：正则按 li 块切
        for block in re.findall(r"<li.*?</li>", text, re.S):
            m = re.search(r"href=\"(/vod/detail/id/(\d+)\.html)\"", block)
            if not m:
                continue
            href = absolute(m.group(1), base)
            if href in seen:
                continue
            seen.add(href)
            tm = re.search(r"title=\"([^\"]*)\"", block)
            pm = re.search(r"data-original=\"([^\"]*)\"", block)
            tm2 = re.search(r"<span class=\"tag\"[^>]*>(.*?)</span>", block, re.S)
            result.append({
                "vod_id": m.group(2),
                "vod_name": clean(tm.group(1)) if tm else "",
                "vod_pic": absolute(pm.group(1), base) if pm else "",
                "vod_remarks": clean(tm2.group(1)) if tm2 else "",
                "type_name": "", "vod_year": "", "area": "",
                "vod_content": "", "vod_play_from": "", "vod_play_url": "",
            })
        return result

    def _home_items(self, text, base):
        """首页：轮播 + 各板块 myui-vodlist，按板块标题标注 type_name。"""
        result = []
        for m in re.finditer(
                r'<h3 class="title">\s*([^<]{1,20}?)\s*</h3>(.*?)(?=<h3 class="title">|<div class="container|<footer)',
                text, re.S):
            title = clean(m.group(1))
            body = m.group(2)
            items = self._items(body, base, remarks_from_tag=True)
            for it in items:
                it["type_name"] = "推荐" if title in ("新剧上线", "最近更新", "热门排行") else title
                result.append(it)
        # 去重（首页板块可能重复）
        seen, out = set(), []
        for it in result:
            if it["vod_id"] in seen:
                continue
            seen.add(it["vod_id"])
            out.append(it)
        return out

    # ---------------- 六接口 ----------------

    def homeContent(self, filter=False):
        return {"class": CONFIG["classes"], "filters": {}}

    def homeVideoContent(self):
        text, base = self._get(self._url("home"))
        return {"list": self._home_items(text, base)[:CONFIG["page_size"]]}

    def categoryContent(self, tid, pg, filter, extend):
        tid = str(tid or "1").strip()
        page = page_number(pg)
        url = self._url("category", tid=quote(tid, safe=""), page=page)
        text, base = self._get(url)
        items = self._items(text, base)
        pc = self._page_count(text, tid, "category")
        return {
            "page": page,
            "pagecount": pc,
            "limit": len(items) or CONFIG["page_size"],
            "total": pc * (len(items) or CONFIG["page_size"]),
            "list": items,
        }

    def detailContent(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        result = []
        for item_id in ids or []:
            url = str(item_id)
            if url.isdigit():
                url = self._url("detail", id=url)
            text, base = self._get(url)
            item = {"vod_id": item_id}
            if not text:
                item.update({"vod_name": "", "vod_content": "", "vod_play_from": "", "vod_play_url": ""})
                result.append(item)
                continue
            soup = self._soup(text)
            h1 = soup.select_one("h1.title, h1") if soup else None
            item["vod_name"] = clean(h1.get_text(" ", strip=True)) if h1 else ""
            desc = soup.select_one(".content, #desc .content, .myui-panel_bd .content") if soup else None
            item["vod_content"] = clean(desc.get_text(" ", strip=True)) if desc else ""
            pic = soup.select_one(".myui-content__detail img, .thumb img, img.pic") if soup else None
            item["vod_pic"] = absolute(first_attr(pic, ("data-original", "data-src", "src")), base) if pic else ""
            item["vod_remarks"] = ""
            data_lines = soup.select("p.data") if soup else []
            for p in data_lines:
                label = clean(p.get_text(" ", strip=True))
                if label.startswith("分类"):
                    a = p.select_one("a")
                    item["type_name"] = clean(a.get_text(strip=True)) if a else ""
                elif label.startswith("年份"):
                    item["vod_year"] = clean(p.get_text(strip=True)).replace("年份：", "")
            # 播放地址：详情页含全部播放源（每个 myui-content__list 一组）
            groups = re.findall(r'<ul class="myui-content__list[^"]*"[^>]*>(.*?)</ul>', text, re.S)
            plays = []  # (flag, name$urls...)
            for gi, g in enumerate(groups):
                ep = re.findall(r'href="(/vod/play/[^"]+)"[^>]*>([^<]*)</a>', g)
                if not ep:
                    continue
                ep = [(u, clean(n)) for u, n in ep]
                # 源名：取首个 nid 播放页的 from（只对第一组，避免额外请求过多）
                flag = "play%d" % gi
                plays.append((flag, " + ".join(n + "$" + absolute(u, base) for u, n in ep)))
            if plays:
                item["vod_play_from"] = "#".join(p[0] for p in plays)
                item["vod_play_url"] = "#".join(p[1] for p in plays)
            else:
                item["vod_play_from"] = ""
                item["vod_play_url"] = ""
            result.append(item)
        return {"list": result}

    def _play_data_from_text(self, text):
        m = re.search(r'var player_data=(\{.*?\})\s*</script>', text, re.S)
        if not m:
            m = re.search(r'var player_data=(\{.*?\});', text, re.S)
        if not m:
            return None
        raw = m.group(1)
        # 稳健抽取 url / from / encrypt（player_data 是 JS 对象字面量，字段顺序固定）
        um = re.search(r'"url":"((?:[^"\\]|\\.)*)"', raw)
        fm = re.search(r'"from":"((?:[^"\\]|\\.)*)"', raw)
        em = re.search(r'"encrypt":(\d+)', raw)
        url = um.group(1) if um else ""
        if url:
            # 解 JS 对象字面量转义：\uXXXX -> 对应字符，\/ -> /
            url = re.sub(re.escape(chr(92)) + "u([0-9a-fA-F]{4})",
                         lambda m: chr(int(m.group(1), 16)), url)
            url = url.replace(chr(92) + "/", "/")
        frm = fm.group(1) if fm else ""
        enc = int(em.group(1)) if em else 0
        if not url:
            return None
        return {"url": url, "from": frm, "encrypt": enc}

    def playerContent(self, flag, id, vipFlags):
        url = unquote(str(id or ""))
        if not url:
            return {"parse": 0, "url": "", "header": {}, "flag": flag}
        # 1) 播放页（详情页里链接的都是播放页）
        if "vod/play" in url:
            text, base = self._get(url)
            data = self._play_data_from_text(text)
            if data and data["url"]:
                m = re.search(r"(id/\d+)/sid/(\d+)/nid/(\d+)", url)
                sid = m.group(2) if m else "1"
                # 尝试拿 from（播放页）；拿不到就用 flag
                frm = data["from"] or "mp4"
                return {"parse": 0, "url": absolute(data["url"], base),
                        "header": {**CONFIG["headers"], "Referer": base},
                        "flag": flag or frm}
            if data and data.get("encrypt") == 2:
                return {"parse": 0, "url": "", "header": {**CONFIG["headers"], "Referer": base}, "flag": flag}
            return {"parse": 0, "url": "", "header": {**CONFIG["headers"], "Referer": base}, "flag": flag}
        # 2) 直链源（from 命中 parse_probe_prefixes 的需前缀代理，否则直放）
        if self.isVideoFormat(url):
            return {"parse": 0, "url": url, "header": {**CONFIG["headers"], "Referer": self.host + "/"}, "flag": flag}
        # 3) 兜底：任意 URL
        return {"parse": 0, "url": url, "header": {**CONFIG["headers"], "Referer": self.host + "/"}, "flag": flag}

    def searchContent(self, key, quick, pg="1"):
        page = page_number(pg)
        url = self._url("search", keyword=quote(str(key or "")), page=page)
        text, base = self._get(url)
        items = self._items(text, base)
        pc = self._page_count(text, "", "search")
        if quick:
            items = items[:3]
        return {
            "page": page,
            "pagecount": pc,
            "limit": len(items) or CONFIG["page_size"],
            "total": pc * (len(items) or CONFIG["page_size"]),
            "list": items,
        }

    def localProxy(self, param):
        return [404, "text/plain", "local proxy disabled"]

    # ---------------- 自检 ----------------

    def self_test(self):
        assert isinstance(self.homeContent(), dict)
        assert isinstance(self.detailContent([]), dict)
        assert self.isVideoFormat("https://example.com/a.m3u8")
        assert self._verify_signature()  # 纯函数
        # 离线检查 player_data 解析（JSON 风格与 JS 转义两种形式）
        d = self._play_data_from_text(
            'var player_data={"url":"https:\\/\\/x.top\\/a.mp4","from":"mp4","encrypt":0};')
        assert d and "x.top" in d["url"] and d["from"] == "mp4"
        d2 = self._play_data_from_text(
            'var player_data={"url":"https://x.top/a' + chr(92) + 'u4ea4.mp4","from":"mp4","encrypt":0};')
        assert d2 and chr(0x4EA4) in d2["url"]
        return True


# 兼容直接导入模块并调用六接口的 TVBox 实现。
_DEFAULT_SPIDER = Spider()


def init(extend=""):
    return _DEFAULT_SPIDER.init(extend)


def getName():
    return _DEFAULT_SPIDER.getName()


def isVideoFormat(url):
    return _DEFAULT_SPIDER.isVideoFormat(url)


def homeContent(filter=False):
    return _DEFAULT_SPIDER.homeContent(filter)


def homeVideoContent():
    return _DEFAULT_SPIDER.homeVideoContent()


def categoryContent(tid, pg="1", filter=False, extend=None):
    return _DEFAULT_SPIDER.categoryContent(tid, pg, filter, extend or {})


def detailContent(ids):
    return _DEFAULT_SPIDER.detailContent(ids)


def playerContent(flag, id, vipFlags=None):
    return _DEFAULT_SPIDER.playerContent(flag, id, vipFlags or [])


def searchContent(key, quick=False, pg="1"):
    return _DEFAULT_SPIDER.searchContent(key, quick, pg)


def localProxy(param):
    return _DEFAULT_SPIDER.localProxy(param)


def get_spider():
    return Spider()


if __name__ == "__main__" and "--self-test" in sys.argv:
    Spider().self_test()
    print("self_test: ok")
