# -*- coding: utf-8 -*-
"""
蜗牛 I 4K 爬虫
目标站: https://zmi.kdns.fr/
站点类型: MacCMS 网盘资源索引站 (panlian 模板)
适配: TVBox / 影视仓 / py-drpy

URL 结构:
    首页: /
    分类: /vodtype/<tid>/  /vodtype/<tid>/<pg>/
    详情: /voddetail/<id>/
    搜索: /vodsearch/-------------/?wd=<keyword>

注意: 网盘链接需登录才能查看，本爬虫提取公开可见的影视信息和元数据。
播放功能提供 push:// 链接格式供用户自行复制使用。
"""
import sys
import re
import json
import time
import urllib.parse
import requests
from lxml import etree

sys.path.append('..')
try:
    from base.spider import Spider
except ImportError:
    class Spider(object):
        def __init__(self):
            pass
        def fetch(self, url, headers=None, timeout=15, method='GET', data=None):
            import urllib.request, ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            hdrs = dict(headers or {})
            if data:
                if isinstance(data, dict):
                    data = urllib.parse.urlencode(data).encode()
                req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            else:
                req = urllib.request.Request(url, headers=hdrs)
            resp = urllib.request.urlopen(req, context=ctx, timeout=timeout)
            class R:
                def __init__(self, text):
                    self.text = text
                    self.status_code = 200
            return R(resp.read().decode('utf-8', errors='ignore'))
        def html(self, text):
            return etree.HTML(text)
        def getpq(self):
            try:
                from pyquery import PyQuery as pq
                return pq
            except ImportError:
                return None


