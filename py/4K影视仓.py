#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4K舱影视 (4kcabin.com) Python Spider
兼容 FongMi/TVBox dr_py

站点结构: MacCMS v10 + mizhiady模板
分类路由: /vodtype/SLUG-PAGE.html (slug=dianying/dianshiju/zongyi/dongman)
详情路由: /voddetail/ID.html
播放路由: /vodplay/ID-SID-EID.html
搜索路由: /vodsearch/KEY-------------.html
播放链路: player_aaaa (encrypt=0) 直链m3u8 (无需解析接口)
卡片结构: <a href="/voddetail/ID.html"><div class="content-card">...<img src="PIC" alt="TITLE" />...<div class="tag text-overflow">REMARKS</div></div></a>
"""

import hashlib, re, json, html as html_mod, sys, os, time

# ==================== FongMi/TV 基类兼容 ====================
sys.path.append('..')
try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    try:
        import requests as _rq
        class _BaseSpider:
            def fetch(self, url, headers=None, timeout=15, **kw):
                kw.pop('timeout', None)
                return _rq.get(url, headers=headers, timeout=15, **kw)
            def post(self, url, json=None, headers=None, timeout=15, **kw):
                return _rq.post(url, json=json, headers=headers, timeout=15, **kw)
    except ImportError:
        _BaseSpider = object

try:
    import requests
    from urllib3 import disable_warnings
    disable_warnings()
except ImportError:
    requests = None

try:
    from curl_cffi import requests as cffi_requests
    _HAS_CFFI = True
except ImportError:
    cffi_requests = None
    _HAS_CFFI = False

try:
    import ssl
    _HAS_SSL = True
except ImportError:
    _HAS_SSL = False


class _MockResponse:
    """http.client 降级用的模拟 Response"""
    def __init__(self, status_code, text, headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.content = text.encode('utf-8', errors='ignore') if text else b''

    def json(self):
        try:
            return json.loads(self.text)
        except Exception:
            return {}


class Spider(_BaseSpider):
    def __init__(self):
        super().__init__()
        self.host = "https://www.4kcabin.com"
        self.header = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 11; KB2000) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Referer": self.host + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self._session = None
        self._cffi_session = None
        self._categories = [
            {"type_id": "dianying", "type_name": "电影"},
            {"type_id": "dianshiju", "type_name": "电视剧"},
            {"type_id": "zongyi", "type_name": "综艺"},
            {"type_id": "dongman", "type_name": "动漫"},
        ]

    def init(self, extend=""):
        if extend and extend.startswith('http'):
            self.host = extend.rstrip('/')
        return ""

    # ==================== HTTP 请求 (多级降级) ====================

    def _get_cffi_session(self):
        if not _HAS_CFFI:
            return None
        if self._cffi_session is None:
            self._cffi_session = cffi_requests.Session(impersonate="chrome")
        return self._cffi_session

    def _get_session(self):
        if self._session is None and requests:
            self._session = requests.Session()
            self._session.headers.update(self.header)
        return self._session

    def _fetch(self, url):
        """HTTP GET 请求 (多级降级)

        优先级: curl_cffi > requests.Session > base.fetch > http.client
        """
        # === 第0级: curl_cffi ===
        cffi = self._get_cffi_session()
        if cffi:
            try:
                r = cffi.get(url, timeout=20, verify=False)
                return r
            except Exception:
                pass

        # === 第1级: requests.Session ===
        session = self._get_session()
        if session:
            try:
                r = session.get(url, timeout=20, verify=False, allow_redirects=True)
                return r
            except Exception:
                pass

        # === 第2级: base.fetch (FongMi Spider) ===
        try:
            r = self.fetch(url, headers=self.header)
            if r is not None:
                return r
        except Exception:
            pass

        # === 第3级: http.client ===
        try:
            return self._http_client_request(url)
        except Exception:
            return _MockResponse(0, '')

    def _http_client_request(self, url):
        """http.client 降级请求"""
        import http.client
        from urllib.parse import urlparse

        parsed = urlparse(url)
        is_https = parsed.scheme == 'https'
        port = parsed.port or (443 if is_https else 80)
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query

        ctx = None
        if is_https and _HAS_SSL:
            ctx = ssl._create_unverified_context()

        h = dict(self.header)

        if is_https:
            conn = http.client.HTTPSConnection(parsed.hostname, port, context=ctx, timeout=20)
        else:
            conn = http.client.HTTPConnection(parsed.hostname, port, timeout=20)
        conn.request('GET', path, headers=h)
        resp = conn.getresponse()
        body_text = resp.read().decode('utf-8', errors='ignore')
        conn.close()
        return _MockResponse(resp.status, body_text)

    @staticmethod
    def _resp_text(r):
        """兼容各种响应类型的text提取"""
        if r is None:
            return ''
        if isinstance(r, str):
            return r
        if isinstance(r, bytes):
            return r.decode('utf-8', errors='ignore')
        txt = getattr(r, 'text', None)
        if txt is not None:
            return txt
        content = getattr(r, 'content', None)
        if content:
            if isinstance(content, bytes):
                return content.decode('utf-8', errors='ignore')
            return str(content)
        try:
            raw = r.read()
            if isinstance(raw, bytes):
                return raw.decode('utf-8', errors='ignore')
            return str(raw)
        except Exception:
            return ''

    # ==================== 卡片解析 ====================

    def _parse_v_items(self, html_text):
        """解析 content-card 卡片 (首页/分类/搜索通用)

        卡片结构:
        <div class="myui-vodbox-content">
          <a href="/voddetail/ID.html">
            <div class="content-card">
              <div class="card-img">
                <div class="myui-vodlist__thumb cover-img">
                  <img src="PIC" alt="TITLE" />
                </div>
                <div class="tag-box">
                  <div class="tag text-overflow">REMARKS</div>
                </div>
              </div>
            </div>
          </a>
        </div>
        """
        videos = []
        # 匹配 <a href="/voddetail/ID.html">...</a> 块 (非贪婪)
        pattern = r'<a\s+href="/voddetail/(\d+)\.html"[^>]*>([\s\S]*?)</a>'
        for m in re.finditer(pattern, html_text):
            vid = m.group(1)
            full_tag = m.group(0)
            block = m.group(2)

            # 跳过 banner/swiper 轮播项
            if 'swiper-lazy' in full_tag:
                continue

            # 提取图片和标题
            img_m = re.search(r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"', block)
            if not img_m:
                continue
            pic = img_m.group(1)
            title = img_m.group(2)

            # 跳过广告/QR码/logo
            if any(skip in title for skip in ['广告', '扫一扫', 'logo', 'LOAD', 'loading']):
                continue
            # 跳过非视频图片 (load.gif 等)
            if 'load.gif' in pic or 'favicon' in pic:
                continue

            # 提取备注/状态
            remarks = ''
            tag_m = re.search(r'class="tag text-overflow">([^<]*)', block)
            if tag_m:
                remarks = tag_m.group(1).strip()

            videos.append({
                "vod_id": vid,
                "vod_name": html_mod.unescape(title),
                "vod_pic": pic,
                "vod_remarks": remarks,
            })

        return videos

    # ==================== 首页 ====================

    def homeContent(self, filter):
        result = {
            "class": [{"type_id": c["type_id"], "type_name": c["type_name"]} for c in self._categories],
            "filters": {},
        }

        try:
            r = self._fetch(self.host)
            html_text = self._resp_text(r)
            if html_text:
                videos = self._parse_v_items(html_text)
                result["list"] = videos
            else:
                result["list"] = []
        except Exception:
            result["list"] = []

        return result

    # ==================== 分类 ====================

    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg) if pg else 1

        # Page 1: /vodtype/SLUG.html
        # Page 2+: /index.php/vodshow/SLUG-----------PAGE.html (vodshow 经 index.php 路由才支持翻页)
        if page <= 1:
            url = self.host + '/vodtype/' + tid + '.html'
        else:
            url = self.host + '/index.php/vodshow/' + tid + '-----------' + str(page) + '.html'

        result = {
            "list": [],
            "page": str(page),
            "pagecount": "1",
            "limit": "24",
            "total": "0",
        }

        try:
            r = self._fetch(url)
            html_text = self._resp_text(r)
            if html_text:
                videos = self._parse_v_items(html_text)
                result["list"] = videos
                result["total"] = str(len(videos))
                # 站点每分类 2 页: page 1 (24条) + page 2 (12条新内容), page 3+ 无新内容
                result["pagecount"] = "2" if videos else "1"
        except Exception:
            pass

        return result

    # ==================== 详情 ====================

    def detailContent(self, ids):
        vod_id = ids[0]
        url = self.host + '/voddetail/' + vod_id + '.html'

        r = self._fetch(url)
        html_text = self._resp_text(r)
        if not html_text:
            return {"list": []}

        # === 标题 ===
        # 优先 h1.title (详情页主标题), 回退 h3
        title = ''
        h1_m = re.search(r'<h1[^>]*class="title"[^>]*>([^<]+)</h1>', html_text)
        if h1_m:
            title = html_mod.unescape(h1_m.group(1).strip())
        if not title:
            h3_m = re.search(r'<h3>([^<]+)</h3>', html_text)
            if h3_m:
                title = html_mod.unescape(h3_m.group(1).strip())

        # === 封面图 ===
        # 封面在 img-box 内的 <img class="lazyload" data-original="URL" />
        # src 是 loading.gif 占位图, 真实 URL 在 data-original 属性
        pic = ''
        pic_m = re.search(r'class="img-box"[\s\S]*?data-original="([^"]*)"', html_text)
        if pic_m:
            pic = pic_m.group(1)
        if not pic:
            # 降级: 从 vod CDN 域名的 src 提取
            pic_m2 = re.search(r'<img[^>]*src="([^"]*(?:yzzyimg|bfzypic|hongniuzyimage|upload/vod)[^"]*)"', html_text)
            if pic_m2:
                pic = pic_m2.group(1)

        # === 简介 ===
        content = ''
        # 方式1: intro div
        intro_m = re.search(r'<div class="intro">([\s\S]*?)</div>\s*</div>\s*</div>', html_text)
        if intro_m:
            content = re.sub(r'<[^>]+>', '', intro_m.group(1)).strip()
        # 方式2: meta description (格式: "TITLE剧情:CONTENT")
        if not content:
            meta_m = re.search(
                r'<meta\s+name="description"\s+content="[^"]*?剧情[:：]([^"]*)"',
                html_text
            )
            if meta_m:
                content = meta_m.group(1).strip()

        # === 元数据 (item-top/item-bottom 对) ===
        year = ''
        area = ''
        type_text = ''
        lang = ''
        score = ''

        # item-top 可能含嵌套 HTML (如 score div), 用 [\s\S]*? 匹配
        items = re.findall(
            r'<div class="item-top">([\s\S]*?)</div>\s*<div class="item-bottom">([^<]*)</div>',
            html_text
        )
        for val_raw, label in items:
            label = label.strip()
            value = re.sub(r'<[^>]+>', '', val_raw).strip()
            if not value:
                continue
            if '评分' in label:
                score = value
            elif '上映' in label or '年份' in label or '时间' in label:
                year = value
            elif '地区' in label:
                area = value
            elif '类型' in label:
                type_text = value
            elif '语言' in label:
                lang = value

        # === 主演/导演 ===
        # detail-box 内: <div class="director text-overflow"><div class="name">主演:</div>VALUE</div>
        actor = ''
        director = ''
        for m in re.finditer(r'<div class="director text-overflow">\s*<div class="name">([^:<]+)[:：]</div>\s*([^<]*)', html_text):
            field = m.group(1).strip()
            value = m.group(2).strip()
            if '主演' in field or '演员' in field:
                actor = value
            elif '导演' in field:
                director = value
        # 降级: info-roles
        if not actor:
            actor_m = re.search(r'class="info-roles">[^：]*[:：]([^<]+)<', html_text)
            if actor_m:
                actor = actor_m.group(1).strip()

        # === 类型 (tags 内的 tag 链接) ===
        if not type_text:
            tags = re.findall(r'<a class="tag"[^>]*>([^<]+)</a>', html_text)
            if tags:
                type_text = ','.join(t.strip() for t in tags)

        # === 备注 ===
        remark = score + '分' if score else ''

        # === 播放源和选集 ===
        play_from = []
        play_url = []

        # 1. 解析播放源标签: href="#playlistN" ... >SOURCE_NAME</a>
        source_map = {}
        for m in re.finditer(r'href="#(playlist\d+)"[^>]*>([^<]+)</a>', html_text):
            sid = re.search(r'playlist(\d+)', m.group(1))
            if sid:
                source_map[sid.group(1)] = m.group(2).strip()

        # 2. 解析所有播放链接并按 sid 分组
        from collections import OrderedDict
        groups = OrderedDict()
        for m in re.finditer(
            r'href="(/vodplay/(\d+)-(\d+)-(\d+)\.html)"[^>]*>([^<]*)</a>',
            html_text
        ):
            ep_url = m.group(1)
            vid = m.group(2)
            sid = m.group(3)
            eid = m.group(4)
            label = m.group(5).strip()

            if sid not in groups:
                groups[sid] = []
            ep_label = label if label else '第' + eid + '集'
            groups[sid].append(ep_label + '$' + ep_url)

        # 3. 构建 play_from / play_url (按 source_map 顺序, 匹配 sid)
        for sid, eps in groups.items():
            name = source_map.get(sid, '线路' + sid)
            play_from.append(name)
            play_url.append('#'.join(eps))

        return {"list": [{
            "vod_id": vod_id,
            "vod_name": title,
            "vod_pic": pic,
            "vod_year": year,
            "vod_area": area,
            "vod_type": type_text,
            "vod_actor": actor,
            "vod_director": '',
            "vod_content": content,
            "vod_remarks": remark,
            "vod_play_from": "$$$".join(play_from) if play_from else "默认线路",
            "vod_play_url": "$$$".join(play_url) if play_url else "",
        }]}

    # ==================== 搜索 ====================

    def searchContent(self, key, quick, pg="1"):
        from urllib.parse import quote

        # MacCMS 标准搜索路由: /vodsearch/KEY-------------.html (13 dashes = 14 segments)
        url = self.host + '/vodsearch/' + quote(key) + '-------------.html'

        try:
            r = self._fetch(url)
            html_text = self._resp_text(r)
            if html_text:
                videos = self._parse_v_items(html_text)
                return {"list": videos}
        except Exception:
            pass

        return {"list": []}

    # ==================== 播放解析 ====================

    def playerContent(self, flag, id, vipFlags):
        # id 可以是 /vodplay/229-1-1.html 或 229-1-1
        play_path = id
        if not play_path.startswith('/vodplay/'):
            play_path = '/vodplay/' + play_path
        if not play_path.endswith('.html'):
            play_path += '.html'

        url = self.host + play_path
        play_url = ""
        parse_flag = 0  # 默认直链 (encrypt=0)

        try:
            r = self._fetch(url)
            html_text = self._resp_text(r)
            if html_text:
                # 提取 player_aaaa
                pa_m = re.search(r'player_aaaa\s*=\s*(\{.*?\})\s*</', html_text, re.DOTALL)
                if pa_m:
                    try:
                        data = json.loads(pa_m.group(1))
                        url_field = data.get('url', '')
                        encrypt = data.get('encrypt', 0)

                        if url_field:
                            if encrypt == 0:
                                # 直链, 无需解密
                                play_url = url_field
                                parse_flag = 0
                            elif encrypt == 2:
                                # Base64 编码
                                try:
                                    import base64
                                    play_url = base64.b64decode(
                                        url_field + '=='
                                    ).decode('utf-8', errors='ignore')
                                    parse_flag = 0
                                except Exception:
                                    play_url = url_field
                                    parse_flag = 1
                            elif encrypt == 1:
                                # URL 编码
                                from urllib.parse import unquote
                                play_url = unquote(url_field)
                                parse_flag = 0
                            else:
                                play_url = url_field
                                parse_flag = 0

                            # 校验 URL 有效性
                            if play_url and not any(
                                ext in play_url.lower()
                                for ext in ['.m3u8', '.mp4', '.flv', '://']
                            ):
                                parse_flag = 1
                    except Exception:
                        pass

                # 降级: 页面中直接搜索 m3u8/mp4
                if not play_url:
                    direct_m = re.search(
                        r'(https?://[^"\'<>\s]+\.(?:m3u8|mp4|flv)[^"\'<>\s]*)',
                        html_text, re.I
                    )
                    if direct_m:
                        play_url = direct_m.group(1)
                        parse_flag = 0

        except Exception:
            pass

        # 构建 header (JSON 字符串)
        header = json.dumps({
            "User-Agent": self.header["User-Agent"],
            "Referer": self.host + "/",
        })

        return {
            "parse": parse_flag,
            "url": play_url,
            "header": header,
        }


# ==================== 模块级接口 ====================
_spider = None

def init(extend=""):
    global _spider
    if _spider is None:
        _spider = Spider()
        _spider.init(extend)

def homeContent(filter):
    return _spider.homeContent(filter) if _spider else {"class": [], "filters": {}, "list": []}

def categoryContent(tid, pg, filter, extend):
    return _spider.categoryContent(tid, pg, filter, extend) if _spider else {"list": [], "page": "1", "pagecount": "1", "limit": "24", "total": "0"}

def detailContent(ids):
    return _spider.detailContent(ids) if _spider else {"list": []}

def searchContent(key, quick, pg="1"):
    return _spider.searchContent(key, quick, pg) if _spider else {"list": []}

def playerContent(flag, id, vipFlags):
    return _spider.playerContent(flag, id, vipFlags) if _spider else {"parse": 1, "url": "", "header": {}}


# ==================== CLI 测试 ====================
if __name__ == '__main__':
    import traceback

    def test_all():
        spider = Spider()
        spider.init()

        # 1. homeContent
        print("\n" + "=" * 60)
        print("[1] homeContent")
        print("=" * 60)
        home = {}
        try:
            home = spider.homeContent(True)
            print(f"  class: {len(home.get('class', []))}")
            for c in home.get('class', []):
                print(f"    {c['type_id']} = {c['type_name']}")
            print(f"  list: {len(home.get('list', []))} items")
            for v in home.get('list', [])[:3]:
                print(f"    {v.get('vod_id')} | {v.get('vod_name')} | {v.get('vod_remarks')} | pic={v.get('vod_pic','')[:50]}")
        except Exception:
            traceback.print_exc()

        # 2. categoryContent (电影 page=1)
        print("\n" + "=" * 60)
        print("[2] categoryContent (dianying page=1)")
        print("=" * 60)
        try:
            cat = spider.categoryContent("dianying", "1", True, "")
            print(f"  page={cat.get('page')}, pagecount={cat.get('pagecount')}, total={cat.get('total')}")
            print(f"  list: {len(cat.get('list', []))} items")
            for v in cat.get('list', [])[:3]:
                print(f"    {v.get('vod_id')} | {v.get('vod_name')} | {v.get('vod_remarks')}")
        except Exception:
            traceback.print_exc()

        # 3. categoryContent page=2
        print("\n" + "=" * 60)
        print("[3] categoryContent (dianying page=2)")
        print("=" * 60)
        try:
            cat2 = spider.categoryContent("dianying", "2", True, "")
            print(f"  page={cat2.get('page')}, pagecount={cat2.get('pagecount')}")
            print(f"  list: {len(cat2.get('list', []))} items")
            for v in cat2.get('list', [])[:3]:
                print(f"    {v.get('vod_id')} | {v.get('vod_name')} | {v.get('vod_remarks')}")
        except Exception:
            traceback.print_exc()

        # 4. categoryContent (动漫)
        print("\n" + "=" * 60)
        print("[4] categoryContent (dongman page=1)")
        print("=" * 60)
        try:
            cat3 = spider.categoryContent("dongman", "1", True, "")
            print(f"  page={cat3.get('page')}, pagecount={cat3.get('pagecount')}, total={cat3.get('total')}")
            print(f"  list: {len(cat3.get('list', []))} items")
            for v in cat3.get('list', [])[:3]:
                print(f"    {v.get('vod_id')} | {v.get('vod_name')} | {v.get('vod_remarks')}")
        except Exception:
            traceback.print_exc()

        # 5. detailContent
        print("\n" + "=" * 60)
        print("[5] detailContent")
        print("=" * 60)
        test_id = "229"
        if home and home.get('list'):
            test_id = home['list'][0].get('vod_id', test_id)
        test_play_id = None
        try:
            detail = spider.detailContent([test_id])
            dlist = detail.get('list', [])
            if dlist:
                d = dlist[0]
                print(f"  vod_id: {d.get('vod_id')}")
                print(f"  vod_name: {d.get('vod_name')}")
                print(f"  vod_pic: {d.get('vod_pic','')[:60]}")
                print(f"  vod_year: {d.get('vod_year')}")
                print(f"  vod_area: {d.get('vod_area')}")
                print(f"  vod_type: {d.get('vod_type')}")
                print(f"  vod_actor: {d.get('vod_actor','')[:60]}")
                print(f"  vod_remarks: {d.get('vod_remarks')}")
                print(f"  vod_content: {d.get('vod_content','')[:80]}")
                pf = d.get('vod_play_from', '')
                pu = d.get('vod_play_url', '')
                lines = pf.split('$$$') if pf else []
                urls = pu.split('$$$') if pu else []
                print(f"  play_from: {lines}")
                for i, u in enumerate(urls):
                    eps = u.split('#')[:5]
                    print(f"    line[{i}]: {len(u.split('#'))} eps, sample: {eps}")
                if urls:
                    first_ep = urls[0].split('#')[0]
                    if '$' in first_ep:
                        test_play_id = first_ep.split('$')[1]
                    else:
                        test_play_id = first_ep
            else:
                print("  NO DETAIL FOUND")
        except Exception:
            traceback.print_exc()

        # 6. searchContent
        print("\n" + "=" * 60)
        print("[6] searchContent (test)")
        print("=" * 60)
        try:
            search = spider.searchContent("test", True)
            print(f"  list: {len(search.get('list', []))} items")
            for v in search.get('list', [])[:3]:
                print(f"    {v.get('vod_id')} | {v.get('vod_name')} | {v.get('vod_remarks')}")
        except Exception:
            traceback.print_exc()

        # 7. playerContent
        print("\n" + "=" * 60)
        print("[7] playerContent")
        print("=" * 60)
        if test_play_id:
            try:
                play = spider.playerContent("test", test_play_id, [])
                print(f"  parse: {play.get('parse')}")
                print(f"  url: {play.get('url','')[:80]}")
                print(f"  header: {play.get('header','')[:80]}")
            except Exception:
                traceback.print_exc()
        else:
            try:
                play = spider.playerContent("test", "/vodplay/229-1-1.html", [])
                print(f"  parse: {play.get('parse')}")
                print(f"  url: {play.get('url','')[:80]}")
                print(f"  header: {play.get('header','')[:80]}")
            except Exception:
                traceback.print_exc()

        print("\n" + "=" * 60)
        print("ALL TESTS DONE")
        print("=" * 60)

    test_all()
