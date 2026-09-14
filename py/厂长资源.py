# -*- coding: utf-8 -*-
# 厂长资源(厂长影视) TVBox 爬虫
# 站点: www.4kcz.com (WordPress + 主题mibt, 服务端渲染HTML)
# 防护: 全站挂 SafeLine 雷池WAF, 纯脚本在部分网络/IP下会被 403 直接拦截
#       (注意: 是"直接拦截block"而非可解的JS挑战; 本机被拦不代表你的TVBox网络被拦)
# 策略: 多域名轮询(4kcz/cz4k/czzy.top/czzy.app), 哪个能连通走哪个;
#       列表/分类/搜索/详情均直接解析服务端HTML, 播放页从iframe的url=参数取真实m3u8.
# 编写标准: 与已验证可用的 枝枝影视/金牌影视/电影天堂 同一套稳妥写法
#   - 自包含、不依赖基类 init
#   - init 兼容 空/None/纯URL/JSON/垃圾输入, 永不抛异常
#   - 所有请求走 requests.Session + 完整浏览器头
#   - 任何一步失败优雅降级, 绝不崩溃

import re
import sys
import json
from urllib.parse import quote, unquote, urlparse, parse_qs

import requests

# 雷池WAF 基于 TLS 指纹拦截 requests; 装了就降级用 curl_cffi 伪装真实浏览器
try:
    from curl_cffi import requests as _cffi_requests
    _HAS_CFFI = True
except Exception:
    _cffi_requests = None
    _HAS_CFFI = False

sys.path.append('..')

# OK影视(drpy) 引擎要求 Spider 继承 base.spider 基类;
# 本地无 base 模块时降级为 object, 保证脚本可独立自测。
try:
    from base.spider import Spider as _BaseSpider
except Exception:
    _BaseSpider = object

# 内容域名池(逐个探测, 取第一个能连通的)
BASES = [
    'https://www.4kcz.com',
    'https://www.cz4k.com',
    'https://czzy.top',
    'https://czzy.app',
]

# TVBox分类(tid -> WP分类slug)
SLUG_MAP = {
    '1': '',                       # 首页/最新
    '2': 'meijutt',                # 美剧
    '3': 'riju',                   # 日剧
    '4': 'hanjutv',                # 韩剧
    '5': 'fanju',                  # 番剧
    '6': 'gcj',                    # 国产剧
    '7': 'haiwaijuqita',           # 海外剧
    '8': 'zuixindianying',         # 最新电影
    '9': 'dongmanjuchangban',      # 剧场版
    '10': 'dbtop250',              # 豆瓣Top250
}

CLASSES = [
    {'type_id': '1', 'type_name': '首页'},
    {'type_id': '2', 'type_name': '美剧'},
    {'type_id': '3', 'type_name': '日剧'},
    {'type_id': '4', 'type_name': '韩剧'},
    {'type_id': '5', 'type_name': '番剧'},
    {'type_id': '6', 'type_name': '国产剧'},
    {'type_id': '7', 'type_name': '海外剧'},
    {'type_id': '8', 'type_name': '最新电影'},
    {'type_id': '9', 'type_name': '剧场版'},
    {'type_id': '10', 'type_name': '豆瓣Top250'},
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}


class Spider(_BaseSpider):
    def __init__(self):
        try:
            _BaseSpider.__init__(self)
        except Exception:
            pass
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._timeout = 15
        self._base = BASES[0]

    def init(self, extend):
        # 兼容任意输入, 永不抛异常
        try:
            if not extend:
                return
            if isinstance(extend, str):
                s = extend.strip()
                if s.startswith('{'):
                    d = json.loads(s)
                    b = d.get('site_url') or d.get('base')
                    if b:
                        self._base = b.rstrip('/')
                elif '://' in s:
                    self._base = s.rstrip('/')
        except Exception:
            pass

    # ---------- 基础请求(多域名轮询) ----------
    @staticmethod
    def _is_blocked(text):
        if not text:
            return True
        return ('safeline' in text) or ('slg-title' in text) or ('SafeLine' in text)

    def _probe(self, base):
        """探测某域名是否可用: 200 且 非雷池页"""
        try:
            text = self._fetch_text(base + '/')
            return bool(text)
        except Exception:
            return False

    def _fetch_text(self, url):
        """抓取 URL 正文; 优先 curl_cffi(绕过雷池TLS指纹拦截), 回退 requests; 拦截/失败返回 None"""
        if _HAS_CFFI:
            try:
                r = _cffi_requests.get(url, impersonate='chrome124', timeout=self._timeout,
                                       headers={'User-Agent': HEADERS['User-Agent'],
                                                'Accept-Language': 'zh-CN,zh;q=0.9'})
                if r.status_code == 200 and not self._is_blocked(r.text or ''):
                    return r.text
            except Exception:
                pass
        # 回退: requests
        try:
            r = self.session.get(url, timeout=self._timeout)
            if r.status_code == 200 and not self._is_blocked(r.text or ''):
                r.encoding = r.apparent_encoding or 'utf-8'
                return r.text
        except Exception:
            pass
        return None

    def _get_path(self, path):
        """按当前base抓相对路径; 失败则轮换域名重试, 返回(text, base)"""
        order = [self._base] + [b for b in BASES if b != self._base]
        for base in order:
            text = self._fetch_text(base + path)
            if text:
                self._base = base
                return text, base
        return None, self._base

    # ---------- HTML解析工具 ----------
    @staticmethod
    def _unescape(s):
        if not s:
            return ''
        return (s.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                .replace('&quot;', '"').replace('&#039;', "'").strip())

    @staticmethod
    def _strip_tags(s):
        if not s:
            return ''
        return re.sub(r'<[^>]+>', '', str(s)).replace('&nbsp;', ' ').strip()

    def _parse_cards(self, html):
        """解析影片卡片(首页swiper / 分类li / 搜索结果 通用), 去重保序"""
        if not html:
            return []
        result = []
        seen = set()

        # 块: 优先 li, 其次 swiper-slide
        blocks = re.findall(r'<li[^>]*>.*?</li>', html, re.S)
        if not blocks:
            blocks = re.findall(r'<div class="swiper-slide"[^>]*>.*?</div>', html, re.S)
        # 再兜底: 直接按 movie 链接切
        if not blocks:
            blocks = re.findall(r'<a[^>]*href="[^"]*/movie/\d+\.html"[^>]*>.*?</a>', html, re.S)

        for blk in blocks:
            mid = re.search(r'/movie/(\d+)\.html', blk)
            if not mid:
                continue
            vid = mid.group(1)
            if vid in seen:
                continue

            # 海报: data-original 优先, 其次 img src
            pic = ''
            m = re.search(r'data-original="([^"]+)"', blk)
            if not m:
                m = re.search(r'<img[^>]*src="([^"]+)"', blk)
            if m:
                pic = self._unescape(m.group(1))

            # 片名: img alt 优先, 其次 dytit 文字
            name = ''
            m = re.search(r'<img[^>]*alt="([^"]+)"', blk)
            if m:
                name = self._unescape(m.group(1))
            if not name:
                m = re.search(r'class="dytit"[^>]*>\s*<a[^>]*>(.*?)</a>', blk, re.S)
                if m:
                    name = self._strip_tags(m.group(1))

            # 备注(集数/更新)
            remarks = ''
            m = re.search(r'class="jidi"[^>]*>\s*<span[^>]*>(.*?)</span>', blk, re.S)
            if m:
                remarks = self._strip_tags(m.group(1))

            # 类型
            tname = ''
            m = re.search(r'class="furk"[^>]*>(.*?)</span>', blk, re.S)
            if m:
                tname = self._strip_tags(m.group(1))

            if not name:
                continue
            seen.add(vid)
            result.append({
                'vod_id': vid,
                'vod_name': name,
                'vod_pic': pic,
                'vod_remarks': remarks,
                'type_name': tname,
            })
        return result

    # ---------- TVBox 接口 ----------
    def homeContent(self, filter=False):
        data = {'class': CLASSES, 'filters': {}, 'list': []}
        text, _ = self._get_path('/')
        data['list'] = self._parse_cards(text)
        return data

    def homeVideoContent(self):
        text, _ = self._get_path('/')
        return {'list': self._parse_cards(text)}

    def categoryContent(self, tid, pg, filter=False, extend={}):
        pg = int(pg) if str(pg).isdigit() else 1
        if isinstance(tid, (list, tuple)):
            tid = str(tid[0])
        slug = SLUG_MAP.get(str(tid), '')
        if not slug:
            path = ('/' if pg == 1 else '/page/%d' % pg)
        else:
            path = ('/%s' % slug) if pg == 1 else ('/%s/page/%d' % (slug, pg))
        text, _ = self._get_path(path)
        lst = self._parse_cards(text)
        return {'page': pg, 'pagecount': 999, 'list': lst}

    def detailContent(self, ids):
        if isinstance(ids, list):
            vid = ids[0] if ids else ''
        else:
            vid = ids
        m = re.search(r'/movie/(\d+)\.html', str(vid))
        if m:
            vid = m.group(1)
        if not str(vid).isdigit():
            return {'list': [{'vod_id': str(vid), 'vod_name': '参数错误'}]}

        text, base = self._get_path('/movie/%s.html' % vid)
        if not text:
            return {'list': [{'vod_id': str(vid), 'vod_name': '请求失败(可能被雷池WAF拦截, 请换网络后重试)'}]}

        # 片名
        name = ''
        m = re.search(r'<h1[^>]*>(.*?)</h1>', text, re.S)
        if m:
            name = self._strip_tags(m.group(1))
        if not name:
            mt = re.search(r'<title>(.*?)</title>', text, re.S)
            if mt:
                name = re.split(r'[|_]', self._strip_tags(mt.group(1)))[0].strip('《》 ')

        # 海报
        pic = ''
        m = re.search(r'class="[^"]*(?:coveimg|poster|pic)[^"]*"[^>]*>\s*(?:<img[^>]*(?:data-original|src)="([^"]+)")?', text, re.S)
        if m and m.group(1):
            pic = self._unescape(m.group(1))

        # 元信息: 类型/地区/年份/导演/编剧/主演/语言/时长
        def pick(label):
            mm = re.search(r'%s[：:]\s*(.*?)(?=(?:类型|地区|年份|上映|导演|编剧|主演|语言|时长|又名)[：:]|电影介绍|</)', text, re.S)
            return self._strip_tags(mm.group(1)) if mm else ''

        ttype = pick('类型')
        area = pick('地区')
        year = pick('年份') or pick('上映')
        director = pick('导演')
        actor = pick('主演')
        lang = pick('语言')
        dur = pick('时长')

        # 简介
        content = ''
        mc = re.search(r'电影介绍[^<]*</[^>]+>(.*?)(?:<div|<p|在线播放)', text, re.S)
        if mc:
            content = self._strip_tags(mc.group(1))[:2000]

        # 播放列表
        eps = []
        for ma in re.finditer(r'<a[^>]*href="([^"]*/v_play/[^"]+\.html)"[^>]*>(.*?)</a>', text, re.S):
            href = ma.group(1)
            ep_name = self._strip_tags(ma.group(2)) or ('第%d集' % (len(eps) + 1))
            # 统一成相对路径
            hm = re.search(r'(/v_play/.+\.html)', href)
            rel = hm.group(1) if hm else href
            eps.append('%s$%s' % (ep_name, rel))

        play_from = '厂长资源' if eps else ''
        play_url = '#'.join(eps)

        return {'list': [{
            'vod_id': str(vid),
            'vod_name': name,
            'vod_pic': pic,
            'type_name': ttype,
            'vod_year': year,
            'vod_area': area,
            'vod_director': director,
            'vod_actor': '' if actor == 'false' else actor,
            'vod_lang': lang,
            'vod_remarks': dur,
            'vod_content': content,
            'vod_play_from': play_from,
            'vod_play_url': play_url,
        }]}

    def searchContent(self, key, quick=False, pg='1'):
        pg = int(pg) if str(pg).isdigit() else 1
        kw = quote(key)
        path = ('/?s=%s' % kw) if pg == 1 else ('/page/%d/?s=%s' % (pg, kw))
        text, _ = self._get_path(path)
        return {'list': self._parse_cards(text)}

    def playerContent(self, flag, id, vipFlags=False):
        if not id:
            return {}
        if isinstance(id, list):
            id = id[0] if id else ''
        # id 为播放页相对路径 /v_play/xxx.html
        m = re.search(r'(/v_play/.+\.html)', str(id))
        if not m:
            # 已经是直链则直接放行
            if str(id).startswith('http'):
                return {'parse': 0, 'url': id, 'header': {'User-Agent': HEADERS['User-Agent']}}
            return {}
        text, _ = self._get_path(m.group(1))
        if not text:
            return {}
        # iframe src
        mi = re.search(r'<iframe[^>]*src="([^"]+)"', text)
        if not mi:
            return {}
        iframe = self._unescape(mi.group(1))
        # 真实m3u8在 url= 参数
        real = ''
        q = urlparse(iframe).query
        qd = parse_qs(q)
        if 'url' in qd:
            real = qd['url'][0]
        else:
            mu = re.search(r'[?&]url=(.+)$', iframe)
            if mu:
                real = unquote(mu.group(1))
        if not real:
            return {}
        return {'parse': 0, 'jx': 0, 'playUrl': '', 'url': real, 'header': {
            'User-Agent': HEADERS['User-Agent'],
            'Referer': iframe,
        }}

    def localProxy(self, param):
        return {}

    def getName(self):
        return '厂长资源'

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass


# ==================== 模块级接口（OK影视 / TVBox 兼容） ====================
_spider = None


def init(extend=""):
    global _spider
    if _spider is None:
        _spider = Spider()
        _spider.init(extend)


def homeContent(filter=False):
    return _spider.homeContent(filter) if _spider else {'class': CLASSES, 'filters': {}, 'list': []}


def homeVideoContent():
    return _spider.homeVideoContent() if _spider else {'list': []}


def categoryContent(tid, pg, filter=False, extend={}):
    return _spider.categoryContent(tid, pg, filter, extend) if _spider else {'list': [], 'page': '1', 'pagecount': '1'}


def detailContent(ids):
    return _spider.detailContent(ids) if _spider else {'list': []}


def searchContent(key, quick=False, pg='1'):
    return _spider.searchContent(key, quick, pg) if _spider else {'list': []}


def playerContent(flag, id, vipFlags=False):
    return _spider.playerContent(flag, id, vipFlags) if _spider else {'parse': 0, 'jx': 0, 'playUrl': '', 'url': '', 'header': {}}


def localProxy(param):
    return _spider.localProxy(param) if _spider else {}


def getName():
    return '厂长资源'


def isVideoFormat(url):
    return False


def manualVideoCheck():
    return False


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sp = Spider()
    sp.init('')
    print('当前可用域名探测:')
    for b in BASES:
        print('  ', b, '->', '可用' if sp._probe(b) else '拦截/不可达')
    h = sp.homeContent()
    print('\nhome 列表:', len(h['list']))
    for v in h['list'][:3]:
        print('  ', v['vod_id'], v['vod_name'], v['vod_pic'][:70])
    if h['list']:
        vid = h['list'][0]['vod_id']
        d = sp.detailContent([vid])['list'][0]
        print('\ndetail:', d['vod_name'], '| 年份', d['vod_year'], '| 演员', d['vod_actor'][:30])
        print('play_url:', d['vod_play_url'][:200])
        if d['vod_play_url']:
            ep0 = d['vod_play_url'].split('#')[0].split('$', 1)[1]
            p = sp.playerContent('', ep0)
            print('player m3u8:', p.get('url'))
