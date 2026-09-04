# -*- coding: utf-8 -*-
# 影探4K 聚合版 (2026-09-03 v4 线路优化版)
# ============ v4: 线路优化(2026-09-03) ============
# 【优化1·聚合线路前置】详情页线路排序改为: 聚合直链(量子/暴风/速播/非凡) →
#   4K引擎线路 → 影探本体线路(死链组自动剔除), 聚合直链秒播优先展示
# 【优化2·新增2个聚合源】新增暴风(bfzyapi)/速播(subocaiji)直链采集站,
#   聚合源从2个扩充到4个, 提升首条线路命中率
# 【优化3·线路分级排序】所有线路按质量分级排序:
#   S级(4K/蓝光/超清) > A级(聚合直链m3u8) > B级(引擎线路) > C级(影探本体)
# 【优化4·播放自动降级】playerContent 增加多线路自动降级:
#   引擎线路失败 → 同集其他引擎线路 → 聚合兜底直链 → 明确失败
# 【优化5·线路可用性探测】详情页对聚合线路做域名级存活探测,
#   死链线路排到末尾而非直接剔除(保留备选), 全站死链才跳过
# ============ v3: 去掉解析(2026-09-03) ============
# 【根因】内置 parse_api(mk1080p.top) 已失效 + Web嗅探站不稳定 → 移除全部解析
# 【修复】parse_api/Web嗅探/isVideoFormat 全部移除; playerContent 统一 parse=0
# 【保留】ldmax_decrypt 本地解密 / 死链探测+m3u8兜底 / 引擎路由 / localProxy返回{}
# ============ v1-2 修复保留(2026-08-30) ============
# 【根因1】ym4kjx.lyyytv.cn 全站404 → 死链探测+自动剔除
# 【根因3】okhttp UA → 已修复
# 【根因4】video_id 污染 → 路由失败明确返回
# 【加固】引擎调用参数兼容; 引擎跨站并发
# ==========================================================
import re, sys, json, base64, os
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
except Exception:
    AES = None
    def unpad(b, n): return b[:-(b[-1] if 0 < b[-1] <= 16 else 0)] if b else b
from urllib.parse import urljoin, quote, unquote
from base.spider import Spider

sys.path.append('..')

# 通用浏览器 UA
BROWSER_UA = ('Mozilla/5.0 (Linux; Android 13; Pixel 7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/124.0.0.0 Mobile Safari/537.36')


class _FallbackCMS(object):
    """内置 m3u8 采集兜底引擎(量子/非凡资源站), 不依赖外部爬虫文件。
    站点失效会自动被异常保护跳过。"""

    def __init__(self, disp, api, fetcher, headers):
        self.disp = disp
        self.api = api
        self._fetch = fetcher
        self.headers = dict(headers or {})
        self._recent = {}

    def init(self, extend=''):
        pass

    def searchContent(self, key, quick, pg='1'):
        try:
            url = '%s?ac=videolist&wd=%s&pg=%s' % (self.api, quote(key or ''), pg)
            data = self._fetch(url, headers=self.headers, timeout=15).json()
            out = []
            for it in (data.get('list') or []):
                vid = it.get('vod_id')
                if vid is None:
                    continue
                self._recent[vid] = it
                if len(self._recent) > 60:
                    for k in list(self._recent)[:30]:
                        self._recent.pop(k, None)
                out.append({'vod_id': vid, 'vod_name': it.get('vod_name') or ''})
            return {'list': out}
        except Exception:
            return {'list': []}

    def detailContent(self, ids):
        try:
            vid = ids[0]
            vod = self._recent.get(vid)
            if vod is None:
                url = '%s?ac=videolist&ids=%s' % (self.api, vid)
                data = self._fetch(url, headers=self.headers, timeout=15).json()
                vod = (data.get('list') or [{}])[0]
            return {'list': [self._filter_direct(vod)]}
        except Exception:
            return {'list': []}

    def playerContent(self, flag, id, vipFlags=None):
        return {'parse': 0, 'jx': 0, 'playUrl': '', 'url': id,
                'header': {'User-Agent': BROWSER_UA}}

    def _filter_direct(self, vod):
        try:
            vod = dict(vod)
        except Exception:
            pass
        fr = (vod.get('vod_play_from') or '').split('$$$')
        groups = (vod.get('vod_play_url') or '').split('$$$')
        names, urls = [], []
        for i, g in enumerate(groups):
            eps = [p.strip() for p in g.split('#') if p.strip()]
            keep = [p for p in eps
                    if self._is_direct(p.split('$', 1)[1] if '$' in p else p)]
            if keep:
                nm = fr[i].strip() if i < len(fr) and fr[i].strip() else self.disp
                names.append(nm)
                urls.append('#'.join(keep))
        vod['vod_play_from'] = '$$$'.join(names) if names else self.disp
        vod['vod_play_url'] = '$$$'.join(urls)
        return vod

    @staticmethod
    def _is_direct(u):
        return bool(re.match(r'^https?://\S+\.(?:m3u8|mp4)(?:[?#]\S*)?$', u or '', re.I))


