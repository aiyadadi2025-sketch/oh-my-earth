# -*- coding: utf-8 -*-
# gimyai.org 劇迷 (MACMS v10 镜像站 - 无CF验证)
# 原站 gimyai.tw 启用了Cloudflare Managed Challenge + Turnstile验证
# 本脚本使用镜像站 gimyai.org 绕过验证
import requests
import re
import json
import base64
from urllib.parse import quote

try:
    from base.spider import Spider
except ImportError:
    class Spider:
        def init(self, extend=""):
            pass

# 使用镜像站，避免CF Turnstile验证
HOST = 'https://gimytv.biz/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

# 镜像站分类映射
CATEGORIES = [
    {"type_id": "13", "type_name": "陸劇"},
    {"type_id": "20", "type_name": "韓劇"},
    {"type_id": "15", "type_name": "日劇"},
    {"type_id": "14", "type_name": "台劇"},
    {"type_id": "22", "type_name": "港劇"},
    {"type_id": "21", "type_name": "海外劇"},
    {"type_id": "2", "type_name": "電視劇"},
    {"type_id": "25", "type_name": "短劇"},
    {"type_id": "4", "type_name": "動漫"},
    {"type_id": "3", "type_name": "綜藝"},
    {"type_id": "23", "type_name": "紀錄片"},
    {"type_id": "1", "type_name": "電影"},
]


