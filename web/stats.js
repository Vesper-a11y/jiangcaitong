'use strict';
/* ================= 数据台 = 独立文件（2026-11-13，0.52.1 正式版起） =================
 *
 * 用户 2026-11-13：「把控制台拆出来发正式版吧，不要让控制台的令牌流入正式版」。
 *
 * 这一整页（本文件 + index.html 里那一段 + stats.css）**只在测试版的包里**：
 * build_apk.py 的 stage_assets 打正式版时根本不复制这三个东西 ——
 * 不是「装进去了但藏起来」，是包里没有。
 *
 * 🔴 为什么必须拆出来：`STATS_KEY` 是打开 `/stats` 的钥匙。以前它在 app.js 里，
 *    靠「正式版抹成空串 + STATS_ON=false 不显示」两道闸，任何一道写错就把运营
 *    数据的钥匙白送出去（2026-11-10 真漏过一次）。现在正式版的包里连这个文件都
 *    没有，钥匙没有地方可待 —— 判据是「包里有没有这个文件」，不是「那一行是不是空的」。
 *
 * 网页版（bridge.py 直接发 web/）也有这个文件，那边靠 statsOn() 判断渠道关掉。
 * 正式版 APK 里它不存在，app.js 靠 `typeof statsLoad === 'function'` 判空。
 */
/* ================= 数据台（0.49.0 v4，只有测试版） =================
 *
 * 用户 2026-11-07：「能不能在测试版给我加个数据台，让我能够直接看网站的数据，
 * 还有咱们 APP 的日活这种」。
 *
 * ## 数字从哪来
 * Worker 的 `/stats`（见 worker/index.js）：Cloudflare 自己的访问统计
 * （每天请求 / 浏览 / 独立 IP）+ 「检查更新」的请求数 + 下载次数 + 热门页面。
 * 🔴 **不是**匿名上报的内容 —— 上报那条线是「收得到、读不出」，这里一个字节都没碰。
 *
 * ## 为什么日活要看「检查更新」
 * 上报里没有设备标识（那是故意的），所以算不出真正的「多少人打开过」。
 * 唯一能反映「还在用」的信号就是每台设备都会打的 version.json：
 * 一台设备一天可能查几次，所以它**偏高**，是个上限，不是人数。
 *
 * ## 只在测试版
 * `STATS_ON` 由 APP_CHANNEL 决定：正式版里这一行和这一页都不存在。
 * 数字本身不敏感，但学校工具的运营数据没道理跟着发给每个人。
 *
 * 🔴 钥匙 `STATS_KEY` 在包里的**位置**是这里，但它不是安全边界 ——
 *    谁拿到安装包谁就有钥匙。所以两条规矩（2026-11-10 定的）：
 *    ① **正式版打包时把它抹空**（build_apk.py 的 stage_assets，渠道不是 beta 就替换成 ''）。
 *       正式版根本不显示这一页，留着就是白送一把能读运营数字的钥匙。
 *    ② 它已经跟着 0.49.0 正式版和 v7 发出去过一轮，2026-11-10 换过一次钥匙
 *       （旧的当场作废）；以后再往包里带，只许带在测试版里。
 *    换钥匙：python deploy/set_stats_secrets.py --rotate，然后同步改这里。
 */
/* 🔴 这一页问的是「我现在是不是测试版」，不是一个启动时算好的常量：
   本文件比 app.js 先执行，而 APP_CHANNEL 是 app.js 里的 const ——
   在这里直接读它就是 TDZ（ReferenceError），整页连白屏都算不上，直接不执行。
   写成函数，函数体在调用时才求值，那时候 app.js 早就跑完了。 */
function statsOn() {
  return typeof APP_CHANNEL !== 'undefined' && APP_CHANNEL === 'beta';
}
const STATS_API = 'https://hobson.cc/stats';
// 🔴 这一行**永远留空**。`mobile/` 是公开仓库（CNB 上匿名可 clone），
//    写进来的钥匙等于贴到公告栏上 —— 2026-11-10 就是这么漏的。
//    打包时由 build_apk.py 的 inject_stats_key 从工作区的 deploy/stats_key.txt
//    （gitignore）读出来塞进**测试版**的包里；正式版一律抹空。
//    所以在浏览器里直接开这页会看到 403 —— 那不是坏了，是源码里本来就没有钥匙。
const STATS_KEY = '';
const STATS = { state: 'idle', at: 0, err: '', data: null };

/** 万位以上折成「1.2 万」—— 这一页是给人看的，不是给人数的。 */
function statsNum(n) {
  const v = Number(n) || 0;
  if (v < 10000) return String(v);
  return (v / 10000).toFixed(1).replace(/\.0$/, '') + ' 万';
}

