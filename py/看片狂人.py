# -*- coding: utf-8 -*-
# ============================================================
# 看片狂人 (kpkuang.us) TVBox 爬虫插件 v2
# 技术栈: MacCMS / FED 模板 (fed-* 类名)
#
# 【v2 修复点】
#   [分类] 重组分类: 电影(含子类型) / 连续剧(含子类型) / 综艺 / 动漫 / 短剧
#   [筛选] 每个主分类 2 维筛选 (地区+年代), URL: /vodtype/{tid}/area/{area}/year/{year}/page/{n}.html
#   [搜索] 修复 Cloudflare 拦截: 改用 /index.php?m=vod-search-wd-{kw}
#   [海报] 优先 data-original (外部图床), 回退 og:image
#   [播放] 3级策略:
#          1) m3u8/mp4 直链 → parse=0
#          2) VIP平台URL(v.qq.com/youku/bilibili等) → 解析口解析 → parse=0 m3u8
#          3) 自定义播放器(abyssplayer/byseqekaho/ezplayer) → parse=1 嗅探
#   [解析] 内置7个可用解析口, 自动轮换
# ============================================================

import sys
import re
import json
import time
import base64
from urllib.parse import quote, unquote

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except ImportError:
    class BaseSpider(object):
        def fetch(self, url, headers=None, **kw):
            import requests as rq
            return rq.get(url, headers=headers, timeout=15, **kw)

try:
    import requests as _requests
except Exception:
    _requests = None

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

# ==================== 解析口配置 ====================
# 7个实测可用的解析口 (VIP平台URL → m3u8)
PARSERS = [
    {'name': '超特解析-02', 'url': 'http://gsd-jx1.810211.dpdns.org/api/?key=Q2IhcHdde0F5fof&url='},
    {'name': '超特解析',    'url': 'http://zxy-th1.jsszks.dpdns.org:8090/?url='},
    {'name': '超特解析-2',  'url': 'http://zxy-2026ys1.sby01.dpdns.org:8090/?url='},
    {'name': '超解析',      'url': 'http://zxy-th.jsszks.dpdns.org:8080/?url='},
    {'name': '快解',        'url': 'https://zxy-yhjx.sby01.dpdns.org/home/api?type=ys&uid=2733323&key=K4EpxeoffYanJcweCk&url='},
    {'name': '胖大海',      'url': 'https://api.suxun.site/api/gjth?url='},
    {'name': '解析聚合',    'url': 'https://api.jxapi.cc/api/?key=e0df4139f1e7f09375e864cc4937570e&url='},
]

# VIP平台域名 (需要解析口处理)
VIP_DOMAINS = [
    'v.qq.com', 'iqiyi.com', 'youku.com', 'v.youku.com',
    'bilibili.com', 'www.bilibili.com', 'mgtv.com', 'sohu.com',
    'le.com', 'pptv.com', '1905.com', 'm1905.cn',
]

# ==================== 分类配置 ====================
CATEGORIES = [
    {'type_id': '1',  'type_name': '电影'},
    {'type_id': '6',  'type_name': '动作片'},
    {'type_id': '7',  'type_name': '喜剧片'},
    {'type_id': '8',  'type_name': '爱情片'},
    {'type_id': '9',  'type_name': '科幻片'},
    {'type_id': '10', 'type_name': '恐怖片'},
    {'type_id': '11', 'type_name': '剧情片'},
    {'type_id': '12', 'type_name': '战争片'},
    {'type_id': '29', 'type_name': '纪录片'},
    {'type_id': '2',  'type_name': '连续剧'},
    {'type_id': '13', 'type_name': '国产剧'},
    {'type_id': '14', 'type_name': '港剧'},
    {'type_id': '15', 'type_name': '日剧'},
    {'type_id': '16', 'type_name': '欧美剧'},
    {'type_id': '23', 'type_name': '韩剧'},
    {'type_id': '3',  'type_name': '综艺'},
    {'type_id': '4',  'type_name': '动漫'},
    {'type_id': '37', 'type_name': '短剧'},
]

# ==================== 筛选器 ====================
# 地区选项
_AREAS = [
    {'n': '全部', 'v': ''},
    {'n': '中国大陆', 'v': '中国大陆'},
    {'n': '香港', 'v': '香港'},
    {'n': '台湾', 'v': '台湾'},
    {'n': '美国', 'v': '美国'},
    {'n': '日本', 'v': '日本'},
    {'n': '韩国', 'v': '韩国'},
    {'n': '英国', 'v': '英国'},
    {'n': '法国', 'v': '法国'},
    {'n': '德国', 'v': '德国'},
    {'n': '泰国', 'v': '泰国'},
    {'n': '印度', 'v': '印度'},
    {'n': '其他', 'v': '其他'},
]

