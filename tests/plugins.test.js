/* U118 回归（真实行为）：插件市场。
 *
 * 覆盖用户提的四件事：
 *   ① 「我的」页在「课程调整」和「权限」之间出现插件市场，且和权限同卡；
 *   ② 插件只从 GitHub 下（域名白名单拦住别的地址），下下来会真出现在生活页；
 *   ③ 插件能更新（清单版本变高 → 列表出现「更新」，点完卡片换新版）；
 *   ④ 别人能提交插件（市场底部那一行），已下载的插件在同一个列表里管（删除 / 下架）。
 *
 * 网络用 __nativeDone 回放：走的是 native.js 里那条真 http() 路径，
 * 连域名白名单都是真的在跑，不是把 fetch 换成假函数。
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'http://localhost/', pretendToBeVisual: true, runScripts: 'outside-only' });
const w = dom.window;
w.matchMedia = w.matchMedia || (() => ({ matches: false, media: '', addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } }));
w.scrollTo = () => {};
// sha256 要真算：把 node 的 webcrypto 递进去，顺带 TextEncoder。
try {
  w.crypto = require('crypto').webcrypto;
  w.TextEncoder = TextEncoder;
} catch (e) { /* 没有就只靠 size 核 */ }

/* 网络：URL → 回放的 body。没登记的地址一律 404。 */
const NET = {};
let asked = [];
/* 外部打开（openUrl）记在这儿：U119 的「我也做一个」要验它跳的是 GitHub。 */
const opened = [];
w.Android = new Proxy({
  openUrl(u) { opened.push(String(u)); },
  req2(id, method, url) {
    asked.push(url);
    const body = NET[url];
    setTimeout(() => {
      if (body === undefined) w.__nativeDone(id, JSON.stringify({ status: 404, body: '' }));
      else w.__nativeDone(id, JSON.stringify({ status: 200, body: body }));
    }, 0);
  },
  req(id, method, url) {
    asked.push(url);
    setTimeout(() => { w.__nativeDone(id, JSON.stringify({ status: 404, body: '' })); }, 0);
  },
}, {
  get(t, k) { if (typeof k === 'symbol') return undefined; if (k in t) return t[k]; return () => ''; },
});

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log('[OK] ' + m); } else { fail++; console.log('[FAIL] ' + m); } };
const wait = (ms) => new Promise((r) => setTimeout(r, ms || 90));

