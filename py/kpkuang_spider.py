# TVBox 爬虫插件 - 看片狂人 (kpkuang)
# 目标网站: https://kpkuang.one
# 类型: HTML解析型

import re
import urllib.request
import urllib.parse
import json
import sys
import ssl

class Spider:
    def __init__(self):
        self.name = '看片狂人'
        self.host = 'https://kpkuang.one'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': self.host + '/',
        }
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

    def req(self, url, data=None):
        """发起HTTP请求"""
        try:
            req = urllib.request.Request(url, headers=self.headers)
            if data:
                req.data = data.encode('utf-8') if isinstance(data, str) else data
            resp = urllib.request.urlopen(req, timeout=15, context=self.ctx)
            content = resp.read().decode('utf-8', errors='ignore')
            return content
        except Exception as e:
            return ''

    def homeContent(self, filter):
        """首页内容 - 返回分类和筛选"""
        result = {"class": []}

        # 硬编码分类数据（根据实际网站结构）
        result["class"] = [
            {"type_id": "1", "type_name": "电影"},
            {"type_id": "2", "type_name": "连续剧"},
            {"type_id": "3", "type_name": "综艺"},
            {"type_id": "4", "type_name": "动漫"},
            {"type_id": "37", "type_name": "短剧"},
        ]

        # 筛选配置
        result["filters"] = {
            "1": [
                {"key": "class", "name": "类型", "value": [
                    {"n": "全部", "v": ""},
                    {"n": "剧情片", "v": "11"},
                    {"n": "动作片", "v": "6"},
                    {"n": "喜剧片", "v": "7"},
                    {"n": "爱情片", "v": "8"},
                    {"n": "科幻片", "v": "9"},
                    {"n": "恐怖片", "v": "10"},
                ]},
            ],
            "2": [
                {"key": "class", "name": "类型", "value": [
                    {"n": "全部", "v": ""},
                    {"n": "国产剧", "v": "13"},
                    {"n": "港剧", "v": "14"},
                    {"n": "日剧", "v": "15"},
                    {"n": "欧美剧", "v": "16"},
                    {"n": "韩剧", "v": "23"},
                ]},
            ],
        }

        return result

    def categoryContent(self, tid, pg, filter, extend):
        """分类列表内容"""
        result = {"list": [], "page": pg, "pagecount": int(pg) + 1}
        videos = []

        # 构建URL
        if int(pg) > 1:
            url = f'{self.host}/vodtype/{tid}/page/{pg}.html'
        else:
            url = f'{self.host}/vodtype/{tid}/'

        content = self.req(url)
        if not content or len(content) < 5000:
            return result

        # 提取视频链接
        links = re.findall(r'href="/voddetail/(\d+)/"', content)

        # 去重并保持顺序
        seen = set()
        unique_links = []
        for link in links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)

        # 提取标题
        titles = re.findall(r'<span[^>]*class="[^"]*cinema_title[^"]*">([^<]+)</span>', content)

        # 提取图片
        pics = re.findall(r'data-original="([^"]*)"', content)

        # 组装数据
        for i, vid in enumerate(unique_links[:20]):
            title = titles[i] if i < len(titles) else ''
            pic = pics[i] if i < len(pics) else ''

            videos.append({
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": ""
            })

        result["list"] = videos
        return result

    def detailContent(self, vid):
        """详情页内容"""
        result = {"list": []}
        video = {
            "vod_id": vid,
            "vod_name": "",
            "vod_pic": "",
            "vod_content": "",
            "vod_actor": "",
            "vod_play_from": "",
            "vod_play_url": ""
        }

        url = f'{self.host}/voddetail/{vid}/'
        content = self.req(url)
        if not content or len(content) < 5000:
            return result

        # 提取标题 - 从title标签
        title_match = re.search(r'<title>([^<]+)</title>', content)
        if title_match:
            title_str = title_match.group(1)
            parts = title_str.split('(')
            video["vod_name"] = parts[0].strip() if parts else title_str

        # 提取封面
        pic_match = re.search(r'<img[^>]*id="[^"]*"[^>]*src="([^"]*)"', content)
        if pic_match:
            video["vod_pic"] = pic_match.group(1)

        # 提取评分
        score_match = re.search(r'豆瓣评分[^>]*>([^<]+)', content)
        if score_match:
            video["vod_score"] = score_match.group(1).strip()

        # 提取简介
        desc_match = re.search(r'以下是剧情简介：(.+?)影片改编', content, re.DOTALL)
        if desc_match:
            video["vod_content"] = desc_match.group(1).strip()

        # 提取演员
        actors = re.findall(r'<a[^>]*href="/celeb/\d+\.html"[^>]*>([^<]+)</a>', content)
        video["vod_actor"] = "、".join(actors[:5]) if actors else ""

        # 提取播放链接
        play_links = re.findall(r'href="/vodplay/(\d+)-(\d+)-(\d+)\.html"', content)

        # 去重并按集数排序
        seen = set()
        unique_plays = []
        for link in play_links:
            key = f"{link[0]}-{link[1]}-{link[2]}"
            if key not in seen:
                seen.add(key)
                unique_plays.append(link)
        unique_plays.sort(key=lambda x: (int(x[1]), int(x[2])))

        # 提取线路名称
        lines = re.findall(r'data-lineid="([^"]*)"[^>]*data-linename="([^"]*)"', content)
        line_names = {l[0]: l[1] for l in lines}

        # 分组线路
        line_map = {}
        for play_id, ep_num, line_num in unique_plays:
            if line_num not in line_map:
                line_map[line_num] = []
            line_map[line_num].append((ep_num, play_id))

        # 构建播放列表
        from_list = []
        url_list = []

        for line_num, eps in line_map.items():
            line_name = line_names.get(line_num, f"线路{line_num}")
            from_list.append(line_name)

            ep_urls = []
            for ep_num, play_id in eps:
                ep_name = f"第{ep_num}集"
                play_url = f"{self.host}/vodplay/{play_id}-{ep_num}-{line_num}.html"
                ep_urls.append(f"{ep_name}${play_url}")

            url_list.append("#".join(ep_urls))

        video["vod_play_from"] = "$$$".join(from_list) if from_list else ""
        video["vod_play_url"] = "$$$".join(url_list) if url_list else ""

        result["list"] = [video]
        return result

    def searchContent(self, key, quick):
        """搜索内容"""
        result = {"list": [], "page": 1, "pagecount": 1}
        videos = []

        url = f'{self.host}/vodsearch/-------------.html?wd={urllib.parse.quote(key)}'
        content = self.req(url)

        # 搜索可能受CF保护，检查内容
        if not content or len(content) < 2000:
            return result

        # 提取结果
        links = re.findall(r'href="/voddetail/(\d+)/"', content)
        titles = re.findall(r'<span[^>]*class="[^"]*cinema_title[^"]*">([^<]+)</span>', content)
        pics = re.findall(r'data-original="([^"]*)"', content)

        seen = set()
        for i, vid in enumerate(links[:20]):
            if vid in seen:
                continue
            seen.add(vid)
            title = titles[i] if i < len(titles) else ''
            pic = pics[i] if i < len(pics) else ''

            videos.append({
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": ""
            })

        result["list"] = videos
        return result

    def playerContent(self, flag, id, vipFlags):
        """播放器内容"""
        result = {"parse": 1, "url": "", "header": {}}

        # 解析播放URL
        match = re.search(r'vodplay/(\d+)-(\d+)-(\d+)\.html', id)
        if not match:
            return result

        vid = match.group(1)
        ep = match.group(2)
        line = match.group(3)

        # 获取播放页
        play_url = f'{self.host}/vodplay/{vid}-{ep}-{line}.html'
        content = self.req(play_url)

        if not content:
            return result

        # 查找解析接口
        parse_matches = re.findall(r'(parse\.[^/]+\.cc/index\.php\?url=)', content)
        parse_api = parse_matches[0] if parse_matches else ""

        # 根据线路选择解析器
        if line in ["qq", "youku", "bilibili", "qiyi"]:
            if parse_api:
                result["parse"] = 0
                result["url"] = parse_api + play_url
        elif line in ["aby", "esv"]:
            if len(parse_matches) > 1:
                result["parse"] = 0
                result["url"] = parse_matches[1] + play_url
            elif parse_api:
                result["parse"] = 0
                result["url"] = parse_api + play_url
        else:
            if parse_api:
                result["parse"] = 0
                result["url"] = parse_api + play_url

        result["header"] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': self.host + '/'
        }

        return result


def main():
    spider = Spider()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == 'home':
            print(json.dumps(spider.homeContent(None), ensure_ascii=False, indent=2))
        elif cmd == 'cate':
            print(json.dumps(spider.categoryContent(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '1', None, {}), ensure_ascii=False, indent=2))
        elif cmd == 'detail':
            print(json.dumps(spider.detailContent(sys.argv[2]), ensure_ascii=False, indent=2))
        elif cmd == 'search':
            print(json.dumps(spider.searchContent(sys.argv[2], False), ensure_ascii=False, indent=2))
        elif cmd == 'player':
            print(json.dumps(spider.playerContent(sys.argv[2], sys.argv[3], None), ensure_ascii=False, indent=2))
    else:
        print("Usage: python kpkuang_spider.py <command> [args]")

if __name__ == "__main__":
    main()
