/* U117 回归（真实行为）：生活页组件库 —— 下载 / 删除。
 *
 * 用户 2026-11-28：「我想加一个下载和删除的功能，入口在这张图右上角……
 *   我以后做的小组件默认不下载，然后下载这四个默认下载，然后可以删除，
 *   我以后做的下载之后就也是以这种小组件出现在这个界面。」
 *
 * 验的是**真的点了按钮之后的状态**：默认装四个、删掉就下架、再点下载又回来、
 * 以后新加的组件（pre:false）默认不出现。
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
w.Android = new Proxy({ req: () => {}, req2: () => {} }, {
  get(t, k) { if (typeof k === 'symbol') return undefined; if (k in t) return t[k]; return () => ''; }
});

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log('[OK] ' + m); } else { fail++; console.log('[FAIL] ' + m); } };

w.eval(fs.readFileSync(path.join(ROOT, 'web/native.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(ROOT, 'web/ai.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8') + '\n' + [
  'window.__t117 = {',
  '  get CFG() { return CFG; },',
  '  lifeOn: lifeOn,',
  '  lifeDefaults: lifeDefaults,',
  '  LIFE_WIDGETS: LIFE_WIDGETS,',
  '  openSheet: openLifeWidgetsSheet,',
  '  renderLifeWidgets: renderLifeWidgets',
  '};'
].join('\n'));

const $ = (id) => w.document.getElementById(id);

(async () => {
  await new Promise((r) => setTimeout(r, 250));
  const A = w.__t117;

  // 新装机：lifeWidgets 还没写过 —— 启动时按 pre 装好四张卡。
  ok(Array.isArray(A.CFG.lifeWidgets) && A.CFG.lifeWidgets.length === 4,
    '启动后本机记下的是「四张都装上了」：' + String(A.CFG.lifeWidgets));
  const d0 = A.lifeOn();
  ok(d0.length === 4 && ['hui', 'zl', 'jump', 'jf'].every((x) => d0.indexOf(x) >= 0),
    '四张老组件默认就是装上的：' + d0.join(','));

  // 以后新加的组件：pre:false —— 默认不装。
  A.LIFE_WIDGETS.push({ id: 'fake', name: '测试组件', tag: '测试', pre: false,
    desc: '以后新做的组件长这样', state: () => '● 未启用', manage: () => {} });
  ok(A.lifeOn().indexOf('fake') < 0, '以后新做的组件（默认不下载）不会自己冒出来');

  // 打开面板：入口还是那颗「+ 组件」，面板里每行一颗按钮。
  A.openSheet();
  const rows = $('sheetBody').querySelectorAll('.wstore-row');
  ok(rows.length === A.LIFE_WIDGETS.length, '面板列出全部组件（含未下载的）：' + rows.length);
  const btnOf = (id) => $('sheetBody').querySelector('.wstore-btn[data-life="' + id + '"]');
  ok(btnOf('jf').textContent === '删除', '已装的组件右边是「删除」');
  ok(btnOf('fake').textContent === '下载', '没装的组件右边是「下载」');
  ok(btnOf('fake').className.indexOf('del') < 0, '「下载」是主色实心按钮');

  // 下载一个没装的。
  btnOf('fake').click();
  ok(A.lifeOn().indexOf('fake') >= 0, '点「下载」之后这个组件在生活页上了');
  ok(btnOf('fake').textContent === '删除', '下载完按钮就地变成「删除」');

  // 删掉智慧江财：卡片应当从生活页消失，记录不动。
  A.CFG.jfAcc = '2202500000';                 // 假装填过一卡通号（记录要留着）
  btnOf('jf').click();
  ok(A.lifeOn().indexOf('jf') < 0, '点「删除」之后从生活页拿掉了');
  ok($('lifeJfWidget').hidden === true, '那张卡真的藏起来了');
  ok(A.CFG.jfAcc === '2202500000', '删除不碰记录（一卡通号还在）');
  ok(btnOf('jf').textContent === '下载', '删掉后按钮变回「下载」');

  // 再下载回来。
  btnOf('jf').click();
  ok(A.lifeOn().indexOf('jf') >= 0 && $('lifeJfWidget').hidden === false, '再点下载，卡又回到生活页');

  // 全部删掉：空状态卡出来。
  ['hui', 'zl', 'jump', 'jf', 'fake'].forEach((id) => {
    const b = btnOf(id);
    if (b && b.textContent === '删除') b.click();
  });
  ok(A.lifeOn().length === 0, '全删光 = 一个组件都没有');
  ok($('lifeEmpty').hidden === false, '空状态卡出来了（不是一片空白）');

  // 重启一次（重新读配置）：下载状态要留着。
  const saved = JSON.parse(w.localStorage.getItem('zt.settings') || '{}');
  ok(Array.isArray(saved.lifeWidgets) && saved.lifeWidgets.length === 0, '删除状态存进了本机，重启不会自己长回来');

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
