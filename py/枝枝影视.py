# -*- coding: utf-8 -*-
# 枝枝影视(多线路聚合版) TVBox 爬虫
# 站点 www.zzoc.cc 全站挂 Cloudflare 托管挑战，纯脚本无法抓取页面；
# 经浏览器实测其 6 条播放源(FF/DY/MT/IQ/DB/YZ)的真实地址，反推出各源对应资源站：
#   FF = 非凡资源(ffm3u8)   -> https://cj.ffzyapi.com  已验证开放
#   DY = 电影天堂(dyttm3u8) -> https://caiji.dyttzyapi.com 已验证开放（播放域名与枝枝影视DY线路完全一致）
#   DB = 百度资源(dbm3u8)   -> https://api.apibdzy.com 已验证开放
#   MT/IQ/YZ 无公开采集接口（1080资源库接口存在但封禁本机IP）
# 本爬虫：列表/分类/搜索走非凡资源（内容库即枝枝影视 FF 源），
#         详情时用片名并行检索 电影天堂/百度资源，合并多线路返回。
# 编写标准：与已验证可用的 金牌影视/电影天堂/枫叶4k 同一套稳妥写法
#   - 自包含、不依赖基类 init
#   - init 兼容 空/None/纯URL/JSON/垃圾输入，永不抛异常
#   - 所有请求走 requests.Session + 完整浏览器头
#   - 任何一步失败优雅降级，绝不崩溃

import re
import sys
import json
import threading

import requests
from urllib.parse import quote

sys.path.append('..')

# 主源：非凡资源（ffm3u8 线路，m3u8 直链）
API_FF = 'https://cj.ffzyapi.com/api.php/provide/vod/from/ffm3u8/at/json/'
# 附加源：电影天堂（dyttm3u8）
API_DY = 'https://caiji.dyttzyapi.com/api.php/provide/vod/from/dyttm3u8/at/json/'
# 附加源：百度资源（dbm3u8）
API_DB = 'https://api.apibdzy.com/api.php/provide/vod/at/json/'

# 主源分类（API 真实分类 -> 五大类，tid 支持逗号合并查询）
CLASSES = [
    {'type_id': '6,7,8,9,10,11,12,20,34', 'type_name': '电影'},
    {'type_id': '13,14,15,16,21,22,23,24', 'type_name': '电视剧'},
    {'type_id': '25,26,27,28', 'type_name': '综艺'},
    {'type_id': '29,30,31,32', 'type_name': '动漫'},
    {'type_id': '36', 'type_name': '短剧'},
]

# 线路顺序与显示名（对应枝枝影视播放源）
LINE_NAMES = {'FF': '非凡', 'DY': '电影天堂', 'DB': '百度'}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'application/json,text/plain,*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://www.zzoc.cc/',
    'Origin': 'https://www.zzoc.cc',
}


