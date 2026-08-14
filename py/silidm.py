# -*- coding: utf-8 -*-
import sys
import json
import re
import urllib.parse
sys.path.append('..')
from base.spider import Spider

class Spider(Spider):
    def init(self, extend=''):
        self.host = 'https://silidm.com'
        self.ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        self.headers = {'User-Agent': self.ua, 'Referer': self.host + '/'}

    def getName(self):
        return '电影先生'

    def homeContent(self, filter):
        result = {}
        result['class'] = [
            {'type_name': '电影', 'type_id': 'dy'},
            {'type_name': '剧集', 'type_id': 'juji'},
            {'type_name': '动漫', 'type_id': 'dongman'},
            {'type_name': '综艺', 'type_id': 'zongyi'},
        ]
        result['filters'] = {}
        return result

    def categoryContent(self, tid, pg, filter, extend):
        result = {}
        try:
            url = self.host + '/type/' + tid + '.html'
            if int(pg) > 1:
                url = url + '?page=' + str(pg)
            html = self.fetch(url, headers=self.headers)
            items = self._parse_items(html)
            result['list'] = items
            result['page'] = pg
            result['pagecount'] = 999
            result['limit'] = 20
            result['total'] = len(items)
        except Exception as e:
            print(f'[电影先生] categoryContent error: {e}')
        return result

    def detailContent(self, ids):
        result = {}
        try:
            url = self.host + ids[0]
            html = self.fetch(url, headers=self.headers)
            vod = self._parse_detail(html)
            if vod:
                result['list'] = [vod]
            else:
                result['list'] = []
        except Exception as e:
            print(f'[电影先生] detailContent error: {e}')
            result['list'] = []
        return result

    def searchContent(self, key, pg, filter, extend):
        result = {}
        try:
            encoded = urllib.parse.quote(key)
            url = self.host + '/search/' + encoded + '-------------.html'
            if int(pg) > 1:
                url = url + '?page=' + str(pg)
            html = self.fetch(url, headers=self.headers)
            items = self._parse_items(html)
            result['list'] = items
            result['page'] = pg
            result['pagecount'] = 999
            result['limit'] = 20
            result['total'] = len(items)
        except Exception as e:
            print(f'[电影先生] searchContent error: {e}')
        return result

    def playerContent(self, flag, id, vipFlags):
        result = {}
        try:
            url = self.host + id
            html = self.fetch(url, headers=self.headers)
            m3u8 = self._extract_m3u8(html)
            if m3u8:
                result['parse'] = 0
                result['url'] = m3u8
                result['header'] = self.headers
            else:
                result['parse'] = 1
                result['url'] = self.host + id
                result['header'] = self.headers
        except Exception as e:
            print(f'[电影先生] playerContent error: {e}')
            result['parse'] = 1
            result['url'] = self.host + id
            result['header'] = self.headers
        return result

    def _parse_items(self, html):
        items = []
        seen = set()
        parts = html.split('<div class="module-item">')[1:]
        for part in parts:
            link_m = re.search(r'href=["\x27](/video/\d+\.html)["\x27]', part)
            if not link_m:
                continue
            link = link_m.group(1)
            if link in seen:
                continue
            seen.add(link)
            title_m = re.search(r'title=["\x27]([^"\x27]+)["\x27]', part)
            title = title_m.group(1).strip() if title_m else ''
            pic_m = re.search(r'data-src=["\x27](https?://[^"\x27]+)["\x27]', part)
            pic = pic_m.group(1) if pic_m else ''
            remark_m = re.search(r'class="module-item-text">([^<]+)</div>', part)
            remark = remark_m.group(1).strip() if remark_m else ''
            caption_m = re.search(r'class="module-item-caption">([\s\S]*?)</div>', part)
            year, area = '', ''
            if caption_m:
                spans = re.findall(r'<span[^>]*>([^<]*)</span>', caption_m.group(1))
                year = spans[0] if len(spans) > 0 and spans[0].isdigit() else ''
                area = spans[2] if len(spans) > 2 else ''
            items.append({'vod_id': link, 'vod_name': title, 'vod_pic': pic, 'vod_remarks': remark, 'vod_year': year, 'vod_area': area, 'vod_play_from': self.getName(), 'vod_play_url': ''})
        return items

    def _parse_detail(self, html):
        tm = re.search(r'<title>([^<]+)</title>', html)
        raw = tm.group(1).strip() if tm else ''
        title = re.sub(r'\s*[-|].*', '', raw).strip()
        pics = re.findall(r'data-src=["\x27](https?://[^"\x27]+)["\x27]', html)
        pic = pics[0] if pics else ''
        im = re.search(r'class="module-info-content[^>]*>(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL)
        info = im.group(1) if im else ''
        year = ''
        ym = re.search(r'<span>(\d{4})</span>', info)
        year = ym.group(1) if ym else ''
        area = ''
        am = re.search(r'href="(/show/\d+---([^<]*))"', info)
        area = am.group(2).strip() if am else ''
        content = ''
        dm = re.search(r'class="module-info-desc[^>]*>[\s\S]*?<p[^>]*>(.*?)</p>', html, re.DOTALL)
        if dm:
            content = re.sub(r'<[^>]+>', '', dm.group(1)).strip()
        plays = re.findall(r'href="(/play/\d+-(\d+)-\d+\.html)"[^>]*>(.*?)</a>', html, re.DOTALL)
        lines = {}
        for pl, lid, pt in plays:
            txt = re.sub(r'<[^>]+>', '', pt).strip()
            if txt:
                lines.setdefault(lid.strip(), []).append((txt, pl))
        if not lines:
            plays2 = re.findall(r'href="(/play/[^"\s]+)"[^>]*title="([^"]+)"', html)
            for pl, en in plays2:
                lines.setdefault('1', []).append((en, pl))
        pf, pu = [], []
        for lid in sorted(lines.keys()):
            eps = lines[lid]
            pf.append('线路' + lid)
            pu.append('#'.join(n + '$' + self.host + l for n, l in eps))
        if not pf:
            pf.append(self.getName())
            pu.append('')
        return {'vod_id': '', 'vod_name': title, 'vod_pic': pic, 'vod_year': year, 'vod_area': area, 'vod_content': content, 'vod_play_from': '$'.join(pf), 'vod_play_url': '$'.join(pu)}

    def _extract_m3u8(self, html):
        pm = re.search(r'var\s+player_aaaa\s*=\s*\{(.*?)\}', html, re.S)
        if pm:
            try:
                cfg = json.loads('{' + pm.group(1) + '}')
                url = cfg.get('url', '')
                if url:
                    return url.replace('\\/', '/')
            except Exception:
                pass
        ms = re.findall(r'(https?://[^"\s<]+\.m3u8[^"\s<]*)', html)
        return ms[0] if ms else ''
