# -*- coding: utf-8 -*-
# 看片狂人 (kpkuang.us) —— TVBox Python 爬虫
# 站点类型: vfed 3.1.47 (仿苹果CMS)
# 功能: 首页分类+筛选 / 分类列表+翻页 / 详情+选集 / 播放解析
# 依赖: 无第三方依赖, 仅使用标准库
# 注意: 搜索接口被 Cloudflare 403 拦截, searchContent 返回空列表

import re
import sys
import json
import base64
import urllib.parse
import urllib.request
import ssl

# TVBox 运行时提供 base.spider，本地测试时降级
sys.path.append('..')
try:
    from base.spider import Spider
except Exception:
    class Spider(object):
        def init(self, extend=""): return self
        def getName(self): return ""
        def isVideoFormat(self, url): return False
        def manualVideoCheck(self): return False
        def destroy(self): return ""
        def localProxy(self, param): return [200, "video/MP2T", {}, None]


class Spider(Spider):
    def __init__(self):
        self.site_url = "https://kpkuang.us"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.site_url + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        # SSL context for urllib
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

        # 分类映射: type_id -> type_name
        self.types = {
            "1": "电影",
            "2": "电视剧",
            "3": "综艺",
            "4": "动漫",
            "37": "短剧",
            "29": "纪录片",
            "31": "动画片",
        }
        # 二级筛选选项
        self.filter_years = [
            {"n": "全部", "v": ""},
            {"n": "2026", "v": "/2026"},
            {"n": "2025", "v": "/2025"},
            {"n": "2024", "v": "/2024"},
            {"n": "2023", "v": "/2023"},
            {"n": "2022", "v": "/2022"},
            {"n": "2021", "v": "/2021"},
            {"n": "2020", "v": "/2020"},
            {"n": "2019", "v": "/2019"},
            {"n": "2018", "v": "/2018"},
            {"n": "2017", "v": "/2017"},
            {"n": "2016", "v": "/2016"},
            {"n": "2015", "v": "/2015"},
            {"n": "2010", "v": "/2010"},
            {"n": "2000", "v": "/2000"},
            {"n": "更早", "v": "/2000以前"},
        ]
        self.filter_areas = [
            {"n": "全部", "v": ""},
            {"n": "美国", "v": "/area/美国"},
            {"n": "中国大陆", "v": "/area/中国大陆"},
            {"n": "中国香港", "v": "/area/中国香港"},
            {"n": "中国台湾", "v": "/area/中国台湾"},
            {"n": "日本", "v": "/area/日本"},
            {"n": "韩国", "v": "/area/韩国"},
            {"n": "英国", "v": "/area/英国"},
            {"n": "法国", "v": "/area/法国"},
            {"n": "德国", "v": "/area/德国"},
            {"n": "印度", "v": "/area/印度"},
            {"n": "泰剧", "v": "/area/泰国"},
            {"n": "欧美", "v": "/area/欧美"},
        ]
        self.filter_states = [
            {"n": "全部", "v": ""},
            {"n": "完结", "v": "/state/完结"},
            {"n": "连载中", "v": "/state/连载中"},
        ]
        self.filter_letters = [
            {"n": "全部", "v": ""},
            {"n": "A", "v": "/letter/A"},
            {"n": "B", "v": "/letter/B"},
            {"n": "C", "v": "/letter/C"},
            {"n": "D", "v": "/letter/D"},
            {"n": "E", "v": "/letter/E"},
            {"n": "F", "v": "/letter/F"},
            {"n": "G", "v": "/letter/G"},
            {"n": "H", "v": "/letter/H"},
            {"n": "I", "v": "/letter/I"},
            {"n": "J", "v": "/letter/J"},
            {"n": "K", "v": "/letter/K"},
            {"n": "L", "v": "/letter/L"},
            {"n": "M", "v": "/letter/M"},
            {"n": "N", "v": "/letter/N"},
            {"n": "O", "v": "/letter/O"},
            {"n": "P", "v": "/letter/P"},
            {"n": "Q", "v": "/letter/Q"},
            {"n": "R", "v": "/letter/R"},
            {"n": "S", "v": "/letter/S"},
            {"n": "T", "v": "/letter/T"},
            {"n": "U", "v": "/letter/U"},
            {"n": "V", "v": "/letter/V"},
            {"n": "W", "v": "/letter/W"},
            {"n": "X", "v": "/letter/X"},
            {"n": "Y", "v": "/letter/Y"},
            {"n": "Z", "v": "/letter/Z"},
            {"n": "#", "v": "/letter/#"},
        ]
        self.filter_sorts = [
            {"n": "全部", "v": ""},
            {"n": "时间", "v": "/by/time"},
            {"n": "人气", "v": "/by/hits"},
            {"n": "评分", "v": "/by/score"},
        ]

    def getName(self):
        return "看片狂人"

    def isVideoFormat(self, url):
        return bool(re.search(r'\.(m3u8|mp4|mkv|flv|avi|ts)(\?|$)', str(url), re.I))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        return ''

    def localProxy(self, param):
        return [200, "video/MP2T", {}, None]

    # ==================== HTTP 请求 ====================
    def _fetch(self, url, headers=None, timeout=15):
        """发送 GET 请求，返回 HTML 字符串"""
        try:
            req = urllib.request.Request(url, headers=headers or self.headers)
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as resp:
                return resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"[kpkuang] 请求失败: {url} -> {e}")
            return ""

    def _get(self, path, ext=""):
        """构建完整 URL 并获取 HTML"""
        url = self.site_url + path
        if ext:
            url = url.rstrip('/') + ext
        return self._fetch(url)

    # ==================== homeContent ====================
    def homeContent(self, filter=False):
        """返回分类列表 + 筛选器"""
        try:
            classes = []
            for tid, tname in self.types.items():
                classes.append({"type_id": tid, "type_name": tname})

            result = {"class": classes}

            if filter:
                filters = {}
                for tid in self.types.keys():
                    filters[tid] = [
                        {"key": "area", "name": "地区", "val": self.filter_areas},
                        {"key": "year", "name": "年份", "val": self.filter_years},
                        {"key": "state", "name": "状态", "val": self.filter_states},
                        {"key": "letter", "name": "字母", "val": self.filter_letters},
                        {"key": "by", "name": "排序", "val": self.filter_sorts},
                    ]
                result["filters"] = filters

            return result
        except Exception as e:
            print(f"[kpkuang] homeContent 错误: {e}")
            return {"class": [], "filters": {}}

    # ==================== categoryContent ====================
    def categoryContent(self, tid, pg, filter, extend):
        """分类列表 + 翻页 + 筛选"""
        try:
            items = []
            page = int(pg) if pg else 1
            limit = 30

            # 构建筛选路径
            path_parts = []
            if extend.get("area"):
                path_parts.append(f"area{extend['area']}")
            if extend.get("year"):
                path_parts.append(extend['year'].lstrip('/'))
            if extend.get("state"):
                path_parts.append(f"state{extend['state']}")
            if extend.get("letter"):
                path_parts.append(f"letter{extend['letter']}")
            if extend.get("by"):
                path_parts.append(f"by{extend['by'].lstrip('/')}")

            filter_path = "/".join(path_parts)
            if filter_path:
                filter_path = "/" + filter_path

            # 分页路径
            if page > 1:
                page_path = f"/index_{page}.html"
            else:
                page_path = ""

            html = self._get(f"/vodtype/{tid}{filter_path}", page_path)
            if not html:
                return {"list": [], "page": page, "pagecount": page, "limit": limit, "total": 0}

            # 解析列表项
            li_pattern = r'<li[^>]*class="[^"]*fed-list-item[^"]*"[^>]*>(.*?)</li>'
            li_matches = re.findall(li_pattern, html, re.DOTALL)

            for li in li_matches:
                # 标题
                title_match = re.search(r'class="cinema_title">([^<]+)', li)
                title = title_match.group(1).strip() if title_match else ""
                if not title:
                    continue

                # 详情链接
                href_match = re.search(r'href="(/voddetail/\d+/)"', li)
                vod_id = href_match.group(1) if href_match else ""

                # 图片
                img_match = re.search(r'data-original="(https://[^"]+)"', li)
                vod_pic = img_match.group(1) if img_match else ""

                # 副标题 (年份+地区+评分)
                sub_match = re.search(r'class="fed-list-name[^"]*"[^>]*>([^<]+)', li)
                vod_remarks = ""
                if sub_match:
                    vod_remarks = re.sub(r'<[^>]+>', '', sub_match.group(1)).strip()
                    vod_remarks = vod_remarks.replace('&nbsp;', ' ').replace('&amp;', '&')

                items.append({
                    "vod_id": vod_id,
                    "vod_name": title,
                    "vod_pic": vod_pic,
                    "vod_remarks": vod_remarks,
                })

            # 检测是否存在下一页
            pagecount = page
            next_pattern = rf'/vodtype/{tid}{filter_path}/index_{page + 1}\.html'
            if re.search(next_pattern, html):
                pagecount = page + 1

            total = len(items)
            return {
                "list": items,
                "page": page,
                "pagecount": pagecount,
                "limit": limit,
                "total": total,
            }
        except Exception as e:
            print(f"[kpkuang] categoryContent 错误: {e}")
            return {"list": [], "page": 1, "pagecount": 1, "limit": 30, "total": 0}

    # ==================== detailContent ====================
    def detailContent(self, ids):
        """详情页: 提取影片信息和播放源"""
        try:
            if not ids:
                return {"list": []}

            vid = str(ids[0])
            id_match = re.search(r'/voddetail/(\d+)', vid)
            if not id_match:
                return {"list": []}

            movie_id = id_match.group(1)
            html = self._get(f"/voddetail/{movie_id}/")
            if not html:
                return {"list": []}

            # 标题
            name_match = re.search(r'data-name="([^"]+)"', html)
            vod_name = name_match.group(1) if name_match else ""

            # 海报 (og:image)
            pic_match = re.search(r'meta itemprop="image" property="og:image" content="(https://[^"]+)"', html)
            vod_pic = pic_match.group(1) if pic_match else ""

            # 年份
            vod_year = ""
            title_match = re.search(r'《[^>]+》\((\d{4})\)', html)
            if title_match:
                vod_year = title_match.group(1)
            if not vod_year:
                yr = re.search(r"vod_year\s*=\s*'(\d{4})'", html)
                if yr:
                    vod_year = yr.group(1)

            # 地区 (og:video:area)
            vod_area = ""
            area_meta = re.search(r'contentLocation" property="og:video:area" content="([^"]+)"', html)
            if area_meta:
                vod_area = area_meta.group(1)

            # 导演
            vod_director = ""
            dir_meta = re.search(r'meta itemprop="director"[^>]*content="([^"]+)"', html)
            if dir_meta:
                vod_director = dir_meta.group(1)

            # 演员 (og:video:actor)
            vod_actor = ""
            actor_match = re.search(r'meta itemprop="actor" property="og:video:actor" content="([^"]+)"', html)
            if actor_match:
                vod_actor = actor_match.group(1)

            # 简介
            vod_content = ""
            desc_match = re.search(r'meta name="description" content="([^"]+)"', html)
            if desc_match:
                vod_content = desc_match.group(1)

            # 播放源和剧集
            play_from = []
            play_url_map = {}

            # 解析剧集链接
            ep_pattern = r'<a[^>]*href="(/vodplay/\d+-\d+-\d+\.html)"[^>]*title="([^"]+)"[^>]*>\s*([^<]+)\s*</a>'
            ep_matches = re.findall(ep_pattern, html)
            seen_eps = set()
            for ep_href, ep_title, ep_name in ep_matches:
                parts = ep_href.replace('/vodplay/', '').replace('.html', '').split('-')
                if len(parts) >= 3:
                    line_id = parts[1]
                    ep_num = parts[2]
                    ep_name_clean = ep_name.strip()
                    if ep_name_clean:
                        dedup_key = f"{line_id}_{ep_href}"
                        if dedup_key not in seen_eps:
                            seen_eps.add(dedup_key)
                            if line_id not in play_url_map:
                                play_url_map[line_id] = {"name": f"线路{line_id}", "eps": []}
                            key = f"{ep_num}${ep_href}"
                            play_url_map[line_id]["eps"].append(key)

            # 构建播放源列表
            for line_id in sorted(play_url_map.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                src = play_url_map[line_id]
                play_from.append(src["name"])
                src["eps"] = list(dict.fromkeys(src["eps"]))

            vod_play_from = "$$$".join(play_from) if play_from else "主源"
            vod_play_url = "$$$".join(
                "#".join(play_url_map[lid]["eps"])
                for lid in sorted(play_url_map.keys(), key=lambda x: int(x) if x.isdigit() else 0)
            ) if play_from else ""

            vod = {
                "vod_id": vid,
                "vod_name": vod_name,
                "vod_pic": vod_pic,
                "vod_year": vod_year,
                "vod_area": vod_area,
                "vod_director": vod_director,
                "vod_actor": vod_actor,
                "vod_content": vod_content,
                "vod_play_from": vod_play_from,
                "vod_play_url": vod_play_url,
            }

            return {"list": [vod]}
        except Exception as e:
            print(f"[kpkuang] detailContent 错误: {e}")
            return {"list": []}

    # ==================== playerContent ====================
    def playerContent(self, flag, id, vipFlags=None):
        """播放页解析: 从 iframe data-play 属性提取 Base64 URL, 拼接解析接口"""
        try:
            play_match = re.search(r'/vodplay/(\d+)-(\d+)-(\d+)\.html', id)
            if not play_match:
                play_match = re.search(r'vodplay/(\d+-\d+-\d+)\.html', id)
                if play_match:
                    parts = play_match.group(1).split('-')
                    mid, line, ep = parts[0], parts[1], parts[2]
                else:
                    return {"parse": 1, "url": "", "header": {}}
            else:
                mid = play_match.group(1)
                line = play_match.group(2)
                ep = play_match.group(3)

            html = self._get(f"/vodplay/{mid}-{line}-{ep}.html")
            if not html:
                return {"parse": 1, "url": "", "header": {}}

            # 提取 iframe 的 data-play 属性
            play_attr = re.search(r'data-play="([^"]+)"', html)
            if play_attr:
                b64_data = play_attr.group(1)
                try:
                    stripped = b64_data[3:] if len(b64_data) > 3 else b64_data
                    padded = stripped + '=' * ((4 - len(stripped) % 4) % 4)
                    decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
                    if decoded and decoded.startswith('http'):
                        parse_url = f"https://jx.m3u8.tv/jiexi/?url={urllib.parse.quote(decoded)}"
                        return {
                            "parse": 0,
                            "url": parse_url,
                            "header": {
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                                "Referer": self.site_url + "/",
                            }
                        }
                except Exception as e:
                    print(f"[kpkuang] Base64 解码失败: {e}")

            # fallback: 尝试直接从页面提取 m3u8
            m3u8_match = re.search(r'(https?://[^\s"\'>]+\.m3u8[^\s"\'>]*)', html)
            if m3u8_match:
                return {
                    "parse": 0,
                    "url": m3u8_match.group(1),
                    "header": {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    }
                }

            return {"parse": 1, "url": "", "header": {}}
        except Exception as e:
            print(f"[kpkuang] playerContent 错误: {e}")
            return {"parse": 1, "url": "", "header": {}}

    # ==================== searchContent ====================
    def searchContent(self, key, quick=False, pg="1"):
        """搜索: 返回空列表 (搜索接口被 CF 403 拦截)"""
        return {"list": [], "page": 1, "pagecount": 1, "limit": 20, "total": 0}


    # ==================== homeVideoContent ====================
    def homeVideoContent(self):
        return {"list": []}
