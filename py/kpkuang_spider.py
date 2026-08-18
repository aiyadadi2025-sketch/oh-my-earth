import re
import urllib.request
import urllib.parse
import json
import sys
import ssl
import http.cookiejar

class Spider:
    def __init__(self):
        self.name = '看片狂人'
        self.host = 'https://kpkuang.one'

        # Cookie管理器
        self.cookie_jar = http.cookiejar.CookieJar()
        self.cookie_processor = urllib.request.HTTPCookieProcessor(self.cookie_jar)

        # 请求头
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': self.host + '/',
        }

        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

        self.opener = urllib.request.build_opener(self.cookie_processor)
        self.opener.addheaders = list(self.headers.items())

        # 分类名称映射
        self.type_names = {
            '1': '电影', '2': '连续剧', '3': '综艺', '4': '动漫', '37': '短剧',
            '6': '动作片', '7': '喜剧片', '8': '爱情片', '9': '科幻片',
            '10': '恐怖片', '11': '剧情片', '12': '战争片',
            '13': '国产剧', '14': '港剧', '15': '日剧', '16': '欧美剧',
            '20': '台剧', '21': '泰剧', '22': '越南剧', '23': '韩剧',
            '29': '纪录片', '30': '海外剧', '31': '卡通片'
        }

        # 初始化会话
        self._init_session()

    def _init_session(self):
        """初始化会话"""
        try:
            req = urllib.request.Request(self.host + '/')
            resp = self.opener.open(req, timeout=10, context=self.ctx)
            resp.read()
        except:
            pass

    def req(self, url):
        """发起HTTP请求"""
        try:
            req = urllib.request.Request(url, headers=self.headers)
            resp = self.opener.open(req, timeout=15, context=self.ctx)
            content = resp.read().decode('utf-8', errors='ignore')

            # 检查是否被拦截
            if len(content) < 1000 or 'Just a moment...' in content:
                self._init_session()
                req = urllib.request.Request(url, headers=self.headers)
                resp = self.opener.open(req, timeout=15, context=self.ctx)
                content = resp.read().decode('utf-8', errors='ignore')

            return content
        except:
            return ''

    def homeContent(self, filter):
        """首页内容"""
        result = {"class": []}

        # 访问首页
        content = self.req(self.host + '/')

        # 提取分类 - 使用简化正则
        # 匹配: href="/vodtype/1/">电影</a>
        links = re.findall(r'href="/vodtype/(\d+)/"[^>]*>([^<]*)</a>', content)

        # 去重并保持顺序
        seen = set()
        for tid, tname in links:
            if tid not in seen:
                seen.add(tid)
                # 清理名称
                tname = tname.strip() if tname.strip() else self.type_names.get(tid, f'分类{tid}')
                result["class"].append({
                    "type_id": tid,
                    "type_name": tname
                })

        # 如果没有提取到，使用默认
        if not result["class"]:
            result["class"] = [
                {"type_id": "1", "type_name": "电影"},
                {"type_id": "2", "type_name": "连续剧"},
                {"type_id": "3", "type_name": "综艺"},
                {"type_id": "4", "type_name": "动漫"},
                {"type_id": "37", "type_name": "短剧"},
            ]

        # 筛选配置
        result["filters"] = {
            "1": [{"key": "class", "name": "类型", "value": [
                {"n": "全部", "v": ""},
                {"n": "剧情片", "v": "11"},
                {"n": "动作片", "v": "6"},
            ]}],
        }

        return result

    def categoryContent(self, tid, pg, filter, extend):
        """分类列表"""
        result = {"list": [], "page": pg, "pagecount": int(pg) + 1}

        # 构建URL
        url = f'{self.host}/vodtype/{tid}/page/{pg}.html' if int(pg) > 1 else f'{self.host}/vodtype/{tid}/'
        content = self.req(url)

        if not content or len(content) < 5000:
            return result

        # 提取视频链接
        links = re.findall(r'href="/voddetail/(\d+)/"', content)

        # 去重
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
        videos = []
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
        """详情页"""
        result = {"list": []}
        video = {"vod_id": vid, "vod_name": "", "vod_pic": "", "vod_content": "",
                 "vod_actor": "", "vod_play_from": "", "vod_play_url": ""}

        content = self.req(f'{self.host}/voddetail/{vid}/')
        if not content or len(content) < 5000:
            return result

        # 标题
        title_match = re.search(r'<title>([^<]+)</title>', content)
        if title_match:
            parts = title_match.group(1).split('(')
            video["vod_name"] = parts[0].strip()

        # 封面
        pic_match = re.search(r'<img[^>]*id="[^"]*"[^>]*src="([^"]*)"', content)
        if pic_match:
            video["vod_pic"] = pic_match.group(1)

        # 评分
        score_match = re.search(r'豆瓣评分[^>]*>([^<]+)', content)
        if score_match:
            video["vod_score"] = score_match.group(1).strip()

        # 简介
        desc_match = re.search(r'以下是剧情简介：(.+?)影片改编', content, re.DOTALL)
        if desc_match:
            video["vod_content"] = desc_match.group(1).strip()

        # 演员
        actors = re.findall(r'<a[^>]*href="/celeb/\d+\.html"[^>]*>([^<]+)</a>', content)
        video["vod_actor"] = "、".join(actors[:5]) if actors else ""

        # 播放链接
        play_links = re.findall(r'href="/vodplay/(\d+)-(\d+)-(\d+)\.html"', content)

        # 去重排序
        seen = set()
        unique_plays = []
        for link in play_links:
            key = f"{link[0]}-{link[1]}-{link[2]}"
            if key not in seen:
                seen.add(key)
                unique_plays.append(link)
        unique_plays.sort(key=lambda x: (int(x[1]), int(x[2])))

        # 线路名称
        lines = re.findall(r'data-lineid="([^"]*)"[^>]*data-linename="([^"]*)"', content)
        line_names = {l[0]: l[1] for l in lines}

        # 分组
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
                play_url = f"{self.host}/vodplay/{play_id}-{ep_num}-{line_num}.html"
                ep_urls.append(f"第{ep_num}集${play_url}")
            url_list.append("#".join(ep_urls))

        video["vod_play_from"] = "$$$".join(from_list)
        video["vod_play_url"] = "$$$".join(url_list)

        result["list"] = [video]
        return result

    def searchContent(self, key, quick):
        """搜索"""
        result = {"list": [], "page": 1, "pagecount": 1}

        content = self.req(f'{self.host}/vodsearch/-------------.html?wd={urllib.parse.quote(key)}')
        if not content or len(content) < 2000:
            return result

        links = re.findall(r'href="/voddetail/(\d+)/"', content)
        titles = re.findall(r'<span[^>]*class="[^"]*cinema_title[^"]*">([^<]+)</span>', content)
        pics = re.findall(r'data-original="([^"]*)"', content)

        videos = []
        seen = set()
        for i, vid in enumerate(links[:20]):
            if vid not in seen:
                seen.add(vid)
                videos.append({
                    "vod_id": vid,
                    "vod_name": titles[i] if i < len(titles) else '',
                    "vod_pic": pics[i] if i < len(pics) else '',
                    "vod_remarks": ""
                })

        result["list"] = videos
        return result

    def playerContent(self, flag, id, vipFlags):
        """播放器"""
        result = {"parse": 1, "url": "", "header": {}}

        match = re.search(r'vodplay/(\d+)-(\d+)-(\d+)\.html', id)
        if not match:
            return result

        vid, ep, line = match.groups()
        play_url = f'{self.host}/vodplay/{vid}-{ep}-{line}.html'
        content = self.req(play_url)

        if not content:
            return result

        # 查找解析接口
        parse_matches = re.findall(r'(parse\.[^/]+\.cc/index\.php\?url=)', content)
        if parse_matches:
            result["parse"] = 0
            result["url"] = parse_matches[0] + play_url

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
