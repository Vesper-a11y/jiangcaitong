/* U114 回归（真实行为，不是字符串断言）：
 *   ① 「把这个星期的星期五调到下个星期六」= 那一天所有课整体挪 —— 不是只挪一门，
 *      也不能把同一门课在这一周别的天一起抹掉；
 *   ② 「跳到淘宝身份码」= 直接跳，别只弹个层、更别回「打不开」。
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
// app.js 里的工具实现在注册时截下来，直接调，走的是同一份代码。
let T = null;
const reg = w.AI.registerTools;
w.AI.registerTools = (t) => { T = t; reg(t); };
// app.js 顶上有 'use strict'，严格模式 eval 的声明不外泄 —— 追加一句把要看的东西递出来。
w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8') + '\n' + [
  'window.__t114 = {',
  '  get CFG() { return CFG; },',
  '  setTT(v) { TT = v; },',
  '  setData(v) { DATA = v; },',
  '  occupancy: occupancy,',
  '  renderSchedule: renderSchedule',
  '};'
].join('\n'));

const slots = (day, a, b) => { const o = []; for (let p = a; p <= b; p++) o.push({ day: day, period: p }); return o; };
const wk = (a, b) => { const o = []; for (let i = a; i <= b; i++) o.push(i); return o; };

(async () => {
  await new Promise((r) => setTimeout(r, 300));
  ok(!!T && typeof T.move_day === 'function', 'move_day 真的注册进助手了');

  // 造一份 12 周周五的课表：Linux 1-2 节（周三也有一节）、马原 3-5 节、计网 6-7 节
  const A = w.__t114;
  const seed = () => {
  A.setTT(null);
  A.setData({
    unitCount: 12,
    totalWeeks: 16,
    courses: [
      { code: 'c1', name: 'Linux操作系统', teacher: '张三', room: '数字经济实训大楼S307',
        weeks: wk(1, 16), slots: slots(4, 0, 1).concat(slots(2, 6, 6)) },
      { code: 'c2', name: '马克思主义基本原理', teacher: '李四', room: '麦三教3201',
        weeks: wk(1, 16), slots: slots(4, 2, 4) },
      { code: 'c3', name: '计算机网络', teacher: '王五', room: '麦工商楼G201',
        weeks: wk(1, 16), slots: slots(4, 5, 6) }
    ]
  });
  A.CFG.view = 'week'; A.CFG.week = 12;
  A.CFG.courseEdits = {}; A.CFG.myCourses = []; A.CFG.hidden = [];
  };
  seed(); A.renderSchedule();

  const before = A.occupancy(12);
  ok((before['4:0'] || []).length >= 1 && (before['4:5'] || []).length >= 1, '第 12 周周五本来是排满的');

  const r = T.move_day({ from_day: '周五', to_day: '周六', from_week: '12', weeks: '13' });
  const txt = String(r && r.text || '');
  ok(/都挪到第 13 周周六/.test(txt), '回话说明整天的课都挪过去了：' + txt);

  const w12 = A.occupancy(12), w13 = A.occupancy(13), w11 = A.occupancy(11), w14 = A.occupancy(14);
  const namesAt = (occ, day, p) => (occ[day + ':' + p] || []).map((c) => c.name);
  ok(namesAt(w12, 4, 0).length === 0, '12 周周五第 1 节空了');
  ok(namesAt(w12, 4, 2).length === 0, '12 周周五第 3 节（马原）空了');
  ok(namesAt(w12, 4, 6).length === 0, '12 周周五第 7 节（计网）空了');
  ok(namesAt(w13, 5, 0).indexOf('Linux操作系统') >= 0, '13 周周6 第 1 节补上了 Linux');
  ok(namesAt(w13, 5, 2).indexOf('马克思主义基本原理') >= 0, '13 周周6 第 3 节补上了马原');
  ok(namesAt(w13, 5, 6).indexOf('计算机网络') >= 0, '13 周周六第 7 节补上了计网');
  ok(namesAt(w12, 2, 6).indexOf('Linux操作系统') >= 0, 'Linux 这周周三那节照旧（没被整天调课误删）');
  ok(namesAt(w11, 4, 0).indexOf('Linux操作系统') >= 0, '第 11 周周五原样');
  ok(namesAt(w14, 4, 0).indexOf('Linux操作系统') >= 0, '第 14 周周五原样');

  // 13 周原本的课不许被动 —— 上面校验的是「只补不搬」，13 周周五第 6 节原是计网
  ok(namesAt(w13, 4, 5).indexOf('计算机网络') >= 0, '13 周周五原本的计网还在原地（没被顺手搬走）');

  // 说「挪」那天没课：如实说，不要假装改过
  const r2 = T.move_day({ from_day: '周日', to_day: '周一', from_week: '12', weeks: '13' });
  ok(/本来就没课/.test(String(r2 && r2.text || '')), '那天没课就说没课：' + (r2 && r2.text));

  // ---- 端到端：模型又只回一句「改好了」时，代码得自己把整天调课做掉 ----
  //   用户这条就是他原话的改写版，模型那时候就是嘴上改好了、课表没动。
  seed();
  let reqN = 0;
  w.NativeApi = { handle: async () => {
    reqN++;
    return { ok: true, data: { choices: [{ message: { role: 'assistant', content: '改好了' } }] } };
  } };
  w.AI.setKey('sk-test'); w.AI.open(); w.AI.clear();
  await w.AI.chat('把第12周的星期五调到第13周的星期六');
  await new Promise((r) => setTimeout(r, 120));
  ok(reqN >= 2, '模型只回文字时，代码自己跑了一遍工具（请求数 ' + reqN + '）');
  const e12 = A.occupancy(12), e13 = A.occupancy(13);
  ok(!(e12['4:0'] || []).length && !(e12['4:2'] || []).length, '第 12 周周五真的空了');
  ok((e13['5:0'] || []).length > 0 && (e13['5:2'] || []).length > 0, '第 13 周周六真的补上了');

  // ---- 用户原话：「不对呀 是把这个星期的星期五调到下个星期六」----
  seed(); reqN = 0; w.AI.clear();
  // 真实 App 里 app.js 会注入「此刻第几周」，forceMoveDay 靠它把「下个星期」算成 13。
  w.AI.setContext(() => ({ week: 12, total: 16, courses: [] }));
  await w.AI.chat('不对呀 是把这个星期的星期五调到下个星期六');
  await new Promise((r) => setTimeout(r, 120));
  const g12 = A.occupancy(12), g13 = A.occupancy(13);
  ok(!(g12['4:0'] || []).length, '原话（这个星期→下个星期六）也能把整天挪走，不再改成查课表');
  ok((g13['5:0'] || []).length > 0, '「下个星期六」算成了第 13 周，没落错周');
  w.AI.setContext(null);

  // ---- 跳转：说了淘宝身份码就真的唤起淘宝 ----
  const jumped = [];
  w.NativeApi = { openJump: (pkg, uri) => { jumped.push(pkg); return 'ok'; },
    hasApp: () => '1', openApp: () => 'ok', openWeb: () => '', handle: async () => ({ ok: true }) };
  const r3 = T.open_page({ name: '跳到淘宝身份码' });
  await new Promise((r) => setTimeout(r, 300));
  ok(jumped.indexOf('com.taobao.taobao') >= 0, '真的唤起了淘宝（不是只弹层）：' + jumped.join(','));
  ok(/淘宝身份码/.test(String(r3 && r3.text || '')), '回话说跳到哪个 App：' + (r3 && r3.text));

  jumped.length = 0;
  const r4 = T.open_page({ name: '菜鸟取件' });
  await new Promise((r) => setTimeout(r, 300));
  ok(jumped.indexOf('com.cainiao.wireless') >= 0, '菜鸟也是直接跳：' + jumped.join(','));
  ok(/菜鸟/.test(String(r4 && r4.text || '')), '回话点名菜鸟');

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
