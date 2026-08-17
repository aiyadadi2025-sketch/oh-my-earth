  #!/usr/bin/env python3
  # -*- coding: utf-8 -*-

  """
  源名称：看片狂人
  生成方式：AI自动生成（遮天法·临字秘·荒塔扫描）
  """

  import re
  import json
  import time
  import random
  import requests
  from bs4 import BeautifulSoup
  from base.spider import Spider


  class SpiderCustom(Spider):
      # ==================== 基础配置 ====================
      name = "看片狂人"
      base_url = "https://kpkuang.us"
      site_url = "https://kpkuang.us"

      # ==================== 分类配置 ====================
      class_name = ["电影", "连续剧", "综艺", "动漫", "短剧", "纪录片"]
      class_url = ["1", "2", "3", "4", "37", "29"]

      # 筛选器定义
      filter_def = {
          "1": [
              {"key": "cateId", "name": "类型", "value": [
                  {"n": "全部类型", "v": ""},
                  {"n": "动作片", "v": "6"}, {"n": "喜剧片", "v": "7"},
                  {"n": "爱情片", "v": "8"}, {"n": "科幻片", "v": "9"},
                  {"n": "恐怖片", "v": "10"}, {"n": "剧情片", "v": "11"},
                  {"n": "战争片", "v": "12"}
              ]},
              {"key": "area", "name": "地区", "value": [
                  {"n": "全部地区", "v": ""},
                  {"n": "中国大陆", "v": "中国大陆"},
                  {"n": "中国香港", "v": "中国香港"},
                  {"n": "美国", "v": "美国"},
                  {"n": "日本", "v": "日本"},
                  {"n": "韩国", "v": "韩国"},
                  {"n": "英国", "v": "英国"},
                  {"n": "法国", "v": "法国"},
                  {"n": "德国", "v": "德国"},
                  {"n": "印度", "v": "印度"}
              ]},
              {"key": "year", "name": "年份", "value": [
                  {"n": "全部年份", "v": ""},
                  {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
                  {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"},
                  {"n": "2022", "v": "2022"}, {"n": "2021", "v": "2021"},
                  {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"},
                  {"n": "2018", "v": "2018"}, {"n": "2017", "v": "2017"},
                  {"n": "2016", "v": "2016"}, {"n": "2015", "v": "2015"}
              ]},
              {"key": "letter", "name": "字母", "value": [
                  {"n": "全部字母", "v": ""},
                  {"n": "A", "v": "A"}, {"n": "B", "v": "B"}, {"n": "C", "v": "C"},
                  {"n": "D", "v": "D"}, {"n": "E", "v": "E"}, {"n": "F", "v": "F"},
                  {"n": "G", "v": "G"}, {"n": "H", "v": "H"}, {"n": "I", "v": "I"},
                  {"n": "J", "v": "J"}, {"n": "K", "v": "K"}, {"n": "L", "v": "L"},
                  {"n": "M", "v": "M"}, {"n": "N", "v": "N"}, {"n": "O", "v": "O"},
                  {"n": "P", "v": "P"}, {"n": "Q", "v": "Q"}, {"n": "R", "v": "R"},
                  {"n": "S", "v": "S"}, {"n": "T", "v": "T"}, {"n": "U", "v": "U"},
                  {"n": "V", "v": "V"}, {"n": "W", "v": "W"}, {"n": "X", "v": "X"},
                  {"n": "Y", "v": "Y"}, {"n": "Z", "v": "Z"}, {"n": "其他", "v": "0-9"}
              ]},
              {"key": "by", "name": "排序", "value": [
                  {"n": "按时间", "v": "time"},
                  {"n": "按人气", "v": "hits"},
                  {"n": "按评分", "v": "score"}
              ]}
          ],
          "2": [
              {"key": "cateId", "name": "类型", "value": [
                  {"n": "全部类型", "v": ""},
                  {"n": "国产剧", "v": "13"}, {"n": "港剧", "v": "14"},
                  {"n": "日剧", "v": "15"}, {"n": "欧美剧", "v": "16"},
                  {"n": "台剧", "v": "20"}, {"n": "泰剧", "v": "21"},
                  {"n": "韩剧", "v": "23"}, {"n": "海外剧", "v": "30"}
              ]},
              {"key": "area", "name": "地区", "value": [
                  {"n": "全部地区", "v": ""},
                  {"n": "中国大陆", "v": "中国大陆"},
                  {"n": "中国香港", "v": "中国香港"},
                  {"n": "日本", "v": "日本"}, {"n": "韩国", "v": "韩国"},
                  {"n": "美国", "v": "美国"}, {"n": "英国", "v": "英国"},
                  {"n": "法国", "v": "法国"}, {"n": "泰国", "v": "泰国"},
                  {"n": "台湾", "v": "台湾"}
              ]},
              {"key": "year", "name": "年份", "value": [
                  {"n": "全部年份", "v": ""},
                  {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
                  {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"},
                  {"n": "2022", "v": "2022"}, {"n": "2021", "v": "2021"},
                  {"n": "2020", "v": "2020"}
              ]},
              {"key": "letter", "name": "字母", "value": [
                  {"n": "全部字母", "v": ""},
                  {"n": "A", "v": "A"}, {"n": "B", "v": "B"}, {"n": "C", "v": "C"},
                  {"n": "D", "v": "D"}, {"n": "E", "v": "E"}, {"n": "F", "v": "F"},
                  {"n": "G", "v": "G"}, {"n": "H", "v": "H"}, {"n": "I", "v": "I"},
                  {"n": "J", "v": "J"}, {"n": "K", "v": "K"}, {"n": "L", "v": "L"},
                  {"n": "M", "v": "M"}, {"n": "N", "v": "N"}, {"n": "O", "v": "O"},
                  {"n": "P", "v": "P"}, {"n": "Q", "v": "Q"}, {"n": "R", "v": "R"},
                  {"n": "S", "v": "S"}, {"n": "T", "v": "T"}, {"n": "U", "v": "U"},
                  {"n": "V", "v": "V"}, {"n": "W", "v": "W"}, {"n": "X", "v": "X"},
                  {"n": "Y", "v": "Y"}, {"n": "Z", "v": "Z"}, {"n": "其他", "v": "0-9"}
              ]},
              {"key": "by", "name": "排序", "value": [
                  {"n": "按时间", "v": "time"},
                  {"n": "按人气", "v": "hits"},
                  {"n": "按评分", "v": "score"}
              ]}
          ],
          "3": [
              {"key": "cateId", "name": "类型", "value": [
                  {"n": "全部类型", "v": ""},
                  {"n": "大陆综艺", "v": "19"}, {"n": "港台综艺", "v": "22"},
                  {"n": "日韩综艺", "v": "24"}, {"n": "欧美综艺", "v": "25"}
              ]},
              {"key": "area", "name": "地区", "value": [
                  {"n": "全部地区", "v": ""},
                  {"n": "中国大陆", "v": "中国大陆"},
                  {"n": "中国香港", "v": "中国香港"},
                  {"n": "日本", "v": "日本"}, {"n": "韩国", "v": "韩国"},
                  {"n": "美国", "v": "美国"}, {"n": "台湾", "v": "台湾"}
              ]},
              {"key": "year", "name": "年份", "value": [
                  {"n": "全部年份", "v": ""},
                  {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
                  {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"},
                  {"n": "2022", "v": "2022"}
              ]},
              {"key": "letter", "name": "字母", "value": [
                  {"n": "全部字母", "v": ""},
                  {"n": "A-Z", "v": "A-Z"}, {"n": "其他", "v": "0-9"}
              ]},
              {"key": "by", "name": "排序", "value": [
                  {"n": "按时间", "v": "time"},
                  {"n": "按人气", "v": "hits"}
              ]}
          ],
          "4": [
              {"key": "cateId", "name": "类型", "value": [
                  {"n": "全部类型", "v": ""},
                  {"n": "国漫", "v": "28"}, {"n": "日漫", "v": "31"},
                  {"n": "美漫", "v": "32"}
              ]},
              {"key": "area", "name": "地区", "value": [
                  {"n": "全部地区", "v": ""},
                  {"n": "中国大陆", "v": "中国大陆"},
                  {"n": "日本", "v": "日本"}, {"n": "美国", "v": "美国"}
              ]},
              {"key": "year", "name": "年份", "value": [
                  {"n": "全部年份", "v": ""},
                  {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
                  {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}
              ]},
              {"key": "letter", "name": "字母", "value": [
                  {"n": "全部字母", "v": ""},
                  {"n": "A-Z", "v": "A-Z"}, {"n": "其他", "v": "0-9"}
              ]},
              {"key": "by", "name": "排序", "value": [
                  {"n": "按时间", "v": "time"},
                  {"n": "按人气", "v": "hits"}
              ]}
          ],
          "37": [
              {"key": "cateId", "name": "类型", "value": [
                  {"n": "全部类型", "v": ""},
                  {"n": "古装短剧", "v": "33"}, {"n": "都市短剧", "v": "34"},
                  {"n": "悬疑短剧", "v": "35"}
              ]},
              {"key": "year", "name": "年份", "value": [
                  {"n": "全部年份", "v": ""},
                  {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"}
              ]},
              {"key": "by", "name": "排序", "value": [
                  {"n": "按时间", "v": "time"},
                  {"n": "按人气", "v": "hits"}
              ]}
          ],
          "29": [
              {"key": "area", "name": "地区", "value": [
                  {"n": "全部地区", "v": ""},
                  {"n": "中国大陆", "v": "中国大陆"},
                  {"n": "美国", "v": "美国"}, {"n": "日本", "v": "日本"}
              ]},
              {"key": "year", "name": "年份", "value": [
                  {"n": "全部年份", "v": ""},
                  {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"}
              ]},
              {"key": "by", "name": "排序", "value": [
                  {"n": "按时间", "v": "time"},
                  {"n": "按人气", "v": "hits"}
              ]}
          ]
      }

      # ==================== 请求参数 ====================
      headers = {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)
  Chrome/120.0.0.0 Safari/537.36",
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
          "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
          "Referer": "https://kpkuang.us/"
      }
      timeout = 15
      page_size = 36

      # ==================== 工具函数层 ====================

      def _get(self, url, headers=None, params=None):
          """GET请求封装（含异常捕获）"""
          try:
              resp = requests.get(
                  url,
                  headers=headers or self.headers,
                  params=params,
                  timeout=self.timeout
              )
              resp.encoding = "utf-8"
              return resp.text
          except Exception as e:
              print(f"[{self.name}] GET请求异常: {e}")
              return None

      def _fetch_json(self, url, headers=None):
          """请求并解析JSON"""
          html = self._get(url, headers)
          if html:
              try:
                  return json.loads(html)
              except:
                  return None
          return None

      def _random_delay(self):
          """随机延迟，模拟人类浏览"""
          time.sleep(random.uniform(0.3, 1.0))

      def _build_vod_item(self, item):
          """标准化影片条目"""
          vod_id = item.get("id", "")
          vod_name = item.get("name", "")
          vod_pic = item.get("pic", "")
          vod_remarks = item.get("remarks", "")
          return {
              "vod_id": str(vod_id),
              "vod_name": vod_name,
              "vod_pic": vod_pic,
              "vod_remarks": vod_remarks,
              "vod_year": item.get("year", ""),
              "vod_area": item.get("area", ""),
              "vod_actor": item.get("actor", ""),
              "vod_director": item.get("director", ""),
          }

      # ==================== 核心方法实现 ====================

      def homeContent(self, filter=False):
          """首页推荐"""
          result = {"list": [], "filters": self.filter_def if filter else {}}
          try:
              url = f"{self.site_url}/index.php"
              html = self._get(url)
              if not html:
                  return result
              soup = BeautifulSoup(html, "html.parser")

              # 提取分类
              class_list = []
              for i, cn in enumerate(self.class_name):
                  cu = self.class_url[i]
                  class_list.append({"type_id": cu, "type_name": cn})
              result["list"].extend(class_list)

              # 首页推荐区
              for sel in ['.fed-list-home .fed-list-info a', '.level3_ul a',
                          '.uk-switcher li a', '.fed-tabr-info a[href*="voddetail"]']:
                  for a in soup.select(sel):
                      href = a.get("href", "")
                      title = a.get("title", "") or a.get_text(strip=True)
                      pic = a.get("data-original", "") or a.get("data-background", "") or ""
                      if href and "/voddetail/" in href:
                          vid_match = re.search(r"/voddetail/(\d+)", href)
                          if vid_match:
                              vod_id = vid_match.group(1)
                              if not any(v.get("vod_id") == vod_id for v in result["list"]):
                                  result["list"].append(self._build_vod_item({
                                      "id": vod_id, "name": title,
                                      "pic": pic, "remarks": "", "year": "", "area": "",
                                      "actor": "", "director": ""
                                  }))
          except Exception as e:
              print(f"[{self.name}] 首页推荐异常: {e}")
          return result

      def categoryContent(self, tid, pg, filter=False, content=None):
          """分类列表（分页）"""
          result = {
              "list": [],
              "page": int(pg),
              "pagecount": 99,
              "limit": self.page_size,
              "total": 0
          }
          try:
              # build filter URL
              filters = content or {}
              cate = filters.get("cateId", "")
              area = filters.get("area", "")
              year = filters.get("year", "")
              letter = filters.get("letter", "")
              sortby = filters.get("by", "time")

              url = f"{self.base_url}/vodshow/{tid}-{area if area else ''}-{letter if letter else ''}-{year if year else
  ''}-{sortby}-----{pg}---.html"
              if cate:
                  url = url.replace(f"/{tid}-", f"/vodtype/{tid}-{cate}.html", 1) if not pg or pg == "1" else url

              if int(pg) == 1 and not cate:
                  url = f"{self.base_url}/vodtype/{tid}.html"
              elif int(pg) == 1 and cate:
                  url = f"{self.base_url}/vodtype/{tid}-{cate}.html"
              else:
                  url = f"{self.base_url}/vodshow/{tid}-{area if area else ''}-{letter if letter else ''}-{year if year
  else ''}-{sortby}-----{pg}---.html"

              html = self._get(url)
              if not html:
                  return result
              self._random_delay()
              soup = BeautifulSoup(html, "html.parser")

              items = soup.select(".fed-list-info .fed-list-item a, .uk-slider-items li a[href*='voddetail'],
  .fed-week-boxs .fed-list-item a[href*='voddetail']")
              for a in items[:self.page_size]:
                  href = a.get("href", "")
                  title = a.get("title", "") or a.get_text(strip=True)
                  pic = a.get("data-original", "") or a.get("data-background", "") or ""
                  vid_match = re.search(r"/voddetail/(\d+)", href)
                  if not vid_match:
                      continue
                  vod_id = vid_match.group(1)
                  item = self._build_vod_item({"id": vod_id, "name": title, "pic": pic})
                  # 从tooltip或父节点提取信息
                  parent = a.find_parent("div")
                  if parent:
                      info = parent.get_text()
                      year_m = re.search(r'(\d{4})', info)
                      if year_m:
                          item["vod_year"] = year_m.group(1)
                      remarks_m = re.search(r'第(\d+)集', info)
                      if remarks_m:
                          item["vod_remarks"] = f"第{remarks_m.group(1)}集"
                  result["list"].append(item)

              # 尝试提取分页信息
              next_a = soup.select_one("a[href*='page/'+ +str(int(pg)+1)], a[href*='--"+ str(int(pg)+1) + "---']")
              if not next_a:
                  for a in soup.select("a"):
                      if str(int(pg) + 1) in a.get("href", "") and "vodshow" in a.get("href", ""):
                          next_a = a
                          break
              if next_a:
                  result["pagecount"] = int(pg) + 1

          except Exception as e:
              print(f"[{self.name}] 分类列表异常: {e}")
          return result

      def detailContent(self, ids):
          """影片详情"""
          result = []
          try:
              vod_id = ids[0] if isinstance(ids, list) else str(ids)
              if not vod_id:
                  return result

              # 先获取详情页基础信息
              url = f"{self.base_url}/voddetail/{vod_id}.html"
              html = self._get(url)
              if not html:
                  return result
              self._random_delay()
              soup = BeautifulSoup(html, "html.parser")

              # 基本信息
              vod = {"vod_id": vod_id, "vod_year": "", "vod_area": "",
                     "vod_actor": "", "vod_director": "", "vod_content": "",
                     "vod_play_from": "", "vod_play_url": ""}

              # 标题
              title_el = soup.select_one("h1, .fed-detail-title, .uk-h1")
              if title_el:
                  vod["vod_name"] = title_el.get_text(strip=True)
              else:
                  vod["vod_name"] = vod_id

              # 图片
              pic_el = soup.select_one("img[data-original], img.fed-detail-pic, .fed-detail-pic img")
              if pic_el:
                  vod["vod_pic"] = pic_el.get("data-original", "") or pic_el.get("src", "")

              # 简介
              desc_el = soup.select_one(".fed-detail-content, .fed-maokuan, .uk-article-body, .fed-remarks")
              if desc_el:
                  vod["vod_content"] = desc_el.get_text(strip=True)[:500]

              # 年份/地区
              info_text = soup.get_text()
              year_m = re.search(r'(\d{4})', info_text)
              if year_m:
                  vod["vod_year"] = year_m.group(1)
              area_m = re.search(r'(中国大陆|中国香港|美国|日本|韩国|英国|法国|印度|泰国|台湾)', info_text)
              if area_m:
                  vod["vod_area"] = area_m.group(1)
              actor_m = re.search(r'(演员[:：]\s*)([\s\S]*?)(?=<|$)', html)
              if actor_m:
                  vod["vod_actor"] = actor_m.group(2).strip()

              # 播放源和集数
              play_blocks = soup.select(".fed-play-from a, .play-source a, .ui-tab-item a, [class*='tab'] a")
              line_names = []
              line_urls_list = []

              for idx, line in enumerate(play_blocks):
                  line_name = line.get_text(strip=True) or f"线路{idx+1}"
                  line_names.append(line_name)
                  # 提取该播放源下的集数链接
                  line_div = line.find_parent("li") or line.find_parent("div")
                  episodes = []
                  if line_div:
                      ep_links = line_div.select("a[href*='vodplay']")
                      for ep_a in ep_links:
                          ep_href = ep_a.get("href", "")
                          ep_name = ep_a.get_text(strip=True)
                          # 提取播放页URL
                          play_match = re.search(r"/vodplay/(\d+)-(\d+)-(\d+)-(\d+)", ep_href)
                          if play_match:
                              pg_num = play_match.group(4)
                              ep_url =
  f"/vodplay/{play_match.group(1)}-{play_match.group(2)}-{play_match.group(3)}-{pg_num}.html"
                          else:
                              ep_url = ep_href
                          if ep_url and ep_name:
                              episodes.append(f"{ep_name}${ep_url}")
                          elif ep_url:
                              episodes.append(f"第{len(episodes)+1}集${ep_url}")
                  if not episodes:
                      # fallback: 使用主播放链接
                      main_a = soup.select_one("a[href*='vodplay']")
                      if main_a:
                          mp = re.search(r"/vodplay/(\d+)-(\d+)-(\d+)-(\d+)", main_a.get("href", ""))
                          if mp:
                              episodes = [f"正片$/vodplay/{mp.group(1)}-{mp.group(2)}-{mp.group(3)}-{mp.group(4)}.html"]
                  line_urls_list.append("#".join(episodes) if episodes else "暂无播放资源")

              vod["vod_play_from"] = "$$$".join(line_names) if line_names else "默认线路"
              vod["vod_play_url"] = "$$$".join(line_urls_list) if line_urls_list else "正片$/"

              result.append(vod)

          except Exception as e:
              print(f"[{self.name}] 详情获取异常: {e}")
          return result

      def searchContent(self, key, pg="1", filter=False):
          """关键词搜索"""
          result = {"list": [], "page": int(pg), "pagecount": 1, "limit": 20, "total": 0}
          try:
              # 看片狂人搜索页
              url = f"{self.base_url}/vodsearch/{'------------'.replace('0'*12, key[:10])}-------------.html"
              # 标准搜索URL格式
              url = f"{self.base_url}/vodsearch/{key}-------------.html"
              html = self._get(url)
              if not html:
                  return result
              self._random_delay()
              soup = BeautifulSoup(html, "html.parser")

              for a in soup.select("a[href*='voddetail']"):
                  href = a.get("href", "")
                  title = a.get("title", "") or a.get_text(strip=True)
                  pic = a.get("data-original", "") or ""
                  vid_match = re.search(r"/voddetail/(\d+)", href)
                  if vid_match:
                      item = self._build_vod_item({"id": vid_match.group(1), "name": title, "pic": pic})
                      if item["vod_name"] and key in item["vod_name"]:
                          result["list"].append(item)

          except Exception as e:
              print(f"[{self.name}] 搜索异常: {e}")
          return result

      def playerContent(self, flag, id, vipFlags=None):
          """播放地址解析"""
          try:
              # id 可能是 /vodplay/xxx-xxx-xxx-xxx.html 或完整URL
              play_url = id
              if not play_url.startswith("http"):
                  play_url = self.base_url + play_url

              html = self._get(play_url)
              if not html:
                  return {"parse": 1, "url": play_url, "header": {}}

              # 方法1: 从 iframe src 提取 m3u8
              iframe_match = re.search(r'<iframe[^>]+src=["\']([^"\']+?)["\']', html)
              if iframe_match:
                  iframe_src = iframe_match.group(1)
                  if iframe_src.startswith("//"):
                      iframe_src = "https:" + iframe_src
                  # 直接是m3u8或可解析的播放页
                  if ".m3u8" in iframe_src or ".mp4" in iframe_src:
                      return {
                          "parse": 0,
                          "url": iframe_src,
                          "header": {"User-Agent": self.headers["User-Agent"],
                                     "Referer": self.base_url + "/"}
                      }

              # 方法2: 从页面JS中提取播放地址
              playscript = re.search(r'player.*?src\s*:\s*["\']([^"\']+)["\']', html, re.I)
              if playscript:
                  return {
                      "parse": 0,
                      "url": playscript.group(1),
                      "header": {"User-Agent": self.headers["User-Agent"]}
                  }

              # 方法3: 查找所有可能的视频链接
              m3u8_match = re.search(r'(https?://[^"\'\s]+?\.m3u8)', html)
              if m3u8_match:
                  return {
                      "parse": 0,
                      "url": m3u8_match.group(1),
                      "header": {"User-Agent": self.headers["User-Agent"],
                                 "Referer": play_url}
                  }

              # 兜底
              return {
                  "parse": 1,
                  "url": play_url,
                  "header": {"User-Agent": self.headers["User-Agent"]}
              }

          except Exception as e:
              print(f"[{self.name}] 播放解析异常: {e}")
              return {"parse": 1, "url": id, "header": {}}