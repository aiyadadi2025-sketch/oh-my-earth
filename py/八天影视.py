# -*- coding: utf-8 -*-
"""
八天影视 Spider — 兼容 FongMi/TV (T3) 与 WebHomeTV / PeekPro (T4)
站点: https://dy.8ttv.cn

特性:
  - 苹果CMS 伪静态页面抓取（官方 JSON API 已关闭，改抓 HTML）
  - 二级分类（类型）走服务端筛选 URL，翻页直接命中，无需客户端过滤 → 快
  - 年份 / 排序 / 类型 全服务端筛选
  - 详情页单次请求解析出全部线路 + 集数（不逐个请求播放页）→ 加载快
  - 播放时再按需请求播放页，提取真实 m3u8/mp4 直链直接播放 → 免解析跳转，起播快
  - 提取失败自动 fallback 交给壳子嗅探
  - 首页推荐带 10 分钟缓存，分类页短超时
  - 全程短超时，SSL 禁验证
"""

import sys
import json
import re
import time

sys.path.append('..')

# ===== 兼容导入 =====
try:
    from base.spider import Spider
except ImportError:
    import requests as _rq
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass

    class Spider:
        """本地测试用餐类：模拟 TVBox 的 fetch（复用连接 + 失败重试）"""
        def __init__(self):
            self._ses = _rq.Session()

        def fetch(self, url, headers=None, **kw):
            timeout = kw.pop('timeout', 12)
            kw.pop('verify', None)  # 防止重复关键字参数
            last = None
            for _ in range(2):
                try:
                    r = self._ses.get(url, headers=headers, timeout=timeout,
                                      verify=False, allow_redirects=True, **kw)
                    if r.status_code == 200 and r.text:
                        return r
                    last = r
                    time.sleep(0.5)
                except Exception as e:
                    last = e
                    time.sleep(0.5)
            if isinstance(last, Exception):
                raise last
            return last

from urllib.parse import quote


# ============================================================
# 常量
# ============================================================

HOST = "https://dy.8ttv.cn"
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Mobile Safari/537.36")

# 一级分类（苹果CMS 伪静态 id）
CLASSES = [
    {"type_name": "电影", "type_id": "1"},
    {"type_name": "电视剧", "type_id": "2"},
    {"type_name": "综艺", "type_id": "3"},
    {"type_name": "动漫", "type_id": "4"},
    {"type_name": "短剧", "type_id": "5"},
    {"type_name": "纪录片", "type_id": "20"},
]

# 各分类的二级分类（服务端支持 class 筛选，体验和速度都最佳）
_CLASS_SUB = {
    "1": ["喜剧", "爱情", "恐怖", "动作", "科幻", "剧情", "战争", "警匪",
          "犯罪", "动画", "奇幻", "武侠", "冒险", "枪战", "悬疑", "惊悚",
          "经典", "青春", "文艺", "微电影", "古装", "历史", "运动", "农村",
          "儿童", "网络电影"],
    "2": ["古装", "战争", "青春偶像", "喜剧", "家庭", "犯罪", "动作",
          "奇幻", "剧情", "历史", "经典", "乡村", "情景", "商战", "网剧",
          "其他"],
    "3": ["选秀", "情感", "访谈", "播报", "旅游", "音乐", "美食", "纪实",
          "曲艺", "生活", "游戏互动", "财经", "求职"],
    "4": ["中国动漫", "港台动漫", "日本动漫", "欧美动漫"],
    "5": ["热门", "都市", "古装", "重生", "逆袭", "虐恋", "仙侠", "言情",
          "穿越", "剧情", "爽文", "其他"],
    "20": [],
}

# 年份筛选（动态生成最近 8 年，避免手写过期）
_YEAR_FILTER = {
    "key": "year", "name": "年份",
    "value": [{"n": "全部", "v": ""}] +
             [{"n": str(y), "v": str(y)} for y in
              range(int(time.strftime("%Y")),
                    int(time.strftime("%Y")) - 7, -1)],
}
_BY_FILTER = {"key": "by", "name": "排序", "value": [
    {"n": "最新", "v": "time"},
    {"n": "最热", "v": "hits"},
    {"n": "评分", "v": "score"},
]}

# 构建 filters（类型子分类 + 年份 + 排序）
FILTERS = {}
for c in CLASSES:
    tid = c["type_id"]
    cls_value = [{"n": "全部", "v": ""}] + \
                [{"n": n, "v": n} for n in _CLASS_SUB.get(tid, [])]
    FILTERS[tid] = [
        {"key": "class", "name": "类型", "value": cls_value},
        _YEAR_FILTER,
        _BY_FILTER,
    ]


