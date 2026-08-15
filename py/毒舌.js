// 刁民制作，仅供测试，请于24小时测试完毕删除
// ================================================================
// 毒蛇电影 爬虫 - TVBox/影视仓/OK影视(FongMi) drpy2 ES模块格式
// 网站: https://www.dushe3.app/
// ================================================================
import cheerio from 'assets://js/lib/cheerio.min.js';

// ===== 站点配置 =====
const appConfig = {
    siteName: "毒蛇电影",
    siteUrl: "https://www.dushe3.app"
};
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

// 备用域名列表 (网站换域名时自动尝试)
const fallbackDomains = [
    "https://www.dushe3.app",
    "https://www.dushe.app",
    "https://dushe3.app",
    "https://www.dushe4.app",
    "https://www.dushe5.app"
];

// 全局状态
let cdnCookie = "";
let searchToken = "";
let inited = false;

// ===== SHA1 计算 (优先用CryptoJS, 纯JS兜底) =====
function sha1_hex(str) {
    // 优先使用环境内置的CryptoJS (OK影视/FongMi内置)
    try {
        if (typeof CryptoJS !== 'undefined' && CryptoJS.SHA1) {
            return CryptoJS.SHA1(str).toString();
        }
    } catch (e) {}

    // 纯JS实现兜底
    function rol(v, s) { return ((v << s) | (v >>> (32 - s))) & 0xFFFFFFFF; }
    function hex(v) {
        var s = '';
        for (var i = 7; i >= 0; i--) s += '0123456789abcdef'.charAt((v >>> (i * 4)) & 0x0F);
        return s;
    }
    var utf8 = unescape(encodeURIComponent(str));
    var len = utf8.length;
    var numBlocks = ((len + 8) >> 6) + 1;
    var blks = new Array(numBlocks * 16);
    for (var i = 0; i < blks.length; i++) blks[i] = 0;
    for (i = 0; i < len; i++) blks[i >> 2] |= utf8.charCodeAt(i) << (24 - (i % 4) * 8);
    blks[len >> 2] |= 0x80 << (24 - (len % 4) * 8);
    blks[numBlocks * 16 - 1] = len * 8;
    var w = new Array(80);
    var a = 0x67452301, b = 0xEFCDAB89, c = 0x98BADCFE, d = 0x10325476, e = 0xC3D2E1F0;
    for (var blk = 0; blk < numBlocks; blk++) {
        for (i = 0; i < 16; i++) w[i] = blks[blk * 16 + i];
        for (i = 16; i < 80; i++) w[i] = rol(w[i-3] ^ w[i-8] ^ w[i-14] ^ w[i-16], 1);
        var aa = a, bb = b, cc = c, dd = d, ee = e;
        for (i = 0; i < 80; i++) {
            var f, k;
            if (i < 20)      { f = (bb & cc) | (~bb & dd);             k = 0x5A827999; }
            else if (i < 40) { f = bb ^ cc ^ dd;                       k = 0x6ED9EBA1; }
            else if (i < 60) { f = (bb & cc) | (bb & dd) | (cc & dd);  k = 0x8F1BBCDC; }
            else             { f = bb ^ cc ^ dd;                       k = 0xCA62C1D6; }
            var tmp = (rol(aa, 5) + f + ee + k + w[i]) & 0xFFFFFFFF;
            ee = dd; dd = cc; cc = rol(bb, 30); bb = aa; aa = tmp;
        }
        a = (a + aa) & 0xFFFFFFFF; b = (b + bb) & 0xFFFFFFFF;
        c = (c + cc) & 0xFFFFFFFF; d = (d + dd) & 0xFFFFFFFF;
        e = (e + ee) & 0xFFFFFFFF;
    }
    return hex(a) + hex(b) + hex(c) + hex(d) + hex(e);
}

// ===== 计算CDN防护Cookie =====
function computeCdnCookie(html) {
    try {
        var m = html.match(/a0_0x2a54=\['([A-Fa-f0-9]{40})'/);
        if (!m) return null;
        var c = m[1];
        var n1 = parseInt(c[0], 16);
        for (var i = 0; i < 500000; i++) {
            var hashHex = sha1_hex(c + i.toString());
            var byte1 = parseInt(hashHex.substr(n1 * 2, 2), 16);
            var byte2 = parseInt(hashHex.substr((n1 + 1) * 2, 2), 16);
            if (byte1 === 0xb0 && byte2 === 0x0b) {
                return 'cdndefend_js_cookie=' + c + i.toString();
            }
        }
    } catch (e) {}
    return null;
}

// ===== 带CDN防护的请求 (参照夏天影院: 直接req, 遇CDN再处理) =====
// 关键: 每次遇到CDN挑战都必须从当前页面重新计算cookie, 旧cookie不能复用
async function fetchUrl(url, retries) {
    retries = retries || 8;
    for (var attempt = 0; attempt < retries; attempt++) {
        try {
            var headers = {
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": appConfig.siteUrl
            };
            if (cdnCookie) headers["Cookie"] = cdnCookie;

            var resp = await req(url, { method: "GET", headers: headers });
            var html = resp.content || "";

            // 检查是否CDN挑战页 - 每次都从当前页面重新计算cookie
            if (html.indexOf("cdndefend") >= 0 || html.indexOf("verifying your browser") >= 0) {
                var cookie = computeCdnCookie(html);
                if (cookie) {
                    cdnCookie = cookie;
                }
                continue;
            }

            if (html.length < 500) {
                continue;
            }

            return html;
        } catch (e) {}
    }
    return "";
}

// ===== 从页面HTML中实时提取动态域名 =====
function extractDomainsFromHtml(html) {
    var domains = [];
    var matches = html.match(/https?:\/\/(?:www\.)?dushe\d*\.(?:app|com|cc|net)/gi);
    if (matches) {
        matches.forEach(function(d) {
            if (domains.indexOf(d) === -1) domains.push(d);
        });
    }
    matches = html.match(/https?:\/\/(?:dl\.)?kekedy\.app/gi);
    if (matches) {
        matches.forEach(function(d) {
            if (domains.indexOf(d) === -1) domains.push(d);
        });
    }
    return domains;
}

// ===== 工具函数 =====
function fixUrl(u) {
    if (!u) return '';
    if (u.startsWith('http')) return u;
    if (u.startsWith('//')) return 'https:' + u;
    if (u.startsWith('/')) return appConfig.siteUrl + u;
    return u;
}

// ===== 初始化 (动态域名抓取 + CDN防护 + 搜索Token) =====
async function init(ext) {
    console.log("初始化爬虫:", appConfig.siteName);
    if (inited) return;
    inited = true;

    var allDomains = fallbackDomains.slice();
    var initPages = ["/channel/1.html", "/channel/2.html", "/channel/6.html", "/"];

    for (var i = 0; i < allDomains.length; i++) {
        try {
            appConfig.siteUrl = allDomains[i];
            cdnCookie = "";

            for (var p = 0; p < initPages.length; p++) {
                var html = await fetchUrl(appConfig.siteUrl + initPages[p], 4);

                if (html.indexOf("cdndefend") >= 0) continue;
                if (html.length < 500) continue;

                if (html.indexOf("/detail/") !== -1 || html.indexOf("/channel/") !== -1) {
                    var tokenMatch = html.match(/name="t"\s+value="([^"]+)"/);
                    if (tokenMatch) searchToken = tokenMatch[1];

                    // 实时抓取动态域名, 加入备用列表头部
                    var newDomains = extractDomainsFromHtml(html);
                    newDomains.forEach(function(d) {
                        if (allDomains.indexOf(d) === -1) {
                            allDomains.unshift(d);
                        }
                    });

                    console.log("域名可用:", appConfig.siteUrl);
                    return;
                }
            }
        } catch (e) {
            console.error("域名尝试失败:", allDomains[i], e.message);
        }
    }

    appConfig.siteUrl = fallbackDomains[0];
    console.error("所有备用域名均不可用, 使用默认:", appConfig.siteUrl);
}

// ===== 分类列表 =====
const classList = [
    { type_id: "1", type_name: "电影" },
    { type_id: "2", type_name: "连续剧" },
    { type_id: "3", type_name: "动漫" },
    { type_id: "4", type_name: "综艺纪录" },
    { type_id: "6", type_name: "短剧" }
];

// ===== 筛选器 (参照夏天影院模板: commonFilters 共享) =====
function getAreaFilter() {
    return {
        "key": "area", "name": "地区", "value": [
            { "n": "全部", "v": "" },
            { "n": "大陆", "v": "中国大陆" },
            { "n": "香港", "v": "中国香港" },
            { "n": "台湾", "v": "中国台湾" },
            { "n": "美国", "v": "美国" },
            { "n": "日本", "v": "日本" },
            { "n": "韩国", "v": "韩国" },
            { "n": "英国", "v": "英国" },
            { "n": "法国", "v": "法国" },
            { "n": "德国", "v": "德国" },
            { "n": "印度", "v": "印度" },
            { "n": "泰国", "v": "泰国" },
            { "n": "其他", "v": "其他" }
        ]
    };
}

function getYearFilter() {
    let years = [{ "n": "全部", "v": "" }];
    const currentYear = new Date().getFullYear();
    for (let y = currentYear; y >= 2010; y--) {
        years.push({ "n": String(y), "v": String(y) });
    }
    return { "key": "year", "name": "年份", "value": years };
}

function getLangFilter() {
    return {
        "key": "lang", "name": "语言", "value": [
            { "n": "全部", "v": "" },
            { "n": "国语", "v": "国语" },
            { "n": "粤语", "v": "粤语" },
            { "n": "英语", "v": "英语" },
            { "n": "日语", "v": "日语" },
            { "n": "韩语", "v": "韩语" },
            { "n": "法语", "v": "法语" },
            { "n": "其他", "v": "其他" }
        ]
    };
}

function getTypeFilter() {
    return {
        "key": "type", "name": "类型", "value": [
            { "n": "全部", "v": "" },
            { "n": "剧情", "v": "剧情" },
            { "n": "喜剧", "v": "喜剧" },
            { "n": "动作", "v": "动作" },
            { "n": "爱情", "v": "爱情" },
            { "n": "科幻", "v": "科幻" },
            { "n": "悬疑", "v": "悬疑" },
            { "n": "惊悚", "v": "惊悚" },
            { "n": "恐怖", "v": "恐怖" },
            { "n": "犯罪", "v": "犯罪" },
            { "n": "奇幻", "v": "奇幻" },
            { "n": "冒险", "v": "冒险" },
            { "n": "战争", "v": "战争" },
            { "n": "历史", "v": "历史" },
            { "n": "古装", "v": "古装" },
            { "n": "武侠", "v": "武侠" },
            { "n": "家庭", "v": "家庭" },
            { "n": "动画", "v": "动画" },
            { "n": "短片", "v": "短片" }
        ]
    };
}

function getSortFilter() {
    return {
        "key": "by", "name": "排序", "value": [
            { "n": "综合", "v": "" },
            { "n": "最新", "v": "2" },
            { "n": "最热", "v": "3" },
            { "n": "评分", "v": "4" }
        ]
    };
}

const commonFilters = [getTypeFilter(), getAreaFilter(), getLangFilter(), getYearFilter(), getSortFilter()];

const myFilters = {};
classList.forEach(item => {
    myFilters[item.type_id] = commonFilters;
});

