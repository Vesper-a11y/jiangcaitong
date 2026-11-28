/* U130 回归：插件自己排版。
 *
 * 用户原话：「不要限制插件」。改动前插件只能交「名字 / 标签 / 两个数 / 一句话 /
 * 一个按钮」，长相由宿主定；改完插件交一段 HTML（可以带 CSS），整张卡自己画。
 *
 * 这里验四件事：
 *   ① 插件给的 HTML 原样出现在生活页，外壳仍是宿主那张白卡；
 *   ② 插件写的 CSS 被圈在自己这张卡里（写 body{display:none} 也不影响页面）；
 *   ③ HTML 里带执行能力的东西进来就被摘掉（script / on* / javascript:）；
 *   ④ 老写法（stats / state / action）没被这次改动弄坏 —— 手机上装着的插件不能白装。
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
  'window.__t130 = {',
  '  KEY: KEY,',
  '  PLG_W: PLG_W,',
  '  LIFE_WIDGETS: LIFE_WIDGETS,',
  '  store: store,',
  '  pluginMountCards: pluginMountCards,',
  '  pluginPaintStats: pluginPaintStats,',
  '  plgScopeCss: plgScopeCss,',
  '  plgCleanHtml: plgCleanHtml',
  '};'
].join('\n'));

const lifeHost = () => w.document.getElementById('lifeWidgets');
const cardOf = (id) => lifeHost().querySelector('[data-life="plg:' + id + '"]');

/* 一个「自己画」的插件：HTML 里带自己的 class，CSS 里故意写一条 body{display:none}
   —— 这条要是漏出去，整页都会白掉，最能说明作用域有没有做。 */
const CODE_CUSTOM = [
  "plugin({",
  "  name: '自习计时', tag: '自制',",
  "  html: () => '<div class=\"ring\"><b>' + (plugin.data('m') || 25) + '</b> 分钟</div>' +",
  "                 '<button data-act=\"add\">加五分</button>',",
  "  css: 'body{display:none} .ring{color:#e5484d} @media (min-width:1px){ .ring b{font-size:20px} }',",
  "  on: { add: function () { plugin.data('m', (Number(plugin.data('m')) || 25) + 5); } }",
  "});"
].join('\n');

/* 一个老写法的插件：改完之后它必须照旧画成模板卡。 */
const CODE_OLD = "plugin({ name:'老插件', tag:'学习通', " +
  "stats: () => [{k:'待签',v:'2'}], state: () => '● 老样子', " +
  "action: { label: '去看看', run() {} } });";

/* 一段什么都想干的 HTML。 */
const CODE_NASTY = "plugin({ name:'不安分', html: " +
  "'<div class=\"n\">hi</div>' + " +
  "'<script>window.__pwned = 1;<\\/script>' + " +
  "'<img class=\"evil\" src=\"x\" onerror=\"window.__pwned=2\">' + " +
  "'<a class=\"jl\" href=\"javascript:window.__pwned=3\">点我</a>' });";

const install = (id, code) => {
  const A = w.__t130;
  const has = A.store.get(A.KEY.plugins, {}) || {};
  has[id] = { ver: '1.0.0', code: 1, size: code.length, at: Math.floor(Date.now() / 1000) };
  A.store.set(A.KEY.plugins, has);
  A.store.set('zt.plugin.' + id, code);
};

(async () => {
  await wait(200);
  const A = w.__t130;
  ok(!!A, 'app.js 起来了');

  // ---------------- ① 插件画的卡真的上墙了 ----------------
  install('focus', CODE_CUSTOM);
  A.pluginMountCards();
  const c = cardOf('focus');
  ok(!!c, '插件卡出现在生活页');
  ok(c.classList.contains('lifew') && c.classList.contains('lifew-custom'), '外壳还是宿主那张卡（.lifew），里面归插件');
  ok(!c.querySelector('.lifew-hd') && !c.querySelector('.lifew-stats'), '没有宿主的固定模板了（名字/标签/两个数那一套不画）');
  const ring = c.querySelector('.plgv .ring');
  ok(!!ring, '插件自己的结构原样在里面');
  ok((c.querySelector('.ring b') || {}).textContent === '25', '插件给的数字画出来了');
  ok(!!c.querySelector('[data-act="add"]'), '插件自己的按钮也在');

  // ---------------- ② CSS 被圈在这张卡里 ----------------
  const st = c.querySelector('style');
  ok(!!st, '插件交的 CSS 挂上了');
  const cssTxt = st ? st.textContent : '';
  ok(cssTxt.indexOf('[data-life="plg:focus"] .ring') >= 0, '普通选择器前面挂上了这张卡的 data-life：' + cssTxt.slice(0, 60));
  ok(/(^|\})\s*body\s*\{/.test(cssTxt) === false, '插件写 body{…} 不会漏成全局规则');
  ok(cssTxt.indexOf('[data-life="plg:focus"]{display:none}') >= 0, 'body 被换成了这张卡本身（不会命中真 body）');
  ok(/@media[^{]*\{\s*\[data-life="plg:focus"\] \.ring b/.test(cssTxt), '@media 里的规则也进了作用域');
  // 页面本身没被插件 CSS 影响
  ok(!/(^|\})\s*body\s*\{/.test(cssTxt), '整份样式表里没有裸 body 规则');

  // ---------------- ③ 点按钮 → 插件自己的逻辑跑起来 ----------------
  c.querySelector('[data-act="add"]').click();
  await wait(30);
  const c2 = cardOf('focus');
  ok((c2.querySelector('.ring b') || {}).textContent === '30', '点插件自己的按钮后卡片跟着重画（25 → 30）');
  ok(A.store.get('zt.plg.focus.m', 0) === 30, '插件用 plugin.data 存下的数也在');

  // 内容没变就不动 DOM：重画会把输入框清空、把滚动位置弹回去
  const nodeBefore = cardOf('focus').querySelector('.ring');
  A.pluginPaintStats();
  const nodeAfter = cardOf('focus').querySelector('.ring');
  ok(nodeBefore === nodeAfter, '内容没变时重画不重建节点（不抢焦点、不弹滚动条）');

  // ---------------- ④ 带执行能力的 HTML 进不来 ----------------
  install('nasty', CODE_NASTY);
  A.pluginMountCards();
  const n = cardOf('nasty');
  ok(!!n, '这个插件也挂上了（不至于整张卡失败）');
  ok(n.querySelector('script') === null, '<script> 被摘掉');
  const img = n.querySelector('.evil');
  ok(!!img && !img.hasAttribute('onerror'), 'img 的 onerror 被摘掉');
  const a = n.querySelector('.jl');
  ok(!!a && !a.hasAttribute('href'), 'javascript: 链接被摘掉');
  ok(w.__pwned === undefined, '没有任何一段偷偷跑起来（__pwned 还是 undefined）');

  // ---------------- ⑤ 老插件照旧 ----------------
  install('old', CODE_OLD);
  A.pluginMountCards();
  const o = cardOf('old');
  ok(!!o, '老写法的插件也还在');
  ok(o.classList.contains('lifew') && !o.classList.contains('lifew-custom'), '它还是模板卡（没被新逻辑带走）');
  ok((o.querySelector('.lifew-stat b') || {}).textContent === '2', '老插件的「两个数」照旧');
  ok((o.querySelector('.lifew-st') || {}).textContent === '● 老样子', '老插件的状态行照旧');
  ok((o.querySelector('.lifew-go') || {}).textContent === '去看看 ›', '老插件的按钮照旧');

  // ---------------- ⑥ 卸掉之后定时器跟着停 ----------------
  // 给自定义卡挂一个 20ms 的自动重画，卸掉后卡片不该再被它碰。
  install('tick', "plugin({ name:'滴答', html: () => '<i class=\"t\">' + Date.now() + '</i>', refresh: 20 });");
  A.pluginMountCards();
  const tc = cardOf('tick');
  ok(!!tc, '带自动重画的插件也挂上了');
  const timer = A.PLG_W['plg:tick'] && A.PLG_W['plg:tick'].timer;
  ok(!!timer, '自动重画的定时器登记在卡片定义上');
  const has = A.store.get(A.KEY.plugins, {});
  delete has.tick;
  A.store.set(A.KEY.plugins, has);
  A.pluginMountCards();
  ok(!cardOf('tick'), '卸掉后卡片从生活页消失');
  ok(!A.PLG_W['plg:tick'], '卸掉后登记也清了');

  // ---------------- ⑦ 插件可以有自己的整页 ----------------
  // 用户：「不要限制插件，不是只能显示在生活组件里面」。
  const CODE_PAGE = [
    "plugin({",
    "  name: '空教室地图', tag: '自习',",
    "  page: {",
    "    title: '空教室',",
    "    html: '<div class=\"m\">A101 空着</div><button data-act=\"pick\">选它</button>',",
    "    css: '.m{color:#0a8}',",
    "    on: { pick: function () { plugin.data('picked', 1); plugin.toast('记下了'); } }",
    "  }",
    "});"
  ].join('\n');
  install('space', CODE_PAGE);
  A.pluginMountCards();
  const sp = cardOf('space');
  ok(!!sp, '有自己页面的插件，生活页上仍有一张卡（那是入口）');
  const goBtn = sp.querySelector('.lifew-go');
  ok(!!goBtn && goBtn.textContent === '打开 ›', '没写 action 时按钮自动是「打开」：' + (goBtn && goBtn.textContent));
  goBtn.click();
  const pageEl = w.document.getElementById('page-plgpage');
  ok(pageEl.hidden === false, '点卡片进到插件自己的一整页');
  ok(w.document.getElementById('plgPageTitle').textContent === '空教室', '页面标题用插件给的 title');
  ok(!!w.document.querySelector('#plgPageBody .m'), '插件画的整页内容出现了');
  const pgStyle = w.document.querySelector('#plgPageBody style');
  ok(!!pgStyle && pgStyle.textContent.indexOf('[data-plgpage="plg:space"] .m') >= 0, '页面 CSS 也圈在这一页里');
  w.document.querySelector('#plgPageBody [data-act="pick"]').click();
  await wait(20);
  ok(A.store.get('zt.plg.space.picked', 0) === 1, '页面里的按钮调到插件自己的逻辑');
  w.document.getElementById('plgPageBack').click();
  ok(pageEl.hidden === true && w.document.getElementById('page-campus').hidden === false,
    '左上角返回回到生活页');

  // ---------------- ⑧ 外观插件：改整页样式 ----------------
  // 用户：「也可以做外观什么的插件」。
  install('skin', "plugin({ name:'深夜蓝', style: ':root{--accent:#123456} .lifew{border-radius:24px}' });");
  A.pluginMountCards();
  const skin = w.document.getElementById('plgstyle-plg:skin');
  ok(!!skin, '外观插件交的样式挂上了');
  ok(/--accent:\s*#123456/.test(skin ? skin.textContent : ''), '样式原样生效（没被卡片作用域圈住）');
  ok((skin ? skin.textContent.indexOf('[data-life') : -1) < 0, '外观样式故意不做作用域 —— 它就是要改整页');
  ok(!cardOf('skin'), '只改外观的插件不占生活页一张空卡');
  ok(!A.LIFE_WIDGETS.some((x) => x.id === 'plg:skin'), '也不进组件列表');
  const has2 = A.store.get(A.KEY.plugins, {});
  delete has2.skin;
  A.store.set(A.KEY.plugins, has2);
  A.pluginMountCards();
  ok(!w.document.getElementById('plgstyle-plg:skin'), '卸载后样式跟着摘掉（界面自己恢复）');

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
