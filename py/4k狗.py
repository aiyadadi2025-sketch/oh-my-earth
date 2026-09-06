#!/usr/bin/python
# -*- coding: utf-8 -*-
import json, re, warnings
from urllib.parse import quote
import requests
from lxml import etree
warnings.filterwarnings("ignore")
try:
    from base.spider import Spider
except ImportError:
    class Spider: pass


class csp_4kgou(Spider):
    def getName(self):
        return "4K狗"

    def init(self, extend=""):
        self.host = "https://www.4kgou.com"
        if extend and extend.startswith("http"):
            self.host = extend.rstrip("/")
        self.api = self.host + "/wp-json/wp/v2"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": self.host + "/"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self._load_cats()

    def _get(self, url):
        try:
            r = self.session.get(url, headers=self.headers, timeout=15, verify=False,
                                  proxies=getattr(self.session, "proxies", None) or None)
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception:
            return ""

    def _jget(self, url):
        try:
            r = self.session.get(url, headers=self.headers, timeout=15, verify=False,
                                  proxies=getattr(self.session, "proxies", None) or None)
            return r.json(), dict(r.headers)
        except Exception:
            return [], {}

    def _fix(self, url):
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return self.host + url
        return url

    def _load_cats(self):
        self.cat_subs = {}
        try:
            cats, _ = self._jget(self.api + "/categories?per_page=100&_fields=id,name,count,parent")
            top = [c for c in cats if c.get("parent") == 0 and c.get("count", 0) > 0]
            self.categories = [{"type_id": str(c["id"]), "type_name": c["name"]}
                                for c in sorted(top, key=lambda x: -x["count"])]
            for c in cats:
                p = c.get("parent", 0)
                if p > 0 and c.get("count", 0) > 0:
                    self.cat_subs.setdefault(str(p), []).append((str(c["id"]), c["name"]))
        except Exception:
            self.categories = [
                {"type_id": "362", "type_name": "60帧【4K】"},
                {"type_id": "415", "type_name": "120帧"},
                {"type_id": "364", "type_name": "60帧【1086P】"},
                {"type_id": "408", "type_name": "其他【热舞及风景】"},
            ]
            self.cat_subs = {
                "362": [("407", "国内"), ("366", "欧美"), ("380", "韩国"), ("374", "香港粤语")],
                "415": [("417", "120帧【4K】"), ("416", "120帧【1086P】")],
                "364": [("394", "欧美"), ("395", "日韩")],
                "408": [("410", "美女热舞"), ("409", "风景纪录片")],
            }
        self.filters = {}
        for c in self.categories:
            tid = c["type_id"]
            sub_list = self.cat_subs.get(tid, [])
            if sub_list:
                self.filters[tid] = [{"key": "sub", "name": "分类",
                                      "value": [{"n": "全部", "v": ""}] + [{"n": n, "v": cid} for cid, n in sub_list]}]

    def _item(self, p):
        vid = str(p.get("id", ""))
        title = re.sub(r"<[^>]+>", "", p.get("title", {}).get("rendered", "")).strip()
        pic = ""
        emb = p.get("_embedded", {})
        fm = emb.get("wp:featuredmedia", [])
        if fm:
            pic = fm[0].get("source_url", "")
        cats = []
        for tg in emb.get("wp:term", []):
            for t in tg:
                if t.get("taxonomy") == "category":
                    cats.append(t.get("name", ""))
        return {"vod_id": vid, "vod_name": title, "vod_pic": pic,
                "vod_remarks": " / ".join(cats) if cats else ""}

    def _api_posts(self, params):
        query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        data, headers = self._jget(f"{self.api}/posts?{query}&_embed")
        total = int(headers.get("X-WP-Total", "0") or "0")
        total_pages = int(headers.get("X-WP-TotalPages", "1") or "1")
        per_page = int(params.get("per_page", "20") or "20")
        return data, total, total_pages, per_page

    @staticmethod
    def _field(text, key):
        b = r'导演|编剧|主演|类型|制片|语言|上映|片长|又名|IMDb'
        m = re.search(rf'{key}[^:：]*[:：]\s*(.*?)(?=(?:{b})[^:：]*[:：]|$)', text, re.S)
        return m.group(1).strip().rstrip(" /") if m else ""

    def homeContent(self, filter):
        data, _, _, _ = self._api_posts({"per_page": "20", "page": "1"})
        return {"class": self.categories, "list": [self._item(p) for p in data], "filters": self.filters}

    def homeVideoContent(self):
        data, _, _, _ = self._api_posts({"per_page": "20", "page": "1"})
        return {"list": [self._item(p) for p in data]}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        if isinstance(extend, str):
            try:
                extend = json.loads(extend) if extend else {}
            except Exception:
                extend = {}
        extend = extend or {}
        sub = extend.get("sub", "")
        cat_id = sub if sub else tid
        data, total, total_pages, per_page = self._api_posts(
            {"categories": cat_id, "per_page": "20", "page": str(pg)})
        return {"page": pg, "pagecount": total_pages, "limit": per_page,
                "total": total, "list": [self._item(p) for p in data]}

    def detailContent(self, ids):
        out = []
        for vid in ids:
            try:
                html = self._get(f"{self.host}/{vid}.html")
                tree = etree.HTML(html)
                name_nodes = tree.xpath('//h1[contains(@class,"entry-title")]')
                name = " ".join(name_nodes[0].xpath(".//text()")).strip() if name_nodes else str(vid)
                pic = ""
                img_nodes = (tree.xpath('//div[contains(@class,"entry-content")]//img[@data-srcset]')
                             or tree.xpath('//div[contains(@class,"entry-content")]//img[@srcset]'))
                if img_nodes:
                    srcset = img_nodes[0].get("data-srcset") or img_nodes[0].get("srcset") or ""
                    urls = re.findall(r"(https?://[^\s,]+)", srcset)
                    if urls:
                        pic = urls[-1]
                if not pic:
                    img_nodes = (tree.xpath('//div[contains(@class,"entry-content")]//img[@data-src]')
                                 or tree.xpath('//div[contains(@class,"entry-content")]//img[@src]'))
                    if img_nodes:
                        pic = self._fix(img_nodes[0].get("data-src") or img_nodes[0].get("src") or "")
                info_text = ""
                for p in tree.xpath('//div[contains(@class,"entry-content")]//p'):
                    t = " ".join(p.xpath(".//text()")).strip()
                    if t and any(k in t for k in ["导演", "编剧", "主演", "类型", "制片", "语言", "上映", "片长", "又名", "IMDb"]):
                        info_text = t
                        break
                director = self._field(info_text, "导演")
                actor = self._field(info_text, "主演")
                year = ""
                m = re.search(r'上映.*?(\d{4})', info_text)
                if m:
                    year = m.group(1)
                area = self._field(info_text, "制片")
                type_name = self._field(info_text, "类型")
                synopsis_nodes = tree.xpath('//*[@id="link-report-intra"]')
                synopsis = " ".join(synopsis_nodes[0].xpath(".//text()")).strip() if synopsis_nodes else ""
                content = info_text
                if synopsis:
                    content += "\n\n剧情简介：\n" + synopsis
                vod_remarks = ""
                if "content-hide-tips" in html:
                    vod_remarks = "钻石免费" if "钻石免费" in html else "付费"
                detail_url = f"{self.host}/{vid}.html"
                out.append({
                    "vod_id": str(vid),
                    "vod_name": name,
                    "vod_pic": pic,
                    "vod_content": content,
                    "vod_remarks": vod_remarks,
                    "vod_year": year,
                    "vod_area": area,
                    "vod_director": director,
                    "vod_actor": actor,
                    "type_name": type_name,
                    "vod_play_from": "4K狗",
                    "vod_play_url": f"查看详情${detail_url}",
                })
            except Exception:
                continue
        return {"list": out}

    def searchContent(self, key, quick, pg="1"):
        pg = int(pg) if str(pg).isdigit() else 1
        data, _, _, _ = self._api_posts({"search": key, "per_page": "20", "page": str(pg)})
        return {"list": [self._item(p) for p in data], "page": pg}

    def playerContent(self, flag, id, vipFlags):
        url = self._fix(id)
        is_media = bool(re.search(r'\.(?:m3u8|mp4)(?:$|[?#])', url, re.I))
        return {"parse": 0 if is_media else 1, "url": url, "header": json.dumps(self.headers)}


Spider = csp_4kgou