# ============================================================
# Spider 主类
# ============================================================

class Spider(Spider):

    def getName(self):
        return "八天影视"

    # ===== 初始化 =====
    def init(self, extend=""):
        self.extend = extend if isinstance(extend, str) else (extend or "")
        self.header = {
            "User-Agent": UA,
            "Referer": HOST + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        self._home_cache = []
        self._home_cache_time = 0

    # ===== 网络工具 =====
    def _rsp_text(self, rsp):
        try:
            return rsp.text
        except Exception:
            try:
                return rsp.content.decode('utf-8', 'ignore')
            except Exception:
                return ""

    def _txt(self, url, referer=None, timeout=8):
        """GET 文本，异常返回空；默认更短超时保证体验"""
        headers = dict(self.header)
        if referer:
            headers["Referer"] = referer
        try:
            rsp = self.fetch(url, headers=headers, timeout=timeout,
                             verify=False)
            return self._rsp_text(rsp)
        except Exception:
            return ""

    def _extract_referer(self, url):
        """从 URL 提取 origin 作为播放 Referer（防盗链）"""
        try:
            if "://" in url:
                scheme = url.split("://")[0]
                host = url.split("://")[1].split("/")[0]
                return scheme + "://" + host + "/"
        except Exception:
            pass
        return HOST + "/"

    # ===== URL 构造 =====
    def _show_url(self, tid, pg=1, cls="", year="", by=""):
        """服务端筛选 URL：class/年份/排序 全部由服务端处理"""
        path = "/index.php/vod/show/"
        parts = []
        if cls:
            parts.append("class/" + quote(cls, safe=""))
        if year:
            parts.append("year/" + quote(year, safe=""))
        if by:
            parts.append("by/" + quote(by, safe=""))
        parts.append("id/" + str(tid))
        if pg and pg > 1:
            parts.append("page/" + str(pg))
        return HOST + path + "/".join(parts) + ".html"

    def _search_url(self, key, pg=1):
        path = "/index.php/vod/search/"
        if pg and pg > 1:
            path += "page/%d/" % pg
        return HOST + path + "wd/" + quote(key, safe="") + ".html"

    def _play_page_url(self, rel_url):
        """详情页里的相对 play URL -> 完整 URL"""
        if rel_url.startswith("http"):
            return rel_url
        return HOST + (rel_url if rel_url.startswith("/") else "/" + rel_url)

    # ===== 卡片解析 =====
    def _parse_list(self, html):
        """从分类/搜索页解析视频列表（兼容 poster / card 两种卡片结构）"""
        vods = []
        # 以卡片起点切块（module-poster-item 或 module-card-item）
        start_re = re.compile(
            r'<(?:a|div)[^>]*?class="[^"]*?module-(?:poster|card)-item'
            r'(?![a-z-])[^"]*"')
        poss = [m.start() for m in start_re.finditer(html)]
        for idx, pos in enumerate(poss):
            end = poss[idx + 1] if idx + 1 < len(poss) else None
            chunk = html[pos:end]

            dm = re.search(r'vod/detail/id/(\d+)\.html', chunk)
            if not dm:
                continue
            vid = dm.group(1)

            # 名称：poster 卡片在 <a title>，card 卡片在 <strong>
            name = ""
            nm = re.search(
                r'title="([^"]*)"[^>]*class="[^"]*?(?:poster|card)-item',
                chunk)
            if not nm:
                nm = re.search(r'<strong>([^<]*)</strong>', chunk)
            if nm:
                name = nm.group(1).strip()
            if not name:
                continue

            pic = ""
            pm = re.search(r'data-original="([^"]+)"', chunk)
            if pm:
                pic = pm.group(1)
            if pic.startswith("//"):
                pic = "https:" + pic

            note = ""
            nm2 = re.search(r'module-item-note">([^<]*)</div>', chunk)
            if nm2:
                note = nm2.group(1).strip()

            vods.append({
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": note or "HD",
            })
        return vods

    # ===== 详情页解析 =====
    def _parse_detail(self, html):
        """从详情页 HTML 提取完整信息（单请求搞定所有线路）"""
        def g(p, s=html, f=0, default=""):
            m = re.search(p, s, f)
            return m.group(1).strip() if m else default

        # 基本信息
        name = g(r'<h1>([^<]*)</h1>', default="")
        if not name:
            return None

        # 年份 / 地区：从 tag-link 区分（year/area 的 href 各不相同）
        year, area = "", ""
        head_i = html.find('module-info-heading')
        head = html[head_i:head_i + 3000] if head_i >= 0 else html
        for t in re.finditer(r'<div class="module-info-tag-link">(.*?)</div>',
                             head, re.S):
            inner = t.group(1)
            href_m = re.search(r'href="([^"]*)"', inner)
            title_m = re.search(r'title="([^"]*)"', inner)
            if not href_m or not title_m:
                continue
            href, ttl = href_m.group(1), title_m.group(1).strip()
            if not year and "/year/" in href and re.match(r'^\d{4}$', ttl):
                year = ttl
            elif not area and "/area/" in href:
                area = ttl

        def strip_html(s):
            return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s or '')).strip(' /')

        director = strip_html(g(
            r'<span class="module-info-item-title">导演：</span>.*?'
            r'<div class="module-info-item-content">(.*?)</div>', f=re.S))
        actor = strip_html(g(
            r'<span class="module-info-item-title">主演：</span>.*?'
            r'<div class="module-info-item-content">(.*?)</div>', f=re.S))
        remarks = g(r'<span class="module-info-item-title">备注：</span>\s*([^<]*)')
        content = g(r'module-info-introduction-content">\s*<p>(.*?)</p>', f=re.S)
        content = re.sub(r'<[^>]+>', '', content or "").strip()

        # 封面
        pic = g(r'module-item-pic"><img[^>]*data-original="([^"]+)"')
        if pic.startswith("//"):
            pic = "https:" + pic

        # 播放源 + 集数
        tabs = [t for t in re.findall(
            r'data-dropdown-value="([^"]*)"', html) if t.strip()]
        blocks = re.split(r'<div class="module-play-list">', html)[1:]
        if not tabs or not blocks:
            return None

        play_from, play_url = [], []
        for tab, block in zip(tabs, blocks):
            eps = re.findall(
                r'<a[^>]*href="([^"]*?play[^"]*)"[^>]*title="[^"]*">'
                r'<span>([^<]*)</span>', block)
            if not eps:
                continue
            ep_list = []
            for rel, ep_name in eps:
                ep_name = (ep_name or "").strip() or "第%d集" % (len(ep_list) + 1)
                ep_list.append("%s$%s" % (ep_name, self._play_page_url(rel)))
            play_from.append(tab.strip())
            play_url.append("#".join(ep_list))

        if not play_from:
            return None

        return {
            "vod_id": g(r'vod/play/id/(\d+)/', f=0, default=""),
            "vod_name": name,
            "vod_pic": pic,
            "type_name": "",
            "vod_year": year,
            "vod_area": area,
            "vod_remarks": remarks or "HD",
            "vod_actor": actor,
            "vod_director": director,
            "vod_content": content[:500],
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }

    # ============================================================
    # 首页
    # ============================================================

    def homeContent(self, filter):
        return {"class": CLASSES, "filters": FILTERS}

    def homeVideoContent(self):
        """首页推荐：电影+电视剧首页数据并集，10 分钟缓存"""
        now = int(time.time())
        if self._home_cache and now - self._home_cache_time < 600:
            return {"list": self._home_cache}

        vods, seen = [], set()
        # 电影分类第 1 页 + 电视剧分类第 1 页，各取一批，合并去重
        for tid in ("1", "2"):
            html = self._txt(self._show_url(tid, 1))
            for v in self._parse_list(html):
                if v["vod_id"] not in seen:
                    seen.add(v["vod_id"])
                    vods.append(v)

        self._home_cache = vods[:72]
        self._home_cache_time = now
        return {"list": self._home_cache}

    # ============================================================
    # 分类列表（服务端筛选）
    # ============================================================

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = int(pg or 1)
            if page < 1:
                page = 1

            ext = {}
            if extend:
                if isinstance(extend, dict):
                    ext = extend
                else:
                    try:
                        ext = json.loads(extend)
                    except Exception:
                        ext = {}

            cls = (ext.get("class") or "").strip()
            year = (ext.get("year") or "").strip()
            by = (ext.get("by") or "").strip()

            url = self._show_url(tid, page, cls=cls, year=year, by=by)
            html = self._txt(url)
            vods = self._parse_list(html)

            # 总页数：从"尾页"链接提取
            pagecount = 1
            pg_m = re.search(
                r'href="[^"]*/page/(\d+)\.html"[^>]*title="尾页"',
                html)
            if pg_m:
                pagecount = int(pg_m.group(1))

            return {
                "list": vods,
                "page": page,
                "pagecount": pagecount,
                "limit": 24,
                "total": 0,
            }
        except Exception:
            return {"page": 1, "pagecount": 1, "limit": 24, "total": 0, "list": []}

    # ============================================================
    # 详情页
    # ============================================================

    def detailContent(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        vid = str(ids[0])

        # 兼容传入完整 URL 的情况
        m = re.search(r'/vod/detail/id/(\d+)\.html', vid)
        if m:
            vid = m.group(1)

        url = HOST + "/index.php/vod/detail/id/" + vid + ".html"
        # 2 次重试（页面偶发抖动）
        html = ""
        for _ in range(2):
            html = self._txt(url)
            if "module-play-list" in html:
                break
            time.sleep(0.3)

        vod = self._parse_detail(html)
        if not vod:
            return {"list": []}
        vod["vod_id"] = vid
        return {"list": [vod]}

    # ============================================================
    # 搜索
    # ============================================================

    def searchContent(self, key, quick, pg="1"):
        try:
            page = int(pg or 1)
            if page < 1:
                page = 1
            key = (key or "").strip()
            if not key:
                return {"list": []}

            html = self._txt(self._search_url(key, page))
            vods = self._parse_list(html)
            return {"list": vods}
        except Exception:
            return {"list": []}

    # ============================================================
    # 播放解析（核心：起播快）
    # ============================================================

    def _media_header(self, url):
        return {
            "User-Agent": UA,
            "Referer": self._extract_referer(url),
        }

    def _player_url(self, play_page_url):
        """请求播放页，提取真实播放地址，失败返回空"""
        html = self._txt(play_page_url, timeout=8)
        if not html:
            return ""
        m = re.search(r'var\s+player_\w+\s*=\s*(\{.*?\});?\s*</script>',
                      html, re.S)
        if not m:
            # 兜底：直接找 m3u8/mp4 直链
            m2 = re.search(r'"(https?://[^"]+\.(?:m3u8|mp4)[^"]*)"', html)
            return re.sub(r'\\/', '/', m2.group(1)) if m2 else ""
        try:
            data = json.loads(m.group(1))
            url = (data.get("url") or "").replace("\\/", "/")
            return url
        except Exception:
            return ""

    def playerContent(self, flag, id, vipFlags):
        if not id:
            return {"parse": 0, "playUrl": "", "url": ""}

        play_page = str(id).replace("\\/", "/")
        if not play_page.startswith("http"):
            play_page = self._play_page_url(play_page)

        # 1. 本身就是直链媒体 → 直接播放
        low = play_page.lower()
        if ".m3u8" in low or ".mp4" in low or ".flv" in low or ".mkv" in low:
            is_m3u8 = ".m3u8" in low
            return {
                "parse": 0, "playUrl": "", "url": play_page,
                "header": self._media_header(play_page),
                "format": "application/x-mpegURL" if is_m3u8 else "",
                "contentType": "application/x-mpegURL" if is_m3u8 else "",
            }

        # 2. 播放页 → 提取真实直链
        real = self._player_url(play_page)
        low_r = real.lower()
        if real and (".m3u8" in low_r or ".mp4" in low_r or ".flv" in low_r):
            is_m3u8 = ".m3u8" in low_r
            return {
                "parse": 0, "playUrl": "", "url": real,
                "header": self._media_header(real),
                "format": "application/x-mpegURL" if is_m3u8 else "",
                "contentType": "application/x-mpegURL" if is_m3u8 else "",
            }

        # 3. 拿到非直链（如加密/iframe 等）→ 交给壳子嗅探
        if real:
            return {
                "parse": 1, "playUrl": "", "url": play_page,
                "header": {"User-Agent": UA, "Referer": HOST + "/"},
            }

        # 4. 解析失败 → 原始播放页交给壳子
        return {
            "parse": 1, "playUrl": "", "url": play_page,
            "header": {"User-Agent": UA, "Referer": HOST + "/"},
        }

    # ===== 本地代理 =====
    def localProxy(self, param):
        return [200, "video/MP2T", b"", ""]

    # ===== 清理 =====
    def destroy(self):
        pass

    def close(self):
        self.destroy()