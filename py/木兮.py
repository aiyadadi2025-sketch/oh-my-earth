# -*- coding: utf-8 -*-
# by @嗷呜
import base64
import sys
from pprint import pprint

import hmac
import hashlib
import secrets
import time
import uuid
import json
import random
import string
from urllib.parse import quote

import requests
from Crypto.Cipher import AES
from Crypto.Hash import MD5
from Crypto.Util.Padding import pad
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
sys.path.append('..')
from base.spider import Spider


class Spider(Spider):

    def init(self, extend='{}'):
        self.session = requests.session()
        pass

    def getName(self):
        pass

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass

    host='https://film.symx.club'
    RSA_N = "c1e3934d1614465b33053e7f48ee4ec87b14b95ef88947713d25eecbff7e74c7977d02dc1d9451f79dd5d1c10c29acb6a9b4d6fb7d0a0279b6719e1772565f09af627715919221aef91899cae08c0d686d748b20a3603be2318ca6bc2b59706592a9219d0bf05c9f65023a21d2330807252ae0066d59ceefa5f2748ea80bab81"
    RSA_E = 65537
    STATIC_BASE = "https://static.geetest.com/"
    VERIFY_HOST = 'https://gcaptcha4.geetest.com'
    ClientId=MD5.new(str(int(time.time())).encode()).hexdigest();Token=''
    _config_cache = {}

    def rsa_encrypt(self,random_key):
        pub_key = RSA.construct((int(self.RSA_N, 16), self.RSA_E))
        cipher = PKCS1_v1_5.new(pub_key)
        return cipher.encrypt(random_key.encode('utf-8')).hex()

    def aes_encrypt(self,plaintext, key):
        iv = b"0000000000000000"
        cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, iv)
        padded_data = pad(plaintext.encode('utf-8'), AES.block_size)
        return cipher.encrypt(padded_data).hex()

    def get_w(self, payload_dict):
        random_key = "".join(random.choices(string.ascii_letters + string.digits, k=16))
        json_str = json.dumps(payload_dict, separators=(',', ':'))
        return self.aes_encrypt(json_str, random_key) + self.rsa_encrypt(random_key)

    def get_dynamic_payload(self,lot_number, set_left, passtime, captcha_id, pow_detail):
        key_name = lot_number[26:30] + lot_number[12:16]
        sub_key = lot_number[16:24]
        val = lot_number[6:10]
        pow_msg = f"1|0|md5|{pow_detail['datetime']}|{captcha_id}|{lot_number}||{secrets.token_hex(8) }"
        pow_sign = hashlib.md5(pow_msg.encode()).hexdigest()
        payload = {
            "setLeft": set_left,
            "passtime": passtime,
            "userresponse": set_left / 1.0059466666666665 +2,
            "device_id": "",
            "lot_number": lot_number,
            "pow_msg": pow_msg,
            "pow_sign": pow_sign,
            "geetest": "captcha",
            "lang": "zh",
            "ep": "123",
            "biht": "1426265548",
            "yDWL": "hZGx",
            key_name: {sub_key: val},
            "em": {"ph": 0, "cp": 0, "ek": "11", "wd": 1, "nt": 0, "si": 0, "sc": 0}
        }
        return payload

    def generate_checksum_timestamp(self):
        r = str(int(time.time() * 1000))
        prefix = r[:-1]
        digit_sum = sum(int(d) for d in prefix)
        check_digit = digit_sum % 10
        return prefix + str(check_digit)

    def _get_config(self):
        if not self._config_cache:
            try:
                h = self.get_site_headers('/category/top', 1)
                r = self.session.get(f'{self.host}/api/system/config', headers=h, timeout=10)
                d = r.json().get('data', {})
                self._config_cache = {
                    'session': d.get('session', ''),
                    'reportId': d.get('reportId', 'X-Report-Id'),
                    'traceId': d.get('traceId', ''),
                }
            except Exception:
                self._config_cache = {'session': '', 'reportId': 'X-Report-Id', 'traceId': ''}
        return self._config_cache

    def get_site_headers(self, path, end=0):
        secret_key = "lslx_sk"
        timestamp = self.generate_checksum_timestamp()
        raw_data = f"{timestamp}symx_{secret_key}{path}"
        arranged = raw_data.replace("1", "i").replace("0", "o").replace("5", "s")
        if end:
            secret_key = ''
            arranged = ''
        signature = hmac.new(
            secret_key.encode('utf-8'),
            arranged.encode('utf-8'),
            digestmod=hashlib.sha256
        ).hexdigest()
        header = {
            'User-Agent': 'SYMX_ANDROID',
            'user-agent': 'Mozilla/5.0 (Linux; Android 13; M2012K10C Build/TP1A.220624.014; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.0.0 Mobile Safari/537.36 uni-app Html5Plus/1.0 (Immersed/30.545454)',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json;charset=UTF-8',
            'X-Platform': 'android',
            'X-Timestamp': timestamp,
            'X-Sign-X': signature,
            'X-Client-Id': self.ClientId,
            'Referer': 'https://film.symx.club/',
        }
        if self.Token:
            header['X-Verify-Token'] = self.Token
        config = self._get_config()
        report_id = config.get('reportId', 'X-Report-Id')
        if end:
            header[report_id] = signature
        else:
            header[report_id] = self._gen_web_sig(path, timestamp, config)
        return header

    def _gen_web_sig(self, path, timestamp, config):
        session_key = config.get('session', '')
        trace_id = config.get('trace_id', '')
        raw = f"{timestamp}symx_{session_key}{path}"
        arranged = raw.replace("1", "i").replace("0", "o").replace("5", "s")
        return hmac.new(session_key.encode(), arranged.encode(), hashlib.sha256).hexdigest() if session_key else ''

    def run_verify(self, i=0):
        """获取验证token"""
        if i > 3:
            print("验证失败次数过多")
            return
        try:
            # 获取验证码
            h = self.get_site_headers('/auth/captcha')
            r = self.session.get(f'{self.host}/api/auth/captcha', headers=h, timeout=10)
            captcha_data = r.json().get('data', {})
            uuid = captcha_data.get('uuid', '')
            captcha_b64 = captcha_data.get('captcha', '')
            if not uuid or not captcha_b64:
                print("获取验证码失败")
                return self.run_verify(i + 1)

            # 保存验证码图片
            img_data = base64.b64decode(captcha_b64.split(',')[1] if ',' in captcha_b64 else captcha_b64)
            captcha_path = os.path.join(os.path.dirname(__file__), 'captcha_temp.png')
            with open(captcha_path, 'wb') as f:
                f.write(img_data)
            print(f"验证码已保存: {captcha_path}")

            # 尝试OCR识别
            captcha_text = ''
            try:
                from cnocr import CnOcr
                ocr = CnOcr()
                result = ocr.ocr(captcha_path)
                if result and isinstance(result, list) and len(result) > 0:
                    texts = [line.get('text', '') for line in result if isinstance(line, dict)]
                    captcha_text = ''.join(texts).strip()
            except Exception as e:
                print(f"OCR识别失败: {e}")

            # 手动输入（如果OCR失败或为空）
            if not captcha_text:
                captcha_text = input("请输入验证码: ").strip()
            if not captcha_text:
                captcha_text = '27WM'

            print(f"提交验证码: {captcha_text}")

            # 提交验证
            body = {'uuid': uuid, 'answer': captcha_text}
            h_post = dict(h)
            h_post['Content-Type'] = 'application/json;charset=UTF-8'
            r = self.session.post(f'{self.host}/api/auth/verify', headers=h_post, json=body, timeout=10)
            d = r.json()
            print(f"验证结果: code={d.get('code')}, msg={d.get('message','')}")

            if d.get('code') == 200 and d.get('data'):
                self.Token = d['data'].get('token', '')
                print(f"Token获取成功")
            else:
                print("验证失败，可能需要重新获取验证码")
                return self.run_verify(i + 1)
        except Exception as e:
            print(f"验证异常: {e}")
            return self.run_verify(i + 1)

    def Req(self, path, params, i=0):
        headers = self.get_site_headers(path.split("/api")[-1], i)
        self.session.headers.update(headers)
        resp = self.session.get(f"{self.host}{path}", params=params)
        jd = resp.json()
        if jd.get('code') == 1004 and i == 0:
            self.run_verify()
            headers = self.get_site_headers(path.split("/api")[-1], i)
            self.session.headers.update(headers)
            resp = self.session.get(f"{self.host}{path}", params=params)
            jd = resp.json()
        return jd

    def homeContent(self, filter):
        data = self.Req("/api/category/top", {}, 1)
        result = {}
        classes = []
        for k in data['data']:
            classes.append({
                'type_name': k['name'],
                'type_id': k['id']
            })
        try:
            filters_resp = self.fetch("http://mytv6688.xyz/pyplugin/木兮筛选.json")
            result['filters'] = filters_resp.json()
        except Exception:
            result['filters'] = self._get_default_filters()
        result['class'] = classes
        return result

    def _get_default_filters(self):
        return {
            "1": [{"key": "areaOptions", "name": "地区", "value": []},
                  {"key": "languageOptions", "name": "语言", "value": []},
                  {"key": "sortOptions", "name": "排序", "value": []},
                  {"key": "yearOptions", "name": "年份", "value": []}],
            "2": [{"key": "areaOptions", "name": "地区", "value": []},
                  {"key": "languageOptions", "name": "语言", "value": []},
                  {"key": "sortOptions", "name": "排序", "value": []},
                  {"key": "yearOptions", "name": "年份", "value": []}],
            "3": [{"key": "areaOptions", "name": "地区", "value": []},
                  {"key": "languageOptions", "name": "语言", "value": []},
                  {"key": "sortOptions", "name": "排序", "value": []},
                  {"key": "yearOptions", "name": "年份", "value": []}],
            "4": [{"key": "areaOptions", "name": "地区", "value": []},
                  {"key": "languageOptions", "name": "语言", "value": []},
                  {"key": "sortOptions", "name": "排序", "value": []},
                  {"key": "yearOptions", "name": "年份", "value": []}],
            "5": [{"key": "areaOptions", "name": "地区", "value": []},
                  {"key": "languageOptions", "name": "语言", "value": []},
                  {"key": "sortOptions", "name": "排序", "value": []},
                  {"key": "yearOptions", "name": "年份", "value": []}]
        }

    def homeVideoContent(self):
        data=self.Req("/api/poster/list",{},1)
        vlist = []
        for k in data['data']:
            vlist.append({
                'vod_id': k.get('filmId'),
                'vod_name': k.get('filmName'),
                'vod_pic': k.get('poster'),
            })
        return {'list':vlist}

    def getList(self,data):
        vlist = []
        for k in data:
            vlist.append({
                'vod_id': k.get('id'),
                'vod_name': k.get('name'),
                'vod_pic': k.get('cover'),
                'vod_remarks': k.get('updateStatus'),
            })
        return vlist

    def categoryContent(self, tid, pg, filter, extend):
        params={
          "area": extend.get('areaOptions', ''),
          "childCategoryId": "",
          "categoryId": tid,
          "language": extend.get('languageOptions', ''),
          "pageNum": pg,
          "pageSize": "10",
          "sort": extend.get('sortOptions', ''),
          "year": extend.get('yearOptions', '')
        }
        resp=self.Req("/api/film/category/list", params)
        result = {}
        result['list'] =self.getList(resp['data']['list'])
        result['page'] = pg
        result['pagecount'] = 9999
        result['limit'] = 90
        result['total'] = 999999
        return result

    def detailContent(self, ids):
        resp = self.Req("/api/film/detail/play/app", {'id': ids[0]})
        if resp.get('code') != 200 or not resp.get('data'):
            return {'list': [{
                'vod_id': ids[0],
                'vod_name': '加载失败',
                'vod_pic': '',
                'vod_play_from': '加载失败',
                'vod_play_url': ''
            }]}
        v = resp['data']
        n, p = [], []
        for i in v.get('playLineList') or []:
            n.append(i.get('playerName', ''))
            m = [f"{j.get('name','')}${j.get('id','')}" for j in i.get('lines') or []]
            p.append('#'.join(m))
        vod = {
            'type_name': v.get('categoryName', ''),
            'vod_year': v.get('year', ''),
            'vod_area': v.get('area', ''),
            'vod_remarks': v.get('updateStatus', ''),
            'vod_actor': v.get('actor', ''),
            'vod_director': '云霄仙子（困困版）',
            'vod_content': v.get('blurb', ''),
            'vod_play_from': '$$$'.join(n),
            'vod_play_url': '$$$'.join(p)
        }
        return {'list': [vod]}

    def searchContent(self, key, quick, pg="1"):
        params = {"pageNum": pg, "pageSize": "10", "keyword": key}
        resp = self.Req('/api/film/search', params=params)
        if resp.get('code') == 200 and resp.get('data'):
            return {'list': self.getList(resp['data'].get('list', [])), 'page': pg}
        return {'list': [], 'page': pg}

    def playerContent(self, flag, id, vipFlags):
        resp = self.Req("/api/line/play/parse", {"lineId": id})
        url = resp.get('data', '')
        if resp.get('code') != 200 or not url:
            url = ''
        return {'parse': 0, 'url': url, 'header': ''}

    def localProxy(self, param):
        pass

    def liveContent(self, url):
        pass


if __name__ == "__main__":
    sp = Spider()
    formatJo = sp.init()
    formatJo = sp.homeContent(False)  # 主页，等于真表示启用筛选
    # formatJo = sp.homeVideoContent()  # 主页视频
    # formatJo = sp.searchContent("斗罗",False,'1') # 搜索{"area":"大陆","by":"hits","class":"国产","lg":"国语"}
    # formatJo = sp.categoryContent('2', '1', False, {})  # 分类
    # formatJo = sp.detailContent(['126634'])  # 详情
    # formatJo = sp.playerContent("","https://www.yingmeng.net/vodplay/140148-2-1.html",{}) # 播放
    # formatJo = sp.localProxy({"":"https://www.yingmeng.net/vodplay/140148-2-1.html"}) # 播放
    pprint(formatJo)
