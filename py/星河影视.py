# -*- coding: utf-8 -*-
# 星河影视 xingheys.xyz (MacCMS V10)
# 依赖: requests, beautifulsoup4
import re
import time
import random
import json
import logging
from urllib.parse import quote

try:
    from base.spider import Spider
except ImportError:
    Spider = object

logger = logging.getLogger("xinghe")

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

# 不可达的图片 CDN 域名 → 可用的替代域名（路径完全一致）
IMG_HOST_FIX = {
    "img.feifeiimg.vip": "fffgood.com",
    "www.feifeiimg.vip": "fffgood.com",
    "feifeiimg.vip": "fffgood.com",
}


class Spider(Spider if Spider is not object else object):
    def __init__(self):
        self.siteUrl = "https://xingheys.xyz"
        self.host = self.siteUrl.rstrip("/")
        self.session = __import__("requests").Session()
        self.session.headers.update({
            "User-Agent": random.choice(UA_POOL),
            "Referer": self.host + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        self.delay = (0.3, 0.8)

    # ---------- 基础工具 ----------
    def _ua(self):
        return random.choice(UA_POOL)

    def _fix_img(self, u):
        """修复图片地址：替换不可达的图片CDN域名"""
        if not u:
            return ""
        for bad, good in IMG_HOST_FIX.items():
            if bad in u:
                u = u.replace(bad, good)
        return u

    def _get(self, url, ref=None, timeout=15):
        try:
            time.sleep(random.uniform(*self.delay))
            headers = {
                "User-Agent": self._ua(),
                "Referer": ref or self.host + "/",
            }
            r = self.session.get(url, headers=headers, timeout=timeout, verify=False)
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() in ("iso-8859-1",):
                r.encoding = r.apparent_encoding or "utf-8"
            return r.text or ""
        except Exception as e:
            logger.warning("GET %s failed: %s", url, e)
            return ""

    @staticmethod
    def _abs(base, u):
        if not u:
            return ""
        if u.startswith("http"):
            return u
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("/"):
            return base.rstrip("/") + u
        return base.rstrip("/") + "/" + u

    def _soup(self, html):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html or "", "html.parser")

    def _parse_card(self, card):
        """从 video-card 元素提取影片信息"""
        a_tag = card.find("a", href=True)
        if not a_tag:
            return None
        link = a_tag["href"]
        title_tag = card.find("h3")
        title = title_tag.get_text(strip=True) if title_tag else ""
        img_tag = card.find("img")
        img = ""
        if img_tag:
            img = img_tag.get("src") or img_tag.get("data-original") or img_tag.get("data-src") or ""
        remarks_tag = card.find("span", class_="remarks")
        remarks = remarks_tag.get_text(strip=True) if remarks_tag else ""
        score_tag = card.find("span", class_="score")
        score = score_tag.get_text(strip=True) if score_tag else ""
        return {
            "vod_id": self._abs(self.host, link),
            "vod_name": title,
            "vod_pic": self._fix_img(self._abs(self.host, img)),
            "vod_remarks": remarks or score,
        }

    # ---------- 六接口 ----------
    def init(self, extend=""):
        pass

    def getName(self):
        return "星河影视"

    def homeContent(self, filter=False):
        result = {"class": [], "list": [], "filters": {}}
        # 只返回一级分类（客户端会将其作为顶部标签栏）
        primary = {
            "1": "电影",
            "2": "电视剧",
            "3": "综艺",
            "4": "动漫",
        }
        for tid, name in primary.items():
            result["class"].append({
                "type_name": name,
                "type_id": tid,
            })

        # filters：每个一级分类下挂对应的二级分类筛选条件
        # MacCMS show 页面直接按分类ID查询，所以用 key="id" 传递二级分类ID
        result["filters"] = {
            "1": [
                {
                    "key": "id",
                    "name": "类型",
                    "value": [
                        {"n": "全部", "v": "1"},
                        {"n": "动作片", "v": "6"},
                        {"n": "喜剧片", "v": "7"},
                        {"n": "爱情片", "v": "8"},
                        {"n": "科幻片", "v": "9"},
                        {"n": "恐怖片", "v": "10"},
                        {"n": "剧情片", "v": "11"},
                        {"n": "战争片", "v": "12"},
                        {"n": "伦理片", "v": "20"},
                    ]
                }
            ],
            "2": [
                {
                    "key": "id",
                    "name": "类型",
                    "value": [
                        {"n": "全部", "v": "2"},
                        {"n": "国产剧", "v": "13"},
                        {"n": "港台剧", "v": "14"},
                        {"n": "日本剧", "v": "15"},
                        {"n": "韩国剧", "v": "21"},
                        {"n": "海外剧", "v": "22"},
                        {"n": "泰国剧", "v": "23"},
                        {"n": "短剧", "v": "24"},
                        {"n": "欧美剧", "v": "25"},
                    ]
                }
            ],
            "3": [
                {
                    "key": "id",
                    "name": "类型",
                    "value": [
                        {"n": "全部", "v": "3"},
                        {"n": "大陆综艺", "v": "26"},
                        {"n": "港台综艺", "v": "27"},
                        {"n": "日韩综艺", "v": "28"},
                        {"n": "欧美综艺", "v": "29"},
                    ]
                }
            ],
            "4": [
                {
                    "key": "id",
                    "name": "类型",
                    "value": [
                        {"n": "全部", "v": "4"},
                        {"n": "国产动漫", "v": "30"},
                        {"n": "日韩动漫", "v": "31"},
                        {"n": "欧美动漫", "v": "32"},
                        {"n": "港台动漫", "v": "33"},
                        {"n": "海外动漫", "v": "34"},
                    ]
                }
            ],
        }

        # 首页推荐
        html = self._get(self.host + "/")
        if not html:
            return result
        soup = self._soup(html)
        for card in soup.select("article.video-card"):
            item = self._parse_card(card)
            if item:
                result["list"].append(item)
        return result

    def categoryContent(self, tid, pg, filter=False, extend=None):
        pg = int(pg or 1)
        result = {"list": [], "page": pg, "pagecount": 9999, "limit": 90, "total": 999999}
        try:
            # 优先用 filter 里选的二级分类 id，否则用 tid（一级分类）
            cid = (extend or {}).get("id") or tid
            if pg > 1:
                url = f"{self.host}/index.php/vod/show/id/{cid}/page/{pg}.html"
            else:
                url = f"{self.host}/index.php/vod/show/id/{cid}.html"
            html = self._get(url)
            if not html:
                return result
            soup = self._soup(html)
            for card in soup.select("article.video-card"):
                item = self._parse_card(card)
                if item:
                    result["list"].append(item)
            # 解析总页数
            last_link = soup.select_one(".pagination a.last, .pagination a.last-child")
            if last_link and last_link.get("href"):
                m = re.search(r'/page/(\d+)\.html', last_link["href"])
                if m:
                    result["pagecount"] = int(m.group(1))
            # 总数
            total_el = soup.select_one(".page-count, .total-info")
            if total_el:
                m = re.search(r'(\d+)', total_el.get_text())
                if m:
                    result["total"] = int(m.group(1))
        except Exception as e:
            logger.error("categoryContent err: %s", e)
        return result

    def detailContent(self, ids):
        result = {"list": []}
        try:
            url = ids[0] if isinstance(ids, (list, tuple)) else ids
            html = self._get(url)
            if not html:
                return result
            soup = self._soup(html)

            title = soup.find("h1") or soup.find("h2") or soup.title
            title = title.get_text(strip=True) if title else url.split("/")[-1]

            pic = ""
            cover = soup.select_one(".detail-cover img, .detail-pic img, .vod-detail-pic img, .pic img")
            if cover:
                pic = cover.get("src") or cover.get("data-original") or ""

            # 元信息
            def _meta(sel):
                el = soup.select_one(sel)
                return el.get_text(strip=True) if el else ""

            director = _meta(".director")
            actor = _meta(".actor")
            area = _meta(".area")
            year = _meta(".year")
            type_name = _meta(".class")
            content_el = soup.select_one(".content-text, .desc, .vod_content")
            content = content_el.get_text(" ", strip=True)[:500] if content_el else ""

            # 播放列表（支持多个播放源）
            play_from = []
            play_url = []
            groups = soup.select(".play-list-group")
            if not groups:
                groups = soup.select(".play-list-section > div")
            for grp in groups:
                title_el = grp.select_one(".play-list-title")
                src_name = title_el.get_text(strip=True) if title_el else "星河"
                eps = []
                for a in grp.select(".play-list a"):
                    ep_url = a.get("href")
                    ep_name = a.get_text(strip=True)
                    if ep_url and ep_name:
                        eps.append(f"{ep_name}${self._abs(self.host, ep_url)}")
                if eps:
                    play_from.append(src_name)
                    play_url.append("#".join(eps))
            if not play_url:
                # fallback: m3u8 直接提取
                text = html.replace("\\/", "/")
                m3u8_links = re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', text)
                for i, m in enumerate(set(m3u8_links)):
                    play_url.append(f"第{i+1}集${m}")
                if play_url:
                    play_from.append("星河")

            pf = "$$$".join(play_from) if play_from else "星河"
            pu = "$$$".join(play_url) if play_url else "正片$"

            result["list"].append({
                "vod_id": url,
                "vod_name": title,
                "vod_pic": self._fix_img(self._abs(self.host, pic)),
                "type_name": type_name,
                "vod_year": year,
                "vod_area": area,
                "vod_actor": actor,
                "vod_director": director,
                "vod_content": content,
                "vod_play_from": pf,
                "vod_play_url": pu,
            })
        except Exception as e:
            logger.error("detailContent err: %s", e)
        return result

    def playerContent(self, flag, id, vipFlags=""):
        try:
            # 直链 m3u8/mp4 直接返回
            if id.startswith("http") and (".m3u8" in id or ".mp4" in id):
                return {"parse": 0, "url": id.replace("\\/", "/"),
                        "header": {"Referer": self.host + "/", "User-Agent": self._ua()}}
            html = self._get(id)
            if not html:
                return {"parse": 0, "url": "", "header": {}}

            # 优先从 player_aaaa JSON 配置提取播放地址
            player_match = re.search(r'player_[a-z]+\s*=\s*(\{.*?\})\s*</script', html, re.S)
            if player_match:
                try:
                    raw = player_match.group(1).replace("\\/", "/")
                    cfg = json.loads(raw)
                    play_url = (cfg.get("url") or "").strip()
                    if play_url and (".m3u8" in play_url or ".mp4" in play_url):
                        return {"parse": 0, "url": play_url,
                                "header": {"Referer": id, "User-Agent": self._ua()}}
                except Exception as e:
                    logger.debug("player_aaaa parse err: %s", e)

            # 回退：正则匹配 m3u8/mp4（先还原转义斜杠）
            text = html.replace("\\/", "/")
            m3u8_list = re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', text)
            if m3u8_list:
                return {"parse": 0, "url": m3u8_list[0],
                        "header": {"Referer": id, "User-Agent": self._ua()}}
            mp4_list = re.findall(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', text)
            if mp4_list:
                return {"parse": 0, "url": mp4_list[0],
                        "header": {"Referer": id, "User-Agent": self._ua()}}
            # initPlayer
            init_match = re.search(r'initPlayer\(\s*[^,]*,\s*[\'"]([^\'"]+)[\'"]', text)
            if init_match:
                return {"parse": 0, "url": self._abs(self.host, init_match.group(1)),
                        "header": {"Referer": id, "User-Agent": self._ua()}}
            return {"parse": 1, "url": id, "header": {"Referer": self.host + "/"}}
        except Exception as e:
            logger.error("playerContent err: %s", e)
            return {"parse": 0, "url": "", "header": {}}

    def searchContent(self, key, quick="1", pg="1"):
        result = {"list": []}
        try:
            url = f"{self.host}/index.php/vod/search.html?wd={quote(key)}"
            if int(pg or 1) > 1:
                url += f"&page={pg}"
            html = self._get(url)
            if not html:
                return result
            soup = self._soup(html)
            for card in soup.select("article.video-card"):
                item = self._parse_card(card)
                if item:
                    result["list"].append(item)
        except Exception as e:
            logger.error("searchContent err: %s", e)
        return result

    def localProxy(self, param):
        try:
            if isinstance(param, str) and param.startswith("http"):
                txt = self._get(param)
                return [200, "application/vnd.apple.mpegurl", txt.encode("utf-8")]
            return [200, "text/plain", b""]
        except Exception as e:
            logger.error("localProxy err: %s", e)
            return [500, "text/plain", b""]

    def isVideoFormat(self, url):
        return any(x in (url or "") for x in [".m3u8", ".mp4", ".ts"])

    def manualVideoCheck(self):
        pass

    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass
