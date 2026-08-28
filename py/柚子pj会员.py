# -*- coding: utf-8 -*-
# QQ群807916734  @Easy
""" 
yznb 影视源 
站点: https://yznb.4y5u.cc/
接口: AES-GCM 加密 API (key=0e3d2cf6f78dc8d8)
封面: AES-GCM 加密 .bin 图片 (key=7320c9f1f84847fc51c7262af022cec4, nonce=base64decode(FYb65V0QEXdAxexI))
"""
import os
import json
import base64
import time
import ssl
import threading
import http.server
import urllib.request
import requests
from urllib.parse import unquote, quote, urlparse, parse_qs
from base.spider import Spider

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

# ==================== AES-GCM 加解密 ====================
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM

    def _gcm_enc(key, iv, pt):
        return _AESGCM(key).encrypt(iv, pt, None)

    def _gcm_dec(key, iv, ct):
        return _AESGCM(key).decrypt(iv, ct, None)
except ImportError:
    from Crypto.Cipher import AES as _AES

    def _gcm_enc(key, iv, pt):
        c = _AES.new(key, _AES.MODE_GCM, nonce=iv)
        ct, tag = c.encrypt_and_digest(pt)
        return ct + tag

    def _gcm_dec(key, iv, ct):
        c = _AES.new(key, _AES.MODE_GCM, nonce=iv)
        return c.decrypt_and_verify(ct[:-16], ct[-16:])


# ==================== 封面代理服务 (移植自柚子.py) ====================
_COVER_PORT = None
_COVER_CACHE = {}
_COVER_LOCK = threading.Lock()
_COVER_GCM_KEY = bytes.fromhex('7320c9f1f84847fc51c7262af022cec4')
_COVER_GCM_IV = base64.b64decode('FYb65V0QEXdAxexI')


def _http_get_bytes(url, timeout=12):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://yznb.4y5u.cc/',
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    rsp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
    return rsp.read()


def _resolve_cover(src_url):
    if not src_url:
        return None
    if src_url in _COVER_CACHE:
        return _COVER_CACHE[src_url]
    try:
        raw = _http_get_bytes(src_url)
        pt = _gcm_dec(_COVER_GCM_KEY, _COVER_GCM_IV, raw)
        if pt and (pt[:2] == b'\xff\xd8' or pt[:8] == b'\x89PNG\r\n\x1a\n'):
            if len(_COVER_CACHE) > 512:
                _COVER_CACHE.clear()
            _COVER_CACHE[src_url] = pt
            return pt
    except Exception:
        pass
    return None


def _ensure_cover_server():
    """启动本地 HTTP 服务用于封面代理"""
    global _COVER_PORT
    if _COVER_PORT:
        return _COVER_PORT

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                q = parse_qs(urlparse(self.path).query)
                u = (q.get('u') or [''])[0]
                img = _resolve_cover(u)
                if img:
                    self.send_response(200)
                    self.send_header('Content-Type',
                                     'image/jpeg' if img[:2] == b'\xff\xd8' else 'image/png')
                    self.send_header('Content-Length', str(len(img)))
                    self.send_header('Cache-Control', 'public, max-age=86400')
                    self.end_headers()
                    self.wfile.write(img)
                else:
                    self.send_response(404)
                    self.end_headers()
            except Exception:
                try:
                    self.send_response(500)
                    self.end_headers()
                except Exception:
                    pass

        def log_message(self, *a):
            pass

    with _COVER_LOCK:
        if _COVER_PORT:
            return _COVER_PORT
        try:
            srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
            th = threading.Thread(target=srv.serve_forever, daemon=True)
            th.start()
            _COVER_PORT = srv.server_address[1]
        except Exception:
            _COVER_PORT = 0
    return _COVER_PORT or 0


def _cover_proxy_url(src_url):
    if not src_url or not str(src_url).startswith('http'):
        return src_url or ''
    port = _ensure_cover_server()
    if not port:
        return src_url
    return 'http://127.0.0.1:{}/cover?u={}'.format(port, quote(src_url, safe=''))


