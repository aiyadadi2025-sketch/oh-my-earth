# -*- coding: utf-8 -*-
"""TVBox Python 爬虫源：VIPTVB06。"""
import html
import logging
import re
import sys
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
    "timeout": 12,
    "page_size": 24,
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
    },
    "paths": {
        "home": "/",
        "category": "/vodshow/{tid}--------{page}---.html",
        "detail": "/voddetail/{id}.html",
        "search": "/vodsearch/{keyword}----------{page}---.html",
    },
    "classes": [
        {"type_id": "1", "type_name": "电影"},
        {"type_id": "2", "type_name": "电视剧"},
        {"type_id": "3", "type_name": "综艺"},
        {"type_id": "4", "type_name": "动漫"},
    ],
}

logging.basicConfig(level=logging.WARNING)
LOGGER = logging.getLogger("viptvb06")


def clean(value):
    value = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def page_number(value, default=1):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def absolute(value, base):
    if not value or str(value).startswith(("data:", "javascript:", "#")):
        return ""
    result = urljoin(base, html.unescape(str(value)).strip())
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
        self.session = requests.Session() if requests else None
        if self.session:
            self.session.headers.update(CONFIG["headers"])

    def getName(self):
        return CONFIG["name"]

    def isVideoFormat(self, url):
        path = urlparse(url or "").path.lower()
        return path.endswith((".m3u8", ".mp4", ".mkv", ".flv", ".avi", ".mov", ".ts"))

    def _get(self, url):
        if not self.session:
            return "", url
        try:
            response = self.session.get(url, timeout=CONFIG["timeout"])
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            return response.text, response.url
        except requests.RequestException as exc:
            LOGGER.warning("请求失败 %s: %s", url, type(exc).__name__)
            return "", url

    def _soup(self, text):
        return BeautifulSoup(text, "html.parser") if BeautifulSoup and text else None

    def _url(self, kind, **values):
        path = CONFIG["paths"][kind].format(**values)
        return urljoin(self.host + "/", path.lstrip("/"))

    def _items(self, text, base):
        soup = self._soup(text)
        if not soup:
            parser = FallbackParser()
            parser.feed(text)
            return [{"vod_id": absolute(href, base), "vod_name": clean(title)} for href, title in parser.links if "voddetail" in href]
        selectors = [".module-item", ".module-card-item", ".myui-vodlist li", ".stui-vodlist li", ".vodlist li", ".video-item", "li"]
        nodes = []
        for selector in selectors:
            nodes = soup.select(selector)
            if nodes:
                break
        result, seen = [], set()
        for node in nodes:
            link = node.select_one("a[href*='voddetail'], a[href*='vodplay']")
            if not link:
                continue
            href = absolute(link.get("href"), base)
            if not href or href in seen:
                continue
            seen.add(href)
            title_node = node.select_one(".module-item-title, .module-card-item-title, .title, .name, h3, h4")
            title = clean(title_node.get_text(" ", strip=True) if title_node else link.get_text(" ", strip=True))
            image = node.select_one("img[data-src], img[data-original], img[src]")
            pic = absolute(first_attr(image, ("data-src", "data-original", "src")), base)
            result.append({"vod_id": href, "vod_name": title, "vod_pic": pic, "vod_remarks": ""})
        return result

    def homeContent(self, filter=False):
        return {"class": CONFIG["classes"], "filters": {}}

    def homeVideoContent(self):
        text, base = self._get(self._url("home"))
        return {"list": self._items(text, base)[:CONFIG["page_size"]]}

    def categoryContent(self, tid, pg, filter, extend):
        page = page_number(pg)
        text, base = self._get(self._url("category", tid=quote(str(tid), safe=""), page=page))
        return {"page": page, "pagecount": page, "limit": CONFIG["page_size"], "total": 0, "list": self._items(text, base)}

    def detailContent(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        result = []
        for item_id in ids or []:
            url = item_id if urlparse(str(item_id)).scheme in ("http", "https") else self._url("detail", id=quote(str(item_id), safe=""))
            text, base = self._get(url)
            soup = self._soup(text)
            title = clean(soup.select_one("h1, .page-title").get_text(" ", strip=True) if soup and soup.select_one("h1, .page-title") else "")
            content = clean(soup.select_one(".module-info-introduction, .vod-content, .content, .desc").get_text(" ", strip=True) if soup and soup.select_one(".module-info-introduction, .vod-content, .content, .desc") else "")
            plays = []
            if soup:
                for link in soup.select("a[href*='vodplay'], .playlist a, .play-list a"):
                    href = absolute(link.get("href"), base)
                    if href and all(href != old for _, old in plays):
                        plays.append((clean(link.get_text(" ", strip=True)) or "播放", href))
            result.append({"vod_id": item_id, "vod_name": title, "vod_content": content, "vod_play_from": "本站", "vod_play_url": "#".join(n + "$" + u for n, u in plays)})
        return {"list": result}

    def playerContent(self, flag, id, vipFlags):
        url = unquote(str(id or ""))
        text, base = self._get(url) if not self.isVideoFormat(url) else ("", url)
        candidates = re.findall(r"https?://[^\"'<>\s\\]+(?:m3u8|mp4)(?:\?[^\"'<>\s\\]*)?", text, re.I)
        play_url = next((absolute(x.replace("\\/", "/"), base) for x in candidates), url if urlparse(url).scheme in ("http", "https") else "")
        return {"parse": 0, "url": play_url, "header": {**CONFIG["headers"], "Referer": base}, "flag": flag}

    def searchContent(self, key, quick, pg="1"):
        page = page_number(pg)
        text, base = self._get(self._url("search", keyword=quote(str(key), safe=""), page=page))
        return {"page": page, "pagecount": page, "limit": CONFIG["page_size"], "total": 0, "list": self._items(text, base)}

    def localProxy(self, param):
        return [404, "text/plain", "local proxy disabled"]

    def self_test(self):
        assert isinstance(self.homeContent(), dict)
        assert isinstance(self.detailContent([]), dict)
        assert self.isVideoFormat("https://example.com/a.m3u8")
        return True


def get_spider():
    return Spider()


if __name__ == "__main__" and "--self-test" in sys.argv:
    Spider().self_test()
    print("self_test: ok")
