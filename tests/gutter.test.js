/* 课表左侧时间轴回归（U100 恢复后的形态）。
 * 2026-11-28：U98 那版「只留开始时间 + 连堂留白」用户要求改回去，
 * 这里跟着改回三行「开始时间 / 节次编号 / 结束时间」的判据。
 * 用 jsdom 起真实页面，直接调 renderGutter() 后检查 DOM 与 CSS。
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
w.Android = new Proxy({ req: () => {}, req2: () => {} }, {
  get(t, k) {
    if (typeof k === 'symbol') return undefined;
    if (k in t) return t[k];
    return () => '';
  },
});

w.eval(fs.readFileSync(path.join(ROOT, 'web/native.js'), 'utf8'));
// app.js 是 'use strict'，eval 里的顶层函数声明不会外泄到 window，
// 所以在同一段代码末尾把它显式挂出来，供下面的断言调用。
w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8') +
  '\n;window.__renderGutter = renderGutter;');

let pass = 0, fail = 0;
const check = (name, cond, detail) => {
  if (cond) { console.log('[OK] ' + name); pass++; }
  else { console.log('[FAIL] ' + name + ' :: ' + (detail || '')); fail++; }
};

setTimeout(() => {
  try { w.eval('__renderGutter()'); }
  catch (e) { console.log('[FAIL] renderGutter 抛错: ' + e.message); fail++; }

  const g = w.document.querySelector('#wkGutter');
  const slots = g ? Array.from(g.querySelectorAll('.g-slot')) : [];
  check('时间轴渲染出节次格子', slots.length >= 8, 'slots=' + slots.length);

  // 恢复态：每节一个节次号 + 两个时间（开始 / 结束）
  check('每节只有一个节次编号',
    slots.every((s) => s.querySelectorAll('b').length === 1),
    'b 数量=' + slots.map((s) => s.querySelectorAll('b').length).join(','));
  check('每节是「开始 / 节次 / 结束」三行（两个时间位）',
    slots.every((s) => s.querySelectorAll('i').length === 2),
    'i 数量=' + slots.map((s) => s.querySelectorAll('i').length).join(','));

  const ends = ['08:45','09:35','10:40','11:30','12:20','14:45','15:35',
                '16:40','17:30','19:25','20:15','21:05'];
  const starts = ['08:00','08:50','09:55','10:45','11:35','14:00','14:50',
                  '15:55','16:45','18:40','19:30','20:20'];
  const itexts = slots.map((s) => Array.from(s.querySelectorAll('i'))
    .map((i) => i.textContent.trim()));
  check('结束时间重新显示（每行末尾是本节结束时间）',
    itexts.every((pair) => pair.length === 2 && ends.indexOf(pair[1]) >= 0),
    'pairs=' + itexts.map((p) => p.join('/')).join(' | '));
  check('每行开头是本节开始时间',
    itexts.every((pair) => starts.indexOf(pair[0]) >= 0),
    'pairs=' + itexts.map((p) => p.join('/')).join(' | '));

  // U98 的「连堂留白」占位必须消失
  const blanks = g ? g.querySelectorAll('i.g-t-empty').length : 0;
  check('不再有连堂留白占位', blanks === 0, 'g-t-empty=' + blanks);

  // 午休 / 晚餐：纯文本，不加节次号
  const brk = g ? Array.from(g.querySelectorAll('.g-break')).map((x) => x.textContent.trim()) : [];
  check('午休 / 晚餐仍是纯文本提示',
    brk.length > 0 && brk.every((t) => /午休|晚餐/.test(t)), 'breaks=' + brk.join('|'));
  check('午休 / 晚餐不加节次号',
    brk.every((t) => !/^\d/.test(t)), 'breaks=' + brk.join('|'));

  // CSS：恢复态样式，U98 那套（竖线 / 12px 加粗 / 9px 浅灰）全部撤掉
  const css = fs.readFileSync(path.join(ROOT, 'web/app.css'), 'utf8');
  check('不再有淡细竖分隔线', !/\.gutter::after/.test(css));
  // 只取时间轴自己的两条规则来判，别把文件里其它规则的 9px / 700 算进来。
  const bRule = (css.match(/\.g-slot b\s*\{[^}]*\}/s) || [''])[0];
  const iRule = (css.match(/\.g-slot i\s*\{[^}]*\}/s) || [''])[0];
  check('节次编号恢复 10px / 600',
    /font-size:\s*10px/.test(bRule) && /font-weight:\s*600/.test(bRule));
  check('时间恢复 10px 墨色',
    /font-size:\s*10px/.test(iRule) && /var\(--text\)/.test(iRule));
  check('U98 的 12px/700 与 9px 浅灰已撤销',
    !/font-size:\s*12px/.test(bRule) && !/font-weight:\s*700/.test(bRule) &&
    !/font-size:\s*9px/.test(iRule));

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
}, 450);
