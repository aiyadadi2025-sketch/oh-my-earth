# -*- coding: utf-8 -*-
import sys
import re
import json
import hashlib
import base64
import time
from urllib.parse import urljoin, quote, unquote, urlencode

sys.path.append('..')
try:
    from base.spider import Spider
except ImportError:
    class Spider:
        def fetch(self, url, headers=None, method='GET', data=None, **kw):
            import requests as rq
            kw.pop('timeout', None)
            if method.upper() == 'POST':
                r = rq.post(url, headers=headers, data=data, timeout=15, **kw)
            else:
                r = rq.get(url, headers=headers, timeout=15, **kw)
            r.encoding = 'utf-8'
            return r

HOST = "https://wbbb1.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
PARSE_DOMAIN = "xn--qvr2v.850088.xyz"
DOMAINS = ["https://wbbb1.com", "https://v.wbbb1.com", "https://www.wbbb1.com"]
CATEGORIES = {"1": "电影", "2": "剧集", "3": "动漫", "4": "综艺"}

def rc4(data, key):
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    i = j = 0
    out = bytearray()
    for ch in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out.append(ch ^ S[(S[i] + S[j]) % 256])
    return bytes(out)

class Spider(Spider):
    def init(self, extend=""):
        global HOST, PARSE_DOMAIN, DOMAINS
        try:
            r = self.fetch("https://xn--clr79vmzkema.com/domains.json", headers={"User-Agent": UA}, timeout=10000)
            h = r.text if hasattr(r, 'text') else str(r)
            data = json.loads(h)
            doms = []
            for k in ("node1", "node2", "node3"):
                for u in data.get(k, []) or []:
                    if str(u).startswith("http") and u not in doms:
                        doms.append(u)
            if doms:
                DOMAINS = doms
                HOST = doms[0]
        except:
            pass
        try:
            r = self.fetch(HOST + "/static/player/parse.js", headers={"User-Agent": UA}, timeout=10000)
            h = r.text if hasattr(r, 'text') else str(r)
            m = re.search(r'https://([^/]+)/player/\?url=', h)
            if m:
                PARSE_DOMAIN = m.group(1)
        except:
            pass

    def _req(self, url, headers=None, method='GET', data=None):
        try:
            try:
                r = self.fetch(url, headers=headers, method=method, data=data, timeout=30000)
            except TypeError:
                try:
                    r = self.fetch(url, headers=headers, method=method, data=data)
                except TypeError:
                    r = self.fetch(url, headers=headers)
            except Exception:
                try:
                    r = self.fetch(url, headers=headers, method=method, data=data)
                except Exception:
                    try:
                        r = self.fetch(url, headers=headers)
                    except Exception:
                        return None
            return r
        except:
            return None

    def _text(self, r):
        if r is None:
            return ""
        return r.text if hasattr(r, 'text') else str(r)

    def _ok(self, r):
        if r is None:
            return False
        if hasattr(r, 'status_code'):
            return r.status_code == 200
        return True

    def _is_challenge(self, txt):
        if len(txt) < 2000 and ('location.href' in txt or 'cf_chl' in txt or 'Just a moment' in txt or 'cf-chl' in txt):
            return True
        return False

    def _fetch_html(self, url, referer=""):
        global HOST, DOMAINS
        headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        if referer:
            headers["Referer"] = referer
        for attempt in range(3):
            r = self._req(url, headers=headers)
            if r is None:
                time.sleep(2 + attempt * 2)
                continue
            txt = self._text(r)
            challenged = (hasattr(r, 'status_code') and r.status_code in (403, 429, 503)) or (not hasattr(r, 'status_code') and self._is_challenge(txt))
            if challenged:
                sc = r.headers.get('Set-Cookie') or r.headers.get('set-cookie') if hasattr(r, 'headers') and r.headers else None
                if sc:
                    ck = str(sc).split(";")[0] if isinstance(sc, str) else str(sc[0]).split(";")[0]
                    if ck:
                        headers["Cookie"] = ck
                if url.startswith(HOST) and len(DOMAINS) > 1:
                    idx = DOMAINS.index(HOST) if HOST in DOMAINS else 0
                    for i in range(1, len(DOMAINS) + 1):
                        new = DOMAINS[(idx + i) % len(DOMAINS)]
                        new_url = url.replace(HOST, new, 1)
                        if new_url == url:
                            continue
                        time.sleep(2)
                        r2 = self._req(new_url, headers=headers)
                        t2 = self._text(r2)
                        if self._ok(r2) and not self._is_challenge(t2):
                            HOST = new
                            return t2
                time.sleep(3 + attempt * 3)
                continue
            if hasattr(r, 'status_code') and r.status_code >= 400:
                time.sleep(1)
                continue
            return txt
        return ""

    def _md5(self, s):
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    def _rc4e(self, data, key):
        return base64.b64encode(rc4(data.encode('utf-8'), key)).decode('utf-8')

    def _rc4d(self, data, key):
        return rc4(base64.b64decode(data), key).decode('utf-8')

    def _decrypt(self, enc, link_next=''):
        global PARSE_DOMAIN, HOST
        try:
            dom = HOST.replace('https://', '').replace('http://', '')
            uvu = enc + '&next=//' + dom + (link_next or '')
            key = (self._md5(uvu) + " P")[-22:].encode('utf-8')
            h = self._rc4e(self._md5(uvu + "stray"), key)
            ts = str(int(time.time()))
            u = self._rc4e(ts + self._md5(key.decode('utf-8') + "stray"), key)
            y = self._rc4e(self._md5(PARSE_DOMAIN + "stray"), key)
            headers = {
                "User-Agent": UA,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Origin": "https://" + PARSE_DOMAIN,
                "Referer": "https://" + PARSE_DOMAIN + "/player/?url=" + enc,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            post = urlencode({"url": uvu, "key": h, "vkey": u, "ckey": y})
            r = None
            for attempt in range(3):
                r = self._req("https://" + PARSE_DOMAIN + "/player/api.php", headers=headers, method='POST', data=post)
                if self._ok(r):
                    break
                time.sleep(1)
            if r is None:
                return None
            result = json.loads(self._text(r))
            if result.get("code") != 200:
                return None
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad
            aes_key = self._rc4d(result["aes_key"], key).encode('utf-8')
            aes_iv = self._rc4d(result["aes_iv"], key).encode('utf-8')
            cipher = AES.new(aes_key, AES.MODE_CBC, aes_iv)
            return unpad(cipher.decrypt(base64.b64decode(result["url"])), AES.block_size).decode('utf-8')
        except:
            return None

    def _is_img_m3u8(self, url):
        try:
            if '.m3u8' not in url.lower():
                return False
            if '123pan' in url or 'cbern' in url or '71edge' in url:
                return True
            r = self._req(url, headers={"User-Agent": UA})
            if not self._ok(r):
                return False
            seg = ""
            for line in self._text(r).split('\n'):
                line = line.strip()
                if line.startswith('http'):
                    seg = line
                    break
            if not seg:
                return False
            r2 = self._req(seg, headers={"User-Agent": UA})
            if r2 is not None and r2.status_code == 200:
                ct = (r2.headers.get('Content-Type') or '').lower() if hasattr(r2, 'headers') and r2.headers else ''
                if 'image/' in ct or 'PNG' in self._text(r2)[:16].upper():
                    return True
            return False
        except:
            return False

    def homeContent(self, filter=False):
        r = {"class": [], "list": []}
        for k, v in CATEGORIES.items():
            r["class"].append({"type_id": k, "type_name": v})
        return r

    def homeVideoContent(self):
        try:
            html = self._fetch_html(HOST)
            m = re.search(r'<h2[^>]*class="[^"]*module-title[^"]*"[^>]*>正在热映.*?</div>(.*?)</div>\s*<div class="module">', html, re.DOTALL)
            if not m:
                m = re.search(r'<div class="module">(.*?)</div>\s*<div class="module">', html, re.DOTALL)
            if not m:
                return {"list": []}
            return {"list": self._items(m.group(1))[:20]}
        except:
            return {"list": []}

    def categoryContent(self, tid, pg=1, filter=False, extend=""):
        pn = 1
        try:
            pn = max(int(str(pg)), 1)
        except:
            pass
        cat = str(tid)
        if cat not in CATEGORIES:
            cat = "1"
        try:
            if pn == 1:
                url = f"{HOST}/show/{cat}-----------.html"
            else:
                url = f"{HOST}/show/{cat}--------{pn}---.html"
            html = self._fetch_html(url)
            items = self._items(html)
            return {
                "page": pn,
                "pagecount": self._pagecount(html, pn),
                "limit": 42,
                "total": len(items),
                "list": items
            }
        except:
            return {"page": pn, "pagecount": 1, "limit": 42, "total": 0, "list": []}

    def detailContent(self, ids):
        if isinstance(ids, list):
            vid = ids[0] if ids else ""
        else:
            vid = str(ids) if ids else ""
        m = re.search(r'(\d+)', str(vid))
        vid = m.group(1) if m else ""
        if not vid:
            return {"list": []}
        try:
            html = self._fetch_html(f"{HOST}/detail/{vid}.html")
        except:
            return {"list": []}
        if not html:
            return {"list": []}

        d = {
            "vod_id": vid, "vod_name": "", "vod_pic": "", "vod_year": "",
            "vod_area": "", "vod_class": "", "vod_director": "", "vod_actor": "",
            "vod_content": "", "vod_remarks": "", "vod_play_from": "", "vod_play_url": ""
        }

        tn = re.search(r'<h1[^>]*>(.*?)</h1>', html)
        if tn:
            d["vod_name"] = re.sub(r'<[^>]+>', '', tn.group(1)).strip()
        else:
            tn = re.search(r'<title>(.*?)</title>', html)
            if tn:
                d["vod_name"] = tn.group(1).split("-")[0].strip()

        p = re.search(r'<img[^>]*data-original="(https?://[^"]+\.(?:jpg|jpeg|png|webp))"', html, re.I)
        if p:
            d["vod_pic"] = p.group(1)

        desc_m = re.search(r'<div[^>]*class="[^"]*module-info-introduction-content[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        if desc_m:
            d["vod_content"] = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', desc_m.group(1))).strip()[:500]

        actor = re.search(r'主演：</span>.*?<div[^>]*class="[^"]*module-info-item-content[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        if actor:
            d["vod_actor"] = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', actor.group(1))).strip()

        director = re.search(r'导演：</span>.*?<div[^>]*class="[^"]*module-info-item-content[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        if director:
            d["vod_director"] = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', director.group(1))).strip()

        ym = re.search(r'<a[^>]*title="(\d{4})"', html)
        if ym:
            d["vod_year"] = ym.group(1)

        rm = re.search(r'<div[^>]*class="[^"]*module-item-note[^"]*"[^>]*>([^<]*)</div>', html)
        if rm:
            d["vod_remarks"] = rm.group(1).strip()

        try:
            pf, pu = [], []
            names = []
            for m2 in re.finditer(r'data-dropdown-value="([^"]+)"', html):
                name = m2.group(1).strip()
                if name and name not in names:
                    names.append(name)
            blocks = []
            for m2 in re.finditer(r'<div class="module-play-list"[^>]*>(.*?)</div>\s*</div>', html, re.DOTALL):
                if '/vplay/' in m2.group(1):
                    blocks.append(m2.group(1))
            if not blocks:
                pos = 0
                while True:
                    start = html.find('<div class="module-list sort-list tab-list his-tab-list" id="panel1">', pos)
                    if start == -1:
                        break
                    start += len('<div class="module-list sort-list tab-list his-tab-list" id="panel1">')
                    depth = 0
                    end = None
                    i = start
                    while i < len(html):
                        if html[i:i+5] == '<div ':
                            depth += 1
                            i += 5
                        elif html[i:i+6] == '</div>':
                            if depth == 0:
                                end = i + 6
                                break
                            else:
                                depth -= 1
                                i += 6
                        else:
                            i += 1
                    if end is not None:
                        if '/vplay/' in html[start:end]:
                            blocks.append(html[start:end])
                        pos = end
                    else:
                        pos = start + 1
                if len(names) < len(blocks):
                    names = names + [f"源{i+1}" for i in range(len(names), len(blocks))]
            for i, block in enumerate(blocks[:len(names)]):
                eps = re.findall(r'href="(/vplay/(\d+)-(\d+)-(\d+)\.html)"[^>]*>.*?<span>([^<]*)</span>', block)
                if eps:
                    pf.append(names[i])
                    pu.append("#".join([f"{ep[4]}${ep[1]}-{ep[2]}-{ep[3]}" for ep in eps]))
            if pf:
                d["vod_play_from"] = "$$$".join(pf)
                d["vod_play_url"] = "$$$".join(pu)
        except:
            pass

        return {"list": [d]}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            pn = 1
            try:
                pn = int(str(pg))
            except:
                pass
            url = f"{HOST}/search/{quote(key)}-------------.html"
            html = self._fetch_html(url)
            return {"list": self._items(html), "page": pn}
        except:
            return {"list": []}

    def playerContent(self, flag, id, vipFlags=None):
        try:
            vid = str(id)
            m = re.search(r'(\d+)-(\d+)-(\d+)', vid)
            if not m:
                return {'url': ''}
            vod_id, sid, nid = m.group(1), m.group(2), m.group(3)
            # vplay 页面是 JS SPA，直接返回 403 + 重定向，无法提取数据
            # 实际播放数据在 /index.php/vod/player/id/{id}/sid/{sid}/nid/{nid}.html
            time.sleep(1)
            html = self._fetch_html(f'{HOST}/index.php/vod/player/id/{vod_id}/sid/{sid}/nid/{nid}.html', referer=f'{HOST}/detail/{vod_id}.html')
            if not html:
                time.sleep(2)
                html = self._fetch_html(f'{HOST}/index.php/vod/player/id/{vod_id}/sid/{sid}/nid/{nid}.html', referer=f'{HOST}/detail/{vod_id}.html')
            if not html:
                return {'url': ''}
            enc = ''
            link_next = ''
            idx = html.find('player_aaaa')
            if idx >= 0:
                start = html.find('{', idx)
                if start >= 0:
                    depth = 0
                    for i in range(start, len(html)):
                        if html[i] == '{':
                            depth += 1
                        elif html[i] == '}':
                            depth -= 1
                            if depth == 0:
                                try:
                                    pj = json.loads(html[start:i+1])
                                    enc = pj.get('url', '')
                                    link_next = pj.get('link_next', '') or ''
                                except:
                                    pass
                                break
            if not enc or len(enc) < 50:
                return {'url': ''}
            play_url = self._decrypt(enc, link_next)
            if not play_url:
                return {'url': ''}
            play_url = unquote(play_url)
            if self._is_img_m3u8(play_url):
                return {'url': 'https://' + PARSE_DOMAIN + '/player/?url=' + enc, 'parse': 1}
            # 字节流视频 URL 使用解析器
            if 'bytetos' in play_url or 'byteimg' in play_url:
                return {'url': 'https://' + PARSE_DOMAIN + '/player/?url=' + enc, 'parse': 1}
            return {'url': play_url, 'parse': 1}
        except:
            return {'url': ''}
    def localProxy(self, param):
        import base64
        url = base64.b64decode(param.get("url", "")).decode("utf-8")
        if not url:
            return None
        try:
            headers = {
                "User-Agent": UA,
                "Referer": HOST,
            }
            r = self.fetch(url, headers=headers)
            if r is None:
                return None
            data = r.content
            return [200, "video/mp4", data, {"Content-Type": "video/mp4"}]
        except:
            return None

    def _pagecount(self, html, current_page=1):
        pages = re.findall(r'href="/show/\d+--------(\d+)---\.html"', html)
        max_page = current_page
        for p in pages:
            try:
                n = int(p)
                if n > max_page:
                    max_page = n
            except:
                pass
        has_next = re.search(r'title="下一页"|class="[^"]*next[^"]*"', html)
        if has_next and max_page <= current_page + 5:
            max_page = current_page + 5
        return max_page

    def _items(self, html):
        items, seen = [], set()
        for m in re.finditer(r'href="/detail/(\d+)\.html"[^>]*title="([^"]*)"', html):
            vid = m.group(1)
            if vid in seen:
                continue
            name = m.group(2).strip()
            if not name or len(name) > 100:
                continue
            after = html[m.end():m.end() + 800]
            cover = re.search(r'(?:data-original|original|src)="(https?://[^"]+\.(?:jpg|jpeg|png|webp))"', after, re.I)
            remark = re.search(r'class="[^"]*module-item-note[^"]*"[^>]*>([^<]+)<', after)
            if not remark:
                remark = re.search(r'<div[^>]*class="[^"]*(?:note|text|remark)[^"]*"[^>]*>([^<]+)<', after, re.I)
            seen.add(vid)
            items.append({
                "vod_id": vid,
                "vod_name": name[:50],
                "vod_pic": cover.group(1) if cover else "",
                "vod_remarks": remark.group(1).strip() if remark else "",
            })
        return items

    def getName(self):
        return "歪比影视"

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass
