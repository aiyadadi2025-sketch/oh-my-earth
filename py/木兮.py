#!/usr/bin/env python3
# QQ群:807916734 @ID丶
# -*- coding: utf-8 -*-
# ============================================================
# TVBox Python 爬虫 · 山有木兮影视 (film.symx.club)
# 版本: 2.0.0 (默影视兼容版)
# 兼容: TVBox / 影视仓 / FongMi / WebHomeTV(默影视) / PeekPro
# 协议: /api/* + X-Platform/X-Timestamp/HMAC签名头 + XOR解密system/config
#       内容接口免登录; 1004 → /api/auth/verify 滑块自愈
# 零第三方依赖: 滑块识别内置纯 Python JPEG 解码 (PIL 可用时优先)
# 零 f-string (老内核 Python 兼容)
# ============================================================
import sys
sys.path.append('..')
try:
    from base.spider import Spider as _BaseSpider
except Exception:
    _BaseSpider = object

import json
import os
import time
import random
import hmac
import hashlib
import struct
import zlib
import base64
import io
import re
import urllib.request
import urllib.parse
import ssl

try:
    _SSL_CTX = ssl.create_default_context()
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE
except Exception:
    _SSL_CTX = None

HOST = "https://film.symx.club"
API = HOST + "/api"
# v4.5: 伪装 PC 客户端身份 (Electron app.asar 逆向实证):
#  - X-Platform: "windows" (web 端是 "web") + X-Version: "1.1.1"
#  - ClientId 为 UUID 格式 (getUUID())
#  - 4K 线路解锁 = GET /line/play/parse?lineId= (服务端解析直链, web 端点不下发)
#  - detail 也有 APP 专属端点 /film/detail/play/app?id=
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
# v4.6: APP 主进程对 /api/* 请求强制覆盖 UA (main/index.js webRequest 实证):
#   API_USER_AGENT = "SYMX_WINDOWS" — 4K 通道身份链的一环
API_UA = "SYMX_WINDOWS"
APP_VERSION = "1.1.1"


def _cid():
    # v4.5: APP 同款 UUID 格式 (getUUID(): 标准带连字符 36 位)
    def _h(n):
        return ''.join(random.choice('0123456789abcdef') for _ in range(n))
    return _h(8) + "-" + _h(4) + "-4" + _h(3) + "-8" + _h(3) + "-" + _h(12)


# ---------- v4.4 磁盘持久化 (重启后保持同一"设备") ----------
_PERSIST_NAME = "symx_dev.json"

def _persist_paths():
    import tempfile
    ps = []
    try:
        ps.append(tempfile.gettempdir())
    except Exception:
        pass
    ps.append(os.getcwd())
    for p in ("/storage/emulated/0/Download", "/sdcard/Download", "/data/local/tmp"):
        ps.append(p)
    return ps

def _persist_load():
    for d in _persist_paths():
        if not d:
            continue
        try:
            f = os.path.join(d, _PERSIST_NAME)
            if os.path.exists(f):
                with open(f, "r") as fh:
                    return json.load(fh), d
        except Exception:
            continue
    return None, None

def _persist_save(obj, preferred_dir=None):
    dirs = [preferred_dir] if preferred_dir else []
    dirs += _persist_paths()
    for d in dirs:
        if not d:
            continue
        try:
            f = os.path.join(d, _PERSIST_NAME)
            with open(f, "w") as fh:
                json.dump(obj, fh)
            return d
        except Exception:
            continue
    return None


