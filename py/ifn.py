# -*- coding: utf-8 -*-
import sys
import re
import json
import time
import random
from urllib.parse import quote

sys.path.append('..')
try:
    from base.spider import Spider
except ImportError:
    class Spider:
        def fetch(self, url, headers=None, **kw):
            import requests as rq
            kw.pop('timeout', None)
            return rq.get(url, headers=headers, timeout=15, **kw)

HOST = "https://cn1.ifn.watch"
HOSTS = ["https://ifn.watch", "https://cn1.ifn.watch"]
SUPA = "https://rvcrrwdtggvbomvzvpev.supabase.co"
MAIL = "https://api.mail.tm"
ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ2Y3Jyd2R0Z2d2Ym9tdnp2cGV2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDQ5MzEwNjUsImV4cCI6MjA2MDUwNzA2NX0.eiNtiUxJxXZzYn1uFRtKaPAXim64vP6brgRWxgMcmyE"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
CATEGORIES = {"3": "电影", "4": "剧集", "5": "综艺", "6": "动漫", "8": "短剧", "7": "纪录片", "40": "4K"}
RESLO = re.compile(r'^(576P|720P|1080P|2160P)$', re.I)
SEED_MAIL = "tvbox_97450@emalupe.com"
SEED_PWD = "Tvbox123456!"
SEED_ACCOUNTS = [
    ("tvbox_97450@emalupe.com", "Tvbox123456!"),
    ("tvbox_25195@emalupe.com", "Tvbox123456!"),
    ("tvbox_53132@emalupe.com", "Tvbox123456!"),
    ("tvbox_32699@emalupe.com", "Tvbox123456!"),
]
_DIAG = "/sdcard/Download/ifn_diag.txt"

def _log(msg):
    try:
        import os
        with open(_DIAG, 'a') as f:
            f.write("%s %s\n" % (time.strftime('%H:%M:%S'), msg))
    except:
        pass

class _UR:
    def __init__(self, resp=None, body=None):
        if body is not None:
            self._body = body
            self.status_code = getattr(resp, 'status', 200)
            return
        try:
            self.status_code = resp.status
        except:
            self.status_code = 0
        try:
            self._body = resp.read()
        except:
            self._body = b''
    @property
    def text(self):
        return self._body.decode('utf-8', 'ignore')
    def read(self):
        return self._body

