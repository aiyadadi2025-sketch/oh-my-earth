#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电影先生 (silidm.com) TVBox 爬虫脚本
基于 MacCMS 苹果CMS 通用结构
参考：（接口源）AI开发指南
"""
import sys
import re
import json
import urllib.parse
from base.spider import Spider

class Spider(Spider):
    name = "电影先生"
    base_url = "https://silidm.com"
    site_url = "https://silidm.com"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    class_name = ["电影", "剧集", "动漫", "综艺"]
    class_url = ["dy", "juji", "dongman", "zongyi"]

    def homeContent(self, filter=False):
        try:
            classes = []
            for i, cname in enumerate(self.class_name):
                classes.append({"type_id": self.class_url[i], "type_name": cname})
            result = {"class": classes}
            home_list = self.categoryContent(self.class_url[0], 1)
            if home_list and "list" in home_list and home_list["list"]:
                result["list"] = home_list["list"][:10]
            return result
        except Exception as e:
            self.log.error(f"[{self.name}] homeContent error: {e}")
            return {}

    def categoryContent(self, tid, pg, filter=False, content=None):
        try:
            url = self.base_url + "/type/" + tid + ".html"
            if int(pg) > 1:
                url = url + "?page=" + str(pg)
            html = self.fetch(url, headers={"User-Agent": self.ua})
            items = self._parse_items(html)
            return {
                "list": items,
                "page": int(pg),
                "pagecount": 999,
                "limit": 20,
                "total": len(items),
            }
        except Exception as e:
            self.log.error(f"[{self.name}] categoryContent error: {e}")
            return {}

    def detailContent(self, ids):
        try:
            url = self.base_url + ids[0]
            html = self.fetch(url, headers={"User-Agent": self.ua})
            vod = self._parse_detail(html)
            if vod:
                return [vod]
            return []
        except Exception as e:
            self.log.error(f"[{self.name}] detailContent error: {e}")
            return []

    def searchContent(self, key, pg, filter=False):
        try:
            encoded = urllib.parse.quote(key)
            url = self.base_url + "/search/" + encoded + "-------------.html"
            if int(pg) > 1:
                url = url + "?page=" + str(pg)
            html = self.fetch(url, headers={"User-Agent": self.ua})
            items = self._parse_items(html)
            return {
                "list": items,
                "page": int(pg),
                "pagecount": 999,
                "limit": 20,
                "total": len(items),
            }
        except Exception as e:
            self.log.error(f"[{self.name}] searchContent error: {e}")
            return {}

    def playerContent(self, flag, id, vipFlags=None):
        try:
            url = self.base_url + id
            html = self.fetch(url, headers={"User-Agent": self.ua})
            m3u8 = self._extract_m3u8(html)
            if m3u8:
                return {"parse": 0, "url": m3u8, "header": {"User-Agent": self.ua}}
        except Exception as e:
            self.log.error(f"[{self.name}] playerContent error: {e}")
        return {"parse": 1, "url": self.base_url + id, "header": {"User-Agent": self.ua}}

    def _build_vod_item(self, link, title, pic, remark, year="", area=""):
        return {
            "vod_id": link,
            "vod_name": title,
            "vod_pic": pic,
            "vod_remarks": remark,
            "vod_year": year,
            "vod_area": area,
            "vod_play_from": self.name,
            "vod_play_url": "",
        }

    def _parse_items(self, html):
        items = []
        seen = set()
        item_pattern = r'<div class="module-item-cover">.*?</div>\s*</div>\s*</div>\s*</div>\s*</div>'
        for m in re.finditer(item_pattern, html, re.S):
            block = m.group(0)
            link_m = re.search(r'href=["\x27](/video/\d+\.html)["\x27][^>]*title=["\x27]([^"\x27]+)["\x27]', block)
            if not link_m:
                continue
            link = link_m.group(1)
            title = link_m.group(2).strip()
            if link in seen:
                continue
            seen.add(link)
            pic_m = re.search(r'data-src=["\x27](https?://[^"\x27]+)["\x27]', block)
            pic = pic_m.group(1) if pic_m else ""
            caption_m = re.search(r'class="module-item-caption">(.*?)</div>', block, re.S)
            year = ""
            area = ""
            if caption_m:
                caption = caption_m.group(1)
                spans = re.findall(r'<span[^>]*>([^<]*)</span>', caption)
                year = spans[0] if len(spans) > 0 and spans[0].isdigit() else ""
                area = spans[2] if len(spans) > 2 else ""
            text_m = re.search(r'class="module-item-text">([^<]+)</div>', block)
            remark = text_m.group(1).strip() if text_m else ""
            items.append(self._build_vod_item(link, title, pic, remark, year, area))
        return items

    def _parse_detail(self, html):
        title_m = re.search(r'<title>([^<]+)</title>', html)
        raw_title = title_m.group(1).strip() if title_m else ""
        title = re.sub(r'\s*[-–—|].*', '', raw_title).strip()
        pics = re.findall(r'data-src=["\x27](https?://[^"\x27]+)["\x27]', html)
        pic = pics[0] if pics else ""
        info_m = re.search(r'class="module-info-content[^>]*>(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL)
        info_text = info_m.group(1) if info_m else ""
        year = ""
        area = ""
        content = ""
        year_m = re.search(r'<span>(\d{4})</span>', info_text)
        year = year_m.group(1) if year_m else ""
        area_m = re.search(r'href="(/show/\d+---([^<]*))"', info_text)
        area = area_m.group(2).strip() if area_m else ""
        desc_m = re.search(r'class="module-info-desc[^>]*>[\s\S]*?<p[^>]*>(.*?)</p>', html, re.DOTALL)
        if desc_m:
            content = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip()
        plays = re.findall(r'href="(/play/\d+-(\d+)-\d+\.html)"[^>]*>(.*?)</a>', html, re.DOTALL)
        lines = {}
        for play_link, line_id, play_text in plays:
            text = re.sub(r'<[^>]+>', '', play_text).strip()
            if not text:
                continue
            lines.setdefault(line_id.strip(), []).append((text, play_link))
        if not lines:
            plays2 = re.findall(r'href="(/play/[^"\s]+)"[^>]*title="([^"]+)"', html)
            for play_link, ep_name in plays2:
                lines.setdefault("1", []).append((ep_name, play_link))
        play_from_list = []
        play_url_list = []
        for lid in sorted(lines.keys()):
            eps = lines[lid]
            play_from_list.append("线路" + lid)
            play_url_list.append("#".join(n + "$" + self.base_url + l for n, l in eps))
        if not play_from_list:
            play_from_list.append(self.name)
            play_url_list.append("")
        return {
            "vod_id": "",
            "vod_name": title,
            "vod_pic": pic,
            "vod_year": year,
            "vod_area": area,
            "vod_content": content,
            "vod_play_from": "$$$".join(play_from_list),
            "vod_play_url": "$$$".join(play_url_list),
        }

    def _extract_m3u8(self, html):
        player_m = re.search(r'var\s+player_aaaa\s*=\s*\{(.*?)\}', html, re.S)
        if player_m:
            try:
                cfg = json.loads("{" + player_m.group(1) + "}")
                url = cfg.get("url", "")
                if url:
                    return url.replace("\\/", "/")
            except Exception:
                pass
        m3u8s = re.findall(r'(https?://[^"\s<]+\.m3u8[^"\s<]*)', html)
        return m3u8s[0] if m3u8s else ""
