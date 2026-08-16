# -*- coding: utf-8 -*-
# ============================================================
# 看片狂人 (kpkuang.us) TVBox 爬虫 v3
# 参考: 1看片狂人部分高清不播放待修复.py
# 修复: 分类显示 + 播放解析
# ============================================================

import sys
import re
import json
import base64
from urllib.parse import quote

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except ImportError:
    class BaseSpider(object):
        def init(self, extend=''): return self
        def getName(self): return ''
        def isVideoFormat(self, url): return False
        def manualVideoCheck(self): return False
        def destroy(self): return ''
        def localProxy(self, param): return [200, 'video/MP2T', '', '']

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

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

_FILTER_TEMPLATE = [
    {'key': 'area', 'name': '地区', 'value': [
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
    ]},
    {'key': 'year', 'name': '年代', 'value': [
        {'n': '全部', 'v': ''},
        {'n': '2026', 'v': '2026'}, {'n': '2025', 'v': '2025'},
        {'n': '2024', 'v': '2024'}, {'n': '2023', 'v': '2023'},
        {'n': '2022', 'v': '2022'}, {'n': '2021', 'v': '2021'},
        {'n': '2020', 'v': '2020'}, {'n': '2019', 'v': '2019'},
        {'n': '2018', 'v': '2018'}, {'n': '2017', 'v': '2017'},
        {'n': '2016', 'v': '2016'}, {'n': '2015', 'v': '2015'},
    ]},
    {'key': 'by', 'name': '排序', 'value': [
        {'n': '默认', 'v': ''},
        {'n': '最新', 'v': 'time'},
        {'n': '最热', 'v': 'hits'},
        {'n': '评分', 'v': 'score'},
    ]},
]

FILTERS = {}
for c in CATEGORIES:
    FILTERS[c['type_id']] = _FILTER_TEMPLATE


