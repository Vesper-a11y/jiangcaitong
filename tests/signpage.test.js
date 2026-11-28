/* U122 回归（真实行为）：签到独立成一页，和学习通、中国大学 MOOC 并列。
 *
 * 用户原话：「把签到页和学习通还有中国大学 MOOC 并列」。
 *
 * 这条测试盯的是四件容易翻车的事：
 *   ① 一级导航真的有三颗，且「签到」点得进去（一页，不是弹层）；
 *   ② exOn() **不能**再把这个新视图删掉（U74 留过一句 `k !== 'sign'` 的清理，
 *      留着它等于每次进学习页都把签到从筛选表里抹掉，导航永远不出现）；
 *   ③ 老配置升级上来要补一次 sign，但用户自己取消之后不许再偷偷塞回去；
 *   ④ 正在签的要按「还剩多久」排，最急的在前；按钮文案只有一种「去签到」。
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'http://localhost/', pretendToBeVisual: true,
  runScripts: 'outside-only' });
const w = dom.window;
w.matchMedia = w.matchMedia || (() => ({
  matches: false, media: '', addListener() {}, removeListener() {},
  addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
}));
w.scrollTo = () => {};

/* 「去签到」走 NativeApi.openWeb → Android.openWeb，这里记下来验地址。 */
const opened = [];
w.Android = new Proxy({
  openWeb(u, t) { opened.push(String(u)); },
  req: (id) => { setTimeout(() => w.__nativeDone(id, JSON.stringify({ status: 404, body: '' })), 0); },
  req2: (id) => { setTimeout(() => w.__nativeDone(id, JSON.stringify({ status: 404, body: '' })), 0); },
}, {
  get(t, k) {
    if (typeof k === 'symbol') return undefined;
    // 🔴 openWebEx 必须明确「没有」：兜底那句 return () => '' 会让它看起来存在，
    //    native.js 于是走 openWebEx 分支，测试就永远记不到 openWeb 被调过。
    if (k === 'openWebEx') return undefined;
    if (k in t) return t[k];
    return () => '';
  },
});

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log('[OK] ' + m); } else { fail++; console.log('[FAIL] ' + m); } };
const wait = (ms) => new Promise((r) => setTimeout(r, ms || 60));
const $ = (s) => w.document.querySelector(s);