class Spider(Spider):

    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass
        self.name = "蜗牛I4K"
        self.host = "https://zmi.kdns.fr"
        self.header = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
        }
        self._session = None

    def getName(self):
        return self.name

    def init(self, extend=""):
        return

    def isVideoFormat(self, url):
        if not url:
            return False
        u = url.split('?')[0].split('#')[0].lower()
        return any(u.endswith(x) for x in ['.m3u8', '.mp4', '.flv', '.ts', '.mkv', '.avi', '.mov', '.webm'])

    def manualVideoCheck(self):
        return False

    def destroy(self):
        return

    def localProxy(self, params):
        return [200, "video/MP2T", {}, ""]

    # ==================== 基础工具 ====================

    def _sess(self):
        if self._session is None:
            s = requests.Session()
            s.trust_env = False
            s.headers.update(self.header)
            self._session = s
        return self._session

    def _get(self, url, timeout=15, headers=None, retry=2):
        for i in range(retry + 1):
            try:
                h = dict(self.header)
                if headers:
                    h.update(headers)
                # 每次创建新 session 避免连接池污染
                s = requests.Session()
                s.trust_env = False
                s.headers.update(h)
                s.headers['Accept-Encoding'] = 'identity'
                r = s.get(url, timeout=timeout, verify=False)
                r.encoding = 'utf-8'
                if r.status_code == 200 and r.text and len(r.text) > 50:
                    return r.text
            except Exception:
                pass
            if i < retry:
                time.sleep(0.3 * (i + 1))
        return ''

    def _fix(self, url):
        if not url:
            return ''
        url = url.strip()
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('http'):
            return url
        if url.startswith('/'):
            return self.host + url
        return self.host + '/' + url.lstrip('./')

    def _txt(self, el):
        if el is None:
            return ''
        try:
            text = ''.join(el.itertext()).strip()
            return text
        except:
            return ''

    def _parse_video_card(self, a_el):
        """解析单个视频卡片"""
        try:
            href = a_el.get('href', '')
            m = re.search(r'/voddetail/(\d+)', href)
            if not m:
                return None
            vid = m.group(1)

            name = (a_el.get('title') or '').strip()
            if not name:
                img = a_el.xpath('.//img')
                if img:
                    name = (img[0].get('alt') or '').strip()
            if not name:
                return None

            pic = ''
            img = a_el.xpath('.//img')
            if img:
                pic = img[0].get('data-src') or img[0].get('src') or ''
                pic = self._fix(pic)

            remark = ''
            score_el = a_el.xpath('.//div[contains(@class,"video-score")]')
            if score_el:
                remark = self._txt(score_el[0])
            if not remark:
                ep_el = a_el.xpath('.//div[contains(@class,"video-episode")]')
                if ep_el:
                    remark = self._txt(ep_el[0])

            return {
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": remark
            }
        except Exception:
            return None

    def _parse_list(self, html):
        """从 HTML 中解析视频列表"""
        out, seen = [], set()
        if not html:
            return out
        try:
            root = etree.HTML(html)
            nodes = root.xpath('//a[contains(@href,"/voddetail/") and contains(@class,"video-card")]')
            if not nodes:
                nodes = root.xpath('//a[contains(@href,"/voddetail/")]')
            for node in nodes:
                info = self._parse_video_card(node)
                if info and info["vod_id"] not in seen:
                    seen.add(info["vod_id"])
                    out.append(info)
        except Exception as e:
            print(f"[{self.name}] 解析列表异常: {e}")
        return out

    def _parse_detail_info(self, html, vid):
        """从详情页提取元数据和网盘资源"""
        info = {
            "vod_id": vid,
            "vod_name": "",
            "vod_pic": "",
            "vod_year": "",
            "vod_area": "",
            "vod_class": "",
            "vod_actor": "",
            "vod_director": "",
            "vod_remarks": "",
            "vod_content": "",
            "vod_play_from": "",
            "vod_play_url": ""
        }
        try:
            root = etree.HTML(html)

            # 标题
            titles = root.xpath('//h1')
            if titles:
                info["vod_name"] = self._txt(titles[0])

            # 海报 - 多种方式尝试
            imgs = root.xpath('//img[contains(@src,"/upload/vod/")]')
            if imgs:
                info["vod_pic"] = self._fix(imgs[0].get('src', ''))
            if not info["vod_pic"]:
                backdrop = root.xpath('//div[contains(@class,"detail-backdrop")]')
                if backdrop:
                    style = backdrop[0].get('style', '')
                    m = re.search(r'url\(([^)]+)\)', style)
                    if m:
                        info["vod_pic"] = self._fix(m.group(1))

            # 元数据标签 (p-tag) - 使用正则更可靠
            tag_texts = re.findall(r'p-tag[^>]*>([^<]+)<', html)
            for t in tag_texts:
                t = t.strip()
                if re.match(r'^[\d]+\.[\d]$', t):
                    info["vod_remarks"] = t
                elif t.isdigit() and len(t) == 4:
                    info["vod_year"] = t
                elif t in ('大陆', '台湾', '香港', '日本', '美国', '韩国', '英国', '法国', '德国', '印度', '其他',
                           '电影', '连续剧', '综艺', '动漫', '短剧'):
                    if not info["vod_area"]:
                        info["vod_area"] = t
                elif t and ('4K' in t or '蓝光' in t or 'WEB' in t or 'HDR' in t or '杜比' in t):
                    info["vod_class"] = t

            # 导演和主演 (使用正则更可靠)
            label_matches = re.findall(r'm-label">([^<]+)<', html)
            val_matches = re.findall(r'm-val">([^<]+)<', html)
            for lbl, val in zip(label_matches, val_matches):
                if '导演' in lbl:
                    info["vod_director"] = val.strip()
                elif '主演' in lbl:
                    info["vod_actor"] = val.strip()
                elif '备注' in lbl and not info["vod_class"]:
                    info["vod_class"] = val.strip()

            # 简介 - 从 meta description 或正文提取
            desc_match = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html)
            if desc_match:
                info["vod_content"] = desc_match.group(1).strip()[:500]
            else:
                # 尝试从正文 div 提取
                content_match = re.search(r'pan-lock-desc">([^<]+)<', html)
                if content_match:
                    info["vod_content"] = content_match.group(1).strip()[:500]

            # 网盘资源信息
            pan_items = root.xpath('//div[contains(@class,"pan-link-item")]')
            group_titles = root.xpath('//div[contains(@class,"pan-group-title")]')
            group_names = [self._txt(g) for g in group_titles]

            # 提取每个资源的标题和状态
            play_froms = []
            play_urls = []
            for idx, item in enumerate(pan_items):
                title_el = item.xpath('.//div[contains(@class,"pan-link-title")]')
                meta_el = item.xpath('.//div[contains(@class,"pan-link-meta")]')
                actions_el = item.xpath('.//span[contains(@class,"pan-link-btn")]')
                title = self._txt(title_el[0]) if title_el else ''
                meta = self._txt(meta_el[0]) if meta_el else ''
                action = self._txt(actions_el[0]) if actions_el else ''

                # 跳过纯标题重复项（列表头）
                if not title or title == '网盘资源' or '登录后可见' in title:
                    continue

                is_locked = 'is-locked' in (item.get('class', '') if hasattr(item, 'get') else '')
                # 从HTML class属性判断
                item_class = item.get('class', '') if hasattr(item, 'get') else ''
                is_locked = 'is-locked' in item_class

                # 构建播放项
                if is_locked:
                    display_title = f"[锁定]{title}" if title else f"[锁定]资源{idx+1}"
                    play_froms.append(display_title)
                    play_urls.append(f"push://{self.host}/voddetail/{vid}/")
                else:
                    if title:
                        play_froms.append(title)
                    else:
                        play_froms.append(f"资源{idx+1}")
                    play_urls.append(meta if meta and '******' not in meta else f"push://{self.host}/voddetail/{vid}/")

            if play_froms:
                info["vod_play_from"] = "网盘资源"
                info["vod_play_url"] = f"详情页${self.host}/voddetail/{vid}/"
            else:
                info["vod_play_from"] = "网盘资源"
                info["vod_play_url"] = f"详情页${self.host}/voddetail/{vid}/"

        except Exception as e:
            print(f"[{self.name}] 解析详情异常: {e}")
        return info

    # ==================== 首页 ====================

    def homeContent(self, filter):
        classes = [
            {"type_id": "1", "type_name": "电影"},
            {"type_id": "2", "type_name": "连续剧"},
            {"type_id": "3", "type_name": "综艺"},
            {"type_id": "4", "type_name": "动漫"},
        ]
        result = {
            "class": classes,
            "filters": self.FILTERS,
            "list": self.homeVideoContent().get('list', []),
            "parse": 0,
            "jx": 0
        }
        return result

    def homeVideoContent(self):
        html = self._get(self.host + '/')
        vlist = self._parse_list(html)
        return {"list": vlist[:30], "parse": 0, "jx": 0}

    # ==================== 分类 ====================

    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg) if str(pg).isdigit() and int(pg) > 0 else 1
        try:
            if isinstance(extend, str) and extend:
                try:
                    extend = json.loads(extend)
                except Exception:
                    extend = {}
            if not isinstance(extend, dict):
                extend = {}

            url = f'{self.host}/vodtype/{tid}/{page}/'
            html = self._get(url)
            vlist = self._parse_list(html)

            pc = min(page + 1, 50)
            if not vlist:
                url2 = f'{self.host}/vodtype/{tid}/'
                html2 = self._get(url2)
                vlist = self._parse_list(html2)

            return {
                'list': vlist,
                'page': page,
                'pagecount': pc,
                'limit': len(vlist) or 36,
                'total': pc * 36
            }
        except Exception as e:
            print(f"[{self.name}] 分类获取失败: {e}")
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 36, 'total': 0}

    # ==================== 详情 ====================

    def detailContent(self, ids):
        try:
            if isinstance(ids, (list, tuple)):
                vid = ids[0] if ids else ''
            else:
                vid = ids
            vid = str(vid).strip()
            m = re.search(r'(\d+)', vid)
            if not m:
                return {'list': [], 'parse': 0, 'jx': 0}
            vid = m.group(1)

            html = self._get(f'{self.host}/voddetail/{vid}/')
            if not html:
                return {'list': [], 'parse': 0, 'jx': 0}

            info = self._parse_detail_info(html, vid)
            return {'list': [info], 'parse': 0, 'jx': 0}
        except Exception as e:
            print(f"[{self.name}] 详情解析异常: {e}")
            import traceback
            traceback.print_exc()
            return {'list': [], 'parse': 0, 'jx': 0}

    # ==================== 播放 ====================

    def playerContent(self, flag, id, vipFlags):
        headers = {
            'User-Agent': self.header['User-Agent'],
            'Referer': self.host + '/',
        }

        if id.startswith('push://'):
            return {
                'parse': 0,
                'playUrl': '',
                'url': id,
                'header': json.dumps(headers, ensure_ascii=False)
            }

        if id.startswith('http'):
            return {
                'parse': 0,
                'playUrl': '',
                'url': f"push://{id}",
                'header': json.dumps(headers, ensure_ascii=False)
            }

        # 直接推送详情页URL，让用户在浏览器中登录查看网盘资源
        m = re.search(r'(\d+)', id)
        if m:
            vid = m.group(1)
            return {
                'parse': 0,
                'playUrl': '',
                'url': f"push://{self.host}/voddetail/{vid}/",
                'header': json.dumps(headers, ensure_ascii=False)
            }

        return {
            'parse': 0,
            'playUrl': '',
            'url': f"push://{self.host}",
            'header': json.dumps(headers, ensure_ascii=False)
        }

    # ==================== 搜索 ====================

    def searchContent(self, key, quick, pg='1'):
        page = int(pg) if str(pg).isdigit() and int(pg) > 0 else 1
        try:
            kw = str(key).strip()
            if not kw:
                return {'list': [], 'page': page, 'pagecount': 1, 'limit': 20, 'total': 0}

            url = f'{self.host}/vodsearch/-------------/?wd={urllib.parse.quote(kw)}'
            html = self._get(url)
            vlist = self._parse_list(html)

            return {
                'list': vlist,
                'page': page,
                'pagecount': 1 if not vlist else page + 1,
                'limit': len(vlist) or 20,
                'total': len(vlist)
            }
        except Exception as e:
            print(f"[{self.name}] 搜索异常: {e}")
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 20, 'total': 0}

    # ==================== 筛选器 ====================

    FILTERS = json.loads(r'''{
  "1": [
    {"key": "class", "name": "类型", "value": [
      {"n": "全部", "v": ""}, {"n": "动作", "v": "动作"}, {"n": "喜剧", "v": "喜剧"},
      {"n": "爱情", "v": "爱情"}, {"n": "科幻", "v": "科幻"}, {"n": "恐怖", "v": "恐怖"},
      {"n": "剧情", "v": "剧情"}, {"n": "战争", "v": "战争"}, {"n": "悬疑", "v": "悬疑"},
      {"n": "犯罪", "v": "犯罪"}, {"n": "奇幻", "v": "奇幻"}, {"n": "冒险", "v": "冒险"},
      {"n": "惊悚", "v": "惊悚"}, {"n": "文艺", "v": "文艺"}, {"n": "纪录片", "v": "纪录片"}
    ]},
    {"key": "area", "name": "地区", "value": [
      {"n": "全部", "v": ""}, {"n": "中国大陆", "v": "大陆"}, {"n": "中国香港", "v": "香港"},
      {"n": "中国台湾", "v": "台湾"}, {"n": "美国", "v": "美国"}, {"n": "日本", "v": "日本"},
      {"n": "韩国", "v": "韩国"}, {"n": "英国", "v": "英国"}, {"n": "法国", "v": "法国"},
      {"n": "德国", "v": "德国"}, {"n": "印度", "v": "印度"}, {"n": "泰国", "v": "泰国"},
      {"n": "其他", "v": "其他"}
    ]},
    {"key": "year", "name": "年份", "value": [
      {"n": "全部", "v": ""}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
      {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
      {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"},
      {"n": "2018", "v": "2018"}, {"n": "2017", "v": "2017"}, {"n": "2016", "v": "2016"},
      {"n": "2015", "v": "2015"}, {"n": "2014", "v": "2014"}, {"n": "2013", "v": "2013"},
      {"n": "2012", "v": "2012"}, {"n": "2011", "v": "2011"}, {"n": "2010", "v": "2010"},
      {"n": "90年代", "v": "90年代"}, {"n": "80年代", "v": "80年代"}, {"n": "更早", "v": "更早"}
    ]},
    {"key": "by", "name": "排序", "value": [
      {"n": "最新", "v": "time"}, {"n": "最热", "v": "hits"}, {"n": "评分", "v": "score"}
    ]}
  ],
  "2": [
    {"key": "class", "name": "类型", "value": [
      {"n": "全部", "v": ""}, {"n": "古装", "v": "古装"}, {"n": "悬疑", "v": "悬疑"},
      {"n": "都市", "v": "都市"}, {"n": "喜剧", "v": "喜剧"}, {"n": "战争", "v": "战争"},
      {"n": "剧情", "v": "剧情"}, {"n": "青春", "v": "青春"}, {"n": "历史", "v": "历史"},
      {"n": "网剧", "v": "网剧"}, {"n": "奇幻", "v": "奇幻"}, {"n": "冒险", "v": "冒险"},
      {"n": "犯罪", "v": "犯罪"}, {"n": "家庭", "v": "家庭"}, {"n": "励志", "v": "励志"}
    ]},
    {"key": "area", "name": "地区", "value": [
      {"n": "全部", "v": ""}, {"n": "中国大陆", "v": "大陆"}, {"n": "中国香港", "v": "香港"},
      {"n": "中国台湾", "v": "台湾"}, {"n": "美国", "v": "美国"}, {"n": "日本", "v": "日本"},
      {"n": "韩国", "v": "韩国"}, {"n": "英国", "v": "英国"}, {"n": "法国", "v": "法国"},
      {"n": "其他", "v": "其他"}
    ]},
    {"key": "year", "name": "年份", "value": [
      {"n": "全部", "v": ""}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
      {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
      {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"},
      {"n": "2018", "v": "2018"}, {"n": "2017", "v": "2017"}, {"n": "2016", "v": "2016"},
      {"n": "2015", "v": "2015"}, {"n": "更早", "v": "更早"}
    ]},
    {"key": "by", "name": "排序", "value": [
      {"n": "最新", "v": "time"}, {"n": "最热", "v": "hits"}, {"n": "评分", "v": "score"}
    ]}
  ],
  "3": [
    {"key": "class", "name": "类型", "value": [
      {"n": "全部", "v": ""}, {"n": "真人秀", "v": "真人秀"}, {"n": "脱口秀", "v": "脱口秀"},
      {"n": "喜剧", "v": "喜剧"}, {"n": "音乐", "v": "音乐"}, {"n": "爱情", "v": "爱情"},
      {"n": "家庭", "v": "家庭"}, {"n": "歌舞", "v": "歌舞"}, {"n": "综艺", "v": "综艺"}
    ]},
    {"key": "area", "name": "地区", "value": [
      {"n": "全部", "v": ""}, {"n": "中国大陆", "v": "大陆"}, {"n": "港台", "v": "港台"},
      {"n": "韩国", "v": "韩国"}, {"n": "欧美", "v": "欧美"}, {"n": "其他", "v": "其他"}
    ]},
    {"key": "year", "name": "年份", "value": [
      {"n": "全部", "v": ""}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
      {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
      {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"},
      {"n": "更早", "v": "更早"}
    ]},
    {"key": "by", "name": "排序", "value": [
      {"n": "最新", "v": "time"}, {"n": "最热", "v": "hits"}, {"n": "评分", "v": "score"}
    ]}
  ],
  "4": [
    {"key": "class", "name": "类型", "value": [
      {"n": "全部", "v": ""}, {"n": "少年", "v": "少年"}, {"n": "热血", "v": "热血"},
      {"n": "科幻", "v": "科幻"}, {"n": "冒险", "v": "冒险"}, {"n": "动画", "v": "动画"},
      {"n": "爱情", "v": "爱情"}, {"n": "奇幻", "v": "奇幻"}, {"n": "武侠", "v": "武侠"},
      {"n": "悬疑", "v": "悬疑"}, {"n": "喜剧", "v": "喜剧"}, {"n": "儿童", "v": "儿童"}
    ]},
    {"key": "area", "name": "地区", "value": [
      {"n": "全部", "v": ""}, {"n": "中国大陆", "v": "大陆"}, {"n": "日本", "v": "日本"},
      {"n": "欧美", "v": "欧美"}, {"n": "其他", "v": "其他"}
    ]},
    {"key": "year", "name": "年份", "value": [
      {"n": "全部", "v": ""}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
      {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
      {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"},
      {"n": "更早", "v": "更早"}
    ]},
    {"key": "by", "name": "排序", "value": [
      {"n": "最新", "v": "time"}, {"n": "最热", "v": "hits"}, {"n": "评分", "v": "score"}
    ]}
  ]
}''')
