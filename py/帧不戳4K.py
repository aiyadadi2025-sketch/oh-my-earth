#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
#  帧不戳4K · TVBox 聚合源 (drpy2 / dr_py py源)
# ----------------------------------------------------------------------------
#  功能:
#   1. 聚合 10 条公开影视采集线路(非凡/360/红牛/蓝采/金鹰/极速/天堂/电影天堂/CK/暴风)
#   2. 动态抓取各线路备用域名(官网首页提取) + 健康探测 + 本地缓存, 主域挂了自动切换
#   3. 首页分类正确(电影/剧集/综艺/动漫/短剧), 各源分类ID自动映射
#   4. 筛选器: 子分类 / 年份 / 地区 / 排序(聚合后前端筛选, 翻页稳定)
#   5. 列表聚合去重(同名同年只留一条), 下拉翻页正确(pagecount 取各源最大值)
#   6. 搜索: 全源并发搜索, 去重合并, 支持翻页
#   7. 详情: 保留该源所有播放线路, 并自动跨源匹配同名影片追加全站可用线路
#   8. 播放: 直接返回 m3u8 直链(parse=0), 无中间解析, 秒开
#   9. 全程并发请求 + 短超时, 页面加载速度优化
# ============================================================================
import os
import re
import io
import json
import time
import gzip
import hashlib
import ssl
import socket
import threading
import traceback
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from base.spider import Spider as _BaseSpider  # dr_py 环境基类(可选)
except Exception:
    class _BaseSpider(object):
        pass

# ---------------------------- 全局配置 ----------------------------
UA = ('Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36')
API_PATH = '/api.php/provide/vod'      # 苹果CMS 8/10 标准采集接口
T_LIST = 8.0                           # 列表/搜索超时(秒)
T_PROBE = 10.0                         # 探测超时(秒, 覆盖移动网络DNS慢)
_T_SEARCH = 8.0                        # 搜索单源超时(秒, 慢源兼容)
PAGE = 20                              # 每页条数(与源一致)
_CAT_DEPTH = 8                          # 每源分类最多拉取的服务器页数
_CAT_MAX = 320                          # 每分类缓存条目上限(虚拟分页)
CACHE_TTL = 6 * 3600                   # 健康域名缓存 6 小时
MAX_CONN = 12                          # 全局并发连接上限(防低配设备hang死)

socket.setdefaulttimeout(T_LIST)       # socket级兜底超时
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE
_SEMA = threading.BoundedSemaphore(MAX_CONN)

# ---------------------------- 线路定义 ----------------------------
# key: 内部标识 / name: 线路显示名 / prio: 优先级(小优先)
# base: 主接口 / roots: 官网域名(用于发现备用域名) / tags: 域名特征(动态发现过滤)
SOURCES = [
    dict(key='ffzy',   name='非凡', prio=1,
         base='https://api.ffzyapi.com' + API_PATH,
         roots=['http://ffzy5.tv/', 'https://www.ffzy.tv/'],
         hosts=['ffzy5.tv', 'ffzy1.tv', 'ffzy2.tv', 'ffzy3.tv', 'ffzy4.tv', 'www.ffzy.tv'],
         tags=['ffzy']),
    dict(key='s360',   name='360', prio=2,
         base='https://360zy.com' + API_PATH,
         roots=['https://360zy.com/'],
         hosts=['360zy5.com', '360zy2.com', '360zy1.com', '360zy.net', '360zy.top',
                '360zy.tv', '360zy.vip', '360zy3.com', '360zy4.com', '360zy6.com'],
         tags=['360zy']),
    dict(key='hongniu', name='红牛', prio=3,
         base='https://hongniuzy2.com' + API_PATH,
         roots=['https://hongniuzy2.com/'],
         hosts=['hongniuzy3.com', 'hongniuziyuan.com', 'www.hongniuzy2.com', 'hongniuzy.com'],
         tags=['hongniu']),
    dict(key='lzi',    name='蓝采', prio=4,
         base='https://cj.lziapi.com' + API_PATH,
         roots=['https://cj.lziapi.com/'],
         hosts=['cj.lziapi.com'],
         tags=['lzi', 'lzyapi']),
    dict(key='jinying', name='金鹰', prio=5,
         base='https://jinyingzy.com' + API_PATH,
         roots=['https://jinyingzy.com/'],
         hosts=['jyzy1.com', 'jyzy2.com', 'jyzy3.com', 'jinyingzy.net'],
         tags=['jinying', 'jyzy']),
    dict(key='jszy',   name='极速', prio=6,
         base='https://jszyapi.com' + API_PATH,
         roots=['https://jszyapi.com/'],
         hosts=['jszyapi.com'],
         tags=['jszy']),
    dict(key='ttzy',   name='天堂', prio=7,
         base='https://tyyszy.com' + API_PATH,
         roots=['https://tyyszy.com/'],
         hosts=['tyyszy1.com', 'www.tyyszy.com', 'tyyszyapi.com', 'tyyszywjx.com'],
         tags=['tyys']),
    dict(key='dytt',   name='天堂电影', prio=8,
         base='https://caiji.dyttzyapi.com' + API_PATH,
         roots=['https://caiji.dyttzyapi.com/'],
         hosts=['caiji.dyttzyapi.com', 'dyttzyapi.com'],
         tags=['dytt']),
    dict(key='ckzy',   name='CK', prio=9,
         base='https://ckzy1.com' + API_PATH,
         roots=['https://ckzy1.com/'],
         hosts=['ckzy.me', 'ckzy3.com', 'www.ckzy1.com'],
         tags=['ckzy']),
    dict(key='bfzy',   name='暴风', prio=10,
         base='https://bfzyapi.com' + API_PATH,
         roots=['https://bfzyapi.com/'],
         hosts=['bfzyapi.com'],
         tags=['bfzy']),
    dict(key='kuaiche', name='快车', prio=11,
         base='https://caiji.kuaichezy.org' + API_PATH,
         roots=['https://caiji.kuaichezy.org/'],
         hosts=['caiji.kuaichezy.org', 'www.kuaichezy.org'],
         tags=['kuaiche']),
]
SRC_BY_KEY = {s['key']: s for s in SOURCES}
# 线路(源前缀)->Referer 映射, 保证各线路 m3u8 直链能带上正确 Referer 拉流
_REFERER = {
    '非凡': 'https://www.ffzy.tv/',
    '360': 'https://360zy.com/',
    '红牛': 'https://hongniuzy2.com/',
    '蓝采': 'https://cj.lziapi.com/',
    '金鹰': 'https://jinyingzy.com/',
    '极速': 'https://jszyapi.com/',
    '天堂': 'https://tyyszy.com/',
    '天堂电影': 'https://caiji.dyttzyapi.com/',
    'CK': 'https://ckzy1.com/',
    '暴风': 'https://bfzyapi.com/',
    '快车': 'https://caiji.kuaichezy.org/',
}
def _line_rank(fl, eps=''):
    """播放线路排序: 4K/超清最优先(线路名或集数URL含4K均算), 其次直接m3u8, 其余靠后"""
    fl_l = str(fl or '').lower()
    u_l = str(eps or '').lower()
    is4k = ('4k' in fl_l or 'uhd' in fl_l or '2160' in fl_l
            or '超清' in fl or '蓝光' in fl
            or '/4k/' in u_l or '4k' in u_l or '2160' in u_l)
    ism3 = 'm3u8' in fl_l
    if is4k and ism3:
        return 0
    if is4k:
        return 1
    if ism3:
        return 2
    return 3

# ---------------------------- 多多影视真4K源 ----------------------------
# 站点: 323433ssdfd.top (web API + protobuf decode, 协议逆向自已验证的公开py源)
# 实测(2026-08, ffprobe): 4K电影类目 CO4K线路为 HEVC 3840 真4K; 电视剧类目 CO4K 仅1280x534, 不打4K标
_DD_HOST = 'https://323433ssdfd.top'

# 占位海报: 原zbc4k.app/app_icon.png已404, 换百度官方PNG(实测200/15KB, 国内CDN稳定)
_DD_PIC_FALLBACK = 'https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png'
_DD_DEC_CACHE = {}   # decode结果缓存: 'from|raw' -> (url, ts)
_DD_DEC_LOCK = threading.Lock()

def _pic_fix(p):
    """海报URL修复: image.baidu.com/search/down 代理已实测死亡(200空体),
    还原出底层URL; 底层为豆瓣图时壳加载器418拿不到, 置空交给占位/借图."""
    p = str(p or '').strip()
    if not p:
        return ''
    if 'image.baidu.com/search/down' in p:
        try:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(p).query)
            u = (q.get('url') or [''])[0]
            if u.startswith('http'):
                p = u
        except Exception:
            pass
    if '.doubanio.com/' in p or '.douban.com/' in p:
        return ''
    return p
_DD_HEADERS = {
    'User-Agent': UA,
    'Accept': 'application/json',
    'X-Client': '8f3d2a1c7b6e5d4c9a0b1f2e3d4c5b6a',
    'web-sign': 'ddtvf65f3a83d6d9ad6f',
    'Referer': _DD_HOST + '/',
    'Origin': _DD_HOST,
}
_DD_FINGER = 'WF-2c064bc5b3400788f31b848849bc3a60f835423ba2dfe69d7ea93974c216e4f2'
_DD_AID = 'com.web.player'
_DD_SK = 'WEB-50a8e9c84a1dc05669a692ded99a2dac46527229e607a7be15db88dbc59059d1'
_DD_TIMEOUT = 10.0

def _dd_http(url, data=None, headers=None, timeout=_DD_TIMEOUT):
    hdrs = dict(_DD_HEADERS)
    if headers:
        hdrs.update(headers)
    try:
        with _SEMA:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            r = urllib.request.urlopen(req, timeout=timeout, context=_SSL)
            raw = r.read()
            try:
                r.close()
            except Exception:
                pass
        if raw[:2] == b'\x1f\x8b':
            try:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            except Exception:
                pass
        return raw
    except Exception:
        return b''

def _pb_varint(value):
    out = bytearray()
    if value < 0:
        value += (1 << 64)
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)

def _pb_str(field, s):
    b = s.encode('utf-8') if isinstance(s, str) else s
    return _pb_varint((field << 3) | 2) + _pb_varint(len(b)) + b

def _pb_int(field, v):
    return _pb_varint((field << 3) | 0) + _pb_varint(int(v))

def _pb_read_varint(buf, pos):
    result, shift = 0, 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos

def _pb_decode(buf):
    fields, pos = {}, 0
    while pos < len(buf):
        tag, pos = _pb_read_varint(buf, pos)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            fields[fn], pos = _pb_read_varint(buf, pos)
        elif wt == 2:
            ln, pos = _pb_read_varint(buf, pos)
            fields[fn] = buf[pos:pos + ln]
            pos += ln
        elif wt == 5:
            fields[fn] = buf[pos:pos + 4]
            pos += 4
        elif wt == 1:
            fields[fn] = buf[pos:pos + 8]
            pos += 8
        else:
            break
    return fields

def _dd_sig(nonce, ts):
    s = 'finger=%s&id=%s&nonce=%s&sk=%s&time=%s&v=1' % (
        _DD_FINGER, _DD_AID, nonce, _DD_SK, ts)
    return hashlib.sha256(s.encode('utf-8')).hexdigest().upper()

def _dd_decode_url(vod_from, raw):
    """decode缓存壳: 30分钟缓存+单飞(详情页预热后播放零等待); 实现在_dd_decode_url_raw"""
    k = '%s|%s' % (vod_from, raw)
    now = time.time()
    c = _DD_DEC_CACHE.get(k)
    if c and now - c[1] < 1800:
        return c[0]
    with _DD_DEC_LOCK:
        c = _DD_DEC_CACHE.get(k)
        if c and now - c[1] < 1800:
            return c[0]
        u = _dd_decode_url_raw(vod_from, raw)
        if u:
            _DD_DEC_CACHE[k] = (u, time.time())
        return u

def _dd_decode_url_raw(vod_from, raw):
    """多多 /api.php/web/decode/url: SHA256签名+protobuf, 返回真实播放地址或''"""
    try:
        ts = int(time.time() * 1000)
        nonce = os.urandom(16).hex()
        pb = (_pb_str(1, raw) + _pb_str(2, vod_from) + _pb_int(3, ts) +
              _pb_str(4, nonce) + _pb_str(5, _dd_sig(nonce, ts)) +
              _pb_str(6, _DD_AID) + _pb_int(7, 1))
        raw = _dd_http(_DD_HOST + '/api.php/web/decode/url', data=pb, headers={
            'Content-Type': 'application/x-protobuf',
            'Accept': 'application/x-protobuf'}, timeout=_DD_TIMEOUT)
        if not raw:
            return ''
        f = _pb_decode(raw)
        if f.get(1, 0) != 1:
            return ''
        for k in sorted(f):
            v = f[k]
            if isinstance(v, bytes) and b'http' in v:
                return v[v.index(b'http'):].decode('utf-8', 'ignore').rstrip('\x00')
    except Exception:
        return ''
    return ''

# ---------------------------- 分类体系 ----------------------------
# 一级分类(家庭): key -> 显示名; 二级子分类用于筛选器
FAMILIES = [('movie', '电影'), ('tv', '电视剧'),
            ('variety', '综艺'), ('anime', '动漫'), ('short', '短剧'),
            ('live', '直播')]
FAM_NAME = dict(FAMILIES)
FAM_DEFAULT_TID = {'movie': 1, 'tv': 2, '4k': None, 'variety': 3, 'anime': 4, 'short': None}
SUBS = {
    'movie':   ['动作片', '喜剧片', '爱情片', '科幻片', '恐怖片', '剧情片',
                '战争片', '犯罪片', '奇幻片', '悬疑片', '古装片', '纪录片'],
    'tv':      ['国产剧', '港台剧', '欧美剧', '日本剧', '韩国剧', '泰国剧', '海外剧'],
    '4k':      ['4K电影'],
    'variety': ['大陆综艺', '港台综艺', '日韩综艺', '欧美综艺'],
    'anime':   ['国产动漫', '日韩动漫', '欧美动漫', '港台动漫'],
    'short':   ['短剧', '女频恋爱', '反转爽剧', '古装仙侠', '年代穿越',
                '脑洞悬疑', '现代都市'],
    'live':    [],
}
FILTER_YEARS = ['2026', '2025', '2024', '2023', '2022', '2021', '2020',
                '2019', '2018', '2017', '2016', '2015']
FILTER_AREAS = ['大陆', '香港', '台湾', '美国', '韩国', '日本', '英国',
                '泰国', '法国', '德国', '印度', '其它']

# ---------------------------- 直播(剧下饭源) ----------------------------
# 复用剧下饭4K的直播管线: AES-256-ECB签名 + 多域名轮换.
# liveVideo接口给出频道组(带台标videoPic), 组内episodes=具体频道;
# analysisUrl 在点播时实时解析出真实直播流(token有时效, 不可预解析).

def _jxf_build_sbox():
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q ^= 0x09 if (q & 0x80) else 0
        q &= 0xFF
        xformed = q ^ ((q << 1) | (q >> 7)) & 0xFF
        xformed = (xformed ^ ((q << 2) | (q >> 6))) & 0xFF
        xformed = (xformed ^ ((q << 3) | (q >> 5))) & 0xFF
        xformed = (xformed ^ ((q << 4) | (q >> 4))) & 0xFF
        sbox[p] = xformed ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox

_JXF_SBOX = _jxf_build_sbox()
_JXF_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D]

def _jxf_xtime(a):
    return ((a << 1) ^ (0x1B if a & 0x80 else 0)) & 0xFF

_JXF_COL2 = [_jxf_xtime(i) for i in range(256)]
_JXF_COL3 = [_jxf_xtime(i) ^ i for i in range(256)]

def _jxf_expand_key(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        temp = list(w[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_JXF_SBOX[b] for b in temp]
            temp[0] ^= _JXF_RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            temp = [_JXF_SBOX[b] for b in temp]
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    return w, nr

def _jxf_encrypt_block(block, w, nr):
    def _rk(r):
        return [x for word in w[r * 4:(r * 4) + 4] for x in word]
    s = list(block)
    rk0 = _rk(0)
    for j in range(16):
        s[j] ^= rk0[j]
    for r in range(1, nr):
        s = [_JXF_SBOX[b] for b in s]
        s = [s[0], s[5], s[10], s[15],
             s[4], s[9], s[14], s[3],
             s[8], s[13], s[2], s[7],
             s[12], s[1], s[6], s[11]]
        t = [0] * 16
        for c in range(4):
            i = c * 4
            a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
            t[i] = _JXF_COL2[a0] ^ _JXF_COL3[a1] ^ a2 ^ a3
            t[i + 1] = a0 ^ _JXF_COL2[a1] ^ _JXF_COL3[a2] ^ a3
            t[i + 2] = a0 ^ a1 ^ _JXF_COL2[a2] ^ _JXF_COL3[a3]
            t[i + 3] = _JXF_COL3[a0] ^ a1 ^ a2 ^ _JXF_COL2[a3]
        s = t
        rk = _rk(r)
        s = [s[j] ^ rk[j] for j in range(16)]
    s = [_JXF_SBOX[b] for b in s]
    s = [s[0], s[5], s[10], s[15],
         s[4], s[9], s[14], s[3],
         s[8], s[13], s[2], s[7],
         s[12], s[1], s[6], s[11]]
    rk = _rk(nr)
    s = [s[j] ^ rk[j] for j in range(16)]
    return bytes(s)

def _jxf_encrypt(plaintext):
    """PKCS7 + AES-256-ECB -> base64 (优先pycryptodome, 缺失时纯py回退)"""
    from base64 import b64encode as _b64e
    key = _JXF_AES_KEY
    data = plaintext.encode('utf-8') if isinstance(plaintext, str) else plaintext
    pad = 16 - len(data) % 16
    data = data + bytes([pad]) * pad
    try:
        from Crypto.Cipher import AES
        return _b64e(AES.new(key, AES.MODE_ECB).encrypt(data)).decode()
    except Exception:
        w, nr = _jxf_expand_key(key)
        out = bytearray()
        for i in range(0, len(data), 16):
            out += _jxf_encrypt_block(bytes(data[i:i + 16]), w, nr)
        return _b64e(bytes(out)).decode()


_JXF_BASES = [
    'http://manian.juxiafan.com', 'http://195.225.24.128:6213',
    'http://jugaoqing.com', 'http://jumianfei.com', 'http://juyongjiu.com',
    'http://kuailezhuiju2.com', 'http://zhuiju666.com', 'http://194.147.100.155:7744',
]
_JXF_AES_KEY = b'kZ6fT8oF6oM8eX6lF7eH2rJ3pW7gW0kC'
_JXF_UA = 'okhttp/4.12.0'


def _jxf_http(method, url, body=None, content_type=None, timeout=10):
    headers = {'User-Agent': _JXF_UA}
    data = None
    if body is not None:
        data = body.encode('utf-8') if isinstance(body, str) else body
        headers['Content-Length'] = str(len(data))
    if content_type:
        headers['Content-Type'] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', 'replace')


def _jxf_post_form(path, params, timeout=10):
    """AES签名表单POST, 多域名轮换"""
    from urllib.parse import quote_plus
    ts = str(int(time.time()))
    params['timestamp'] = ts
    items = sorted(params.items())
    concat = '&'.join(k + '=' + quote_plus(str(v)) for k, v in items)
    ds = _jxf_encrypt(concat)
    form = '&'.join(k + '=' + quote_plus(str(v)) for k, v in items)
    form += '&datasign=' + quote_plus(ds)
    last = '{}'
    for b in _JXF_BASES:
        try:
            last = _jxf_http('POST', b + path, form, 'application/x-www-form-urlencoded')
            if '"code"' in last:
                return last
        except Exception:
            continue
    return last


def _jxf_get(path, timeout=10):
    last = '{}'
    for b in _JXF_BASES:
        try:
            last = _jxf_http('GET', b + path, timeout=timeout)
            if '"code"' in last:
                return last
        except Exception:
            continue
    return last


def _jxf_analysis(source_code, code):
    """analysisUrl: 直播加密码 -> 真实直播流地址"""
    if not source_code or not code:
        return ''
    try:
        r = _jxf_post_form('/api/v1/player/analysisUrl',
                           {'from': source_code, 'code': code})
        u = (json.loads(r).get('data') or '')
        if str(u).startswith('http'):
            return str(u)
    except Exception:
        pass
    return ''


_JXF_PROBE_CACHE = {}
_LIVE_BLOCK = {'重庆新闻', '长沙女性'}  # 用户点名的不可播频道黑名单(服务端有流但真机播不了)
_PROBE_LOCK = threading.Lock()
_JXF_PROBE_FILE = ''
_JXF_PROBE_SAVED = [0.0]


def _probe_store_load():
    """判流结果持久化: 壳重启后零等待(读盘恢复缓存)"""
    global _JXF_PROBE_FILE
    try:
        _JXF_PROBE_FILE = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '.jxf_probe_cache.json')
        with open(_JXF_PROBE_FILE, 'r', encoding='utf-8') as f:
            for k, v in (json.loads(f.read() or '{}')).items():
                try:
                    _JXF_PROBE_CACHE[k] = (float(v[0]), bool(v[1]))
                except Exception:
                    pass
    except Exception:
        pass


def _probe_store_save():
    try:
        if not _JXF_PROBE_FILE:
            return
        tmp = _JXF_PROBE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({k: [v[0], v[1]] for k, v in _JXF_PROBE_CACHE.items()}, f)
        os.replace(tmp, _JXF_PROBE_FILE)
    except Exception:
        pass


_probe_store_load()


def _jxf_probe(source_code, code, timeout=8):
    """服务端判流: analysisUrl返回空=服务端无此频道流(与播放端网络无关,判死可靠);
    结果缓存12小时+落盘持久化(壳重启零等待)"""
    if not code:
        return False
    k = '%s|%s' % (source_code or '', code)
    now = time.time()
    c = _JXF_PROBE_CACHE.get(k)
    if c and now - c[0] < 43200:
        return c[1]
    try:
        r = _jxf_post_form('/api/v1/player/analysisUrl',
                           {'from': source_code or 'zhibo', 'code': code},
                           timeout=timeout)
        ok = str((json.loads(r).get('data') or '')).startswith('http')
    except Exception:
        ok = False
    with _PROBE_LOCK:
        _JXF_PROBE_CACHE[k] = (now, ok)
        if time.time() - _JXF_PROBE_SAVED[0] > 3:
            _JXF_PROBE_SAVED[0] = time.time()
            _probe_store_save()
    return ok


def _jxf_live_groups():
    """liveVideo频道组(带台标), 10分钟缓存"""
    now = time.time()
    c = _JXF_LIVE_CACHE.get('g')
    if c and now - c[0] < 600:
        return c[1]
    try:
        r = _jxf_get('/api/v1/video/liveVideo')
        data = json.loads(r).get('data') or {}
        out = []
        for v in data.get('list') or []:
            if not v.get('id') or not v.get('name'):
                continue
            out.append({'id': v['id'], 'name': v['name'],
                        'pic': str(v.get('videoPic') or ''),
                        'remarks': str(v.get('remarks') or '')})
        if out:
            _JXF_LIVE_CACHE['g'] = (now, out)
            return out
    except Exception:
        pass
    return c[1] if c else []


def _jxf_video_detail(vid):
    """videoDetails详情(5分钟缓存): playerSource内episodes=频道列表"""
    k = str(vid)
    now = time.time()
    c = _JXF_DETAIL_CACHE.get(k)
    if c and now - c[0] < 300:
        return c[1]
    try:
        r = _jxf_post_form('/api/v1/video/videoDetails', {'id': str(vid)})
        d = json.loads(r).get('data') or {}
        if d:
            _JXF_DETAIL_CACHE[k] = (now, d)
            return d
    except Exception:
        pass
    return c[1] if c else {}


_JXF_LIVE_CACHE = {}
_JXF_DETAIL_CACHE = {}


# ---------------- 直播补充(公开IPTV源, 增加频道种类) ----------------
_IPTV_URLS = [
    'https://raw.githubusercontent.com/suxuang/myIPTV/main/ipv4.m3u',
    'https://ghproxy.net/https://raw.githubusercontent.com/suxuang/myIPTV/main/ipv4.m3u',
    'https://cdn.jsdelivr.net/gh/suxuang/myIPTV@main/ipv4.m3u',
]
_IPTV_CACHE = {}
def _logo_enc(u):
    """台标URL含中文路径时percent编码(壳图片加载器不编码必404默认图)"""
    u = (u or '').strip()
    if u and not u.isascii():
        try:
            from urllib.parse import quote as _uq
            p = u.split('://', 1)
            u = (p[0] + '://' + _uq(p[1], safe="/?:#&=%+.~!$'()*,")) if len(p) == 2 \
                else _uq(u, safe="/?:#&=%+.~!$'()*,")
        except Exception:
            pass
    return u

_LIVE_BG = {'running': False, 't0': 0.0}  # 直播后台全量构建状态(防重复触发)

# IPTV组优先级(探活预算有限, 优先探测主流组)
_GRP_ORDER = {'央视频道': 0, '央视高清': 1, '卫视频道': 2, '咪咕央视': 3,
              '4K频道': 4, '数字频道': 5, '咪咕赛事': 6, '港澳台频道': 7,
              'NewTV频道': 8, 'IHOT频道': 9, 'APTV专享': 10, '国际频道': 11,
              '动画频道': 12, '电视剧频道': 13, '春晚频道': 14, '备用频道': 15,
              '埋堆堆频道': 16, '虎牙影视': 17, '斗鱼影视': 18, '点播电影': 19,
              '移动IPTV': 20, '港澳代理': 21, '其他频道': 22, 'IPTV专享': 23}


def _b64u(s):
    from base64 import urlsafe_b64encode as _e
    return _e(str(s).encode('utf-8')).decode('ascii')


def _b64ud(s):
    from base64 import urlsafe_b64decode as _d
    t = str(s).strip()
    t += '=' * (-len(t) % 4)
    return _d(t.encode('ascii')).decode('utf-8', 'replace')


def _iptv_channels():
    """公开IPTV频道表(m3u, 20分钟缓存) -> [{name,url,grp,pic}]
    作为剧下饭直播的补充: 卫视/地方/港澳台/体育/纪实/少儿等公开信号."""
    now = time.time()
    c = _IPTV_CACHE.get('c')
    if c and now - c[0] < 1200:
        return c[1]
    txt = ''
    for u in _IPTV_URLS:
        try:
            txt = _http(u, timeout=8)
        except Exception:
            txt = ''
        if txt and '#EXTINF' in txt:
            break
        txt = ''
    if not txt:
        return c[1] if c else []
    out = []
    name = grp = pic = ''
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXTINF'):
            m = re.search(r'tvg-logo="([^"]*)"', line)
            pic = _logo_enc((m.group(1) if m else '').strip())
            g = re.search(r'group-title="([^"]*)"', line)
            grp = (g.group(1) if g else '').strip()
            # 兼容两种格式: "#EXTINF:-1 属性...,频道名" / "#EXTINF:-1,属性...,频道名"
            seg = line.split(':', 1)[1] if ':' in line else ''
            seg = re.sub(r'^\s*[-\d.]+\s*,?\s*', '', seg)
            if re.search(r'(?:tvg-|group-title)=', seg) and ',' in seg:
                name = seg.rsplit(',', 1)[1].strip()
            else:
                name = seg.strip()
        elif line.startswith(('http://', 'https://')) and name:
            out.append({'name': name, 'url': line, 'grp': grp, 'pic': pic})
            name, grp, pic = '', '', ''
    if out:
        _IPTV_CACHE['c'] = (now, out)
        return out
    return c[1] if c else []


LIVE_M3U_UA = 'okhttp/3.15.0'


def _live_url_ok(u, timeout=4):
    """直播流快测: 连通+内容形态校验(必须m3u8/视频流, 剔除FLV/403页/过期页假流)"""
    try:
        import ssl as _ssl
        from urllib.parse import quote as _q
        req = urllib.request.Request(_q(u, safe="/?:=&%.:_-~"),
                                     headers={'User-Agent': LIVE_M3U_UA})
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl._create_unverified_context()) as r:
            ct = (r.headers.get('Content-Type') or '').lower()
            b = r.read(300)
            if not b:
                return False
            if 'mpegurl' in ct or b.lstrip()[:7] == b'#EXTM3U':
                return True  # m3u8 播放列表
            if 'video/' in ct or ct.startswith('audio/') or 'mp2t' in ct:
                return True  # 视频形态(ts/mp4/flv均可播, 实测虎牙FLV壳内正常播放)
            if 'text/' in ct or 'html' in ct or 'json' in ct:
                return False  # 403页/错误页/佛祖页等文本响应
            return b.lstrip()[:4] in (b'\x00\x00\x00\x18', b'\x00\x00\x00\x20',
                                      b'\x00\x00\x00\x1c', b'\x00\x00\x00\x14',
                                      b'\x1aE\xdf\xa3')  # ftyp盒(常见长度)/matroska
    except Exception:
        return False


