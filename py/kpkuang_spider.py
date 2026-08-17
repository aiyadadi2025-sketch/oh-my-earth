# TVBox 爬虫插件 - 看片狂人 (kpkuang)
# 目标网站: https://kpkuang.one
# 核心特性: 完整分类、搜索、详情页解析、多线路播放
# 特点: 使用CF保护的网站，部分接口需要特殊处理

import re
import urllib.request
import urllib.parse
import json
import sys

class Spider:
    def __init__(self):
        self.name = '看片狂人'
        self.host = 'https://kpkuang.one'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': self.host + '/',
        }
        self.ctx = None
        try:
            import ssl
            self.ctx = ssl.create_default_context()
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        except:
            pass

    def req(self, url, data=None, referer=None):
        """发起HTTP请求"""
        try:
            req = urllib.request.Request(url, headers=self.headers)
            if data:
                req.data = data.encode('utf-8') if isinstance(data, str) else data
            resp = urllib.request.urlopen(req, timeout=15, context=self.ctx)
            content = resp.read().decode('utf-8', errors='ignore')
            return content
        except Exception as e:
            print(f'Request error: {e}')
            return ''

    def homeContent(self, filter):
        """首页内容 - 返回分类和筛选"""
        result = {}
        classes = [
            {"type_id": "1", "type_name": "电影"},
            {"type_id": "2", "type_name": "连续剧"},
            {"type_id": "3", "type_name": "综艺"},
            {"type_id": "4", "type_name": "动漫"},
            {"type_id": "37", "type_name": "短剧"},
        ]

        filters = {
            "1": [
                {"key": "class", "name": "类型", "value": [{"n": "全部", "v": ""}, {"n": "剧情片", "v": "11"}, {"n": "动作片", "v": "6"}, {"n": "喜剧片", "v": "7"}, {"n": "爱情片", "v": "8"}, {"n": "科幻片", "v": "9"}, {"n": "恐怖片", "v": "10"}, {"n": "战争片", "v": "12"}]},
                {"key": "year", "name": "年份", "value": [{"n": "全部", "v": ""}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"}, {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}]},
                {"key": "letter", "name": "字母", "value": [{"n": "全部", "v": ""}, {"n": "A", "v": "A"}, {"n": "B", "v": "B"}]},
                {"key": "area", "name": "地区", "value": [{"n": "全部", "v": ""}, {"n": "美国", "v": "美国"}, {"n": "中国", "v": "中国大陆"}, {"n": "日本", "v": "日本"}, {"n": "韩国", "v": "韩国"}]},
            ],
            "2": [
                {"key": "class", "name": "类型", "value": [{"n": "全部", "v": ""}, {"n": "国产剧", "v": "13"}, {"n": "港剧", "v": "14"}, {"n": "日剧", "v": "15"}, {"n": "欧美剧", "v": "16"}, {"n": "韩剧", "v": "23"}]},
                {"key": "year", "name": "年份", "value": [{"n": "全部", "v": ""}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"}]},
            ],
            "3": [
                {"key": "year", "name": "年份", "value": [{"n": "全部", "v": ""}, {"n": "2026", "v": "2026"}]},
            ],
            "4": [
                {"key": "year", "name": "年份", "value": [{"n": "全部", "v": ""}, {"n": "2026", "v": "2026"}]},
            ],
            "37": [],
        }

        result["class"] = classes
        result["filters"] = filters
        return result

    def categoryContent(self, tid, pg, filter, extend):
        """分类列表内容"""
        result = {}
        videos = []

        # 构建URL - 使用基础分类页
        url = f'{self.host}/vodtype/{tid}/page/{pg}.html'

        content = self.req(url)
        if not content or len(content) < 1000:
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

        # 配对数据
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
        result["page"] = pg
        result["pagecount"] = int(pg) + 1
        result["limit"] = 20
        result["total"] = len(videos)
        result["totalpages"] = int(pg) + 1

        return result

    def detailContent(self, vid):
        """详情页内容"""
        result = {}
        video = {"vod_id": vid, "list": []}

        url = f'{self.host}/voddetail/{vid}/'
        content = self.req(url)
        if not content or len(content) < 5000:
            return result

        # 提取标题 - 从title标签
        title_match = re.search(r'<title>([^<]+)</title>', content)
        if title_match:
            title_str = title_match.group(1)
            # 格式: "电影名 年份 线上看,在线观看..."
            parts = title_str.split('(')
            if len(parts) > 0:
                video["vod_name"] = parts[0].strip()
            else:
                video["vod_name"] = title_str

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

        # 提取播放链接 - 从vodplay链接
        play_links = re.findall(r'href="/vodplay/(\d+)-(\d+)-(\d+)\.html"', content)
        # 去重
        seen_plays = set()
        unique_plays = []
        for link in play_links:
            key = f"{link[0]}-{link[1]}-{link[2]}"
            if key not in seen_plays:
                seen_plays.add(key)
                unique_plays.append(link)

        # 按集数排序
        unique_plays.sort(key=lambda x: (int(x[1]), int(x[2])))

        # 构建播放列表
        # 分组线路
        line_map = {}
        for play_id, ep_num, line_num in unique_plays:
            if line_num not in line_map:
                line_map[line_num] = []
            line_map[line_num].append((ep_num, play_id))

        # 提取线路名称
        lines = re.findall(r'data-lineid="([^"]*)"[^>]*data-linename="([^"]*)"', content)
        line_names = {l[0]: l[1] for l in lines}

        # 构建 vod_play_from 和 vod_play_url
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

        video["vod_play_from"] = "$$$".join(from_list) if from_list else "默认线路"
        video["vod_play_url"] = "$$$".join(url_list) if url_list else ""

        result["list"] = [video]
        return result

    def searchContent(self, key, quick):
        """搜索内容 - 使用首页推荐或分类页面近似搜索"""
        result = {}
        videos = []

        # 由于CF保护，搜索接口可能需要特殊处理
        # 尝试直接访问搜索页
        url = f'{self.host}/vodsearch/-------------.html?wd={urllib.parse.quote(key)}'
        content = self.req(url)

        if not content or len(content) < 2000:
            # 如果搜索被拦截，返回空结果
            result["list"] = videos
            return result

        # 解析搜索结果
        links = re.findall(r'href="/voddetail/(\d+)/"', content)
        titles = re.findall(r'<span[^>]*class="[^"]*cinema_title[^"]*">([^<]+)</span>', content)
        pics = re.findall(r'data-original="([^"]*)"', content)

        seen = set()
        for i, vid in enumerate(links):
            if vid in seen:
                continue
            seen.add(vid)
            title = titles[i] if i < len(titles) else vid
            pic = pics[i] if i < len(pics) else ''

            videos.append({
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": ""
            })

        result["list"] = videos
        result["page"] = 1
        result["pagecount"] = 1
        result["limit"] = len(videos)
        result["total"] = len(videos)
        result["totalpages"] = 1

        return result

    def playerContent(self, flag, id, vipFlags):
        """播放器内容"""
        result = {}

        # 解析播放URL格式: https://kpkuang.one/vodplay/ID-Episode-Line.html
        # 提取参数
        match = re.search(r'vodplay/(\d+)-(\d+)-(\d+)\.html', id)
        if not match:
            result["parse"] = 1
            result["url"] = ""
            result["header"] = ""
            return result

        vid = match.group(1)
        ep = match.group(2)
        line = match.group(3)

        # 构造播放页URL
        play_url = f'{self.host}/vodplay/{vid}-{ep}-{line}.html'

        # 获取播放页内容
        content = self.req(play_url)
        if not content:
            result["parse"] = 1
            result["url"] = ""
            result["header"] = ""
            return result

        # 提取线路名称
        line_name = ""
        line_match = re.search(r'id="from_' + line + r'"[^>]*data-linename="([^"]*)"', content)
        if line_match:
            line_name = line_match.group(1)

        # 提取解析接口
        parse_urls = []
        parse_matches = re.findall(r'(parse\.[^/]+\.cc/index\.php\?url=)', content)
        for p in parse_matches:
            if p not in parse_urls:
                parse_urls.append(p)

        # 根据线路选择解析器
        parse_api = ""
        if line == "qq" or line == "youku" or line == "bilibili" or line == "qiyi":
            # VIP线路使用解析
            if parse_urls:
                parse_api = parse_urls[0]
        elif line in ["aby", "esv"]:
            # 超清线路
            if len(parse_urls) > 1:
                parse_api = parse_urls[1]
            elif parse_urls:
                parse_api = parse_urls[0]

        # 构造最终播放URL
        if parse_api:
            final_url = parse_api + play_url
        else:
            # 直接返回播放页，让播放器处理
            final_url = play_url

        result["parse"] = 0 if parse_api else 1
        result["url"] = final_url
        result["header"] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': self.host + '/'
        }

        return result

def getHomeFilter():
    """获取首页筛选配置"""
    return {
        "1": [
            {"key": "class", "name": "类型", "value": [{"n": "全部", "v": ""}, {"n": "剧情", "v": "11"}, {"n": "动作", "v": "6"}, {"n": "喜剧", "v": "7"}]},
            {"key": "year", "name": "年份", "value": [{"n": "全部", "v": ""}, {"n": "2026", "v": "2026"}]},
        ],
        "2": [
            {"key": "class", "name": "类型", "value": [{"n": "全部", "v": ""}, {"n": "国产", "v": "13"}, {"n": "韩剧", "v": "23"}]},
        ],
    }

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
        print("Commands:")
        print("  home                              - 获取首页分类")
        print("  cate <type_id> [page]             - 获取分类列表")
        print("  detail <vid>                      - 获取详情")
        print("  search <keyword>                  - 搜索")
        print("  player <flag> <play_url>          - 获取播放地址")

if __name__ == "__main__":
    main()
