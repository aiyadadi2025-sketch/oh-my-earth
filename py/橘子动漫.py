# -*- coding: utf-8 -*-
"""
橘子动漫 (mgnacg.com) 爬虫 - TVBox / 影视仓适配
- 分类列表通过 AJAX API 获取（time+key 认证）
- 搜索通过 MacCMS suggest 接口
- 详情页 HTML 解析剧集列表
- 播放地址通过 AES-CBC 解密获取
"""

import re
import json
import time
import hashlib
import base64
import logging
import urllib.parse
import os
import sys

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# AES 解密库兼容
_AES_BACKEND = None
try:
    from Crypto.Cipher import AES as _PyCryptoAES
    from Crypto.Util.Padding import unpad as _pycrypto_unpad
    _AES_BACKEND = "pycryptodome"
except ImportError:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher as _CryptoCipher
        from cryptography.hazmat.primitives.ciphers import algorithms as _crypto_algos
        from cryptography.hazmat.primitives.ciphers import modes as _crypto_modes
        from cryptography.hazmat.primitives import padding as _crypto_padding
        from cryptography.hazmat.backends import default_backend as _crypto_backend
        _AES_BACKEND = "cryptography"
    except ImportError:
        _AES_BACKEND = None

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from base.spider import Spider as BaseSpider
except ImportError:
    BaseSpider = object

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 禁用 SSL 警告
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


class Spider(BaseSpider):
    """橘子动漫爬虫 - TVBox 适配"""

    BASE_URL = "https://www.mgnacg.com"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 12; SM-S908U) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.mgnacg.com/",
        "Connection": "keep-alive",
    }

    # 分类映射
    CATEGORY_MAP = {
        "1":  "动漫",
        "2":  "剧场版",
        "29": "7月新番",
        "32": "10月新番",
        "5":  "4月新番",
        "6":  "1月新番",
        "4":  "BD动漫",
        "3":  "迷之花园",
    }

    # API 认证密钥
    API_UID = "DCC147D11943AF75"
    API_SALT = "DS"

    # 播放器解析地址映射 (from playerconfig.js)
    PARSE_MAP = {
        "2_": "https://play.mknacg.top:8585/cloudplay/?url=",
        "5_": "https://play.mknacg.top:8585/cloudplay/yp2.php?url=",
        "6_": "https://play.mknacg.top:8585/cloudplay/yp3.php?url=",
        "4_": "https://play.mknacg.top:8585/cloudplay/ccxl.php?url=",
        "3_": "https://play.mknacg.top:8585/muiplayer/?url=",
        "1_": "/",
    }

    # 解密后缀盐值
    DECRYPT_SUFFIX = "Mknacg123321"

    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.session.verify = False
        self._cache = {}
        self._cache_ttl = 300

    def init(self, extend=""):
        pass

    def getName(self):
        return "橘子动漫"

    # ==================== 工具方法 ====================

    def _get(self, url, params=None, headers=None):
        """GET 请求"""
        try:
            resp = self.session.get(url, params=params, timeout=15, headers=headers)
            if resp.status_code == 200:
                return resp.text
            logger.warning(f"GET 请求失败: {resp.status_code} - {url}")
            return None
        except Exception as e:
            logger.error(f"GET 请求异常: {e}")
            return None

    def _post(self, url, data=None, headers=None):
        """POST 请求"""
        try:
            resp = self.session.post(url, data=data, timeout=15, headers=headers)
            if resp.status_code == 200:
                return resp.text
            logger.warning(f"POST 请求失败: {resp.status_code} - {url}")
            return None
        except Exception as e:
            logger.error(f"POST 请求异常: {e}")
            return None

    def _get_json(self, url, params=None, headers=None):
        """GET 请求返回 JSON"""
        text = self._get(url, params=params, headers=headers)
        if text:
            try:
                return json.loads(text)
            except Exception:
                return None
        return None

    def _post_json(self, url, data=None, headers=None):
        """POST 请求返回 JSON"""
        text = self._post(url, data=data, headers=headers)
        if text:
            try:
                return json.loads(text)
            except Exception:
                return None
        return None

    def _fix_pic(self, url):
        """修复图片 URL"""
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return self.BASE_URL + url
        if not url.startswith("http"):
            return self.BASE_URL + "/" + url
        return url

    def _generate_api_key(self, timestamp):
        """生成 API 认证 key: md5('DS' + time + UID)"""
        raw = self.API_SALT + str(timestamp) + self.API_UID
        return hashlib.md5(raw.encode('utf-8')).hexdigest()

    def _aes_decrypt(self, ciphertext_b64, key_string):
        """AES-CBC-Pkcs7 解密"""
        md5_hex = hashlib.md5((key_string + self.DECRYPT_SUFFIX).encode('utf-8')).hexdigest()
        key = md5_hex[16:].encode('utf-8')   # 后16位作为 key
        iv = md5_hex[:16].encode('utf-8')    # 前16位作为 iv

        try:
            ciphertext = base64.b64decode(ciphertext_b64)
        except Exception:
            return ""

        if _AES_BACKEND == "pycryptodome":
            try:
                cipher = _PyCryptoAES.new(key, _PyCryptoAES.MODE_CBC, iv)
                padded = cipher.decrypt(ciphertext)
                result = _pycrypto_unpad(padded, _PyCryptoAES.block_size)
                return result.decode('utf-8')
            except Exception as e:
                logger.error(f"AES 解密失败(pycryptodome): {e}")
                return ""

        elif _AES_BACKEND == "cryptography":
            try:
                cipher = _CryptoCipher(
                    _crypto_algos.AES(key),
                    _crypto_modes.CBC(iv),
                    backend=_crypto_backend()
                )
                decryptor = cipher.decryptor()
                padded = decryptor.update(ciphertext) + decryptor.finalize()
                unpadder = _crypto_padding.PKCS7(128).unpadder()
                result = unpadder.update(padded) + unpadder.finalize()
                return result.decode('utf-8')
            except Exception as e:
                logger.error(f"AES 解密失败(cryptography): {e}")
                return ""

        else:
            logger.error("无可用的 AES 解密库，请安装 pycryptodome 或 cryptography")
            return ""

    # ==================== 首页 ====================

    def homeContent(self, filter=False):
        result = {"class": [], "list": [], "filters": {}}

        # 分类列表
        classes = []
        for cid, cname in self.CATEGORY_MAP.items():
            classes.append({"type_id": cid, "type_name": cname})
        result["class"] = classes

        # 首页推荐：从"动漫"分类获取最新内容
        data = self._fetch_category_api("1", 1, 20)
        if data and "list" in data:
            result["list"] = data["list"][:20]

        return result

    def homeVideoContent(self):
        return self.homeContent()

    # ==================== 分类 ====================

    def _fetch_category_api(self, type_id, page=1, num=40):
        """通过 AJAX API 获取分类内容"""
        timestamp = int(time.time())
        key = self._generate_api_key(timestamp)

        data = {
            "type": str(type_id),
            "page": str(page),
            "num": str(num),
            "time": str(timestamp),
            "key": key,
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{self.BASE_URL}/category/{type_id}-----------/",
        }

        result = self._post_json(f"{self.BASE_URL}/index.php/api/vod", data=data, headers=headers)
        if not result or result.get("code") != 1:
            logger.warning(f"分类 API 返回异常: {result}")
            return None

        # 解析视频列表
        videos = []
        for item in result.get("list", []):
            vod_id = str(item.get("vod_id", ""))
            if not vod_id:
                continue
            pic = self._fix_pic(item.get("vod_pic", ""))
            # 尝试获取更高质量的封面图
            if ".md.jpg" in pic:
                pic = pic.replace(".md.jpg", ".jpg")
            remarks = item.get("vod_remarks", "")
            if not remarks:
                serial = item.get("vod_serial", "")
                if serial:
                    remarks = f"更新至{serial}集"
                else:
                    remarks = "连载中"
            videos.append({
                "vod_id": vod_id,
                "vod_name": item.get("vod_name", ""),
                "vod_pic": pic,
                "vod_remarks": remarks,
            })

        return {
            "list": videos,
            "page": result.get("page", page),
            "pagecount": result.get("pagecount", 1),
            "limit": result.get("limit", num),
            "total": result.get("total", len(videos)),
        }

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = int(pg) if pg else 1
            type_id = str(tid)

            if type_id not in self.CATEGORY_MAP:
                return {"list": [], "page": page, "pagecount": 1, "limit": 20, "total": 0}

            data = self._fetch_category_api(type_id, page, 40)
            if not data:
                return {"list": [], "page": page, "pagecount": 1, "limit": 20, "total": 0}

            return {
                "list": data["list"],
                "page": data["page"],
                "pagecount": data["pagecount"],
                "limit": data["limit"],
                "total": data["total"],
            }
        except Exception as e:
            logger.error(f"获取分类内容失败: {e}")
            return {"list": [], "page": 1, "pagecount": 1, "limit": 20, "total": 0}

    # ==================== 详情 ====================

    def detailContent(self, ids):
        try:
            vod_id = ids[0] if isinstance(ids, list) else str(ids)
            url = f"{self.BASE_URL}/media/{vod_id}/"
            html = self._get(url)
            if not html:
                return {"list": []}

            if BeautifulSoup:
                return self._parse_detail_bs(html, vod_id)
            else:
                return self._parse_detail_regex(html, vod_id)
        except Exception as e:
            logger.error(f"获取详情失败: {e}")
            return {"list": []}

    def _parse_detail_bs(self, html, vod_id):
        """使用 BeautifulSoup 解析详情页"""
        soup = BeautifulSoup(html, 'html.parser')

        # 标题 - 优先从 slide-info-title 获取
        title = ""
        h3 = soup.find('h3', class_='slide-info-title')
        if h3:
            title = h3.get_text(strip=True)
        if not title:
            h1 = soup.find('h1')
            if h1:
                title = h1.get_text(strip=True)
        if not title:
            title_tag = soup.find('a', class_='player-title-link')
            if title_tag:
                title = title_tag.get_text(strip=True)

        # 封面图 - 从 public-list-exp 获取
        poster = ""
        img_box = soup.find('div', class_='public-list-exp')
        if img_box:
            img = img_box.find('img')
            if img:
                poster = img.get('data-src') or img.get('data-original') or img.get('src', '')
        if not poster:
            img_tag = soup.find('img', attrs={'data-src': re.compile(r'pic\.mknacg')})
            if img_tag:
                poster = img_tag.get('data-src', '')
        poster = self._fix_pic(poster)
        if ".md.jpg" in poster:
            poster = poster.replace(".md.jpg", ".jpg")

        # 简介信息
        info = self._parse_info(html)

        # 剧集列表
        play_from_list, play_url_list = self._parse_episodes(html, vod_id)

        vod_item = {
            "vod_id": vod_id,
            "vod_name": title or f"动漫{vod_id}",
            "vod_pic": poster,
            "type_name": info.get("type", "动漫"),
            "vod_year": info.get("year", ""),
            "vod_area": info.get("area", ""),
            "vod_remarks": info.get("state", ""),
            "vod_actor": info.get("actor", ""),
            "vod_director": info.get("director", ""),
            "vod_content": info.get("content", "暂无简介"),
            "vod_play_from": "$$$".join(play_from_list) if play_from_list else "默认线路",
            "vod_play_url": "$$$".join(play_url_list) if play_url_list else "",
        }

        return {"list": [vod_item]}

    def _parse_detail_regex(self, html, vod_id):
        """使用正则解析详情页（无 BeautifulSoup 时的后备方案）"""
        # 标题 - 优先从 slide-info-title 获取
        title = ""
        m = re.search(r'class="slide-info-title[^"]*"[^>]*>(.*?)</h3>', html, re.DOTALL)
        if m:
            title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if not title:
            m = re.search(r'<title>(.*?)</title>', html)
            if m:
                raw = m.group(1).strip()
                title = re.sub(r'动漫高清完整版.*$', '', raw).replace('-橘子动漫', '').strip()
                title = title.strip('《》')
        if not title:
            m = re.search(r'class="player-title-link"[^>]*>(.*?)</a>', html)
            if m:
                title = m.group(1).strip()

        # 封面图
        poster = ""
        m = re.search(r'class="public-list-exp[^"]*"[^>]*>.*?data-src="([^"]+)"', html, re.DOTALL)
        if m:
            poster = m.group(1)
        if not poster:
            m = re.search(r'data-src="(https://pic\.mknacg[^"]+)"', html)
            if m:
                poster = m.group(1)
        poster = self._fix_pic(poster)
        if ".md.jpg" in poster:
            poster = poster.replace(".md.jpg", ".jpg")

        # 简介信息
        info = self._parse_info(html)

        # 剧集列表
        play_from_list, play_url_list = self._parse_episodes(html, vod_id)

        vod_item = {
            "vod_id": vod_id,
            "vod_name": title or f"动漫{vod_id}",
            "vod_pic": poster,
            "type_name": info.get("type", "动漫"),
            "vod_year": info.get("year", ""),
            "vod_area": info.get("area", ""),
            "vod_remarks": info.get("state", ""),
            "vod_actor": info.get("actor", ""),
            "vod_director": info.get("director", ""),
            "vod_content": info.get("content", "暂无简介"),
            "vod_play_from": "$$$".join(play_from_list) if play_from_list else "默认线路",
            "vod_play_url": "$$$".join(play_url_list) if play_url_list else "",
        }

        return {"list": [vod_item]}

    def _parse_info(self, html):
        """解析详情页的元数据"""
        info = {}

        # 年份和地区 - 从 slide-info-remarks 的链接获取
        remarks_links = re.findall(r'slide-info-remarks"><a[^>]*>([^<]+)</a>', html)
        if len(remarks_links) >= 1:
            info["year"] = remarks_links[0].strip()
        if len(remarks_links) >= 2:
            info["area"] = remarks_links[1].strip()

        # 从 slide-info hide div 中提取导演、演员、类型
        for m in re.finditer(r'<div class="slide-info hide">(.*?)</div>', html, re.DOTALL):
            content = m.group(1)
            label_m = re.search(r'<strong[^>]*>([^<]+)</strong>', content)
            if not label_m:
                continue
            label = label_m.group(1).strip().replace(':', '').replace('：', '').strip()
            links = re.findall(r'>([^<]+)</a>', content)
            links = [l.strip() for l in links if l.strip()]

            if label == '导演':
                info["director"] = " ".join(links[:5])
            elif label in ('演员', '主演'):
                if "actor" not in info:
                    info["actor"] = " ".join(links[:10])
            elif label == '类型':
                info["type"] = " ".join(links)

        # 状态 - 从 <em>状态：</em><span>...</span> 获取
        m = re.search(r'状态[：:]\s*</em>\s*<span[^>]*>([^<]+)</span>', html)
        if m:
            info["state"] = m.group(1).strip()
        else:
            m = re.search(r'状态[：:]\s*</span>\s*([^<]+)<', html)
            if m:
                info["state"] = m.group(1).strip()

        # 简介 - 从 meta description 获取
        m = re.search(r'<meta name="description" content="([^"]+)"', html)
        if m:
            desc = m.group(1).strip()
            # 去掉 "标题剧情介绍：" 前缀
            desc = re.sub(r'^.*?剧情介绍[：:]\s*', '', desc)
            info["content"] = desc
        if "content" not in info:
            m = re.search(r'剧情介绍[：:]\s*(.*?)(?:<|"|$)', html, re.DOTALL)
            if m:
                info["content"] = m.group(1).strip()

        return info

    def _parse_episodes(self, html, vod_id):
        """解析剧集列表，返回 (play_from_list, play_url_list)"""
        play_from_list = []
        play_url_list = []

        # 提取线路名称（从 tab 标签）
        line_names = []
        tab_section = re.search(r'anthology-tab.*?swiper-wrapper.*?</div>', html, re.DOTALL)
        if tab_section:
            tabs = re.findall(r'<a[^>]*class="vod-playerUrl[^"]*"[^>]*>(.*?)</a>', tab_section.group(), re.DOTALL)
            for tab in tabs:
                # 提取文本内容，去掉 HTML 标签
                name = re.sub(r'<[^>]+>', '', tab).strip().replace('&nbsp;', '')
                name = re.sub(r'\d+$', '', name)  # 去掉末尾的集数
                if name:
                    line_names.append(name)

        # 提取所有剧集链接
        all_eps = re.findall(r'href="(/bangumi/\d+-\d+-\d+/)"[^>]*>([^<]+)</a>', html)
        if not all_eps:
            # 备用正则：有些页面格式略有不同
            all_eps = re.findall(r'href="(/bangumi/\d+-\d+-\d+/)"[^>]*>\s*<span>([^<]+)</span>', html)

        # 按线路分组
        from collections import OrderedDict
        groups = OrderedDict()
        for url, name in all_eps:
            parts = url.split('-')
            if len(parts) >= 3:
                line_num = parts[1]
            else:
                line_num = "default"
            if line_num not in groups:
                groups[line_num] = []
            full_url = self.BASE_URL + url if url.startswith('/') else url
            groups[line_num].append(f"{name}${full_url}")

        # 如果没有找到线路名称，使用默认名称
        if not line_names:
            line_names = [f"线路{chr(65 + i)}" for i in range(len(groups))]

        # 组装结果
        for i, (line_num, eps) in enumerate(groups.items()):
            name = line_names[i] if i < len(line_names) else f"线路{line_num}"
            play_from_list.append(name)
            play_url_list.append("#".join(eps))

        return play_from_list, play_url_list

    # ==================== 播放 ====================

    def playerContent(self, flag, id, vipFlags):
        try:
            play_url = urllib.parse.unquote(id) if id else ''

            # 如果是 bangumi 播放页 URL，需要解析获取真实视频地址
            if '/bangumi/' in play_url or '/media/' in play_url:
                video_url = self._resolve_play_url(play_url)
                if video_url:
                    return {
                        "parse": 0,
                        "playUrl": "",
                        "url": video_url,
                        "header": json.dumps({
                            "User-Agent": self.HEADERS["User-Agent"],
                            "Referer": "https://play.mknacg.top:8585/",
                        }),
                    }
                # 解析失败，降级为嗅探模式
                return {
                    "parse": 1,
                    "playUrl": "",
                    "url": play_url,
                    "header": json.dumps({
                        "User-Agent": self.HEADERS["User-Agent"],
                        "Referer": self.BASE_URL + "/",
                    }),
                }

            # 如果已经是直接视频地址
            return {
                "parse": 0,
                "playUrl": "",
                "url": play_url,
                "header": json.dumps({
                    "User-Agent": self.HEADERS["User-Agent"],
                    "Referer": self.BASE_URL + "/",
                }),
            }
        except Exception as e:
            logger.error(f"解析播放失败: {e}")
            return {"parse": 0, "playUrl": "", "url": ""}

    def _resolve_play_url(self, bangumi_url):
        """从 bangumi 播放页解析真实视频地址"""
        try:
            # Step 1: 获取播放页，提取 player_aaaa
            html = self._get(bangumi_url)
            if not html:
                return None

            m = re.search(r'var player_aaaa=(\{.*?\})\s*</script>', html)
            if not m:
                logger.error("未找到 player_aaaa 变量")
                return None

            player_data = json.loads(m.group(1))
            enc_url = player_data.get("url", "")
            line_from = player_data.get("from", "")

            if not enc_url or not line_from:
                logger.error("player_aaaa 缺少 url 或 from 字段")
                return None

            # 本地线路（已下线）直接返回
            if line_from == "1_":
                logger.warning("本地线路已下线")
                return None

            # Step 2: 获取解析页
            parse_url = self.PARSE_MAP.get(line_from)
            if not parse_url or parse_url == "/":
                logger.error(f"未知线路: {line_from}")
                return None

            full_parse_url = parse_url + enc_url  # enc_url 已是 URL 编码
            parse_html = self._get(full_parse_url, headers={"Referer": bangumi_url})
            if not parse_html:
                return None

            # Step 3: 提取 meta 标签 ID 和 config.url
            vm = re.search(r'<meta name="viewport"[^>]*id="now_([^"]+)"', parse_html)
            cm = re.search(r'<meta charset="UTF-8"[^>]*id="now_([^"]+)"', parse_html)
            if not vm or not cm:
                logger.error("未找到 meta 标签 ID")
                return None

            viewport_id = vm.group(1)
            charset_id = cm.group(1)

            cm2 = re.search(r'"url"\s*:\s*"([^"]+)"', parse_html)
            if not cm2:
                logger.error("未找到 config.url")
                return None

            config_url = cm2.group(1)

            # Step 4: 构建密钥字符串并解密
            pairs = []
            for i in range(len(charset_id)):
                if i < len(viewport_id):
                    pairs.append({'id': charset_id[i], 'text': viewport_id[i]})
            pairs.sort(key=lambda x: int(x['id']))
            key_string = ''.join(p['text'] for p in pairs)

            video_url = self._aes_decrypt(config_url, key_string)
            if not video_url:
                logger.error("AES 解密失败")
                return None

            logger.info(f"解析成功: {video_url[:80]}...")
            return video_url

        except Exception as e:
            logger.error(f"解析播放地址失败: {e}")
            return None

    # ==================== 搜索 ====================

    def searchContent(self, key, quick, pg="1"):
        try:
            page = int(pg) if pg else 1
            encoded_key = urllib.parse.quote(key)

            # 使用 MacCMS suggest 接口
            url = f"{self.BASE_URL}/index.php/ajax/suggest"
            data = self._get_json(url, params={"mid": "1", "wd": key, "limit": "20"})

            if data and data.get("code") == 1:
                videos = []
                for item in data.get("list", []):
                    vod_id = str(item.get("id", ""))
                    if not vod_id:
                        continue
                    pic = self._fix_pic(item.get("pic", ""))
                    if ".md.jpg" in pic:
                        pic = pic.replace(".md.jpg", ".jpg")
                    videos.append({
                        "vod_id": vod_id,
                        "vod_name": item.get("name", ""),
                        "vod_pic": pic,
                        "vod_remarks": "动漫",
                    })
                return {
                    "list": videos,
                    "page": page,
                    "pagecount": 1,
                    "limit": len(videos),
                    "total": len(videos),
                }

            # 降级：HTML 搜索
            search_url = f"{self.BASE_URL}/search/{encoded_key}/"
            html = self._get(search_url)
            if html:
                videos = self._parse_search_html(html)
                if videos:
                    return {
                        "list": videos,
                        "page": page,
                        "pagecount": 1,
                        "limit": len(videos),
                        "total": len(videos),
                    }

            return {"list": [], "page": page, "pagecount": 1, "limit": 20, "total": 0}
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return {"list": [], "page": 1, "pagecount": 1, "limit": 20, "total": 0}

    def _parse_search_html(self, html):
        """从 HTML 搜索结果页解析视频列表"""
        videos = []
        if BeautifulSoup:
            soup = BeautifulSoup(html, 'html.parser')
            for link in soup.find_all('a', href=re.compile(r'/media/\d+/')):
                href = link.get('href', '')
                m = re.search(r'/media/(\d+)/', href)
                if not m:
                    continue
                vod_id = m.group(1)
                title = link.get('title', '') or link.get_text(strip=True)
                if not title:
                    continue
                pic = ''
                img = link.find('img')
                if img:
                    pic = img.get('data-src') or img.get('data-original') or img.get('src', '')
                pic = self._fix_pic(pic)
                videos.append({
                    "vod_id": vod_id,
                    "vod_name": title,
                    "vod_pic": pic,
                    "vod_remarks": "动漫",
                })
        else:
            # 正则备用
            items = re.findall(r'href="(/media/(\d+)/)"[^>]*title="([^"]*)"', html)
            seen = set()
            for href, vod_id, title in items:
                if vod_id in seen or not title:
                    continue
                seen.add(vod_id)
                videos.append({
                    "vod_id": vod_id,
                    "vod_name": title,
                    "vod_pic": "",
                    "vod_remarks": "动漫",
                })
        return videos

    # ==================== 其他 ====================

    def isVideoFormat(self, url):
        video_formats = ['.m3u8', '.mp4', '.ts', '.flv', '.avi', '.mkv', '.mknvideo']
        return any(url.lower().endswith(fmt) for fmt in video_formats)

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass

    def localProxy(self, params):
        pass


if __name__ == "__main__":
    spider = Spider()

    print("=" * 60)
    print("测试首页推荐")
    print("=" * 60)
    home = spider.homeContent()
    print(f"分类数: {len(home.get('class', []))}")
    print(f"推荐数: {len(home.get('list', []))}")
    for item in home.get('list', [])[:3]:
        print(f"  - {item['vod_name']} ({item['vod_remarks']})")

    print("\n" + "=" * 60)
    print("测试分类内容 (动漫 第1页)")
    print("=" * 60)
    cat = spider.categoryContent("1", 1, {}, {})
    print(f"总数: {cat.get('total', 0)}, 页数: {cat.get('pagecount', 0)}")
    print(f"本页: {len(cat.get('list', []))} 条")
    for item in cat.get('list', [])[:3]:
        print(f"  - {item['vod_name']} ({item['vod_remarks']})")

    print("\n" + "=" * 60)
    print("测试搜索 (罪恶王冠)")
    print("=" * 60)
    search = spider.searchContent("罪恶王冠", False)
    print(f"结果: {len(search.get('list', []))} 条")
    for item in search.get('list', [])[:3]:
        print(f"  - {item['vod_name']} (ID: {item['vod_id']})")

    print("\n" + "=" * 60)
    print("测试详情 (ID: 1597)")
    print("=" * 60)
    detail = spider.detailContent(["1597"])
    if detail.get('list'):
        vod = detail['list'][0]
        print(f"标题: {vod['vod_name']}")
        print(f"年份: {vod['vod_year']}")
        print(f"地区: {vod['vod_area']}")
        print(f"类型: {vod['type_name']}")
        print(f"导演: {vod['vod_director']}")
        print(f"线路: {vod['vod_play_from']}")
        play_urls = vod['vod_play_url'].split('$$$')
        for i, pu in enumerate(play_urls):
            eps = pu.split('#')
            print(f"  线路{i+1}: {len(eps)} 集, 第一集: {eps[0][:60]}...")

    print("\n" + "=" * 60)
    print("测试播放解析 (bangumi/1597-2-1/)")
    print("=" * 60)
    result = spider.playerContent("云端线路", urllib.parse.quote("https://www.mgnacg.com/bangumi/1597-2-1/"), [])
    print(f"parse: {result.get('parse')}")
    print(f"url: {result.get('url', '')[:100]}...")