def _live_type_rank(name):
    """频道排序: 央视(按台号1,2,3...数字序)>卫视>港澳台>纪实教育>其他(地方/轮播)"""
    n = str(name or '')
    if re.search(r'CCTV|CGTN|央视', n, re.I):
        m = re.search(r'(\d+)', n)
        r = (0, int(m.group(1)) if m else 99)  # CCTV1->1, CCTV10->10 数字序
    elif '卫视' in n:
        r = (1, 0)
    elif re.search(r'香港|凤凰|TVB|澳门|台湾', n, re.I):
        r = (2, 0)
    elif re.search(r'纪实|教育|纪录|少儿|卡通|动漫', n):
        r = (3, 0)
    else:
        r = (4, 0)
    return (r, n)


_DOM_RE = re.compile(
    r'(?:[a-zA-Z0-9-]+\.){1,3}(?:com|net|cc|tv|xyz|top|vip|cn|me|org|app|site|info|pro|fun|icu|club)',
    re.I)


def _http(url, timeout=T_LIST, headers=None):
    """极简 HTTP GET: 支持gzip/自签证书, 信号量限流, 失败返回 ''"""
    hdrs = {'User-Agent': UA,
            'Accept': 'application/json, text/html;q=0.8, */*;q=0.5',
            'Accept-Encoding': 'gzip',
            'Connection': 'close'}
    if headers:
        hdrs.update(headers)
    try:
        with _SEMA:
            req = urllib.request.Request(url, headers=hdrs)
            r = urllib.request.urlopen(req, timeout=timeout, context=_SSL)
            raw = r.read()
            try:
                r.close()
            except Exception:
                pass
        if raw[:2] == b'\x1f\x8b':
            try:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            except Exception:
                pass
        return raw.decode('utf-8', 'ignore')
    except Exception:
        return ''


def _jload(txt):
    try:
        return json.loads(txt)
    except Exception:
        return None


def _num(v):
    try:
        return float(v)
    except Exception:
        return -1.0


def _tnorm(n):
    """归一化分类名: 去掉尾部 片/剧 等后缀便于跨源匹配"""
    return re.sub(r'(片|剧|综艺|动漫)$', '', (n or '').strip())


