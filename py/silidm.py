# coding=utf-8
import sys
import re
import json
import requests
from bs4 import BeautifulSoup
from base.spider import Spider

sys.path.append('..')

SITE_URL = "https://www.silidm.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": SITE_URL + "/",
}

cat_map = {"dy": "电影", "juji": "电视剧", "dongman": "动漫", "zongyi": "综艺"}


def _get(url):
    return requests.get(SITE_URL + url, headers=HEADERS, timeout=10, verify=False).text


class Spider(Spider):
    def getName(self):
        return "silidm"

    def init(self, extend=""):
        pass

    def isVideoFormat(self, url):
        return bool(re.search(r'\.m3u8|\.mp4|\.flv', url))

    def manualVideoCheck(self):
        return False

    def homeContent(self, filter):
        classes = []
        default_ids = ["dy", "juji", "dongman", "zongyi"]
        for idx, tid in enumerate(default_ids):
            classes.append({"type_id": tid, "type_name": cat_map[tid]})
        return {"class": classes}

    def homeVideoContent(self):
        html = _get("/")
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".module-items .module-item")
        list_data = []
        for item in items[:20]:
            a = item.select_one('a[href*="/video/"]')
            if not a:
                continue
            vid = a.get("href", "")
            m = re.search(r"/video/(\d+)", vid)
            vod_id = m.group(1) if m else ""
            title = a.get("title", "") or a.get_text(strip=True)
            if not title:
                continue
            img = item.select_one(".module-item-pic img")
            pic = img.get("data-src", "") or img.get("src", "") if img else ""
            list_data.append({
                "vod_id": vod_id,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": "",
            })
        return {"list": list_data}

    def categoryContent(self, cid, page, filter, ext):
        base_url = "/type/%s.html" % cid
        if ext and ext.startswith("/"):
            base_url = ext
        p = int(page)
        if p > 1:
            base_url = re.sub(r'-(\d+)\.html$', '-%d.html' % p, base_url)
            if '-.html' in base_url:
                base_url = base_url.replace('-.html', '-%d.html' % p)
        html = _get(base_url)
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".module-item")
        list_data = []
        for item in items:
            a = item.select_one('a[href*="/video/"]')
            if not a:
                continue
            vid = a.get("href", "")
            m = re.search(r"/video/(\d+)", vid)
            vod_id = m.group(1) if m else ""
            title = a.get("title", "") or a.get_text(strip=True)
            if not title:
                continue
            img = item.select_one(".module-item-pic img")
            pic = img.get("data-src", "") or img.get("src", "") if img else ""
            desc_el = item.select_one(".module-item-caption span")
            remark = desc_el.get_text(strip=True) if desc_el else ""
            list_data.append({
                "vod_id": vod_id,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": remark,
            })
        return {"list": list_data, "page": p, "pagecount": p + 1 if len(list_data) >= 15 else p, "limit": 15}

    def detailContent(self, did):
        vod_id = did[0]
        html = _get("/video/%s.html" % vod_id)
        soup = BeautifulSoup(html, "html.parser")

        title_el = soup.select_one(".page-title, h1")
        vod_name = title_el.get_text(strip=True) if title_el else ""

        pic = ""
        cover_img = soup.select_one(".module-item-pic img, .video-info-pic img")
        if cover_img:
            pic = cover_img.get("data-src", "") or cover_img.get("src", "")

        tags = soup.select(".video-info-aux a.tag-link")
        vod_year = ""
        vod_area = ""
        vod_class = ""
        for t in tags:
            txt = t.get_text(strip=True)
            href = t.get("href", "")
            if txt.isdigit() and "20" in txt:
                vod_year = txt
            elif "/type/" in href:
                vod_class = txt
            elif "/show/" in href and not vod_area:
                vod_area = txt

        desc_el = soup.select_one(".video-info-content")
        vod_content = desc_el.get_text(strip=True) if desc_el else ""

        actors = []
        directors = []
        info_items = soup.select(".video-info-items")
        for item in info_items:
            label = item.select_one(".video-info-itemtitle")
            if not label:
                continue
            label_text = label.get_text(strip=True)
            val = item.select_one(".video-info-item")
            links = val.select("a") if val else []
            names = [a.get_text(strip=True) for a in links]
            if "actor" in label_text.lower() or "star" in label_text.lower():
                actors = names
            elif "director" in label_text.lower():
                directors = names

        sources = []
        episodes = {}
        source_tabs = soup.select(".play-source-tab")
        source_contents = soup.select(".play-source-content")
        for i, tab in enumerate(source_tabs):
            flag = tab.get_text(strip=True)
            if not flag or flag == "Download":
                continue
            links = []
            if i < len(source_contents):
                for a in source_contents[i].select("a"):
                    ep_title = a.get_text(strip=True)
                    ep_href = a.get("href", "")
                    if ep_href and ep_href != "javascript:void(0);":
                        links.append("%s$%s" % (ep_title, ep_href))
            if links:
                sources.append(flag)
                episodes[flag] = "#".join(links)

        if not sources:
            sources.append("Play")
            eps = []
            for a in soup.select(".play-source-content a"):
                t = a.get_text(strip=True)
                h = a.get("href", "")
                if h and h != "javascript:void(0);":
                    eps.append("%s$%s" % (t, h))
            if eps:
                episodes["Play"] = "#".join(eps)
            else:
                play_a = soup.select_one('a[href*="/play/"]')
                if play_a:
                    episodes["Play"] = "Play$%s" % play_a.get('href', '')

        vod_play_from = "$".join(sources)
        vod_play_url = "|".join("%s$%s" % (flag, episodes.get(flag, '')) for flag in sources)

        return {"list": [{"vod_id": vod_id, "vod_name": vod_name, "vod_pic": pic,
                          "vod_year": vod_year, "vod_area": vod_area, "vod_remarks": vod_class,
                          "vod_actor": ",".join(actors), "vod_director": ",".join(directors),
                          "vod_content": vod_content,
                          "vod_play_from": vod_play_from, "vod_play_url": vod_play_url}]}

    def playerContent(self, flag, pid, vipFlags):
        if "$" in pid:
            play_url = pid.rsplit("$", 1)[-1]
        else:
            play_url = pid
        html = _get(play_url if play_url.startswith("/") else "/" + play_url.lstrip("/"))

        m3u8 = ""
        m = re.search(r'player_aaaa\s*=\s*(\{.+?\})', html)
        if m:
            try:
                raw = m.group(1).replace("\\/", "/")
                data = json.loads(raw)
                m3u8 = data.get("url", "")
            except Exception:
                pass

        if not m3u8:
            m2 = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
            if m2:
                m3u8 = m2.group(1)

        header = json.dumps({"Referer": SITE_URL + "/", "User-Agent": UA}, ensure_ascii=False)
        return {"parse": 0, "url": m3u8, "header": header}

    def searchContent(self, key, quick="", page='1'):
        url = "/search/%s-------------.html" % key
        html = _get(url)
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".module-items .video-info, .video-info")
        list_data = []
        for item in items:
            a = item.select_one('h3 a[href*="/video/"], a[href*="/video/"]')
            if not a:
                continue
            vid = a.get("href", "")
            m = re.search(r"/video/(\d+)", vid)
            vod_id = m.group(1) if m else ""
            title = a.get("title", "") or a.get_text(strip=True)
            if not title:
                continue
            img = item.select_one("img")
            pic = (img.get("data-src", "") or img.get("src", "")) if img else ""
            remark_el = item.select_one(".video-info-header span, .module-item-text")
            remark = remark_el.get_text(strip=True) if remark_el else ""
            list_data.append({
                "vod_id": vod_id,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": remark,
            })
        return {"list": list_data, "page": page, "pagecount": 2}

    def localProxy(self, params):
        return [200, "application/octet-stream", "", None, None]

    def destroy(self):
        pass
