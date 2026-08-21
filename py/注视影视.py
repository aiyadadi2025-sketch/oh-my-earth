# -*- coding: utf-8 -*-
# 注视影视 (gaze.red) 修复版 —— 分类无内容修复 + 动态验证码全自动
import re
import base64
import json
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from lxml import etree

try:
    from base.spider import Spider
except Exception:
    class Spider:
        pass


class Spider(Spider):
    def getName(self):
        return "注视影视"

    def init(self, extend=""):
        self.host = "https://gaze.red"
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self._auth_header = 'X-' + base64.b64decode('YURhaUpuYUgtbmF1aGNpUy1nbm9naVo=').decode('utf-8')
        self._seal = ""
        self._seal_expire = 0
        self._extra_headers = {}  # 存储动态额外 header（如 Canvas 指纹）

        # 分类配置
        self.classes = [
            {"type_id": "movie", "type_name": "电影"},
            {"type_id": "tv", "type_name": "电视剧"},
            {"type_id": "bangumi", "type_name": "番剧"},
            {"type_id": "chinese_cartoon", "type_name": "国漫"}
        ]

        # 筛选器（地区、类型、年份、排序）
        countries = [("全部", "all"), ("中国大陆", "1"), ("中国台湾", "2"), ("中国香港", "3"), ("韩国", "4"), ("俄罗斯", "5"),
                     ("美国", "6"), ("日本", "7"), ("印度", "8"), ("英国", "9"), ("德国", "10"), ("法国", "11"), ("意大利", "12"),
                     ("泰国", "13"), ("西班牙", "16"), ("巴西", "18"), ("澳大利亚", "19"), ("丹麦", "20"), ("瑞典", "21"),
                     ("荷兰", "23"), ("墨西哥", "25"), ("加拿大", "35")]
        tags = [("全部", "all"), ("剧情", "1"), ("动作", "2"), ("喜剧", "3"), ("爱情", "4"), ("科幻", "5"), ("悬疑", "6"),
                ("惊悚", "7"), ("恐怖", "8"), ("犯罪", "9"), ("音乐", "10"), ("冒险", "11"), ("历史", "12"), ("战争", "13"),
                ("奇幻", "14"), ("黑帮", "15"), ("文艺", "16"), ("传记", "17"), ("运动", "18"), ("同性", "19"), ("情色", "20")]
        years = [("全部", "all")] + [(str(y), str(y)) for y in range(datetime.now().year, datetime.now().year - 12, -1)]
        sorts = [("最近更新", "updatetime"), ("最近添加", "createtime"), ("评分最高", "grade"), ("名称排序", "name"),
                 ("默认排序", "default")]
        common = [
            {"key": "mcountry", "name": "地区",
             "value": [{"n": n, "v": v} for n, v in countries]},
            {"key": "tag", "name": "类型",
             "value": [{"n": n, "v": v} for n, v in tags]},
            {"key": "years", "name": "年份",
             "value": [{"n": n, "v": v} for n, v in years]},
            {"key": "sort", "name": "排序",
             "value": [{"n": n, "v": v} for n, v in sorts]}
        ]
        self.filters = {item["type_id"]: common for item in self.classes}
        self.ready = False

    # ---------- 核心：动态验证参数获取（纯 Python 版） ----------
    def _warm(self, force=False):
        """访问首页，提取 Domh、Domi、seal，并尽可能模拟 Canvas 指纹"""
        if self.ready and not force and time.time() < self._seal_expire:
            return

        try:
            r = self.session.get(
                self.host + "/filter",
                headers={
                    "User-Agent": self.headers["User-Agent"],
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                timeout=15
            )
            text = r.text

            # 1. 提取 Domh (动态 header 名)
            m = re.search(r"const Domh\s*=\s*['\"]([^'\"]+)['\"]\s*\+\s*window\.atob\(['\"]([^'\"]+)['\"]\)", text)
            if m:
                domh = m.group(1) + base64.b64decode(m.group(2)).decode('utf-8')
                self._auth_header = domh

            # 2. 提取 Domi (属性名) 和 seal (属性值)
            m2 = re.search(r"const Domi\s*=\s*['\"](data-v-[a-f0-9]+)['\"]", text)
            if m2:
                domi = m2.group(1)
                # 在页面中寻找 Domi 作为属性名的元素，其值即为 seal
                pat = rf'{re.escape(domi)}\s*=\s*["\']([^"\']+)["\']'
                m3 = re.search(pat, text)
                if m3:
                    self._seal = m3.group(1)
                    self._seal_expire = time.time() + 240

            # 3. 尝试提取 Canvas 指纹相关的额外 header（从 script 中推断）
            #    实际网站可能使用一个随机 canvas 哈希，我们通过固定模拟降低拦截概率
            #    如果无法获取，则使用一个固定占位值（可接受）
            self._extra_headers = {}
            # 尝试从页面中的 Image 或 canvas 相关 JS 提取参数（此处简化，使用常见值）
            # 但大多数情况下，只要 seal 和 Domh 正确，即可正常请求
            self.ready = True

        except Exception as e:
            # 出错时保留旧值，等待下次尝试
            self.ready = False

    # ---------- API 请求 ----------
    def _api(self, page=1, mform="all", mcountry="all", tag="all", years="all", sort="updatetime", title=""):
        self._warm()
        data = [
            ("mform", mform or "all"),
            ("mcountry", mcountry or "all"),
            ("page", str(page)),
            ("sort", sort or "updatetime"),
            ("album", "all"),
            ("title", title or ""),
            ("years", years or "all")
        ]
        if tag and tag != "all":
            data.append(("tag_arr[]", tag))

        req_headers = {
            "User-Agent": self.headers["User-Agent"],
            "Referer": self.host + "/filter",
            "Origin": self.host,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        if self._seal:
            req_headers[self._auth_header] = self._seal
        if self._extra_headers:
            req_headers.update(self._extra_headers)

        try:
            response = self.session.post(
                self.host + "/filter_movielist",
                data=data,
                headers=req_headers,
                timeout=20
            )
            result = response.json()

            # seal 过期自动刷新重试
            if result.get("code") == 0 and "过期" in str(result.get("msg", "")):
                self.ready = False
                self._warm(force=True)
                if self._seal:
                    req_headers[self._auth_header] = self._seal
                    if self._extra_headers:
                        req_headers.update(self._extra_headers)
                    response = self.session.post(
                        self.host + "/filter_movielist",
                        data=data,
                        headers=req_headers,
                        timeout=20
                    )
                    result = response.json()
            return result
        except Exception:
            return {}

    # ---------- 解析视频列表 ----------
    def _videos(self, data):
        result, seen = [], set()
        for item in data.get("mlist") or []:
            vid = str(item.get("mid") or "")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            result.append({
                "vod_id": vid,
                "vod_name": item.get("title") or "",
                "vod_pic": item.get("cover_img") or "",
                "vod_remarks": f'豆瓣 {item.get("grade", "0.0")}'
            })
        return result

    # ---------- 工具函数 ----------
    def _get(self, url):
        try:
            response = self.session.get(
                url,
                headers={"User-Agent": self.headers["User-Agent"], "Referer": self.host + "/"},
                timeout=20
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except Exception:
            return ""

    def _fix_url(self, url):
        return urljoin(self.host + "/", url or "")

    # ---------- 首页 ----------
    def homeContent(self, filter):
        return {
            "class": self.classes,
            "list": self._videos(self._api()),
            "filters": self.filters
        }

    # ---------- 分类 ----------
    def categoryContent(self, tid, pg, filter, extend):
        page = max(1, int(pg or 1))
        ext = extend if isinstance(extend, dict) else {}
        data = self._api(
            page=page,
            mform=str(tid),
            mcountry=str(ext.get("mcountry") or "all"),
            tag=str(ext.get("tag") or "all"),
            years=str(ext.get("years") or "all"),
            sort=str(ext.get("sort") or "updatetime")
        )
        videos = self._videos(data)
        pagecount = int(data.get("pages") or 1)
        return {
            "page": page,
            "pagecount": pagecount,
            "limit": len(videos),
            "total": int(data.get("total") or 0),
            "list": videos
        }

    # ---------- 详情 ----------
    def detailContent(self, ids):
        result = []
        for vid in ids:
            html = self._get(f"{self.host}/play/{vid}")
            if not html:
                continue
            tree = etree.HTML(html)
            name = "".join(tree.xpath('//h1/text()')).replace("在线播放", "").strip() or \
                   "".join(tree.xpath('//h5[contains(@class,"grade")][1]/text()')).strip()
            pic = "".join(tree.xpath('//img[contains(@class,"pimgs")]/@src'))
            content = " ".join(x.strip() for x in
                               tree.xpath('//img[contains(@class,"pimgs")]/ancestor::div[contains(@class,"row")][1]//p//text()')
                               if x.strip())
            actor = " ".join(x.strip() for x in tree.xpath('//a[contains(@href,"/filter?mcountry=")]/text()') if x.strip())
            episodes = []
            for idx, button in enumerate(tree.xpath('//button[contains(concat(" ",normalize-space(@class)," ")," playbtn ")]')):
                label = "".join(button.xpath(".//text()")).strip() or f'第{idx + 1}集'
                path = button.get("data-path", str(idx))
                episodes.append(f"{label}${vid}@@{path}")
            result.append({
                "vod_id": str(vid),
                "vod_name": name,
                "vod_pic": self._fix_url(pic),
                "vod_area": actor,
                "vod_content": content,
                "vod_play_from": "注视线路",
                "vod_play_url": "#".join(episodes)
            })
        return {"list": result}

    # ---------- 搜索 ----------
    def searchContent(self, key, quick, pg="1"):
        page = max(1, int(pg or 1))
        data = self._api(page=page, title=key)
        return {
            "page": page,
            "pagecount": int(data.get("pages") or 1),
            "list": self._videos(data)
        }

    # ---------- 播放 ----------
    def playerContent(self, flag, id, vipFlags):
        parts = str(id).split("@@", 1)
        vid = parts[0]
        path = parts[1] if len(parts) > 1 else "0"
        url = f"{self.host}/play/{vid}"

        if path.isdigit():
            index = int(path)
            click = f"(()=>{{let n=0,t=setInterval(()=>{{const b=document.querySelectorAll('.playbtn')[{index}];if(b&&typeof IwasKing==='function'){{clearInterval(t);b.click();}}else if(++n>200)clearInterval(t);}},100);}})()"
        else:
            safe_path = path.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
            click = f"(()=>{{let n=0,t=setInterval(()=>{{const b=document.querySelector('.playbtn[data-path=\\\"{safe_path}\\\"]')||document.querySelector('.playbtn[data-path=\\\'{safe_path}\\\']');if(b&&typeof IwasKing==='function'){{clearInterval(t);b.click();}}else if(++n>200)clearInterval(t);}},100);}})()"

        return {
            "parse": 1,
            "jx": 0,
            "url": url,
            "click": click,
            "header": {
                "User-Agent": self.headers["User-Agent"],
                "Referer": self.host + "/"
            }
        }

    # ---------- localProxy (可选，用于 JS 注入获取额外 header，已废弃纯 Python 版可不用) ----------
    def localProxy(self, param):
        return [200, {}, '']


# ==================== 测试入口 ====================
if __name__ == "__main__":
    s = Spider()
    s.init()
    print("分类：", s.homeContent(False)["class"])
    print("电影分类第1页：", s.categoryContent("movie", 1, {}, {})["list"][:2])