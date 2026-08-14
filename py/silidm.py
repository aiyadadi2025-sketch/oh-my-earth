import requests
from bs4 import BeautifulSoup
import re
import json

SITE_URL = "https://www.silidm.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": SITE_URL + "/",
}


def _get(url):
    return requests.get(SITE_URL + url, headers=HEADERS, timeout=10, verify=False).text


class py_spider:
    def init(self, extend=""):
        pass

    def homeContent(self, filter=False):
        html = _get("/")
        soup = BeautifulSoup(html, "html.parser")
        classes = []
        module_wrappers = soup.select(".module.module-wrapper")
        default_ids = ["dy", "juji", "dongman", "zongyi"]
        for idx, m in enumerate(module_wrappers):
            title_el = m.select_one(".module-title a span, .module-title span")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not title or title == "•":
                continue
            tid = ""
            type_links = m.select('a[href*="/type/"]')
            if type_links:
                m2 = re.search(r"/type/(\w+)\.html", type_links[0].get("href", ""))
                if m2:
                    tid = m2.group(1)
            if not tid and idx < len(default_ids):
                tid = default_ids[idx]
            classes.append({"type_name": title, "type_id": tid})
        if not classes:
            classes = [
                {"type_name": "电影", "type_id": "dy"},
                {"type_name": "电视剧", "type_id": "juji"},
                {"type_name": "动漫", "type_id": "dongman"},
                {"type_name": "综艺", "type_id": "zongyi"},
            ]
        return {"class": classes}

    def categoryContent(self, tid, pg, filter=False, extend={}):
        url = f"/type/{tid}.html"
        page = int(pg)
        if page > 1:
            url = f"/type/{tid}-{page}.html"
        html = _get(url)
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
            pic = ""
            if img:
                pic = img.get("data-src", "") or img.get("src", "")
            desc_el = item.select_one(".module-item-caption span")
            remark = desc_el.get_text(strip=True) if desc_el else ""
            list_data.append({
                "vod_id": vod_id,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": remark,
            })
        return {"list": list_data, "page": page, "pagecount": page + 1 if len(list_data) >= 15 else page, "limit": 15, "total": 999}

    def detailContent(self, ids):
        vod_id = ids[0]
        html = _get(f"/video/{vod_id}.html")
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
            if "演员" in label_text or "主演" in label_text:
                actors = names
            elif "导演" in label_text:
                directors = names

        sources = []
        episodes = {}
        source_tabs = soup.select(".play-source-tab")
        source_contents = soup.select(".play-source-content")
        for i, tab in enumerate(source_tabs):
            flag = tab.get_text(strip=True)
            if not flag or flag in ("下载",):
                continue
            links = []
            if i < len(source_contents):
                for a in source_contents[i].select("a"):
                    ep_title = a.get_text(strip=True)
                    ep_href = a.get("href", "")
                    if ep_href and ep_href != "javascript:void(0);":
                        links.append(f"{ep_title}${ep_href}")
            if links:
                sources.append(flag)
                episodes[flag] = "#".join(links)

        if not sources:
            sources.append("播放")
            eps = []
            for a in soup.select(".play-source-content a"):
                t = a.get_text(strip=True)
                h = a.get("href", "")
                if h and h != "javascript:void(0);":
                    eps.append(f"{t}${h}")
            if eps:
                episodes["播放"] = "#".join(eps)
            else:
                play_a = soup.select_one('a[href*="/play/"]')
                if play_a:
                    episodes["播放"] = f"播放${play_a.get('href', '')}"

        vod_play_from = "$".join(sources)
        vod_play_url = "|".join(f"{flag}${episodes.get(flag, '')}" for flag in sources)

        return {"list": [{"vod_id": vod_id, "vod_name": vod_name, "vod_pic": pic,
                          "vod_year": vod_year, "vod_area": vod_area, "vod_remarks": vod_class,
                          "vod_actor": ",".join(actors), "vod_director": ",".join(directors),
                          "vod_content": vod_content,
                          "vod_play_from": vod_play_from, "vod_play_url": vod_play_url}]}

    def playerContent(self, flag, id, vipFlags=""):
        if "$" in id:
            play_url = id.rsplit("$", 1)[-1]
        else:
            play_url = id
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

    def searchContent(self, key, quick="0", pg="1"):
        url = f"/search/{key}-------------.html"
        html = _get(url)
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".module-items .video-info, .video-info")
        list_data = []
        for item in items:
            a = item.select_one("h3 a[href*=\"/video/\"], a[href*=\"/video/\"]")
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
        return {"list": list_data, "page": pg, "pagecount": 2}