# 年代选项
_YEARS = [
    {'n': '全部', 'v': ''},
    {'n': '2026', 'v': '2026'},
    {'n': '2025', 'v': '2025'},
    {'n': '2024', 'v': '2024'},
    {'n': '2023', 'v': '2023'},
    {'n': '2022', 'v': '2022'},
    {'n': '2021', 'v': '2021'},
    {'n': '2020', 'v': '2020'},
    {'n': '2019', 'v': '2019'},
    {'n': '2018', 'v': '2018'},
    {'n': '2017', 'v': '2017'},
    {'n': '2016', 'v': '2016'},
    {'n': '2015', 'v': '2015'},
    {'n': '2014', 'v': '2014'},
    {'n': '2013', 'v': '2013'},
    {'n': '2012', 'v': '2012'},
    {'n': '2011', 'v': '2011'},
    {'n': '2010', 'v': '2010'},
    {'n': '2009', 'v': '2009'},
    {'n': '2008', 'v': '2008'},
    {'n': '2007', 'v': '2007'},
    {'n': '2006', 'v': '2006'},
    {'n': '2005', 'v': '2005'},
    {'n': '2000~2004', 'v': '2000~2004'},
    {'n': '1990~1999', 'v': '1990~1999'},
    {'n': '1980~1989', 'v': '1980~1989'},
]

# 排序选项
_SORTS = [
    {'n': '默认', 'v': ''},
    {'n': '最新', 'v': 'time'},
    {'n': '最热', 'v': 'hits'},
    {'n': '评分', 'v': 'score'},
]

# 每个主分类的筛选器 (至少2维)
_FILTER_TEMPLATE = [
    {'key': 'area', 'name': '地区', 'value': _AREAS},
    {'key': 'year', 'name': '年代', 'value': _YEARS},
    {'key': 'by',   'name': '排序', 'value': _SORTS},
]

# 所有分类共用同一套筛选器
FILTERS = {}
for c in CATEGORIES:
    FILTERS[c['type_id']] = _FILTER_TEMPLATE