class Spider:
    def __init__(self):
        self.site_url = 'https://www.zzoc.cc/'
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._timeout = 15

    def init(self, extend):
        # 兼容任意输入，永不抛异常
        try:
            if not extend:
                return
            if isinstance(extend, str):
                s = extend.strip()
                if s.startswith('{'):
                    d = json.loads(s)
                    if d.get('site_url'):
                        self.site_url = d['site_url']
                elif '://' in s:
                    self.site_url = s
        except Exception:
            pass

    # ---------- 基础请求 ----------
    def _get(self, url, timeout=None):
        try:
            r = self.session.get(url, timeout=timeout or self._timeout)
            if r.status_code != 200:
                return None
            return r
        except Exception:
            return None

    def _fetch_json(self, url, timeout=None):
        r = self._get(url, timeout=timeout)
        if not r:
            return None
        try:
            return r.json()
        except Exception:
            return None

    # ---------- 格式化 ----------
    @staticmethod
    def _strip_tags(s):
        if not s:
            return ''
        return re.sub(r'<[^>]+>', '', str(s)).replace('&nbsp;', ' ').strip()

    @staticmethod
    def _clean_name(n):
        # 片名归一化：去空白/大小写，用于跨源匹配
        if not n:
            return ''
        return re.sub(r'\s+', '', str(n)).lower()

    def _fmt_list(self, items):
        out = []
        if not items:
            return out
        for it in items:
            try:
                vid = it.get('vod_id')
                name = it.get('vod_name') or ''
                if not vid or not name:
                    continue
                out.append({
                    'vod_id': str(vid),
                    'vod_name': name,
                    'vod_pic': it.get('vod_pic') or '',
                    'vod_remarks': it.get('vod_remarks') or '',
                    'type_name': it.get('type_name') or '',
                })
            except Exception:
                continue
        return out

    @staticmethod
    def _clean_play_url(play_url):
        # 只保留真实可播地址（m3u8/mp4），过滤 /share/ HTML 跳转页
        if not play_url:
            return ''
        lines = play_url.split('$$$')
        kept = []
        for line in lines:
            eps = [e for e in line.split('#') if e]
            good = []
            for e in eps:
                if '$' not in e:
                    continue
                label, url = e.split('$', 1)
                if not url or '/share/' in url:
                    continue
                good.append('%s$%s' % (label, url))
            if good:
                kept.append('#'.join(good))
        return '$$$'.join(kept)

    # ---------- 跨源详情合并 ----------
    def _detail_from_api(self, api, vid=None, wd=None):
        """从一个采集接口取详情（优先ids，其次片名搜索），返回(play_from, play_url, name)"""
        try:
            if vid is not None:
                d = self._fetch_json('%s?ac=detail&ids=%s' % (api, vid), timeout=10)
            else:
                d = self._fetch_json('%s?ac=detail&wd=%s' % (api, quote(wd)), timeout=10)
            if not d:
                return None
            lst = d.get('list') or []
            if not lst:
                return None
            v = lst[0]
            return {
                'name': v.get('vod_name') or '',
                'year': v.get('vod_year') or '',
                'play_from': v.get('vod_play_from') or '',
                'play_url': self._clean_play_url(v.get('vod_play_url') or ''),
            }
        except Exception:
            return None

    def _find_match(self, api, name):
        """跨源搜索，返回与目标片名完全一致的条目（详情数据）"""
        # 先按片名搜
        found = self._detail_from_api(api, wd=name)
        if not found:
            return None
        if self._clean_name(found['name']) != self._clean_name(name):
            return None
        if not found['play_url']:
            return None
        return found

    def _merge_lines(self, main_v, name):
        """并行检索附加源，合并线路；返回(play_from, play_url)"""
        play_from = main_v.get('vod_play_from') or ''
        play_url = main_v.get('vod_play_url') or ''
        # 附加源统一归入本源的线路名
        main_from = play_from.split('$$$')[0] if play_from else 'ffm3u8'
        if main_from not in ('ffm3u8',):
            main_from = 'ffm3u8'

        results = {}
        def fetch(key, api):
            try:
                m = self._find_match(api, name)
                if m:
                    results[key] = m
            except Exception:
                pass

        threads = []
        for key, api in (('DY', API_DY), ('DB', API_DB)):
            t = threading.Thread(target=fetch, args=(key, api))
            t.daemon = True
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=15)

        pf_parts = [LINE_NAMES.get('FF', '非凡')]
        pu_parts = [play_url]
        if results.get('DY') and results['DY']['play_url']:
            pf_parts.append(LINE_NAMES['DY'])
            pu_parts.append(results['DY']['play_url'])
        if results.get('DB') and results['DB']['play_url']:
            pf_parts.append(LINE_NAMES['DB'])
            pu_parts.append(results['DB']['play_url'])

        new_from = '$$$'.join(p for p in pf_parts if p)
        new_url = '$$$'.join(p for p in pu_parts if p)
        return new_from, new_url

    # ---------- TVBox 接口 ----------
    def homeContent(self, filter=False):
        data = {'class': CLASSES, 'list': []}
        # ac=detail 模式才返回海报 vod_pic（ac=list 精简模式无图）
        d = self._fetch_json(API_FF + '?ac=detail&h=24')
        if d:
            data['list'] = self._fmt_list(d.get('list', []))
        return data

    def categoryContent(self, tid, pg, filter=False, extend={}):
        pg = int(pg) if str(pg).isdigit() else 1
        if isinstance(tid, (list, tuple)):
            tid = ','.join(str(x) for x in tid)
        d = self._fetch_json('%s?ac=detail&t=%s&pg=%s' % (API_FF, tid, pg))
        if not d:
            return {'page': pg, 'pagecount': 0, 'list': []}
        try:
            pc = int(d.get('pagecount') or 1)
        except Exception:
            pc = 1
        return {'page': pg, 'pagecount': pc, 'list': self._fmt_list(d.get('list', []))}

    def detailContent(self, ids):
        if isinstance(ids, list):
            vid = ids[0] if ids else ''
        else:
            vid = ids
        m = re.search(r'/voddetail/(\d+)\.html', str(vid))
        if m:
            vid = m.group(1)

        d = self._fetch_json('%s?ac=detail&ids=%s' % (API_FF, vid))
        if not d or not d.get('list'):
            return {'list': [{'vod_id': str(vid), 'vod_name': '请求失败或接口不可用'}]}
        v = d['list'][0]
        name = v.get('vod_name') or ''

        play_from, play_url = self._merge_lines(v, name)

        return {'list': [{
            'vod_id': str(vid),
            'vod_name': name,
            'vod_pic': v.get('vod_pic') or '',
            'type_name': v.get('type_name') or '',
            'vod_year': v.get('vod_year') or '',
            'vod_area': v.get('vod_area') or '',
            'vod_director': (v.get('vod_director') or '').strip().rstrip(','),
            'vod_actor': (v.get('vod_actor') or '').strip().rstrip(','),
            'vod_content': self._strip_tags(v.get('vod_content'))[:2000],
            'vod_play_from': play_from,
            'vod_play_url': play_url,
            'vod_remarks': v.get('vod_remarks') or '',
        }]}

    def searchContent(self, key, quick=False, pg='1'):
        d = self._fetch_json('%s?ac=detail&wd=%s&pg=%s' % (API_FF, quote(key), pg))
        if not d:
            return {'list': []}
        return {'list': self._fmt_list(d.get('list', []))}

    def homeVideoContent(self):
        # OK影视 调用: 返回首页推荐内容
        try:
            d = self._fetch_json(API_FF + '?ac=detail&h=12')
            if d and d.get('list'):
                return {'list': self._fmt_list(d['list'])[:20]}
        except Exception:
            pass
        return {'list': []}

    def playerContent(self, flag, id, vipFlags=False):
        # id 为 m3u8 直链，直接返回
        if not id:
            return {}
        if isinstance(id, list):
            id = id[0] if id else ''
        url = str(id)
        if not url.startswith('http'):
            return {}
        return {'parse': 0, 'jx': 0, 'playUrl': '', 'url': url, 'header': {
            'User-Agent': HEADERS['User-Agent'],
            'Referer': 'https://www.zzoc.cc/',
            'Origin': 'https://www.zzoc.cc',
        }}

    def localProxy(self, param):
        return {}

    def getName(self):
        return '枝枝影视'

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
    return _spider.homeContent(filter) if _spider else {'class': CLASSES, 'list': []}


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
    return '枝枝影视'


def isVideoFormat(url):
    return False


def manualVideoCheck():
    return False


if __name__ == '__main__':
    import sys as _s
    _s.stdout.reconfigure(encoding='utf-8', errors='replace')
    init('')
    print('home classes:', [c['type_name'] for c in homeContent()['class']])
    c = categoryContent('6,7,8,9,10,11,12,20,34', 1)
    print('cat list:', len(c['list']), 'pagecount:', c['pagecount'])
    if c['list']:
        vid = c['list'][0]['vod_id']
        d = detailContent([vid])
        v = d['list'][0]
        print('detail:', v['vod_name'])
        print('play_from:', v['vod_play_from'])
        pu = v['vod_play_url']
        print('线路数:', len(pu.split('$$$')))
        print('play_url:', pu[:180])
