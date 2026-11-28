/* U125 回归：更新包自己下载、自己起名，不再交给浏览器。
 *
 * 背景（用户 2026-11-28）：「之前有人下载老是下载到 zip 改了吗」。
 * 实测：Gitee 的附件不管内容是什么，一律回 `Content-Type: application/zip`，
 * 文件名却是 .apk。手机下载器按类型补后缀 → 用户下到「压缩包」，装不上。
 * GitHub 直链是好的，所以只有「读不到 GitHub、落到 Gitee 备援」的人踩坑。
 *
 * 这条测试盯三件事：
 *   ① 有新接口时走自己下载，文件名是 jiangcaitong-<版本>.apk，**不再调 openUrl**；
 *   ② 下载器起不来（返回 fail）要退回浏览器 —— 不能变成「点更新没反应」；
 *   ③ 旧壳（没有 downloadApk）同样退回浏览器。
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

let dlCalls = [];
let openedUrls = [];
let dlReturn = 'ok';
let hasDownloadApi = true;

w.Android = new Proxy({
  downloadApk(u, n) { dlCalls.push([String(u), String(n)]); return dlReturn; },
  openUrl(u) { openedUrls.push(String(u)); },
  req: (id) => { setTimeout(() => w.__nativeDone(id, JSON.stringify({ status: 404, body: '' })), 0); },
  req2: (id) => { setTimeout(() => w.__nativeDone(id, JSON.stringify({ status: 404, body: '' })), 0); },
}, {
  get(t, k) {
    if (typeof k === 'symbol') return undefined;
    // 开关：模拟「旧版本 APK 的壳里没有这个方法」
    if (k === 'downloadApk') return hasDownloadApi ? t.downloadApk : undefined;
    if (k === 'openWebEx') return undefined;
    if (k in t) return t[k];
    return () => '';
  },
});

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log('[OK] ' + m); } else { fail++; console.log('[FAIL] ' + m); } };
const wait = (ms) => new Promise((r) => setTimeout(r, ms || 40));

w.eval(fs.readFileSync(path.join(ROOT, 'web/native.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8') + '\n' + [
  'window.__t125 = { openUpdateSheet: openUpdateSheet };',
].join('\n'));
const A = w.__t125;

const INFO = {
  version: '1.0.4',
  url: 'https://gitee.com/x/y/releases/download/v1.0.4/jiangcaitong-1.0.4.apk',
  notes: ['测试一下'],
};

function clickGo() {
  const b = w.document.getElementById('updGo');
  b.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
}

(async function () {
  // ---- ① 有接口：自己下载，文件名自己拼 ----
  dlCalls = []; openedUrls = [];
  dlReturn = 'ok'; hasDownloadApi = true;
  A.openUpdateSheet(INFO);
  clickGo();
  await wait();
  ok(dlCalls.length === 1, '点了「去更新」先走 App 自己下载（调了一次 downloadApk）');
  ok(dlCalls[0][1] === 'jiangcaitong-1.0.4.apk',
    '文件名自己拼成安卓安装包，不看服务端返回的类型：' + dlCalls[0][1]);
  ok(dlCalls[0][0] === INFO.url, '下的是清单里那个地址');
  ok(openedUrls.length === 0, '能自己下的时候不再交给浏览器（浏览器才会补 .zip）');

  // ---- ② 下载器起不来：退回浏览器 ----
  dlCalls = []; openedUrls = [];
  dlReturn = 'fail'; hasDownloadApi = true;
  A.openUpdateSheet(INFO);
  clickGo();
  await wait();
  ok(dlCalls.length === 1, '先试了一次自己下载');
  ok(openedUrls.length === 1 && openedUrls[0] === INFO.url,
    '下载器失败就退回浏览器，不能变成「点了没反应」：' + openedUrls[0]);

  // ---- ③ 旧壳（没有这个接口）：直接退回浏览器 ----
  dlCalls = []; openedUrls = [];
  hasDownloadApi = false;
  A.openUpdateSheet(INFO);
  clickGo();
  await wait();
  ok(dlCalls.length === 0, '旧壳里不调不存在的接口');
  ok(openedUrls.length === 1, '旧壳照旧用浏览器打开（老用户仍能更新）');

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