class Spider(Spider):
    def init(self, extend=""):
        self.headers = {
            "User-Agent": UA,
            "Referer": HOST,
            "Accept-Language": "zh-TW,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        self.headers_ajax = {
            "User-Agent": UA,
            "Referer": HOST,
            "X-Requested-With": "XMLHttpRequest",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def getName(self):
        return "劇迷Gimy(镜像)"

    def homeContent(self, filter):
        result = {"class": CATEGORIES, "list": []}
        try:
            r = self.session.get(HOST, timeout=12)
            result["list"] = self._cards(r.text)
        except Exception:
            pass
        return result

    def homeVideoContent(self):
        try:
            r = self.session.get(HOST, timeout=12)
            return {"list": self._cards(r.text)}
        except Exception:
            return {"list": []}

    def categoryContent(self, tid, pg, filter, extend):
        result = {"list": [], "page": int(pg), "pagecount": 1, "limit": 20, "total": 0}
        # 镜像站使用 /vodtype/{id}.html 格式
        if int(pg) > 1:
            url = f"{HOST}/vodtype/{tid}/page/{pg}.html"
        else:
            url = f"{HOST}/vodtype/{tid}.html"
        try:
            r = self.session.get(url, timeout=12)
            videos = self._cards(r.text)
            result["list"] = videos
            result["pagecount"] = max(1, (len(videos) + 19) // 20) if videos else 1
            result["total"] = len(videos)
        except Exception:
            pass
        return result

    def detailContent(self, ids):
        result = {"list": []}
        if not ids:
            return result
        vod_id = ids[0]
        # 镜像站使用 /voddetail/{id}.html 格式
        if not vod_id.startswith('/voddetail/'):
            vod_id = f"/voddetail/{vod_id}"
        url = HOST + vod_id
        if not url.endswith('.html'):
            url += '.html'
        try:
            r = self.session.get(url, headers=self.headers, timeout=12)
            html = r.text
        except Exception:
            return result

        vod = {}
        vod["vod_id"] = vod_id
        # 提取标题
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        vod["vod_name"] = m.group(1).strip() if m else ""
        # 提取海报
        pic = ""
        m = re.search(r'background:\s*url\(([^)]+)\)', html)
        if m:
            pic = m.group(1).strip('"\'')
            if pic.startswith('/'):
                pic = HOST + pic
        if not pic:
            m = re.search(r'<img[^>]*src="(https?://[^"]+)"', html)
            if m:
                pic = m.group(1).replace('&amp;', '&')
        vod["vod_pic"] = pic
        vod["vod_remarks"] = ""
        m = re.search(r'class="note[^>]*>([^<]+)<', html)
        if m:
            vod["vod_remarks"] = m.group(1).strip()
        vod["vod_year"] = ""
        vod["vod_actor"] = ""
        m = re.search(r'class="subtitle[^>]*>([^<]+)<', html)
        if m:
            vod["vod_actor"] = m.group(1).strip()
        vod["vod_director"] = ""
        vod["vod_content"] = ""
        m = re.search(r'name="description"\s+content="([^"]+)"', html)
        if m:
            vod["vod_content"] = re.sub(r'^[^,]+,\s*', '', m.group(1)).strip()

        # 提取播放集数和线路名
        eps = self._eps_from_detail(html)
        route_names = self._get_route_names(html)
        if eps:
            # 使用实际线路名，如果没有则使用默认名
            if route_names and len(route_names) == len(eps):
                vod["vod_play_from"] = "$$$".join(route_names)
            else:
                vod["vod_play_from"] = "$$$".join(["線上看"] * len(eps))
            vod["vod_play_url"] = "$$$".join(eps)
        else:
            # 尝试从详情页提取播放链接
            play_links = re.findall(r'href="(/video/\d+-\d+\.html)#sid=(\d+)"', html)
            if play_links:
                seen = set()
                eps = []
                for link, sid in play_links:
                    key = (link, sid)
                    if key in seen:
                        continue
                    seen.add(key)
                    # 提取集数
                    em = re.search(r'/video/\d+-(\d+)\.html', link)
                    ep_num = em.group(1) if em else link
                    eps.append(f"{ep_num}${link}#sid={sid}")
                if eps:
                    vod["vod_play_from"] = "線上看"
                    vod["vod_play_url"] = "#".join(eps)

        result["list"] = [vod]
        return result

    def searchContent(self, key, quick, pg=1):
        result = {"list": [], "page": 1, "pagecount": 1, "limit": 20, "total": 0}
        q = quote(key)
        # 镜像站搜索URL
        url = f"{HOST}/vodsearch/{q}-------------.html"
        try:
            r = self.session.get(url, headers=self.headers, timeout=12)
            lst = self._cards(r.text)
            if lst:
                result["list"] = lst
                result["total"] = len(lst)
        except Exception:
            pass
        return result

    def playerContent(self, flag, id, vipFlags):
        result = {"parse": 0, "url": id}
        if not id.startswith('http'):
            play_url = HOST + id
            if not play_url.endswith('.html'):
                play_url += '.html'
            try:
                r = self.session.get(play_url, headers=self.headers, timeout=12)
                pd = self._player_data(r.text)
            except Exception:
                return result
            if not pd:
                return result
            url = pd.get("url", "").replace('\\/', '/')
            if not url:
                return result
            result["url"] = self._resolve(url)
        else:
            result["url"] = self._resolve(id)
        return result

    def _resolve(self, url):
        # 直接返回m3u8等直链
        return url

    def _player_data(self, html):
        """解析player_aaaa数据（镜像站格式）"""
        # 从script标签中提取
        scripts = re.findall(r'<script[^>]*>([\s\S]{0,5000}?)</script>', html)
        for s in scripts:
            if 'player_aaaa' in s:
                m = re.search(r'player_aaaa\s*=\s*(\{[\s\S]*?\"nid\"\s*:\s*\d+\})', s)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except Exception:
                        pass
        # 备用：直接搜索
        m = re.search(r'var\s+player_aaaa\s*=\s*(\{[\s\S]*?\"nid\"\s*:\s*\d+\})', html)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        return None

    def _cards(self, html):
        """提取视频卡片（镜像站格式）"""
        out = []
        seen = set()
        # 镜像站使用 <li class="col-md-2 col-sm-3 col-xs-4"> 结构
        for m in re.finditer(r'<li\s+class="col-md-2\s+col-sm-3\s+col-xs-4">([\s\S]{0,1500}?)</li>', html):
            block = m.group(1)
            path = ""
            pm = re.search(r'href="(/voddetail/\d+\.html)"', block)
            if pm:
                path = pm.group(1)
            if not path or path in seen:
                continue
            seen.add(path)

            pic = ""
            # 优先从data-original获取（懒加载图片）
            im = re.search(r'data-original="([^"]+)"', block)
            if im:
                pic = im.group(1)
                if pic.startswith('/'):
                    pic = HOST + pic
            if not pic:
                im = re.search(r'background:\s*url\(([^)]+)\)', block)
                if im:
                    pic = im.group(1).strip('"\'')
                    if pic.startswith('/'):
                        pic = HOST + pic
            if not pic:
                im = re.search(r'<img[^>]*src="([^"]+)"', block)
                if im:
                    pic = im.group(1)
                    if pic.startswith('/'):
                        pic = HOST + pic

            name = ""
            nm = re.search(r'title="([^"]+)"', block)
            if nm:
                name = nm.group(1).strip()
            if not name:
                nm = re.search(r'<h5[^>]*><a[^>]*>([^<]+)</a>', block)
                if nm:
                    name = nm.group(1).strip()

            remark = ""
            rm = re.search(r'class="note[^>]*>([^<]+)<', block)
            if rm:
                remark = rm.group(1).strip()

            if name:
                out.append({"vod_id": path, "vod_name": name, "vod_pic": pic, "vod_remarks": remark})
        return out

    def _eps_from_detail(self, html):
        """从详情页提取集数列表（支持多线路）"""
        # 提取线路名称和对应的playlist ID
        routes = re.findall(r'<li><a\s+class="gico[^"]*"[^>]*>([^<]+)</a></li>\s*<ul[^>]*id="con_playlist_(\d+)"', html)

        if not routes:
            # 备用：直接按sid分组
            ep_links = re.findall(r'href="(/video/\d+-\d+\.html)#sid=(\d+)"', html)
            if not ep_links:
                return []
            by_sid = {}
            for link, sid in ep_links:
                if sid not in by_sid:
                    by_sid[sid] = []
                by_sid[sid].append(link)
            lines = []
            for sid, links in sorted(by_sid.items()):
                eps = []
                seen = set()
                for link in links:
                    if link in seen:
                        continue
                    seen.add(link)
                    em = re.search(r'/video/\d+-(\d+)\.html', link)
                    ep_num = em.group(1) if em else link
                    # 使用 #sid=X 作为链接的一部分，但不作为分隔符
                    eps.append(f"{ep_num}${link}")
                if eps:
                    lines.append("#".join(eps))
            return lines

        # 按线路提取集数
        lines = []
        for route_name, sid in routes:
            # 提取该线路的所有集数
            # 格式：<li><a ... href="/video/30-1.html#sid=3">第01集</a></li>
            eps = []
            seen = set()
            for m in re.finditer(r'href="(/video/\d+-\d+\.html)#sid=' + re.escape(sid) + r'"[^>]*>([^<]+)<', html):
                link = m.group(1)
                ep_title = m.group(2).strip()
                if link in seen:
                    continue
                seen.add(link)
                if ep_title:
                    # 使用 #sid=X 作为链接的一部分
                    eps.append(f"{ep_title}${link}")
                else:
                    em = re.search(r'/video/\d+-(\d+)\.html', link)
                    ep_num = em.group(1) if em else link
                    eps.append(f"{ep_num}${link}")
            if eps:
                lines.append("#".join(eps))

        return lines

    def _get_route_names(self, html):
        """提取线路名称列表"""
        routes = re.findall(r'<li><a\s+class="gico[^"]*"[^>]*>([^<]+)</a></li>', html)
        return [r.strip() for r in routes]

    def localProxy(self, param):
        return [200, "video/MP2T", {}, param]

    def destroy(self):
        self.session.close()