class Spider(Spider):
    def init(self, extend=""):
        _log("== init v7 ==")
        self._at = ""
        self._at_ts = 0.0
        self._rt = ""
        self._mail = ""
        self._pwd = ""
        self._host = ""
        self._supa_ip = ""
        self._pick_t = 0.0
        self._last = ""
        self._acc_idx = 0
        self._accounts = []
        if self._mail and self._pwd:
            for i, (em, pw) in enumerate(SEED_ACCOUNTS):
                if em == self._mail and pw == self._pwd:
                    self._acc_idx = i
                    break
        if extend and "|" in str(extend):
            e = str(extend).split("|")
            self._mail = e[0].strip()
            self._pwd = e[1].strip() if len(e) > 1 else ""
        self._load_cache()
        if self._rt:
            try:
                self._refresh()
            except:
                pass
        _log("init done host=%s at=%s rt=%s" % (self._host or '?', bool(self._at), bool(self._rt)))

    def _pick(self):
        if self._host:
            return self._host
        now = time.time()
        if self._pick_t and now - self._pick_t < 10:
            return self._last or HOST
        self._pick_t = now
        last = HOST
        for h in HOSTS:
            last = h
            try:
                r = self._ureq(h + "/", timeout=3, max_read=1)
                if r is not None and getattr(r, 'status_code', 0) == 200:
                    self._host = h
                    _log("pick OK %s" % h)
                    return h
            except:
                pass
        self._last = last
        _log("pick FAIL all")
        return last

    def _save_cache(self):
        try:
            import os
            data = json.dumps({"rt": self._rt, "mail": self._mail, "pwd": self._pwd, "accounts": getattr(self, '_accounts', [])})
            paths = [os.path.join(os.getcwd(), '.ifn_sess.json'), '/sdcard/Download/.ifn_sess.json', '/storage/emulated/0/Download/.ifn_sess.json']
            for p in paths:
                try:
                    with open(p, 'w') as f:
                        f.write(data)
                    return
                except:
                    pass
        except:
            pass

    def _load_cache(self):
        try:
            import os
            paths = ['/storage/emulated/0/Download/.ifn_sess.json', '/sdcard/Download/.ifn_sess.json', os.path.join(os.getcwd(), '.ifn_sess.json')]
            for p in paths:
                try:
                    with open(p) as f:
                        d = json.loads(f.read())
                    acc = d.get("accounts", []) or []
                    if not acc:
                        continue
                    self._rt = self._rt or d.get("rt", "")
                    if not self._mail:
                        self._mail = d.get("mail", "")
                        self._pwd = d.get("pwd", "")
                    self._accounts = acc
                    for em, pw in acc:
                        if (em, pw) not in SEED_ACCOUNTS:
                            SEED_ACCOUNTS.append((em, pw))
                    return True
                except:
                    pass
            for p in paths:
                try:
                    with open(p) as f:
                        d = json.loads(f.read())
                    self._rt = self._rt or d.get("rt", "")
                    if not self._mail:
                        self._mail = d.get("mail", "")
                        self._pwd = d.get("pwd", "")
                    return True
                except:
                    pass
        except:
            pass
        return False

    def _supa_headers(self):
        return {"apikey": ANON, "Authorization": "Bearer " + ANON, "Content-Type": "application/json", "User-Agent": UA}

    def _sreq(self, path, method='POST', body=None, headers=None):
        try:
            import http.client, ssl, socket
            host = self._pick().split('://')[1]
            sni = SUPA.split('://')[1]
            ip = self._supa_ip
            if not ip:
                try:
                    ip = socket.getaddrinfo(host, 443)[0][4][0]
                    self._supa_ip = ip
                except:
                    return None
            class _C(http.client.HTTPSConnection):
                def __init__(self, ipp, snih, tm):
                    http.client.HTTPSConnection.__init__(self, ipp, 443, timeout=tm)
                    self._snih = snih
                def connect(self):
                    ctx = ssl.create_default_context()
                    sock = socket.create_connection((self.host, self.port), self.timeout)
                    self.sock = ctx.wrap_socket(sock, server_hostname=self._snih)
            c = _C(ip, sni, 30)
            hd = {'Host': sni}
            if headers:
                hd.update(headers)
            if body is not None:
                hd.setdefault('Content-Type', 'application/json')
            c.request(method, path, body=body, headers=hd)
            r = c.getresponse()
            st = getattr(r, 'status', 0)
            if st >= 400:
                _log("sreq %s -> %s" % (path[:40], st))
            return _UR(r)
        except:
            return None

    def _supa_post(self, path, obj):
        hd = self._supa_headers()
        return self._sreq(path, 'POST', json.dumps(obj), hd)

    def _refresh(self):
        if not self._rt:
            return False
        r = self._supa_post("/auth/v1/token?grant_type=refresh_token", {"refresh_token": self._rt})
        if r is None:
            r = self._req(SUPA + "/auth/v1/token?grant_type=refresh_token", headers=self._supa_headers(), method='POST', data=json.dumps({"refresh_token": self._rt}))
        if r is None:
            return False
        try:
            d = json.loads(self._text(r))
            at = d.get("access_token", "")
            if not at:
                return False
            self._at = at
            self._at_ts = time.time()
            self._rt = d.get("refresh_token", self._rt)
            self._save_cache()
            return True
        except:
            return False

    def _login(self, email, pwd):
        r = self._supa_post("/auth/v1/token?grant_type=password", {"email": email, "password": pwd})
        if r is None:
            r = self._req(SUPA + "/auth/v1/token?grant_type=password", headers=self._supa_headers(), method='POST', data=json.dumps({"email": email, "password": pwd}))
        if r is None:
            return False
        try:
            d = json.loads(self._text(r))
            at = d.get("access_token", "")
            if not at:
                return False
            self._at = at
            self._at_ts = time.time()
            self._rt = d.get("refresh_token", "")
            self._mail = email
            self._pwd = pwd
            self._save_cache()
            return True
        except:
            return False

    def _register(self):
        try:
            dom = ""
            r = self._req(MAIL + "/domains", headers={"User-Agent": UA})
            if r is not None:
                try:
                    dom = json.loads(self._text(r))["hydra:member"][0]["domain"]
                except:
                    dom = ""
            if not dom:
                dom = "emalupe.com"
            addr = "tvbox_%d@%s" % (random.randint(10000, 99999), dom)
            pwd = "Tvbox123456!"
            r2 = self._post_json(MAIL + "/accounts", {"address": addr, "password": pwd})
            if r2 is None:
                return False
            r3 = self._post_json(MAIL + "/token", {"address": addr, "password": pwd})
            if r3 is None:
                return False
            mt = ""
            try:
                mt = json.loads(self._text(r3)).get("token", "")
            except:
                return False
            if not mt:
                return False
            hd = self._supa_headers()
            r4 = self._req(SUPA + "/auth/v1/signup", headers=hd, method='POST', data=json.dumps({"email": addr, "password": pwd, "data": {"username": "tvuser"}}))
            if r4 is None or not self._ok(r4):
                return False
            vlink = ""
            for i in range(5):
                time.sleep(4)
                r5 = self._req(MAIL + "/messages", headers={"Authorization": "Bearer " + mt, "User-Agent": UA})
                if r5 is None:
                    continue
                try:
                    items = json.loads(self._text(r5)).get("hydra:member", [])
                except:
                    continue
                if not items:
                    continue
                mid = items[0].get("id", "")
                r6 = self._req(MAIL + "/messages/" + mid, headers={"Authorization": "Bearer " + mt, "User-Agent": UA})
                if r6 is None:
                    continue
                body = self._text(r6)
                try:
                    body = json.loads(body).get("text", "") or body
                except:
                    pass
                body = body.replace('\\/', '/')
                m = re.search(r'https://[^"\'\s<>]+?/auth/v1/verify\?token=[a-f0-9]+&type=signup[^"\'\s<>]*', body)
                if m:
                    vlink = m.group(0).replace('\\', '')
                    break
            if not vlink:
                return False
            self._req(vlink, headers={"User-Agent": UA})
            if (addr, pwd) not in SEED_ACCOUNTS:
                SEED_ACCOUNTS.append((addr, pwd))
            if (addr, pwd) not in self._accounts:
                self._accounts.append((addr, pwd))
            self._mail, self._pwd = addr, pwd
            self._save_cache()
            _log("registered new account %s (pool=%d)" % (addr, len(SEED_ACCOUNTS)))
            for i in range(2):
                if self._login(addr, pwd):
                    return True
                time.sleep(2)
            return True
        except:
            return False

    def _ensure_session(self):
        if self._at and time.time() - self._at_ts < 600:
            return True
        if self._rt and self._refresh():
            _log("session via refresh")
            return True
        if self._mail and self._pwd and self._login(self._mail, self._pwd):
            _log("session via extend account")
            return True
        if SEED_ACCOUNTS:
            em, pw = SEED_ACCOUNTS[self._acc_idx % len(SEED_ACCOUNTS)]
            if self._login(em, pw):
                _log("session via SEED login (%s)" % em)
                return True
        _log("session FAIL all paths")
        return self._register()

    def _next_account(self):
        if SEED_ACCOUNTS:
            if self._mail and self._pwd:
                for i, (em, pw) in enumerate(SEED_ACCOUNTS):
                    if em == self._mail and pw == self._pwd:
                        self._acc_idx = i
                        break
            self._acc_idx = (self._acc_idx + 1) % len(SEED_ACCOUNTS)
            self._mail, self._pwd = SEED_ACCOUNTS[self._acc_idx]
        self._at = ""
        self._rt = ""
        _log("switch account -> %s" % self._mail)

    def _req(self, url, headers=None, method='GET', data=None):
        try:
            r = None
            try:
                r = self.fetch(url, headers=headers, method=method, data=data, timeout=30000)
            except TypeError:
                try:
                    r = self.fetch(url, headers=headers, method=method, data=data)
                except TypeError:
                    try:
                        r = self.fetch(url, headers=headers)
                    except:
                        r = None
            except Exception:
                try:
                    r = self.fetch(url, headers=headers, method=method, data=data)
                except Exception:
                    try:
                        r = self.fetch(url, headers=headers)
                    except:
                        r = None
            if r is not None:
                st = getattr(r, 'status_code', 0)
                if st and st < 400:
                    return r
            return self._ureq(url, headers, method, data)
        except:
            try:
                return self._ureq(url, headers, method, data)
            except:
                return None

    def _ureq(self, url, headers=None, method='GET', data=None, timeout=30, max_read=0):
        import urllib.request
        hd = dict(headers or {})
        hd.setdefault('User-Agent', UA)
        body = data.encode('utf-8') if isinstance(data, str) else data
        req = urllib.request.Request(url, data=body, headers=hd, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            if max_read > 0:
                return _UR(resp, resp.read(max_read))
            return _UR(resp)
        except Exception as e:
            import urllib.error
            if isinstance(e, urllib.error.HTTPError):
                return _UR(e)
            return None

    def _text(self, r):
        if r is None:
            return ""
        if hasattr(r, 'text'):
            return r.text
        if isinstance(r, (bytes, bytearray)):
            return r.decode('utf-8', 'ignore')
        return str(r)

    def _ok(self, r):
        if r is None:
            return False
        if hasattr(r, 'status_code'):
            return r.status_code == 200
        return True

    def _get(self, url, referer="", headers=None):
        h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        if headers:
            h.update(headers)
        if referer:
            h["Referer"] = referer
        for attempt in range(3):
            r = self._req(url, headers=h)
            if r is None:
                time.sleep(1)
                continue
            if hasattr(r, 'status_code') and r.status_code >= 400:
                time.sleep(1)
                continue
            return self._text(r)
        return ""

    def _post_json(self, url, obj, headers=None):
        h = {"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            h.update(headers)
        for attempt in range(3):
            r = self._req(url, headers=h, method='POST', data=json.dumps(obj))
            if r is None:
                time.sleep(1)
                continue
            return r
        return None

    def _api(self, url, headers=None):
        h = {"User-Agent": UA, "Accept": "application/json", "Referer": self._pick() + "/"}
        if headers:
            h.update(headers)
        if self._at:
            h["Authorization"] = "Bearer " + self._at
        r = self._req(url, headers=h)
        if r is None:
            return None
        try:
            return json.loads(self._text(r))
        except:
            return None

    def _play_token(self, mediaId, episodeId, title):
        hosts = list(HOSTS)
        if self._host and self._host not in hosts:
            hosts.insert(0, self._host)
        for attempt in range(max(1, len(SEED_ACCOUNTS) or 1)):
            self._ensure_session()
            for h in hosts:
                r = self._post_json(h + "/api/play/token", {"mediaId": mediaId, "episodeId": episodeId, "title": title}, headers={"Authorization": "Bearer " + self._at, "Referer": h + "/"})
                if r is None:
                    continue
                body = self._text(r)
                try:
                    d = json.loads(body)
                    if d.get("success"):
                        self._host = h
                        return d.get("token", "")
                    if "INSUFFICIENT" in body.upper():
                        _log("points insufficient, switch")
                        self._next_account()
                        break
                except:
                    pass
        if self._register():
            _log("pool exhausted, auto-registered %s" % self._mail)
            self._ensure_session()
            for h in hosts:
                r = self._post_json(h + "/api/play/token", {"mediaId": mediaId, "episodeId": episodeId, "title": title}, headers={"Authorization": "Bearer " + self._at, "Referer": h + "/"})
                if r is None:
                    continue
                try:
                    d = json.loads(self._text(r))
                    if d.get("success"):
                        self._host = h
                        return d.get("token", "")
                except:
                    pass
        _log("play_token FAIL all")
        return ""

    def _items(self, html):
        items, seen, names = [], set(), set()
        for m in re.finditer(r'<a href="(/detail/([^"]+))" title="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
            vid = m.group(2)
            name = m.group(3).strip()[:80]
            if vid in seen or name in names:
                continue
            seen.add(vid)
            names.add(name)
            b = m.group(4)
            im = re.search(r'<img[^>]*src="(/api/image/[^"]+)"', b)
            if not im:
                im = re.search(r'<img[^>]*src="(https?://[^"]+)"', b)
            pic = im.group(1) if im else ""
            if pic.startswith('/') and not pic.startswith('//'):
                pic = self._pick() + pic
            elif pic.startswith('//'):
                pic = 'https:' + pic
            items.append({
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": "",
            })
        return items

    def _detail_data(self, html):
        BS = chr(92)

        def _close(txt, start, br, bl):
            dep = 0
            kk = start
            esc = False
            ins = False
            while kk < len(txt):
                c = txt[kk]
                if esc:
                    esc = False
                elif c == BS:
                    esc = True
                elif ins:
                    if c == '"':
                        ins = False
                else:
                    if c == '"':
                        ins = True
                    elif c == bl:
                        dep += 1
                    elif c == br:
                        dep -= 1
                        if dep == 0:
                            return kk
                kk += 1
            return -1

        def _loads(seg):
            seg = seg.replace(BS * 3 + '"', chr(1))
            for _ in range(6):
                n = seg.replace(BS * 2 + '"', BS + '"')
                if n == seg:
                    break
                seg = n
            seg = seg.replace(BS + '"', '"').replace(chr(1), BS + '"')
            try:
                return json.loads(seg)
            except:
                return None

        try:
            chunks = re.findall(r'self\.__next_f\.push\(\[1,\s*"((?:\\.|[^"\\])*)"', html)
            if chunks:
                payload = ''.join(json.loads('"%s"' % c) for c in chunks)
                i = payload.find('detailData')
                if i >= 0:
                    j = payload.find('{', i)
                    if j >= 0:
                        k = _close(payload, j, '}', '{')
                        if k >= 0:
                            d = _loads(payload[j:k + 1])
                            if d:
                                return d
        except:
            pass
        i = -1
        for lv in range(1, 5):
            i = html.find(BS * lv + '"detailData' + BS * lv + '"')
            if i >= 0:
                break
        if i < 0:
            i = html.find('detailData')
        if i < 0:
            return None
        j = html.find('{', i)
        if j < 0:
            return None
        segs = []
        ei = html.find(BS + '"episodes' + BS + '"')
        if ei < 0:
            ei = html.find('"episodes"')
        if ei >= 0:
            ej = html.find('[', ei)
            if ej >= 0:
                ek = _close(html, ej, ']', '[')
                if ek >= 0:
                    kk = ek + 1
                    esc = False
                    ins = False
                    while kk < len(html):
                        c = html[kk]
                        if esc:
                            esc = False
                        elif c == BS:
                            esc = True
                        elif ins:
                            if c == '"':
                                ins = False
                        else:
                            if c == '"':
                                ins = True
                            elif c == '}':
                                break
                        kk += 1
                    if kk < len(html):
                        segs.append(html[j:kk + 1])
        ek2 = _close(html, j, '}', '{')
        if ek2 >= 0:
            segs.append(html[j:ek2 + 1])
        for seg in segs:
            d = _loads(seg)
            if d:
                return d
        return None

    def homeContent(self, filter=False):
        return {"class": [{"type_id": k, "type_name": v} for k, v in CATEGORIES.items()], "list": []}

    def homeVideoContent(self):
        return {"list": self._items(self._get(self._pick() + "/"))[:50]}

    def categoryContent(self, tid, pg=1, filter=False, extend=""):
        try:
            pn = max(int(str(pg)), 1)
        except:
            pn = 1
        cat = str(tid)
        url = f"{self._pick()}/category/{cat}?page={pn}" if pn > 1 else f"{self._pick()}/category/{cat}"
        html = self._get(url)
        items = self._items(html)
        pages = re.findall(rf'/category/{cat}\?page=(\d+)', html)
        maxp = 1
        for p in pages:
            try:
                if int(p) > maxp:
                    maxp = int(p)
            except:
                pass
        return {"page": pn, "pagecount": maxp, "limit": 30, "total": len(items), "list": items}

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, list) else str(ids)
        for attempt in range(3):
            html = self._get(f"{self._pick()}/detail/{vid}")
            d = self._detail_data(html) if html else None
            if d:
                break
            time.sleep(1)
        if not d:
            return {"list": []}
        eps = d.get("episodes", []) or []
        if not eps:
            return {"list": []}
        mediaId = d.get("mediaId", "")
        d["vod_id"] = vid
        d["vod_name"] = d.get("title", "") or ""
        pic = d.get("coverImgUrl", "") or ""
        if pic.startswith("//"):
            pic = "https:" + pic
        d["vod_pic"] = pic
        d["vod_year"] = (d.get("date") or "")[:4]
        d["vod_area"] = d.get("regional", "") or ""
        d["vod_class"] = d.get("contentType", "") or ""
        d["vod_director"] = d.get("director", "") or ""
        d["vod_actor"] = d.get("actor", "") or ""
        d["vod_content"] = (d.get("description", "") or "")[:500]
        d["vod_remarks"] = d.get("updateStatus", "") or ""
        d["vod_play_from"] = "IFN直链$$$IFN代理"
        d["vod_play_url"] = "#".join([f"{e.get('title','')}${mediaId}|{e.get('id','')}|{e.get('title','')}|direct" for e in eps]) + "$$$" + "#".join([f"{e.get('title','')}${mediaId}|{e.get('id','')}|{e.get('title','')}|proxy" for e in eps])
        return {"list": [d]}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            html = self._get(f"{self._pick()}/search?q={quote(str(key))}")
            return {"list": self._items(html), "page": 1}
        except:
            return {"list": []}

    def playerContent(self, flag, id, vipFlags=None):
        try:
            parts = str(id).split("|")
            if len(parts) != 4:
                _log("player id bad: %s" % str(id)[:60])
                return {"url": ""}
            mediaId, episodeId, title, mode = parts[0], parts[1], parts[2], parts[3]
            reslo = title if RESLO.match(title) else "1080P"
            tok = self._play_token(mediaId, episodeId, title)
            if not tok:
                _log("player NO token")
                return {"url": ""}
            mode = "proxy" if "代理" in str(flag) or mode == "proxy" else "direct"
            param = "thirdParty=true" if mode == "proxy" else "ts=true"
            url = f"{self._pick()}/api/play/{tok}.m3u8?{param}&reslo={reslo}"
            _log("player url=%s" % url[:100])
            return {"parse": 0, "url": url, "header": {"User-Agent": UA}}
        except Exception as e:
            _log("player EXC %s" % str(e)[:80])
            return {"url": ""}

    def localProxy(self, param):
        pass

    def _pagecount(self, html, current_page=1):
        return current_page

    def getName(self):
        return "IFN影视"

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass
