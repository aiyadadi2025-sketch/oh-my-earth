# -*- coding: utf-8 -*-
import re
import json
import base64
import time
from base.spider import Spider

class Spider(Spider):
    def getName(self):
        return "看片狂人"
    
    def init(self, extend=""):
        self.HOST = "https://www.kpkuang.fyi"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
        }
    
    def homeContent(self, filter):
        result = {}
        classes = [
            {"type_id": "1", "type_name": "电影"},
            {"type_id": "2", "type_name": "电视剧"},
            {"type_id": "3", "type_name": "综艺"},
            {"type_id": "4", "type_name": "动漫"},
            {"type_id": "13", "type_name": "华语"},
            {"type_id": "14", "type_name": "欧美"},
            {"type_id": "15", "type_name": "韩剧"},
            {"type_id": "16", "type_name": "日剧"},
            {"type_id": "26", "type_name": "短剧"}
        ]
        result['class'] = classes
        return result
    
    def homeVideoContent(self):
        result = {}
        videos = []
        try:
            html = self.fetch(self.HOST, headers=self.headers).text
            items = re.findall(r'<a class="fed-list-pics.*?href="/voddetail/(\d+)/".*?data-original="(.*?)"[^>]*>.*?<a class="fed-list-title.*?>(.*?)</a>', html, re.S)
            for item in items[:20]:
                video_id = item[0]
                pic = item[1] if item[1] else ""
                title = item[2] if item[2] else ""
                videos.append({
                    "vod_id": video_id,
                    "vod_name": title,
                    "vod_pic": pic,
                    "vod_remarks": ""
                })
        except Exception as e:
            pass
        result['list'] = videos
        return result
    
    def categoryContent(self, tid, pg, filter, extend):
        result = {}
        videos = []
        try:
            url = f"{self.HOST}/vodtype/{tid}/page/{pg}.html"
            html = self.fetch(url, headers=self.headers).text
            items = re.findall(r'<a class="fed-list-pics.*?href="/voddetail/(\d+)/".*?data-original="(.*?)"[^>]*>.*?<a class="fed-list-title.*?>(.*?)</a>', html, re.S)
            for item in items:
                video_id = item[0]
                pic = item[1] if item[1] else ""
                title = item[2] if item[2] else ""
                videos.append({
                    "vod_id": video_id,
                    "vod_name": title,
                    "vod_pic": pic,
                    "vod_remarks": ""
                })
            result['list'] = videos
            result['page'] = pg
            result['pagecount'] = 999
            result['limit'] = 20
            result['total'] = 999
        except Exception as e:
            pass
        return result
    
    def detailContent(self, ids):
        result = {}
        try:
            url = f"{self.HOST}/voddetail/{ids[0]}/"
            html = self.fetch(url, headers=self.headers).text
            
            # 提取基本信息
            name = re.search(r'<h1 class="fed-part-eb.*?>(.*?)</h1>', html, re.S)
            title = name.group(1) if name else ids[0]
            
            pic = re.search(r'<a class="fed-list-pics.*?data-original="(.*?)"', html, re.S)
            pic_url = pic.group(1) if pic else ""
            
            desc = re.search(r'<div class="fed-part-eb.*?>(.*?)</div>', html, re.S)
            content = desc.group(1) if desc else ""
            
            # 提取年份和地区
            year = re.search(r'年份：</span>(.*?)</a>', html, re.S)
            year = year.group(1).strip() if year else ""
            
            area = re.search(r'地区：</span>(.*?)</a>', html, re.S)
            area = area.group(1).strip() if area else ""
            
            # 提取播放列表
            episodes = []
            ep_matches = re.findall(r'id="jsvidf_(\d+)".*?data-mp4-url="(.*?)"', html, re.S)
            for ep in ep_matches:
                ep_id = ep[0]
                play_url = ep[1]
                try:
                    # Base64解码播放地址
                    decoded = base64.b64decode(play_url).decode('utf-8')
                    episodes.append(f"第{ep_id}集${decoded}")
                except:
                    episodes.append(f"第{ep_id}集${play_url}")
            
            vod_play_from = "来看片"
            vod_play_url = "#".join(episodes)
            
            vod = {
                "vod_id": ids[0],
                "vod_name": title,
                "vod_pic": pic_url,
                "type_name": "",
                "vod_year": "",
                "vod_area": area,
                "vod_remarks": "",
                "vod_actor": "",
                "vod_director": "",
                "vod_content": content,
                "vod_play_from": vod_play_from,
                "vod_play_url": vod_play_url
            }
            result['list'] = [vod]
        except Exception as e:
            pass
        return result
    
    def searchContent(self, key, quick="", pg="1"):
        result = {}
        videos = []
        try:
            ts = int(time.time() * 1000)
            search_url = f"https://kpdata.flixfiend.top/esearch/index?kw={key}&ts={ts}"
            html = self.fetch(search_url, headers=self.headers).text
            
            # 解析JSONP响应
            match = re.search(r'callback\((.*?)\)', html, re.S)
            if match:
                data = json.loads(match.group(1))
                if data.get('code') == 1:
                    js_data = json.loads(base64.b64decode(data['js']))
                    for item in js_data.get('data', []):
                        video_id = str(item.get('id', ''))
                        title = item.get('high', {}).get('vod_name', '') or item.get('data', {}).get('vod_name', '')
                        pic = item.get('data', {}).get('vod_pic', '')
                        year = item.get('data', {}).get('vod_year', '')
                        
                        videos.append({
                            "vod_id": video_id,
                            "vod_name": title,
                            "vod_pic": pic,
                            "vod_year": year,
                            "vod_remarks": ""
                        })
        except Exception as e:
            pass
        result['list'] = videos
        result['page'] = pg
        result['pagecount'] = 1
        result['limit'] = 20
        result['total'] = len(videos)
        return result
    
    def playerContent(self, flag, id, vipFlags):
        result = {}
        result["parse"] = 0
        result["url"] = id
        result["header"] = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        return result
    
    def localProxy(self, param):
        return [200, "video/mp4", "", None]