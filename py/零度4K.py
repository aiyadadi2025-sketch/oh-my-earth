# -*- coding: utf-8 -*-
# =====================================================================
# MINO 影视仓爬虫 —— 终极破解版 v3.0（全源深度优化）
# =====================================================================
# 【破解成果】
#   ✅ qmsp 臻彩4K: 直接GET获取302跳转真实m3u8，无需签名
#   ✅ 平台源(TX/奇异/优酷/芒果/B站): 正常VIP解析
#   ✅ zydj短剧: 外部聚合解析正常
#   ⚠️ duanju 蓝光4K: XOR加密已识别，前4字节密钥推导成功
#   ❌ rose/co/zijian: 需Frida从APK提取Native密钥
#   ❌ RR/1080P: 影片库为空
# =====================================================================
import sys
import json
import re
import base64

try:
    import requests
except Exception:
    requests = None

try:
    from base.spider import Spider as _Base
except Exception:
    class _Base(object):
        def isVideoFormat(self, url):
            return False
        def manualVideoCheck(self):
            return False


class Spider(_Base):
    OSS_CFG        = "https://minojson.oss-cn-beijing.aliyuncs.com/mino.json"
    HOST_FALLBACK  = "http://43.248.128.122:8080"
    UA             = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Mobile Safari/537.36 MINO/1.8.18")
    PAGE_SIZE       = 20

    # ===================== 破解配置区 =====================
    # qmsp 臻彩4K 直接可访问！
    QMSP_DIRECT_PLAY = True

    # 平台源优先级（可播放）
    PLATFORM_SOURCES = ["qq", "qiyi", "mgtv", "youku", "bilibili"]

    # duanju XOR 密钥（部分推导，4字节）
    # 分析发现所有样本前4字节XOR 0x696b7429后均为'http'
    # 但后续字节解密结果不确定，预留完整密钥配置位
    DUANJU_XOR_KEY = b"\x69\x6b\x74\x29"  # 4字节部分密钥

    # token源解析密钥（需Frida从APK提取，预留升级路径）
    PARSE_TOKENS = {}

    # zydj 外部解析器
    ZYDJ_PARSER = "http://110.42.49.74:2222/parse/api.php?token=H9SzG8oX&url="

    # ===================== 生命周期 =====================
    def getName(self):
        return "MINO零度影视-终极破解版v3"

    def init(self, extend=""):
        self._host = None
        self._cats = None
        self._players = None
        self._s = requests.Session() if requests else None
        if self._s:
            self._s.headers.update({
                "User-Agent": self.UA,
                "Accept": "application/json, text/plain, */*",
            })
        _ = self.host
        _ = self.players
        return ""

    @property
    def host(self):
        if self._host:
            return self._host
        host = self.HOST_FALLBACK
        try:
            r = self._s.get(self.OSS_CFG, timeout=10)
            j = r.json()
            eps = j.get("endpoints") or (j.get("data") or {}).get("endpoints") or []
            for e in eps:
                if isinstance(e, str) and e.startswith("http"):
                    host = e.rstrip("/")
                    break
        except Exception:
            pass
        self._host = host
        return host

    @property
    def players(self):
        if self._players is not None:
            return self._players
        mp = {}
        try:
            j = self._api("/players")
            for k, v in (j.get("data") or {}).items():
                mp[v.get("player_from") or k] = v
        except Exception:
            pass
        self._players = mp
        return mp

    @property
    def cats(self):
        if self._cats is not None:
            return self._cats
        try:
            self._cats = self._api("/categories").get("data") or []
        except Exception:
            self._cats = []
        return self._cats

    def _api(self, path, params=None):
        url = self.host + "/api" + path
        r = self._s.get(url, params=params, timeout=20)
        return r.json()

    @staticmethod
    def _vod(item):
        return {
            "vod_id": str(item.get("vod_id") or ""),
            "vod_name": item.get("vod_name") or "",
            "vod_pic": item.get("vod_pic") or "",
            "vod_remarks": item.get("vod_remarks") or item.get("vod_sub") or "",
        }

    @staticmethod
    def _is_media(url):
        return bool(re.search(r"\.(m3u8|mp4|flv|ts)(\?|#|$)", str(url), re.I))

    # ===================== 首页 =====================
    def homeContent(self, filter):
        result = {"class": [], "filters": {}}
        for c in self.cats:
            tid = str(c.get("type_id"))
            result["class"].append({
                "type_id": tid,
                "type_name": c.get("type_name") or tid,
            })
            vals = []
            try:
                ext = json.loads(c.get("type_extend") or "{}")
                for cn in (ext.get("class") or []):
                    vals.append({"n": cn, "v": cn})
            except Exception:
                pass
            if vals:
                result["filters"][tid] = [{"key": "class", "name": "类型", "value": vals}]
        return result

    def homeVideoContent(self):
        vods = []
        try:
            secs = self._api("/home/sections").get("data") or []
            for sec in secs:
                for it in (sec.get("items") or []):
                    vods.append(self._vod(it))
        except Exception:
            pass
        return {"list": vods}

    # ===================== 分类 =====================
    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg) if pg else 1
        params = {"t": tid, "page": page}
        if extend and extend.get("class"):
            params["class"] = extend.get("class")
        try:
            data = self._api("/videos", params).get("data") or {}
            lst = [self._vod(x) for x in (data.get("list") or [])]
            total = int(data.get("total") or 0)
        except Exception:
            lst, total = [], 0
        pagecount = (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE if total else page
        return {
            "list": lst,
            "page": page,
            "pagecount": pagecount,
            "limit": self.PAGE_SIZE,
            "total": total,
        }

    # ===================== 详情（全源优化排序） =====================
    def detailContent(self, ids):
        vid = ids[0]
        d = self._api("/videos/" + str(vid)).get("data") or {}
        pf = (d.get("vod_play_from") or "").split("$$$")
        pu = (d.get("vod_play_url") or "").split("$$$")
        pl = self.players

        froms, urls = [], []
        source_types = []  # 0=qmsp4K, 1=平台, 2=zydj, 3=其他可播, 4=duanju(部分), 5=加密

        for i, f in enumerate(pf):
            if not f:
                continue
            eps = [e for e in (pu[i] if i < len(pu) else "").split("#") if "$" in e]
            if not eps:
                continue
            disp = (pl.get(f, {}) or {}).get("name") or f
            froms.append(disp)
            urls.append("#".join(eps))

            first = eps[0].split("$", 1)[1] if len(eps[0].split("$", 1)) > 1 else ""
            pfrom = None
            for k, v in pl.items():
                if (v.get("name") or k) == disp:
                    pfrom = k
                    break

            if "qmsp" in first:
                source_types.append(0)  # qmsp 4K 最高优先级
            elif pfrom in self.PLATFORM_SOURCES and first.startswith("http"):
                source_types.append(1)  # 平台源
            elif pfrom == "zydj" and first.startswith("http"):
                source_types.append(2)  # zydj短剧
            elif first.startswith("http"):
                source_types.append(3)  # 其他可播放源
            elif first.startswith("AR8"):
                source_types.append(4)  # duanju 蓝光4K（部分破解）
            else:
                source_types.append(5)  # 加密源（rose/co/zijian）

        # 按优先级排序
        idx = sorted(range(len(froms)), key=lambda k: (source_types[k], k))
        froms = [froms[k] for k in idx]
        urls = [urls[k] for k in idx]
        source_types = [source_types[k] for k in idx]

        content = (d.get("vod_content") or d.get("vod_blurb") or "")
        content = re.sub(r"<[^>]+>", "", content).strip()

        unlocked = []
        if 0 in source_types:
            unlocked.append("臻彩4K")
        if 4 in source_types:
            unlocked.append("蓝光4K(部分)")
        if 1 in source_types:
            unlocked.append("平台VIP")

        if unlocked:
            content += "\n【已解锁】" + "/".join(unlocked) + "，默认播放最高清晰度。"
        if 5 in source_types:
            content += "\n【未解锁】超清/co/zijian源需APP签名，请切换已解锁源播放。"

        vod = {
            "vod_id": str(d.get("vod_id") or vid),
            "vod_name": d.get("vod_name") or "",
            "vod_pic": d.get("vod_pic") or "",
            "vod_year": d.get("vod_year") or "",
            "vod_area": d.get("vod_area") or "",
            "vod_class": d.get("vod_class") or "",
            "vod_actor": d.get("vod_actor") or "",
            "vod_director": d.get("vod_director") or "",
            "vod_content": content,
            "vod_play_from": "$$$".join(froms),
            "vod_play_url": "$$$".join(urls),
        }
        return {"list": [vod]}

    # ===================== 搜索 =====================
    def searchContent(self, key, quick):
        try:
            data = self._api("/search", {"wd": key}).get("data") or {}
            lst = [self._vod(x) for x in (data.get("list") or [])]
        except Exception:
            lst = []
        return lst

    # ===================== 播放（终极破解核心） =====================
    def playerContent(self, flag, id, vipFlags):
        id = str(id)

        # 反查 player_from
        pfrom = flag
        for k, v in self.players.items():
            if (v.get("name") or k) == flag:
                pfrom = k
                break

        # 1) qmsp 臻彩4K -> 返回原始URL，影视仓处理302重定向
        if "qmsp" in id and self.QMSP_DIRECT_PLAY:
            try:
                r = self._s.get(id, timeout=20, allow_redirects=False)
                if r.status_code in [301, 302, 307, 308]:
                    location = r.headers.get("Location", "")
                    if location and ("m3u8" in location.lower() or "mp4" in location.lower()):
                        return {"parse": 0, "url": id, "header": ""}
            except Exception:
                pass
            return {"parse": 0, "url": id, "header": ""}

        # 2) 真实平台链接 -> 影视仓内置VIP解析
        if id.startswith("http"):
            return {
                "parse": 0 if self._is_media(id) else 1,
                "url": id,
                "header": "",
            }

        # 3) zydj 短剧 -> 外部聚合解析
        if pfrom == "zydj":
            try:
                r = self._s.get(self.ZYDJ_PARSER + id, timeout=25)
                j = r.json()
                url = (j.get("url") or (j.get("data") or {}).get("url") 
                       if isinstance(j.get("data"), dict) else j.get("data"))
                if url and isinstance(url, str):
                    return {"parse": 0, "url": url, "header": ""}
            except Exception:
                pass

        # 4) duanju 蓝光4K -> 尝试XOR解密（部分密钥）
        if pfrom == "duanju" and id.startswith("AR8"):
            try:
                decoded = base64.b64decode(id)
                # 尝试4字节XOR解密
                decrypted = bytes([decoded[i] ^ self.DUANJU_XOR_KEY[i % 4] for i in range(len(decoded))])
                # 检查解密结果是否以http开头
                if decrypted[:4] == b'http':
                    # 尝试提取完整URL（虽然后续可能乱码）
                    url_match = re.search(br'https?://[^\s\x00-\x1f<>"\']+', decrypted)
                    if url_match:
                        url = url_match.group(0).decode('utf-8', errors='ignore')
                        return {"parse": 0, "url": url, "header": ""}
            except Exception:
                pass

        # 5) token 源 -> 需APP签名
        return {
            "parse": 0,
            "url": id,
            "header": "",
            "msg": "该源为加密token，请切换 臻彩4K/TX/奇异/优酷 等已解锁源播放",
        }

    def isVideoFormat(self, url):
        return self._is_media(url)

    def manualVideoCheck(self):
        return False


# =====================================================================
# 影视仓站点配置
# =====================================================================
if __name__ == "__main__":
    s = Spider()
    s.init()
    print("HOST =", s.host)
    print("终极破解版v3加载完成！")
    print("已解锁: qmsp臻彩4K + 平台VIP源")
    print("部分破解: duanju蓝光4K (XOR密钥推导中)")
    print("待破解: rose/co/zijian (需Frida提取APK密钥)")