class Spider(BaseSpider):
    def __init__(self):
        super(Spider, self).__init__()
        self.host = 'https://kpkuang.us'
        self.headers = {'User-Agent': UA, 'Referer': self.host + '/'}
        self._seen = {}
        self._parser_idx = 0

    def init(self, extend=''):
        try:
            if extend and extend.startswith('http'):
                self.host = extend.rstrip('/')
                self.headers = {'User-Agent': UA, 'Referer': self.host + '/'}
        except Exception:
            pass

    def getName(self):
        return "看片狂人"

    def isVideoFormat(self, url):
        return bool(re.search(r'\.(m3u8|mp4|flv|ts|mkv)(\?|$)', str(url), re.I))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def localProxy(self, param):
        return [200, "video/MP2T", "", ""]

    # ---------------- 网络层 ----------------
    def _fetch(self, url, referer=None, timeout=15):
        """TVBox 环境用 self.fetch, 本地用 requests; 3 次重试"""
        h = dict(self.headers)
        if referer:
            h['Referer'] = referer
        txt = ''
        for attempt in range(3):
            if hasattr(self, 'fetch') and self.fetch is not None:
                try:
                    r = self.fetch(url, headers=h, timeout=timeout)
                    txt = (r.text if hasattr(r, 'text') else str(r)) or ''
                except Exception:
                    txt = ''
            if not txt and _requests is not None:
                try:
                    r = _requests.get(url, headers=h, timeout=timeout, verify=False)
                    txt = r.text or ''
                except Exception:
                    txt = ''
            if txt:
                break
            time.sleep(0.6 * (attempt + 1))
        return txt

    # ---------------- 工具 ----------------
    @staticmethod
    def _clean(s):
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s or '')).strip()

    @staticmethod
    def _fmt_ep(raw, fallback=None):
        """集数名规范: '第1集/第01集' → '01'; 画质标签保留原文"""
        t = str(raw or '').strip()
        t = re.sub(r'更新至|已完结|全集|高清|超清|HD|BD|国语|粤语', '', t).strip()
        m = re.search(r'第\s*(\d+)\s*集', t)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 9999:
                return '%02d' % n
        return t or (fallback or '')

    @staticmethod
    def _fix_poster(p):
        if not p:
            return ''
        if p.startswith('//'):
            p = 'https:' + p
        return p

    # ---------------- 列表卡片解析 ----------------
    def _parse_cards(self, txt):
        """解析 fed-list-item 卡片列表"""
        vods, seen = [], set()
        for m in re.finditer(
                r'<li[^>]*class="[^"]*fed-list-item[^"]*"[^>]*>([\s\S]*?)</li>', txt or '', re.I):
            block = m.group(1)
            # 优先匹配带 data-original 的 (外部图床海报)
            dm = re.search(r'href="/voddetail/(\d+)/"[^>]*title="([^"]*)"[^>]*data-original="([^"]*)"', block)
            if not dm:
                dm = re.search(r'href="/voddetail/(\d+)/"[^>]*data-original="([^"]*)"[^>]*title="([^"]*)"', block)
            if not dm:
                dm = re.search(r'href="/voddetail/(\d+)/"[^>]*title="([^"]*)"', block)
            if not dm:
                continue
            vid = dm.group(1)
            if vid in seen:
                continue
            seen.add(vid)
            name = self._clean(dm.group(2))
            pic = ''
            if len(dm.groups()) >= 3 and dm.group(3):
                pic = self._fix_poster(dm.group(3))
            rm = re.search(r'class="[^"]*fed-list-remarks[^"]*"[^>]*>([\s\S]*?)<', block)
            vods.append({
                'vod_id': vid,
                'vod_name': name,
                'vod_pic': pic,
                'vod_remarks': self._clean(rm.group(1)) if rm else '',
            })
        return vods

    def _main_list(self, txt):
        """
        提取主列表容器并解析
        门户页(tid=1/2)有多个 overflow:unset 区块, 全部合并
        子分类页(tid=6/13等)只有一个主列表
        """
        vods, seen = [], set()
        # 匹配所有 overflow:unset 的主列表区块
        for m in re.finditer(
                r'<ul class="fed-list-info fed-part-rows" style="overflow: unset">([\s\S]*?)</ul>',
                txt or ''):
            for v in self._parse_cards(m.group(1)):
                if v['vod_id'] not in seen:
                    seen.add(v['vod_id'])
                    vods.append(v)
        if vods:
            return vods
        # 回退: 解析全页 fed-list-item
        return self._parse_cards(txt or '')

    def _pagecount(self, txt):
        """从分页链接提取总页数"""
        pages = re.findall(r'/vodtype/\d+/[^"]*?page/(\d+)', txt or '')
        if not pages:
            pages = re.findall(r'page/(\d+)', txt or '')
        maxp = 0
        for p in pages:
            try:
                if int(p) > maxp:
                    maxp = int(p)
            except Exception:
                pass
        return max(maxp, 1)

    # ---------------- 首页 / 分类 ----------------
    def homeContent(self, filter):
        result = {'class': CATEGORIES}
        if filter:
            result['filters'] = FILTERS
        try:
            result['list'] = self.homeVideoContent()['list']
        except Exception:
            result['list'] = []
        return result

    def homeVideoContent(self):
        txt = self._fetch(self.host + '/', self.host + '/')
        vods = self._parse_cards(txt)
        if not vods:
            vods = self._main_list(self._fetch(self.host + '/vodtype/1/', self.host + '/'))
        return {'list': vods[:30]}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg) if pg else 1
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1

        # 构建筛选URL: /vodtype/{tid}/area/{area}/by/{by}/year/{year}/page/{n}.html
        parts = [self.host, 'vodtype', str(tid)]
        if extend and isinstance(extend, dict):
            area = extend.get('area', '').strip()
            by = extend.get('by', '').strip()
            year = extend.get('year', '').strip()
            if area:
                parts.append('area')
                parts.append(quote(area))
            if by:
                parts.append('by')
                parts.append(quote(by))
            if year:
                parts.append('year')
                parts.append(quote(year))
        else:
            area = by = year = ''

        base = '/'.join(parts) + '/'
        if pg > 1:
            url = base + 'page/%d.html' % pg
        else:
            url = base

        txt = self._fetch(url, self.host + '/')
        vods = self._main_list(txt)

        # 翻页去重
        if pg > 1:
            key = 'cat_%s_%s_%s_%s' % (tid, area if extend else '', by if extend else '', year if extend else '')
            seen = self._seen.setdefault(key, set())
            fresh = [v for v in vods if v['vod_id'] not in seen]
            if fresh:
                for v in fresh:
                    seen.add(v['vod_id'])
                vods = fresh
            else:
                vods = []

        pagecount = self._pagecount(txt)
        return {
            'list': vods,
            'page': pg,
            'pagecount': pagecount,
            'limit': len(vods),
            'total': pagecount * 12,
        }

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        vid = str(ids[0])
        m = re.search(r'/voddetail/(\d+)', vid)
        if m:
            vid = m.group(1)
        txt = self._fetch(self.host + '/voddetail/%s/' % vid, self.host + '/')
        if not txt:
            return {'list': []}

        v = {
            'vod_id': vid, 'vod_name': '', 'vod_pic': '', 'type_name': '',
            'vod_year': '', 'vod_area': '', 'vod_actor': '', 'vod_director': '',
            'vod_content': '', 'vod_play_from': '', 'vod_play_url': '',
            'vod_remarks': '',
        }

        # 标题
        nm = re.search(r'<h1[^>]*>([\s\S]*?)</h1>', txt)
        if nm:
            v['vod_name'] = self._clean(nm.group(1))
        else:
            tm = re.search(r'<title>([^<]*?)(_看片狂人| - )', txt)
            if tm:
                v['vod_name'] = self._clean(tm.group(1))

        # 海报: 优先 data-original (外部图床), 回退 og:image
        # data-original 在详情页的 fed-deta-images 容器中
        pm = re.search(r'class="[^"]*fed-deta-images[^"]*"[^>]*>[\s\S]*?<img[^>]*data-original="([^"]*)"', txt)
        if not pm:
            pm = re.search(r'<img[^>]*data-original="(https?://[^"]*(?:tmdb|media-amazon|doubanio|flixfiend)[^"]*)"', txt)
        if not pm:
            pm = re.search(r'<img[^>]*data-original="([^"]*)"[^>]*', txt)
        if not pm:
            pm = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', txt)
        if pm:
            v['vod_pic'] = self._fix_poster(pm.group(1))

        # 字段
        fields = {
            'vod_director': '导演', 'vod_actor': '主演', 'vod_area': '地区',
            'vod_year': '年份',
        }
        for k, kw in fields.items():
            fm = re.search(r'<span class="fed-text-muted">%s[：:]\s*</span>([\s\S]{0,3000}?)(?=<li|</li>)' % kw, txt)
            if fm:
                t = self._clean(fm.group(1))
                t = t.replace('&nbsp;', ' ').replace('/', ' / ')
                v[k] = t[:500]

        # 类型
        tm2 = re.search(r'property="og:video:class" content="([^"]*)"', txt)
        if tm2:
            v['type_name'] = self._clean(tm2.group(1))

        # 简介
        cm = re.search(r'<span class="fed-text-muted">简介[：:]\s*</span>([\s\S]{0,800}?)</', txt)
        if cm:
            v['vod_content'] = self._clean(cm.group(1))

        # 线路与选集
        froms, urls = [], []
        blocks = re.findall(r'来自\s*<span class="uk-label">([^<]+)</span>\s*的播放列表([\s\S]*?)(?=来自\s*<span class="uk-label">|$)', txt)
        for name, seg in blocks:
            eps = re.findall(r'<a[^>]*href="(/vodplay/\d+-\d+-\d+\.html)"[^>]*>([\s\S]*?)</a>', seg)
            seen_ep = set()
            ep_parts = []
            for href, ep_txt in eps:
                url = self.host + href
                if url in seen_ep:
                    continue
                seen_ep.add(url)
                ep_name = self._fmt_ep(ep_txt)
                if not ep_name:
                    continue
                ep_parts.append('%s$%s' % (ep_name, url))
            if ep_parts:
                froms.append(self._clean(name))
                urls.append('#'.join(ep_parts))

        v['vod_play_from'] = '$$$'.join(froms)
        v['vod_play_url'] = '$$$'.join(urls)
        return {'list': [v]}

    # ---------------- 搜索 (修复 Cloudflare 拦截) ----------------
    def searchContent(self, key, quick, pg='1'):
        try:
            pg = int(pg) if pg else 1
        except Exception:
            pg = 1
        kw = quote(str(key or ''))
        # 使用 /index.php?m=vod-search-wd-{kw} 绕过 Cloudflare
        if pg > 1:
            url = self.host + '/index.php?m=vod-search-wd-%s-page-%d' % (kw, pg)
        else:
            url = self.host + '/index.php?m=vod-search-wd-%s' % kw
        txt = self._fetch(url, self.host + '/')
        if not txt or 'Just a moment' in txt[:500]:
            return {'list': [], 'page': pg, 'pagecount': 1, 'limit': 0, 'total': 0}
        vods = self._parse_cards(txt)
        # 搜索结果页可能用不同的卡片结构, 尝试回退解析
        if not vods:
            vods = self._main_list(txt)
        if not vods:
            # 回退: 直接从搜索结果提取
            seen = set()
            for m in re.finditer(r'href="/voddetail/(\d+)/"[^>]*title="([^"]*)"[^>]*data-original="([^"]*)"', txt):
                vid = m.group(1)
                if vid not in seen:
                    seen.add(vid)
                    vods.append({
                        'vod_id': vid,
                        'vod_name': self._clean(m.group(2)),
                        'vod_pic': self._fix_poster(m.group(3)),
                        'vod_remarks': '',
                    })
        pagecount = self._pagecount(txt)
        return {
            'list': vods,
            'page': pg,
            'pagecount': max(pagecount, 1),
            'limit': len(vods),
            'total': len(vods),
        }

    # ---------------- 播放解析 ----------------
    def _is_vip_url(self, url):
        """判断是否为VIP平台URL"""
        try:
            host = re.search(r'https?://([^/]+)', url).group(1).lower()
        except Exception:
            return False
        return any(d in host for d in VIP_DOMAINS)

    def _try_parse(self, play_url):
        """
        用解析口解析VIP平台URL → m3u8 直链
        返回 m3u8 URL 或 None
        """
        for p in PARSERS:
            try:
                full_url = p['url'] + quote(play_url, safe='')
                r = None
                if hasattr(self, 'fetch') and self.fetch is not None:
                    try:
                        r = self.fetch(full_url, headers={'User-Agent': UA}, timeout=15)
                        txt = r.text if hasattr(r, 'text') else str(r)
                    except Exception:
                        txt = ''
                else:
                    if _requests:
                        r = _requests.get(full_url, headers={'User-Agent': UA}, timeout=15, verify=False)
                        txt = r.text
                    else:
                        txt = ''
                if not txt:
                    continue
                # 尝试 JSON 解析
                try:
                    d = json.loads(txt)
                    for key in ('url', 'data', 'play_url', 'm3u8', 'source'):
                        if key in d:
                            val = d[key]
                            if isinstance(val, str) and val.startswith('http') and 'error' not in val.lower():
                                return val
                            elif isinstance(val, dict) and 'url' in val:
                                u = val['url']
                                if isinstance(u, str) and u.startswith('http'):
                                    return u
                            elif isinstance(val, list) and val and isinstance(val[0], dict) and 'url' in val[0]:
                                u = val[0]['url']
                                if isinstance(u, str) and u.startswith('http'):
                                    return u
                except Exception:
                    pass
                # 非JSON, 找 m3u8
                m = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', txt)
                if m:
                    return m.group(1)
                m2 = re.search(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', txt)
                if m2:
                    return m2.group(1)
            except Exception:
                continue
            time.sleep(0.2)
        return None

    def playerContent(self, flag, id, vipFlags):
        if not id:
            return {}
        # id 可能是 /vodplay/xxx.html 或完整 URL
        if id.startswith('http'):
            url = id
        elif id.startswith('/vodplay/'):
            url = self.host + id
        else:
            url = self.host + '/vodplay/%s.html' % id

        txt = self._fetch(url, self.host + '/')
        m = re.search(r'data-play="([^"]+)"', txt or '')
        if not m:
            return {'parse': 1, 'url': url, 'header': json.dumps({'User-Agent': UA})}

        raw = m.group(1)
        # data-play = [3字符前缀] + base64
        b64 = raw[3:] if len(raw) > 3 else raw
        try:
            dec = base64.b64decode(b64 + '=' * (-len(b64) % 4)).decode('utf-8', 'ignore')
        except Exception:
            dec = ''
        if not dec:
            return {'parse': 1, 'url': url, 'header': json.dumps({'User-Agent': UA})}
        dec = dec.strip()

        # 1) m3u8/mp4 直链 → parse=0
        if dec.startswith('http') and ('.m3u8' in dec.lower() or '.mp4' in dec.lower()):
            hdr = {'User-Agent': UA}
            # 第三方CDN需要Referer防盗链
            if 'kpkuang' not in dec and 'flixfiend' not in dec:
                hdr['Referer'] = self.host + '/'
            return {
                'parse': 0,
                'url': dec,
                'header': json.dumps(hdr),
            }

        # 2) VIP平台URL (v.qq.com/youku/bilibili等) → 解析口解析
        if self._is_vip_url(dec):
            m3u8 = self._try_parse(dec)
            if m3u8:
                return {
                    'parse': 0,
                    'url': m3u8,
                    'header': json.dumps({'User-Agent': UA}),
                }
            # 解析失败 → parse=1 让TVBox尝试
            return {
                'parse': 1,
                'url': dec,
                'header': json.dumps({'User-Agent': UA}),
            }

        # 3) 自定义播放器URL (abyssplayer/byseqekaho/ezplayer等) → parse=1 嗅探
        return {
            'parse': 1,
            'url': dec,
            'header': json.dumps({'User-Agent': UA, 'Referer': self.host + '/'}),
        }


# ---------------- 本地自检 ----------------
if __name__ == '__main__':
    sp = Spider()
    sp.init('')
    print('=' * 60)
    print('看片狂人 v2 自检')
    print('=' * 60)

    # 首页
    print('\n[首页]')
    hc = sp.homeContent(True)
    print('  分类: %d个' % len(hc['class']))
    print('  筛选: %d组' % len(hc.get('filters', {})))
    hv = sp.homeVideoContent()['list']
    print('  推荐: %d条 | 首: %s' % (len(hv), hv[0]['vod_name'] if hv else '-'))
    if hv:
        print('  海报: %s' % ('有' if hv[0].get('vod_pic') else '无'))

    # 分类
    print('\n[分类列表]')
    for tid in ['1', '6', '2', '3', '4', '37']:
        lst = sp.categoryContent(tid, '1', False, {})['list']
        print('  tid=%-3s: %d条 | 首: %s' % (tid, len(lst), lst[0]['vod_name'][:15] if lst else '-'))

    # 筛选
    print('\n[筛选测试]')
    lst = sp.categoryContent('1', '1', True, {'area': '美国', 'year': '2025'})['list']
    print('  电影+美国+2025: %d条' % len(lst))
    if lst:
        print('  首条: %s | 海报: %s' % (lst[0]['vod_name'][:15], '有' if lst[0].get('vod_pic') else '无'))

    # 搜索
    print('\n[搜索]')
    s = sp.searchContent('战狼', False, '1')
    print('  结果: %d条 | 首: %s' % (len(s['list']), s['list'][0]['vod_name'] if s['list'] else '-'))

    # 详情+播放
    print('\n[详情+播放]')
    if hv:
        d = sp.detailContent([hv[0]['vod_id']])['list'][0]
        print('  %s | 海报: %s' % (d['vod_name'][:20], '有' if d.get('vod_pic') else '无'))
        lines = d['vod_play_from'].split('$$$') if d.get('vod_play_from') else []
        line_urls = d['vod_play_url'].split('$$$') if d.get('vod_play_url') else []
        print('  线路: %d条' % len(lines))
        for i in range(min(5, len(lines))):
            ln = lines[i]
            eps = line_urls[i].split('#') if i < len(line_urls) else []
            print('    %d. %-15s (%d集)' % (i+1, ln[:12], len(eps)))
            if eps:
                first_ep_url = eps[0].split('$', 1)[1] if '$' in eps[0] else ''
                pc = sp.playerContent(ln, first_ep_url, '')
                print('       parse=%s | %s' % (pc.get('parse', '?'), pc.get('url', '')[:70]))

    # VIP线路播放测试
    print('\n[VIP解析线播放测试]')
    # 找一个有VIP线路的片子
    html = sp._fetch(sp.host + '/vodtype/1/', sp.host + '/')
    cards = re.findall(r'href="/voddetail/(\d+)/"', html)
    for vid in cards[:5]:
        d = sp.detailContent([vid])['list']
        if d:
            d = d[0]
            lines = d.get('vod_play_from', '').split('$$$')
            line_urls = d.get('vod_play_url', '').split('$$$')
            for i, ln in enumerate(lines):
                if 'VIP' in ln or '解析' in ln:
                    eps = line_urls[i].split('#') if i < len(line_urls) else []
                    if eps:
                        first_ep_url = eps[0].split('$', 1)[1] if '$' in eps[0] else ''
                        pc = sp.playerContent(ln, first_ep_url, '')
                        print('  [%s] parse=%s | %s' % (ln, pc.get('parse', '?'), pc.get('url', '')[:70]))
                    break
            else:
                continue
            break

    print('\n' + '=' * 60)
