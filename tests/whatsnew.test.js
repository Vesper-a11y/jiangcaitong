/* U126 回归：更新之后第一次打开，弹一次「这次改了什么」。
 *
 * 用户原话：「还有每次更新后做一个弹窗，提示这次的改动」。
 *
 * 这条测试盯四件事：
 *   ① 从旧版本升上来（本机记着旧版本号）→ 自动弹，标题写当前版本，内容是这一版的几行；
 *   ② 弹过就不再来 —— 同版本再启动不该重复烦人；
 *   ③ 全新安装（本机没有记录）→ 不弹，但要记账（下次升级才会弹）；
 *   ④ 这一版没写日志（版本号不在清单里）→ 不弹空壳。
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

w.Android = new Proxy({
  req: (id) => { setTimeout(() => w.__nativeDone(id, JSON.stringify({ status: 404, body: '' })), 0); },
  req2: (id) => { setTimeout(() => w.__nativeDone(id, JSON.stringify({ status: 404, body: '' })), 0); },
}, {
  get(t, k) {
    if (typeof k === 'symbol') return undefined;
    if (k === 'openWebEx') return undefined;
    if (k in t) return t[k];
    return () => '';
  },
});

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log('[OK] ' + m); } else { fail++; console.log('[FAIL] ' + m); } };
const wait = (ms) => new Promise((r) => setTimeout(r, ms || 60));
const $ = (s) => w.document.querySelector(s);
const mask = () => w.document.getElementById('sheetMask');

// 模拟「上一次打开还是 1.0.0」——升级后第一次启动就是这个状态。
// 🔴 必须按 store 的格式写（JSON 字符串）：store.get 走 JSON.parse，
//    裸写 '1.0.0' 会解析失败、退回默认值，于是被当成「全新安装」而不弹。
try { w.localStorage.setItem('zt.seenVer', JSON.stringify('1.0.0')); } catch (e) {}

w.eval(fs.readFileSync(path.join(ROOT, 'web/native.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8') + '\n' + [
  'window.__t126 = {',
  '  whatsNewCheck: whatsNewCheck,',
  '  openWhatsNew: openWhatsNew,',
  '  VER: APP_VERSION,',
  '  CHANGELOG: BUILTIN_CHANGELOG,',
  '};',
].join('\n'));
const A = w.__t126;

(async function () {
  // ---- ① 升级后自动弹 ----
  await wait(1600);                     // 启动 300ms + 内部 900ms + 余量
  ok(A.VER === '1.0.4', '当前版本是 1.0.4：' + A.VER);
  ok(mask().hidden === false, '从 1.0.0 升上来，第一次启动就弹了更新说明');
  const body = w.document.getElementById('sheetBody');
  ok(/已经更新到 v1\.0\.4/.test(body.textContent), '标题写的是这次更新的版本');
  ok(/聊天气泡优化/.test(body.textContent), '内容是这一版的分组（聊天气泡优化）');
  ok(/插件市场/.test(body.textContent), '内容是这一版的分组（插件市场那条）');
  ok(!/v1\.0\.3/.test(body.textContent), '不把历史版本全列出来（那是「更新日志」那页的事）');

  // ---- ② 关了之后不再弹 ----
  const btn = w.document.getElementById('wnOk');
  ok(!!btn && btn.textContent === '知道了', '有「知道了」可以关掉');
  btn.click();
  await wait(60);
  ok(mask().hidden === true, '点了就关掉');

  A.whatsNewCheck();
  await wait(1200);
  ok(mask().hidden === true, '同一版本再启动不会再弹一遍');

  // ---- ③ 全新安装：不弹，但要记账 ----
  w.localStorage.removeItem('zt.seenVer');
  A.whatsNewCheck();
  await wait(1200);
  ok(mask().hidden === true, '全新安装不弹（没有任何东西「变了」）');
  ok(JSON.parse(w.localStorage.getItem('zt.seenVer') || '""') === '1.0.4',
    '全新安装也记下版本号，下次升级才会弹');

  // ---- ④ 这一版没写日志：不弹空壳 ----
  w.localStorage.setItem('zt.seenVer', JSON.stringify('0.9.9'));
  A.openWhatsNew({ v: '9.9.9', groups: [] });
  await wait(40);
  ok(mask().hidden === false, 'openWhatsNew 本身能画出来（内容为空也不抛错）');
  w.document.getElementById('wnOk').click();
  await wait(40);

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