// ===== 解析列表页 (分类页 a.v-item + 搜索页 a.search-result-item) =====
function parseListHtml(html) {
    const $ = cheerio.load(html);
    let list = [];
    let vodIds = {};

    // 分类页: a.v-item 卡片
    $("a.v-item").each(function () {
        let vod_id = $(this).attr("href") || "";
        if (vod_id.indexOf("/detail/") === -1) return;
        if (vodIds[vod_id]) return;

        // 标题: .v-item-title (排除隐藏的"可可影视"占位)
        let vod_name = "";
        $(this).find(".v-item-title").each(function () {
            let t = $(this).text().trim();
            let style = $(this).attr("style") || "";
            if (t && t.indexOf("可可影视") === -1 && t.indexOf("kekys") === -1) {
                if (style.indexOf("none") === -1) {
                    vod_name = t;
                }
            }
        });
        if (!vod_name) {
            $(this).find(".v-item-title").each(function () {
                let t = $(this).text().trim();
                if (t && t.indexOf("可可影视") === -1 && t.indexOf("kekys") === -1) {
                    vod_name = t;
                }
            });
        }
        if (!vod_name || vod_name.indexOf("可可影视") !== -1) return;

        // 封面图: 取data-original (非placeholder)
        let vod_pic = "";
        $(this).find("img").each(function () {
            let src = $(this).attr("data-original") || "";
            if (src && src.indexOf("placeholder") === -1 &&
                src.indexOf("cyscyy") === -1 && src.indexOf("logo_") === -1) {
                vod_pic = src;
            }
        });
        vod_pic = fixUrl(vod_pic);

        // 备注: .v-item-bottom
        let vod_remarks = ($(this).find(".v-item-bottom").text() || "").trim().replace(/\s+/g, " ");
        let rating = ($(this).find(".v-item-top-left").text() || "").trim();
        if (rating && vod_remarks) vod_remarks = vod_remarks + " | " + rating;
        else if (rating) vod_remarks = rating;

        vodIds[vod_id] = true;
        list.push({ vod_id, vod_name, vod_pic, vod_remarks });
    });

    // 搜索页: a.search-result-item (结构与分类页完全不同)
    $("a.search-result-item").each(function () {
        let vod_id = $(this).attr("href") || "";
        if (vod_id.indexOf("/detail/") === -1) return;
        if (vodIds[vod_id]) return;

        // 标题: .search-result-item-main 内第一个文本节点
        let vod_name = "";
        let $main = $(this).find(".search-result-item-main");
        if ($main.length > 0) {
            // 取第一个非空文本块 (标题在年份/地区/类型之前)
            $main.contents().each(function () {
                if (this.type === 'text') {
                    let t = $(this).text().trim();
                    if (t && !t.match(/^\d{4}\/.*/)) {
                        vod_name = t;
                        return false;
                    }
                }
                if (this.type === 'tag' && this.name === 'div') {
                    let t = $(this).text().trim();
                    if (t && !t.match(/^\d{4}\/.*/) && t.indexOf("/") === -1) {
                        vod_name = t;
                        return false;
                    }
                }
            });
        }
        if (!vod_name) {
            // 回退: 取整个main文本的第一行
            let fullText = ($main.text() || "").trim();
            let lines = fullText.split(/[\n\r]+/);
            if (lines.length > 0) vod_name = lines[0].trim();
        }
        if (!vod_name || vod_name.indexOf("可可影视") !== -1) return;

        // 封面图: .search-result-item-side img
        let vod_pic = "";
        let $side = $(this).find(".search-result-item-side img");
        if ($side.length > 0) {
            let src = $side.attr("data-original") || $side.attr("src") || "";
            if (src && src.indexOf("placeholder") === -1 && src.indexOf("cyscyy") === -1) {
                vod_pic = src;
            }
        }
        vod_pic = fixUrl(vod_pic);

        // 备注: 从main文本中提取年份/地区
        let vod_remarks = "";
        let mainText = ($main.text() || "").trim();
        let yearMatch = mainText.match(/(\d{4})/);
        if (yearMatch) vod_remarks = yearMatch[1];

        vodIds[vod_id] = true;
        list.push({ vod_id, vod_name, vod_pic, vod_remarks });
    });

    // 分页: 查找最大页码
    let pagecount = 1;
    $("a[href*='/channel/'], a[href*='/show/']").each(function () {
        let href = $(this).attr("href") || "";
        let m = href.match(/-(\d+)\.html$/);
        if (m) {
            let p = parseInt(m[1]);
            if (p > pagecount) pagecount = p;
        }
    });
    if (list.length > 0 && pagecount === 1) pagecount = 999;

    return { list, pagecount };
}

// ===== 首页 (参照夏天影院: 直接req首页) =====
async function home(filter) {
    let list = [];
    try {
        let html = await fetchUrl(appConfig.siteUrl + "/channel/2.html");
        const result = parseListHtml(html);
        list = result.list.slice(0, 30);
    } catch (e) {
        console.error("首页推荐获取失败:", e.message);
    }

    return JSON.stringify({
        class: classList,
        filters: myFilters,
        list: list
    });
}

