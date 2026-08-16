#!/usr/bin/env python3
# coding=utf-8
# !/usr/bin/python
"""
白嫖者联盟 (bpz.app) —— TVBox / 影视仓 Python 爬虫 (T4 py)
功能  : 首页推荐 / 分类浏览+翻页 / 搜索 / 详情选集 / 播放解析(官方线路换票 + 第三方 m3u8)
依赖  : 无第三方强依赖(有 requests 用 requests, 否则回退 urllib)

站点结构说明:
  - 数据接口均为 JSON API(/v1/*), 需要 HMAC-SHA256 请求签名
  - 签名算法: HMAC-SHA256(key, "{METHOD}\n{pathname}{search}\n{timestamp}\n{nonce}")
  - 首页   : GET /v1/feed/home
  - 分类   : GET /v1/browse/catalog?kind={movie|series|anime|variety|short_drama|documentary}&page=&limit=&offset=
  - 搜索   : GET /v1/suggest?q={关键词}&mode=search
  - 详情   : GET /v1/catalog/{variant_id}
  - 剧集   : GET /v1/catalog/{variant_id}/episodes   (48集/页, 自动翻页)
  - 播放   : GET player.baipiaozhe.com/v1/playback/resolve/{token}
            官方线路返回 resolve_ticket 票根 → 带 ?ps={playback_source_id} 重新请求换票
            (官方 m3u8 为短期签名, 必须播放时实时换取; 失败自动退回第三方 m3u8)

播放请求头: 第三方图床对 Referer 敏感, 只回传 User-Agent, 不给 Referer。
"""

import base64
import json
import re
import sys
import time
import urllib.parse

sys.path.append('..')

# ---- TVBox 运行环境提供 base.spider; 本地调试时降级为空基类 ----
try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass

try:
    import requests
    HAS_REQUESTS = True
except Exception:
    HAS_REQUESTS = False

DEFAULT_SITE = 'https://bpz.app'
PLAY_SITE = 'https://player.baipiaozhe.com'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# 站点 API 网关签名密钥(从前端 chunk 提取)
SIGN_SECRET = 'f39d73aa7a6426203cdee1ef17b31d3b7ea8c23f4c59c62a3a8aa0f39ee5e79d'