/** 表格里用的原数（不压成「万」）。
 *
 * 大字和合计要的是「大概多少」，压成 1.5 万正好；但一行一天的表比的是
 * 「哪天多、哪天少」，同一列里一行写「1.2 万」、下面几行写 1500，
 * 既没法一眼比大小，也看不出到底多少 —— 那一列就白摆了。
 */
function statsRaw(n) {
  return String(Math.round(Number(n) || 0));
}

/** 文件名的「人话」（2026-11-15）。
 *
 * 用户原话：「底下的这些什么 jiangcaitong.apk 77 次，/campus/ 69 次这些，
 * 你改成具体代表什么不就行了……为什么不直接改成各版本下载次数」。
 * 他说得对：这一页是给人看的，文件名和路径是给机器看的。
 *
 * 🔴 认不出来的就原样返回，不要编一个「其他」。编出来的名字会让人以为
 *    我们知道那是什么，而某一个陌生的包名往往正是要查的东西。
 */
function statsFileLabel(f) {
  const name = String(f || '');
  const v = (re) => {
    const m = name.match(re);
    return m ? (m[1] || '') : null;
  };
  let vv = v(/^jiangcaitong-(\d+(?:\.\d+)*)\.apk$/i);
  if (vv !== null) return '酱菜通 ' + vv + '（安卓）';
  vv = v(/^wanshitong-(\d+(?:\.\d+)*)\.apk$/i);
  if (vv !== null) return '万事通 ' + vv + '（安卓）';
  vv = v(/^campus-login-(\d+(?:\.\d+)*)\.exe$/i);
  if (vv !== null) return '校园网自动登录 ' + vv + '（电脑）';
  if (/^jiangcaitong\.apk$/i.test(name)) return '酱菜通（安卓）';
  if (/^wanshitong\.apk$/i.test(name)) return '万事通（安卓）';
  return name.replace(/\.(apk|exe|hap)$/i, '');
}

/** 路径的「人话」。规则同上：认识的翻，不认识的原样给。 */
function statsPathLabel(p) {
  const path = String(p || '');
  const exact = {
    '/': '网站首页',
    '/jiangcaitong/': '安卓版下载页',
    '/jiangcaitong/beta/': '测试版页面',
    '/jiangcaitong/history.html': '安卓版版本历史',
    '/campus/': '校园网自动登录',
    '/wanshitong/': '万事通',
    '/help/': '使用说明',
    '/history.html': '版本历史',
    '/ios/': '苹果版',
    '/iosapp/': '苹果版安装页',
  };
  if (exact[path]) return exact[path];
  if (path.indexOf('/dl/') === 0) return '安装包直链';
  return path;
}

/**
 * 口径。这一段**必须留在页面上**：这几个数都会被单独截图传播，
 * 而「1 台」和「728 次」放在一起，不说清楚就成了自相矛盾。
 *
 * 0.50.0 用户连着问了三个问题，都是「这个数是真的吗」，所以这里逐条写死：
 *  ① 「来访」到底数的是什么（用户问「今天看网页那个是真的打开网页吗」）
 *  ② 「下载」算不算国内线路、算不算续传（用户问「是真下载量吗 / 国内线路算不算」）
 *  ③ 设备数为什么前几天是 0（用户看到「表里完全没数据」）
 * 另外两条：日期怎么切、独立 IP 为什么跟别的不一样。
 */
function statsNoteHtml() {
  return '<p class="stats-note">' +
    '口径：日界按北京时间算。<br>' +
    '「来访」是从站外点进来看网页的次数 —— 同一个人点站内几个链接、' +
    '刷新页面，都只算一次。所以它比「到底打开了多少次网页」要少；' +
    '装成 App 之后在里面看的内容，一次都不进这个数。<br>' +
    '「在用」是每天报到一次的设备台数 —— 每天换一个新号码，跟手机、账号都没关系；' +
    '只有装了这一版及以后的才报，所以它比「检查更新」小。<br>' +
    '「下载」是从我们网站下一个文件的次数。手机上走国内线路自动更新、' +
    '从那边下的那部分不在这里；断点续传会把一个包算成好几次。' +
    '所以它既不是装机台数，也不能当完整的下载量看。<br>' +
    '独立 IP 是按国际时间切的（比北京时间晚 8 小时），' +
    '而且校园网是一整个出口，真实人数只会比它多。' +
    '</p>';
}

async function statsLoad(force) {
  if (!statsOn()) return;
  if (STATS.state === 'loading') return;
  // 一分钟内不重复打：来回切页不该每次都去问一遍
  if (!force && STATS.state === 'ok' && Date.now() - STATS.at < 60 * 1000) return;
  STATS.state = 'loading';
  STATS.err = '';
  statsPaint();
  try {
    // 🔴 钥匙走**请求头**，不走 query（2026-11-10 晚上改的，用户拍的板）。
    //    放在地址里会跟着三处一起外流：浏览器历史、Referer、Cloudflare 的请求日志。
    //    头不会。
    //    早先担心「自定义头触发预检、APK 里是 file:// 起点会出岔子」—— 那个理由
    //    已经过期了：页面现在由 WebViewAssetLoader 以 `https://app.local` 发出，
    //    是正常的安全来源，Worker 的 /stats 也早就会回 OPTIONS 204
    //    （`Access-Control-Allow-Headers` 里带着 x-hyt-key）。
    //    🔴 钥匙为空时**不发这个头**：正式版里 STATS_KEY 是空串，发一个空头
    //       等于自报「这儿有个字段」，而且会让预检白跑一趟。
    const r = await fetch(STATS_API + '?d=7', {
      headers: STATS_KEY ? { 'x-hyt-key': STATS_KEY } : {},
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    if (!d || !d.ok) throw new Error('接口回了个空');
    STATS.data = d;
    STATS.state = 'ok';
    STATS.at = Date.now();
  } catch (e) {
    // 🔴 失败就说失败，不拿上一次的数字冒充现在 —— 那一页全是数字，
    //    看上去像真的才是最坏的。
    STATS.state = 'err';
    STATS.err = String((e && e.message) || e);
  }
  statsPaint();
}

function statsPaint() {
  const box = $('statsBody');
  if (!box || !statsOn()) return;

  if (STATS.state === 'idle' || STATS.state === 'loading') {
    box.innerHTML = '<div class="card"><div class="stats-loading">正在取数…</div></div>';
    return;
  }
  if (STATS.state === 'err') {
    box.innerHTML =
      '<div class="card"><div class="stats-err">这次没取到数据，过一会儿再试。' +
      '<em>' + esc(STATS.err || '') + '</em></div></div>' + statsNoteHtml();
    return;
  }

  const d = STATS.data;
  const days = d.days || [];
  const today = days.length ? days[days.length - 1] : { pv: 0, uv: 0, upd: 0, dl: 0, req: 0 };
  const tot = d.tot || {};

  let html = '<div class="card">' +
    '<div class="stats-hero">' +
      // 这一格的口径是 Cloudflare 的 visits，**不是**「打开了几次网页」：
      // 从站外点进来才算一次，站内点链接、刷新都不重复计。所以标签写「来访」，
      // 下面那段口径里再补一句「比真实打开次数少」。写「看网页」会让人拿它
      // 跟后台的 Page views 对，一对比就觉得我们少算了 —— 那是两个东西。
      '<div><b>' + statsNum(today.pv) + '</b><span>今天来访</span></div>' +
      // 0.49.0 v8：这一格原来是「今天日活（估）」= 检查更新的**请求**次数。
      // 现在换成真数（每天一个新随机串，一台一天只报一次），
      // 但它是「新版本的在用设备」—— 老版本不上报，所以这个数会明显小于
      // 检查更新次数。两个数都给，别让「1 台」被读成「没人用」。
      // 🔴 dev 是 null 时显示「—」：那是「这次没数出来」，不是「0 台」。
      //    两者说成一样的话，就又犯了「取不到 ≠ 没有」。
      '<div><b>' + (today.dev == null ? '—' : statsNum(today.dev)) +
        '</b><span>今天在用（台）</span></div>' +
      '<div><b>' + statsNum(today.dl) + '</b><span>今天下载</span></div>' +
    '</div>' +
    '<div class="stats-sub">近 7 天合计：请求 ' + statsNum(tot.req) +
      ' · 来访 ' + statsNum(tot.pv) + ' · 独立 IP ' + statsNum(tot.uv) + '</div>' +
    '<div class="stats-sub">检查更新共 ' + statsNum(tot.upd) +
      ' 次（含还没升级的）</div>' +
    '</div>';

  // ---- 近 7 天，一天一行 ----
  // 用户 2026-11-15：「能不能改进一下，变得清晰全面一些，我觉得应该还能看更多数据」。
  // 原来这一块只有「每天在用设备」一件事，而且是一条条长条 —— 一天一行只读得到一个数。
  // 现在一张表四个数：来访 / 在用 / 下载 / 请求。
  // 🔴 不再用长条：柱子只是把数字画一遍，而这一页要的是「哪天多、哪天少」，
  //    四个数并排摆着比四组柱子好读，也不用为每组挑一个不同的最大值。
  if (days.length) {
    const rows = days.slice().reverse().map((x, i) => {
      const dev = (x.dev == null) ? '—' : String(x.dev);
      return '<div class="drow' + (i === 0 ? ' today' : '') + '">' +
        '<span class="d">' + esc(String(x.d || '').slice(5)) +
          (i === 0 ? '<em>今天</em>' : '') + '</span>' +
        // 四列一律原数：同一列里混进一个「1.2 万」就没法比大小了（见 statsRaw）。
        '<b>' + statsRaw(x.pv) + '</b><b>' + dev + '</b>' +
        '<b>' + statsRaw(x.dl) + '</b><b class="dim">' + statsRaw(x.req) + '</b>' +
      '</div>';
    }).join('');
    html += '<div class="card">' +
      '<div class="dhead"><span>日期</span><span>来访</span><span>在用</span>' +
        '<span>下载</span><span>请求</span></div>' + rows +
      // 前几天全是 0 的时候要说清是「那时候还没有这个数」，不是「没人用」。
      (days.slice(0, -1).every((x) => !(Number(x.dev) > 0))
        ? '<div class="stats-sub" style="padding:6px 16px 12px">' +
          '「在用」是这一版才开始记的，之前那些天确实没有，不是丢了。</div>'
        : '') +
      (d.devBad ? '<div class="stats-sub" style="padding:0 16px 12px">' +
        '有几天没数出来（显示成「—」），那是没数到，不是没有设备</div>' : '') +
      '</div>';
  }

  // ---- 各版本下载 ----
  // 用户 2026-11-15：「底下的这些什么 jiangcaitong.apk 77 次，你改成具体代表什么
  // 不就行了，写成这样一点也不清晰，为什么不直接改成各版本下载次数」。
  // ⇒ 文件名翻译成人话：哪个 App、哪个版本、什么平台。
  // 🔴 翻译不了的文件名照原样显示（不能吞掉）—— 认不出来是一回事，
  //    假装没有就是另一回事。
  const files = (d.files || []).map((x) => {
    const f = String(x.f || '');
    return { n: Number(x.n) || 0, name: statsFileLabel(f), raw: f };
  });
  if (files.length) {
    const sum = files.reduce((a, b) => a + b.n, 0);
    html += '<div class="card">' +
      '<div class="stats-sub" style="padding:12px 16px 0">各版本下载次数</div>' +
      files.map((x) => '<div class="drow2">' +
        '<span class="k">' + esc(x.name) + '</span>' +
        '<b>' + statsNum(x.n) + ' 次</b></div>').join('') +
      '<div class="stats-sub" style="padding:2px 16px 12px">' +
        '这 7 天从我们网站下了 ' + statsNum(sum) + ' 次（含断点续传，一个包可能算好几次）' +
      '</div></div>';
  }

  // ---- 各地址访问 ----
  // 同一件事：`/campus/` 这种路径用户看不懂，直接写「校园网工具」。
  const top = (d.top || []).filter((x) => {
    const p = String(x.p || '');
    if (!p) return false;
    // 数据台自己和对外的清单文件不算「页面」—— 混进来只会占掉一行。
    if (p.indexOf('version.json') >= 0 || p.indexOf('/stats') === 0) return false;
    return true;
  }).slice(0, 8);
  if (top.length) {
    html += '<div class="card">' +
      '<div class="stats-sub" style="padding:12px 16px 0">各地址访问次数</div>' +
      top.map((x) => '<div class="drow2">' +
        '<span class="k">' + esc(statsPathLabel(String(x.p || ''))) + '</span>' +
        '<b>' + statsNum(x.n) + ' 次</b></div>').join('') +
      '<div class="stats-sub" style="padding:2px 16px 12px">' +
        '按路径数，同一个页面里的图片、脚本不算</div></div>';
  }

  box.innerHTML = html + statsNoteHtml();
}

/* 入口和两个按钮的接线。放在这里而不是 app.js：这几颗元素只在本页存在，
   接线跟着这一页走。（app.js 里用 `typeof statsWire === 'function'` 调它，
   正式版里这个文件没有，那一句就是空转。） */
function statsWire() {
  const b = $('statsBtn');
  if (!b) return;                       // 正式版里没有这颗按钮
  b.hidden = !statsOn();
  b.addEventListener('click', () => switchTab('stats'));
  $('statsBack').addEventListener('click', () => switchTab('me'));
  $('statsRefresh').addEventListener('click', () => statsLoad(true));
}
