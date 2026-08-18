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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    
    def homeContent(self, filter):
        result = {}
        classes = [
            {"type_id": "1", "type_name": "电影"},
            {"type_id": "2", "type_name": "电视剧"},
            {"type_id": "3", "type_name": "综艺"},
            {"type_id": "4", "type_name": "动漫"}
        ]
        result['class'] = classes
        return result
    
    def categoryContent(self, tid, pg, filter, extend):
        result = {}
        videos = []
        url = f"{self.HOST}/vodtype/{tid}/page/{pg}.html"
        html = self.fetch(url, headers=self.headers).text
        items = re.findall(r'<a class="fed-list-pics.*?href="/voddetail/(\d+)/".*?data-original="(.*?)"[^>]*>.*?<a class="fed-list-title.*?>(.*?)</a>', html, re.S)
        for item in items:
            videos.append({"vod_id": item[0], "vod_name": item[2], "vod_pic": item[1]})
        result['list'] = videos
        return result
    
    def detailContent(self, ids):
        result = {}
        url = f"{self.HOST}/voddetail/{ids[0]}/"
        html = self.fetch(url, headers=self.headers).text
        title = re.search(r'<h1.*?>(.*?)</h1>', html, re.S)
        pic = re.search(r'data-original="(.*?)"', html, re.S)
        episodes = re.findall(r'id="jsvidf_(\d+)".*?data-mp4-url="(.*?)"', html, re.S)
        play_list = []
        for ep in episodes:
            try:
                decoded = base64.b64decode(ep[1]).decode('utf-8')
                play_list.append(f"第{ep[0]}集${decoded}")
            except:
                play_list.append(f"第{ep[0]}集${ep[1]}")
        vod = {
            "vod_id": ids[0],
            "vod_name": title.group(1) if title else ids[0],
            "vod_pic": pic.group(1) if pic else "",
            "vod_play_from": "来看片",
            "vod_play_url": "#".join(play_list)
        }
        result['list'] = [vod]
        return result
    
    def searchContent(self, key, quick="", pg="1"):
        result = {}
        videos = []
        ts = int(time.time() * 1000)
        url = f"https://kpdata.flixfiend.top/esearch/index?kw={key}&ts={ts}"
        html = self.fetch(url, headers=self.headers).text
        match = re.search(r'callback\((.*?)\)', html, re.S)
        if match:
            data = json.loads(match.group(1))
            if data.get('code') == 1:
                js_data = json.loads(base64.b64decode(data['js']))
                for item in js_data.get('data', []):
                    videos.append({
                        "vod_id": str(item.get('id', '')),
                        "vod_name": item.get('high', {}).get('vod_name', ''),
                        "vod_pic": item.get('data', {}).get('vod_pic', '')
                    })
        result['list'] = videos
        return result
    
    def playerContent(self, flag, id, vipFlags):
        result = {}
        result["parse"] = 0
        result["url"] = id
        result["header"] = {"User-Agent": "Mozilla/5.0"}
        return result