class Spider(_BaseSpider):
    name = '帧不戳4K'

    # ---------------- 生命周期 ----------------
    def __init__(self):
        self._lock = threading.RLock()
        self._ready = False
        self._bases = {}      # key -> 健康api基地址
        self._vid2nk = {}     # (key, vid) -> (name, year) 反查表
        self._classes = {}    # key -> [(type_id, type_name), ...]
        self._bad = {}        # key -> 失败时间戳(冷却)
        self._alt = {}        # (name, year) -> [(src_key, vid), ...]
        self._alt_order = []
        self._cache_file = self._pick_cache_file()
        self._cat_cache = {}    # fam -> 客户端过滤后的分类条目(虚拟分页)
        self._cat_progress = {}  # (fam,sub) -> 已拉取的服务器页数(懒加载游标)
        self._cat_pagecount = {}  # (fam,sub) -> 各源真实总页数(壳层翻页按钮依据)
        self._cat_lock = threading.RLock()

    # ---------- 缓存文件 ----------
    @staticmethod
    def _pick_cache_file():
        import tempfile
        for d in (tempfile.gettempdir(), os.getcwd(), '/tmp',
                  '/storage/emulated/0/Download'):
            try:
                p = os.path.join(d, '.drpy_zbc4k_cache.json')
                with open(p, 'a', encoding='utf-8'):
                    pass
                return p
            except Exception:
                continue
        return ''

    def _load_cache(self):
        if not self._cache_file:
            return False
        try:
            with open(self._cache_file, 'r', encoding='utf-8') as f:
                c = json.load(f)
            if time.time() - c.get('ts', 0) < CACHE_TTL and c.get('bases'):
                self._bases = dict(c['bases'])
                self._classes = {k: [tuple(x) for x in v]
                                 for k, v in (c.get('classes') or {}).items()}
                return True
        except Exception:
            pass
        return False

    def _save_cache(self):
        if not self._cache_file:
            return
        try:
            with open(self._cache_file, 'w', encoding='utf-8') as f:
                json.dump({'ts': time.time(),
                           'bases': self._bases,
                           'classes': {k: [list(x) for x in v]
                                       for k, v in self._classes.items()}},
                          f, ensure_ascii=False)
        except Exception:
            pass

    # ---------- 探测 ----------
    @staticmethod
    def _probe(base):
        j = _jload(_http(base + '?ac=list&pg=1', T_PROBE))
        if not j:
            return None
        cls = j.get('class')
        if not isinstance(cls, list) or not cls:
            return None
        out = []
        for c in cls:
            if isinstance(c, dict) and c.get('type_id') is not None:
                out.append((int(c.get('type_id')),
                            str(c.get('type_name', '')),
                            int(c.get('type_pid') or 0)))
        return out if out else None

    def _probe_any(self, cands):
        """并发探测候选接口, 命中即取消其余任务; 返回 (base, classes) 或 None"""
        if not cands:
            return None
        ex = ThreadPoolExecutor(max_workers=min(8, len(cands)))
        futs = {ex.submit(self._probe, c): c for c in cands}
        hit = None
        try:
            for f in as_completed(futs):
                r = f.result()
                if r:
                    hit = (futs[f], r)
                    for g in futs:
                        g.cancel()
                    break
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return hit

    def _api_candidates(self, src):
        """主接口 + 已知备用域名接口"""
        cands = [src['base']]
        for h in src.get('hosts', []):
            cands.append('https://' + h + API_PATH)
            cands.append('http://' + h + API_PATH)
        return cands

    def _discover_domains(self, src):
        """从官网首页动态抓取备用域名"""
        found = []
        for home in src.get('roots', []):
            html = _http(home, T_PROBE)
            if not html:
                continue
            for m in _DOM_RE.finditer(html):
                d = m.group().lower()
                ok = False
                for t in src['tags']:
                    if d.startswith(t) or ('.' + t) in d:
                        ok = True
                        break
                if ok and d not in found:
                    found.append(d)
            if found:
                break
        return found

    def _ensure_main(self, src):
        """仅探测主接口(域名健康, 快速不阻塞)"""
        key = src['key']
        if key in self._bases:
            return True
        j = self._probe(src['base'])
        if j:
            with self._lock:
                self._bases[key] = src['base']
                self._classes[key] = j
                self._bad.pop(key, None)
            return True
        return False

    def _ensure_src(self, src, force=False):
        """完整恢复: 主接口 → 已知备用域名 → 官网动态发现(仅后台调用)"""
        key = src['key']
        if not force and key in self._bases:
            return True
        now = time.time()
        if not force and now < self._bad.get(key, 0):
            return False
        # 1) 主接口
        if self._ensure_main(src):
            return True
        # 2) 已知备用域名并发探测(命中即取消)
        hit = self._probe_any(self._api_candidates(src)[1:])
        if hit:
            with self._lock:
                self._bases[key] = hit[0]
                self._classes[key] = hit[1]
                self._bad.pop(key, None)
            return True
        # 3) 从官网首页动态发现备用域名再探测
        doms = self._discover_domains(src)
        if doms:
            cands = [sch + d + API_PATH for d in doms[:8] for sch in ('https://', 'http://')]
            hit = self._probe_any(cands[:16])
            if hit:
                with self._lock:
                    self._bases[key] = hit[0]
                    self._classes[key] = hit[1]
                    self._bad.pop(key, None)
                return True
        self._bad[key] = now + 300  # 5分钟冷却
        return False

    def _bootstrap(self):
        """后台引导: 并发探测各源主接口, 就绪一个服务一个"""
        if self._load_cache() and len(self._bases) >= 3:
            threading.Thread(target=self._recheck_bg, daemon=True).start()
            return
        ex = ThreadPoolExecutor(max_workers=10)
        futs = [ex.submit(self._ensure_main, s) for s in SOURCES]
        try:
            for f in as_completed(futs):
                try:
                    f.result()
                except Exception:
                    pass
        except Exception:
            pass
        ex.shutdown(wait=False)
        self._save_cache()
        # 失败线路后台完整恢复(备用域名+动态发现)
        threading.Thread(target=self._recheck_bg, daemon=True).start()

    def _recheck_bg(self):
        for s in SOURCES:
            if s['key'] not in self._bases:
                try:
                    self._ensure_src(s)
                except Exception:
                    pass
        self._save_cache()

    def _start_boot(self):
        """启动后台引导(仅一次, init立即返回)"""
        with self._lock:
            if getattr(self, '_booted', False):
                return
            self._booted = True
        threading.Thread(target=self._bootstrap, daemon=True).start()

    def _wait_ready(self, min_n=1, timeout=25.0):
        """渐进等待: 有足够源就绪(或超时)即返回"""
        if len(self._bases) >= min_n:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self._bases) >= min_n:
                return True
            time.sleep(0.3)
        return len(self._bases) > 0

    def _ensure(self, min_n=1, timeout=25.0):
        self._start_boot()
        try:
            self._wait_ready(min_n, timeout)
        except Exception:
            pass

    def _healthy(self, min_prio=99, exclude=None, max_n=99):
        """返回当前健康线路(按优先级)"""
        out = []
        for s in sorted(SOURCES, key=lambda x: x['prio']):
            if s['prio'] > min_prio or s['key'] == exclude:
                continue
            if s['key'] in self._bases:
                out.append(s)
            if len(out) >= max_n:
                break
        return out

    # ---------------- 分类映射 ----------------
    def _resolve_tid(self, key, fam, sub=''):
        cls = self._classes.get(key) or []
        name = FAM_NAME.get(fam, '')
        if sub:
            for c in cls:
                if str(c[1]).strip() == sub:
                    return c[0]
            for c in cls:
                if _tnorm(c[1]) == _tnorm(sub):
                    return c[0]
            # 模糊: 关键词包含
            kw = _tnorm(sub)
            for c in cls:
                if kw and kw in _tnorm(c[1]):
                    return c[0]
            return None
        # 一级分类: 精确 → 前缀 → 默认id
        for c in cls:
            if str(c[1]).strip() == name:
                return c[0]
        for c in cls:
            if str(c[1]).strip().startswith(name):
                return c[0]
        return FAM_DEFAULT_TID.get(fam)
    def _sources_for(self, fam, sub='', exclude=None):
        out = []
        for s in self._healthy(exclude=exclude):
            t = self._resolve_tid(s['key'], fam, sub)
            if t:
                out.append(dict(s, t=t))
        return out
    @staticmethod
    def _fam_of_top(tn):
        tn = tn or ''
        if '短剧' in tn:
            return 'short'
        # 垃圾频道(体育/足球/篮球/预告/解说/资讯等): 隔离为junk, 不混入电影/剧集
        for kw in ('体育', '足球', '篮球', '赛事', '预告', '解说', '资讯', '新闻',
                   '花絮', '短片'):
            if kw in tn:
                return 'junk'
        # 短剧频道子分类(爽剧/女频/脑洞/闪婚/赘婿/古装仙侠/年代穿越/现代都市等)归short
        for kw in ('爽剧', '女频', '脑洞', '逆袭', '战神', '闪婚', '赘婿',
                   '古装仙侠', '年代穿越', '现代都市', '都市脑洞'):
            if kw in tn:
                return 'short'
        if '动漫' in tn or '动画' in tn:
            return 'anime'
        if '综艺' in tn:
            return 'variety'
        if '剧' in tn or '连续' in tn:
            return 'tv'
        return 'movie'
    def _top_fam_map(self, key):
        m = {}
        for c in (self._classes.get(key) or []):
            tid, tn = c[0], c[1]
            pid = c[2] if len(c) > 2 else 0
            if pid == 0:
                m[tid] = self._fam_of_top(tn)
        return m

    def _short_tids(self, key):
        """收集该源所有短剧类分类的 type_id (含'短剧'分类及其子分类)"""
        if not hasattr(self, '_short_tid_map'):
            self._short_tid_map = {}
        if key in self._short_tid_map:
            return self._short_tid_map[key]
        tids = set()
        cls = self._classes.get(key) or []
        # 自身含"短剧"的分类 + "短剧"分类的子分类
        for c in cls:
            tid, tn, pid = c[0], c[1], (c[2] if len(c) > 2 else 0)
            if '短剧' in str(tn):
                tids.add(tid)
        for c in cls:
            tid, tn, pid = c[0], c[1], (c[2] if len(c) > 2 else 0)
            if pid in tids:
                tids.add(tid)
        self._short_tid_map[key] = tids
        return tids

    def _fam_of_item(self, src_key, item):
        tn = item.get('type_name') or ''
        # 0) 4K专区识别: type_name含"4K"(如"4K电影")归入4k家族
        if '4k' in tn.lower():
            return '4k'
        # 1) 名称识别: type_name 或顶层分类名含"短剧"
        if '短剧' in tn:
            return 'short'
        # 1.5) 名称优先于父类: 垃圾频道(体育/预告/解说/资讯等)与短剧频道子分类
        for kw in ('体育', '足球', '篮球', '赛事', '预告', '解说', '资讯', '新闻',
                   '花絮', '短片'):
            if kw in tn:
                return 'junk'
        for kw in ('爽剧', '女频', '脑洞', '逆袭', '战神', '闪婚', '赘婿',
                   '古装仙侠', '年代穿越', '现代都市', '都市脑洞'):
            if kw in tn:
                return 'short'
        # 2) ID识别: 该源的短剧分类ID集合(兜底名称不含"短剧"的情况)
        for tid_v in (item.get('tid'), item.get('type_id_1')):
            if tid_v is not None:
                try:
                    if int(tid_v) in self._short_tids(src_key):
                        return 'short'
                except Exception:
                    pass
        t1 = item.get('type_id_1')
        if t1 is not None:
            try:
                fam = self._top_fam_map(src_key).get(int(t1))
                if fam:
                    return fam
            except Exception:
                pass
        # type_id_1缺失: 按该源顶层分类名兜底(体育/预告→junk, 爽剧/女频等→short)
        try:
            tid_v = int(item.get('tid'))
        except Exception:
            tid_v = None
        for c in (self._classes.get(src_key) or []):
            if c[0] == tid_v and (len(c) < 3 or not c[2]):
                return self._fam_of_top(c[1])
        return self._fam_of_top(item.get('type_name'))
    def _fam_available(self, fam):
        # 直播数据来自内嵌IPTV频道表, 与点播源无关, 恒可用
        if fam == 'live':
            return True
        # 顶层映射优先
        for key in self._bases:
            if fam in self._top_fam_map(key).values():
                return True
        # 短剧/4K常挂在电视剧/电影下(非顶层), 只要某源存在该类型即视为可用
        kw = '短剧' if fam == 'short' else ('4k' if fam == '4k' else None)
        if kw:
            for key in self._bases:
                for c in (self._classes.get(key) or []):
                    if kw in str(c[1]).lower():
                        return True
        return False

    # ---------------- 列表解析 ----------------
    @staticmethod
    def _norm_item(key, v):
        vid = v.get('vod_id')
        if vid is None:
            return None
        name = str(v.get('vod_name') or '').strip()
        if not name:
            return None
        return {
            'vid': str(vid),
            'name': name,
            'pic': str(v.get('vod_pic') or v.get('vod_pic_thumb') or '').strip(),
            'remarks': str(v.get('vod_remarks') or '').strip(),
            'year': str(v.get('vod_year') or '').strip(),
            'area': str(v.get('vod_area') or '').strip(),
            'score': str(v.get('vod_douban_score') or v.get('vod_score') or '').strip(),
            'hits': str(v.get('vod_hits') or '').strip(),
            'time': str(v.get('vod_time') or '').strip(),
            'type_name': str(v.get('type_name') or '').strip(),
            'type_id_1': v.get('type_id_1'),
            'tid': v.get('type_id'),
        }

    def _fetch_list(self, src, pg, t=None, wd=None, timeout=T_LIST, ac='videolist'):
        """ac=videolist: 列表/搜索用(快且稳); ac=detail: 详情/播放用.
        实测部分源 ac=detail 搜索超时(极速/暴风), 搜索统一走 videolist.
        t 支持 tuple: 依次拉多个叶子分类合并(顶层大类服务端过滤用)."""
        if isinstance(t, tuple):
            items, tot, pc = [], 0, 0
            for t1 in t[:2]:
                il, tot1, pc1 = self._fetch_list(src, pg, t=t1, wd=wd,
                                                 timeout=timeout, ac=ac)
                items.extend(il)
                tot = max(tot, tot1)
                pc = max(pc, pc1)
            return items, tot, pc
        base = self._bases.get(src['key'])
        if not base:
            return [], 0, 0
        q = ['ac=%s' % ac, 'pg=%d' % pg]
        if t:
            q.append('t=%s' % t)
        if wd:
            q.append('wd=' + urllib.parse.quote(wd))
        url = base + '?' + '&'.join(q)
        j = _jload(_http(url, timeout))
        if not j:
            return [], 0, 0
        items = []
        for v in (j.get('list') or []):
            if not isinstance(v, dict):
                continue
            it = self._norm_item(src['key'], v)
            if it:
                it['id'] = '%s__%s' % (src['key'], it['vid'])
                items.append(it)
        return items, int(j.get('total') or 0), int(j.get('pagecount') or 0)

    @staticmethod
    def _merge(items_by_prio):
        """按优先级合并去重: (name, year) 唯一; 记录跨源候选"""
        merged, alts = {}, {}
        for prio, key, items in items_by_prio:
            for it in items:
                nk = (it['name'], it['year'])
                if nk not in merged:
                    merged[nk] = it
                alts.setdefault(nk, [])
                if all(k != key for k, _ in alts[nk]):
                    alts[nk].append((key, it['vid']))
        return list(merged.values()), alts

    def _store_alt(self, alts):
        with self._lock:
            for nk, lst in alts.items():
                self._alt[nk] = lst
                self._alt_order.append(nk)
                # 反查表: (key, vid) -> (name, year), 供详情跨源救援用
                for k, v in lst:
                    try:
                        self._vid2nk[(k, v)] = nk
                    except Exception:
                        pass
            # 上限保护
            while len(self._alt_order) > 3000:
                old = self._alt_order.pop(0)
                self._alt.pop(old, None)

    # ---------------- TVBox drpy2 标准接口 ----------------
    def getName(self):
        return '帧不戳4K·3.11'

    def init(self, extend=""):
        try:
            self._ensure()
        except Exception:
            traceback.print_exc()
        try:
            # 壳启动即后台预热直播探活缓存: 用户进直播时列表已清洗完毕(秒出)
            if not getattr(self, '_warm_started', False):
                self._warm_started = True

                def _warm():
                    try:
                        self._live_flat()
                    except Exception:
                        pass
                threading.Thread(target=_warm, daemon=True).start()
        except Exception:
            pass
        return ''

    def _catalog(self):
        """分类目录: (tid, 显示名, fam, sub).
        仿糯米影视.py 的组织方式: 纪录片独立成一级分类, 且把所有子分类也作为可直接点击的分类标签展示.
        直播无子分类(用户要求): 点开即全量频道列表."""
        if getattr(self, '_catalog_cache', None):
            return self._catalog_cache
        cat = []
        for fam, fname in FAMILIES:
            cat.append((fam, fname, fam, ''))
        cat.append(('documentary', '纪录片', 'movie', '纪录片'))
        for fam, subs in SUBS.items():
            for sub in subs:
                if sub == '纪录片':
                    continue
                if sub == FAM_NAME.get(fam):
                    continue  # 与顶层同名的子分类跳过(顶层"短剧"≈子分类"短剧"), 避免首页重复
                cat.append(('%s_%s' % (fam, sub), sub, fam, sub))
        self._catalog_cache = cat
        return cat

    def _cat_lookup(self, tid):
        for t, disp, fam, sub in self._catalog():
            if t == tid:
                return fam, sub
        return 'movie', ''

    def homeContent(self, filter):
        self._ensure()
        classes, filters = [], {}
        for tid, disp, fam, sub in self._catalog():
            if not self._fam_available(fam):
                continue
            if fam == 'live':
                # 直播无子分类无筛选器(用户要求): 点开即全部频道
                classes.append({'type_id': tid, 'type_name': disp})
                continue
            if sub and not any(self._resolve_tid(s['key'], fam, sub)
                               for s in self._healthy()):
                continue  # 该子分类无任何源支持则跳过
            classes.append({'type_id': tid, 'type_name': disp})
            if sub:
                continue  # 子分类标签本身就是内容, 无需筛选器
            # 顶层分类筛选器(子分类/年份/地区/排序)
            sub_vals = [{'n': '全部', 'v': ''}]
            seen = set()
            for sb in SUBS.get(fam, []):
                for s in self._healthy():
                    if self._resolve_tid(s['key'], fam, sb):
                        if sb not in seen:
                            seen.add(sb)
                            sub_vals.append({'n': sb, 'v': sb})
                        break
            fl = []
            if len(sub_vals) > 1:
                fl.append({'key': 'type', 'name': '分类', 'value': sub_vals})
            fl.append({'key': 'year', 'name': '年份',
                       'value': [{'n': '全部', 'v': ''}] +
                                [{'n': y, 'v': y} for y in FILTER_YEARS]})
            fl.append({'key': 'area', 'name': '地区',
                       'value': [{'n': '全部', 'v': ''}] +
                                [{'n': a, 'v': a} for a in FILTER_AREAS]})
            fl.append({'key': 'sort', 'name': '排序',
                       'value': [{'n': '更新时间', 'v': 'time'},
                                 {'n': '人气', 'v': 'hits'},
                                 {'n': '评分', 'v': 'score'}]})
            filters[tid] = fl
        return {'class': classes, 'filters': filters}

    def homeVideoContent(self):
        self._ensure()
        srcs = self._healthy(max_n=5)
        got = []
        ex = ThreadPoolExecutor(max_workers=5)
        futs = {ex.submit(self._fetch_list, s, 1): s for s in srcs}
        try:
            for f in as_completed(futs):
                try:
                    items, _, _ = f.result()
                except Exception:
                    continue
                prio = futs[f]['prio']
                got.append((prio, futs[f]['key'], items))
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        got.sort(key=lambda x: x[0])
        items, alts = self._merge(got)
        self._store_alt(alts)
        items.sort(key=lambda v: v.get('time') or '', reverse=True)
        return {'list': [self._to_card(it) for it in items[:30]]}

    @staticmethod
    def _to_card(it):
        pic = it.get('pic') or ''
        pool = getattr(Spider, '_pic_pool', None)
        # 坏图(百度代理/豆瓣, _pic_fix已置空)或空图: 跨源同名条目借真海报
        if not pic and pool and it['id'].startswith('__dd__'):
            nm = it.get('name') or ''
            best = pool.get(nm) or pool.get(re.sub(r'\s*[4ｋKＫ].*$', '', nm).strip())
            if best:
                pic = best
        if not pic:
            pic = _DD_PIC_FALLBACK
        return {'vod_id': it['id'], 'vod_name': it['name'],
                'vod_pic': pic, 'vod_remarks': it['remarks'],
                'vod_year': it['year'], 'vod_area': it['area']}

    def _cat_ensure(self, fam, sub, upto):
        """懒加载+增量: 确保某分类已拉取到 upto页(每源), 超出部分按需补拉并缓存.
        首次调用只拉1页/源(约10请求), 秒出第一页; 翻页时再补拉, 避免TVBox超时."""
        key = (fam, sub)
        all_srcs = self._healthy()
        if not all_srcs:
            # 冷启动兜底: 用户刚进壳就点分类时源尚未就绪. 顶层大类等3个源再选源
            # (服务端过滤依赖分类表, 拿1个半成品源泛拉全站只会空转12s出0条), 上限5s;
            # 子分类翻页通常发生在用户已浏览期间, 最多等3s
            if sub:
                self._wait_ready(min_n=1, timeout=3.0)
            elif fam in ('movie', 'tv', 'variety', 'anime', 'short', '4k'):
                self._wait_ready(min_n=3, timeout=5.0)
            else:
                self._wait_ready(min_n=1, timeout=5.0)
            all_srcs = self._healthy()
        if not all_srcs:
            return self._cat_cache.get(key, [])
        # 优先选能解析到该分类的源(4K/短剧等专道只存在于少数源), 最多6个
        srcs = []
        tid_by_src = {}
        for s in all_srcs:
            t0 = None
            if sub:
                if sub == '港台剧':
                    # _resolve_tid模糊匹配"港台剧"会错位到"港台综艺"(实测ffzy/lzi/dytt
                    # 等tid全命中综艺, 服务端返回综艺→复检全滤掉→分类恒空).
                    # 改用名称精确命中的叶子组合(香港剧+台湾剧等), 复用tuple服务端过滤链路.
                    _gt_names = ('香港剧', '台湾剧', '港澳剧', '港台剧')
                    _leaf = [c[0] for c in (self._classes.get(s['key']) or [])
                             if str(c[1]).strip() in _gt_names
                             and len(c) > 2 and int(c[2] or 0) > 0]
                    if _leaf:
                        t0 = tuple(_leaf[:2])
                if not t0:
                    t0 = self._resolve_tid(s['key'], fam, sub)
            elif fam == '4k':
                for c in (self._classes.get(s['key']) or []):
                    if '4k' in str(c[1]).lower():
                        t0 = c[0]
                        break
            elif fam in ('movie', 'tv', 'variety', 'anime', 'short'):
                # 顶层大类: 用2个叶子分类(如动作片+喜剧片)做服务端过滤.
                # 实测父级tid查询(如ffzy t=1)多家返回0条, 而叶子分类查询必出满页.
                # 客户端 `_fam_of_item` 复检保证归族准确, 不会串类.
                cls = self._classes.get(s['key']) or []
                tids = []
                for c in cls:
                    if self._fam_of_top(c[1]) == fam and len(c) > 2 and int(c[2] or 0) > 0:
                        tids.append(c[0])
                t0 = tuple(tids[:2])  # 空元组=该源无叶子分类, 走下方泛拉兜底
            if t0:
                srcs.append(s)
                tid_by_src[s['key']] = t0
            if len(srcs) >= 6:
                break
        if not srcs:
            srcs = all_srcs[:6]
        with self._cat_lock:
            done = self._cat_progress.get(key, 0)
            cached = self._cat_cache.get(key, [])
        if done >= upto:
            return cached
        # 增量拉取 done+1 .. upto 页(每源)
        # 子分类解析到服务端 t 则用 t 过滤(快且准, 首屏不再空), 未解析则generic+客户端过滤
        ex = ThreadPoolExecutor(max_workers=8)
        futs = {}
        for s in srcs:
            t = tid_by_src.get(s['key'])
            for pg in range(done + 1, upto + 1):
                futs[ex.submit(self._fetch_list, s, pg, t, timeout=_T_SEARCH)] = (s['prio'], s['key'])
        new_items = []  # (prio, key, item)
        target = max(1, (upto - done) * PAGE)  # 集满即提前返回
        deadline = time.time() + (upto - done) * 3.0 + 2.0
        max_pc = 0

        def _absorb(f):
            nonlocal max_pc
            try:
                il, _total, pc = f.result()
            except Exception:
                return
            if pc and pc > max_pc:
                max_pc = pc
            prio, k = futs[f]
            t_used = tid_by_src.get(k)
            for it in il:
                if self._fam_of_item(k, it) != fam:
                    continue
                if not t_used and sub and _tnorm(sub) not in _tnorm(it.get('type_name')) \
                        and sub not in (it.get('type_name') or ''):
                    continue
                it['id'] = '%s__%s' % (k, it['vid'])
                it['prio'] = prio
                it['key'] = k
                new_items.append((prio, k, it))

        try:
            for f in as_completed(futs):
                _absorb(f)  # 先吸收已完成的数据再判断, 避免冷启动慢网时全部白等
                if len(new_items) >= target or (new_items and time.time() > deadline):
                    break
            # 首轮空兜底(冷启动/DNS慢): 追加等待, 至少等出一个源
            if not new_items:
                for f in as_completed([x for x in futs if not x.done()], timeout=9):
                    _absorb(f)
                    if new_items:
                        break
        except Exception:
            pass
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        # 记录各源真实总页数: 壳层据此显示翻页按钮(修复子分类不能翻页)
        if max_pc:
            with self._cat_lock:
                prev_pc = self._cat_pagecount.get(key, 0)
                self._cat_pagecount[key] = max(prev_pc, max_pc)
        new_items.sort(key=lambda x: x[0])
        # 合并进缓存(去重)
        seen = {(c['name'], c['year']) for c in cached}
        order = [(c['name'], c['year']) for c in cached]
        merged = {(c['name'], c['year']): c for c in cached}
        alts = {}
        for prio, k, it in new_items:
            nk = (it['name'], it['year'])
            if nk not in seen:
                seen.add(nk)
                order.append(nk)
                merged[nk] = it
            alts.setdefault(nk, [])
            if all(kk != k for kk, _ in alts[nk]):
                alts[nk].append((k, it['vid']))
        if len(order) > _CAT_MAX:
            order = order[:_CAT_MAX]
            merged = {nk: merged[nk] for nk in order}
        out = [merged[nk] for nk in order]
        self._store_alt({nk: alts[nk] for nk in order if nk in alts})
        with self._cat_lock:
            self._cat_cache[key] = out
            self._cat_progress[key] = upto
        return out

    def categoryContent(self, tid, pg, filter, extend):
        # 直播频道: 独立数据管线(IPTV m3u), 不走点播源
        if str(tid or '') == 'live' or str(tid or '').startswith('live_'):
            return self._live_category(tid, pg, extend)
        self._ensure()
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        ext = extend if isinstance(extend, dict) else (_jload(extend) or {})
        ext_sub = str(ext.get('type') or ext.get('分类') or '').strip()
        year = str(ext.get('year') or ext.get('年份') or '').strip()
        area = str(ext.get('area') or ext.get('地区') or '').strip()
        sort = str(ext.get('sort') or ext.get('排序') or 'time').strip()
        tab_fam, tab_sub = self._cat_lookup(tid)
        fam = tab_fam
        # 子分类标签(tid本身是子分类)优先于筛选器; 顶层分类则用筛选器
        sub = tab_sub or ext_sub
        key = (fam, sub)
        # 懒加载+增量: 先秒出第一页(每源只拉1页), 翻页时才按需补拉, 避免TVBox超时返回空
        needed = pg * PAGE
        items = self._cat_ensure(fam, sub, 1)
        guard = 0
        while len(items) < needed and guard < _CAT_DEPTH:
            done = self._cat_progress.get(key, 0)
            if done <= 0:
                break
            nxt = self._cat_ensure(fam, sub, done + 1)
            guard += 1
            if self._cat_progress.get(key, 0) == done:
                break
            items = nxt
        if year:
            items = [v for v in items if year in (v.get('year') or '')]
        if area:
            items = [v for v in items
                     if area in (v.get('area') or '') or (v.get('area') or '') in area]
        if sort == 'hits':
            items.sort(key=lambda v: _num(v.get('hits')), reverse=True)
        elif sort == 'score':
            items.sort(key=lambda v: _num(v.get('score')), reverse=True)
        else:
            # 'time'(默认): 保持 _cat_ensure 的增量追加顺序(round1=各源最新页,天然按时间新->旧)
            # 不做全量重排, 避免拉取更多round后重排导致跨页重叠, 保证翻页不重复
            pass
        total = len(items)
        pagecount = max(1, (total + PAGE - 1) // PAGE)
        # 并入各源真实总页数: 首拉即可显示下一页(修复子分类不能翻页)
        try:
            pagecount = max(pagecount, int(self._cat_pagecount.get(key, 0)))
        except Exception:
            pass
        start = (pg - 1) * PAGE
        out = items[start:start + PAGE]
        return {'list': [self._to_card(it) for it in out],
                'page': pg, 'pagecount': pagecount,
                'limit': PAGE, 'total': total}

    def searchContent(self, key, quick, pg="1"):
        """兼容默影视/WebHomeTV壳层: 必须接受(key, quick, pg)三参数调用"""
        return self.searchContentPage(key, quick, pg)

    # ---------------- 直播(剧下饭源) ----------------
    def _live_group_chans(self, gid):
        """频道组 -> 频道列表 [{name,pic,remarks,src,pc}]"""
        d = _jxf_video_detail(gid)
        out = []
        for src in (d.get('playerSource') or []):
            sc = src.get('sourceCode') or 'zhibo'
            fl = src.get('sourceName') or '直播'
            for i, ep in enumerate(src.get('episodes') or [], 1):
                pc = str(ep.get('playerCode') or '')
                if not pc:
                    continue
                out.append({'name': str(ep.get('episodeName') or '') or ('频道%d' % i),
                            'pic': str(d.get('videoPic') or ''),
                            'remarks': fl, 'src': sc, 'pc': pc})
        return out

    def _live_category(self, tid, pg, extend):
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        # 直播无子分类(用户要求): 顶层即全部频道平铺(剧下饭可播源+公开IPTV补充)
        chans = self._live_flat()
        PP = 90
        total = len(chans)
        pc = max(1, (total + PP - 1) // PP)
        seg = chans[(pg - 1) * PP: pg * PP]
        lst = [{'vod_id': c['vid'], 'vod_name': c['name'], 'vod_pic': c['pic'],
                'vod_remarks': c['remarks'] or '直播',
                'vod_year': '', 'vod_area': ''}
               for c in seg]
        return {'list': lst, 'page': pg, 'pagecount': pc,
                'limit': PP, 'total': total}

    def _live_flat(self):
        """直播频道列表: 首开秒出(黑名单剔除, 未判流), 后台并发判流后换干净缓存;
        详情页有全组找回兜底, 列表变动不影响已打开的页面."""
        now = time.time()
        c = getattr(self, '_live_flat_cache', None)
        if c and now - c[0] < 1800:
            return c[1]
        out = self._live_quick()
        self._live_flat_cache = (now, out)
        if not _LIVE_BG['running'] and time.time() - _LIVE_BG['t0'] > 60:
            _LIVE_BG['running'] = True
            _LIVE_BG['t0'] = time.time()
            threading.Thread(target=self._live_bg_probe, daemon=True).start()
        return out

    def _live_bg_probe(self):
        """后台全量判流: 完成后用干净列表替换缓存(下次进直播生效)"""
        try:
            full = self._live_quick(probe=True)
            if full:
                self._live_flat_cache = (time.time(), full)
        except Exception:
            pass
        finally:
            _LIVE_BG['running'] = False

    def _live_quick(self, probe=False):
        """直播频道列表: 剧下饭源, 同名合并多路.
        probe=False: 首开模式, 只拉频道不判流(秒出, 死频道由后台清洗);
        probe=True: 全量并发判流(黑名单直判死, 服务端无流剔除)."""
        byname = {}

        def push(nm, pic, remarks, key):
            nm = (nm or '').strip()
            if not nm:
                return
            pic = _logo_enc(pic)
            it = byname.get(nm)
            if it is None:
                it = byname[nm] = {'name': nm, 'pic': '', 'remarks': remarks,
                                   'sigs': [], 'n': 0}
            it['pic'] = it['pic'] or (pic or '')
            it['n'] += 1
            if len(it['sigs']) < 5:
                it['sigs'].append(key)
        try:
            gs = _jxf_live_groups()
        except Exception:
            gs = []

        def _collect(g):
            try:
                chans = self._live_group_chans(g['id'])
            except Exception:
                return []
            return [(g['id'], i, ch) for i, ch in enumerate(chans, 1)]
        ex = ThreadPoolExecutor(max_workers=8)
        try:
            allc = []
            try:
                for f in as_completed([ex.submit(_collect, g) for g in gs], timeout=10):
                    allc.extend(f.result())
            except Exception:
                pass

            def _pw(t):
                gid, i, ch = t
                if (ch.get('name') or '').strip() in _LIVE_BLOCK:
                    return (gid, i, ch, False)  # 用户点名黑名单频道: 直接判死不探测
                if not probe:
                    return (gid, i, ch, True)  # 首开模式: 不判流, 全部收录(后台再清洗)
                try:
                    okp = _jxf_probe(ch.get('src') or 'zhibo', ch.get('pc') or '', timeout=5)
                except Exception:
                    okp = False
                return (gid, i, ch, okp)
            if probe:
                pex = ThreadPoolExecutor(max_workers=20)
                try:
                    for fut in as_completed([pex.submit(_pw, t) for t in allc], timeout=45):
                        gid, i, ch, okp = fut.result()
                        if okp:
                            push(ch['name'], ch['pic'], '直播', '%s_%d' % (gid, i))
                except Exception:
                    pass
                finally:
                    pex.shutdown(wait=False, cancel_futures=True)
            else:
                for gid, i, ch in allc:
                    push(ch['name'], ch['pic'], '直播', '%s_%d' % (gid, i))
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        out = list(byname.values())
        for x in out:
            if x['n'] > 1:
                x['remarks'] = '%s·%d路' % (x['remarks'], x['n'])
            x['vid'] = '__live__m%s' % _b64u(x['name'])
        out.sort(key=lambda x: (_live_type_rank(x['name']), -x['n'], x['name']))
        return out

    def _live_find_by_name(self, nm):
        """按频道名定位(_live_flat项): 收集全部信号源(JXF组键/IPTV直链)"""
        for x in self._live_flat():
            if x['name'] != nm:
                continue
            urls = []
            for k in x['sigs']:
                if k.startswith('i') and '::' in k:
                    try:
                        u = _b64ud(k[1:].split('::', 1)[0])
                    except Exception:
                        u = ''
                    if u.startswith('http'):
                        urls.append(('i', u))
                else:
                    urls.append(('j', k))
            return {'name': nm, 'pic': x['pic'], 'remarks': x['remarks'],
                    'sigs': urls, 'n': x['n']}
        # 兜底: 列表缓存刷新后该频道被剔除(或探活临时判死), 按名在全部组里找回,
        # 保证壳里已打开的详情页不消失、播放仍可尝试
        try:
            gs = _jxf_live_groups()
        except Exception:
            gs = []
        for g in gs:
            try:
                chans = self._live_group_chans(g['id'])
            except Exception:
                continue
            for i, ch in enumerate(chans, 1):
                if (ch.get('name') or '').strip() == nm:
                    return {'name': nm, 'pic': _logo_enc(ch.get('pic') or ''),
                            'remarks': '直播', 'n': 1,
                            'sigs': [('j', '%s_%d' % (g['id'], i))]}
        return None

    def _live_play_url(self, vkey):
        """直播频道键 -> 播放地址(多路自动回退):
        '__live__m<b64名>'=多信号频道(逐路实测选通);
        '__live__<组id>_<序号>'=剧下饭实解; '__live__i<b64>::<b64>'=IPTV直链."""
        body = (vkey or '').strip()
        if body.startswith('__live__'):
            body = body[len('__live__'):]
        if body.startswith('m'):
            try:
                nm = _b64ud(body[1:])
            except Exception:
                return ''
            it = self._live_find_by_name(nm)
            if not it:
                return ''
            for kind, v in it['sigs']:
                if kind == 'i':
                    if _live_url_ok(v):
                        return v
                else:
                    try:
                        gid_s, idx_s = v.rsplit('_', 1)
                        ch = self._live_group_chans(int(gid_s))[int(idx_s) - 1]
                        u = _jxf_analysis(ch.get('src') or 'zhibo', ch.get('pc') or '')
                        if u and _live_url_ok(u):
                            return u
                    except Exception:
                        continue
            return ''
        if body.startswith('i') and '::' in body:
            try:
                u = _b64ud(body[1:].split('::', 1)[0])
                if u.startswith('http'):
                    return u
            except Exception:
                pass
            return ''
        if body.startswith('g'):
            return ''
        try:
            gid_s, idx_s = body.rsplit('_', 1)
            gid, idx = int(gid_s), int(idx_s)
        except Exception:
            return ''
        chans = None
        for attempt in (0, 1):
            if attempt:
                try:
                    _JXF_DETAIL_CACHE.pop(str(gid), None)
                except Exception:
                    pass
            chans = self._live_group_chans(gid)
            if 1 <= idx <= len(chans):
                c = chans[idx - 1]
                u = _jxf_analysis(c.get('src') or 'zhibo', c.get('pc') or '')
                if u:
                    return u
        return ''

    def _detail_live(self, lid):
        """直播详情: '__live__m<b64名>'=多信号频道; '__live__<组id>_<序号>'=JXF单频道;
        '__live__i<b64>::<b64>'=IPTV直链频道"""
        body = lid[len('__live__'):]
        if body.startswith('m'):
            try:
                nm = _b64ud(body[1:])
            except Exception:
                return {'list': []}
            it = self._live_find_by_name(nm)
            if not it:
                return {'list': []}
            vod = {'vod_id': lid, 'vod_name': nm, 'vod_pic': it['pic'],
                   'type_name': '直播', 'vod_year': '', 'vod_area': '',
                   'vod_remarks': it['remarks'], 'vod_actor': '', 'vod_director': '',
                   'vod_content': '直播频道 · 共%d路信号 · 播放时自动选择可用线路'
                                  % max(1, it['n']),
                   'vod_play_from': '直播',
                   'vod_play_url': '%s$%s' % (nm, lid)}
            return {'list': [vod]}
        if body.startswith('i') and '::' in body:
            try:
                p = body[1:].split('::', 1)
                u, nm = _b64ud(p[0]), _b64ud(p[1])
            except Exception:
                return {'list': []}
            if not u.startswith('http'):
                return {'list': []}
            vod = {'vod_id': lid, 'vod_name': nm, 'vod_pic': '',
                   'type_name': '直播', 'vod_year': '', 'vod_area': '',
                   'vod_remarks': '公开IPTV', 'vod_actor': '', 'vod_director': '',
                   'vod_content': '公开IPTV直播信号 · 若黑屏请换其他频道',
                   'vod_play_from': '直播', 'vod_play_url': '%s$%s' % (nm, lid)}
            return {'list': [vod]}
        if body.startswith('g'):
            gid = body[1:]
            chans = self._live_group_chans(gid)
            if not chans:
                return {'list': []}
            dname = ''
            try:
                dname = str(_jxf_video_detail(gid).get('name') or '')
            except Exception:
                pass
            eps = '#'.join('%s$__live__%s_%d' % (c['name'], gid, i + 1)
                           for i, c in enumerate(chans))
            vod = {'vod_id': lid, 'vod_name': dname or ('直播频道组%s' % gid),
                   'vod_pic': (chans[0].get('pic') or ''),
                   'type_name': '直播', 'vod_year': '', 'vod_area': '',
                   'vod_remarks': '%d个频道' % len(chans),
                   'vod_actor': '', 'vod_director': '',
                   'vod_content': '直播频道组 · 共%d个频道' % len(chans),
                   'vod_play_from': '直播频道', 'vod_play_url': eps}
            return {'list': [vod]}
        # 单频道: gid_idx
        try:
            gid_s, idx_s = body.rsplit('_', 1)
            gid, idx = int(gid_s), int(idx_s)
        except Exception:
            return {'list': []}
        chans = self._live_group_chans(gid)
        if not (1 <= idx <= len(chans)):
            return {'list': []}
        c = chans[idx - 1]
        try:
            dname = str(_jxf_video_detail(gid).get('name') or '')
        except Exception:
            dname = ''
        vod = {'vod_id': lid, 'vod_name': c['name'], 'vod_pic': c['pic'],
               'type_name': '直播', 'vod_year': '', 'vod_area': '',
               'vod_remarks': dname or '直播',
               'vod_actor': '', 'vod_director': '',
               'vod_content': '直播频道 · %s · 播放时实时解析直播流' % (dname or '直播'),
               'vod_play_from': '直播',
               'vod_play_url': '%s$__live__%s_%d' % (c['name'], gid, idx)}
        return {'list': [vod]}

    def searchContentPage(self, key, quick, pg='1'):
        self._ensure()
        key = (key or '').strip()
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        if not key:
            return {'list': [], 'page': pg, 'pagecount': 1, 'limit': PAGE, 'total': 0}
        srcs = self._healthy()
        got, max_total, max_pc = [], 0, 0
        ex = ThreadPoolExecutor(max_workers=10)
        futs = {ex.submit(self._fetch_list, s, pg, None, key, _T_SEARCH): s
                for s in srcs}
        dd_fut = ex.submit(self._dd_search, key, pg)  # 多多(真4K源)并发搜
        deadline = time.time() + 10.0  # 搜索限时: 慢源(非凡/金鹰实测7s+)也要等结果
        got_fast = 0
        try:
            for f in as_completed(futs):
                if time.time() > deadline:
                    break
                try:
                    items, total, pc = f.result()
                except Exception:
                    continue
                s = futs[f]
                if total > max_total:
                    max_total = total
                if pc > max_pc:
                    max_pc = pc
                got.append((s['prio'], s['key'], items))
                # 已集满一页即提前返回, 快速响应
                got_fast += len(items)
                if got_fast >= PAGE:
                    break
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        # 多多影视(真4K)结果并入(prio=0排最前, 详情可直达真4K线路)
        try:
            dd_items = dd_fut.result(timeout=3)
        except Exception:
            dd_items = []
        if dd_items:
            got.append((0, 'dd', dd_items))
            v2n = getattr(self, '_vid2nk', None)
            if v2n is None:
                v2n = self._vid2nk = {}
            for d in dd_items:
                try:
                    v2n.setdefault(('dd', d['vid']), (d['name'], d['year']))
                except Exception:
                    pass
        got.sort(key=lambda x: x[0])
        merged, alts = self._merge(got)
        self._store_alt(alts)
        # 串行兜底: 并发全空时逐源补查(应对偶发超时/限流), 确保能出结果
        if not merged:
            for s in self._healthy()[:3]:
                try:
                    items, total, pc = self._fetch_list(s, pg, None, key, _T_SEARCH)
                except Exception:
                    continue
                if total > max_total:
                    max_total = total
                if pc > max_pc:
                    max_pc = pc
                got.append((s['prio'], s['key'], items))
                merged, alts = self._merge(got)
                self._store_alt(alts)
                if merged:
                    break
        # 完全匹配优先, 其次时间新
        merged.sort(key=lambda v: (0 if v['name'] == key else 1,
                                   -_num((v.get('time') or '').replace('-', '').replace(':', '').replace(' ', '') or 0)))
        # 构建跨源借图池: 非多多条目的可用海报按片名索引(供 _to_card 打标首图/坏图条目借用)
        try:
            pool = {}
            bad = getattr(Spider, '_bad_pic', None)
            if bad is None:
                bad = Spider._bad_pic = {}
            for v in merged:
                p = (v.get('pic') or '').strip()
                if not p:
                    continue
                pool.setdefault(v['name'], p)
                base = re.sub(r'\s*[4ｋKＫ].*$', '', v['name']).strip()
                if base and base != v['name']:
                    pool.setdefault(base, p)
            Spider._pic_pool = pool
            # 兜底: 多多条目缺图且本页有同名非多多条目时, 补借(不覆盖已有好图)
            for v in merged:
                if not v['id'].startswith('__dd__') or (v.get('pic') or '').strip():
                    continue
                nm = v['name'] or ''
                bp = pool.get(nm) or pool.get(re.sub(r'\s*[4ｋKＫ].*$', '', nm).strip())
                if bp:
                    bad.pop(nm, None)
                    v['pic'] = bp
        except Exception:
            pass
        return {'list': [self._to_card(it) for it in merged[:PAGE]],
                'page': pg, 'pagecount': max_pc if merged else max(1, pg - 1),
                'limit': PAGE, 'total': max_total}

    # ---------------- 详情 ----------------
    @staticmethod
    def _is_direct_url(u):
        """判断是否直链媒体(可 parse=0 直连播放)."""
        u = (u or '').split('?')[0].lower()
        return u.endswith(('.m3u8', '.m3u', '.mp4', '.flv', '.ts', '.mkv',
                           '.rmvb', '.avi', '.mov', '.webm'))
    @staticmethod
    def _line_usable(eps):
        """线路是否可保留: 需有真实集数URL, 且非失效的分享/HTML代理页.
        保留 直链(m3u8/mp4) 与标准 /play/ 播放页(可解析); 仅剔除 /share/ 分享页、纯HTML页等失效线路."""
        eps = (eps or '').strip()
        if not eps or '$' not in eps:
            return False
        m = re.search(r'\$([^\s#]+)', eps)
        if not m:
            return False
        u = m.group(1).split('?')[0].lower()
        if not u.startswith('http'):
            return False
        if '/share/' in u or u.endswith(('.html', '.htm')):
            return False  # 分享代理页/纯页面, 直连黑屏, 剔除
        return True
    @staticmethod
    def _is_direct_eps(eps):
        """该组集数第一集是否直链媒体(用于直连优先排序)"""
        m = re.search(r'\$([^\s#]+)', eps or '')
        return bool(m) and Spider._is_direct_url(m.group(1))
    @staticmethod
    def _directize_eps(eps):
        """把 /play/xxx 播放页URL转成直链 m3u8(苹果CMS标准: 同路径下 index.m3u8).
        纯字符串变换不加请求; 已是直链的不变."""
        def conv(m):
            u = m.group(1)
            if Spider._is_direct_url(u):
                return u
            if re.search(r'/play/[^/]+$', u):
                return u.rstrip('/') + '/index.m3u8'
            return u
        return re.sub(r'\$([^\s#]+)', lambda m: '$' + conv(m), eps or '')
    @staticmethod
    def _parse_flags(v):
        """vod_play_from / vod_play_url → [(flag, eps_str)]
        苹果CMS存在多种对齐写法(实测: 非凡/红牛/蓝采/金鹰/极速/天堂电影为 $$$ 分组):
          A) from="f1$$$f2"    ⇔ url="组1$$$组2"   一一对应(最常见, 此前按#切会全错位丢线路)
          B) from="f1#f2"      ⇔ url="组1"         同名flag共享同一组
          C) from="f1#f2$$$f3" ⇔ url="组1$$$组2"   同名合并, 组按顺序对齐
        策略: 把 from 按 $$$ 和 # 一并展平, 再与组按顺序一一对齐;
        组多于flag时剩余组自动命名(m3u8/线路N), 一个组都不丢.
        解析层即剔除: 空flag / 含"同站"字样 / 无集数URL 的线路."""
        from_str = str(v.get('vod_play_from') or '').strip()
        raw_groups = str(v.get('vod_play_url') or '').split('$$$')
        if not any(g.strip() and '$' in g for g in raw_groups):
            return []
        names = [s.strip() for s in re.split(r'\$\$\$|#', from_str) if s.strip()]
        out, used = [], set()
        for i, g in enumerate(raw_groups):
            # 按原始组位置对齐flag(空组跳过但不占名), 防止错位
            g = g.strip()
            if not g or '$' not in g:
                continue
            fl = names[i] if i < len(names) else ''
            if not fl or fl.lower() in ('none', 'null'):
                first_ep = g.split('#')[0].split('$')[-1].lower()
                fl = 'm3u8' if '.m3u8' in first_ep else '线路%d' % (i + 1)
            if '同站' in fl:
                continue
            if fl in used:
                fl = '%s(%d)' % (fl, i + 1)
            used.add(fl)
            out.append((fl, g))
        return out

    # ---------------- 多多影视(真4K)接入 ----------------
    @staticmethod
    def _dd_parse_vod(v):
        """多多详情vod → 帧不戳统一item结构(搜索卡片可无播放组, 详情接口才带play数据)"""
        name = str(v.get('vod_name') or '').strip()
        if not name:
            return None
        raw_groups = str(v.get('vod_play_url') or '').split('$$$')
        froms_raw = str(v.get('vod_play_from') or '').split('$$$')
        ordered = []
        for i, g in enumerate(raw_groups):
            g = g.strip()
            if not g or '$' not in g:
                continue
            fc = froms_raw[i].strip() if i < len(froms_raw) else ''
            ordered.append((0 if i == 0 else 1, i, g, fc))
        ordered.sort(key=lambda t: (t[0], t[1]))
        return {
            'name': name, 'year': str(v.get('vod_year') or '').strip(),
            'pic': _pic_fix(v.get('vod_pic') or v.get('vod_pic_thumb')),
            'remarks': str(v.get('vod_remarks') or '').strip(),
            'area': str(v.get('vod_area') or '').strip(),
            'type_name': str(v.get('vod_class') or v.get('type_name') or '').strip(),
            'actor': '', 'director': '', 'content': '',
            'groups': [t[2] for t in ordered],
            'froms': [t[3] for t in ordered],
        }

    def _dd_search(self, wd, pg):
        """多多搜索(同款聚合进结果)"""
        try:
            b = _dd_http(_DD_HOST + '/api.php/web/search/index?wd=%s&page=%s'
                         % (urllib.parse.quote(wd or ''), pg or 1), timeout=_DD_TIMEOUT)
            j = _jload(b.decode('utf-8', 'ignore')) or {}
        except Exception:
            return []
        data = j.get('data')
        items = data if isinstance(data, list) else []
        out = []
        for v in items[:12]:
            it = self._dd_parse_vod(v)
            if it:
                it['vid'] = '__dd__%s' % v.get('vod_id')
                it['id'] = it['vid']
                it['prio'] = 0
                it['key'] = 'dd'
                out.append(it)
        return out

    def _fetch_detail(self, src, vid):
        base = self._bases.get(src['key'])
        if not base:
            return None
        j = _jload(_http(base + '?ac=detail&ids=%s' % vid, T_LIST))
        if not j:
            return None
        lst = j.get('list') or []
        if not lst:
            return None
        v = lst[0]
        it = self._norm_item(src['key'], v)
        if not it:
            return None
        return {
            'key': src['key'],
            'prio': src['prio'],
            'name': it['name'],
            'year': it['year'],
            'pic': it['pic'],
            'remarks': it['remarks'],
            'type_name': str(v.get('type_name') or it['type_name'] or ''),
            'year_': it['year'],
            'area': it['area'],
            'actor': str(v.get('vod_actor') or '').strip(),
            'director': str(v.get('vod_director') or '').strip(),
            'content': re.sub(r'<[^>]+>', '', str(v.get('vod_content')
                                                  or v.get('vod_blurb') or '')).strip(),
            'flags': self._parse_flags(v),
        }

    def _find_same(self, src, name, year, timeout=T_PROBE):
        """在某源精确查找同名影片, 返回 vid 或 ''(短超时, 避免详情页过慢)"""
        items, _, _ = self._fetch_list(src, 1, None, name, timeout)
        for it in items:
            if it['name'] == name:
                if year and it['year'] and year != it['year']:
                    continue
                return it['vid']
        return ''
    def _collect_extra(self, name, year, exclude_key):
        """跨源收集同名影片的其他线路(优先_alt缓存; 无缓存时只并发探3源,短超时)"""
        cands = []
        alts = self._alt.get((name, year)) or []
        for k, vid in alts:
            if k != exclude_key:
                cands.append((k, vid))
        if not cands:
            # 无缓存: 只并发探2个源, 短超时, 避免详情页过慢
            srcs = self._healthy(exclude=exclude_key)[:2]
            ex = ThreadPoolExecutor(max_workers=2)
            futs = {ex.submit(self._find_same, s, name, year, _T_SEARCH): s for s in srcs}
            try:
                for f in as_completed(futs):
                    try:
                        vid = f.result()
                    except Exception:
                        continue
                    if vid:
                        cands.append((futs[f]['key'], vid))
            finally:
                ex.shutdown(wait=False, cancel_futures=True)
        # 并发取详情(限流,最多4源)
        details = []
        ex = ThreadPoolExecutor(max_workers=4)
        futs = {}
        try:
            for k, vid in cands[:4]:
                s = SRC_BY_KEY.get(k)
                if s and k in self._bases:
                    futs[ex.submit(self._fetch_detail, s, vid)] = s
            for f in as_completed(futs):
                try:
                    d = f.result()
                except Exception:
                    continue
                if d and d['flags']:
                    details.append(d)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        details.sort(key=lambda d: d['prio'])
        return details

    @staticmethod
    def _eps_key(eps):
        """同一资源的指纹: 取首集URL, 去掉前两级域名后比较(跨CDN/跨源同资源去重)"""
        try:
            u = re.search(r'\$([^\s#]+)', eps or '').group(1)
        except Exception:
            return ''
        u = u.split('?')[0].lower()
        u = re.sub(r'^https?://', '', u)
        parts = u.split('/')
        host_parts = parts[0].split('.')
        host = '.'.join(host_parts[2:]) if len(host_parts) > 2 else parts[0]
        return '/'.join([host] + parts[1:])

    def detailContent(self, ids):
        self._ensure()
        try:
            raw = ids[0] if isinstance(ids, (list, tuple)) else ids
        except Exception:
            raw = ''
        raw = str(raw or '')
        if raw.startswith('__live__'):
            return self._detail_live(raw)
        if raw.startswith('__dd__'):
            return self._detail_dd(raw[len('__dd__'):])
        if '__' not in raw:
            return {'list': []}
        key, vid = raw.split('__', 1)
        if vid.startswith('__dd__'):
            return self._detail_dd(vid[len('__dd__'):])
        s = SRC_BY_KEY.get(key)
        main = self._fetch_detail(s, vid) if s else None
        if not main and s:
            # 主源失败: 快速重探主接口(不阻塞), 再试一次
            self._ensure_main(s)
            main = self._fetch_detail(s, vid)
        if not main:
            # 跨源救援: 用 alt缓存里的名称回查其他源同名影片
            nk = getattr(self, '_vid2nk', {}).get((key, vid))
            if nk:
                nm = re.sub(r'\s*第[一二三四五六七八九十\d]+季.*$', '', nk[0]) or nk[0]
                for s2 in self._healthy(exclude=key)[:3]:
                    v2 = self._find_same(s2, nm, nk[1], _T_SEARCH)
                    if v2:
                        main = self._fetch_detail(s2, v2)
                        if main:
                            key, vid = s2['key'], v2
                            raw = '%s__%s' % (key, v2)
                            break
        if not main:
            return {'list': []}
        name, year = main['name'], main['year']
        # 汇集线路: 主源优先(含全部线路), 其他源同名影片追加
        lines = []
        used_disp = set()
        seen_eps = set()  # 相同集数URL去重(同站/跨站同一资源只保留一条)

        def add_lines(det):
            for fl, eps in det['flags']:
                if not fl or not self._line_usable(eps):
                    continue
                # 剔除线路名含"同站"字样的线路(用户要求)
                if '同站' in str(fl):
                    continue
                eps = self._directize_eps(eps)  # /play/xxx → index.m3u8 直链
                # 同一资源(相同/跨CDN同路径)去重, 剔除同站与跨站重复线路
                ek = self._eps_key(eps)
                if ek and ek in seen_eps:
                    continue
                if ek:
                    seen_eps.add(ek)
                disp = '%s·%s' % (det['key_display'], fl)
                # 真4K才打标: 仅多多CO4K等已验证线路; 采集站URL里的"4k"多为假标(实测1080p)
                if disp in used_disp:
                    disp = disp + ('(%s)' % det['key'])
                if disp in used_disp:
                    continue
                seen_eps.add(eps)
                used_disp.add(disp)
                # 直链(m3u8/mp4)排最前(可parse=0直播), /play/播放页靠后(需解析)
                direct = 0 if self._is_direct_eps(eps) else 1
                lines.append((direct, _line_rank(fl, eps), det['prio'], disp, eps))

        main['key_display'] = SRC_BY_KEY[main['key']]['name']
        add_lines(main)
        try:
            for det in self._collect_extra(name, year, main['key']):
                det['key_display'] = SRC_BY_KEY[det['key']]['name']
                add_lines(det)
        except Exception:
            pass
        lines.sort(key=lambda x: (x[0], x[1], x[2]))
        # 兜底: 虾米解析线路(parse=1), 可解析网页/加密源, 镜像第一条线路的集数, 放最后备用
        if lines:
            eps0 = lines[0][4]
            lines.append((2, 99, 99, '虾米解析',
                          '#'.join('第%02d集$https://jx.xmflv.com/?url=%s'
                                   % (i + 1, ep.split('$')[-1])
                                   for i, ep in enumerate(eps0.split('#')))))
        vod = {
            'vod_id': raw,
            'vod_name': name,
            'vod_pic': main['pic'] or _DD_PIC_FALLBACK,
            'type_name': main['type_name'],
            'vod_year': main['year'],
            'vod_area': main['area'],
            'vod_remarks': main['remarks'],
            'vod_actor': main['actor'],
            'vod_director': main['director'],
            'vod_content': main['content'] or '暂无简介',
            'vod_play_from': '$$$'.join(x[3] for x in lines),
            'vod_play_url': '$$$'.join(x[4] for x in lines),
        }
        return {'list': [vod]}

    # ---------------- 多多真4K详情 ----------------
    def _detail_dd(self, dd_id):
        """多多影视详情: CO4K线路decode真4K直链置顶, 其余线路保留原始码(播放时按需decode)"""
        try:
            b = _dd_http(_DD_HOST + '/api.php/web/vod/get_detail?vod_id=%s' % dd_id,
                         timeout=_DD_TIMEOUT)
            j = _jload(b.decode('utf-8', 'ignore')) or {}
        except Exception:
            return {'list': []}
        dl = j.get('data')
        v = dl[0] if isinstance(dl, list) and dl else (dl if isinstance(dl, dict) else {})
        it = self._dd_parse_vod(v or {})
        if not it:
            return {'list': []}
        if not it['pic']:
            # 详情页借图: 搜索阶段建立的跨源海报池(Spider._pic_pool)
            pool = getattr(Spider, '_pic_pool', None)
            if pool:
                nm = it['name'] or ''
                bp = pool.get(nm) or pool.get(re.sub(r'\s*[4ｋKＫ].*$', '', nm).strip())
                if bp:
                    it['pic'] = bp
        used, lines = set(), []
        froms = it.get('froms') or []
        # 后台预热: 前三线路首集解码先行(结果入_DD_DEC_CACHE), 播放时零等待;
        # 用户停留详情页的几秒足够完成起播准备(实测单次解码1.5~1.8s)
        def _prewarm():
            try:
                for gi, grp in enumerate(it['groups'][:3]):
                    fci = froms[gi] if gi < len(froms) else ''
                    e0 = ''
                    for e in grp.split('#'):
                        if '$' in e:
                            t = e.split('$', 1)[1].strip()
                            if t and not t.startswith('http'):
                                e0 = t
                                break
                    if not e0:
                        continue
                    u = _dd_decode_url(fci, e0)
                    if u and u.startswith('http') and 'getM3u8' in u:
                        try:  # 时效网关: 预解后探活, 已失效则清缓存让播放时重解
                            rq = urllib.request.Request(u, headers={
                                'User-Agent': UA, 'Range': 'bytes=0-64'})
                            rr = urllib.request.urlopen(rq, timeout=4, context=_SSL)
                            rr.read(64); rr.close()
                        except Exception:
                            _DD_DEC_CACHE.pop('%s|%s' % (fci, e0), None)
            except Exception:
                pass
        threading.Thread(target=_prewarm, daemon=True).start()
        # 诚实打标: 片名含4K或归入4K类目(源站明确宣称)才标真4K;
        # CO4K只是采集站名, 普通剧(如御廷谣,实测1280x534)不标; 片名带4K的实测确有3840(隐秘的角落4K)
        hint4k = '4k' in ((it.get('type_name') or '') + (it['remarks'] or '') + it['name']).lower()
        for i, g in enumerate(it['groups']):
            if i == 0:
                fl = '多多·真4K' if hint4k else '多多·高清'
            else:
                fl = '多多·线路%d' % i
            fc = froms[i] if i < len(froms) else ''
            eps = []
            for e in g.split('#'):
                if '$' not in e:
                    continue
                en, eu = e.split('$', 1)
                eu = eu.strip()
                if not eu:
                    continue
                if eu.startswith('http'):
                    u = eu
                elif i == 0 and not eps:
                    # 首线路首集预解(30分钟缓存): BBA=139云m3u8直链最稳, CO4K部分剧真4K;
                    # 失败回退ddraw播放时重解
                    u = _dd_decode_url(fc, eu)
                    if not u:
                        u = 'ddraw:%s:%s' % (fc, eu)
                else:
                    u = 'ddraw:%s:%s' % (fc, eu)
                if not u or u in used:
                    continue
                used.add(u)
                eps.append('%s$%s' % (en, u))
            if eps:
                lines.append((fl, '#'.join(eps)))
        if not lines:
            return {'list': []}
        vod = {
            'vod_id': '__dd__%s' % dd_id,
            'vod_name': it['name'],
            'vod_pic': it['pic'] or _DD_PIC_FALLBACK,
            'type_name': it['type_name'],
            'vod_year': it['year'],
            'vod_area': it['area'],
            'vod_remarks': it['remarks'],
            'vod_actor': '',
            'vod_director': '',
            'vod_content': it['content'] or '多多影视聚合源 · 线路清晰度以实际播放为准',
            'vod_play_from': '$$$'.join(x[0] for x in lines),
            'vod_play_url': '$$$'.join(x[1] for x in lines),
        }
        return {'list': [vod]}

    # ---------------- 播放 ----------------
    def playerContent(self, flag, id, vipFlags):
        # 直播频道: __live__<组id>_<序号> -> 剧下饭 analysisUrl 实时解析
        # (token有时效必须点播时现解, 不可预缓存; 失败清缓存重试一次)
        if str(flag or '') == '直播' or str(id or '').startswith(('__live__',)):
            url = str(id or '').strip()
            if '$' in url:
                url = url.split('$')[-1]
            url = self._live_play_url(url)
            if not url:
                return {'parse': 0, 'playUrl': '', 'url': '',
                        'header': {'User-Agent': _JXF_UA}}
            hdr = {'User-Agent': _JXF_UA}
            if url.endswith(('.m3u8', '.m3u', '.ts', '.flv')) or 'epg.pw' in url:
                hdr['User-Agent'] = LIVE_M3U_UA
            if 'huya.com' in url:
                hdr['Referer'] = 'https://www.huya.com/'
            return {'parse': 0, 'playUrl': '', 'url': url, 'header': hdr}
        url = str(id or '').strip()
        if '$' in url:
            url = url.split('$')[-1]
        hdr = {'User-Agent': UA}
        flag = str(flag or '')
        # 由线路显示名(源前缀)确定 Referer; 前缀未命中再用URL域名反推
        disp = flag.split('·')[0] if '·' in flag else flag
        ref = _REFERER.get(disp) or _REFERER.get(flag)
        if not ref:
            m = re.search(r'https?://([^/]+)', url)
            host = (m.group(1) if m else '').lower()
            if 'feifei' in host or 'ffzy' in host or 'ffm3u8' in flag:
                ref = 'https://www.ffzy.tv/'
            elif '360zy' in host:
                ref = 'https://360zy.com/'
            elif 'lzi' in host or 'liangzi' in host or 'lzm3u8' in flag:
                ref = 'https://cj.lziapi.com/'
            elif 'bfzy' in host:
                ref = 'https://bfzyapi.com/'
            elif 'hongniu' in host:
                ref = 'https://hongniuzy2.com/'
            elif 'jinying' in host or 'jyzy' in host:
                ref = 'https://jinyingzy.com/'
            elif 'jszy' in host:
                ref = 'https://jszyapi.com/'
            elif 'tyys' in host:
                ref = 'https://tyyszy.com/'
            elif 'dytt' in host:
                ref = 'https://caiji.dyttzyapi.com/'
            elif 'ckzy' in host:
                ref = 'https://ckzy1.com/'
        if ref:
            hdr['Referer'] = ref
        # 虾米解析线路: 需走网页解析(parse=1)
        if '虾米' in flag or 'xmflv' in url or 'jx.xmflv' in url:
            return {'parse': 1, 'playUrl': '', 'url': url, 'header': hdr}
        # 多多线路: 原始地址(ddraw:from:token)需经多多decode接口实时解析(签名+protobuf);
        # NBY时效网关(详情页预解的getM3u8 URL)到播放时已过期: 先探活, 不通则实时重解替换
        if '多多' in flag:
            is_gw = url.startswith('http') and 'getM3u8' in url
            is_raw = url.startswith('ddraw:')
            if is_gw or is_raw or not url.startswith('http'):
                fc, tok = ('CO4K' if '4K' in flag else ''), url
                if is_raw:
                    try:
                        fc, tok = url[len('ddraw:'):].split(':', 1)
                    except Exception:
                        fc, tok = 'CO4K', url
                elif is_gw:
                    fc = 'NBY'
                    tok = url.split('url=', 1)[1] if 'url=' in url else ''
                fresh = ''
                if is_gw and tok:
                    try:
                        rq = urllib.request.Request(url, headers={
                            'User-Agent': hdr.get('User-Agent', UA),
                            'Range': 'bytes=0-200'})
                        rr = urllib.request.urlopen(rq, timeout=4, context=_SSL)
                        bb = rr.read(200)
                        rr.close()
                        if b'#EXTM3U' in bb or b'#EXT-X' in bb:
                            fresh = url  # 旧网关URL仍存活, 直接复用
                    except Exception:
                        pass
                if not fresh and tok:
                    fresh = _dd_decode_url(fc, tok)
                if fresh:
                    url = fresh
        return {'parse': 0, 'playUrl': '', 'url': url, 'header': hdr}

    def isVideoFormat(self, url):
        return True

    def manualVideoCheck(self):
        return False

    def destroy(self):
        return ''

    def localProxy(self, param):
        return [200, 'text/plain', '', '']


# ---------------- 自测(仅在直接运行时执行, 不影响TVBox加载) ----------------
if __name__ == '__main__':
    sp = Spider()
    t0 = time.time()
    print(sp.init(''), 'init %.2fs' % (time.time() - t0))
    t0 = time.time()
    hc = sp.homeContent(True)
    print('homeContent %.2fs' % (time.time() - t0),
          json.dumps(hc.get('class'), ensure_ascii=False))
    t0 = time.time()
    hv = sp.homeVideoContent()
    print('homeVideo %.2fs' % (time.time() - t0), len(hv.get('list', [])))
    t0 = time.time()
    cc = sp.categoryContent('movie', '1', True, {})
    print('cat1 %.2fs' % (time.time() - t0), len(cc['list']),
          'pc', cc['pagecount'], 'total', cc['total'])
    t0 = time.time()
    cc2 = sp.categoryContent('movie', '2', True, {})
    n1 = {v['vod_name'] for v in cc['list']}
    n2 = {v['vod_name'] for v in cc2['list']}
    print('cat2 %.2fs' % (time.time() - t0), len(cc2['list']), 'overlap', len(n1 & n2))
    if cc['list']:
        fid = cc['list'][0]['vod_id']
        t0 = time.time()
        det = sp.detailContent([fid])
        v = det['list'][0] if det['list'] else {}
        print('detail %.2fs' % (time.time() - t0), v.get('vod_name'),
              '| lines:', len((v.get('vod_play_from') or '').split('$$$')))
        if v:
            pc = sp.playerContent(v['vod_play_from'].split('$$$')[0],
                                  v['vod_play_url'].split('$$$')[0].split('#')[0].split('$')[-1], {})
            print('play:', pc.get('url', '')[:90])
    t0 = time.time()
    sc = sp.searchContent('爱', False)
    print('search %.2fs' % (time.time() - t0), len(sc['list']))
