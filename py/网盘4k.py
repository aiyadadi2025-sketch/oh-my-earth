#!/usr/bin/python
# -*- coding: utf-8 -*-
import json, re, base64
from urllib.parse import quote
import requests
from lxml import etree
from base.spider import Spider

class Spider(Spider):
    def getName(self): return "SeedHub"
    def init(self, extend=""):
        self.name = "SeedHub"
        self.hosts = ["https://seedog.cc", "https://sidhub.cc", "https://seeduck.cc", "https://hubdog.cc"]
        self.host = self.hosts[0]
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.host + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        try:
            import cloudscraper
            self.session = cloudscraper.create_scraper()
        except:
            self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.verify = False
        self.cf_cookie = ""
        self.proxies = None
        if extend:
            try:
                cfg = json.loads(extend) if isinstance(extend, str) else extend
                self.cf_cookie = cfg.get("cookie", "")
                if cfg.get("host"):
                    self.host = cfg["host"]
                    if self.host not in self.hosts: self.hosts.insert(0, self.host)
                if cfg.get("proxy"):
                    self.proxies = {"http": cfg["proxy"], "https": cfg["proxy"]}
            except:
                if isinstance(extend, str) and ("cf_clearance" in extend or "=" in extend):
                    self.cf_cookie = extend
        if self.cf_cookie:
            self.session.headers["Cookie"] = self.cf_cookie
        if self.proxies:
            self.session.proxies = self.proxies
        self.categories = [
            {"type_id": "1", "type_name": "电影"},
            {"type_id": "2", "type_name": "动漫"},
            {"type_id": "3", "type_name": "剧集"},
        ]
        self.cat_types = {
            "1": [("7","剧情"),("8","喜剧"),("4","惊悚"),("1","动作"),("10","爱情"),("12","犯罪"),("3","恐怖"),("13","悬疑"),("6","冒险"),("2","科幻"),("5","奇幻"),("43","纪录片"),("9","家庭"),("14","传记"),("16","战争"),("15","历史"),("19","音乐"),("23","运动"),("11","同性"),("22","古装"),("28","歌舞"),("24","西部"),("35","短片"),("21","武侠"),("17","灾难")],
            "2": [("25","动画"),("29","喜剧"),("26","奇幻"),("27","冒险"),("30","剧情"),("50","动作"),("53","科幻"),("48","爱情"),("31","家庭"),("51","短片"),("54","悬疑"),("32","儿童"),("59","惊悚"),("62","运动"),("47","音乐"),("94","古装"),("67","恐怖"),("69","犯罪"),("49","歌舞"),("70","战争"),("71","武侠"),("68","历史"),("90","同性"),("105","灾难"),("86","西部")],
            "3": [("33","剧情"),("39","喜剧"),("58","爱情"),("34","犯罪"),("38","悬疑"),("37","惊悚"),("36","动作"),("41","奇幻"),("55","科幻"),("88","古装"),("63","纪录片"),("42","冒险"),("60","历史"),("56","恐怖"),("75","同性"),("57","家庭"),("66","真人秀"),("61","战争"),("40","传记"),("87","运动"),("65","音乐"),("99","武侠"),("64","短片"),("76","西部"),("79","歌舞")],
        }
    def _get(self, url):
        for host in self.hosts:
            target = url.replace(self.hosts[0], host) if host != self.hosts[0] else url
            try:
                r = self.session.get(target, headers=self.headers, timeout=15, verify=False, proxies=getattr(self.session, "proxies", None) or None)
                if r.status_code == 200 and len(r.text) > 1000:
                    r.encoding = r.apparent_encoding or "utf-8"
                    return r.text
            except: continue
        return ""
    def _fix(self, url):
        if not url: return ""
        if url.startswith("//"): return "https:" + url
        if url.startswith("/"): return self.host + url
        return url
    def _list(self, html):
        tree = etree.HTML(html or "")
        out = []
        seen = set()
        for c in tree.xpath('//div[contains(@class,"cover-container")]/div[contains(@class,"cover")]'):
            try:
                a = c.xpath('.//a[contains(@class,"image")]')
                if not a: continue
                href = a[0].get("href", "")
                m = re.search(r'/movies/(\d+)', href)
                if not m or m.group(1) in seen: continue
                vid = m.group(1)
                seen.add(vid)
                img = a[0].xpath('.//img')
                pic = self._fix((img[0].get("src") or img[0].get("data-src") or "") if img else "")
                title = (a[0].get("title") or "").strip()
                if not title:
                    h2 = c.xpath('.//h2')
                    title = ''.join(h2[0].xpath('.//text()')).strip().replace('#', '').strip() if h2 else vid
                li = c.xpath('.//li')
                meta = ''.join(li[1].xpath('.//text()')).strip() if len(li) > 1 else ""
                remarks = ""
                sm = re.search(r'豆瓣评分.*?(\d+\.\d+)', meta)
                if sm and float(sm.group(1)) > 0: remarks = "评分:" + sm.group(1)
                else:
                    ym = re.match(r'(\d{4})', meta)
                    if ym: remarks = ym.group(1)
                out.append({"vod_id": vid, "vod_name": title, "vod_pic": pic, "vod_remarks": remarks})
            except: continue
        return out
    def _pagecount(self, html, pg):
        tree = etree.HTML(html or "")
        max_page = pg
        for a in tree.xpath('//a[contains(@href,"page=")]'):
            m = re.search(r'page=(\d+)', a.get("href", ""))
            if m:
                p = int(m.group(1))
                if p > max_page: max_page = p
        return max_page
    def homeContent(self, filter):
        html = self._get(self.host + "/")
        filters = {}
        for cat in self.categories:
            tid = cat["type_id"]
            type_opts = [{"n": "全部", "v": ""}] + [{"n": n, "v": t} for t, n in self.cat_types.get(tid, [])]
            sort_opts = [{"n": "最近更新", "v": "update"}, {"n": "上映时间", "v": "date"}, {"n": "豆瓣评分", "v": "score"}]
            filters[tid] = [{"key": "type", "name": "类型", "value": type_opts}, {"key": "order", "name": "排序", "value": sort_opts}]
        return {"class": self.categories, "list": self._list(html), "filters": filters}
    def homeVideoContent(self):
        return {"list": self._list(self._get(self.host + "/"))}
    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        if isinstance(extend, str):
            try: extend = json.loads(extend)
            except: extend = {}
        extend = extend or {}
        type_id = extend.get("type", "")
        order = extend.get("order", "update")
        if type_id:
            url = f"{self.host}/categories/{tid}/types/{type_id}/movies/?page={pg}&order={order}"
        else:
            url = f"{self.host}/categories/{tid}/movies/?page={pg}&order={order}"
        html = self._get(url)
        items = self._list(html)
        limit = len(items) or 20
        pagecount = self._pagecount(html, pg)
        return {"page": pg, "pagecount": pagecount, "limit": limit, "total": 0, "list": items}
    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, list) else ids
        html = self._get(f"{self.host}/movies/{vid}/")
        if not html: return {"list": []}
        tree = etree.HTML(html)
        h1 = tree.xpath('//h1')
        name = ''.join(h1[0].xpath('.//text()')).strip().replace('#', '').strip() if h1 else str(vid)
        pic = self._fix((tree.xpath('//div[contains(@class,"cover-container")]//img/@src') or [""])[0])
        director = actor = genre = area = year = ""
        for li in tree.xpath('//div[contains(@class,"cover-container")]//ul/li'):
            text = ''.join(li.xpath('.//text()')).strip()
            if text.startswith("导演"): director = text.split(":", 1)[1].strip()
            elif text.startswith("主演"): actor = text.split(":", 1)[1].strip()
            elif text.startswith("类型"): genre = text.split(":", 1)[1].strip()
            elif text.startswith("制片国家"): area = text.split(":", 1)[1].strip()
            elif text.startswith("首播"):
                ym = re.search(r'(\d{4})', text)
                if ym: year = ym.group(1)
        desc_nodes = tree.xpath('//h2[@id="description"]/following-sibling::p[1]')
        desc = ''.join(desc_nodes[0].xpath('.//text()')).strip() if desc_nodes else ""
        magnet_eps = []
        for s in tree.xpath('//ul[@class="seeds"]/li'):
            a = s.xpath('.//a')
            if not a: continue
            title = (a[0].get("title") or "").strip().replace('#', ' ').replace('$', ' ')
            href = a[0].get("href", "")
            sid_m = re.search(r'seed_id=(\d+)', href)
            if not sid_m: continue
            sid = sid_m.group(1)
            size = s.xpath('.//code[@class="size"]/text()')
            size_text = size[0].strip() if size else ""
            feats = s.xpath('.//code[@class="seed-feature"]/text()')
            feat_text = "/".join(f.strip() for f in feats) if feats else ""
            ep_title = f"{title[:40]} [{size_text}]{(' ' + feat_text) if feat_text else ''}"
            magnet_eps.append(f"{ep_title}$magnet${sid}")
        pan_data = {}
        pan_names = {"baidu": "百度网盘", "quark": "夸克网盘", "xunlei": "迅雷网盘", ".uc.": "UC网盘", "ali": "阿里网盘"}
        for p in tree.xpath('//ul[@class="pan-links"]/li/a'):
            title = (p.get("title") or "").strip().replace('#', ' ').replace('$', ' ')
            href = p.get("href", "")
            link_type = p.get("data-link", "")
            pan_m = re.search(r'pan_id_(\d+)', href)
            if not pan_m: continue
            pan_id = pan_m.group(1)
            pan_type = "网盘"
            for key, name_t in pan_names.items():
                if key in link_type:
                    pan_type = name_t
                    break
            if pan_type not in pan_data: pan_data[pan_type] = []
            pan_data[pan_type].append(f"{title[:50]}$pan${pan_id}")
        play_from = []
        play_url = []
        if magnet_eps:
            play_from.append("磁力下载")
            play_url.append("#".join(magnet_eps))
        for ptype in ["百度网盘", "夸克网盘", "迅雷网盘", "UC网盘", "阿里网盘", "网盘"]:
            eps = pan_data.get(ptype, [])
            if eps:
                play_from.append(ptype)
                play_url.append("#".join(eps))
        if not play_from:
            play_from.append("暂无资源")
            play_url.append(f"暂无资源${vid}")
        vod = {
            "vod_id": str(vid), "vod_name": name, "vod_pic": pic,
            "type_name": genre, "vod_year": year, "vod_area": area,
            "vod_director": director, "vod_actor": actor, "vod_content": desc,
            "vod_play_from": "$$$".join(play_from), "vod_play_url": "$$$".join(play_url),
        }
        return {"list": [vod]}
    def searchContent(self, key, quick, pg="1"):
        url = f"{self.host}/s/{quote(key)}/"
        html = self._get(url)
        return {"list": self._list(html), "page": int(pg) if str(pg).isdigit() else 1}
    def playerContent(self, flag, id, vipFlags):
        parts = id.split("$")
        if len(parts) < 2:
            return {"parse": 1, "url": self._fix(id), "header": json.dumps(self.headers)}
        rtype = parts[0]
        rid = parts[1]
        if rtype == "magnet":
            html = self._get(f"{self.host}/link_start/?seed_id={rid}&movie_title=play")
            m = re.search(r'const data\s*=\s*"([^"]+)"', html)
            if m:
                magnet = base64.b64decode(m.group(1)).decode('utf-8')
                return {"parse": 0, "url": magnet, "header": json.dumps(self.headers)}
        elif rtype == "pan":
            html = self._get(f"{self.host}/link_start/?redirect_to=pan_id_{rid}&movie_title=play")
            m = re.search(r'var panLink\s*=\s*"([^"]+)"', html)
            if m:
                return {"parse": 0, "url": m.group(1), "header": json.dumps(self.headers)}
        return {"parse": 1, "url": "", "header": json.dumps(self.headers)}
