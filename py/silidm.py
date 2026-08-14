#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电影先生 (silidm.com) TVBox 爬虫脚本
使用标准库 urllib，不依赖 requests
"""
import re
import json
import urllib.parse
import urllib.request
import ssl

class Spider:
    name = "电影先生"
    base_url = "https://silidm.com"
    site_url = "https://silidm.com"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    class_name = ["电影", "剧集", "动漫", "综艺"]
    class_url = ["dy", "juji", "dongman", "zongyi"]

    def fetch(self, url, headers=None):
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", self.ua)
            if headers:
                for k, v in headers.items():
                    req.add_header(k, v)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[{self.name}] fetch error: {e}")
            return ""

    def homeContent(self, filter=False):
        try:
            classes = []
            for i, cname in enumerate(self.class_name):
                classes.append({"type_id": self.class_url[i], "type_name": cname})
            return {"class": classes, "filters": {}}
        except Exception as e:
            print(f"[{self.name}] homeContent error: {e}")
            return {"class": classes, "filters": {}}

    def categoryContent(self, tid, pg, filter=False, content=None):
        try:
            url = self.base_url + "/type/" + tid + ".html"
            if int(pg) > 1:
                url = url + "?page=" + str(pg)
            html = self.fetch(url)
            items = self._parse_items(html)
            return {"list": items, "page": int(pg), "pagecount": 999, "limit": 20, "total": len(items)}
        except Exception as e:
            print(f"[{self.name}] categoryContent error: {e}")
            return {}

    def detailContent(self, ids):
        try:
            url = self.base_url + ids[0]
            html = self.fetch(url)
            vod = self._parse_detail(html)
            if vod:
                return [vod]
            return []
        except Exception as e:
            print(f"[{self.name}] detailContent error: {e}")
            return []

    def searchContent(self, key, pg, filter=False):
        try:
            encoded = urllib.parse.quote(key)
            url = self.base_url + "/search/" + encoded + "-------------.html"
            if int(pg) > 1:
                url = url + "?page=" + str(pg)
            html = self.fetch(url)
            items = self._parse_items(html)
            return {"list": items, "page": int(pg), "pagecount": 999, "limit": 20, "total": len(items)}
        except Exception as e:
            print(f"[{self.name}] searchContent error: {e}")
            return {}

    def playerContent(self, flag, id, vipFlags=None):
        try:
            url = self.base_url + id
            html = self.fetch(url)
            m3u8 = self._extract_m3u8(html)
            if m3u8:
                return {"parse": 0, "url": m3u8, "header": {"User-Agent": self.ua}}
        except Exception as e:
            print(f"[{self.name}] playerContent error: {e}")
        return {"parse": 1, "url": self.base_url + id, "header": {"User-Agent": self.ua}}

    def _build_vod_item(self, link, title, pic, remark, year="", area=""):
        return {"vod_id": link, "vod_name": title, "vod_pic": pic, "vod_remarks": remark, "vod_year": year, "vod_area": area, "vod_play_from": self.name, "vod_play_url": ""}

    def _parse_items(self, html):
        items = []
        seen = set()
        pattern = r'<div class="module-item-cover">.*?</div>\s*</div>\s*</div>\s*</div>\s*</div>'
        for m in re.finditer(pattern, html, re.S):
            block = m.group(0)
            lm = re.search(r'href=["\x27](/video/\d+\.html)["\x27][^>]*title=["\x27]([^"\x27]+)["\x27]', block)
            if not lm:
                continue
            link = lm.group(1)
            title = lm.group(2).strip()
            if link in seen:
                continue
            seen.add(link)
            pm = re.search(r'data-src=["\x27](https?://[^"\x27]+)["\x27]', block)
            pic = pm.group(1) if pm else ""
            cm = re.search(r'class="module-item-caption">(.*?)</div>', block, re.S)
            year, area = "", ""
            if cm:
                spans = re.findall(r'<span[^>]*>([^<]*)</span>', cm.group(1))
                year = spans[0] if len(spans) > 0 and spans[0].isdigit() else ""
                area = spans[2] if len(spans) > 2 else ""
            tm = re.search(r'class="module-item-text">([^<]+)</div>', block)
            remark = tm.group(1).strip() if tm else ""
            items.append(self._build_vod_item(link, title, pic, remark, year, area))
        return items

    def _parse_detail(self, html):
        tm = re.search(r'<title>([^<]+)</title>', html)
        raw = tm.group(1).strip() if tm else ""
        title = re.sub(r'\s*[-|].*', '', raw).strip()
        pics = re.findall(r'data-src=["\x27](https?://[^"\x27]+)["\x27]', html)
        pic = pics[0] if pics else ""
        im = re.search(r'class="module-info-content[^>]*>(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL)
        info = im.group(1) if im else ""
        year = ""
        ym = re.search(r'<span>(\d{4})</span>', info)
        year = ym.group(1) if ym else ""
        area = ""
        am = re.search(r'href="(/show/\d+---([^<]*))"', info)
        area = am.group(2).strip() if am else ""
        content = ""
        dm = re.search(r'class="module-info-desc[^>]*>[\s\S]*?<p[^>]*>(.*?)</p>', html, re.DOTALL)
        if dm:
            content = re.sub(r'<[^>]+>', '', dm.group(1)).strip()
        plays = re.findall(r'href="(/play/\d+-(\d+)-\d+\.html)"[^>]*>(.*?)</a>', html, re.DOTALL)
        lines = {}
        for pl, lid, pt in plays:
            txt = re.sub(r'<[^>]+>', '', pt).strip()
            if txt:
                lines.setdefault(lid.strip(), []).append((txt, pl))
        if not lines:
            plays2 = re.findall(r'href="(/play/[^"\s]+)"[^>]*title="([^"]+)"', html)
            for pl, en in plays2:
                lines.setdefault("1", []).append((en, pl))
        pf, pu = [], []
        for lid in sorted(lines.keys()):
            eps = lines[lid]
            pf.append("线路" + lid)
            pu.append("#".join(n + "$" + self.base_url + l for n, l in eps))
        if not pf:
            pf.append(self.name)
            pu.append("")
        return {"vod_id": "", "vod_name": title, "vod_pic": pic, "vod_year": year, "vod_area": area, "vod_content": content, "vod_play_from": "$$$".join(pf), "vod_play_url": "$$$".join(pu)}

    def _extract_m3u8(self, html):
        pm = re.search(r'var\s+player_aaaa\s*=\s*\{(.*?)\}', html, re.S)
        if pm:
            try:
                cfg = json.loads("{" + pm.group(1) + "}")
                url = cfg.get("url", "")
                if url:
                    return url.replace("\\/", "/")
            except Exception:
                pass
        ms = re.findall(r'(https?://[^"\s<]+\.m3u8[^"\s<]*)', html)
        return ms[0] if ms else ""


def load():
    return Spider()
