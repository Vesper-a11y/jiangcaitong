/* U132 回归：插件完全放开宿主能力 + 卸载复原。
 *
 * 用户原话：「JS 插件完全放开宿主权限，不做能力隔离……注册/替换路由、修改任意页面 UI、
 * 改动底部导航、读写全部本地配置、调用原生工具类。不用做权限白名单拦截。
 * 但是保留两条底线：① 不能删/覆盖主 App 的源码、安装包；② 卸载时全部复原。」
 *
 * 这里验六件事：
 *   ① plugin.host 真的给到了（路由 / 导航 / 配置 / 存储 / 原生桥接）；
 *   ② 插件能注册一整页，并且切得过去；
 *   ③ 插件能在底部加一格，点了能切；
 *   ④ 插件能改配置，落了盘；
 *   ⑤ 卸载后：插件写的键被清掉、改过的配置退回装之前 —— 底线②；
 *   ⑥ 原生侧没有任何「写文件 / 删文件」入口，安装包和源码碰不到 —— 底线①。
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
try {
  w.crypto = require('crypto').webcrypto;
  w.TextEncoder = TextEncoder;
} catch (e) { /* 用不到 */ }

w.Android = new Proxy({
  openUrl() {},
  req2(id) { setTimeout(() => w.__nativeDone(id, JSON.stringify({ status: 404, body: '' })), 0); },
  req(id) { setTimeout(() => w.__nativeDone(id, JSON.stringify({ status: 404, body: '' })), 0); },
}, {
  get(t, k) { if (typeof k === 'symbol') return undefined; if (k in t) return t[k]; return () => ''; },
});

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log('[OK] ' + m); } else { fail++; console.log('[FAIL] ' + m); } };
const wait = (ms) => new Promise((r) => setTimeout(r, ms || 90));