w.eval(fs.readFileSync(path.join(ROOT, 'web/native.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(ROOT, 'web/ai.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8') + '\n' + [
  'window.__t118 = {',
  '  get CFG() { return CFG; },',
  '  KEY: KEY,',
  '  PLG: PLG,',
  '  PLG_W: PLG_W,',
  '  LIFE_WIDGETS: LIFE_WIDGETS,',
  '  lifeOn: lifeOn,',
  '  pluginInstalled: pluginInstalled,',
  '  pluginCodeKey: pluginCodeKey,',
  '  pluginMountCards: pluginMountCards,',
  '  pluginPaintEntry: pluginPaintEntry,',
  '  openPluginMarket: openPluginMarket,',
  '  openPluginSubmit: openPluginSubmit,',
  '  pluginByLine: pluginByLine,',
  '  store: store',
  '};'
].join('\n'));

// 🔴 owner 必须是规范大小写：raw.githubusercontent.com 区分大小写，
//    小写那份在真机上直接连不上（实测 000），这也是 U118 差点踩的坑。
const RAW = 'https://raw.githubusercontent.com/Vesper-a11y/jiangcaitong/main/';
const INDEX_URL = RAW + 'plugins/index.json';
const CODE_URL = RAW + 'plugins/demo/1.0.0.js';

const CODE1 = "plugin({ name:'签到提醒', tag:'学习通', " +
  "stats: () => [{k:'待签',v:'2'}], state: () => '● 下一节 10:00', " +
  "action: { label: '去看看', run() { plugin.toast('点了'); } } });";
const CODE2 = "plugin({ name:'签到提醒', tag:'学习通', " +
  "stats: () => [{ k:'待签', v:'0' }], state: () => '● 今天签完了', " +
  "action: { label: '打开', run() {} } });";

const mkIndex = (o) => JSON.stringify({ v: 1, plugins: [o] });
const demo = (extra) => Object.assign({
  id: 'demo', name: '签到提醒', tag: '学习通', desc: '上课前一键看一眼有没有签到',
  ver: '1.0.0', code: 1, file: 'plugins/demo/1.0.0.js',
  size: CODE1.length, sha256: '',
  // U119：卡片要显示提交人 / 提交时间
  by: 'alice', at: '2026-11-20'
}, extra || {});

// U119：插件市场是**单独一页**（#page-plugins），列表容器是 #plgList。
// 这里的 sheet() 保留名字、指到列表上 —— 下面大半断言只是在读市场里的字。
const pagePlg = () => w.document.getElementById('page-plugins');
const sheet = () => w.document.getElementById('plgList');
const lifeHost = () => w.document.getElementById('lifeWidgets');
const cardOf = (id) => lifeHost().querySelector('[data-life="plg:' + id + '"]');
const rowTxt = () => (w.document.getElementById('mePlugin') || {}).textContent;

(async () => {
  await wait(200);
  const A = w.__t118;
  ok(!!A, 'app.js 起来了');

  // ---------------- ⓪ CSP 必须放行插件代码 ----------------
  // 这条不是形式：CSP 是 script-src 'self' file: 时，new Function 会被直接拒，
  // 真机上的现象是「装不上」，而 jsdom 不认 CSP，别的用例照样全绿。
  const csp = (html.match(/Content-Security-Policy"\s+content="([^"]+)"/) || [])[1] || '';
  ok(/script-src[^;]*'unsafe-eval'/.test(csp), '页面 CSP 允许执行插件代码（否则真机装不上）');
  const scriptSrc = (csp.match(/script-src([^;]*)/) || [])[1] || '';
  ok(!/'unsafe-inline'/.test(scriptSrc), "但 script-src 里没顺手放开 'unsafe-inline'（那是另一种注入）");

  // ---------------- ① 「我的」页入口 ----------------
  const row = w.document.getElementById('rowPlugin');
  const perm = w.document.getElementById('rowPerm');
  ok(!!row && !!perm, '「我的」页有插件市场那一行');
  ok(row.parentElement === perm.parentElement && perm.parentElement === w.document.getElementById('rowMisc').parentElement,
    '插件市场和「权限 / 清除本机缓存」在同一张卡里');
  ok(row.nextElementSibling === perm, '位置就在「权限」正上方（也就是课程调整和权限之间）');
  ok(rowTxt() === '还没装插件', '还没装过时右边写「还没装插件」：' + rowTxt());
  ok(/需要魔法才能打开/.test(row.textContent), '入口下面那行小字写着「需要魔法才能打开」');
  ok(!!pagePlg(), '插件市场有自己的一整页（不再套底部弹层）');

  // ---------------- ② 拉不到也不能崩 ----------------
  A.openPluginMarket();
  await wait(120);
  ok(pagePlg().hidden === false, '点开的是整页（#page-plugins 显示出来了）');
  ok(w.document.getElementById('page-me').hidden === true, '整页把「我的」盖住了');
  ok(w.document.getElementById('sheetMask').hidden === true, '没有弹层');
  ok(!/插件只从 GitHub 下载/.test(pagePlg().textContent), '那句长说明按用户要求删掉了');
  ok(/连不上 GitHub/.test(sheet().textContent), '断网时市场只说连不上，不是空白');
  ok(!!w.document.getElementById('plgList'), '列表容器还在，可以点「重新检查」');

  // ---------------- ③ 下下来真的出现在生活页 ----------------
  NET[INDEX_URL] = mkIndex(demo());
  NET[CODE_URL] = CODE1;
  w.document.getElementById('plgReload').click();
  await wait(150);
  const listTxt = sheet().textContent;
  ok(listTxt.indexOf('签到提醒') >= 0, '市场里列出了插件的名字');
  ok(listTxt.indexOf('v1.0.0') >= 0, '列表带版本号');
  ok(/@alice/.test(listTxt), '卡片上写了提交人：' + listTxt.slice(0, 40));
  ok(/11月20日/.test(listTxt), '卡片上写了提交时间');
  // U120：用户要求自己的插件标「作者」。清单写 role，没写但 by 是仓库主也算作者。
  ok(/作者/.test(A.pluginByLine({ by: 'Vesper-a11y' })), '仓库主的插件自动标「作者」头衔');
  ok(/维护者/.test(A.pluginByLine({ by: 'bob', role: '维护者' })), '清单里的头衔能显示出来');
  const dl = sheet().querySelector('[data-plg="demo"][data-act="in"]');
  ok(!!dl && dl.textContent === '下载', '没装过的显示「下载」');
  dl.click();
  await wait(200);
  const c = cardOf('demo');
  ok(!!c, '装完生活页上真的多出一张卡');
  ok(c.classList.contains('lifew'), '卡片用的是内置组件那套 .lifew 排版');
  ok(c.textContent.indexOf('签到提醒') >= 0 && c.textContent.indexOf('学习通') >= 0, '卡上有名字和标签');
  ok((c.querySelector('.lifew-stat b') || {}).textContent === '2', '卡上的数是插件给的（待签 2）');
  ok((c.querySelector('.lifew-st') || {}).textContent === '● 下一节 10:00', '卡底部是插件给的状态');
  ok((c.querySelector('.lifew-go') || {}).textContent === '去看看 ›', '按钮字也来自插件');
  ok(A.lifeOn().indexOf('plg:demo') >= 0, '装完自动摆到生活页（不用再去组件库勾一次）');
  ok(!!A.pluginInstalled().demo, '本机记下了装的是什么版本');
  ok(rowTxt() === '已装 1 个', '「我的」那行跟着变成已装 1 个：' + rowTxt());

  // 关了重开：不联网也能挂回来
  delete A.PLG_W['plg:demo'];
  const keep = A.LIFE_WIDGETS.filter((x) => x.id !== 'plg:demo');
  A.LIFE_WIDGETS.length = 0;
  keep.forEach((x) => A.LIFE_WIDGETS.push(x));
  if (cardOf('demo')) cardOf('demo').remove();
  const keepNet = NET[INDEX_URL];
  delete NET[INDEX_URL];
  delete NET[CODE_URL];
  A.pluginMountCards();
  ok(!!cardOf('demo'), '断网重启后插件照样挂回生活页（代码在本机）');
  NET[INDEX_URL] = keepNet;

  // ---------------- ④ 插件能更新 ----------------
  NET[INDEX_URL] = mkIndex(demo({ ver: '1.1.0', code: 2, file: 'plugins/demo/1.1.0.js', size: CODE2.length }));
  NET[RAW + 'plugins/demo/1.1.0.js'] = CODE2;
  A.openPluginMarket();
  await wait(150);
  const upBtn = sheet().querySelector('[data-plg="demo"][data-act="up"]');
  ok(!!upBtn && upBtn.textContent === '更新', '清单版本变高，列表出现「更新」');
  // U119 卡片右上角标的是「可更新 / 已装」，不再往版本行里拼「· 已装」。
  ok(/可更新|已装/.test(sheet().textContent), '已装的那条也在这个列表里管（右上角有状态）');
  ok(!!sheet().querySelector('[data-plg="demo"][data-act="rm"]'), '已装的那条有「删除」');
  upBtn.click();
  await wait(220);
  const c2 = cardOf('demo');
  ok(!!c2 && c2.textContent.indexOf('今天签完了') >= 0, '更新后卡片换成新版内容');
  ok((c2.querySelector('.lifew-stat b') || {}).textContent === '0', '更新后数字也是新版的');
  ok(A.pluginInstalled().demo.ver === '1.1.0', '本机版本号跟着更新');
  ok(rowTxt() === '已装 1 个', '更新完不再是「有更新」：' + rowTxt());

  // ---------------- ⑤ 校验：大小 / sha256 对不上就不装 ----------------
  NET[INDEX_URL] = mkIndex(demo({ id: 'bad', name: '坏插件', ver: '1.0.0', size: 3, file: 'plugins/demo/1.0.0.js' }));
  A.openPluginMarket();
  await wait(150);
  const bad = sheet().querySelector('[data-plg="bad"][data-act="in"]');
  if (bad) bad.click();
  await wait(180);
  ok(!cardOf('bad'), '大小对不上的插件没装上');
  ok(!A.pluginInstalled().bad, '也没在本机留下记录');
  ok(/装不上/.test(w.document.getElementById('toast').textContent), '给了一句「装不上」而不是假装成功');

  const badSha = demo({ id: 'bs', name: '改过的插件', sha256: 'deadbeef' });
  NET[INDEX_URL] = mkIndex(badSha);
  A.openPluginMarket();
  await wait(150);
  const bs = sheet().querySelector('[data-plg="bs"][data-act="in"]');
  if (bs) bs.click();
  await wait(200);
  ok(!cardOf('bs'), 'sha256 对不上时也装不上');

  // ---------------- ⑥ 只能用 GitHub：别的地址一律拒 ----------------
  NET[INDEX_URL] = mkIndex({ id: 'ext', name: '外部插件', ver: '1.0.0', size: CODE1.length, file: 'https://example.com/x.js' });
  A.openPluginMarket();
  await wait(150);
  const ext = sheet().querySelector('[data-plg="ext"][data-act="in"]');
  if (ext) ext.click();
  await wait(200);
  ok(!cardOf('ext'), '不是 GitHub 的地址装不进来');
  ok(asked.indexOf('https://example.com/x.js') < 0, '那个地址根本没被请求过（白名单在原生那层就挡住了）');

  // ---------------- ⑦ 插件的名字要转义，不能变出标签 ----------------
  const XSS = "plugin({ name: '<img src=x onerror=1>', tag: 't', state: () => '● 正常' });";
  NET[INDEX_URL] = mkIndex({ id: 'xss', name: '恶意', ver: '1.0.0', size: XSS.length, file: 'plugins/xss/1.0.0.js' });
  NET[RAW + 'plugins/xss/1.0.0.js'] = XSS;
  A.openPluginMarket();
  await wait(150);
  const xs = sheet().querySelector('[data-plg="xss"][data-act="in"]');
  if (xs) xs.click();
  await wait(200);
  const xc = cardOf('xss');
  ok(!!xc, '这个插件装上了（只有名字带标签）');
  ok(xc && !xc.querySelector('img'), '名字里的 HTML 被转义，没有变成真标签');

  // ---------------- ⑧ 删除 + 下架也要能删 ----------------
  NET[INDEX_URL] = mkIndex(demo({ ver: '1.1.0', code: 2, file: 'plugins/demo/1.1.0.js', size: CODE2.length }));
  A.openPluginMarket();
  await wait(150);
  const rm = sheet().querySelector('[data-plg="demo"][data-act="rm"]');
  ok(!!rm && rm.textContent === '删除', '已装的在同一个列表里有「删除」');
  if (rm) rm.click();
  await wait(180);
  ok(!cardOf('demo'), '删掉后生活页上那张卡没了');
  ok(!A.pluginInstalled().demo, '本机记录也清了');
  ok(A.store.get(A.pluginCodeKey('demo'), '') === '', '插件代码一起删了，不留垃圾');
  ok(A.lifeOn().indexOf('plg:demo') < 0, '组件列表里那条也清了');

  // 下架：清单里没有它，但本机还装着 —— 必须还能在列表里删掉
  A.store.set(A.KEY.plugins, { ghost: { ver: '1.0.0', code: 1, name: '已下架的插件', at: 1 } });
  A.store.set(A.pluginCodeKey('ghost'), "plugin({ name:'已下架的插件', state: () => '● 在跑' });");
  NET[INDEX_URL] = mkIndex(demo({ ver: '1.1.0', code: 2, file: 'plugins/demo/1.1.0.js', size: CODE2.length }));
  A.openPluginMarket();
  await wait(150);
  ok(sheet().textContent.indexOf('已下架的插件') >= 0, '下架的插件仍然列出来（不然用户删不掉它）');
  const g = sheet().querySelector('[data-plg="ghost"][data-act="rm"]');
  ok(!!g, '下架的那条也有「删除」');
  if (g) g.click();
  await wait(150);
  ok(!A.pluginInstalled().ghost, '下架的也能删掉');

  // ---------------- ⑨ 提交入口 ----------------
  A.openPluginMarket();
  await wait(100);
  const sub = w.document.getElementById('plgSubmit');
  ok(!!sub, '市场底部有提交入口');
  ok(sub.textContent === '我也做一个', '按钮文案就是「我也做一个」：' + sub.textContent);
  sub.click();
  await wait(60);
  // 用户要求：点了**直接**去 GitHub 交插件的地方（新建文件，提交即 PR），
  // 不再中转「发群里 / 提 Issue」那一层。
  const target = opened[opened.length - 1] || '';
  ok(/github\.com\/vesper-a11y\/jiangcaitong\/new\/main\/plugins/.test(target),
    '点了直接跳 GitHub 新建插件文件（提 PR 的位置）：' + target);

  // ---------------- ⑩ 插件能查第几周 / 总共几周 / 今天 ----------------
  const WK = "plugin({ name:'学期进度', tag:'这周第几周', " +
    "stats: () => [{ k:'本周', v:'第 ' + plugin.week() + ' 周' }], " +
    "state: () => '● ' + plugin.today().text + ' · 共 ' + plugin.total() + ' 周' });";
  NET[INDEX_URL] = mkIndex({ id: 'wk', name: '学期进度', ver: '1.0.0', code: 1, size: WK.length, file: 'plugins/wk/1.0.0.js' });
  NET[RAW + 'plugins/wk/1.0.0.js'] = WK;
  A.openPluginMarket();
  await wait(150);
  const wkBtn = sheet().querySelector('[data-plg="wk"][data-act="in"]');
  if (wkBtn) wkBtn.click();
  await wait(200);
  const wc = cardOf('wk');
  ok(!!wc, '能用 plugin.week() / total() / today() 的插件装得上');
  const stTxt = (wc && wc.querySelector('.lifew-st') || {}).textContent || '';
  ok(/共 \d+ 周/.test(stTxt), '状态里拿到了总周数：' + stTxt);
  ok(/\d+月\d+日 周[日一二三四五六]/.test(stTxt), '状态里拿到了今天的日期：' + stTxt);

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  if (fail) process.exit(1);
})().catch((e) => { console.log('跑挂了：', e && e.stack || e); process.exit(1); });
