# coding=utf-8
"""
酷狗音乐 TVBox 爬虫（修复版）
- 修复：搜索结果无法跳转详情页（付费歌曲导致detailContent返回空）
- 修复：歌手信息显示"未知"（兼容多API字段）
- 修复：MV hash提取（榜单mvdata数组格式）
- 优化：尝试多音质获取播放链接，即使失败也显示详情页
- 作者：堂主
"""
 
import sys
import re
import json
import base64
import requests
 
sys.path.append('..')
from base.spider import Spider
 
 
class Spider(Spider):
    # ---------- 基础方法 ----------
    def getName(self):
        return "酷狗音乐"
 
    def init(self, extend=""):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16 like Mac OS X) '
                          'AppleWebKit/605.1.15 (KHTML, like Gecko) '
                          'Version/16 Mobile/15E148 Safari/604.1',
            'Referer': 'https://www.kugou.com/',
            'Origin': 'https://www.kugou.com',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept': 'application/json, text/plain, */*',
        })
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except:
            pass
        self.log("酷狗音乐初始化完成")
 
    def destroy(self):
        if hasattr(self, 'session'):
            self.session.close()
 
    # ---------- 首页 ----------
    def homeContent(self, filter):
        classes = [
            {"type_id": "1", "type_name": "热门榜"},
            {"type_id": "2", "type_name": "全球榜"},
            {"type_id": "5", "type_name": "特色榜"},
        ]
        return {"class": classes, "filters": {}}
 
    def homeVideoContent(self):
        return self.categoryContent("1", "1", None, {})
 
    # ---------- 分类/榜单 ----------
    def categoryContent(self, tid, pg, filter, ext):
        if tid.startswith("kugou#"):
            return self._rank_detail(tid, pg)
        else:
            return self._rank_list(tid, pg)
 
    def _rank_list(self, tid, pg):
        if pg != "1":
            return {"list": [], "page": pg, "pagecount": 0, "limit": 0, "total": 0}
        rank_list = self._get_rank_list()
        if not rank_list:
            return {"list": [], "page": pg, "pagecount": 0, "limit": 0, "total": 0}
        classify = int(tid)
        sub_ranks = []
        for item in rank_list:
            c = item.get("classify", 0)
            if classify == 1 and c == 1:
                sub_ranks.append(item)
            elif classify == 2 and (c == 2 or c == 4):
                sub_ranks.append(item)
            elif classify == 5 and c not in (1, 2):
                sub_ranks.append(item)
            elif classify == c:
                sub_ranks.append(item)
        video_list = []
        for item in sub_ranks:
            rank_cid = str(item.get("rank_cid", ""))
            rankname = item.get("rankname", "")
            img = (item.get("imgurl") or "").replace("{size}", "400")
            if rank_cid and rankname:
                video_list.append({
                    "vod_id": f"kugou#{rank_cid}",
                    "vod_name": rankname,
                    "vod_pic": img,
                    "vod_remarks": "酷狗榜单",
                    "vod_tag": "folder",
                })
        return {
            "list": video_list,
            "page": pg, "pagecount": 1,
            "limit": len(video_list), "total": len(video_list)
        }
 
    def _rank_detail(self, tid, pg):
        rank_cid = tid.replace("kugou#", "")
        page = int(pg) if pg else 1
        songs_data = self._get_rank_songs(rank_cid, page=page, pagesize=30)
        songs = songs_data.get("songs", [])
        total = songs_data.get("total", 0)
        if not songs:
            return {"list": [], "page": pg, "pagecount": 0, "limit": 0, "total": 0}
        video_list = []
        for song in songs:
            hash_val = self._extract_best_hash(song)
            if not hash_val:
                continue
            name = self._get_song_name(song)
            singer = self._get_singer_name(song)
            pic = self._get_song_cover(song)
            album_id = self._get_album_id(song)
            mvhash = self._get_mv_hash(song)
            vod_id = f"{hash_val}|{album_id}|{mvhash}|{pic}"
            video_list.append({
                "vod_id": vod_id,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": singer,
            })
        return {
            "list": video_list,
            "page": pg, "pagecount": 9999,
            "limit": len(video_list), "total": total
        }
 
    # ---------- 详情页（核心修复） ----------
    def detailContent(self, ids):
        vod_id = ids[0]
        parts = vod_id.split("|")
        hash_val = parts[0] if len(parts) > 0 else ""
        album_id = parts[1] if len(parts) > 1 else ""
        mvhash_from_id = parts[2] if len(parts) > 2 else ""
        pic_from_id = parts[3] if len(parts) > 3 else ""
 
        if not hash_val:
            return {"list": []}
 
        # 用标准音质获取歌曲基本信息（歌名、歌手、封面、专辑）
        song_info = self._get_song_info(hash_val, album_id)
        if not song_info:
            song_info = self._get_song_info(hash_val, "")
        if not song_info:
            song_info = {}
 
        name = (
            song_info.get("songName")
            or song_info.get("songname")
            or "未知歌曲"
        )
        singer = self._extract_singer_from_info(song_info)
        if not singer or singer == "未知歌手":
            singer = "未知歌手"
        pic = pic_from_id or song_info.get("album_img") or song_info.get("img") or ""
        if pic and "{size}" in pic:
            pic = pic.replace("{size}", "400")
        album = (
            song_info.get("albumName")
            or song_info.get("album_name")
            or ""
        )
        extra = song_info.get("extra") or {}
 
        # 收集所有音质的hash（从song_info里提取）
        mp3_hashes = []
        seen = set()
        h_order = [
            ("无损音质", song_info.get("sqhash")),
            ("无损音质", song_info.get("SQFileHash")),
            ("无损音质", extra.get("sqhash")),
            ("高清音质", song_info.get("320hash")),
            ("高清音质", song_info.get("HQFileHash")),
            ("高清音质", extra.get("320hash")),
            ("标准音质", song_info.get("hash")),
            ("标准音质", song_info.get("FileHash")),
        ]
        for label, h in h_order:
            if h and h not in seen:
                mp3_hashes.append((label, h))
                seen.add(h)
        if not mp3_hashes:
            mp3_hashes.append(("标准音质", hash_val))
 
        # 尝试每个音质，看哪个能获取到播放链接
        mp3_playable = []
        for label, h in mp3_hashes:
            info = self._get_song_info(h, album_id)
            if not info:
                info = self._get_song_info(h, "")
            if info and info.get("status") == 1 and info.get("url"):
                mp3_playable.append((label, h, info.get("url")))
 
        # MV
        mv_hash = mvhash_from_id or song_info.get("mvhash") or song_info.get("MvHash")
        mv_items = []
        if mv_hash:
            mv_urls = self._get_mv_urls(mv_hash)
            quality_map = {"sq": "超清", "rq": "高清", "le": "流畅"}
            for q in ["sq", "rq", "le"]:
                if q in mv_urls:
                    mv_items.append((quality_map[q], mv_urls[q]))
 
        # 构造选集（即使没有可播放的，也要返回详情页，避免TVBox直接返回）
        play_from_list = []
        play_url_list = []
 
        if mp3_playable:
            mp3_episodes = []
            for label, h, _ in mp3_playable:
                mp3_episodes.append(f"{label}${h}|{album_id}")
            play_from_list.append("MP3")
            play_url_list.append("#".join(mp3_episodes))
        elif mp3_hashes:
            # 没有可播放的，也显示选集条目，点击时会提示无法播放
            mp3_episodes = []
            for label, h in mp3_hashes:
                mp3_episodes.append(f"{label}${h}|{album_id}")
            play_from_list.append("MP3")
            play_url_list.append("#".join(mp3_episodes))
 
        if mv_items:
            mv_episodes = []
            for label, url in mv_items:
                mv_episodes.append(f"{label}${url}")
            play_from_list.append("MV")
            play_url_list.append("#".join(mv_episodes))
 
        if not play_from_list:
            # 什么都没有，至少返回基本信息
            play_from_list.append("MP3")
            play_url_list.append(f"暂无资源${hash_val}|{album_id}")
 
        vod_play_from = "$$$".join(play_from_list)
        vod_play_url = "$$$".join(play_url_list)
 
        video_detail = {
            "vod_id": vod_id,
            "vod_name": name,
            "vod_pic": pic,
            "vod_content": f"歌手：{singer}\n专辑：{album}",
            "vod_actor": singer,
            "vod_remarks": f"可播放音质：{len(mp3_playable)}种" + (f" | MV画质：{len(mv_items)}种" if mv_items else ""),
            "vod_play_from": vod_play_from,
            "vod_play_url": vod_play_url,
        }
        return {"list": [video_detail]}
 
    # ---------- 播放地址 ----------
    def playerContent(self, flag, id, vipFlags):
        if flag == "MV":
            url = id
            if "$" in id:
                _, url = id.split("$", 1)
            if not url:
                return {"parse": 0, "url": "", "msg": "MV地址为空"}
            return {
                "parse": 0, "url": url,
                "header": {
                    "User-Agent": self.session.headers["User-Agent"],
                    "Referer": "https://www.kugou.com/"
                }
            }
 
        else:
            play_id = id
            if "$" in id:
                _, play_id = id.split("$", 1)
            parts = play_id.split("|")
            hash_val = parts[0]
            album_id = parts[1] if len(parts) > 1 else ""
            if not hash_val:
                return {"parse": 0, "url": "", "msg": "缺少hash"}
 
            song_info = self._get_song_info(hash_val, album_id)
            if not song_info:
                song_info = self._get_song_info(hash_val, "")
            if not song_info or song_info.get("status") != 1:
                return {"parse": 0, "url": "", "msg": "该歌曲需要付费或暂无播放资源"}
 
            play_url = song_info.get("url") or ""
            if not play_url:
                backup = song_info.get("backup_urls") or song_info.get("backup_url")
                if isinstance(backup, list) and backup:
                    play_url = backup[0]
                elif isinstance(backup, str) and backup:
                    play_url = backup
            if not play_url:
                return {"parse": 0, "url": "", "msg": "无播放链接"}
 
            subs = []
            lrc = self._get_lyric(hash_val)
            if lrc:
                ssa = self._create_ssa_subtitle(lrc)
                if ssa:
                    ssa_b64 = base64.b64encode(ssa.encode('utf-8')).decode('utf-8')
                    subs.append({
                        "name": "歌词",
                        "url": f"data:text/x-ssa;base64,{ssa_b64}",
                        "format": "text/x-ssa",
                        "selected": True
                    })
 
            return {
                "parse": 0, "url": play_url,
                "header": {
                    "User-Agent": self.session.headers["User-Agent"],
                    "Referer": "https://www.kugou.com/"
                },
                "subs": subs
            }
 
    # ---------- 搜索 ----------
    def searchContent(self, key, quick, pg="1"):
        page = int(pg) if pg else 1
        songs = self._search_songs(key, page=page, pagesize=20)
        if not songs:
            return {"list": [], "page": pg, "pagecount": 0, "limit": 0, "total": 0}
        video_list = []
        for song in songs:
            hash_val = self._extract_best_hash(song)
            if not hash_val:
                continue
            name = self._get_song_name(song)
            singer = self._get_singer_name(song)
            pic = self._get_song_cover(song)
            album_id = self._get_album_id(song)
            mvhash = self._get_mv_hash(song)
            vod_id = f"{hash_val}|{album_id}|{mvhash}|{pic}"
            video_list.append({
                "vod_id": vod_id,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": singer,
            })
        return {
            "list": video_list,
            "page": pg, "pagecount": 9999,
            "limit": len(video_list), "total": len(video_list)
        }
 
    # ---------- API 调用 ----------
    def _get_rank_list(self):
        url = "http://mobilecdnbj.kugou.com/api/v3/rank/list"
        params = {
            "version": "9108", "plat": "0", "showtype": "2",
            "parentid": "0", "apiver": "6", "area_code": "1",
            "withsong": "0", "with_res_tag": "0",
        }
        try:
            resp = self.session.get(url, params=params, timeout=15, verify=False)
            return resp.json().get("data", {}).get("info", []) or []
        except Exception as e:
            self.log(f"获取榜单列表失败: {e}")
            return []
 
    def _get_rank_songs(self, rank_id, page=1, pagesize=50):
        url = "http://mobilecdnbj.kugou.com/api/v3/rank/song"
        params = {
            "version": "9108", "ranktype": "0", "plat": "0",
            "pagesize": str(pagesize), "area_code": "1",
            "page": str(page), "rankid": str(rank_id),
            "volid": "35050", "with_res_tag": "0",
        }
        try:
            resp = self.session.get(url, params=params, timeout=15, verify=False)
            data = resp.json()
            return {
                "songs": data.get("data", {}).get("info", []) or [],
                "total": data.get("data", {}).get("total", 0)
            }
        except Exception as e:
            self.log(f"获取榜单歌曲失败: {e}")
            return {"songs": [], "total": 0}
 
    def _search_songs(self, keyword, page=1, pagesize=20):
        url = "http://songsearch.kugou.com/song_search_v2"
        params = {
            "keyword": keyword, "page": page,
            "pagesize": pagesize, "platform": "WebFilter",
            "format": "json",
        }
        try:
            resp = self.session.get(url, params=params, timeout=15, verify=False)
            return resp.json().get("data", {}).get("lists", []) or []
        except Exception as e:
            self.log(f"搜索失败: {e}")
            return []
 
    def _get_song_info(self, hash_val, album_id=""):
        url = "http://m.kugou.com/app/i/getSongInfo.php"
        params = {"hash": hash_val, "cmd": "playInfo"}
        if album_id:
            params["album_id"] = album_id
        try:
            resp = self.session.get(url, params=params, timeout=15, verify=False)
            return resp.json()
        except Exception as e:
            self.log(f"获取歌曲信息失败: {e}")
            return None
 
    def _get_mv_urls(self, mv_hash):
        url = "https://m.kugou.com/app/i/mv.php"
        params = {"cmd": "100", "hash": mv_hash, "ismp3": "1", "ext": "mp4"}
        result = {}
        try:
            resp = self.session.get(url, params=params, timeout=15, verify=False)
            data = resp.json()
            if data.get("status") != 1:
                return result
            mvdata = data.get("mvdata", {}) or {}
            for q in ["sq", "rq", "le"]:
                info = mvdata.get(q)
                if info:
                    downurl = info.get("downurl") or ""
                    if not downurl and info.get("backupdownurl"):
                        downurl = info["backupdownurl"][0]
                    if downurl:
                        result[q] = downurl
            return result
        except Exception as e:
            self.log(f"获取MV失败: {e}")
            return result
 
    def _get_lyric(self, hash_val):
        try:
            search_url = "http://lyrics.kugou.com/search"
            params = {"ver": 1, "man": "yes", "client": "pc", "hash": hash_val}
            resp = self.session.get(search_url, params=params, timeout=10, verify=False)
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return None
            c = candidates[0]
            download_url = "http://lyrics.kugou.com/download"
            params = {
                "ver": 1, "client": "pc",
                "id": c.get("id"),
                "accesskey": c.get("accesskey"),
                "fmt": "lrc", "charset": "utf8"
            }
            resp2 = self.session.get(download_url, params=params, timeout=10, verify=False)
            data2 = resp2.json()
            content = data2.get("content")
            if content:
                return base64.b64decode(content).decode("utf-8")
            return None
        except Exception as e:
            self.log(f"获取歌词失败: {e}")
            return None
 
    # ---------- 辅助函数 ----------
    def _extract_best_hash(self, song):
        order = [
            "sqhash", "SQFileHash", "SuperFileHash",
            "320hash", "HQFileHash",
            "hash", "FileHash", "Hash",
            "ResFileHash",
        ]
        for key in order:
            h = song.get(key)
            if h:
                return h
        return None
 
    def _get_song_cover(self, song):
        cover = (
            song.get("album_sizable_cover")
            or song.get("album_img")
            or song.get("img")
            or song.get("cover")
            or song.get("AlbumImage")
            or song.get("albumImg")
            or song.get("Image")
            or ""
        )
        if cover and "{size}" in cover:
            cover = cover.replace("{size}", "400")
        return cover
 
    def _get_mv_hash(self, song):
        # 优先直接字段
        h = (
            song.get("MvHash")
            or song.get("mvhash")
            or song.get("MVHash")
            or ""
        )
        if h:
            return h
        # 榜单API的mvdata数组格式
        mvdata = song.get("mvdata")
        if mvdata and isinstance(mvdata, list) and len(mvdata) > 0:
            return mvdata[0].get("hash", "")
        return ""
 
    def _get_song_name(self, song):
        return song.get("songname") or song.get("SongName") or "未知歌曲"
 
    def _get_singer_name(self, song):
        authors = song.get("authors")
        if authors and isinstance(authors, list):
            names = "、".join(
                a.get("author_name", "") or a.get("name", "")
                for a in authors
                if a.get("author_name") or a.get("name")
            )
            if names:
                return names
        singers = song.get("Singers")
        if singers and isinstance(singers, list):
            names = "、".join(s.get("name", "") for s in singers if s.get("name"))
            if names:
                return names
        return (
            song.get("singerName")
            or song.get("SingerName")
            or song.get("singer_name")
            or song.get("author_name")
            or "未知歌手"
        )
 
    def _extract_singer_from_info(self, song_info):
        """从getSongInfo返回的数据中提取歌手名"""
        authors = song_info.get("authors")
        if authors and isinstance(authors, list):
            names = "、".join(
                a.get("author_name", "") or a.get("name", "")
                for a in authors
                if a.get("author_name") or a.get("name")
            )
            if names:
                return names
        return (
            song_info.get("singerName")
            or song_info.get("author_name")
            or song_info.get("SingerName")
            or song_info.get("artistName")
            or "未知歌手"
        )
 
    def _get_album_id(self, song):
        return str(
            song.get("album_id")
            or song.get("AlbumID")
            or song.get("albumid")
            or ""
        )
 
    # ---------- SSA 字幕 ----------
    def _create_ssa_subtitle(self, lrc_text):
        lines = []
        pattern = r'\[(\d{2}):(\d{2})\.(\d{2})\](.*)'
        for line in lrc_text.split('\n'):
            match = re.match(pattern, line)
            if match:
                minutes = int(match.group(1))
                seconds = int(match.group(2))
                hundredths = int(match.group(3))
                text = match.group(4).strip()
                total_seconds = minutes * 60 + seconds + hundredths / 100.0
                if text:
                    lines.append({'start': total_seconds, 'text': text})
        if not lines:
            return ""
        ssa_header = """[Script Info]
ScriptType: v4.00+
Collisions: Normal
PlayResX: 1280
PlayResY: 720
Timer: 100.0000
WrapStyle: 0
 
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CENTER,Roboto,60,&H0000FF00,&H00808080,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,2,2,0,0,340,1
Style: TOP,Roboto,55,&H0000FFFF,&H00808080,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,1,2,0,0,200,1
Style: BOTTOM,Roboto,55,&H0000FFFF,&H00808080,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,1,2,0,0,500,1
 
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        def fmt(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            cs = int((seconds * 100) % 100)
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
        events = []
        for i, current in enumerate(lines):
            end = lines[i+1]['start'] if i+1 < len(lines) else current['start'] + 5.0
            events.append(f"Dialogue: 1,{fmt(current['start'])},{fmt(end)},CENTER,,0,0,0,,{current['text']}")
            if i > 0:
                prev = lines[i-1]
                events.append(f"Dialogue: 2,{fmt(current['start'])},{fmt(end)},TOP,,0,0,0,,{prev['text']}")
            if i+1 < len(lines):
                next_line = lines[i+1]
                events.append(f"Dialogue: 3,{fmt(current['start'])},{fmt(end)},BOTTOM,,0,0,0,,{next_line['text']}")
        return ssa_header + "\n".join(events)
 
    def localProxy(self, params):
        return None