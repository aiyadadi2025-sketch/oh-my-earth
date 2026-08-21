# -*- coding: utf-8 -*-
# 注视影视 (gaze.red) 修复版 —— 使用 Node.js 计算 Canvas 指纹
import re
import base64
import json
import time
import subprocess
import os
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
        self._auth_header = 'X-' + base64.b64decode('YURhaUpuYUgtbmF1Y2hpUy1nbm9naVo=').decode('utf-8')
        self._seal = ""
        self._seal_expire = 0
        self._fp_header_name = ""
        self._fp_header_value = ""

        # 分类配置
        self.classes = [
            {"type_id": "movie", "type_name": "电影"},
            {"type_id": "tv", "type_name": "电视剧"},
            {"type_id": "bangumi", "type_name": "番剧"},
            {"type_id": "chinese_cartoon", "type_name": "国漫"}
        ]

        # 筛选器
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
            {"key": "mcountry", "name": "地区", "value": [{"n": n, "v": v} for n, v in countries]},
            {"key": "tag", "name": "类型", "value": [{"n": n, "v": v} for n, v in tags]},
            {"key": "years", "name": "年份", "value": [{"n": n, "v": v} for n, v in years]},
            {"key": "sort", "name": "排序", "value": [{"n": n, "v": v} for n, v in sorts]}
        ]
        self.filters = {item["type_id"]: common for item in self.classes}
        self.ready = False

    def _warm(self, force=False):
        """访问首页，提取验证参数并计算 Canvas 指纹"""
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

            # 1. 提取 seal (时间戳认证)
            m = re.search(r'(\w+)\s*=\s*"(\d+\.\w+\.\w+)"', text)
            if m:
                self._seal = m.group(2)
                self._seal_expire = time.time() + 240

            # 2. 提取 header 名
            m = re.search(r'const\s+\w+=atob\("([^"]+)"\),\w+=atob\("([^"]+)"\)', text)
            if m:
                b1 = base64.b64decode(m.group(1))
                b2 = base64.b64decode(m.group(2))
                self._auth_header = ''.join(chr(b1[i] ^ b2[i]) for i in range(len(b1)))

            # 3. 计算 Canvas 指纹
            self._compute_fingerprint(text)

            self.ready = True

        except Exception as e:
            self.ready = False

    def _compute_fingerprint(self, html):
        """使用 Node.js 计算 Canvas 指纹"""
        try:
            # 提取 BMP 图片
            img_match = re.search(r'src="data:image/bmp;base64,([A-Za-z0-9+/=]+)"', html)
            if not img_match:
                return

            img_b64 = img_match.group(1)
            img_data = base64.b64decode(img_b64)

            # 提取 canvas 指纹计算函数
            func_match = re.search(r'function\s+\w+\(\)\{[\s\S]*?_HAkBgIvX3Niw=', html)
            if not func_match:
                return

            func_start = html.find('function ')
            if func_start < 0:
                return

            # 找到函数结束
            depth = 0
            for i, c in enumerate(html[func_start:]):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        func = html[func_start:func_start + i + 1]
                        break
            else:
                return

            # 创建 Node.js 脚本
            script = f'''
const fs = require('fs');

// BMP 数据
const bmpData = Buffer.from('{img_b64}', 'base64');

// 像素数据 (10x10 RGBA)
const imageData = new Uint8Array(400);
const offset = 54;
const rowSize = 32;
for (let y = 0; y < 10; y++) {{
    for (let x = 0; x < 10; x++) {{
        const dstIdx = (y * 10 + x) * 4;
        const srcIdx = offset + (9 - y) * rowSize + x * 3;
        imageData[dstIdx] = bmpData[srcIdx];
        imageData[dstIdx + 1] = bmpData[srcIdx + 1];
        imageData[dstIdx + 2] = bmpData[srcIdx + 2];
        imageData[dstIdx + 3] = 255;
    }}
}}

// atob 解码
function atob(str) {{
    return Uint8Array.from(Buffer.from(str, 'base64'));
}}

// 执行指纹计算函数
{func}

// 输出结果
console.log(JSON.stringify({{_HAkBgIvX3Niw}}));
'''

            # 写入临时文件
            script_path = os.path.join(os.environ.get('TEMP', '/tmp'), 'gaze_fp.js')
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(script)

            # 运行 Node.js
            result = subprocess.run(
                ['node', script_path],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                try:
                    fp_data = json.loads(result.stdout.strip())
                    key = list(fp_data.keys())[0]
                    self._fp_header_name = fp_data[key].get('13930^13930' if '13930' in str(fp_data[key]) else list(fp_data[key].keys())[0], '')
                    self._fp_header_value = fp_data[key].get('41039^41038' if '41039' in str(fp_data[key]) else list(fp_data[key].values())[0], '')

                    # 简化处理：直接解析 JSON
                    if isinstance(fp_data, dict):
                        for k, v in fp_data.items():
                            if isinstance(v, dict):
                                for kk, vv in v.items():
                                    if isinstance(kk, int) and isinstance(vv, str):
                                        self._fp_header_name = vv
                                    elif isinstance(vv, str) and len(vv) > 10:
                                        self._fp_header_value = vv
                except:
                    pass

            # 清理临时文件
            try:
                os.remove(script_path)
            except:
                pass

        except Exception as e:
            pass

    def _api(self, page=1, mform="all", mcountry="all", tag="all", years="all", sort="updatetime", title=""):
        self._warm()
        data = {
            "mform": mform or "all",
            "mcountry": mcountry or "all",
            "page": str(page),
            "sort": sort or "updatetime",
            "album": "all",
            "title": title or "",
            "years": years or "all"
        }
        if tag and tag != "all":
            data["genre_arr"] = [tag]

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
        if self._fp_header_name and self._fp_header_value:
            req_headers[self._fp_header_name] = self._fp_header_value

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
                    if self._fp_header_name:
                        req_headers[self._fp_header_name] = self._fp_header_value
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

    def homeContent(self, filter):
        return {
            "class": self.classes,
            "list": self._videos(self._api()),
            "filters": self.filters
        }

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

    def searchContent(self, key, quick, pg="1"):
        page = max(1, int(pg or 1))
        data = self._api(page=page, title=key)
        return {
            "page": page,
            "pagecount": int(data.get("pages") or 1),
            "list": self._videos(data)
        }

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
            click = f'(()=>{{let n=0,t=setInterval(()=>{{const b=document.querySelector(\'.playbtn[data-path="{safe_path}"]\')||document.querySelector(\'.playbtn[data-path=\'{safe_path}\']\');if(b&&typeof IwasKing===\'function\'){{clearInterval(t);b.click();}}else if(++n>200)clearInterval(t);}},100);}})()'

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

    def localProxy(self, param):
        return [200, {}, '']


# ==================== 测试入口 ====================
if __name__ == "__main__":
    s = Spider()
    s.init()
    print("分类：", s.homeContent(False)["class"])
    result = s.categoryContent("movie", 1, {}, {})
    print("电影分类第1页：", result["list"][:2])
    print("总数：", result["total"])
