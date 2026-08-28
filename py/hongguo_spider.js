/**
 * 红果短剧 TVBox 爬虫
 * 站点: https://hongguoduanju.com/
 * 适配: TVBox / OK影视 / 影视Pro
 */

var rule = {
    title: '红果短剧',
    host: 'https://hongguoduanju.com',
    url: '/category?cate=fyclass&page=fypage',
    searchUrl: '',
    searchable: 0,
    quickSearch: 0,
    filterable: 1,
    filter: 'hongguo_filter.json',
    class_name: '首页推荐',
    class_url: 'home',
    headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    },
    timeout: 10000,
    play_parse: true,
    lazy: $js.toString(() => {
        let parts = input.split('||');
        let series_id = parts[1] || '';
        let vid = parts[2] || '';

        if (!series_id || !vid) {
            input = { json: {} };
            return;
        }

        let playUrl = `https://hongguoduanju.com/player/${series_id}/${vid}`;
        let html = req(playUrl, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
        });

        let match = html.match(/_ROUTER_DATA\s*=\s*(\{.+?\});/);
        if (!match) {
            input = { json: {} };
            return;
        }

        try {
            let data = JSON.parse(match[1]);
            let pp = data.loaderData && data.loaderData['player_(series_id)/(vid)/page'];
            if (!pp || !pp.isSuccess) {
                input = { json: {} };
                return;
            }

            let vpi = pp.video_player_info || {};
            let mainUrl = vpi.main_url || '';
            if (!mainUrl) {
                input = { json: {} };
                return;
            }

            input = {
                url: mainUrl,
                header: {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Referer': 'https://hongguoduanju.com/',
                },
                jx: 0,
            };
        } catch (e) {
            input = { json: {} };
        }
    }),
    推荐: $js.toString(() => {
        let html = req('https://hongguoduanju.com/', {
            headers: { 'User-Agent': rule.headers['User-Agent'] }
        });
        let match = html.match(/_ROUTER_DATA\s*=\s*(\{.+?\});/);
        if (!match) return;

        let data = JSON.parse(match[1]);
        let page = data.loaderData && data.loaderData.page;
        if (!page || !page.isSuccess) return;

        let results = [];
        (page.bannerList || []).forEach(function (item) {
            results.push({
                vod_id: item.series_id + '||' + '',
                vod_name: item.series_name || '',
                vod_pic: item.series_cover || '',
                vod_remarks: '',
                vod_year: '',
            });
        });
        (page.mBannerList || []).forEach(function (item) {
            results.push({
                vod_id: item.series_id + '||' + '',
                vod_name: item.series_name || '',
                vod_pic: item.title_link_pc || item.series_cover || '',
                vod_remarks: '',
                vod_year: '',
            });
        });

        VODS = results;
    }),
    一级: $js.toString(() => {
        let cid = MY_CATE || '';
        let page = MY_PAGE || 1;
        let filter = MY_FL || {};

        let list = [];
        if (cid === 'home') {
            // 首页推荐 - 从分类页获取
            let html = req('https://hongguoduanju.com/category', {
                headers: { 'User-Agent': rule.headers['User-Agent'] }
            });
            let match = html.match(/_ROUTER_DATA\s*=\s*(\{.+?\});/);
            if (match) {
                let data = JSON.parse(match[1]);
                let cp = data.loaderData && data.loaderData.category_page;
                if (cp && cp.recommendList) {
                    list = cp.recommendList;
                }
            }
        } else {
            // 分类筛选 - 使用cate参数
            let el = filter.el || cid;
            let html = req(`https://hongguoduanju.com/category?cate=${el}&page=${page}`, {
                headers: { 'User-Agent': rule.headers['User-Agent'] }
            });
            let match = html.match(/_ROUTER_DATA\s*=\s*(\{.+?\});/);
            if (match) {
                let data = JSON.parse(match[1]);
                let cp = data.loaderData && data.loaderData.category_page;
                if (cp && cp.recommendList) {
                    list = cp.recommendList;
                }
                // 分页信息
                let pag = cp && cp.pagination || {};
                MY_PAGE = page;
                if (pag.totalPages) {
                    // 设置总页数
                }
            }
        }

        VODLIST = list.map(function (item) {
            return {
                vod_id: item.series_id + '||' + (item.vid_list && item.vid_list[0] || ''),
                vod_name: item.series_name || '',
                vod_pic: item.series_cover || '',
                vod_remarks: '共' + (item.episode_cnt || 0) + '集',
                vod_year: '',
            };
        });
    }),
    二级: $js.toString(() => {
        let series_id = MY_ID.split('||')[0];
        let html = req(`https://hongguoduanju.com/detail?series_id=${series_id}`, {
            headers: { 'User-Agent': rule.headers['User-Agent'] }
        });

        let match = html.match(/_ROUTER_DATA\s*=\s*(\{.+?\});/);
        if (!match) return;

        let data = JSON.parse(match[1]);
        let dp = data.loaderData && data.loaderData.detail_page;
        if (!dp || !dp.isSuccess) return;

        let sd = dp.seriesDetail || {};
        let vidList = sd.vid_list || [];

        VOD = {
            vod_id: sd.series_id || series_id,
            vod_name: sd.series_name || '',
            vod_pic: sd.series_cover || '',
            vod_content: sd.series_intro || '',
            vod_remarks: '共' + (sd.episode_cnt || 0) + '集 | ' + (sd.accessible_episode_cnt || 0) + '集免费',
            vod_year: '',
            vod_actor: '',
            vod_director: '',
            vod_play_from: '红果短剧',
            vod_play_url: vidList.map(function (vid, idx) {
                return '第' + (idx + 1) + '集||' + sd.series_id + '||' + vid;
            }).join('#'),
        };
    }),
}