// ===== 分类列表 URL构建 =====
// 无筛选: /channel/{tid}.html (第1页), /channel/{tid}-{page}.html (第2页+)
// 有筛选: /show/{tid}-{type}-{area}-{lang}-{year}-{by}-{page}.html
function buildCategoryUrl(tid, pg, extend) {
    extend = extend || {};
    let area = extend.area ? encodeURIComponent(extend.area) : '';
    let year = extend.year || '';
    let lang = extend.lang ? encodeURIComponent(extend.lang) : '';
    let type = extend.type ? encodeURIComponent(extend.type) : '';
    let by = extend.by || '';

    if (area || year || lang || type || by) {
        let url = `/show/${tid}-${type}-${area}-${lang}-${year}-${by}-${pg}.html`;
        return appConfig.siteUrl + url;
    } else {
        if (pg > 1) {
            return `${appConfig.siteUrl}/channel/${tid}-${pg}.html`;
        } else {
            return `${appConfig.siteUrl}/channel/${tid}.html`;
        }
    }
}

async function category(tid, pg, filter, extend) {
    pg = pg || 1;
    extend = extend || {};

    let url = buildCategoryUrl(tid, pg, extend);

    try {
        let html = await fetchUrl(url);
        const result = parseListHtml(html);
        return JSON.stringify(result);
    } catch (e) {
        console.error("分类列表获取失败:", e.message);
        return JSON.stringify({ list: [], pagecount: 0 });
    }
}

// ===== 搜索 (参照夏天影院: 直接req搜索URL) =====
async function search(wd, quick, page) {
    page = page || 1;
    if (page > 1) return JSON.stringify({ list: [], pagecount: 0 });

    try {
        // 确保有搜索Token
        if (!searchToken) {
            var tokenPages = ["/channel/1.html", "/channel/2.html", "/channel/6.html", "/"];
            for (var p = 0; p < tokenPages.length; p++) {
                let pageHtml = await fetchUrl(appConfig.siteUrl + tokenPages[p]);
                let tokenMatch = pageHtml.match(/name="t"\s+value="([^"]+)"/);
                if (tokenMatch) {
                    searchToken = tokenMatch[1];
                    break;
                }
            }
        }

        // 搜索URL: /search?k=keyword&t=token
        var searchUrl = appConfig.siteUrl + "/search?k=" + encodeURIComponent(wd);
        if (searchToken) {
            searchUrl += "&t=" + encodeURIComponent(searchToken);
        }

        var html = await fetchUrl(searchUrl);
        var result = parseListHtml(html);

        // 如果带token无结果, 尝试不带token
        if (result.list.length === 0) {
            var html2 = await fetchUrl(appConfig.siteUrl + "/search?k=" + encodeURIComponent(wd));
            var result2 = parseListHtml(html2);
            if (result2.list.length > 0) {
                return JSON.stringify({ list: result2.list, pagecount: 1 });
            }
        }

        return JSON.stringify({ list: result.list, pagecount: 1 });
    } catch (e) {
        console.error("搜索失败:", e.message);
        return JSON.stringify({ list: [], pagecount: 0 });
    }
}

// ===== 详情页 =====
async function detail(id) {
    try {
        const html = await fetchUrl(appConfig.siteUrl + id);
        const $ = cheerio.load(html);

        // 标题: .detail-pic img alt
        let vod_name = "";
        let $detailPic = $(".detail-pic img");
        if ($detailPic.length > 0) {
            vod_name = $detailPic.attr("alt") || "";
        }
        if (!vod_name) {
            let titleMatch = html.match(/<title>([^<-]+)/);
            vod_name = titleMatch ? titleMatch[1].trim() : "";
        }
        if (!vod_name) {
            vod_name = $("h1").first().text().trim();
        }

        // 封面图
        let vod_pic = "";
        if ($detailPic.length > 0) {
            let src = $detailPic.attr("data-original") || $detailPic.attr("src") || "";
            if (src.indexOf("placeholder") === -1 && src.indexOf("cyscyy") === -1) {
                vod_pic = fixUrl(src);
            }
        }

        // 详情信息
        let vod_director = "", vod_actor = "", vod_year = "", vod_area = "";
        let vod_class = "", vod_remarks = "", vod_content = "", vod_lang = "";

        $(".detail-info-row").each(function () {
            let label = $(this).find(".detail-info-row-side").text().trim().replace(":", "");
            let value = $(this).find(".detail-info-row-main").text().trim();

            if (label.indexOf("导演") !== -1) vod_director = value;
            else if (label.indexOf("演员") !== -1 || label.indexOf("主演") !== -1) vod_actor = value;
            else if (label.indexOf("首映") !== -1 || label.indexOf("年份") !== -1 || label.indexOf("时间") !== -1) vod_year = value;
            else if (label.indexOf("地区") !== -1) vod_area = value;
            else if (label.indexOf("类型") !== -1) vod_class = value;
            else if (label.indexOf("语言") !== -1) vod_lang = value;
            else if (label.indexOf("备注") !== -1 || label.indexOf("更新") !== -1) vod_remarks = value;
        });

        // 简介
        let $intro = $(".detail-desc, .detail-introduction-content, .video-info-content");
        if ($intro.length > 0) {
            vod_content = $intro.first().text().replace(/简介[：:]\s*/, "").trim();
        }

        // 播放源和剧集
        let lines = [];
        let playlists = [];
        let seenEpisodes = new Set();

        // source-item 是tab选择器, episode-list 是对应的剧集列表
        let sourceNames = [];
        $("a.source-item").each(function () {
            let label = $(this).find(".source-item-label").text().trim();
            let sublabel = $(this).find(".source-item-sublabel").text().trim();
            let lineName = sublabel ? (label + "(" + sublabel + ")") : label;
            if (lineName) sourceNames.push(lineName);
        });

        // 遍历每个 episode-list
        $(".episode-list").each(function (index) {
            let episodes = [];
            let epArray = [];

            $(this).find("a.episode-item").each(function () {
                let name = $(this).text().trim();
                let href = $(this).attr("href") || "";
                if (name && href && href.indexOf("/play/") !== -1) {
                    let episodeKey = `${name}_${href}`;
                    if (!seenEpisodes.has(episodeKey)) {
                        seenEpisodes.add(episodeKey);
                        epArray.push({ name, href });
                    }
                }
            });

            epArray.sort((a, b) => {
                let numA = parseInt((a.name.match(/第(\d+)[集话]/i) || [0, 0])[1] || (a.name.match(/(\d+)/) || [0, 0])[1] || 0);
                let numB = parseInt((b.name.match(/第(\d+)[集话]/i) || [0, 0])[1] || (b.name.match(/(\d+)/) || [0, 0])[1] || 0);
                return numA - numB;
            });

            epArray.forEach(ep => {
                episodes.push(`${ep.name}$${ep.href}`);
            });

            if (episodes.length > 0) {
                let lineName = (index < sourceNames.length) ? sourceNames[index] : "线路" + (index + 1);
                lines.push(lineName);
                playlists.push(episodes);
            }
        });

        // 备用: 全局查找播放链接
        if (lines.length === 0) {
            let episodes = [];
            let epArray = [];

            $("a.episode-item, a[href*='/play/']").each(function () {
                let name = $(this).text().trim();
                let href = $(this).attr("href") || "";
                if (name && href && href.indexOf("/play/") !== -1 && name !== "立即播放") {
                    let episodeKey = `${name}_${href}`;
                    if (!seenEpisodes.has(episodeKey)) {
                        seenEpisodes.add(episodeKey);
                        epArray.push({ name, href });
                    }
                }
            });

            epArray.forEach(ep => {
                episodes.push(`${ep.name}$${ep.href}`);
            });

            if (episodes.length > 0) {
                lines.push("默认");
                playlists.push(episodes);
            }
        }

        if (lines.length === 0) {
            lines.push("默认");
            playlists.push([`暂无播放地址$${id}`]);
        }

        const { vod_play_from, vod_play_url } = buildVodPlayData(lines, playlists);

        return JSON.stringify({
            list: [{
                vod_id: id,
                vod_name,
                vod_pic,
                vod_actor,
                vod_director,
                vod_remarks,
                vod_year,
                vod_area,
                vod_content,
                vod_class,
                vod_play_from,
                vod_play_url
            }]
        });
    } catch (error) {
        console.error(`解析详情页异常 [ID: ${id}]:`, error);
        return JSON.stringify({ list: [] });
    }
}

function buildVodPlayData(lines, playlists) {
    const processedPlaylists = playlists.map(eps => eps.join('#'));
    return {
        vod_play_from: lines.filter(Boolean).join('$$$'),
        vod_play_url: processedPlaylists.join('$$$')
    };
}

// ===== 播放 =====
async function play(flag, id, flags) {
    try {
        let playUrl = id;
        if (playUrl.indexOf("http") < 0) {
            playUrl = appConfig.siteUrl + playUrl;
        }

        const html = await fetchUrl(playUrl);

        // 1. 直接匹配 m3u8 直链
        let m3u8Match = html.match(/https?:\/\/[^"'\s\\]+\.m3u8[^"'\s\\]*/);
        if (m3u8Match) {
            return JSON.stringify({
                parse: 0,
                header: { "User-Agent": UA, "Referer": appConfig.siteUrl },
                url: m3u8Match[0]
            });
        }

        // 2. 匹配转义后的 m3u8
        m3u8Match = html.match(/https?:\/\/[^"'\s]+\\.m3u8[^"'\s\\]*/);
        if (m3u8Match) {
            return JSON.stringify({
                parse: 0,
                header: { "User-Agent": UA, "Referer": appConfig.siteUrl },
                url: m3u8Match[0].replace(/\\/g, '')
            });
        }

        // 3. playSource.src
        let srcMatch = html.match(/playSource\s*[\[=]\s*\{[^}]*src\s*:\s*"([^"]+)"/);
        if (srcMatch && srcMatch[1]) {
            return JSON.stringify({
                parse: 0,
                header: { "User-Agent": UA, "Referer": appConfig.siteUrl },
                url: srcMatch[1].replace(/\\/g, '')
            });
        }

        // 4. mp4 直链
        let mp4Match = html.match(/https?:\/\/[^"'\s\\]+\.mp4[^"'\s\\]*/);
        if (mp4Match) {
            return JSON.stringify({
                parse: 0,
                header: { "User-Agent": UA, "Referer": appConfig.siteUrl },
                url: mp4Match[0]
            });
        }

        // 5. player_aaaa (maccms标准)
        let playerMatch = html.match(/player_aaaa\s*=\s*(\{[\s\S]+?\})\s*<\/script>/);
        if (!playerMatch) {
            playerMatch = html.match(/player_aaaa\s*=\s*(\{[^}]+\})/);
        }
        if (playerMatch) {
            try {
                let playerData = JSON.parse(playerMatch[1]);
                let url = playerData.url || "";
                if (url) {
                    let encrypt = playerData.encrypt || 0;
                    if (encrypt === 2) {
                        try { url = decodeURIComponent(atob(url)); } catch (e) {}
                    } else if (encrypt === 1) {
                        try { url = decodeURIComponent(url); } catch (e) {}
                    }
                    if (url.indexOf(".m3u8") !== -1 || url.indexOf(".mp4") !== -1) {
                        return JSON.stringify({
                            parse: 0,
                            header: { "User-Agent": UA, "Referer": appConfig.siteUrl },
                            url: url
                        });
                    }
                    if (url.startsWith("http")) {
                        return JSON.stringify({
                            parse: 1,
                            header: { "User-Agent": UA, "Referer": appConfig.siteUrl },
                            url: url
                        });
                    }
                }
            } catch (e) {}
        }

        // 6. iframe
        const $ = cheerio.load(html);
        let iframeSrc = $("iframe").attr("src");
        if (iframeSrc) {
            return JSON.stringify({
                parse: 1,
                header: { "User-Agent": UA, "Referer": appConfig.siteUrl },
                url: fixUrl(iframeSrc)
            });
        }

        // 7. 最终: TVBox内置嗅探
        return JSON.stringify({
            parse: 1,
            header: { "User-Agent": UA, "Referer": appConfig.siteUrl },
            url: playUrl
        });
    } catch (e) {
        console.error("播放解析失败:", e.message);
        return JSON.stringify({ parse: 0, url: "" });
    }
}

// ===== 导出模块 (参照夏天影院模板) =====
export default {
    init,
    home,
    category,
    detail,
    search,
    play
};
