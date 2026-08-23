# -*- coding: utf-8 -*-
"""
果果短剧 (app.ggduanju.com) Spider
适配: FongMi / TV (T3) / WebHomeTV / PeekPro (T4)
功能特点:
- 一级主分类: 短剧 / 电影 / 电视剧 / 动漫
- 二级子分类 (Filters): 
  * 短剧: 全部/穿越年代/现代言情/反转爽文/女恋总裁/闪婚离婚/都市脑洞/古装仙侠/重生民国
  * 电影: 全部/爱情/喜剧/动作/剧情/科幻/战争/奇幻/音乐/西部/历史
  * 电视剧: 全部/古装/战争/喜剧/家庭/犯罪/动作/奇幻/剧情/历史/商战
  * 动漫: 全部/科幻/热血/推理/搞笑/冒险/动作/少女/益智
- 首页精选: 100% 鲜活无死链封面
- 播放线路: 智能优先级排序 (魔都/暴风/幼稚优先，望望if故障源自动换线)
"""
import sys
import re
import json
import time
from urllib.parse import urljoin, quote, unquote
from html import unescape

try:
    from base.spider import Spider as BaseSpider
except ImportError:
    import requests as rq
    class BaseSpider:
        def fetch(self, url, headers=None, **kw):
            kw.pop('timeout', None)
            r = rq.get(url, headers=headers, timeout=15, **kw)
            ct = r.headers.get('Content-Type', '')
            m = re.search(r'charset=([\w-]+)', ct)
            if m:
                r.encoding = m.group(1).lower()
            else:
                r.encoding = 'utf-8'
            return r

        def post(self, url, data=None, headers=None, **kw):
            kw.pop('timeout', None)
            return rq.post(url, data=data, headers=headers, timeout=15, **kw)


class Spider(BaseSpider):

    def getName(self):
        return "果果短剧"

    def init(self, extend=""):
        self.host = "https://app.ggduanju.com"
        self.UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        self.header = {
            "User-Agent": self.UA,
            "Referer": self.host + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        self.BAD_CDNS = ('10cong.com', 'wwzycdn.10cong.com', 'wwzycdn')
        self.BAD_IMG_DOMAINS = ('wangwangzyimg.com', 'vip.dytt-img.com', 'img.picbf.com')

    def isVideoFormat(self, url):
        return any(ext in url.lower() for ext in ['.m3u8', '.mp4', '.ts', 'm3u8', 'flv'])

    def manualVideoCheck(self):
        return False

    def action(self, action):
        return ""

    def destroy(self):
        pass

    # ------------------ 网络工具 ------------------

    def _fetch(self, url, headers=None, timeout=15):
        """带 429 智能指数退避重试的网络请求"""
        last = None
        for attempt in range(4):
            try:
                rsp = self.fetch(url, headers=headers or self.header, timeout=timeout)
                if rsp is not None and rsp.status_code == 200:
                    return rsp
                if rsp is not None and rsp.status_code == 429 and attempt < 3:
                    time.sleep(1.0 * (2 ** attempt))
                    continue
                last = rsp
            except Exception:
                last = None
                if attempt < 3:
                    time.sleep(0.6)
        return last

    def _clean_text(self, s):
        if not s:
            return ""
        s = re.sub(r'<[^>]+>', '', s)
        s = unescape(s)
        return s.strip()

    def _encode_url(self, url):
        if not url:
            return ""
        if any(ord(c) > 127 for c in url):
            from urllib.parse import urlsplit, urlunsplit
            p = urlsplit(url)
            return urlunsplit((p.scheme, p.netloc, quote(p.path), quote(p.query, safe="=&?"), quote(p.fragment)))
        return url

    # ------------------ 通用卡片解析 ------------------

    def _parse_cards(self, html):
        """精准解析卡片列表, 自动过滤已下线的失效图床"""
        videos = []
        seen = set()

        items = re.findall(r'<(?:li|div)[^>]*class="[^"]*(?:hl-list-item|hl-item-div)[^"]*"[^>]*>(.*?)</(?:li|div)>', html, re.S)
        if not items:
            items = re.findall(r'<li[^>]*>(?:(?!<li).)*?/v[a-z0-9]+\.html.*?</li>', html, re.S)

        for it in items:
            vid_m = re.search(r'href="(/v[a-z0-9]+\.html)"', it)
            if not vid_m:
                continue
            vid = vid_m.group(1)
            if vid in seen:
                continue

            title_m = re.search(r'title="([^"]+)"', it) or re.search(r'alt="([^"]+)"', it) or re.search(r'class="[^"]*hl-item-title[^"]*"[^>]*>([^<]+)<', it)
            title = self._clean_text(title_m.group(1)) if title_m else ""
            if not title or title in ("立即播放", "观看记录", "扫码下载", "友情链接", "APP下载"):
                continue

            pic_m = re.search(r'data-original="([^"]+)"', it) or re.search(r'data-src="([^"]+)"', it) or re.search(r'src="([^"]+\.(?:jpg|png|webp|jpeg)[^"]*)"', it)
            pic = pic_m.group(1).strip() if pic_m else ""
            if pic.startswith('//'):
                pic = 'https:' + pic
            elif pic.startswith('/'):
                pic = self.host + pic

            if any(bad in pic for bad in self.BAD_IMG_DOMAINS):
                pic = ""

            remark_m = re.search(r'class="[^"]*remarks[^"]*"[^>]*>([^<]+)<', it) or re.search(r'<em[^>]*class="[^"]*hl-text-muted[^"]*"[^>]*>([^<]+)<', it)
            remark = self._clean_text(remark_m.group(1)) if remark_m else ""

            seen.add(vid)
            videos.append({
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": remark,
            })
        return videos

    # ------------------ 首页 ------------------

    def homeContent(self, filter):
        # 4 大纯净一级父分类
        classes = [
            {"type_id": "duanju", "type_name": "短剧"},
            {"type_id": "dianying", "type_name": "电影"},
            {"type_id": "dianshiju", "type_name": "电视剧"},
            {"type_id": "dongman", "type_name": "动漫"},
        ]

        # 4 大分类各自完整的二级子分类 (Filters) 筛选配置
        filters = {
            "duanju": [{"key": "theme", "name": "题材", "value": [
                {"n": "全部", "v": ""},
                {"n": "穿越年代", "v": "穿越年代"},
                {"n": "现代言情", "v": "现代言情"},
                {"n": "反转爽文", "v": "反转爽文"},
                {"n": "女恋总裁", "v": "女恋总裁"},
                {"n": "闪婚离婚", "v": "闪婚离婚"},
                {"n": "都市脑洞", "v": "都市脑洞"},
                {"n": "古装仙侠", "v": "古装仙侠"},
                {"n": "重生民国", "v": "重生民国"},
            ]}],
            "dianying": [{"key": "theme", "name": "题材", "value": [
                {"n": "全部", "v": ""},
                {"n": "爱情", "v": "爱情"},
                {"n": "喜剧", "v": "喜剧"},
                {"n": "动作", "v": "动作"},
                {"n": "剧情", "v": "剧情"},
                {"n": "科幻", "v": "科幻"},
                {"n": "战争", "v": "战争"},
                {"n": "奇幻", "v": "奇幻"},
                {"n": "音乐", "v": "音乐"},
                {"n": "西部", "v": "西部"},
                {"n": "历史", "v": "历史"},
            ]}],
            "dianshiju": [{"key": "theme", "name": "题材", "value": [
                {"n": "全部", "v": ""},
                {"n": "古装", "v": "古装"},
                {"n": "战争", "v": "战争"},
                {"n": "喜剧", "v": "喜剧"},
                {"n": "家庭", "v": "家庭"},
                {"n": "犯罪", "v": "犯罪"},
                {"n": "动作", "v": "动作"},
                {"n": "奇幻", "v": "奇幻"},
                {"n": "剧情", "v": "剧情"},
                {"n": "历史", "v": "历史"},
                {"n": "商战", "v": "商战"},
            ]}],
            "dongman": [{"key": "theme", "name": "题材", "value": [
                {"n": "全部", "v": ""},
                {"n": "科幻", "v": "科幻"},
                {"n": "热血", "v": "热血"},
                {"n": "推理", "v": "推理"},
                {"n": "搞笑", "v": "搞笑"},
                {"n": "冒险", "v": "冒险"},
                {"n": "动作", "v": "动作"},
                {"n": "少女", "v": "少女"},
                {"n": "益智", "v": "益智"},
            ]}],
        }

        # 首页精选推荐: 聚合鲜活短剧与影视精选, 确保 100% 鲜活封面
        videos = []
        seen_ids = set()

        # 1. 精选短剧 (鲜活 moduzytuuu 图床)
        rsp_dj = self._fetch(f"{self.host}/lduanju.html")
        if rsp_dj and rsp_dj.status_code == 200:
            for v in self._parse_cards(rsp_dj.text):
                if v['vod_pic'] and v['vod_id'] not in seen_ids:
                    seen_ids.add(v['vod_id'])
                    videos.append(v)

        # 2. 精选影视 (补全电影/电视剧鲜活图床数据)
        rsp_home = self._fetch(self.host)
        if rsp_home and rsp_home.status_code == 200:
            for hv in self._parse_cards(rsp_home.text):
                if hv['vod_id'] not in seen_ids and hv['vod_pic']:
                    seen_ids.add(hv['vod_id'])
                    videos.append(hv)

        # 无论 filter 参数是什么, 始终返回完整 filters 字典供壳端展示子分类筛选栏
        return {
            "class": classes,
            "filters": filters,
            "list": videos,
        }

    # ------------------ 分类列表 ------------------

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        theme = ""
        actual_tid = tid

        if extend and extend.get("theme"):
            theme = extend.get("theme", "").strip()

        if theme:
            # 带题材筛选
            if pg <= 1:
                url = f"{self.host}/Category{actual_tid}-__{quote(theme)}________.html"
            else:
                url = f"{self.host}/Category{actual_tid}-__{quote(theme)}_____{pg}___.html"
        else:
            # 默认分类翻页 (第8位为页码占位符)
            if pg <= 1:
                url = f"{self.host}/l{actual_tid}.html"
            else:
                url = f"{self.host}/Category{actual_tid}-_______{pg}___.html"

        rsp = self._fetch(url)
        if not rsp or rsp.status_code != 200:
            return {"page": pg, "pagecount": pg, "limit": 36, "total": 0, "list": []}

        html = rsp.text
        videos = self._parse_cards(html)

        # 最大页数检测
        max_page = pg
        for p in re.findall(r'/Category[a-z0-9]+-[^_]*_{3,8}(\d+)_*\.html', html):
            if p.isdigit():
                max_page = max(max_page, int(p))

        if max_page <= pg:
            if actual_tid == 'duanju':
                max_page = 1590
            elif actual_tid in ('dianying', 'dianshiju'):
                max_page = 500
            elif actual_tid == 'dongman':
                max_page = 200
            else:
                max_page = max(pg, 100)

        return {
            "page": pg,
            "pagecount": max_page,
            "limit": len(videos) or 36,
            "total": max_page * (len(videos) or 36),
            "list": videos,
        }

    # ------------------ 详情页 ------------------

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        vid = ids[0]
        url = vid if vid.startswith("http") else (self.host + (vid if vid.startswith("/") else "/" + vid))

        rsp = self._fetch(url)
        if not rsp or rsp.status_code != 200:
            return {"list": []}

        html = rsp.text
        vod = self._parse_detail(html, vid)
        return {"list": [vod]}

    def _parse_detail(self, html, vid):
        vod = {
            "vod_id": vid,
            "vod_name": "",
            "vod_pic": "",
            "type_name": "",
            "vod_year": "",
            "vod_area": "",
            "vod_remarks": "",
            "vod_actor": "",
            "vod_director": "",
            "vod_content": "",
            "vod_play_from": "",
            "vod_play_url": "",
        }

        # 标题 (支持 h1/h2 title 或 <title>《片名》)
        tm = re.search(r'<h[1-2][^>]*class="[^"]*(?:hl-dc-title|hl-item-title)[^"]*"[^>]*>([^<]+)</h[1-2]>', html) or              re.search(r'<h[1-2][^>]*>([^<]+)</h[1-2]>', html) or              re.search(r'<title>《([^》]+)》', html)
        if tm:
            vod["vod_name"] = self._clean_text(tm.group(1))

        # 封面
        pm = re.search(r'class="[^"]*hl-item-thumb[^"]*"[^>]*data-original="([^"]+)"', html) or re.search(r'data-original="([^"]+)"', html)
        if pm:
            pic = pm.group(1).strip()
            vod["vod_pic"] = ("https:" + pic) if pic.startswith("//") else ((self.host + pic) if pic.startswith("/") else pic)

        # 简介
        cm = re.search(r'class="[^"]*hl-content-text[^"]*"[^>]*>(.*?)</div>', html, re.S) or              re.search(r'class="[^"]*hl-infos-content[^"]*"[^>]*>(.*?)</div>', html, re.S)
        if cm:
            vod["vod_content"] = self._clean_text(cm.group(1))

        # 元数据
        for m in re.finditer(r'<li[^>]*>\s*(?:<span[^>]*>)?\s*([^：:<]+)[：:]\s*(?:</span>)?\s*(.*?)\s*</li>', html, re.S):
            k = self._clean_text(m.group(1))
            v = self._clean_text(m.group(2)).rstrip('/')
            if not v:
                continue
            if "类型" in k and not vod["type_name"]:
                vod["type_name"] = v
            elif ("主演" in k or "演员" in k) and not vod["vod_actor"]:
                vod["vod_actor"] = v
            elif "导演" in k and not vod["vod_director"]:
                vod["vod_director"] = v
            elif ("年份" in k or "上映" in k or "年代" in k) and not vod["vod_year"]:
                vod["vod_year"] = v
            elif "地区" in k and not vod["vod_area"]:
                vod["vod_area"] = v
            elif "状态" in k and not vod["vod_remarks"]:
                vod["vod_remarks"] = v

        # 解析播放列表
        play_from, play_url = self._parse_playlist(html)
        vod["vod_play_from"] = play_from
        vod["vod_play_url"] = play_url

        return vod

    def _parse_playlist(self, html):
        """
        精准提取播放列表:
        1. 动态映射每一个 flag 与实际线路名 (魔都/暴风/幼稚/望望if)
        2. 动态优先级排序: 无论该片的 flag 序号如何, 一律将【魔都】和【暴风】排在最前, 【望望if】排在最后
        3. 从专用 ul 提取纯净第01集格式, 杜绝立即播放/线路名等杂质
        """
        flag_names = {}
        for m in re.finditer(r'<li[^>]*data-href="(/p[a-z0-9]+-(\d+)_\d+\.html)"[^>]*>(.*?)</li>', html, re.S):
            flag = m.group(2)
            content = m.group(3)
            name_m = re.search(r'<span[^>]*class="[^"]*hl-from-[^"]*"[^>]*>([^<]+)</span>', content) or re.search(r'<span[^>]*>([^<]+)</span>', content)
            name = name_m.group(1).strip() if name_m else f"线路{flag}"
            flag_names[flag] = name

        if not flag_names:
            for m in re.finditer(r'data-href="(/p[a-z0-9]+-(\d+)_\d+\.html)"[^>]*alt="([^"]+)"', html):
                flag_names[m.group(2)] = m.group(3).strip()

        playlists_by_flag = {}

        # 匹配专门的播放列表 ul
        uls = re.findall(r'<ul[^>]*class="[^"]*(?:hl-plays-list|hl-sort-list)[^"]*"[^>]*>(.*?)</ul>', html, re.S)
        for ul_code in uls:
            links = re.findall(r'<a[^>]+href="(/p[a-z0-9]+-(\d+)_(\d+)\.html)"[^>]*>(.*?)</a>', ul_code, re.S)
            if not links:
                continue
            flag = links[0][1]
            ep_list = []
            seen_eps = set()
            for href, f, ep, text in links:
                if href in seen_eps:
                    continue
                seen_eps.add(href)
                clean_text = self._clean_text(text)
                if clean_text in ("立即播放", "播放", "选集", ""):
                    label = f"第{int(ep):02d}集" if ep.isdigit() else f"第{ep}集"
                elif clean_text.isdigit():
                    label = f"第{int(clean_text):02d}集"
                elif clean_text in ("全集", "HD", "BD", "预告", "正片"):
                    label = clean_text
                else:
                    label = clean_text

                ep_list.append(f"{label}${href}")

            if ep_list:
                playlists_by_flag[flag] = ep_list

        # 降级备用
        if not playlists_by_flag:
            for m in re.finditer(r'<a[^>]+href="(/p[a-z0-9]+-(\d+)_(\d+)\.html)"[^>]*>(.*?)</a>', html, re.S):
                href, flag, ep = m.group(1), m.group(2), m.group(3)
                txt = self._clean_text(m.group(0))
                if "立即播放" in txt:
                    continue
                if flag not in playlists_by_flag:
                    playlists_by_flag[flag] = []
                label = f"第{int(ep):02d}集" if ep.isdigit() else f"第{ep}集"
                item = f"{label}${href}"
                if item not in playlists_by_flag[flag]:
                    playlists_by_flag[flag].append(item)

        # 动态智能排序: 魔都(1) > 暴风(2) > 幼稚(3) > 其他(10) > 望望if(99, 易SSL握手失败)
        def line_priority(f):
            nm = flag_names.get(f, f"线路{f}")
            if "魔都" in nm or "modu" in nm.lower():
                return 1
            if "暴风" in nm or "fengbao" in nm.lower():
                return 2
            if "幼稚" in nm or "yzzy" in nm.lower():
                return 3
            if "望望" in nm or "wwzy" in nm.lower():
                return 99
            return 10

        sorted_flags = sorted(playlists_by_flag.keys(), key=line_priority)

        play_from = []
        play_url = []
        for f in sorted_flags:
            from_name = flag_names.get(f, f"线路{f}")
            play_from.append(from_name)
            play_url.append("#".join(playlists_by_flag[f]))

        return "$$$".join(play_from), "$$$".join(play_url)

    # ------------------ 播放解析 ------------------

    def playerContent(self, flag, id, vipFlags):
        url = id if str(id).startswith('http') else self.host + (id if id.startswith('/') else '/' + id)
        rsp = self._fetch(url)
        if not rsp or rsp.status_code != 200:
            alt = self._try_alternative_lines(url)
            if alt:
                return alt
            return {"parse": 1, "playUrl": "", "url": url, "header": self.header}

        html = rsp.text
        data = self._parse_player_aaaa(html)
        if not data or not data.get('url'):
            alt = self._try_alternative_lines(url)
            if alt:
                return alt
            return {"parse": 1, "playUrl": "", "url": url, "header": self.header}

        raw_url = data['url'].replace('\/', '/')
        enc = data.get('encrypt', 0)
        if enc == 2:
            from base64 import b64decode
            try:
                raw_url = unquote(b64decode(raw_url).decode('utf-8'))
            except Exception:
                pass
        elif enc == 1:
            from base64 import b64decode
            try:
                raw_url = b64decode(raw_url).decode('utf-8')
            except Exception:
                pass

        real = self._encode_url(raw_url)

        # 故障源拦截 (10cong / wwzycdn 等在壳端/ExoPlayer无法握手的CDN) -> 智能换线
        if any(bad in real for bad in self.BAD_CDNS):
            alt = self._try_alternative_lines(url)
            if alt:
                return alt

        # 快速探测: master 解包子 m3u8
        ok_url, ok, used_headers = self._probe_m3u8(real)
        final_url = ok_url if ok else real

        return {
            "parse": 0,
            "playUrl": "",
            "url": final_url,
            "header": {"User-Agent": self.UA},
            "format": "application/x-mpegURL" if ".m3u8" in final_url else "",
        }

    def _try_alternative_lines(self, play_page_url):
        """当前线路为故障源或遭遇限流时自动尝试同片其他优质线路 (优先魔都、暴风、幼稚)"""
        try:
            m = re.search(r'/p([a-z0-9]+)-(\d+)_(\d+)\.html', play_page_url)
            if not m:
                return None
            slug, cur_flag, ep = m.group(1), m.group(2), m.group(3)
            for flag in ['1', '3', '4', '2', '5']:
                if str(flag) == str(cur_flag):
                    continue
                alt_url = f"{self.host}/p{slug}-{flag}_{ep}.html"
                rsp = self._fetch(alt_url, timeout=10)
                if not rsp or rsp.status_code != 200:
                    continue
                d = self._parse_player_aaaa(rsp.text)
                if not d or not d.get('url'):
                    continue
                alt_real = self._encode_url(d['url'].replace('\/', '/'))
                if any(bad in alt_real for bad in self.BAD_CDNS):
                    continue
                ok_url, ok, used_headers = self._probe_m3u8(alt_real)
                final = ok_url if ok else alt_real
                return {
                    "parse": 0,
                    "playUrl": "",
                    "url": final,
                    "header": {"User-Agent": self.UA},
                    "format": "application/x-mpegURL" if ".m3u8" in final else "",
                }
        except Exception:
            pass
        return None

    def _parse_player_aaaa(self, html):
        """平衡大括号提取 var player_aaaa = {...};"""
        i = html.find('player_aaaa')
        if i < 0:
            return None
        lb = html.find('{', i)
        if lb < 0:
            return None
        depth = 0
        in_str = False
        esc = False
        quote_char = ''
        end = -1
        for idx in range(lb, len(html)):
            c = html[idx]
            if in_str:
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == quote_char:
                    in_str = False
            else:
                if c in ('"', "'"):
                    in_str = True
                    quote_char = c
                elif c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end = idx + 1
                        break
        if end > lb:
            try:
                return json.loads(html[lb:end])
            except Exception:
                pass
        return None

    def _probe_m3u8(self, url):
        """单次快速探测 master playlist 子 m3u8"""
        headers = {"User-Agent": self.UA}
        try:
            rsp = self._fetch(url, headers=headers, timeout=8)
            if not rsp or rsp.status_code != 200:
                return "", False, {}
            text = rsp.text or ""
            if '#EXTM3U' not in text:
                return "", False, {}
            if '#EXT-X-STREAM-INF' in text:
                lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith('#')]
                if lines:
                    child = self._encode_url(urljoin(url, lines[0]))
                    crsp = self._fetch(child, headers=headers, timeout=8)
                    if crsp and crsp.status_code == 200 and '#EXTM3U' in (crsp.text or ''):
                        return child, True, headers
                return "", False, {}
            return url, True, headers
        except Exception:
            return "", False, {}

    # ------------------ 搜索 ------------------

    def searchContent(self, key, quick, pg="1"):
        pg = int(pg) if str(pg).isdigit() else 1
        if pg <= 1:
            url = f"{self.host}/so-{quote(key)}-.html"
        else:
            url = f"{self.host}/so-{quote(key)}-{pg}.html"

        rsp = self._fetch(url)
        if not rsp or rsp.status_code != 200:
            return {"page": pg, "pagecount": pg, "limit": 20, "total": 0, "list": []}

        videos = self._parse_cards(rsp.text)
        max_page = pg + 1 if len(videos) >= 10 else pg

        return {
            "page": pg,
            "pagecount": max_page,
            "limit": len(videos) or 20,
            "total": max_page * (len(videos) or 20),
            "list": videos,
        }