class Spider(BaseSpider):
    def __init__(self):
        super(Spider, self).__init__()
        self.host = 'https://kpkuang.us'
        self.headers = {'User-Agent': UA, 'Referer': self.host + '/'}
        self._seen = {}

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
        h = dict(self.headers)
        if referer:
            h['Referer'] = referer
        try:
            import urllib.request
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"[kpkuang] 请求失败: {url} -> {e}")
            return ""

    # ---------------- 工具 ----------------
    @staticmethod
    def _clean(s):
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s or '')).strip()

    @staticmethod
    def _fix_poster(p):
        if not p:
            return ''
        if p.startswith('//'):
            p = 'https:' + p
        return p

    # ---------------- 列表卡片解析 ----------------
    def _parse_cards(self, txt):
        vods, seen = [], set()
        for m in re.finditer(
                r'<li[^>]*class="[^"]*fed-list-item[^"]*"[^>]*>([\s\S]*?)</li>', txt or '', re.I):
            block = m.group(1)
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
        vods, seen = [], set()
        for m in re.finditer(
                r'<ul class="fed-list-info fed-part-rows" style="overflow: unset">([\s\S]*?)</ul>', txt or ''):
            for v in self._parse_cards(m.group(1)):
                if v['vod_id'] not in seen:
                    seen.add(v['vod_id'])
                    vods.append(v)
        if vods:
            return vods
        return self._parse_cards(txt or '')

    def _pagecount(self, txt):
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

        nm = re.search(r'<h1[^>]*>([\s\S]*?)</h1>', txt)
        if nm:
            v['vod_name'] = self._clean(nm.group(1))
        else:
            tm = re.search(r'<title>([^<]*?)(_看片狂人| - )', txt)
            if tm:
                v['vod_name'] = self._clean(tm.group(1))

        pm = re.search(r'class="[^"]*fed-deta-images[^"]*"[^>]*>[\s\S]*?<img[^>]*data-original="([^"]*)"', txt)
        if not pm:
            pm = re.search(r'<img[^>]*data-original="(https?://[^"]*(?:tmdb|media-amazon|doubanio|flixfiend)[^"]*)"', txt)
        if not pm:
            pm = re.search(r'<img[^>]*data-original="([^"]*)"[^>]*', txt)
        if not pm:
            pm = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', txt)
        if pm:
            v['vod_pic'] = self._fix_poster(pm.group(1))

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

        tm2 = re.search(r'property="og:video:class" content="([^"]*)"', txt)
        if tm2:
            v['type_name'] = self._clean(tm2.group(1))

        cm = re.search(r'<span class="fed-text-muted">简介[：:]\s*</span>([\s\S]{0,800}?)</', txt)
        if cm:
            v['vod_content'] = self._clean(cm.group(1))

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
                t = self._clean(ep_txt)
                t = re.sub(r'更新至|已完结|全集|高清|超清|HD|BD|国语|粤语', '', t).strip()
                m2 = re.search(r'第\s*(\d+)\s*集', t)
                if m2:
                    n = int(m2.group(1))
                    if 1 <= n <= 9999:
                        t = '%02d' % n
                if not t:
                    continue
                ep_parts.append('%s$%s' % (t, url))
            if ep_parts:
                froms.append(self._clean(name))
                urls.append('#'.join(ep_parts))

        v['vod_play_from'] = '$$$'.join(froms)
        v['vod_play_url'] = '$$$'.join(urls)
        return {'list': [v]}

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick, pg='1'):
        try:
            pg = int(pg) if pg else 1
        except Exception:
            pg = 1
        kw = quote(str(key or ''))
        if pg > 1:
            url = self.host + '/index.php?m=vod-search-wd-%s-page-%d' % (kw, pg)
        else:
            url = self.host + '/index.php?m=vod-search-wd-%s' % kw
        txt = self._fetch(url, self.host + '/')
        if not txt or 'Just a moment' in txt[:500]:
            return {'list': [], 'page': pg, 'pagecount': 1, 'limit': 0, 'total': 0}
        vods = self._parse_cards(txt)
        if not vods:
            vods = self._main_list(txt)
        if not vods:
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
    def playerContent(self, flag, id, vipFlags):
        if not id:
            return {}
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
        b64 = raw[3:] if len(raw) > 3 else raw
        try:
            dec = base64.b64decode(b64 + '=' * (-len(b64) % 4)).decode('utf-8', 'ignore')
        except Exception:
            dec = ''
        if not dec:
            return {'parse': 1, 'url': url, 'header': json.dumps({'User-Agent': UA})}
        dec = dec.strip()

        if dec.startswith('http') and ('.m3u8' in dec.lower() or '.mp4' in dec.lower()):
            hdr = {'User-Agent': UA}
            if 'kpkuang' not in dec and 'flixfiend' not in dec:
                hdr['Referer'] = self.host + '/'
            return {
                'parse': 0,
                'url': dec,
                'header': json.dumps(hdr),
            }

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
    print('看片狂人 v3 自检')
    print('=' * 60)

    print('\n[首页]')
    hc = sp.homeContent(True)
    print('  分类: %d个' % len(hc['class']))
    print('  筛选: %d组' % len(hc.get('filters', {})))
    hv = sp.homeVideoContent()['list']
    print('  推荐: %d条 | 首: %s' % (len(hv), hv[0]['vod_name'] if hv else '-'))
    if hv:
        print('  海报: %s' % ('有' if hv[0].get('vod_pic') else '无'))

    print('\n[分类列表]')
    for tid in ['1', '6', '2', '3', '4', '37']:
        lst = sp.categoryContent(tid, '1', False, {})['list']
        print('  tid=%-3s: %d条 | 首: %s' % (tid, len(lst), lst[0]['vod_name'][:15] if lst else '-'))

    print('\n[详情+播放]')
    if hv:
        d = sp.detailContent([hv[0]['vod_id']])['list'][0]
        print('  %s | 海报: %s' % (d['vod_name'][:20], '有' if d.get('vod_pic') else '无'))
        lines = d['vod_play_from'].split('$$$') if d.get('vod_play_from') else []
        line_urls = d['vod_play_url'].split('$$$') if d.get('vod_play_url') else []
        print('  线路: %d条' % len(lines))
        for i in range(min(3, len(lines))):
            ln = lines[i]
            eps = line_urls[i].split('#') if i < len(line_urls) else []
            print('    %d. %-15s (%d集)' % (i+1, ln[:12], len(eps)))
            if eps:
                first_ep_url = eps[0].split('$', 1)[1] if '$' in eps[0] else ''
                pc = sp.playerContent(ln, first_ep_url, '')
                print('       parse=%s | %s' % (pc.get('parse', '?'), pc.get('url', '')[:70]))

    print('\n' + '=' * 60)