# ============================================================
# 纯 Python JPEG (baseline) 解码器 — 滑块识别用, 仅解码亮度
# ============================================================
class _JpegGray(object):
    """极简 baseline JPEG 解码, 只输出灰度 (行数组)"""

    def __init__(self, data):
        self.d = data
        self.pos = 0
        self.qt = [[16] * 64 for _ in range(4)]
        self.huff_dc = {}
        self.huff_ac = {}
        self.w = 0
        self.h = 0
        self.comp = []  # (h,v,tq,dc_id,ac_id)
        self.mcux = 1
        self.mcuy = 1

    def u16(self):
        v = (self.d[self.pos] << 8) | self.d[self.pos + 1]
        self.pos += 2
        return v

    def decode(self):
        d = self.d
        if d[0] != 0xFF or d[1] != 0xD8:
            raise ValueError("not jpeg")
        self.pos = 2
        while self.pos < len(d):
            if d[self.pos] != 0xFF:
                self.pos += 1
                continue
            m = d[self.pos + 1]
            self.pos += 2
            if m in (0xD8, 0xD9, 0x01) or 0xD0 <= m <= 0xD7:
                continue
            ln = self.u16() - 2
            if m == 0xDA:
                # pos 已指向 header 内容 (u16 消费了 length)
                return self._parse_sos()
            seg = d[self.pos:self.pos + ln]
            self.pos += ln
            if m == 0xDB:
                self._parse_dqt(seg)
            elif m in (0xC0, 0xC1):
                self._parse_sof(seg)
            elif m == 0xC4:
                self._parse_dht(seg)
            elif m == 0xDA:
                return self._parse_sos()
        raise ValueError("no SOS")

    def decode_rgb(self):
        """v4.1 全图 RGB (兼容/对拍用); 业务路径走 decode_rgb_rows (窗口)"""
        return self.decode_rgb_rows(0, None)

    def decode_rgb_rows(self, ys=0, ye=None):
        # v4.3 窗口解码: 哈夫曼全量 (流同步必须, 仅 0.1s) 但 IDCT+存储仅窗口行
        d = self.d
        if d[0] != 0xFF or d[1] != 0xD8:
            raise ValueError("not jpeg")
        self.pos = 2
        while self.pos < len(d):
            if d[self.pos] != 0xFF:
                self.pos += 1
                continue
            m = d[self.pos + 1]
            self.pos += 2
            if m in (0xD8, 0xD9, 0x01) or 0xD0 <= m <= 0xD7:
                continue
            ln = self.u16() - 2
            if m == 0xDA:
                if ye is None:
                    ye = self.h  # walk 完成后尺寸才有效
                return self._parse_sos_rgb(ys, ye)
            seg = d[self.pos:self.pos + ln]
            self.pos += ln
            if m == 0xDB:
                self._parse_dqt(seg)
            elif m in (0xC0, 0xC1):
                self._parse_sof(seg)
            elif m == 0xC4:
                self._parse_dht(seg)
        raise ValueError("no SOS")

    def _parse_sos_rgb(self, ys=0, ye=None):
        # v4.3: 窗口行解码 (滑块只需掩码行 ~84/360, IDCT 量降 75%)
        if ye is None:
            ye = self.h
        d = self.d
        ns = d[self.pos + 0]
        self.pos += 1
        comps = []
        for i in range(ns):
            cid = d[self.pos]
            tabs = d[self.pos + 1]
            comps.append((cid, tabs >> 4, tabs & 15))
            self.pos += 2
        self.pos += 3
        self.scan = d[self.pos:]
        self.bytepos = -1
        self.bitpos = 8
        self.cur = 0
        planes = []
        for c in self.comp:
            h, v = c[0], c[1]
            pw = self.mcux * h * 8
            ph = self.mcuy * v * 8
            planes.append([[0] * pw for _ in range(ph)])
        pred = [0] * len(self.comp)
        comp_map = {}
        for idx, c in enumerate(self.comp):
            comp_map[c[3]] = (idx, c)
        sy_c = [self.vmax // c[1] if self.vmax >= c[1] else 1 for c in self.comp]
        for mcu in range(self.mcux * self.mcuy):
            mcu_row = mcu // self.mcux
            for (cid, dc_t, ac_t) in comps:
                idx, c = comp_map[cid]
                h, v, tq = c[0], c[1], c[2]
                for by in range(v):
                    for bx in range(h):
                        blk = [0] * 64
                        t = self._decode_huff(self.huff_dc[dc_t])
                        diff = self._extend(self._get_bits(t), t) if t else 0
                        pred[idx] += diff
                        blk[0] = pred[idx] * self.qt[tq][0]
                        k = 1
                        while k < 64:
                            rs = self._decode_huff(self.huff_ac[ac_t])
                            r = rs >> 4
                            s = rs & 15
                            if s == 0:
                                if r == 15:
                                    k += 16
                                    continue
                                break
                            k += r + 1
                            if k > 64:
                                break
                            val = self._extend(self._get_bits(s), s)
                            blk[ZIGZAG[k - 1]] = val * self.qt[tq][ZIGZAG[k - 1]]
                        # v4.3 窗口门控: 窗口外的块只解系数跳过 IDCT
                        r0 = ((mcu_row * v) + by) * 8
                        syv = sy_c[idx]
                        if (r0 + 8) * syv <= ys or r0 * syv >= ye:
                            continue
                        pix = self._idct(blk)
                        plane = planes[idx]
                        bcol = (mcu % self.mcux) * h + bx
                        x0 = bcol * 8
                        ph_ = len(plane)
                        pw_ = len(plane[0]) if ph_ else 0
                        for yy in range(8):
                            py = r0 + yy
                            if py >= ph_:
                                continue
                            row = plane[py]
                            for xx in range(8):
                                px = x0 + xx
                                if px >= pw_:
                                    continue
                                row[px] = pix[yy * 8 + xx] + 128
        # v4.3 物化: 仅 [ys, min(ye,h)) 行
        W = self.w
        y_end = min(ye, self.h)
        rPo = []
        gPo = []
        bPo = []
        if len(self.comp) >= 3:
            syY = sy_c[0]; syCb = sy_c[1]; syCr = sy_c[2]
            sxY = self.hmax // self.comp[0][0] if self.hmax >= self.comp[0][0] else 1
            sxCb = self.hmax // self.comp[1][0] if self.hmax >= self.comp[1][0] else 1
            sxCr = self.hmax // self.comp[2][0] if self.hmax >= self.comp[2][0] else 1
            pY = planes[0]; pCb = planes[1]; pCr = planes[2]
            for y in range(max(0, ys), y_end):
                yr = pY[y // syY]
                if sxY != 1:
                    yr = [yr[x // sxY] for x in range(W)]
                if sxCb == 1 and sxCr == 1:
                    cbr = pCb[y // syCb]
                    crr = pCr[y // syCr]
                else:
                    cb_raw = pCb[y // syCb]
                    cr_raw = pCr[y // syCr]
                    cbr = [cb_raw[x // sxCb] for x in range(W)]
                    crr = [cr_raw[x // sxCr] for x in range(W)]
                rr = [0] * W
                gg = [0] * W
                bb = [0] * W
                for x in range(W):
                    Y = yr[x]
                    cbb = cbr[x] - 128.0
                    crr_ = crr[x] - 128.0
                    R = Y + 1.402 * crr_
                    G = Y - 0.344136 * cbb - 0.714136 * crr_
                    B = Y + 1.772 * cbb
                    rr[x] = 255 if R > 254.5 else (0 if R < 0 else int(R + 0.5))
                    gg[x] = 255 if G > 254.5 else (0 if G < 0 else int(G + 0.5))
                    bb[x] = 255 if B > 254.5 else (0 if B < 0 else int(B + 0.5))
                rPo.append(rr)
                gPo.append(gg)
                bPo.append(bb)
        else:
            syY = sy_c[0]
            pY = planes[0]
            for y in range(max(0, ys), y_end):
                yr = pY[y // syY]
                rPo.append(yr[:W])
                gPo.append(yr[:W])
                bPo.append(yr[:W])
        return rPo, gPo, bPo

    def _parse_dqt(self, seg):
        i = 0
        while i < len(seg):
            pq = seg[i] >> 4
            tq = seg[i] & 15
            i += 1
            tbl = [0] * 64
            for j in range(64):
                tbl[ZIGZAG[j]] = seg[i + j] if pq == 0 else ((seg[i + 2 * j] << 8) | seg[i + 2 * j + 1])
            self.qt[tq] = tbl
            i += 64 if pq == 0 else 128

    def _parse_sof(self, seg):
        self.prec = seg[0]
        self.h = (seg[1] << 8) | seg[2]
        self.w = (seg[3] << 8) | seg[4]
        nc = seg[5]
        self.comp = []
        for i in range(nc):
            cid = seg[6 + 3 * i]
            hv = seg[7 + 3 * i]
            tq = seg[8 + 3 * i]
            self.comp.append((hv >> 4, hv & 15, tq, cid))
        hmax = max(c[0] for c in self.comp)
        vmax = max(c[1] for c in self.comp)
        self.hmax = hmax
        self.vmax = vmax
        self.mcux = (self.w + 8 * hmax - 1) // (8 * hmax)
        self.mcuy = (self.h + 8 * vmax - 1) // (8 * vmax)

    def _parse_dht(self, seg):
        i = 0
        while i < len(seg):
            tc = seg[i] >> 4
            th = seg[i] & 15
            i += 1
            counts = seg[i:i + 16]
            i += 16
            vals = []
            for c in counts:
                vals.extend(seg[i:i + c] if isinstance(seg, bytes) else [seg[i + k] for k in range(c)])
                i += c
            self._build_huff(tc, th, list(counts), vals)

    def _build_huff(self, tc, th, counts, vals):
        code = 0
        k = 0
        table = {}
        for l in range(16):
            for _ in range(counts[l]):
                table[(l + 1, code)] = vals[k]
                k += 1
                code += 1
            code <<= 1
        if tc == 0:
            self.huff_dc[th] = table
        else:
            self.huff_ac[th] = table
        self.huff_rev = getattr(self, 'huff_rev', {})
        self.huff_rev[(tc, th)] = {}
        code = 0
        k = 0
        for l in range(16):
            for _ in range(counts[l]):
                if k < len(vals):
                    self.huff_rev[(tc, th)][(l + 1, code)] = vals[k]
                k += 1
                code += 1
            code <<= 1

    def _get_bits(self, n):
        v = 0
        for _ in range(n):
            if self.bitpos >= 8:
                self.bitpos = 0
                self.bytepos += 1
                if self.bytepos >= len(self.scan):
                    raise ValueError("bits eof")
                # JPEG 字节填充: 数据 FF 在流中写作 FF 00
                # => 上一原始字节为 FF 时, 当前 00 是填充字节, 跳过之
                if (self.scan[self.bytepos] == 0x00 and self.bytepos > 0
                        and self.scan[self.bytepos - 1] == 0xFF):
                    self.bytepos += 1
                    if self.bytepos >= len(self.scan):
                        raise ValueError("bits eof")
                self.cur = self.scan[self.bytepos]
            v = (v << 1) | ((self.cur >> (7 - self.bitpos)) & 1)
            self.bitpos += 1
        return v

    def _decode_huff(self, table):
        code = 0
        for l in range(1, 17):
            code = (code << 1) | self._get_bits(1)
            if (l, code) in table:
                return table[(l, code)]
        raise ValueError("huff miss")

    def _extend(self, v, n):
        if n == 0:
            return 0
        return v if v >= (1 << (n - 1)) else v - (1 << n) + 1

    def _idct(self, blk):
        # v4.3 可分离 IDCT: 行/列两趟 2×512 MAC (朴素版 4096, ~4×提速)
        CT = COS_T
        c0 = 0.70710678
        tmp = [0.0] * 64
        out = [0.0] * 64
        for v in range(8):
            vb = v * 8
            b0 = blk[vb]; b1 = blk[vb + 1]; b2 = blk[vb + 2]; b3 = blk[vb + 3]
            b4 = blk[vb + 4]; b5 = blk[vb + 5]; b6 = blk[vb + 6]; b7 = blk[vb + 7]
            for x in range(8):
                k = 2 * x + 1
                tmp[vb + x] = (b0 * c0 + b1 * CT[k]
                               + b2 * CT[2 * k] + b3 * CT[3 * k]
                               + b4 * CT[4 * k] + b5 * CT[5 * k]
                               + b6 * CT[6 * k] + b7 * CT[7 * k])
        for y in range(8):
            ky = 2 * y + 1
            for x in range(8):
                s = (tmp[x] * c0 + tmp[8 + x] * CT[ky]
                     + tmp[16 + x] * CT[2 * ky] + tmp[24 + x] * CT[3 * ky]
                     + tmp[32 + x] * CT[4 * ky] + tmp[40 + x] * CT[5 * ky]
                     + tmp[48 + x] * CT[6 * ky] + tmp[56 + x] * CT[7 * ky])
                out[y * 8 + x] = s * 0.25
        return out

    def _parse_sos(self):
        ns = self.d[self.pos + 0]
        self.pos += 1
        comps = []
        for i in range(ns):
            cid = self.d[self.pos]
            tabs = self.d[self.pos + 1]
            comps.append((cid, tabs >> 4, tabs & 15))
            self.pos += 2
        self.pos += 3  # Ss, Se, AhAl
        self.scan = self.d[self.pos:]
        self.bytepos = -1  # 首次 advance 落在 0
        self.bitpos = 8
        self.cur = 0
        # 输出画布 (仅亮度, 全尺寸)
        gray = [[128] * self.w for _ in range(self.h)]
        pred = [0] * ns
        mcus = self.mcux * self.mcuy
        comp_map = {}
        for idx, c in enumerate(self.comp):
            comp_map[c[3]] = (idx, c)
        for mcu in range(mcus):
            for (cid, dc_t, ac_t) in comps:
                idx, c = comp_map[cid]
                h, v, tq = c[0], c[1], c[2]
                for by in range(v):
                    for bx in range(h):
                        blk = [0] * 64
                        t = self._decode_huff(self.huff_dc[dc_t])
                        diff = self._extend(self._get_bits(t), t) if t else 0
                        pred[idx] += diff
                        blk[0] = pred[idx] * self.qt[tq][0]
                        k = 1
                        while k < 64:
                            rs = self._decode_huff(self.huff_ac[ac_t])
                            r = rs >> 4
                            s = rs & 15
                            if s == 0:
                                if r == 15:
                                    k += 16
                                    continue
                                break
                            k += r + 1
                            if k > 64:
                                break
                            val = self._extend(self._get_bits(s), s)
                            blk[ZIGZAG[k - 1]] = val * self.qt[tq][ZIGZAG[k - 1]]
                        # 仅写入 Y (第一个) 分量; 色度块只解码保持流同步
                        if idx != 0:
                            continue
                        pix = self._idct(blk)
                        # MCU 内块位置
                        mx = (mcu % self.mcux) * self.hmax
                        my = (mcu // self.mcux) * self.vmax
                        x0 = (mx + bx) * 8
                        y0 = (my + by) * 8
                        for yy in range(8):
                            py = y0 + yy
                            if py >= self.h:
                                continue
                            row = gray[py]
                            for xx in range(8):
                                px = x0 + xx
                                if px >= self.w:
                                    continue
                                v = pix[yy * 8 + xx] + 128
                                if v < 0:
                                    v = 0
                                elif v > 255:
                                    v = 255
                                row[px] = int(v)
        self.gray = gray
        return gray


ZIGZAG = [
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63
]
COS_T = {}
for _u in range(8):
    for _x in range(16):
        pass
for _i in range(8 * 16):
    _u = _i // 16
    _x = _i % 16
    COS_T[(2 * _x + 1) * _u] = None
import math as _math


def math_sqrt(v):
    return _math.sqrt(v)
for _k in list(COS_T.keys()):
    COS_T[_k] = _math.cos(_k * _math.pi / 16.0)


# ============================================================
# PNG 模板解码 (取形状 x 范围与宽度)
# ============================================================
def _png_shape(b):
    try:
        payload = b.split(',', 1)[1] if (isinstance(b, str) and b.startswith('data:')) else b
        data = base64.b64decode(payload)
        if data[:8] != b'\x89PNG\r\n\x1a\n':
            return None
        pos = 8
        w = h = 0
        idat = b''
        while pos < len(data):
            ln = struct.unpack('>I', data[pos:pos + 4])[0]
            typ = data[pos + 4:pos + 8]
            chunk = data[pos + 8:pos + 8 + ln]
            if typ == b'IHDR':
                w = struct.unpack('>I', chunk[0:4])[0]
                h = struct.unpack('>I', chunk[4:8])[0]
                bd = chunk[8]
                ct = chunk[9]
            elif typ == b'IDAT':
                idat += chunk
            pos += 12 + ln
        if bd != 8 or ct not in (6, 2):
            return None
        raw = zlib.decompress(idat)
        ch = 4 if ct == 6 else 3
        stride = w * ch
        x0, x1, y0, y1 = w, -1, h, -1
        prev = None
        rows = []
        p = 0
        for yy in range(h):
            ft = raw[p]
            p += 1
            line = bytearray(raw[p:p + stride])
            p += stride
            if ft == 1 and prev:
                for i in range(ch, stride):
                    line[i] = (line[i] + line[i - ch]) & 0xFF
            elif ft == 2 and prev:
                for i in range(stride):
                    line[i] = (line[i] + prev[i]) & 0xFF
            elif ft == 3 and prev:
                for i in range(stride):
                    a = line[i - ch] if i >= ch else 0
                    line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
            elif ft == 4 and prev:
                for i in range(stride):
                    a = line[i - ch] if i >= ch else 0
                    b_ = prev[i]
                    c = prev[i - ch] if i >= ch else 0
                    pa = abs(b_ - c)
                    pb = abs(a - c)
                    pc = abs(a + b_ - 2 * c)
                    pr = a if (pa <= pb and pa <= pc) else (b_ if pb <= pc else c)
                    line[i] = (line[i] + pr) & 0xFF
            prev = line
            rows.append(line)
        for yy in range(h):
            row = rows[yy]
            for xx in range(w):
                o = xx * ch
                alpha = row[o + 3] if ch == 4 else 255
                if alpha > 128:
                    if xx < x0:
                        x0 = xx
                    if xx > x1:
                        x1 = xx
                    if yy < y0:
                        y0 = yy
                    if yy > y1:
                        y1 = yy
        if x1 < 0:
            return None
        return (x0, x1, y0, y1)
    except Exception:
        return None


# ============================================================
# Spider
# ============================================================
class Spider(_BaseSpider):
    NAME = "山有木兮"
    session = ""
    trace_ids = []
    report_header = "X-Report-Id"
    cid = ""
    vtoken = ""
    cfg = None
    host = HOST
    api = HOST + "/api"
    # v3.1 类属性兜底 (壳跳过 init 直接调接口时不炸)
    _circuit_ts = 0.0
    _last_api_ts = 0.0
    _detail_fail_body = None
    _detail_fail_until = 0.0
    _diag_v = ""
    _pdir = None
    _verify_tries = 0

    def init(self, extend=""):
        if isinstance(extend, dict):
            ext = extend
        else:
            ext = {}
            try:
                if extend and isinstance(extend, str) and extend.strip().startswith("{"):
                    ext = json.loads(extend)
                elif extend and isinstance(extend, str) and extend.strip().startswith("http"):
                    ext = {"url": extend.strip()}
            except Exception:
                ext = {}
        self.ext = ext
        self.host = ext.get("host") or HOST
        self.api = self.host + "/api"
        # v4.4: 设备指纹持久化 — 重启后仍是同一"设备" (前端 localStorage 同款语义,
        # 风控引擎对「同 IP 反复出现新 clientId」加权)
        self._pdir = None
        saved, self._pdir = _persist_load()
        now_ms = int(time.time() * 1000)
        if saved and isinstance(saved.get("cid"), str) and len(saved.get("cid")) in (32, 36):
            self.cid = saved["cid"]
        else:
            self.cid = _cid()
            self._pdir = _persist_save({"cid": self.cid, "vtoken": "", "ts": now_ms}, self._pdir)
        # vtoken 持久化 2h 内复用 (失效自愈: 业务 1004 时清掉重解)
        try:
            vt = saved.get("vtoken") or ""
            vts = int(saved.get("ts") or 0)
            self.vtoken = vt if (vt and now_ms - vts < 7200000) else ""
        except Exception:
            self.vtoken = ""
        self._diag_v = ""
        self.session = ""
        self.trace_ids = []
        self.report_header = "X-Report-Id"
        self.cat_cache = None
        self._filter_cache = {}
        self._search_cache = {}
        self._type_cache = {}
        self._diag = True
        # v3.1 防滚雪球三件套 (速搜 v2.7 血泪/红果熔断器)
        self._circuit_ts = 0.0     # verify 失败熔断起始时刻
        self._last_api_ts = 0.0    # 全局节流
        self._detail_fail_until = 0.0  # detail 失败缓存到期
        self._detail_fail_body = None

    # ---------- HTTP ----------
    def fetch(self, url, headers=None, post=None, timeout=15):
        # 纯 urllib (猫头鹰金标准姿势; base.spider.fetch 签名不可靠且会被本方法遮蔽)
        try:
            hdrs = dict(headers or {})
            if post is not None:
                body = json.dumps(post).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json;charset=UTF-8")
                req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
            else:
                req = urllib.request.Request(url, headers=hdrs)
            if _SSL_CTX is not None and url.startswith("https"):
                resp = urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)
            else:
                resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.read().decode("utf-8", "replace")
        except Exception:
            return ""

    def _jget(self, url, headers=None, post=None, timeout=15):
        body = self.fetch(url, headers=headers, post=post, timeout=timeout)
        if not body:
            return None
        try:
            return json.loads(body)
        except Exception:
            return None

    # ---------- 签名 ----------
    def timestamp(self):
        r = str(int(time.time() * 1000))
        s = sum(int(c) for c in r[:-1])
        return r[:-1] + str(s % 10)

    def _xor(self, s):
        # Gw(e): e 为十六进制串; 每 2 hex 字符 -> 字节, 与密钥串逐字符异或
        key = "0x1A2B3C4D5E6F7A8B9C"
        out = []
        try:
            for i in range(0, len(s) - 1, 2):
                b = int(s[i:i + 2], 16)
                k = ord(key[(i // 2) % len(key)])
                out.append(chr(b ^ k))
        except Exception:
            return s
        return ''.join(out)

    def _xor_legacy(self, s):
        key = 0x1A2B3C4D5E6F7A8B9C
        out = []
        for i, ch in enumerate(s):
            k = (key >> ((i % 8) * 8)) & 0xFF
            out.append(chr(ord(ch) ^ k))
        return ''.join(out)

    def sign(self, path, ts):
        # u = traceIds 顺序拼接 {p,t,s}; s = "symx_" + session; p 去掉 query
        p = path.split("?")[0]
        d = {"p": p, "t": ts, "s": "symx_" + self.session}
        u = ""
        for c in (self.trace_ids or []):
            u += d.get(c, "")
        u = u.replace("1", "i").replace("0", "o").replace("5", "s")
        return hmac.new(self.session.encode("utf-8"), u.encode("utf-8"), hashlib.sha256).hexdigest()

    def _headers(self, path, extra=None):
        ts = self.timestamp()
        # v4.6 全套对齐 Electron APP 真实请求形态:
        #   UA=SYMX_WINDOWS (主进程覆盖) / 无 Referer / 无 Origin (axios 直连)
        #   X-Platform=windows + X-Version + X-Verify-Token 恒带 (拦截器)
        h = {
            "User-Agent": API_UA,
            "X-Platform": "windows",
            "X-Version": APP_VERSION,
            "X-Timestamp": ts,
            self.report_header: self.sign(path, ts),
            "X-Client-Id": self.cid,
            "X-Verify-Token": self.vtoken or "",
        }
        if extra:
            h.update(extra)
        return h

    def _ensure_session(self):
        if self.session:
            return True
        d = self._jget(self.api + "/system/config", headers=self._headers("/system/config"))
        try:
            if d and d.get("code") == 200:
                sec = d.get("data")
                if isinstance(sec, str):
                    try:
                        sec = json.loads(sec)
                    except Exception:
                        sec = {}
                if not isinstance(sec, dict):
                    return False
                # reportId/traceId/session 三个字段为 Gw 十六进制加密串
                self.session = self._xor(str(sec.get("session") or "")) if sec.get("session") else ""
                tr = self._xor(str(sec.get("traceId") or "")) if sec.get("traceId") else ""
                if not tr:
                    tr = ""
                self.trace_ids = list(tr)
                rid = self._xor(str(sec.get("reportId") or "")) if sec.get("reportId") else ""
                self.report_header = rid or "X-Report-Id"
                return True
        except Exception:
            pass
        # 兜底: 与 JS 默认态一致 (traceId="" → 签名输入空串, session="" → 空 key)
        self.trace_ids = []
        return False

    # ---------- 滑块 (TianaiCaptcha) ----------
    def _png_rgb(self, b64):
        # 轻量 PNG 解码 → (r, g, b, a) 平面
        try:
            data = base64.b64decode(b64.split(',')[1] if b64.startswith('data:') else b64)
            pos = 8
            w = h = 0
            idat = b''
            ctype = 6
            while pos < len(data):
                ln = int.from_bytes(data[pos:pos + 4], 'big')
                typ = data[pos + 4:pos + 8]
                chunk = data[pos + 8:pos + 8 + ln]
                if typ == b'IHDR':
                    w = int.from_bytes(chunk[0:4], 'big')
                    h = int.from_bytes(chunk[4:8], 'big')
                    ctype = chunk[9]
                elif typ == b'IDAT':
                    idat += chunk
                pos += 12 + ln
            raw = zlib.decompress(idat)
            ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype, 4)
            stride = w * ch
            rows = []
            prev = [0] * stride
            p = 0
            for y in range(h):
                ft = raw[p]
                p += 1
                line = list(raw[p:p + stride])
                p += stride
                if ft == 1:
                    for i in range(ch, stride):
                        line[i] = (line[i] + line[i - ch]) & 0xFF
                elif ft == 2:
                    for i in range(stride):
                        line[i] = (line[i] + prev[i]) & 0xFF
                elif ft == 3:
                    for i in range(stride):
                        a = line[i - ch] if i >= ch else 0
                        line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
                elif ft == 4:
                    for i in range(stride):
                        a = line[i - ch] if i >= ch else 0
                        b_ = prev[i]
                        c = prev[i - ch] if i >= ch else 0
                        pa, pb, pc = abs(a - b_), abs(b_ - c), abs(a - c)
                        pr = a if (pa <= pb and pa <= pc) else (b_ if pb <= pc else c)
                        line[i] = (line[i] + pr) & 0xFF
                prev = line
                rows.append(line)
            rP = [[0] * w for _ in range(h)]
            gP = [[0] * w for _ in range(h)]
            bP = [[0] * w for _ in range(h)]
            aP = [[255] * w for _ in range(h)]
            for y in range(h):
                row = rows[y]
                rr = rP[y]
                gg = gP[y]
                bb = bP[y]
                aa = aP[y]
                for x in range(w):
                    o = x * ch
                    if ch >= 3:
                        rr[x] = row[o]
                        gg[x] = row[o + 1]
                        bb[x] = row[o + 2]
                        if ch == 4:
                            aa[x] = row[o + 3]
                    elif ch == 1:
                        rr[x] = gg[x] = bb[x] = row[o]
                        if ch == 2:
                            aa[x] = row[o + 1]
                    elif ch == 2:
                        aa[x] = row[o + 1]
            return rP, gP, bP, aP
        except Exception:
            return None, None, None, None

    def _locate_notch(self, bg_b64, tp_b64):
        # v4.3 提速版: 模板 PNG 单次解码 + 背景窗口行解码 + 单遍统计 NCC
        #  + 粗扫提名 top6 → 全采样终扫 (精度 = v4.1, 速度 ~20×)
        # 返回 (randomX, score); score < 0.42 调用方应弃图重取
        try:
            tr, tg, tb, ta = self._png_rgb(tp_b64)
        except Exception:
            return None, 0.0
        if not tr or not ta:
            return None, 0.0
        Ht = len(ta)
        Wt = len(ta[0]) if Ht else 0
        if not Wt:
            return None, 0.0
        # alpha 形状框 (模板缺口形状的包围盒)
        x0 = None
        x1 = -1
        y0 = None
        y1 = -1
        for yy in range(Ht):
            arow = ta[yy]
            for xx in range(Wt):
                if arow[xx] > 128:
                    if x0 is None or xx < x0:
                        x0 = xx
                    if xx > x1:
                        x1 = xx
                    if y0 is None:
                        y0 = yy
                    y1 = yy
        if x0 is None:
            return None, 0.0
        tw = x1 - x0 + 1
        th = y1 - y0 + 1
        if tw < 12 or th < 12:
            return None, 0.0
        # 掩码坐标 + 模板彩色中心化向量
        coords = []
        for yy in range(th):
            arow = ta[y0 + yy]
            for xx in range(tw):
                if arow[x0 + xx] > 128:
                    coords.append((xx, yy))
        n = len(coords)
        if n < 12:
            return None, 0.0
        mr = mg = mb = 0.0
        for (xx, yy) in coords:
            mr += tr[y0 + yy][x0 + xx]
            mg += tg[y0 + yy][x0 + xx]
            mb += tb[y0 + yy][x0 + xx]
        mr /= n
        mg /= n
        mb /= n
        tc = []
        for (xx, yy) in coords:
            tc.append((tr[y0 + yy][x0 + xx] - mr,
                       tg[y0 + yy][x0 + xx] - mg,
                       tb[y0 + yy][x0 + xx] - mb))
        tn2 = 0.0
        for (a1, b1, c1) in tc:
            tn2 += a1 * a1 + b1 * b1 + c1 * c1
        if tn2 <= 0:
            return None, 0.0
        tn = math_sqrt(tn2)
        # 背景: 只解码掩码行 [y0, y1+1) (窗口 IDCT 提速)
        try:
            data = base64.b64decode(bg_b64.split(',')[1] if bg_b64.startswith('data:') else bg_b64)
            rP, gP, bP = _JpegGray(data).decode_rgb_rows(y0, y1 + 1)
        except Exception:
            return None, 0.0
        if not rP or len(rP) < th:
            return None, 0.0
        W = len(rP[0])
        if W < tw + 16:
            return None, 0.0

        def _prep(pts):
            # 行引用预绑定: 内层循环纯标量索引
            out = []
            for (xx, yy) in pts:
                out.append((rP[yy], gP[yy], bP[yy], xx))
            return out

        def _tsum(tcv):
            # v4.3fix: 子集模板分量和 (全集时为 0, 子集时不为 0 必须补偿)
            s1 = s2 = s3 = 0.0
            for (a1, b1, c1) in tcv:
                s1 += a1
                s2 += b1
                s3 += c1
            return (s1, s2, s3)

        def _ncc(x, prep, tcv, tsum):
            # 单遍统计 NCC (与两遍法严格等价):
            # numerator = Σt·p - p̄·Σt_sub (子集模板和补偿项)
            m2 = len(prep)
            sr = sg = sb = 0.0
            qr = qg = qb = 0.0
            dot = 0.0
            for i in range(m2):
                rr, gg, bb, xx = prep[i]
                a = rr[x + xx]
                b = gg[x + xx]
                c = bb[x + xx]
                sr += a
                sg += b
                sb += c
                qr += a * a
                qg += b * b
                qb += c * c
                a1, b1, c1 = tcv[i]
                dot += a1 * a + b1 * b + c1 * c
            pn2 = (qr + qg + qb) - (sr * sr + sg * sg + sb * sb) / m2
            if pn2 <= 0:
                return -1.0
            num = dot - (sr * tsum[0] + sg * tsum[1] + sb * tsum[2]) / m2
            return num / (tn * math_sqrt(pn2))

        # 粗扫: 1/16 采样 (xx%4,yy%4), x 步4 → top4 候选
        pts_a = []
        tc_a = []
        for i in range(n):
            xx, yy = coords[i]
            if (xx % 4 == 0) and (yy % 4 == 0):
                pts_a.append(coords[i])
                tc_a.append(tc[i])
        if len(pts_a) < 8:
            for i in range(0, n, 4):
                pts_a.append(coords[i])
                tc_a.append(tc[i])
        prep_a = _prep(pts_a)
        tsum_a = _tsum(tc_a)
        coarse = []
        for x in range(8, W - tw - 8, 4):
            coarse.append((_ncc(x, prep_a, tc_a, tsum_a), x))
        coarse.sort(reverse=True)
        top = []
        for s, x in coarse:
            if all(abs(x - t) > 8 for t in top):
                top.append(x)
            if len(top) >= 6:
                break
        if not top:
            return None, 0.0
        # v4.3fix 终扫: top6 候选 ±6 全采样直接定最优
        # (中间 1/4 采样级实测会选错峰 — NCC 只占 ~0.01s, 没必要省)
        prep_f = _prep(coords)
        tsum_f = _tsum(tc)
        fb = None
        for base in top:
            for x in range(max(8, base - 6), min(W - tw - 8, base + 6) + 1):
                s = _ncc(x, prep_f, tc, tsum_f)
                if fb is None or s > fb[0]:
                    fb = (s, x)
        if fb is None:
            return None, 0.0
        return fb[1] - x0, fb[0]

    def _track(self, delta):
        duration = random.randint(900, 1600)
        n = random.randint(25, 40)
        st = int(time.time() * 1000) - random.randint(1500, 4000)
        px = random.randint(100, 200)
        py = random.randint(300, 600)
        over = random.randint(3, 9)
        final = delta + over
        track = [{"x": px, "y": py, "type": "down", "t": 0}]
        for i in range(1, n):
            p = i / float(n - 1)
            e = 1 - (1 - p) ** 3
            track.append({"x": int(px + final * e), "y": py + random.randint(-4, 4),
                          "type": "move", "t": int(duration * p)})
        bn = random.randint(3, 7)
        tl = duration
        for j in range(1, bn + 1):
            p = j / float(bn)
            tl = int(duration * (1 + 0.15 * p))
            track.append({"x": int(px + final - over * p), "y": py + random.randint(-3, 3),
                          "type": "move", "t": tl})
        t_up = tl + random.randint(30, 120)
        track.append({"x": px + delta, "y": py, "type": "up", "t": t_up})
        # v4.0 关键修复: stopTime 必须是绝对墙钟 (st + 总轨道时长 + 尾隙)
        # 旧版 et=相对时长(~2000ms) → 服务端 stopTime-startTime=负天文数字
        # → 4001 基础校验失败, 定位再准也到不了位置校验 (solve_final.py 成功案例对照实证)
        return track, st, st + t_up + random.randint(150, 500)

    def _verify_headers(self):
        # 前端 TAC 同款干净头 (useVerifyPage 实证): 只有 X-Platform + X-Client-Id
        # 不带签名头、不带 X-Verify-Token (空头会被网关解析成无效 → 1004 死循环)
        return {
            "User-Agent": API_UA,
            "X-Platform": "windows",
            "X-Client-Id": self.cid,
        }

    _srv_off = None  # 服务器-本机时钟偏移 (ms), 类级缓存

    def _server_offset(self):
        # v4.4: 电视盒子时钟常有偏差; startTime/stopTime 用服务器时钟算,
        # 防墙钟跳变被 TianaiCaptcha 判为重放 (沙箱 NTP 对时测不出)
        if Spider._srv_off is not None:
            return Spider._srv_off
        off = 0
        try:
            req = urllib.request.Request(self.api + "/system/config",
                                         headers=self._verify_headers())
            if _SSL_CTX is not None and self.api.startswith("https"):
                resp = urllib.request.urlopen(req, timeout=10, context=_SSL_CTX)
            else:
                resp = urllib.request.urlopen(req, timeout=10)
            dh = resp.headers.get("Date") or ""
            if dh:
                import email.utils
                dt = email.utils.parsedate_to_datetime(dh)
                off = int(dt.timestamp() * 1000) - int(time.time() * 1000)
        except Exception:
            off = 0
        Spider._srv_off = off
        return off

    def _persist_token(self):
        # v4.4: cid+vtoken 落盘, 重启免重解滑块
        try:
            _persist_save({"cid": self.cid, "vtoken": self.vtoken,
                           "ts": int(time.time() * 1000)}, self._pdir)
        except Exception:
            pass

    def handle_1004(self, path):
        # POST /auth/verify/generate → 定位 → POST /auth/verify → token
        # v3.0: verify 系列请求一律走 _verify_headers (前端 TAC 同款)
        # v4.4: vtoken 失效自愈 + 服务器时钟对齐 + 子诊断码 (D3 [G/L/V])
        self._diag_v = ""
        self._verify_tries = 0
        try:
            if self.vtoken:
                # 带 token 还吃 1004 = token 失效 → 清掉 (磁盘同步) 再重解
                self.vtoken = ""
                self._persist_token()
            t_gen = time.time()
            d = self._jget(self.api + "/auth/verify/generate",
                           headers=self._verify_headers(), post={})
            if not d or d.get("code") != 200:
                # v4.6.1: generate 瞬态失败 (G500 实证: 重进即恢复 = 服务抖动) → 退避重试一次
                time.sleep(1.5)
                d = self._jget(self.api + "/auth/verify/generate",
                               headers=self._verify_headers(), post={})
                if not d or d.get("code") != 200:
                    self._diag_v = "G%s" % (str((d or {}).get("code") if d else 0)[:6])
                    return False
            info = d.get("data") or {}
            # v4.4.1: 服务端按概率混发 CONCAT(拼接)/ROTATE(旋转)/IMAGE_CLICK(点选) 验证码,
            # 前端 TAC web 端默认 concatImageSwitch 开启 — 非 SLIDER 类型重取新图
            # (同一 IP 连续 4 次全非滑块 = 类型锁定, 概率极低, 熔断退避)
            type_retry = 0
            while info.get("type") != "SLIDER" and type_retry < 4:
                type_retry += 1
                time.sleep(0.6)
                d = self._jget(self.api + "/auth/verify/generate",
                               headers=self._verify_headers(), post={})
                if not d or d.get("code") != 200:
                    self._diag_v = "G%s" % (str((d or {}).get("code") if d else 0)[:6])
                    return False
                info = d.get("data") or {}
            if info.get("type") != "SLIDER":
                self._diag_v = "T%s" % str(info.get("type"))[:6]
                return False
            off = self._server_offset()
            # v4.4: 6 次取图预算 (难图弃图很便宜), 但 verify 提交最多 2 次
            loc_fail = 0
            for _ in range(6):
                if self._verify_tries >= 2:
                    break
                delta, score = self._locate_notch(info.get("backgroundImage") or "", info.get("templateImage") or "")
                if delta is None or delta < 20 or score < 0.45:
                    # v4.1: 低置信度图直接丢弃 (不浪费 verify 请求)
                    loc_fail += 1
                    self._diag_v = "L%.2f" % (score or 0.0)
                    d = self._jget(self.api + "/auth/verify/generate",
                                   headers=self._verify_headers(), post={})
                    if not d or d.get("code") != 200:
                        self._diag_v = "G%s" % (str((d or {}).get("code") if d else 0)[:6])
                        return False
                    info = d.get("data") or {}
                    t_gen = time.time()
                    continue
                track, st, et = self._track(delta)
                # v4.4: 时间对平移到服务器时钟 (盒子时钟偏差防护)
                if off:
                    st += off
                    et += off
                body = {
                    "id": info.get("id"),
                    "data": {
                        "bgImageWidth": info.get("backgroundImageWidth") or 600,
                        "bgImageHeight": info.get("backgroundImageHeight") or 360,
                        "sliderImageWidth": info.get("templateImageWidth") or 110,
                        "sliderImageHeight": info.get("templateImageHeight") or 360,
                        "startTime": st, "stopTime": et,
                        "trackList": track,
                    },
                }
                # v4.3 墙钟反机器人: generate→verify <2s 一律 4001 (变量隔离实验实证:
                # 4.6s 提交 3/4 通过 200, 1.2s 提交全灭) — 解题再快也要装满人类时间
                el = time.time() - t_gen
                if el < 3.2:
                    time.sleep(3.2 - el)
                self._verify_tries += 1
                r = self._jget(self.api + "/auth/verify",
                               headers=self._verify_headers(), post=body)
                if r and r.get("code") == 200:
                    tk = (r.get("data") or {})
                    self.vtoken = tk.get("token") or tk.get("captchaId") or ""
                    self._diag_v = ""
                    self._persist_token()  # v4.4: 重启免重解
                    return True
                self._diag_v = "V%s" % str((r or {}).get("code") or 0)[:6]
                d = self._jget(self.api + "/auth/verify/generate",
                               headers=self._verify_headers(), post={})
                if not d or d.get("code") != 200:
                    return False
                info = d.get("data") or {}
                t_gen = time.time()
            return False
        except Exception as e:
            # v4.4: 异常路径也带诊断码 (X 前缀 = 代码异常, 区别于服务端拒绝)
            try:
                self._diag_v = "X" + str(e)[:12]
            except Exception:
                self._diag_v = "X"
            return False

    # ---------- 请求封装 ----------
    def api_get(self, path, params=None, retry_verify=True, timeout=15):
        # v3.1 全局节流 2.5s (速搜铁律: 请求密度是风控灰名单的燃料)
        # category/search (已验证豁免) 放行, 其余接口强制间隔
        if path not in ("/film/category", "/system/config", "/film/search", "/film/search/hot"):
            # v4.2 节流放宽: 密度风险由 3 轮上限+熔断兜底
            gap = time.time() - self._last_api_ts
            if gap < 0.8:
                time.sleep(0.8 - gap)
            self._last_api_ts = time.time()
        try:
            self._ensure_session()
            url = self.api + path
            if params:
                url += "?" + urllib.parse.urlencode(params)
            d = self._jget(url, headers=self._headers(path), timeout=timeout)
            if not d:
                return None
            c = d.get("code")
            if c == 1004 and retry_verify:
                if self._circuit_ts and (time.time() - self._circuit_ts) < 180:
                    # 熔断期内: 不再尝试滑块 (防滚雪球), 透传 1004
                    self._diag_v = "CD%d" % int(180 - (time.time() - self._circuit_ts))
                    return d
                if self.handle_1004(path):
                    self._circuit_ts = 0.0
                    return self.api_get(path, params, False, timeout)
                # verify 失败 → 熔断 180s (红果熔断器: 连续失败停止发请求防雪崩)
                self._circuit_ts = time.time()
                return d
            if c == 200:
                return d
            return d
        except Exception:
            return None

    # ---------- TVBox 接口 ----------
    def getName(self):
        return "山有木兮"

    def isVideoFormat(self, url):
        if not url:
            return False
        u = url.lower()
        return ("m3u8" in u) or ("mp4" in u) or ("mkv" in u) or (u.endswith(".flv")) or (".ts" in u)

    def homeContent(self, filter1=1):
        out = {"class": [], "list": []}
        try:
            d = self.api_get("/film/category")
            data = (d or {}).get("data") or []
            self.cat_cache = data
            cls = []
            for c in data:
                cls.append({"type_id": str(c.get("categoryId", "")), "type_name": c.get("categoryName", "")})
                # 首页推荐: 每类前几部
                for f in (c.get("filmList") or [])[:6]:
                    out["list"].append(self._vod(f, c.get("categoryId")))
            out["class"] = cls
        except Exception:
            pass
        return out

    def _vod(self, f, cid=None):
        vod = {
            "vod_id": str(f.get("id", "")),
            "vod_name": f.get("name", ""),
            "vod_pic": self._img(f.get("cover", "")),
            "vod_remarks": self._remark(f),
        }
        if cid is not None:
            vod["type_id"] = str(cid)
        return vod

    def _remark(self, f):
        parts = []
        sc = f.get("doubanScore")
        if sc and str(sc) != "0.0":
            parts.append(str(sc) + "分")
        us = f.get("updateStatus")
        if us:
            parts.append(str(us))
        return " ".join(parts) if parts else ""

    def homeVideoContent(self):
        try:
            d = self.api_get("/film/rank/quality")
            lst = (d or {}).get("data") or []
            return {"list": [self._vod(f) for f in lst[:20]]}
        except Exception:
            return {}

    def _filters(self, cid):
        if cid in self._filter_cache:
            return self._filter_cache[cid]
        d = self.api_get("/film/category/filter", params={"categoryId": cid})
        data = (d or {}).get("data") or {}
        ext = {"area": "地区", "year": "年代", "language": "语言", "sort": "排序"}
        f = []
        for key in ("area", "year", "language", "sort"):
            opts = data.get(key + "Options") or []
            if not opts:
                continue
            vals = [{"n": "全部", "v": ""}]
            # v4.6.2: 筛选项兼容两种形态 (web/APP 前端渲染实证):
            #   排序=对象数组 [{label, value}]; 地区/年份/语言=字符串数组
            for o in opts:
                if isinstance(o, dict):
                    ovl = o.get("value")
                    if isinstance(ovl, dict):
                        ovl = ovl.get("value") or ovl.get("id")
                    lbl = o.get("label") or str(ovl)
                    # label 可能也是 {label,value} 嵌套 (臻彩/腾讯模板同款防御)
                    if isinstance(lbl, dict):
                        lbl = lbl.get("label") or str(ovl)
                    ov = str(ovl if ovl is not None else o.get("id") or lbl)
                    vals.append({"n": str(lbl), "v": ov})
                else:
                    vals.append({"n": str(o), "v": str(o)})
            f.append({"key": key, "name": ext[key], "value": vals})
        # 子分类
        copts = data.get("categoryOptions") or []
        if copts:
            vals = [{"n": "全部", "v": ""}]
            for o in copts:
                if isinstance(o, dict):
                    vals.append({"n": str(o.get("value", "")), "v": str(o.get("value", ""))})
                else:
                    vals.append({"n": str(o), "v": str(o)})
            f.append({"key": "childCategoryId", "name": "分类", "value": vals})
        self._filter_cache[cid] = f
        return f

    def categoryContent(self, tid, pg=1, filter1=1, extend=None):
        out = {"list": [], "page": int(pg or 1), "pagecount": 1, "limit": 15, "total": 0}
        try:
            # extend 兼容 str/dict (内核可能传 JSON 字符串)
            if isinstance(extend, str) and extend.strip().startswith("{"):
                try:
                    extend = json.loads(extend)
                except Exception:
                    extend = {}
            if not isinstance(extend, dict):
                extend = {}
            try:
                pgn = int(pg)
            except Exception:
                pgn = 1
            # v4.6: 筛选参数对齐 APP null 语义 — APP 传 null 时 axios 直接不发该字段,
            # 爬虫旧版发空串 "" → 服务端把 "" 当无效值处理, 排序/筛选行为与 APP 不一致
            params = {
                "categoryId": str(tid),
                "pageNum": pgn,
                "pageSize": 15,
                "sort": extend.get("sort") or "updateTime",
            }
            for k in ("area", "year", "language"):
                v = extend.get(k)
                if v:
                    params[k] = str(v)
            cc = extend.get("childCategoryId")
            if cc:
                params["childCategoryId"] = str(cc)
            d = self.api_get("/film/category/list", params=params)
            data = (d or {}).get("data") or {}
            lst = data.get("list") or []
            total = data.get("total") or 0
            out["list"] = [self._vod(f) for f in lst]
            out["total"] = total
            out["page"] = pgn
            # pagecount 自适应: total 有效用 ceil; 否则满页续翻/空页收口 (布布流派)
            if total:
                pc = (total + 14) // 15
            elif lst:
                pc = pgn + 1
            else:
                pc = max(1, pgn - 1)
            out["pagecount"] = pc if pc >= 1 else 1
        except Exception:
            pass
        return out

    def _dfail(self, body):
        # v3.1: 失败结果缓存 60s, 壳的连续重试不再打到服务器 (速搜防滚雪球)
        self._detail_fail_body = body
        self._detail_fail_until = time.time() + 60
        return body

    def detailContent(self, ids):
        # 兜底防壳跳源: 详情失败也返回带名字的最小结构 (空详情会触发壳的聚合搜索→跳别的源)
        # v2.8 诊断模式: 失败原因编码进片名/选集 — 用户截图一次即可定位
        # v4.0: startTime/stopTime 单位错位根因修复
    # v3.1: 失败缓存 60s (壳频繁重进详情不再打服务器) + 多路径降级 (红果流派)
        fid = str(ids[0])
        now = time.time()
        if self._detail_fail_body is not None and now < self._detail_fail_until:
            return self._detail_fail_body
        fallback = {"list": [{"vod_id": fid, "vod_name": "加载失败 D1 详情请求空",
                              "vod_play_from": "提示", "vod_play_url": "D1-网络或签名失败#err"}]}
        try:
            d = self.api_get("/film/detail", params={"id": fid})
            if d is None and self._diag:
                # v4.5 APP 专属详情端点优先兜底 (Electron 逆向: /film/detail/play/app?id=)
                d2 = self.api_get("/film/detail/play/app", params={"id": fid})
                if d2 and d2.get("code") == 200 and (d2.get("data") or {}).get("playLineList"):
                    d = d2
            if d is None and self._diag:
                # 多路径降级: 备用详情端点 (管理端同源接口, 风控等级可能不同)
                d2 = self.api_get("/film/detail/play", params={"filmId": fid})
                if d2 and d2.get("code") == 200 and (d2.get("data") or {}).get("playLineList"):
                    d = d2
            if d is None:
                # 网络层失败 / 1004 自愈失败
                fb = "加载失败 D2 请求无响应"
                ep = "D2-检查网络或稍后重试#err"
                if self._diag:
                    fb += "(verify=" + ("ok" if self.vtoken else "no") + ")"
                return self._dfail({"list": [{"vod_id": fid, "vod_name": fb,
                                  "vod_play_from": "提示", "vod_play_url": ep}]})
            c = d.get("code")
            if c != 200:
                return self._dfail({"list": [{"vod_id": fid,
                    "vod_name": "加载失败 D3 %s%s" % (str(c), (" [" + self._diag_v + "]") if self._diag_v else ""),
                    "vod_play_from": "提示",
                    "vod_play_url": ("D3-" + str(d.get("message") or "服务端错误")[:20] +
                        ("#err 稍后3分钟再试" if c == 1004 else "#err"))}]})
            f = d.get("data") or {}
            if not f.get("name"):
                return self._dfail({"list": [{"vod_id": fid, "vod_name": "加载失败 D4 响应缺片名",
                    "vod_play_from": "提示", "vod_play_url": "D4-数据结构异常#err"}]})
            lines = f.get("playLineList") or []
            froms = []
            urls = []
            for ln in lines:
                pn = str(ln.get("playerName") or ln.get("playerId") or "线路")
                eps = ln.get("lines") or []
                if not eps:
                    continue
                # 不做首集探测 (前端同款: 全部线路展示, 点播时才处理; 探测会拖慢详情页)
                froms.append(pn)
                eu = []
                for e in eps:
                    eu.append(str(e.get("name", "")) + "$" + str(e.get("id", "")))
                urls.append("#".join(eu))
            if not froms:
                return self._dfail({"list": [{"vod_id": fid, "vod_name": "加载失败 D5 无可用线路",
                    "vod_play_from": "提示", "vod_play_url": "D5-该片线路为空#err"}]})
            vod = {
                "vod_id": fid,
                "vod_name": f.get("name", ""),
                "vod_pic": self._img(f.get("cover", "")),
                "type_name": f.get("categoryName", ""),
                "vod_year": f.get("year", ""),
                "vod_area": f.get("area", ""),
                "vod_director": f.get("director", ""),
                "vod_actor": f.get("actor", ""),
                "vod_remarks": self._remark(f),
                "vod_content": (f.get("blurb") or f.get("other") or "").strip(),
                "vod_play_from": "$$$".join(froms) if froms else "山有木兮",
                "vod_play_url": "$$$".join(urls),
            }
            return {"list": [vod]}
        except Exception:
            return self._dfail({"list": [{"vod_id": fid, "vod_name": "加载失败 D6 异常",
                "vod_play_from": "提示", "vod_play_url": "D6-内部错误#err"}]})

    def searchContent(self, *args, **kwargs):
        key = ""
        pg = None
        for a in args:
            if a is None:
                continue
            if isinstance(a, str) and not key:
                key = a
            elif isinstance(a, int) and not isinstance(a, bool):
                pg = a
        if not key and args:
            key = str(args[0] or "")
        if "key" in kwargs:
            key = kwargs["key"]
        if "wd" in kwargs:
            key = kwargs["wd"]
        if "pg" in kwargs and kwargs["pg"]:
            pg = kwargs["pg"]
        if not key:
            return {}
        try:
            d = self.api_get("/film/search", params={
                "keyword": key,
                "pageNum": int(pg) if pg else 1,
                "pageSize": 20,
            })
            data = (d or {}).get("data") or {}
            lst = data.get("list") or []
            return {"list": [self._vod(f) for f in lst]}
        except Exception:
            return {}

    def searchContentPage(self, *args, **kwargs):
        return self.searchContent(*args, **kwargs)

    def _zfe(self, u):
        # ZFe 源码逻辑: 含 http → /api/files/proxy?url=<enc> (服务器代理加防盗链头)
        #               相对路径 → "/api" + u; 空 → ""
        if not u or not isinstance(u, str):
            return ""
        if "http" in u:
            return self.api + "/files/proxy?url=" + urllib.parse.quote(u, safe="")
        if u.startswith("/"):
            if u.startswith("/api"):
                return self.host + u
            return self.host + "/api" + u
        return u

    def _probe(self, url, headers):
        # 32 字节探测直链可达性 (Range 判型): m3u8 播放器必然支持
        try:
            h = dict(headers)
            h["Range"] = "bytes=0-31"
            req = urllib.request.Request(url, headers=h)
            if _SSL_CTX is not None and url.startswith("https"):
                resp = urllib.request.urlopen(req, timeout=8, context=_SSL_CTX)
            else:
                resp = urllib.request.urlopen(req, timeout=8)
            data = resp.read(64)
            head = (data or b'')[:32]
            # #EXTM3U / ftyp(mp4) / 00000018 66 74 79 70 → 流直链
            if head.startswith(b'#EXTM3U') or head[4:8] == b'ftyp' or head[:4] in (b'\x00\x00\x00\x18',):
                return True
            ct = resp.headers.get('Content-Type', '') or ''
            if 'mpegurl' in ct or 'video' in ct or 'octet-stream' in ct:
                return True
            # HTML → 平台页, 直链不可用
            if head[:1] in (b'<', b'<!') or 'text/html' in ct:
                return False
            # 其它二进制 → 当作可播
            return True
        except Exception:
            return None  # 网络不可达 → 交代理

    def playerContent(self, flag, id, vipFlags=None):
        # v4.6 播放头对齐 APP 媒体代理 (main/index.js 实证):
        #   UA=Lavf/58.76.100 (ffmpeg UA, 直链 CDN 对它放行), 无 Referer
        # TVBox 播放器无 CORS 概念, 直链直接播
        headers = {"User-Agent": "Lavf/58.76.100"}
        try:
            lid = str(id)
            # 1. 缓存命中? (解析结果 10 分钟)
            ck = "play:" + lid
            hit = self._search_cache.get(ck) if hasattr(self, "_search_cache") else None
            direct = ""
            if hit and time.time() - hit[0] < 600:
                direct = hit[1]
            if not direct:
                # 2. 线路信息
                d = self.api_get("/line/play", params={"lineId": lid}, timeout=30)
                info = (d or {}).get("data") or {}
                # v4.5 APP 解析通道 (Electron app.asar 逆向实证):
                #   GET /line/play/parse?lineId= — 服务端解析直链, web 端点不下发
                #   code=200 → data 即直链; 1100=PARSE_ERROR → web 逻辑兜底
                #   4K 线路 (主-NS4K/主-4K) 仅此通道可解
                try:
                    pr = self.api_get("/line/play/parse", params={"lineId": lid}, timeout=60)
                    pc = (pr or {}).get("code")
                    if pc == 200 and (pr or {}).get("data"):
                        pu = str(pr.get("data") or "")
                        if pu.startswith("http") or pu.startswith("/"):
                            direct = pu
                            if self._search_cache is not None:
                                self._search_cache[ck] = (time.time(), direct)
                                if len(self._search_cache) > 128:
                                    for k in list(self._search_cache.keys())[:64]:
                                        if k.startswith("play:"):
                                            self._search_cache.pop(k, None)
                except Exception:
                    pass
                # 3. web 端 playUrl 兜底 (parse 失败/线路无 4K 限制时仍可用)
                if not direct:
                    direct = info.get("playUrl") or ""
                    if direct and self._search_cache is not None:
                        try:
                            self._search_cache[ck] = (time.time(), direct)
                            if len(self._search_cache) > 128:
                                for k in list(self._search_cache.keys())[:64]:
                                    if k.startswith("play:"):
                                        self._search_cache.pop(k, None)
                        except Exception:
                            pass
            if not direct:
                return {}
            # 3. 相对路径 → 站内直连
            if direct.startswith("/") and "http" not in direct[:8]:
                u = self.host + (direct if direct.startswith("/api") else "/api" + direct)
                return {"url": u, "parse": 0, "header": headers}
            # 4. 外链: 站点自己的 URL → 直连
            if direct.startswith(self.host):
                return {"url": direct, "parse": 0, "header": headers}
            # 5. 外链直连探测 → 失败降级代理
            if "http" in direct:
                ul = direct.lower()
                # mp4/mkv 直连 (单文件流, 播放器自带头即可)
                if ul.endswith(".mp4") or ul.endswith(".mkv") or ".mp4?" in ul or ".mkv?" in ul:
                    return {"url": direct, "parse": 0, "header": headers}
                ok = self._probe(direct, headers)
                if ok is True:
                    return {"url": direct, "parse": 0, "header": headers}
                # None(探测超时/不可达) 或 False(HTML) → 服务器代理 (服务器侧无 Referer 限制且带宽兜底)
                proxy = self.api + "/files/proxy?url=" + urllib.parse.quote(direct, safe="")
                return {"url": proxy, "parse": 0, "header": headers}
            return {"url": direct, "parse": 0, "header": headers}
        except Exception:
            return {}

    def _img(self, u):
        if not u:
            return ""
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("http"):
            return u
        if u.startswith("/"):
            return self.host + u
        return u

    def localProxy(self, params):
        return None


# ============================================================
# 模块级导出 (WebHomeTV 调 模块.函数, 不实例化类)
# ============================================================
_SP = None
_SP_T = 0


def _ensure():
    global _SP, _SP_T
    if _SP is None or time.time() - _SP_T > 8 * 3600:
        _SP = Spider()
        _SP_T = time.time()
    return _SP


def init(ext=""):
    return _ensure().init(ext)


def getName():
    return _ensure().getName()


def homeContent(filter1=1):
    return _ensure().homeContent(filter1)


def homeVideoContent():
    return _ensure().homeVideoContent()


def homeFilterContent(params):
    return {}


def categoryContent(tid, pg=1, filter1=1, extend=None):
    return _ensure().categoryContent(tid, pg, filter1, extend)


def detailContent(ids):
    return _ensure().detailContent(ids)


def playerContent(flag, id, vipFlags=None):
    return _ensure().playerContent(flag, id, vipFlags)


def searchContent(key, quick=0, pg=None):
    return _ensure().searchContent(key, quick, pg)


def searchContentPage(key, quick=0, pg=None):
    return _ensure().searchContentPage(key, quick, pg)


def isVideoFormat(url):
    return _ensure().isVideoFormat(url)


def localProxy(params):
    return None


# ============================================================
# 自测
# ============================================================
if __name__ == "__main__":
    sp = Spider()
    sp.init()
    print("[1] " + sp.getName() + " | 签名 " + sp.sign("/film", sp.timestamp())[:16] + "...")
    ts = sp.timestamp()
    assert len(ts) == 13 and int(ts[-1]) == sum(int(c) for c in ts[:-1]) % 10
    print("[2] timestamp 校验位 OK: " + ts)
    # PNG 模板自测
    import struct as _s
    def _mkpng(w, h, x0, x1):
        raw = b''
        for y in range(h):
            raw += b'\x00'
            for x in range(w):
                a = 255 if (x0 <= x <= x1 and 10 <= y <= 50) else 0
                raw += bytes([255, 255, 255, a])
        def chunk(t, d):
            return _s.pack('>I', len(d)) + t + d + _s.pack('>I', zlib.crc32(t + d) & 0xffffffff)
        ihdr = _s.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
        return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
    b64 = base64.b64encode(_mkpng(110, 60, 20, 90)).decode()
    shape = _png_shape(b64)
    assert shape == (20, 90, 10, 50), str(shape)
    print("[3] PNG 形状解码 OK: " + str(shape))
    # XOR (Gw 语义): 服务端加密=明文字节^密钥串字符, 十六进制传输
    key = "0x1A2B3C4D5E6F7A8B9C"
    plain = "rtsTestSession42"
    enc_hex = ''.join('%02x' % (ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(plain))
    assert sp._xor(enc_hex) == plain
    print("[4] XOR (Gw hex) 往返 OK")
    # 零 f-string
    src = open(__file__.replace('.pyc', '.py'), 'r', encoding='utf-8', errors='ignore').read()
    import re as _re
    cnt = len(_re.findall(r'f"[^"\n]*\{', src)) + len(_re.findall(r"f'[^'\n]*\{", src))
    print("[5] f-string 检查: %d (应=0)" % cnt)
    # homeContent 容错
    h = sp.homeContent(1)
    assert "class" in h and "list" in h
    print("[6] homeContent 容错 OK (沙箱网络不通返回空结构)")
    print("=== ALL SELF-TESTS PASS ===")
