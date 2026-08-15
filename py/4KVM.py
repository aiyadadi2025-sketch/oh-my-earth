# -*- coding: utf-8 -*-
# 4K影视插件 - 优化版本
import re
import sys
import json
from pyquery import PyQuery as pq
sys.path.append('..')
from base.spider import Spider


class Spider(Spider):

    def __init__(self):
        super().__init__()
        self.domains = [
            'https://www.4kvm.tv',
            'https://www.4kvm.net',
            'https://www.4kvm.org',
            'https://www.4kvms.org',
            'https://www.4kvms.com',
            'https://www.4kvms.top',
        ]
        self.host = ''
        self._initHost()

    def _initHost(self):
        """自动检测可用域名"""
        for domain in self.domains:
            try:
                resp = self.fetch(domain, headers=self.headers, timeout=5)
                if resp.status_code == 200 and len(resp.text) > 1000:
                    self.host = domain
                    return
            except:
                continue

    def init(self, extend=""):
        pass

    def getName(self):
        return "4k影视"

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Sec-Ch-Ua': '"Not/A)Brand";v="8", "Chromium";v="134", "Google Chrome";v="134"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"macOS"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
    }

    # 分类配置 - 硬编码确保稳定
    CATEGORIES = {
        'movies': {'name': '电影', 'url': '/movies'},
        'tvshows': {'name': '电视剧', 'url': '/tvshows'},
        'animation': {'name': '动漫', 'url': '/animation'},
        'documentary': {'name': '纪录片', 'url': '/documentary'},
        'foreign': {'name': '海外短剧', 'url': '/foreign'},
    }

    # 公共方法
    def _normalize_url(self, url):
        """标准化URL处理"""
        if not url:
            return url
        if url.startswith('//'):
            return f"https:{url}"
        elif url.startswith('/'):
            return f"{self.host}{url}"
        return url

    def _extract_video_basic(self, item):
        """提取视频基本信息"""
        try:
            link = self._normalize_url(item('a').attr('href') or item('h3 a').attr('href') or item('.data h3 a').attr('href'))
            if not link:
                return None

            title = (item('h3').text().strip() or item('.data h3').text().strip() or
                    item('img').attr('alt') or item('a').attr('title') or '未知标题')

            img = self._normalize_url(item('img').attr('src') or item('img').attr('data-src'))

            # 简化备注提取
            remarks = (item('.rating, .imdb, .vote').text().strip() or
                      item('.year, .date, span').text().strip() or
                      item('.type, .genre, .tag').text().strip())

            # 尝试从标题中提取年份
            year = ''
            year_match = re.search(r'(\d{4})', title)
            if year_match:
                year = year_match.group(1)

            return {
                'vod_id': link,
                'vod_name': title,
                'vod_pic': img or '',
                'vod_remarks': remarks,
                'vod_year': year
            }
        except:
            return None

    def _get_episode_count(self, season_data, page_html):
        """智能检测集数"""
        # 方法1: 精确容器检测
        episode_container = season_data('.jujiepisodios')
        if episode_container:
            episode_links = episode_container('a')
            episode_numbers = [int(link.text().strip()) for link in episode_links.items()
                             if link.text().strip().isdigit() and 1 <= int(link.text().strip()) <= 200]
            if episode_numbers:
                return max(episode_numbers)

        # 方法2: JavaScript数据提取
        video_matches = re.findall(r'video.*?=.*?\[(.*?)\]', page_html, re.IGNORECASE | re.DOTALL)
        for match in video_matches:
            if '"name":' in match:
                episode_names = re.findall(r'"name"\s*:\s*(\d+)', match)
                if len(episode_names) >= 5:
                    episode_numbers = sorted(set(int(name) for name in episode_names))
                    if episode_numbers[0] == 1 and episode_numbers[-1] - episode_numbers[0] == len(episode_numbers) - 1:
                        return max(episode_numbers)

        # 方法3: 文本模式匹配
        page_text = season_data.text()
        for pattern in [r'共(\d+)集', r'全(\d+)集', r'更新至(\d+)集', r'第(\d+)集']:
            matches = re.findall(pattern, page_text)
            if matches:
                return max(int(m) for m in matches if m.isdigit())

        # 默认值
        return 24 if season_data('iframe, video, .player') else 1

    def homeContent(self, filter):
        try:
            # 使用硬编码分类，不依赖页面解析（避免无分类问题）
            classes = []
            for tid, info in self.CATEGORIES.items():
                class_info = {'type_name': info['name'], 'type_id': tid}
                classes.append(class_info)

            result = {'class': classes}

            # 尝试获取筛选配置
            result['filters'] = self._get_filters()

            # 获取首页推荐内容
            try:
                data = self.getpq(self.fetch(self.host, headers=self.headers).text)
                videos = self.getHomeList(data)
                result['list'] = videos
            except:
                result['list'] = []

            return result
        except Exception as e:
            return {'class': [], 'list': [], 'filters': {}}

    def homeVideoContent(self):
        pass

    def categoryContent(self, tid, pg, filter, extend):
        try:
            # 解析筛选参数
            cls = ''
            area = ''
            year = ''
            by = 'time'

            if extend and isinstance(extend, dict):
                cls = extend.get('class', '')
                area = extend.get('area', '')
                year = extend.get('year', '')
                by = extend.get('by', 'time')

            # 构建基础URL
            cat_info = self.CATEGORIES.get(tid, {})
            base_url = cat_info.get('url', f'/{tid}')

            # 构建筛选路径
            filters_parts = []
            if cls:
                filters_parts.append(f"class/{cls}")
            if area:
                filters_parts.append(f"area/{area}")
            if year:
                filters_parts.append(f"year/{year}")
            if by and by != 'time':
                filters_parts.append(f"by/{by}")

            # 组合完整URL
            if filters_parts:
                url = f"{self.host}{base_url}/{'/'.join(filters_parts)}"
            else:
                url = f"{self.host}{base_url}"

            # 分页
            if pg != '1':
                url += f"/page/{pg}"

            resp = self.fetch(url, headers=self.headers)
            data = self.getpq(resp.text)

            video_list = self.getVideoList(data)

            # 电视剧分类过滤电影
            if tid == 'tvshows':
                video_list = self.filterTVShowsOnly(video_list)

            return {'list': video_list, 'page': int(pg), 'pagecount': 9999, 'limit': 30, 'total': 999999}
        except Exception as e:
            return {'list': [], 'page': int(pg), 'pagecount': 1, 'limit': 30, 'total': 0}

    def detailContent(self, ids):
        try:
            first_id = next(iter(ids)) if hasattr(ids, '__iter__') and not isinstance(ids, str) else ids
            data = self.getpq(self.fetch(first_id, headers=self.headers).text)

            # 基本信息提取
            vod = {
                'vod_id': first_id,
                'vod_name': data('.sheader h1, h1').text().strip(),
                'vod_pic': self._normalize_url(data('.sheader .poster img, .poster img').attr('src')),
                'vod_content': data('.sbox .wp-content, #info .wp-content').text().strip(),
                'vod_year': '', 'vod_area': '', 'vod_remarks': '', 'vod_actor': '', 'vod_director': ''
            }

            # 提取分类
            genres = data('.sgeneros a')
            if genres:
                vod['type_name'] = ', '.join(g.text() for g in genres.items())

            # 提取年份
            year_match = re.search(r'(\d{4})', data.text())
            if year_match:
                vod['vod_year'] = year_match.group(1)

            # 播放链接处理
            play_options = data('#playeroptions ul li, .dooplay_player_option')
            if play_options:
                play_links = self._extract_play_options(play_options, first_id)
            else:
                season_links = data('.seasons-list a, .season-item a, .se-c a, .se-a a, .seasons a')
                play_links = self.getSeasonEpisodes(season_links) if season_links else [f"播放${first_id}"]

            vod['vod_play_from'] = '老僧酿酒'
            vod['vod_play_url'] = '#'.join(play_links)

            return {'list': [vod]}
        except Exception as e:
            return {'list': []}

    def _extract_play_options(self, play_options, first_id):
        """提取播放选项"""
        play_links = []
        for option in play_options.items():
            title = option('.title, span.title').text().strip() or '播放'
            server = option('.server, span.server').text().strip()
            if server:
                title = f"{title}-{server}"

            data_post = option.attr('data-post')
            data_nume = option.attr('data-nume')
            data_type = option.attr('data-type')

            if data_post and data_nume:
                play_url = f"{first_id}?post={data_post}&nume={data_nume}&type={data_type}"
                play_links.append(f"{title}${play_url}")

        return play_links

    def searchContent(self, key, quick, pg="1"):
        try:
            search_url = f"{self.host}/xssearch?s={key}"
            if pg != "1":
                search_url += f"&p={pg}"

            data = self.getpq(self.fetch(search_url, headers=self.headers).text)
            raw_results = self.getVideoList(data)
            filtered_results = self.filterSearchResults(raw_results, key)

            return {'list': filtered_results, 'page': int(pg)}
        except Exception as e:
            return {'list': [], 'page': int(pg)}

    def playerContent(self, flag, id, vipFlags):
        try:
            # 解析播放参数
            data_post = data_nume = data_type = None
            if '?' in id:
                base_url, params = id.split('?', 1)
                param_dict = dict(param.split('=', 1) for param in params.split('&') if '=' in param)
                data_post = param_dict.get('post')
                data_nume = param_dict.get('nume')
                data_type = param_dict.get('type')

            # API调用
            if data_post and data_nume:
                try:
                    api_url = f"{self.host}/wp-json/dooplayer/v1/post/{data_post}"
                    api_response = self.fetch(api_url, headers=self.headers,
                                            params={'type': data_type or 'movie', 'source': data_nume})
                    if api_response.status_code == 200:
                        api_data = api_response.json()
                        if 'embed_url' in api_data:
                            embed_url = api_data['embed_url']
                            parse_flag = 0 if any(ext in embed_url.lower() for ext in ['.m3u8', '.mp4', '.flv', '.avi']) else 1
                            return {'parse': parse_flag, 'url': embed_url, 'header': self.headers}
                except:
                    pass

            # 页面解析回退
            page_url = base_url if '?' in id else id
            data = self.getpq(self.fetch(page_url, headers=self.headers).text)

            # 查找播放源
            iframe = data('iframe.metaframe, .dooplay_player iframe, .player iframe').attr('src')
            if iframe:
                iframe = self._normalize_url(iframe)
                parse_flag = 0 if any(ext in iframe.lower() for ext in ['.m3u8', '.mp4', '.flv']) else 1
                return {'parse': parse_flag, 'url': iframe, 'header': self.headers}

            video_src = self._normalize_url(data('video source, video').attr('src'))
            if video_src:
                return {'parse': 0, 'url': video_src, 'header': self.headers}

            return {'parse': 1, 'url': page_url, 'header': self.headers}

        except Exception as e:
            return {'parse': 1, 'url': id, 'header': self.headers}

    def localProxy(self, param):
        pass

    def liveContent(self, url):
        pass

    def _get_filters(self):
        """生成筛选器配置"""
        filters = {}

        # 类型选项
        class_vals = [
            {"n": "全部", "v": ""},
            {"n": "动作", "v": "action"},
            {"n": "爱情", "v": "romance"},
            {"n": "喜剧", "v": "comedy"},
            {"n": "科幻", "v": "scifi"},
            {"n": "恐怖", "v": "horror"},
            {"n": "悬疑", "v": "thriller"},
            {"n": "剧情", "v": "drama"},
            {"n": "动画", "v": "animation"},
            {"n": "纪录片", "v": "documentary"},
        ]

        # 地区选项
        area_vals = [
            {"n": "全部", "v": ""},
            {"n": "美国", "v": "usa"},
            {"n": "英国", "v": "uk"},
            {"n": "中国大陆", "v": "china"},
            {"n": "香港", "v": "hongkong"},
            {"n": "台湾", "v": "taiwan"},
            {"n": "日本", "v": "japan"},
            {"n": "韩国", "v": "korea"},
            {"n": "法国", "v": "france"},
            {"n": "德国", "v": "germany"},
            {"n": "印度", "v": "india"},
        ]

        # 年份选项
        year_vals = [{"n": "全部", "v": ""}]
        for y in range(2026, 1990, -1):
            year_vals.append({"n": str(y), "v": str(y)})

        # 排序选项
        by_vals = [
            {"n": "最新", "v": "time"},
            {"n": "最热", "v": "hits"},
            {"n": "评分", "v": "score"},
        ]

        # 为每个分类添加筛选器
        for tid in self.CATEGORIES.keys():
            filters[tid] = [
                {"key": "class", "name": "类型", "value": class_vals},
                {"key": "area", "name": "地区", "value": area_vals},
                {"key": "year", "name": "年份", "value": year_vals},
                {"key": "by", "name": "排序", "value": by_vals},
            ]

        return filters

    def fetch(self, url, headers=None, params=None, timeout=10):
        """带域名自动切换的fetch，失败时尝试下一个域名"""
        if not headers:
            headers = self.headers
        original_host = self.host
        # 记录哪些域名已尝试过（避免死循环）
        tried = set()

        for attempt_domain in [self.host] + [d for d in self.domains if d != self.host]:
            if attempt_domain in tried:
                continue
            tried.add(attempt_domain)
            try:
                import urllib.parse as urlparse
                parsed = urlparse.urlparse(url)
                # 替换URL中的域名为当前尝试的域名
                current_url = url
                if parsed.netloc in [d.replace('https://', '') for d in self.domains] or not parsed.netloc:
                    path = parsed.path or '/'
                    query = parsed.query
                    current_url = attempt_domain + path
                    if query:
                        current_url += '?' + query
                resp = super().fetch(current_url, headers=headers, params=params, timeout=timeout)
                if resp.status_code == 200 and len(resp.text) > 100:
                    self.host = attempt_domain
                    return resp
            except Exception as e:
                self.log(f"域名 {attempt_domain} 请求失败: {e}")
                continue
        # 恢复原始host
        self.host = original_host
        return super().fetch(url, headers=headers, params=params, timeout=timeout)

    def getHomeList(self, data):
        """获取首页推荐列表"""
        videos = []
        items = data('article, .module .content .items .item, .movies-list article')
        for item in items.items():
            video_info = self._extract_video_basic(item)
            if video_info:
                videos.append(video_info)
        return videos

    def getVideoList(self, data):
        """获取视频列表"""
        videos = []
        items = data('article, .items article, .content article, .search-results article')
        for item in items.items():
            video_info = self._extract_video_basic(item)
            if video_info:
                videos.append(video_info)
        return videos

    def extractVideoInfo(self, item):
        """兼容性方法，已优化为_extract_video_basic"""
        return self._extract_video_basic(item)

    def getpq(self, text):
        """创建PyQuery对象"""
        try:
            return pq(text)
        except:
            try:
                return pq(text.encode('utf-8'))
            except:
                return pq('')

    def filterSearchResults(self, results, search_key):
        """过滤和排序搜索结果"""
        if not results or not search_key:
            return results

        search_key_lower = search_key.lower().strip()
        search_words = search_key_lower.split()
        scored_results = []

        for result in results:
            title = result.get('vod_name', '').lower()

            # 计算相关性分数
            if search_key_lower == title:
                score = 100
            elif search_key_lower in title:
                score = 80
            elif title.startswith(search_key_lower):
                score = 70
            elif all(word in title for word in search_words):
                score = 60
            else:
                word_matches = sum(1 for word in search_words if word in title)
                if word_matches > 0:
                    score = 30 + (word_matches * 10)
                else:
                    continue

            # 内容类型加分
            if '剧' in search_key_lower and 'tvshows' in result.get('vod_id', ''):
                score += 5
            elif '电影' in search_key_lower and 'movies' in result.get('vod_id', ''):
                score += 5

            scored_results.append((score, result))

        # 排序和过滤
        scored_results.sort(key=lambda x: x[0], reverse=True)
        min_score = 30 if len(search_words) > 1 else 40
        filtered = [result for score, result in scored_results if score >= min_score]

        # 如果结果太少，放宽标准
        if len(filtered) < 3 and len(scored_results) > 3:
            filtered = [result for score, result in scored_results[:10]]

        return filtered

    def filterTVShowsOnly(self, video_list):
        """过滤电视剧分类中的电影内容"""
        if not video_list:
            return video_list

        filtered_videos = []
        movie_keywords = ['/movies/', '/movie/', 'Movie', '电影']
        tvshow_keywords = ['/tvshows/', '/tvshow/', '/seasons/', 'TV', '剧', '季', '集']

        for video in video_list:
            vod_id = video.get('vod_id', '')
            vod_name = video.get('vod_name', '')
            vod_remarks = video.get('vod_remarks', '')

            # 检查是否是电影
            is_movie = any(keyword in vod_id for keyword in movie_keywords[:3])
            if is_movie:
                continue

            # 检查是否是电视剧
            is_tvshow = (any(keyword in vod_id for keyword in tvshow_keywords[:3]) or
                        any(keyword in vod_name + vod_remarks for keyword in tvshow_keywords[3:]))

            if is_tvshow or not is_movie:
                filtered_videos.append(video)

        return filtered_videos

    def getSeasonEpisodes(self, season_links):
        """获取电视剧每个季的集数信息"""
        play_links = []

        try:
            for season in season_links.items():
                season_title = season.text().strip() or '第1季'
                season_url = self._normalize_url(season.attr('href'))

                if not season_url:
                    continue

                try:
                    season_resp = self.fetch(season_url, headers=self.headers)
                    if season_resp.status_code == 200:
                        season_data = self.getpq(season_resp.text)
                        episode_count = self._get_episode_count(season_data, season_resp.text)

                        # 限制集数范围（提高上限以支持长篇动漫）
                        episode_count = min(max(episode_count, 1), 500)

                        # 生成播放链接
                        if episode_count == 1:
                            play_links.append(f"{season_title}${season_url}")
                        else:
                            clean_title = season_title.split('已完結')[0].split('更新')[0].strip()
                            for ep_num in range(1, episode_count + 1):
                                episode_title = f"{clean_title} 第{ep_num}集"
                                episode_url = f"{season_url}?ep={ep_num}"
                                play_links.append(f"{episode_title}${episode_url}")
                    else:
                        play_links.append(f"{season_title}${season_url}")

                except Exception:
                    play_links.append(f"{season_title}${season_url}")

        except Exception:
            pass

        return play_links