class Spider(Spider):
    # ==================== 基础配置 ====================
    name = 'yznb'
    base_url = 'https://yznb.4y5u.cc'

    searchable = 1
    quickSearch = 1
    filterable = 0
    changeable = 1

    # dr_py 本地服务端口（按实际配置修改）
    LOCAL_PORT = 5705
    USE_DATURI = False

    # API 加密密钥
    API_KEY = b'0e3d2cf6f78dc8d8'

    # 请求头
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Content-Type': 'text/plain',
        'Referer': 'https://yznb.4y5u.cc/',
        'Origin': 'https://yznb.4y5u.cc',
    }
    play_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://yznb.4y5u.cc/',
    }

    def __init__(self):
        super().__init__()
        self._session = requests.Session()
        self._session.headers.update(self.headers)
        self._verify = False
        self._menus_cache = None
        self._img_cache = {}

    # ==================== AES-GCM API 调用 ====================
    def _encrypt_body(self, data_dict):
        pt = json.dumps(data_dict, separators=(',', ':')).encode()
        iv = os.urandom(12)
        ct = _gcm_enc(self.API_KEY, iv, pt)
        return base64.b64encode(iv + ct).decode()

    def _decrypt_resp(self, text):
        raw = base64.b64decode(text)
        pt = _gcm_dec(self.API_KEY, raw[:12], raw[12:])
        return json.loads(pt.decode())

    def _api(self, path, data=None):
        body = self._encrypt_body(data or {})
        resp = self._session.post(
            f'{self.base_url}{path}',
            data=body,
            timeout=15,
            verify=self._verify,
        )
        return self._decrypt_resp(resp.text)

    # ==================== 封面图 ====================
    def _get_pic(self, cover_url):
        return _cover_proxy_url(cover_url)

    # ==================== 工具方法 ====================
    @staticmethod
    def _fmt_dur(seconds):
        if not seconds:
            return ''
        seconds = int(seconds)
        h, s = divmod(seconds, 3600)
        m, s = divmod(s, 60)
        return f'{h:02d}:{m:02d}:{s:02d}' if h > 0 else f'{m:02d}:{s:02d}'

    @staticmethod
    def _fmt_year(ts):
        if not ts:
            return ''
        try:
            return str(time.localtime(ts).tm_year)
        except Exception:
            return ''

    def _parse_video(self, item):
        """解析列表项"""
        return {
            'vod_id': str(item['id']),
            'vod_name': item.get('title', ''),
            'vod_pic': self._get_pic(item.get('cover', '')),
            'vod_remarks': self._fmt_dur(item.get('duration', 0)),
        }

    def _get_menus(self):
        """获取分类菜单（带缓存）"""
        if self._menus_cache is not None:
            return self._menus_cache
        try:
            self._menus_cache = self._api('/api/system/menus', {})
            return self._menus_cache
        except Exception:
            return None

    # ==================== dr_py 标准接口 ====================
    def init(self, extend=''):
        self._get_menus()

    def homeContent(self, filter=False):
        """首页分类"""
        result = {'class': []}
        d = self._get_menus()
        if d and 'data' in d:
            for _module_key, cat_list in d['data'].items():
                if not isinstance(cat_list, list):
                    continue
                for cat in cat_list:
                    children = cat.get('child', [])
                    if children:
                        for child in children:
                            result['class'].append({
                                'type_id': str(child['id']),
                                'type_name': child['name'],
                            })
                    else:
                        result['class'].append({
                            'type_id': str(cat['id']),
                            'type_name': cat['name'],
                        })
        return result

    def homeVideoContent(self):
        """首页推荐视频（每日更新）"""
        try:
            d = self._api('/api/movie/sublist/v2', {
                'typeid': 43,
                'page': 1,
                'page_size': 20,
            })
            videos = []
            if 'data' in d and 'list' in d['data']:
                for item in d['data']['list']:
                    videos.append(self._parse_video(item))
            return {'list': videos}
        except Exception:
            return {'list': []}

    def categoryContent(self, tid, pg, filter=False, content=None):
        """分类列表（支持翻页）"""
        try:
            pg = int(pg)
            d = self._api('/api/movie/sublist/v2', {
                'typeid': int(tid),
                'page': pg,
                'page_size': 20,
            })
            videos = []
            if 'data' in d and 'list' in d['data']:
                for item in d['data']['list']:
                    videos.append(self._parse_video(item))
            pagecount = d['data'].get('total_page', 1)
            if pagecount > 1000:
                pagecount = 200
            return {
                'list': videos,
                'page': pg,
                'pagecount': pagecount,
                'limit': 20,
                'total': pagecount * 20,
            }
        except Exception:
            return {'list': [], 'page': pg, 'pagecount': 1, 'limit': 20, 'total': 0}

    def detailContent(self, ids):
        """详情页（含播放列表）"""
        try:
            vod_id = ids[0] if isinstance(ids, list) else str(ids)
            d = self._api('/api/movie/detail/v2', {'id': int(vod_id)})
            if 'data' not in d:
                return {'list': []}
            item = d['data']

            vod = {
                'vod_id': str(item['id']),
                'vod_name': item.get('title', ''),
                'vod_pic': self._get_pic(item.get('cover', '')),
                'vod_year': self._fmt_year(item.get('created_at')),
                'vod_content': item.get('desc', '') or (
                    f"时长:{self._fmt_dur(item.get('duration', 0))} "
                    f"观看:{item.get('watch', 0)} "
                    f"点赞:{item.get('like', 0)}"
                ),
                'vod_play_from': 'easytv',
                'vod_play_url': f'播放${vod_id}',
            }
            return {'list': [vod]}
        except Exception:
            return {'list': []}

    def searchContent(self, key, quick=False, pg='1'):
        """搜索"""
        try:
            pg = max(1, int(pg or 1))
            page_size = 5 if quick else 20
            d = self._api('/api/movie/search', {
                'keyword': str(key),
                'page': pg,
                'page_size': page_size,
                'module': 1,
            })
            videos = []
            if 'data' in d and 'list' in d['data']:
                for item in d['data']['list']:
                    videos.append(self._parse_video(item))
            pagecount = d['data'].get('total_page', 1)
            if pagecount > 1000:
                pagecount = 200
            return {
                'page': pg,
                'pagecount': pagecount,
                'limit': page_size,
                'total': pagecount * page_size,
                'list': videos,
            }
        except Exception:
            return {'page': pg, 'pagecount': 1, 'limit': 20, 'total': 0, 'list': []}

    def playerContent(self, flag, id, vipFlags=None):
        try:
            d = self._api('/api/movie/play', {'id': int(id)})
            play_url = d.get('data', {}).get('play_url', '')
            if play_url:
                play_url = play_url.replace('preview=1', 'preview=0')
                return {
                    'parse': 0,
                    'url': play_url,
                    'header': self.play_headers,
                }
            return {'parse': 0, 'url': '', 'header': ''}
        except Exception:
            return {'parse': 0, 'url': '', 'header': ''}

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass

    def localProxy(self, param):
        return None