class Spider(Spider):
    headers = {'User-Agent': 'okhttp/4.12.0'}

    FIXED_CONFIG = {
        'host': 'https://cms.lyyytv.cn',
        'cmskey': 'wP5bvxoc3yv7FoBQENFZuAF0EUYr4LTy',
        'RawPlayUrl': 0,
    }

    # 内置 m3u8 聚合直链源(排序即展示顺序, 直链秒播优先)
    FALLBACK_APIS = {
        'fb1': 'https://cj.lziapi.com/api.php/provide/vod/',
        'fb2': 'https://cj.ffzyapi.com/api.php/provide/vod/',
        'fb3': 'https://bfzyapi.com/api.php/provide/vod/',
        'fb4': 'https://www.subocaiji.com/api.php/provide/vod/',
    }

    # ============ 引擎挂载表 ============
    # 聚合直链源(path=None)排最前, 外部4K引擎其次, 影探本体最后
    # (tag, 显示名, 文件路径, 优先级) —— 优先级越小越靠前
    ENGINES = [
        ('fb1', '聚合·量子', None, 0),
        ('fb3', '聚合·暴风', None, 0),
        ('fb4', '聚合·速播', None, 0),
        ('fb2', '聚合·非凡', None, 0),
        ('dd', '多多4K', '/sdcard/TVBox/py/多多影视4K.py', 1),
        ('jxf', '剧下饭4K', '/sdcard/TVBox/py/剧下饭4K.py', 1),
        ('zn', '真不错4K', '/sdcard/TVBox/py/真不错4K.py', 1),
        ('xy', '星影', '/sdcard/TVBox/py/星影.py', 1),
    ]

    def init(self, extend=''):
        self.host = self.FIXED_CONFIG['host']
        self.cmskey = self.FIXED_CONFIG.get('cmskey', '')
        self.raw_play_url = self.FIXED_CONFIG.get('RawPlayUrl', 0)
        self._engine_cache = None
        self._detail_cache = {}
        self._play_map = {}
        self._domain_status = {}
        self._ep_fallback = {}  # 每集多线路降级表: {raw_id: [(tag, eflag, eid), ...]}

    def _ensure_state(self):
        if not hasattr(self, '_engine_cache'):
            self.init('')

    # ==================== 引擎加载 ====================
    def _load_engines(self):
        if getattr(self, '_engine_cache', None) is not None:
            return self._engine_cache
        out = []
        for tag, disp, path, _pri in self.ENGINES:
            try:
                if path is None:
                    sp = _FallbackCMS(disp, self.FALLBACK_APIS[tag],
                                      self.fetch, self.headers)
                else:
                    sp = None
                    for cand in self._engine_path_candidates(path):
                        if not os.path.isfile(cand):
                            continue
                        try:
                            import importlib.util
                            spec = importlib.util.spec_from_file_location('eng_' + tag, cand)
                            mod = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(mod)
                            sp = mod.Spider()
                            break
                        except Exception:
                            continue
                    if sp is None:
                        continue
                if hasattr(sp, 'init'):
                    sp.init('')
                out.append((tag, disp, sp))
            except Exception:
                continue
        self._engine_cache = out
        return out

    @staticmethod
    def _engine_path_candidates(path):
        cands = [path]
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            same = os.path.join(base_dir, os.path.basename(path))
            if same not in cands:
                cands.append(same)
        except Exception:
            pass
        return cands

    @staticmethod
    def _norm(s):
        s = s or ''
        s = re.sub(r'[（(][^（）()]*[）)]', '', s)
        s = re.sub(r'[\s:：·（）()\-_,，。.\[\]【】!！?？]', '', s)
        return s.lower()

    @staticmethod
    def _search_key(title):
        """提取核心搜索关键词: 去季数/年份/标点, 取主标题"""
        t = re.sub(r'[（(][^（）()]*[）)]', '', title or '').strip()
        # 去季数标记: 第一季/第二季/第三季/第四季/第五季...
        t = re.sub(r'第[一二三四五六七八九十百\d]+季', '', t)
        # 去英文季数: S01/S1/Season 1
        t = re.sub(r'(?i)\s*s\d+\b|\s*season\s*\d+', '', t)
        # 去年份后缀: 2024/2025
        t = re.sub(r'\b(20\d{2})\b', '', t)
        # 去其他常见后缀
        t = re.sub(r'(完结|连载|高清|蓝光|4K|超清|国语|粤语|中字|双语|抢先版|修复版)', '', t, flags=re.I)
        t = t.strip()
        return t or (title or '').strip()

    @staticmethod
    def _fuzzy_match(search_title, candidate_name):
        """模糊匹配评分: 返回 0-100 分数, >40 视为匹配"""
        a = Spider._norm(search_title)
        b = Spider._norm(candidate_name)
        if not a or not b:
            return 0
        if a == b:
            return 100
        # 子串匹配
        if a in b or b in a:
            return 80
        # 字符集交集占比
        sa, sb = set(a), set(b)
        inter = sa & sb
        if not inter:
            return 0
        ratio = len(inter) / min(len(sa), len(sb))
        return int(ratio * 60)

    @staticmethod
    def _search_variants(title):
        """生成多组搜索关键词, 从精确到宽泛, 确保长标题也能命中"""
        variants = []
        core = Spider._search_key(title)
        if core:
            variants.append(core)
        # 去掉所有空格后再搜
        compact = re.sub(r'\s+', '', core)
        if compact and compact != core:
            variants.append(compact)

        # 按中文连接词截断: "西游记1之天地争霸" → "西游记1"
        # 常见模式: XXX之YYY / XXX的YYY / XXX与YYY / XXX·YYY
        parts = re.split(r'[之的与·\-—]+\s*', core)
        for p in parts:
            p = p.strip()
            if p and len(p) >= 2 and p not in variants:
                variants.append(p)

        # 去掉标题中的数字后缀再搜: "西游记1" → "西游记"
        no_num = re.sub(r'\d+$', '', core).strip()
        if no_num and len(no_num) >= 2 and no_num not in variants:
            variants.append(no_num)

        # 逐步截短: 6字→4字→3字, 覆盖超长标题
        base = no_num or compact or core
        for n in (6, 4, 3):
            if len(base) > n:
                short = base[:n]
                if short not in variants:
                    variants.append(short)

        # 原始标题(去括号)
        raw = re.sub(r'[（(][^（）()]*[）)]', '', title or '').strip()
        if raw and raw not in variants:
            variants.append(raw)
        return variants

    def _engine_lines(self, tag, disp, sp, title):
        try:
            tnorm = self._norm(title)
            if not tnorm:
                return [], []
            cached = self._detail_cache.get((tag, tnorm))
            if cached is not None:
                return cached

            # v5: 多关键词搜索 + 模糊匹配, 确保所有内容都能拿到聚合线路
            variants = self._search_variants(title)
            vid = None
            all_results = []
            for kw in variants:
                if not kw:
                    continue
                try:
                    res = self._engine_search(sp, kw)
                    if isinstance(res, str):
                        res = json.loads(res)
                    items = res.get('list') or []
                    if items:
                        all_results.extend(items)
                except Exception:
                    continue

            # 去重
            seen_ids = set()
            unique = []
            for it in all_results:
                vid_it = it.get('vod_id')
                if vid_it is not None and vid_it not in seen_ids:
                    seen_ids.add(vid_it)
                    unique.append(it)

            # 模糊匹配评分: 取最高分结果
            best_score = 0
            best_item = None
            for it in unique:
                score = self._fuzzy_match(title, it.get('vod_name') or '')
                if score > best_score:
                    best_score = score
                    best_item = it
            # 分数 > 30 即可使用(放宽匹配阈值)
            if best_item is not None and best_score >= 30:
                vid = best_item.get('vod_id')
            elif unique:
                # 兜底: 取第一个结果
                vid = unique[0].get('vod_id')
                best_item = unique[0]

            if vid is None:
                self._cache_put((tag, tnorm), ([], []))
                return [], []

            det = None
            try:
                det = sp.detailContent([vid])
            except TypeError:
                try:
                    det = sp.detailContent(vid)
                except Exception:
                    det = None
            except Exception:
                det = None
            if isinstance(det, str):
                det = json.loads(det)
            vod = ((det or {}).get('list') or [{}])[0]
            raw_from = (vod.get('vod_play_from') or '').strip()
            raw_urls = (vod.get('vod_play_url') or '').strip()
            if not raw_from or not raw_urls:
                self._cache_put((tag, tnorm), ([], []))
                return [], []

            names = [n.strip() for n in raw_from.split('$$$')]
            groups = raw_urls.split('$$$')
            show, play_urls = [], []
            for i, group in enumerate(groups):
                eflag = names[i] if i < len(names) and names[i] else '线路%d' % (i + 1)
                eflag = eflag.replace('@@', '@')
                episodes = []
                for part in group.split('#'):
                    part = part.strip()
                    if not part:
                        continue
                    if '$' in part:
                        ep_name, epid = part.split('$', 1)
                    else:
                        ep_name, epid = part, part
                    episodes.append(f"{ep_name}${tag}@@{eflag}@@{epid}")
                if episodes:
                    show.append(f"{disp}·{eflag}")
                    play_urls.append('#'.join(episodes))
            self._cache_put((tag, tnorm), (show, play_urls))
            return show, play_urls
        except Exception:
            return [], []

    @staticmethod
    def _engine_search(sp, title):
        try:
            return sp.searchContent(title, '1', '1')
        except TypeError:
            return sp.searchContent(title, '1')

    def _gather_engine_lines(self, title):
        engines = self._load_engines()
        if not engines:
            return []
        results = [None] * len(engines)
        done = False
        try:
            from concurrent.futures import ThreadPoolExecutor
            import concurrent.futures as cf
            ex = ThreadPoolExecutor(max_workers=min(8, len(engines)))
            try:
                futs = {ex.submit(self._engine_lines, t, d, s, title): i
                        for i, (t, d, s) in enumerate(engines)}
                finished, _ = cf.wait(set(futs), timeout=15)
                for f in finished:
                    results[futs[f]] = f.result()
                done = True
            finally:
                try:
                    ex.shutdown(wait=False)
                except Exception:
                    pass
        except Exception:
            done = False
        if not done:
            results = [self._engine_lines(t, d, s, title)
                       for t, d, s in engines]
        out = []
        for i, (t, _d, _s) in enumerate(engines):
            r = results[i]
            if r and (r[0] or r[1]):
                out.append((t, r[0], r[1]))
        return out

    def _cache_put(self, key, val):
        c = getattr(self, '_detail_cache', None)
        if c is None:
            return
        if len(c) > 300:
            for k in list(c)[:150]:
                c.pop(k, None)
        c[key] = val

    # ==================== 直链存活性探测 ====================
    def _probe_direct(self, url):
        try:
            r = self.fetch(url, headers={'User-Agent': 'okhttp/4.12.0',
                                         'Range': 'bytes=0-0'}, timeout=8)
            code = getattr(r, 'status_code', 0) or 0
            if 400 <= code < 600:
                return False
            return True
        except Exception:
            return True

    def _domain_alive(self, url):
        m = re.match(r'https?://([^/]+)', url or '')
        if not m:
            return True
        dom = m.group(1).lower()
        st = getattr(self, '_domain_status', {})
        if dom in st:
            return st[dom]
        alive = self._probe_direct(url)
        if not hasattr(self, '_domain_status'):
            self._domain_status = {}
        self._domain_status[dom] = alive
        return alive

    # ==================== 影探本体 ====================
    def ldmax_decrypt(self, encrypted_base64, depth=0):
        """本地 AES 解密 ldmax.cooom 加密链(非解析, 不请求外部 API)"""
        if depth > 5:
            return None
        cleaned = re.sub(r'\s+', '', encrypted_base64 or '')
        try:
            decoded = base64.b64decode(cleaned, validate=True).decode('utf-8', errors='ignore')
        except Exception:
            return encrypted_base64
        url = re.sub(r'\s+', '', decoded)
        if 'ldmax.cooom' not in url:
            return url
        path = re.sub(r'https?://ldmax\.cooom/', '', url)
        if len(path) < 16:
            return None
        key = path[:16][::-1].encode('utf-8')
        ciphertext_b64 = re.sub(r'\s+', '', path[16:])
        try:
            ciphertext = base64.b64decode(ciphertext_b64, validate=True)
            cipher = AES.new(key, AES.MODE_CBC, key)
            decrypted = cipher.decrypt(ciphertext)
        except Exception:
            return None
        if decrypted:
            pad = decrypted[-1]
            if 0 < pad <= 16:
                decrypted = decrypted[:-pad]
        result = decrypted.decode('utf-8', errors='ignore').strip()
        if 'ldmax.cooom' in result:
            return self.ldmax_decrypt(base64.b64encode(result.encode('utf-8')).decode('utf-8'), depth + 1)
        return result

    def lvdou(self, text):
        if AES is None:
            return text
        key = self.cmskey[:16].encode("utf-8")
        iv = self.cmskey[-16:].encode("utf-8")
        url_prefix = "lvdou+"
        text = text or ''
        if text.startswith(url_prefix):
            ciphertext_b64 = text[len(url_prefix):]
            try:
                cipher = AES.new(key, AES.MODE_CBC, iv)
                ct_bytes = base64.b64decode(ciphertext_b64)
                pt_bytes = cipher.decrypt(ct_bytes)
                return unpad(pt_bytes, AES.block_size).decode('utf-8')
            except Exception:
                return text
        return text

    @staticmethod
    def clean_url(url):
        if not url:
            return url
        url = url.strip().replace(' ', '%20')
        if url.startswith('http%3A') or url.startswith('https%3A'):
            try:
                url = unquote(url)
            except Exception:
                pass
        try:
            url = quote(url, safe=":/?&=#%@+,;$!'()*~[]")
        except Exception:
            pass
        return url

    # ---------- v4: 线路分级排序 ----------
    @staticmethod
    def _line_grade(name, tag=''):
        """线路质量分级: S=4K/蓝光, A=聚合直链, B=引擎线路, C=影探本体"""
        n = (name or '').lower()
        t = (tag or '').lower()
        if re.search(r'4k|蓝光|超清|uhd|bluray', n):
            return 0
        if t.startswith('fb') or '聚合' in n:
            return 1
        if t and not t.startswith('fb'):
            return 2
        if '影探' in n:
            return 3
        return 4

    @staticmethod
    def _sort_lines(items):
        """items: [(show_name, play_url, tag), ...] → 按分级排序"""
        return sorted(items, key=lambda x: Spider._line_grade(x[0], x[2]))

    # ---------- v4: 详情(聚合线路前置 + 分级排序 + 死链过滤) ----------
    def detailContent(self, ids, *args):
        self._ensure_state()
        try:
            data = self.fetch(f"{self.host}/api.php/app/video_detail?id={ids[0]}",
                              headers=self.headers, timeout=20).json()
        except Exception:
            return {'list': []}

        vod_data = data.get('data') or {}
        if not vod_data:
            return {'list': []}

        title = vod_data.get('vod_name') or ''

        # --- 1. 影探本体线路(死链组排末尾, 不直接剔除) ---
        body_lines = []  # [(show, play_url, tag, eps)]
        raw_from = (vod_data.get('vod_play_from') or '').strip()
        raw_urls = (vod_data.get('vod_play_url') or '').strip()
        if raw_from and raw_urls:
            names = [n.strip() for n in raw_from.split('$$$')]
            groups = raw_urls.split('$$$')
            for i, group in enumerate(groups):
                name = names[i] if i < len(names) and names[i] else '线路%d' % (i + 1)
                episodes = []
                for part in group.split('#'):
                    part = part.strip()
                    if not part:
                        continue
                    if '$' in part:
                        episode, url = part.split('$', 1)
                        episodes.append((episode, self.lvdou(url)))
                    else:
                        episodes.append((part, self.lvdou(part)))
                if not episodes:
                    continue
                play_url = '#'.join(f"{n}${u}" for n, u in episodes)
                body_lines.append(('影探·' + name, play_url, '', episodes))

        # --- 2. 引擎/聚合线路(并发跨站同名匹配) ---
        engine_lines = []  # [(show, play_url, tag, eps)]
        engine_groups = []
        if title:
            for tag, s_list, u_list in self._gather_engine_lines(title):
                for s, u in zip(s_list, u_list):
                    if not s or not u:
                        continue
                    eps = []
                    for part in u.split('#'):
                        part = part.strip()
                        if not part:
                            continue
                        if '$' in part:
                            en, eid = part.split('$', 1)
                        else:
                            en, eid = part, part
                        eps.append((en, eid))
                    engine_lines.append((s, u, tag, eps))
                    engine_groups.append((tag, s, eps))

        # --- 3. 合并所有线路, 按分级排序(聚合直链优先) ---
        all_lines = []
        for s, u, t, eps in engine_lines:
            all_lines.append((s, u, t, eps))
        for s, u, t, eps in body_lines:
            all_lines.append((s, u, t, eps))

        # 死链探测: 直链线路域名级存活检测, 死链降级到末尾
        alive_lines, dead_lines = [], []
        for s, u, t, eps in all_lines:
            first_url = eps[0][1] if eps else ''
            is_direct = self.check_paly_url(first_url)
            if is_direct and not self._domain_alive(first_url):
                dead_lines.append((s, u, t, eps))
            else:
                alive_lines.append((s, u, t, eps))

        sorted_lines = self._sort_lines(alive_lines) + self._sort_lines(dead_lines)

        if not sorted_lines:
            return {'list': [vod_data]}

        show = [s for s, _, _, _ in sorted_lines]
        play_urls = [u for _, u, _, _ in sorted_lines]

        # --- 4. 构建播放降级映射(每集多线路自动切换) ---
        self._build_play_map(
            [(s, eps) for s, _, _, eps in [l for l in sorted_lines if not l[2]]],  # body
            [(t, s, eps) for s, _, t, eps in [l for l in sorted_lines if l[2]]],   # engine
        )

        vod_data.pop('vod_url_with_player', None)
        vod_data['vod_play_from'] = '$$$'.join(show)
        vod_data['vod_play_url'] = '$$$'.join(play_urls)
        return {'list': [vod_data]}

    def _build_play_map(self, body_groups, engine_groups):
        """v4: 为每集构建多线路降级表。
        body_groups: [(name, eps)]  影探本体线路
        engine_groups: [(tag, disp, eps)]  引擎线路
        降级链: 原始URL → 引擎线路列表 → 聚合m3u8直链"""
        if not hasattr(self, '_play_map'):
            self._play_map = {}
        if not hasattr(self, '_ep_fallback'):
            self._ep_fallback = {}

        # 收集所有引擎线路按集索引: {ep_index: [(tag, eflag, eid), ...]}
        ep_engines = {}
        for tag, _disp, eps in engine_groups:
            for ei, (_n, eid) in enumerate(eps):
                ep_engines.setdefault(ei, []).append((tag, _disp, eid))

        # 为影探本体每集记录: 引擎降级列表 + m3u8兜底直链
        fb_eps = None
        for tag, _disp, eps in engine_groups:
            if not str(tag).startswith('fb') or not eps:
                continue
            urls, ok = [], True
            for _n, eid in eps:
                ps = eid.split('@@', 2)
                if len(ps) != 3 or not self.check_paly_url(ps[2]):
                    ok = False
                    break
                urls.append(ps[2])
            if ok and urls:
                fb_eps = list(zip([n for n, _ in eps], urls))
                break

        for _name, eps in body_groups:
            for ei, (_n, u) in enumerate(eps):
                # m3u8 兜底直链
                if fb_eps:
                    mu = fb_eps[min(ei, len(fb_eps) - 1)][1]
                    if mu and mu != u:
                        self._play_map.setdefault(u, {})['m3u8'] = mu
                # 引擎降级列表
                eng_list = ep_engines.get(ei, [])
                if eng_list:
                    self._ep_fallback.setdefault(u, {})['engines'] = eng_list

        for k in (list(self._play_map)[:1500], list(self._ep_fallback)[:1500]):
            pass  # LRU trim below
        if len(self._play_map) > 3000:
            for k in list(self._play_map)[:1500]:
                self._play_map.pop(k, None)
        if len(self._ep_fallback) > 3000:
            for k in list(self._ep_fallback)[:1500]:
                self._ep_fallback.pop(k, None)

    # ---------- v4: 播放(多线路自动降级, 统一 parse=0) ----------
    def playerContent(self, flag, video_id, vipFlags, *args):
        self._ensure_state()
        video_id = video_id or ''

        # ===== 引擎线路路由: tag@@线路码@@引擎剧集id =====
        if '@@' in video_id:
            parts = video_id.split('@@', 2)
            if len(parts) == 3:
                tag, eflag, eid = parts
                r = self._try_engine(tag, eflag, eid)
                if r and r.get('url'):
                    r.setdefault('header', {'User-Agent': BROWSER_UA})
                    r.setdefault('parse', 0)
                    r.setdefault('jx', 0)
                    r.setdefault('playUrl', '')
                    return r
                # 引擎失败 → 尝试同集其他引擎线路
                alt = self._ep_fallback.get(video_id) or self._ep_fallback.get(eid)
                if alt and alt.get('engines'):
                    for at, af, ae in alt['engines']:
                        if at == tag:
                            continue
                        r2 = self._try_engine(at, af, ae)
                        if r2 and r2.get('url'):
                            r2.setdefault('header', {'User-Agent': BROWSER_UA})
                            r2.setdefault('parse', 0)
                            r2.setdefault('jx', 0)
                            r2.setdefault('playUrl', '')
                            return r2
                # 所有引擎失败 → m3u8 兜底
                fb = self._play_map.get(video_id) or self._play_map.get(eid) or {}
                if fb.get('m3u8'):
                    return {'jx': 0, 'parse': 0, 'playUrl': '', 'url': fb['m3u8'],
                            'header': {'User-Agent': BROWSER_UA}}
                return {'jx': 0, 'parse': 0, 'playUrl': '', 'url': '',
                        'header': self.headers}

        # ===== 影探本体线路 =====
        raw_id = video_id
        video_id = self.lvdou(video_id)
        video_id = self.clean_url(video_id)

        # ldmax 本地解密(非解析, 不请求外部 API)
        if not re.match(r'^https?://', video_id):
            decrypted = self.ldmax_decrypt(video_id)
            if decrypted and re.match(r'^https?://', decrypted):
                video_id = decrypted

        # 直链 → parse=0 直接播放
        if self.check_paly_url(video_id):
            if self._domain_alive(video_id):
                return {'jx': 0, 'parse': 0, 'playUrl': '', 'url': video_id,
                        'header': self.headers}
            # --- 死链自动降级: 引擎线路 → m3u8 兜底 ---
            alt = self._ep_fallback.get(raw_id) or self._ep_fallback.get(video_id) or {}
            if alt.get('engines'):
                for at, af, ae in alt['engines']:
                    r = self._try_engine(at, af, ae)
                    if r and r.get('url'):
                        r.setdefault('header', {'User-Agent': BROWSER_UA})
                        r.setdefault('parse', 0)
                        r.setdefault('jx', 0)
                        r.setdefault('playUrl', '')
                        return r
            fb = self._play_map.get(raw_id) or self._play_map.get(video_id) or {}
            if fb.get('m3u8'):
                return {'jx': 0, 'parse': 0, 'playUrl': '', 'url': fb['m3u8'],
                        'header': {'User-Agent': BROWSER_UA}}
            return {'jx': 0, 'parse': 0, 'playUrl': '', 'url': '',
                    'header': self.headers}

        # 非直链 URL → 统一 parse=0 返回原始 URL(无解析)
        return {'jx': 0, 'parse': 0, 'playUrl': '', 'url': video_id,
                'header': self.headers}

    def _try_engine(self, tag, eflag, eid):
        """尝试调用指定引擎获取播放地址, 失败返回 None"""
        for t, _disp, sp in self._load_engines():
            if t != tag:
                continue
            try:
                r = sp.playerContent(eflag, eid, [])
            except TypeError:
                try:
                    r = sp.playerContent(eflag, eid)
                except Exception:
                    return None
            except Exception:
                return None
            if isinstance(r, str):
                try:
                    r = json.loads(r)
                except Exception:
                    return None
            return r if (r and r.get('url')) else None
        return None

    # ---------- 其他接口 ----------
    def homeVideoContent(self):
        try:
            data = self.fetch(f"{self.host}/api.php/app/index_video?token=",
                              headers=self.headers, timeout=20).json()
            videos = []
            for item in data.get('list') or []:
                videos.extend(item.get('vlist') or [])
            return {'list': videos}
        except Exception:
            return {'list': []}

    def homeContent(self, filter, *args):
        try:
            data = self.fetch(f"{self.host}/api.php/app/nav?token=",
                              headers=self.headers, timeout=20).json()
        except Exception:
            return {"class": [], "filters": {}}

        keys = ["class", "area", "lang", "year", "letter", "by", "sort"]
        filters = {}
        classes = []
        for item in data.get('list') or []:
            has_non_empty_field = False
            jsontype_extend = item.get("type_extend") or {}
            classes.append({"type_name": item.get("type_name"), "type_id": item.get("type_id")})
            for key in keys:
                v = jsontype_extend.get(key, '')
                if v and v.strip() != "":
                    has_non_empty_field = True
                    break
            if has_non_empty_field:
                filters[str(item.get("type_id"))] = []
            for dkey in jsontype_extend:
                if dkey in keys and jsontype_extend[dkey].strip() != "":
                    values = jsontype_extend[dkey].split(",")
                    value_array = []
                    for value in values:
                        if value.strip() != "":
                            value_array.append({"n": value.strip(), "v": value.strip()})
                    filters.setdefault(str(item.get("type_id")), []).append(
                        {"key": dkey, "name": dkey, "value": value_array})
        return {"class": classes, "filters": filters}

    def categoryContent(self, tid, pg, filter, extend, *args):
        try:
            query_params = [f"tid={tid}", f"pg={pg}", "limit=18"]
            for k in ('class', 'area', 'lang', 'year'):
                if extend and extend.get(k):
                    query_params.append(f"{k}={extend.get(k)}")
            url = f"{self.host}/api.php/app/video?" + "&".join(query_params)
            return self.fetch(url, headers=self.headers, timeout=20).json()
        except Exception:
            return {'list': [], 'page': pg, 'pagecount': 0, 'total': 0}

    def searchContent(self, key, quick, pg="1", *args):
        try:
            data = self.fetch(f"{self.host}/api.php/app/search?text={quote(key)}&pg={pg}",
                              headers=self.headers, timeout=20).json()
            videos = data.get('list') or []
            for item in videos:
                item.pop('type', None)
            return {'list': videos, 'page': pg}
        except Exception:
            return {'list': [], 'page': pg}

    @staticmethod
    def check_paly_url(content):
        """直链判定(决定 parse=0 直接播放): 必须以视频扩展名结尾(允许带query/hash)。"""
        return bool(re.search(
            r"https?://\S+\.(?:mp4|m3u8|flv|avi|mkv|ts|mov|wmv|webm)(?:[?#]\S*)?$",
            content or '', re.IGNORECASE))

    def getName(self): return "lyyytv"
    def localProxy(self, param): return {}
    def isVideoFormat(self, url): return False
    def manualVideoCheck(self): return False