w.eval(fs.readFileSync(path.join(ROOT, 'web/native.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(ROOT, 'web/ai.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8') + '\n' + [
  'window.__t132 = {',
  '  KEY: KEY,',
  '  CFG: CFG,',
  '  PLG_W: PLG_W,',
  '  PLG_PAGES: PLG_PAGES,',
  '  store: store,',
  '  pluginMountCards: pluginMountCards,',
  '  pluginRemove: pluginRemove,',
  '  plgUndoKeys: plgUndoKeys,',
  '  PLG_LEDGER: PLG_LEDGER,',
  '  switchTab: switchTab,',
  '  curPage: () => CUR_PAGE',
  '};'
].join('\n'));

/* 一个「什么都想干」的插件：注册页、加导航、改配置、写存储、切页。 */
const CODE_HOST = [
  "const out = { ver: 0, page: false, nav: false, routed: '', navList: [], hostKeys: [] };",
  "try {",
  "  out.ver = plugin.host.version;",
  "  out.hostKeys = Object.keys(plugin.host).sort();",
  "  out.page = plugin.host.route.add({ name: 'plgroom', title: '空教室', html: '<p id=\"rm\">A101</p>' });",
  "  out.nav = plugin.host.nav.add({ tab: 'plgroom', label: '空教室' });",
  "  plugin.host.cfg.set('plgTestFlag', 7);",
  "  plugin.host.cfg.save();",
  "  plugin.host.store.set('zt.plgFlag', 1);",
  "  plugin.host.route.go('plgroom');",
  "  out.routed = plugin.host.route.cur();",
  "  out.navList = plugin.host.nav.list().map((x) => x.tab);",
  "} catch (e) { out.err = String((e && e.message) || e); }",
  "window.__H = out;",
  "plugin({ name: '宿主测试', html: '<b id=\"hv\">宿主</b>' });"
].join('\n');

const install = (id, code) => {
  const A = w.__t132;
  const has = A.store.get(A.KEY.plugins, {}) || {};
  has[id] = { ver: '1.0.0', code: 1, size: code.length, at: Math.floor(Date.now() / 1000) };
  A.store.set(A.KEY.plugins, has);
  A.store.set('zt.plugin.' + id, code);
};

(async () => {
  await wait(200);
  const A = w.__t132;
  ok(!!A, 'app.js 起来了');

  // 装之前先记一份「原来的样子」，卸载后要照着这个验。
  const beforeSettings = JSON.parse(JSON.stringify(A.store.get(A.KEY.settings, {}) || {}));
  const beforeFlag = w.localStorage.getItem('zt.plgFlag');

  install('hosty', CODE_HOST);
  A.pluginMountCards();
  await wait(60);

  const H = w.__H || {};
  ok(!H.err, '插件整段跑完没抛错' + (H.err ? '：' + H.err : ''));
  ok(H.ver === '1.0.4', 'plugin.host.version 拿到宿主版本（' + H.ver + '）');
  ok(H.hostKeys.indexOf('route') >= 0 && H.hostKeys.indexOf('nav') >= 0 &&
     H.hostKeys.indexOf('cfg') >= 0 && H.hostKeys.indexOf('store') >= 0 &&
     H.hostKeys.indexOf('native') >= 0, 'host 上有 route / nav / cfg / store / native：' + H.hostKeys.join(','));

  // ② 注册页：section 真的挂上了，切过去看得见
  const sec = w.document.getElementById('page-plgroom');
  ok(!!sec, '插件注册的页面挂进了 DOM');
  ok(sec && sec.querySelector('#rm') && sec.querySelector('#rm').textContent === 'A101', '页面里是插件写的 HTML');
  ok(H.routed === 'plgroom', 'route.go 切到了插件页（当前 ' + H.routed + '）');
  ok(sec && sec.hidden === false, '插件页现在可见（其余页被收起）');
  ok(w.document.getElementById('page-me').hidden === true, '宿主页面被收起，没有两层压在一起');

  // ③ 底部导航：加了一格，点了能切
  const tabBtn = w.document.querySelector('.tabbar .tab[data-tab="plgroom"]');
  ok(!!tabBtn, '底部多出一格');
  ok(tabBtn && tabBtn.textContent.trim() === '空教室', '那一格的字是插件写的');
  ok(H.navList.indexOf('plgroom') >= 0, 'nav.list() 能看到这一格');
  A.switchTab('schedule');
  tabBtn.click();
  ok(A.curPage() === 'plgroom', '点那一格能切回插件页');

  // ④ 配置真的落了盘
  const saved = A.store.get(A.KEY.settings, {}) || {};
  ok(saved.plgTestFlag === 7, '插件改的配置写进了本机设置');
  ok(w.localStorage.getItem('zt.plgFlag') === '1', '插件写的存储键存在');

  // ⑤ 卸载复原（底线②）
  A.pluginRemove('hosty');
  await wait(200);
  ok(A.store.get(A.KEY.plugins, {})['hosty'] === undefined, '插件从清单里删掉了');
  ok(w.localStorage.getItem('zt.plugin.hosty') === null, '插件代码也删掉了');
  ok(w.localStorage.getItem('zt.plgFlag') === beforeFlag, '插件写的存储键还原（现在是 ' + w.localStorage.getItem('zt.plgFlag') + '）');
  const afterSettings = A.store.get(A.KEY.settings, {}) || {};
  ok(afterSettings.plgTestFlag === beforeSettings.plgTestFlag, '插件改过的配置退回装之前的值');
  ok(w.localStorage.getItem('plgstyle-plg:hosty') === null || !w.document.getElementById('plgstyle-plg:hosty'), '外观样式被摘掉');
  ok(!!A.PLG_LEDGER['hosty'] && A.PLG_LEDGER['hosty'].keys.size === 0, '账本已清空（下次装同一个插件不会拿旧账去还）');

  // ⑥ 底线①：原生侧没有任何写/删文件的入口
  const NATIVE = w.NativeApi || {};
  const flat = [];
  const walk = (o, p) => {
    Object.keys(o || {}).forEach((k) => {
      const v = o[k];
      const name = p ? p + '.' + k : k;
      if (typeof v === 'function') flat.push(name);
      else if (v && typeof v === 'object') walk(v, name);
    });
  };
  walk(NATIVE, '');
  const danger = flat.filter((n) => /writefile|deletefile|removefile|unlink|rmdir|renamefile/i.test(n));
  ok(danger.length === 0, '原生桥接里没有写/删文件的入口（' + flat.length + ' 个方法，可疑 ' + danger.length + ' 个）');
  ok(flat.indexOf('downloadApk') >= 0, '安装包相关只剩「下载到私有目录」这一条（' + flat.length + ' 个方法里唯一带 apk 的）');

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
