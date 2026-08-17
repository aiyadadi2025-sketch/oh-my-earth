# -*- coding: utf-8 -*-
import re
import json
import time
from urllib.parse import quote, urljoin
from base.spider import Spider


class Spider(Spider):
    BASE_URL = "https://hddj.tv"
    SITE_NAME = "黄豆短剧"

    CATEGORY_MAP = {
        "魔改短剧": "cbdj",
        "AI漫剧": "ai",
        "真人短剧": "real",
        "二次元": "erciyuan",
        "黑料": "heiliao",
        "短剧": "mgdj"
    }

    def_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": BASE_URL + "/",
        "Connection": "keep-alive",
    }

    play_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": BASE_URL + "/",
        "Accept": "*/*",
    }

    _cookies = ""

    # ---------- 工具函数 ----------
    def _fetch_cookies(self):
        try:
            headers = {"User-Agent": self.def_headers["User-Agent"], "Accept": "text/html", "Referer": self.BASE_URL + "/"}
            resp = self.fetch(self.BASE_URL, headers=headers)
            cookie_list = []
            if hasattr(resp, 'cookies') and resp.cookies:
                try:
                    for cookie in resp.cookies:
                        cookie_list.append(f"{cookie.name}={cookie.value}")
                except:
                    pass
            if not cookie_list:
                if hasattr(resp.headers, "get_all"):
                    raw = resp.headers.get_all("Set-Cookie")
                    for c in raw:
                        cookie_list.append(c.split(";")[0])
                elif "Set-Cookie" in resp.headers:
                    raw = resp.headers["Set-Cookie"]
                    if isinstance(raw, list):
                        for c in raw:
                            cookie_list.append(c.split(";")[0])
                    else:
                        cookie_list.append(raw.split(";")[0])
            self._cookies = "; ".join(cookie_list)
            if self._cookies:
                print(f"[{self.SITE_NAME}] Cookie获取成功")
        except Exception as e:
            print(f"[{self.SITE_NAME}] Cookie获取失败: {e}")
            self._cookies = ""

    def _fetch_html(self, url):
        headers = self.def_headers.copy()
        if self._cookies:
            headers["Cookie"] = self._cookies
        try:
            resp = self.fetch(url, headers=headers)
            return resp.text
        except Exception as e:
            print(f"[{self.SITE_NAME}] 请求失败: {url}, {e}")
            if not self._cookies:
                print(f"[{self.SITE_NAME}] 尝试重新获取Cookie...")
                self._fetch_cookies()
                if self._cookies:
                    headers["Cookie"] = self._cookies
                    try:
                        resp = self.fetch(url, headers=headers)
                        return resp.text
                    except Exception as e2:
                        print(f"[{self.SITE_NAME}] 重试失败: {e2}")
            return ""

    def _clean_html(self, text):
        if not text:
            return ""
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    # ---------- 解析函数 ----------
    def _parse_card(self, card_html, area=""):
        item = {
            "vod_id": "",
            "vod_name": "",
            "vod_pic": "",
            "vod_remarks": "",
            "vod_year": "",
            "vod_area": area or "短剧",
            "vod_actor": "",
            "vod_director": "",
            "vod_type": "",
            "vod_score": "",
        }
        # 图片
        pic = re.search(r'src=["\']([^"\']+?)["\']', card_html)
        if not pic:
            pic = re.search(r'data-src=["\']([^"\']+?)["\']', card_html)
        if pic:
            item["vod_pic"] = urljoin(self.BASE_URL, pic.group(1).strip())
        # 标题
        title = re.search(r'<a[^>]+>(.+?)</a>', card_html)
        if title:
            item["vod_name"] = re.sub(r'<[^>]+>', '', title.group(1)).strip()
        if not item["vod_name"]:
            alt = re.search(r'alt=["\']([^"\']{2,60})["\']', card_html)
            if alt:
                item["vod_name"] = alt.group(1).strip()
        # 链接
        href = re.search(r'href=["\'](/series/details/[^"\']+\.html)["\']', card_html)
        if href:
            item["vod_id"] = href.group(1)
        else:
            href2 = re.search(r'href=["\']([^"\']*details[^"\']+)["\']', card_html)
            if href2:
                item["vod_id"] = href2.group(1)
        # 备注
        badge = re.search(r'<span[^>]*>(热度|集数?|全)?\s*([0-9]+(?:\.[0-9]+)?万?|更新至?\s*[0-9]+)</span>', card_html)
        if badge:
            item["vod_remarks"] = badge.group(2).strip()
        else:
            misc = re.search(r'(热度\s*[0-9.]+万?|更新至?\s*[0-9]+|全[0-9]+集?)', card_html)
            if misc:
                item["vod_remarks"] = misc.group(1).strip()
        return item

    def _parse_video_list(self, html, area=""):
        videos = []
        patterns = [
            r'<article[^>]*class="[^"]*dm-card[^"]*"[^>]*>([\s\S]+?)</article>',
            r'<div[^>]*class="[^"]*dm-card[^"]*"[^>]*>([\s\S]+?)</div>',
            r'<div[^>]*class="[^"]*video-item[^"]*"[^>]*>([\s\S]+?)</div>',
        ]
        cards = []
        for pat in patterns:
            cards = re.findall(pat, html)
            if cards:
                break
        for card in cards:
            it = self._parse_card(card, area)
            if it["vod_id"] and it["vod_name"]:
                videos.append(it)
        return videos

    def _parse_detail(self, html, vod_id):
        result = {
            "vod_id": vod_id,
            "vod_name": "",
            "vod_pic": "",
            "vod_year": "",
            "vod_area": "短剧",
            "vod_actor": "",
            "vod_director": "",
            "vod_type": "",
            "vod_remarks": "",
            "vod_content": "",
            "vod_play_from": "",
            "vod_play_url": "",
        }
        # 标题
        title = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        if not title:
            title = re.search(r'<meta property="og:title"[^>]+content="([^"]+)"', html)
        if title:
            result["vod_name"] = title.group(1).strip().replace(" - 黄豆短剧", "").replace("黄豆短剧", "").strip()
        # 图片
        img = re.search(r'<meta property="og:image"[^>]+content="([^"]+)"', html)
        if img:
            result["vod_pic"] = urljoin(self.BASE_URL, img.group(1))
        # 简介
        desc = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]{10,500})"', html)
        if desc:
            result["vod_content"] = desc.group(1).strip()
        # 类型
        tags = re.findall(r'<a[^>]+href="/video-tag/[^"]+"[^>]*>([^<]+)</a>', html)
        if tags:
            result["vod_type"] = ",".join([t.strip() for t in tags[:5]])
        # 集数备注
        badge = re.search(r'<span[^>]*class="[^"]*dm-ep-badge[^"]*"[^>]*>\s*([^<]+)\s*</span>', html)
        if badge:
            result["vod_remarks"] = badge.group(1).strip()

        # ---- 1. 优先从 JSON-LD 中提取所有 VideoObject ----
        ep_pairs = []
        ld_pattern = r'<script[^>]*type="application/ld\+json"[^>]*>([\s\S]+?)</script>'
        ld_scripts = re.findall(ld_pattern, html)
        for ld in ld_scripts:
            try:
                data = json.loads(ld)
                items = []
                if isinstance(data, dict):
                    if data.get('@type') == 'VideoObject':
                        items = [data]
                    elif '@graph' in data:
                        items = data['@graph']
                elif isinstance(data, list):
                    items = data
                for item in items:
                    if isinstance(item, dict) and item.get('@type') == 'VideoObject':
                        name = item.get('name', '').strip()
                        content_url = item.get('contentUrl', '').strip()
                        if content_url and name:
                            full_url = urljoin(self.BASE_URL, content_url)
                            # 尝试从 URL 或 name 中提取集数
                            ep_num = re.search(r'/(\d+)\.m3u8', full_url)
                            if not ep_num:
                                ep_num = re.search(r'第(\d+)集', name)
                            if ep_num:
                                ep_num = ep_num.group(1)
                            else:
                                ep_num = len(ep_pairs) + 1
                            ep_pairs.append((ep_num, full_url, name))
                if ep_pairs:
                    break
            except Exception as e:
                print(f"[{self.SITE_NAME}] JSON-LD 解析异常: {e}")

        # ---- 2. 如果 JSON-LD 未提取到，回退到按钮提取 ----
        if not ep_pairs:
            ep_pattern = r'<button[^>]*class="[^"]*dm-watch-ep[^"]*"[^>]*data-ep=["\'](\d+)["\'][^>]*data-ep-src=["\']([^"\']+)["\'][^>]*>'
            matches = re.findall(ep_pattern, html)
            if matches:
                for ep_num, src in matches:
                    full_url = urljoin(self.BASE_URL, src)
                    ep_pairs.append((ep_num, full_url, f"第{ep_num}集"))
            else:
                # 3. 最后兜底：从 videoPlayerContainer 取单个
                container = re.search(r'<div[^>]*id="videoPlayerContainer"[^>]*data-video-src=["\']([^"\']+)["\']', html)
                if container:
                    src = container.group(1)
                    full_url = urljoin(self.BASE_URL, src)
                    ep_pairs = [("1", full_url, "正片")]

        # 构造播放 URL
        if ep_pairs:
            # 按集数排序（字符串数字）
            ep_pairs.sort(key=lambda x: int(x[0]))
            parts = []
            seen_urls = set()
            for ep_num, full_url, name in ep_pairs:
                if full_url in seen_urls:
                    continue  # 去重
                seen_urls.add(full_url)
                if not name:
                    name = f"第{ep_num}集"
                parts.append(f"{name}${full_url}")
            result["vod_play_from"] = "黄豆线路"
            result["vod_play_url"] = "#".join(parts)
            print(f"[{self.SITE_NAME}] 提取到 {len(parts)} 集，示例: {parts[0] if parts else ''}")
        else:
            print(f"[{self.SITE_NAME}] 警告：未提取到任何剧集")

        return result

    def _get_real_play_url(self, play_page_url):
        """从播放页获取真实视频地址，支持 iframe 和 JavaScript 变量"""
        try:
            # 若已包含完整视频地址，直接返回
            if re.search(r'\.(m3u8|mp4|flv|ts)(\?|$)', play_page_url, re.I):
                return play_page_url

            html = self._fetch_html(play_page_url)
            if not html:
                print(f"[{self.SITE_NAME}] 播放页获取失败: {play_page_url}")
                return None

            # 1. 提取 iframe，递归获取
            iframe = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html)
            if iframe:
                src = iframe.group(1)
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = self.BASE_URL + src
                print(f"[{self.SITE_NAME}] 发现 iframe，跟进: {src}")
                return self._get_real_play_url(src)

            # 2. 提取 video 标签 src
            video = re.search(r'<video[^>]+src=["\']([^"\']+)["\']', html)
            if video:
                return urljoin(self.BASE_URL, video.group(1))

            # 3. 从 JavaScript 中提取常见变量
            js_patterns = [
                r'player\.url\s*=\s*["\']([^"\']+)["\']',
                r'video\.src\s*=\s*["\']([^"\']+)["\']',
                r'source\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'src\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'url\s*:\s*["\']([^"\']+\.(?:m3u8|mp4)[^"\']*)["\']',
                r'"url"\s*:\s*"([^"]+\.m3u8[^"]*)"',
                r'"src"\s*:\s*"([^"]+\.m3u8[^"]*)"',
            ]
            for pat in js_patterns:
                match = re.search(pat, html, re.I)
                if match:
                    addr = match.group(1)
                    if addr.startswith("//"):
                        addr = "https:" + addr
                    elif addr.startswith("/"):
                        addr = self.BASE_URL + addr
                    print(f"[{self.SITE_NAME}] 从 JS 提取到地址: {addr[:80]}")
                    return addr

            # 4. 直接搜索 m3u8 或 mp4 链接
            direct = re.search(r'(https?://[^\s"\']+\.(?:m3u8|mp4|flv)[^\s"\']*)', html)
            if direct:
                return direct.group(1)

            # 5. data-href / data-url
            data_url = re.search(r'data-(?:href|url)=["\']([^"\']+)["\']', html)
            if data_url:
                addr = data_url.group(1)
                if addr.startswith("//"):
                    addr = "https:" + addr
                elif addr.startswith("/"):
                    addr = self.BASE_URL + addr
                return addr

            print(f"[{self.SITE_NAME}] 未提取到播放地址，将使用播放页嗅探")
            return None
        except Exception as e:
            print(f"[{self.SITE_NAME}] 解析播放地址异常: {e}")
            return None

    # ---------- TVBox 标准接口 ----------
    def init(self, extend=''):
        self._fetch_cookies()
        print(f"[{self.SITE_NAME}] 初始化完成")

    def homeContent(self, filter=False):
        classes = [{"type_id": k, "type_name": k} for k in self.CATEGORY_MAP.keys()]
        return {"class": classes}

    def homeVideoContent(self):
        html = self._fetch_html(self.BASE_URL + "/")
        if not html:
            return {"list": []}
        items = []
        # 轮播区域
        carousel_sec = re.search(r'<section[^>]*class="[^"]*dm-carousel[^"]*"[^>]*>([\s\S]+?)</section>', html, re.I)
        if carousel_sec:
            cards = re.findall(r'<div[^>]*class="[^"]*(?:dm-feature-card|carousel-item)[^"]*"[^>]*>([\s\S]+?)</div>', carousel_sec.group(1))
            for card in cards:
                it = self._parse_card(card)
                if it["vod_id"] and it["vod_name"]:
                    items.append(it)
        # 普通卡片
        normal = self._parse_video_list(html)
        seen = set()
        for it in normal:
            if it["vod_id"] not in seen:
                seen.add(it["vod_id"])
                items.append(it)
        return {"list": items[:30]}

    def categoryContent(self, tid, pg, filter=False, extend=None):
        pg = int(pg) if pg else 1
        result = {"list": [], "page": pg, "pagecount": 1, "limit": 24, "total": 0}
        slug = self.CATEGORY_MAP.get(tid, tid)
        url = f"{self.BASE_URL}/category/{slug}/" + (f"page/{pg}/" if pg > 1 else "")
        html = self._fetch_html(url)
        if not html:
            return result
        area = tid or "短剧"
        result["list"] = self._parse_video_list(html, area)
        page_nums = re.findall(r'/category/[^/]+/page/(\d+)/', html)
        if page_nums:
            result["pagecount"] = max(int(p) for p in page_nums)
            result["total"] = result["pagecount"] * 24
        print(f"[{self.SITE_NAME}] 分类 '{tid}' 第{pg}页: {len(result['list'])} 条")
        return result

    def searchContent(self, key, quick, pg='1'):
        pg = int(pg) if pg else 1
        result = {"list": [], "page": pg, "pagecount": 1, "limit": 24, "total": 0}
        if not key:
            return result
        encoded_key = quote(key)
        url = f"{self.BASE_URL}/search/?q={encoded_key}"
        if pg > 1:
            url += f"&page={pg}"
        html = self._fetch_html(url)
        if not html:
            return result
        result["list"] = self._parse_video_list(html)
        page_nums = re.findall(r'page=(\d+)', html)
        if page_nums:
            result["pagecount"] = max(int(p) for p in page_nums)
            result["total"] = result["pagecount"] * 24
        print(f"[{self.SITE_NAME}] 搜索 '{key}' 第{pg}页: {len(result['list'])} 条")
        return result

    def detailContent(self, ids):
        vod_list = []
        for vod_id in ids:
            if not vod_id.startswith("/"):
                vod_id = "/series/details/" + vod_id
            if not vod_id.startswith("/series/details/"):
                vod_id = "/series/details/" + vod_id.lstrip("/")
            html = self._fetch_html(self.BASE_URL + vod_id)
            if not html:
                continue
            detail = self._parse_detail(html, vod_id)
            if detail["vod_name"]:
                vod_list.append(detail)
                print(f"[{self.SITE_NAME}] 详情: {detail['vod_name']} | 播放 {detail['vod_play_url'][:80] if detail['vod_play_url'] else '无'}")
        return {"list": vod_list}

    def playerContent(self, flag, vid, vip_flags):
        try:
            # 如果 vid 已是视频格式，补全绝对 URL
            if re.search(r'\.(m3u8|mp4|flv|ts)(\?|$)', vid, re.I):
                if vid.startswith('//'):
                    vid = 'https:' + vid
                elif vid.startswith('/'):
                    vid = self.BASE_URL + vid
                elif not vid.startswith('http'):
                    vid = self.BASE_URL + '/' + vid.lstrip('/')
                print(f"[{self.SITE_NAME}] 直接播放地址: {vid}")
                return {"jx": 0, "parse": 0, "url": vid, "header": json.dumps(self.play_headers)}

            # 否则视为播放页，尝试解析
            if not vid.startswith('http'):
                full_url = self.BASE_URL + vid if vid.startswith('/') else self.BASE_URL + '/' + vid
            else:
                full_url = vid

            real_url = self._get_real_play_url(full_url)
            if real_url:
                is_direct = re.search(r'\.(m3u8|mp4|flv|ts)(\?|$)', real_url, re.I) is not None
                parse_flag = 0 if is_direct else 1
                header_str = json.dumps(self.play_headers)
                print(f"[{self.SITE_NAME}] 播放地址: {real_url[:80]}... parse={parse_flag}")
                return {"jx": 0, "parse": parse_flag, "url": real_url, "header": header_str}
            else:
                print(f"[{self.SITE_NAME}] 未解析到直接地址，返回播放页供嗅探: {full_url}")
                return {"jx": 0, "parse": 1, "url": full_url, "header": json.dumps(self.play_headers)}
        except Exception as e:
            print(f"[{self.SITE_NAME}] playerContent 异常: {e}")
            return {"jx": 0, "parse": 0, "url": "", "header": ""}

    def getName(self):
        return self.SITE_NAME

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass

    def localProxy(self, param):
        pass