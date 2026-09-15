# -*- coding: utf-8 -*-
# OK影视专用爬虫插件 | 奇优影院(qiyou05.com)
import sys
import re
import json
import requests
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
requests.packages.urllib3.disable_warnings()

from base.spider import Spider

class Spider(Spider):
    def getName(self):
        return "奇优影院"

    def init(self, extend=""):
        super().init(extend)
        self.site_url = "http://www.qiyou05.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36",
            "Referer": self.site_url,
            "Accept-Language": "zh-CN,zh;q=0.9"
        }
        self.sess = requests.Session()
        self.sess.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])))
        self.sess.mount("http://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])))
        self.page_size = 24
        self.total = 9999

    def fetch(self, url, timeout=10, method="GET", data=None):
        try:
            if method.upper() == "POST":
                res = self.sess.post(url, headers=self.headers, data=data, timeout=timeout, verify=False)
            else:
                res = self.sess.get(url, headers=self.headers, timeout=timeout, verify=False)
            res.encoding = "utf-8"
            return res
        except Exception as e:
            return None

    def homeContent(self, filter):
        cate_list = [
            {"type_name": "电影", "type_id": "1"},
            {"type_name": "电视剧", "type_id": "2"},
            {"type_name": "动漫", "type_id": "3"},
            {"type_name": "综艺", "type_id": "4"}
        ]
        return {"class": cate_list}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        if pg == 1:
            list_url = f"{self.site_url}/list/{tid}.html"
        else:
            list_url = f"{self.site_url}/list/{tid}_{pg}.html"

        res = self.fetch(list_url)
        video_list = []
        if res and res.ok:
            html = res.text
            for match in re.finditer(r'<li class="col-md-\d+ col-sm-4 col-xs-3">(.*?)</li>', html, re.S):
                item_html = match.group(1)
                vod_id = re.search(r'href="(/view/\d+\.html)"', item_html)
                vod_name = re.search(r'title="([^"]+)"', item_html)
                vod_pic = re.search(r'data-original="([^"]+)"', item_html)
                vod_remarks = re.search(r'<span class="pic-text text-right">([^<]*)</span>', item_html)

                if vod_id and vod_name and vod_pic:
                    pic_url = vod_pic.group(1)
                    if pic_url.startswith("//"):
                        pic_url = "https:" + pic_url
                    elif not pic_url.startswith(("http://", "https://")):
                        pic_url = self.site_url + pic_url

                    video_list.append({
                        "vod_id": self.site_url + vod_id.group(1),
                        "vod_name": vod_name.group(1).strip(),
                        "vod_pic": pic_url,
                        "vod_remarks": vod_remarks.group(1).strip() if vod_remarks else "",
                        "style": {"type": "rect", "ratio": 0.75}
                    })

        pagecount = pg + 1 if len(video_list) >= self.page_size else pg
        return {
            "list": video_list,
            "page": pg,
            "pagecount": pagecount,
            "limit": self.page_size,
            "total": self.total
        }

    def detailContent(self, ids):
        vod_id = ids[0] if ids else ""
        if not vod_id:
            return {"list": [{"vod_name": "视频ID为空"}]}

        detail_url = vod_id
        res = self.fetch(detail_url)
        if not res or not res.ok:
            return {"list": [{"vod_id": vod_id, "vod_name": "视频详情解析失败"}]}

        html = res.text

        title_match = re.search(r'<h1 class="line1">([^<]+)</h1>', html)
        vod_name = title_match.group(1).strip() if title_match else "未知名称"

        pic_match = re.search(r'<img class="lazyload" data-original="([^"]+)"', html)
        vod_pic = pic_match.group(1) if pic_match else ""
        if vod_pic.startswith("//"):
            vod_pic = "https:" + vod_pic

        remarks_match = re.search(r'<span class="pic-text text-right">([^<]*)</span>', html)
        vod_remarks = remarks_match.group(1).strip() if remarks_match else ""

        type_match = re.search(r'<a href="/list/\d+\.html">([^<]+)</a>', html)
        type_name = type_match.group(1).strip() if type_match else ""

        desc_match = re.search(r'<p class="desc[^"]*"><span class="left text-muted">简介：</span>(.*?)<a href="#desc">', html, re.S)
        if desc_match:
            vod_content = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip()
        else:
            desc_match2 = re.search(r'<div class="col-pd">欢迎在线观看.*?讲述了(.*?)</div>', html, re.S)
            vod_content = re.sub(r'<[^>]+>', '', desc_match2.group(1)).strip() if desc_match2 else ""

        play_from_list = []
        play_url_list = []
        seen_episode_sets = []

        source_matches = re.findall(r'<a data-toggle="tab" href="#(down\d+)">([^<]+)</a>', html)
        for source_id, source_name in source_matches:
            # 精确提取当前源对应的 <ul> 内容，避免跨源匹配
            source_pattern = r'id="' + source_id + r'"[^>]*>.*?<ul class="stui-content__playlist clearfix">(.*?)</ul>'
            source_html_match = re.search(source_pattern, html, re.S)
            if source_html_match:
                source_html = source_html_match.group(1)
                episode_matches = re.findall(r'<a href="(/play/[^"]+)" title="([^"]+)">', source_html)
                episodes = []
                ep_names = []
                for ep_url, ep_name in episode_matches:
                    ep_full_url = self.site_url + ep_url if ep_url.startswith("/") else ep_url
                    episodes.append(f"{ep_name.strip()}${ep_full_url}")
                    ep_names.append(ep_name.strip())

                if episodes:
                    ep_set = tuple(ep_names)
                    if ep_set not in seen_episode_sets:
                        seen_episode_sets.append(ep_set)
                        play_from_list.append(source_name.strip())
                        play_url_list.append("#".join(episodes))

        if not play_from_list:
            episode_matches = re.findall(r'<a href="(/play/[^"]+)" title="([^"]+)">', html)
            episodes = []
            ep_names = []
            for ep_url, ep_name in episode_matches:
                ep_full_url = self.site_url + ep_url if ep_url.startswith("/") else ep_url
                episodes.append(f"{ep_name.strip()}${ep_full_url}")
                ep_names.append(ep_name.strip())
            if episodes:
                play_from_list.append("奇优线路")
                play_url_list.append("#".join(episodes))

        detail_info = {
            "vod_id": vod_id,
            "vod_name": vod_name,
            "vod_pic": vod_pic,
            "vod_remarks": vod_remarks,
            "type_name": type_name,
            "vod_content": vod_content,
            "vod_play_from": "$$$".join(play_from_list) if play_from_list else "奇优线路",
            "vod_play_url": "$$$".join(play_url_list) if play_url_list else ""
        }
        return {"list": [detail_info]}

    def searchContent(self, key, quick, pg=1):
        pg = int(pg) if str(pg).isdigit() else 1
        search_url = f"{self.site_url}/search.php"
        data = {"searchword": key}
        res = self.fetch(search_url, method="POST", data=data)
        video_list = []

        if res and res.ok:
            html = res.text
            for match in re.finditer(r'<li class="active[^"]*clearfix">(.*?)</li>', html, re.S):
                item_html = match.group(1)
                vod_id = re.search(r'href="(/view/\d+\.html)"', item_html)
                vod_name = re.search(r'title="([^"]+)"', item_html)
                vod_pic = re.search(r'data-original="([^"]+)"', item_html)
                vod_remarks = re.search(r'<span class="pic-text text-right">([^<]*)</span>', item_html)

                if vod_id and vod_name and vod_pic:
                    pic_url = vod_pic.group(1)
                    if pic_url.startswith("//"):
                        pic_url = "https:" + pic_url
                    elif not pic_url.startswith(("http://", "https://")):
                        pic_url = self.site_url + pic_url

                    video_list.append({
                        "vod_id": self.site_url + vod_id.group(1),
                        "vod_name": vod_name.group(1).strip(),
                        "vod_pic": pic_url,
                        "vod_remarks": vod_remarks.group(1).strip() if vod_remarks else "搜索结果",
                        "style": {"type": "rect", "ratio": 0.75}
                    })

        pagecount = pg + 1 if len(video_list) >= self.page_size else pg
        return {
            "list": video_list,
            "page": pg,
            "pagecount": pagecount,
            "limit": self.page_size,
            "total": len(video_list) if len(video_list) < self.total else self.total
        }

    def playerContent(self, flag, id, vipFlags):
        play_url = id.split("$")[1] if "$" in id else id
        if not play_url:
            return {"parse": 0, "url": "", "header": self.headers}

        res = self.fetch(play_url)
        if res and res.ok:
            html = res.text

            iframe_match = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.S)
            if iframe_match:
                iframe_url = iframe_match.group(1)
                if iframe_url.startswith("//"):
                    iframe_url = "https:" + iframe_url
                elif not iframe_url.startswith(("http://", "https://")):
                    iframe_url = self.site_url + iframe_url

                iframe_res = self.fetch(iframe_url)
                if iframe_res and iframe_res.ok:
                    iframe_html = iframe_res.text

                    url_var = re.search(r'const\s+Url\s*=\s*["\']([^"\']+)["\']', iframe_html)
                    sign_var = re.search(r'const\s+Sign\s*=\s*["\']([^"\']+)["\']', iframe_html)
                    from_var = re.search(r'const\s+From\s*=\s*["\']([^"\']+)["\']', iframe_html)

                    if url_var and sign_var and from_var:
                        parsed = urlparse(iframe_url)
                        api_base = f"{parsed.scheme}://{parsed.netloc}"
                        api_url = f"{api_base}/player/api.php?url={url_var.group(1)}&sign={sign_var.group(1)}&t={from_var.group(1)}"

                        api_headers = {
                            "User-Agent": self.headers["User-Agent"],
                            "Referer": iframe_url,
                        }
                        api_res = self.sess.get(api_url, headers=api_headers, timeout=10, verify=False)
                        if api_res and api_res.ok:
                            try:
                                api_data = api_res.json()
                                if api_data.get("code") == 200 and api_data.get("url"):
                                    return {"parse": 0, "url": api_data["url"], "header": self.headers}
                            except:
                                pass

                    m3u8_match = re.search(r'(https?://[^"\']+\.m3u8[^"\']*)', iframe_html)
                    if m3u8_match:
                        return {"parse": 0, "url": m3u8_match.group(1), "header": self.headers}

                    mp4_match = re.search(r'(https?://[^"\']+\.mp4[^"\']*)', iframe_html)
                    if mp4_match:
                        return {"parse": 0, "url": mp4_match.group(1), "header": self.headers}

                    js_url_match = re.search(r'var\s+(?:play_url|url|src|videoUrl|video_url)\s*=\s*["\']([^"\']+)["\']', iframe_html)
                    if js_url_match:
                        js_url = js_url_match.group(1)
                        if js_url.startswith("//"):
                            js_url = "https:" + js_url
                        return {"parse": 0, "url": js_url, "header": self.headers}

                return {"parse": 0, "url": iframe_url, "header": self.headers}

            m3u8_match = re.search(r"(https?://[^\"\']+\.m3u8)", html)
            if m3u8_match:
                return {"parse": 0, "url": m3u8_match.group(1), "header": self.headers}

            mp4_match = re.search(r"(https?://[^\"\']+\.mp4)", html)
            if mp4_match:
                return {"parse": 0, "url": mp4_match.group(1), "header": self.headers}

            js_url_match = re.search(r'var\s+(?:play_url|url|src|now)\s*=\s*["\']([^"\']+)["\']', html)
            if js_url_match:
                js_url = js_url_match.group(1)
                if js_url.startswith("//"):
                    js_url = "https:" + js_url
                elif not js_url.startswith(("http://", "https://")):
                    js_url = self.site_url + js_url
                return {"parse": 0, "url": js_url, "header": self.headers}

            return {"parse": 1, "url": play_url, "header": self.headers}

        return {"parse": 0, "url": play_url, "header": self.headers}