class Spider(BaseSpider):
    # ==================== 生命周期 ====================
    def init(self, extend=""):
        """extend 可传入新域名, 站点换域名时无需改代码"""
        self.site = DEFAULT_SITE
        try:
            if extend:
                ext = extend.strip()
                if ext.startswith('{'):
                    ext = json.loads(ext).get('site', '')
                if ext.startswith('http'):
                    self.site = ext.rstrip('/')
        except Exception:
            pass
        return self

    def getName(self):
        return '白嫖者联盟'

    def isVideoFormat(self, url):
        return bool(re.search(r'\.(m3u8|mp4|mkv|flv|avi|ts)(\?|$)', str(url), re.I))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        return ''

    def localProxy(self, param):
        return [200, "video/MP2T", {}, None]

    # ==================== 签名 ====================
    @staticmethod
    def _sign(method, path, ts, nonce):
        msg = '%s\n%s\n%s\n%s' % (method, path, ts, nonce)
        try:
            import hmac
            import hashlib
            return hmac.new(SIGN_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        except Exception:
            return ''

    def _api_headers(self, method, path):
        ts = str(int(time.time() * 1000))
        nonce = self._rand_hex(16)
        sig = self._sign(method, path, ts, nonce)
        return {
            'x-ai-movie-timestamp': ts,
            'x-ai-movie-nonce': nonce,
            'x-ai-movie-signature': sig,
            'x-ai-movie-client-name': 'movie-search-frontend',
            'x-ai-movie-client-version': '1.0.0',
            'x-ai-movie-protocol-version': '2026-07-05.library-v2.playback-v1',
            'x-ai-movie-build-version': 'aimovie-v2026.08.15.5-150e53a6eed3',
            'User-Agent': UA,
        }

    @staticmethod
    def _rand_hex(n):
        try:
            import secrets
            return secrets.token_hex(n)
        except Exception:
            return ''.join('%02x' % ((int(time.time() * 1e9) + i) % 256) for i in range(n))

    # ==================== 网络 ====================
    def _session(self):
        """复用连接省去重复 TLS 握手"""
        if not HAS_REQUESTS:
            return None
        se = getattr(self, '_se', None)
        if se is None:
            try:
                se = requests.Session()
                ad = requests.adapters.HTTPAdapter(
                    pool_connections=4, pool_maxsize=8, max_retries=0)
                se.mount('https://', ad)
                se.mount('http://', ad)
            except Exception:
                se = requests
            self._se = se
        return se

    def _get(self, url, headers=None, timeout=None, retry=3):
        """GET 请求; 失败退避重试, 最终失败返回空串"""
        ct, rt = timeout or (8, 25)
        for i in range(max(1, retry)):
            try:
                if HAS_REQUESTS:
                    r = self._session().get(url, headers=headers or {},
                                            timeout=(ct, rt), allow_redirects=True)
                    if r.status_code >= 500:
                        raise IOError('http %d' % r.status_code)
                    return r.content.decode('utf-8', 'ignore')
                import urllib.request
                req = urllib.request.Request(url, headers=headers or {})
                return urllib.request.urlopen(req, timeout=rt).read().decode('utf-8', 'ignore')
            except Exception:
                if i + 1 < max(1, retry):
                    time.sleep(0.8 * (i + 1))
        return ''

    def _api_get(self, path, params=None, signed=True):
        """
        请求 JSON API; signed=True 时附加 HMAC 签名头。
        签名基于 path(含 query)。
        """
        if params:
            path = path + '?' + urllib.parse.urlencode(params)
        headers = self._api_headers('GET', path) if signed else {'User-Agent': UA}
        body = self._get(self.site + path, headers=headers)
        if not body:
            return None
        try:
            return json.loads(body)
        except Exception:
            return None

    # ==================== 首页 ====================
    def homeContent(self, filter):
        cats = [
            {'type_id': 'movie', 'type_name': '电影'},
            {'type_id': 'series', 'type_name': '剧集'},
            {'type_id': 'anime', 'type_name': '动漫'},
            {'type_id': 'variety', 'type_name': '综艺'},
            {'type_id': 'short_drama', 'type_name': '短剧'},
            {'type_id': 'documentary', 'type_name': '纪录片'},
        ]
        return {'class': cats, 'filters': {}}

    def homeVideoContent(self):
        d = self._api_get('/v1/feed/home',
                          {'scope': 'public', 'mode': 'preview',
                           'sections': '11', 'cards': '20'})
        lst = []
        if d and d.get('sections'):
            for sec in d['sections']:
                for c in sec.get('cards', []):
                    v = self._card_to_vod(c)
                    if v:
                        lst.append(v)
        return {'list': lst[:80]}

    @staticmethod
    def _card_to_vod(c):
        """站点卡片 -> TVBox vod 条目"""
        if not c or not c.get('id'):
            return None
        return {
            'vod_id': c.get('id', ''),
            'vod_name': c.get('title', ''),
            'vod_pic': c.get('poster_url', ''),
            'vod_remarks': c.get('remarks', '') or '',
            'vod_year': str(c.get('year', '') or ''),
            'type_name': c.get('content_kind', '') or '',
        }

    # ==================== 分类列表 ====================
    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1

        limit = 24
        offset = (pg - 1) * limit
        d = self._api_get('/v1/browse/catalog', {
            'label': str(tid), 'path': '/v1/browse/catalog',
            'page': str(pg), 'limit': str(limit), 'offset': str(offset),
            'kind': str(tid), 'intent': 'latest_catalog',
        })
        lst, total = [], 0
        if d:
            total = d.get('total') or 0
            for c in d.get('cards', []):
                v = self._card_to_vod(c)
                if v:
                    lst.append(v)
        pagecount = max(1, (total + limit - 1) // limit) if total else pg
        return {
            'list': lst,
            'page': pg,
            'pagecount': pagecount,
            'limit': limit,
            'total': total,
        }

    # ==================== 搜索 ====================
    def searchContent(self, key, quick, pg="1"):
        key = str(key).strip()
        d = self._api_get('/v1/suggest', {'q': key, 'mode': 'search'})
        lst = []
        if d and d.get('suggestions'):
            for s in d['suggestions']:
                vid = (s.get('target') or {}).get('variant_id', '')
                if not vid:
                    continue
                plan = s.get('plan') or {}
                lst.append({
                    'vod_id': vid,
                    'vod_name': s.get('label', ''),
                    'vod_pic': '',
                    'vod_remarks': s.get('subtitle', '') or '',
                    'vod_year': str(plan.get('year', '') or ''),
                    'type_name': plan.get('kind', '') or '',
                })
        # 精确匹配置顶
        def score(v):
            n = v.get('vod_name', '')
            if n == key:
                return 0
            if key and key in n:
                return 1
            return 2
        lst.sort(key=score)
        return {'list': lst, 'page': 1, 'pagecount': 1,
                'limit': len(lst), 'total': len(lst)}

    # ==================== 详情 / 选集 ====================
    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        vid = str(vid).strip()

        d = self._api_get('/v1/catalog/' + urllib.parse.quote(vid, safe=''))
        if not d:
            return {'list': []}

        vod = {
            'vod_id': d.get('variant_id') or d.get('id', ''),
            'vod_name': d.get('title', ''),
            'vod_pic': d.get('poster_url', ''),
            'vod_year': str(d.get('year', '') or ''),
            'vod_area': d.get('area', '') or '',
            'vod_actor': ','.join(d.get('actors', [])),
            'vod_director': ','.join(d.get('directors', [])),
            'vod_remarks': d.get('remarks', '') or '',
            'type_name': d.get('content_kind', '') or '',
            'vod_content': d.get('description', '') or '',
            'vod_play_from': '白嫖者联盟',
            'vod_play_url': '',
        }

        # 剧集: 逐页拉取(48集/页)
        eps = []
        offset = 0
        while True:
            e = self._api_get('/v1/catalog/' + urllib.parse.quote(vid, safe='') + '/episodes',
                              {'offset': str(offset), 'limit': '100'})
            if not e or not e.get('episodes'):
                break
            eps.extend(e['episodes'])
            pg = e.get('episode_pagination') or {}
            if not pg.get('has_more'):
                break
            offset = pg.get('offset', 0) + len(e['episodes'])
            if offset >= pg.get('total_count', 0):
                break

        if eps:
            parts = []
            for ep in eps:
                label = ep.get('title') or ('第%s集' % ep.get('number', ''))
                label = str(label).replace('#', '').replace('$', '')
                parts.append('%s$bpz://%s' % (label, ep.get('token', '')))
            play_str = '#'.join(parts)

            # 多线路: 用第一集 token 探测一次 resolve, 取全部线路名
            lines = self._probe_lines(eps[0].get('token', ''))
            vod['vod_play_from'] = '$$$'.join(lines)
            vod['vod_play_url'] = '$$$'.join([play_str] * len(lines))

        return {'list': [vod]}

    def _probe_lines(self, token):
        """探测某集的可用线路名列表(去重保序, 重名补序号)
        排序优先级: 4K 线路 > 官方线路(高清/1080P) > 第三方线路
        """
        d = self._resolve(token)
        if not d:
            return ['默认线路']

        def sort_key(lo):
            n = lo.get('provider_name') or ''
            if '4k' in n.lower():
                return 0
            if '官方' in n or '1080' in n or '高清' in n or 'cloudflare' in str(lo.get('play_from', '')).lower():
                return 1
            return 2

        ordered = sorted(d.get('line_options', []), key=sort_key)
        names = []
        for lo in ordered:
            n = lo.get('provider_name') or ''
            if n:
                names.append(n)
        if not names:
            return ['默认线路']
        lines, used = [], {}
        for n in names:
            used[n] = used.get(n, 0) + 1
            if used[n] > 1:
                lines.append('%s%d' % (n, used[n]))
            else:
                lines.append(n)
        return lines

    def _resolve_line(self, d, token, lo):
        """针对单个线路对象取真实播放地址; 返回 (url, header) 或 None"""
        u = lo.get('url', '')
        if lo.get('url_kind') == 'm3u8' and u.startswith('http'):
            return u, self._play_header(u)
        if lo.get('url_kind') == 'resolve_ticket':
            ps = lo.get('playback_source_id', '')
            if not ps:
                return None
            d2 = self._resolve(token, ps)
            if not d2:
                return None
            # 匹配同名线路, 或取换票结果中第一个可用地址
            name = lo.get('provider_name', '')
            for lo2 in d2.get('line_options', []):
                if lo2.get('url_kind') not in ('m3u8', 'mp4'):
                    continue
                u2 = lo2.get('url', '')
                if not u2.startswith('http'):
                    continue
                if (lo2.get('provider_name') == name or
                        lo2.get('playback_source_id') == ps or
                        lo2.get('id') == lo.get('id')):
                    return u2, self._play_header(u2)
            for lo2 in d2.get('line_options', []):
                u2 = lo2.get('url', '')
                if lo2.get('url_kind') in ('m3u8', 'mp4') and u2.startswith('http'):
                    return u2, self._play_header(u2)
        return None

    # ==================== 播放解析 ====================
    @staticmethod
    def _play_header(url):
        """按视频分片所在 CDN 决定回传给播放器的请求头; 第三方图床只给 UA"""
        h = {'User-Agent': UA}
        try:
            host = urllib.parse.urlparse(url).hostname or ''
        except Exception:
            host = ''
        if any(k in host for k in ('bpz.app', 'baipiaozhe.com')):
            h['Referer'] = 'https://bpz.app/'
        return h

    def _resolve(self, token, ps=None):
        """请求播放解析接口; ps 指定线路时返回该线路的真实地址"""
        path = '/v1/playback/resolve/' + urllib.parse.quote(token, safe='')
        if ps:
            path += '?ps=' + urllib.parse.quote(ps, safe='')
        body = self._get(self._play_site() + path, headers={'User-Agent': UA})
        if not body:
            return None
        try:
            return json.loads(body)
        except Exception:
            return None

    def _play_site(self):
        return getattr(self, 'play_site', PLAY_SITE)

    def playerContent(self, flag, id, vipFlags):
        pid = str(id).strip()
        # 兼容: bpz://token 或裸 token
        token = pid[len('bpz://'):] if pid.startswith('bpz://') else pid

        result = {'parse': 0, 'playUrl': '', 'url': '',
                  'header': {'User-Agent': UA}}

        d = self._resolve(token)
        if not d:
            # 解析失败 → 交给播放器嗅探页面(保底)
            result['parse'] = 1
            result['url'] = '%s/v1/playback/resolve/%s' % (self._play_site(), token)
            return result

        lines = d.get('line_options', [])

        # 0) 用户显式选择了某条线路(flag = 线路名)
        if flag:
            target = None
            for lo in lines:
                if lo.get('provider_name') == flag or lo.get('label') == flag:
                    target = lo
                    break
            if not target:  # 兼容"重名补序号"的线路名
                f = str(flag)
                for lo in lines:
                    n = lo.get('provider_name', '')
                    if n and (f == n or f.startswith(n)):
                        target = lo
                        break
            if target:
                got = self._resolve_line(d, token, target)
                if got:
                    result['url'], result['header'] = got
                    return result

        # 1) 官方线路: resolve_ticket → 带 ps 参数实时换票
        for lo in lines:
            if lo.get('url_kind') != 'resolve_ticket':
                continue
            got = self._resolve_line(d, token, lo)
            if got:
                result['url'], result['header'] = got
                return result

        # 2) 退回第三方 m3u8 线路
        for lo in lines:
            u = lo.get('url', '')
            if lo.get('url_kind') == 'm3u8' and u.startswith('http'):
                result['url'] = u
                result['header'] = self._play_header(u)
                return result

        # 3) 保底: 交给播放器嗅探
        result['parse'] = 1
        result['url'] = '%s/v1/playback/resolve/%s' % (self._play_site(), token)
        return result


# ============================================================
# 本地自测:  python3 bpz_tvbox.py
# ============================================================
if __name__ == '__main__':
    s = Spider().init('')
    print('== 分类 ==')
    cs = s.homeContent(False)['class']
    print(len(cs), [c['type_name'] for c in cs])

    print('\n== 首页视频 ==')
    hv = s.homeVideoContent()['list']
    print('共%d条, 首条: %s' % (len(hv), hv[0] if hv else '空'))

    print('\n== 列表(动漫 第1页) ==')
    lst = s.categoryContent('anime', 1, {}, {})['list']
    print('共%d条, 首条: %s' % (len(lst), lst[0] if lst else '空'))

    print('\n== 详情 ==')
    d = s.detailContent([lst[0]['vod_id']])['list'][0]
    print('%s | %s | %s | %s' % (d['vod_name'], d['vod_year'],
                                  d['vod_area'], d['type_name']))
    print('选集: %s ...' % d['vod_play_url'][:120])

    print('\n== 播放解析 ==')
    first = d['vod_play_url'].split('#')[0].split('$')[1]
    pr = s.playerContent('白嫖者联盟', first, '')
    print('url: %s' % pr['url'])
    print('header: %s' % pr['header'])

    print('\n== 搜索 ==')
    r = s.searchContent('火影', True)['list']
    print('共%d条:' % len(r), [x['vod_name'] for x in r[:5]])