w.eval(fs.readFileSync(path.join(ROOT, 'web/native.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8') + '\n' + [
  'window.__t122 = {',
  '  get CFG() { return CFG; },',
  '  get CX() { return CX; },',
  '  KEY: KEY,',
  '  EX_VIEWS: EX_VIEWS,',
  '  EX_PRIMARY: EX_PRIMARY,',
  '  exOn: exOn,',
  '  setExamView: setExamView,',
  '  renderExamView: renderExamView,',
  '  signPaint: signPaint,',
  '  signAll: signAll,',
  '  renderCxSignBar: renderCxSignBar,',
  '};',
].join('\n'));
const A = w.__t122;

/* 造一份签到数据：两场正在签（一场 9 分钟、一场 3 分钟）+ 两条历史（一签一漏）。 */
function mockSigns() {
  const now = Math.floor(Date.now() / 1000);
  return {
    at: now, items: [],
    signs: [
      { course: '应用密码学', title: '签到', state: 'live',
        startAt: now - 120, endAt: now + 9 * 60, signed: false, url: 'https://mooc1.chaoxing.com/sign' },
      { course: '马克思主义基本原理', title: '签退', state: 'live',
        startAt: now - 300, endAt: now + 3 * 60, signed: false, url: '' },
      { course: '数据结构', title: '签到', state: 'end',
        startAt: now - 3600 * 26, endAt: now - 3600 * 26 + 600, signed: true },
      { course: '大学英语', title: '签到', state: 'end',
        startAt: now - 3600 * 50, endAt: now - 3600 * 50 + 600, signed: false },
    ],
  };
}

(async () => {
  await wait(350);   // boot 的补画

  // ---- ① 视图表：签到是一级，不是二级、不是药丸 ----
  ok(A.EX_VIEWS.indexOf('sign') >= 0, 'EX_VIEWS 里有 sign');
  ok(A.EX_PRIMARY.join('|') === 'cx|mooc|sign',
    '一级并排的是 学习通 / 中国大学 MOOC / 签到：' + A.EX_PRIMARY.join('|'));

  // 老配置（没有 sign）升级上来：补一次，并记账
  A.CFG.exWidgets = ['cx', 'mooc', 'work', 'grade'];
  A.CFG.exMig = 0;
  let on = A.exOn();
  ok(on.indexOf('sign') >= 0, '老配置升级时自动补上「签到」，导航里才看得见');
  ok(A.CFG.exMig === 2, '迁移记了账（exMig=2），不会每次都补');
  // 用户自己取消之后，不许再偷偷塞回去
  A.CFG.exWidgets = ['cx', 'mooc'];
  ok(A.exOn().indexOf('sign') < 0, '用户取消勾选后不会被自动塞回来');
  // 🔴 回归线：U74 那句 `k !== 'sign'` 只要还在，这一条必然红
  A.CFG.exWidgets = ['cx', 'mooc', 'sign', 'work', 'grade'];
  ok(A.exOn().indexOf('sign') >= 0, 'exOn() 不再把 sign 删掉（U74 留下的清理已拆）');

  // ---- ② 导航三颗，点「签到」进整页 ----
  A.CFG.examView = 'cx';
  A.renderExamView();
  await wait(30);
  const nav = w.document.getElementById('exNav');
  const prime = Array.prototype.map.call(nav.querySelectorAll('.exnav-p'),
    (b) => b.textContent);
  ok(prime.join('|') === '学习通|中国大学 MOOC|签到', '导航上三颗并列：' + prime.join('|'));

  const signBtn = nav.querySelector('[data-view="sign"]');
  ok(!!signBtn, '导航里有「签到」这颗');
  signBtn.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await wait(40);
  ok(A.CFG.examView === 'sign', '点了之后当前视图就是 sign');
  ok(w.document.getElementById('exViewSign').hidden === false,
    '签到有自己的整页（#exViewSign 显示出来）');
  ok(w.document.getElementById('exViewCx').hidden === true, '学习通那一块收起来了');
  // 导航是重画 innerHTML 的，点击前抓到的那个节点已经脱离文档 —— 重新查一次。
  const onBtn = nav.querySelector('[data-view="sign"]');
  ok(!!onBtn && onBtn.classList.contains('on'), '「签到」这颗是选中态（深底）');

  // ---- ③ 没连学习通时：只说一句话，给一颗按钮 ----
  A.CX.state = 'none';
  A.signPaint();
  await wait(20);
  const box = w.document.getElementById('cxSignBody');
  ok(/签到要连上学习通/.test(box.textContent), '没连学习通时写清「签到要连上学习通」');
  ok(!!w.document.getElementById('sgGoCx'), '给一颗「去连学习通」，不是一句干等的话');
  // 没连着的时候不该摆「现在没有要签的」—— 那是另一件事
  ok(!/现在没有要签的/.test(box.textContent), '没连上时不说「没有要签的」（会让人以为真没有）');

  // ---- ④ 有数据：两张正在签 + 历史 ----
  A.CX.state = 'ok';
  A.CX.data = mockSigns();
  A.signPaint();
  await wait(30);
  const cards = box.querySelectorAll('.sg-live');
  ok(cards.length === 2, '两场正在签各一张卡：' + cards.length);
  // 最急的排最前（剩 3 分钟的那场在 9 分钟前面）
  ok(/马克思主义基本原理/.test(cards[0].textContent),
    '最急的排最前：' + cards[0].querySelector('.sg-course').textContent);
  ok(/还剩 3 分钟/.test(cards[0].textContent), '卡上写了还剩多久：' + cards[0].textContent.slice(0, 40));
  ok(/签退/.test(cards[0].textContent), '标题不是「签到」时补上（签退要说清楚）');

  // U123 用户：「为什么一个是打开学习通，一个是去签到，统一一下」。
  // 现在两颗粒文字完全一样，差别只在点了之后去哪儿 + 一行小字说明。
  const goA = cards[1].querySelector('.sg-go[data-url]');
  ok(!!goA && goA.textContent === '去签到', '有地址的那场写「去签到」');
  const goCx = cards[0].querySelector('.sg-go-cx');
  ok(!!goCx && goCx.textContent === '去签到',
    '没地址的那场也写「去签到」（和上面那颗一个字都不差）：' + (goCx && goCx.textContent));
  ok(goA.textContent === goCx.textContent, '两张卡的按钮文案一致，界面上不再出现第二种叫法');
  // 差异只在按钮下面那行小字：说清「这一场进不去，得自己点」。
  const hint = cards[0].querySelector('.sg-hint');
  ok(!!hint && /自己点/.test(hint.textContent), '没地址的那张在按钮下用一行小字交代原因');
  ok(!cards[1].querySelector('.sg-hint'), '能直达的那张不加这行小字（不给没用的提示）');

  // ---- ⑤ 点「去签到」→ App 内打开那一场 ----
  opened.length = 0;
  goA.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await wait(20);
  ok(opened.length === 1 && /chaoxing\.com\/sign/.test(opened[0]),
    '「去签到」打开的是那一场的原页：' + opened[0]);

  // ---- ⑥ 点没地址那颗「去签到」→ 直接开学习通原页，人还留在签到这一页 ----
  // U122：学习通列表里那颗「签到」药丸已经撤了，不能再把人送回一个
  // 已经没有签到的列表 —— 直接打开学习通。
  opened.length = 0;
  goCx.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await wait(30);
  ok(opened.length === 1 && /chaoxing\.com/.test(opened[0]),
    '没地址那颗「去签到」落到学习通原页：' + opened[0]);
  ok(A.CFG.examView === 'sign', '点完人仍留在签到这一页（不被拽走）');

  // ---- ⑦ 历史：签过的、漏掉的都在，漏的标出来 ----
  A.setExamView('sign');
  await wait(40);
  A.CX.data = mockSigns();
  A.signPaint();
  await wait(30);
  const hist = box.querySelector('.sg-hist');
  ok(!!hist, '有「最近的签到」那一块');
  ok(/数据结构/.test(hist.textContent) && /已签到/.test(hist.textContent),
    '签过的那条写着「已签到」');
  ok(/大学英语/.test(hist.textContent), '没签的那条也在（漏了哪次要看得见）');
  ok(!!hist.querySelector('.cx-sstate.miss'), '没签的那条用红字标出来（.miss）');

  // ---- ⑧ 签到这一页不再重复挂学习页顶上那条 ----
  A.CFG.examView = 'sign';
  A.renderCxSignBar();
  ok(w.document.getElementById('cxSignBar').hidden === true,
    '签到整页里不再重复顶一条「正在签到」');

  // ---- ⑨ 学习通那一页里不再有「签到」这颗药丸（U122 用户：「把这个删了」）----
  A.CFG.cxKind = 'all';
  A.CFG.cxKindTouched = false;
  A.CX.state = 'ok';
  A.CX.data = {
    at: Math.floor(Date.now() / 1000),
    items: [{ kind: 'work', title: '作业一', course: '应用密码学' }],
    signs: mockSigns().signs,
  };
  A.setExamView('cx');
  await wait(40);
  const cats = w.document.getElementById('cxCats');
  ok(!!cats, '有待办时分类药丸行在');
  ok(!cats.querySelector('[data-kind="sign"]'),
    '分类药丸里没有「签到」了：' +
    Array.prototype.map.call(cats.querySelectorAll('.cx-cat'), (b) => b.textContent).join('/'));
  ok(!w.document.querySelector('#cxBody .cx-signcard'),
    '学习通页不再叠一张签到卡（签到就是那一页）');
  // 老配置里存过 cxKind='sign'：不能卡在一个再也点不出来的空视图上
  A.CFG.cxKind = 'sign';
  A.CFG.cxKindTouched = false;
  A.setExamView('cx');
  await wait(40);
  ok(!!w.document.getElementById('cxCats'), '老的 cxKind=sign 落回「全部」，不是空视图');

  // ---- ⑩ 全空：说清「现在没有要签的」，不给一张白页 ----
  A.CX.data = { at: Math.floor(Date.now() / 1000), items: [], signs: [] };
  A.signPaint();
  await wait(20);
  ok(/现在没有要签的/.test(box.textContent), '没有任何签到时说清「现在没有要签的」');
  ok(/还没有签到记录/.test(box.textContent), '一条记录都没有时也不留白');

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
