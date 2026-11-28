/* U115 回归（真实行为）：「老师说明天的课取消、调到下周六」这类单门调课。
 *
 * 用户 2026-11-28 两次翻车：
 *   ① 他说「明天的课取消，调整到下周六」，AI 回改好了，实际动的是 **13 周周五**那节，
 *      12 周周五那节还在；
 *   ② 他明说「你要把12周周五的改到13周周六，13周周五的不变」——AI 又只是嘴上说改了。
 *
 * 根因：源周（原来那一周）靠模型的 from_week 可选字段，模型不填就退回「目标周」，
 *      于是跳过 13 周、补 13 周周六 —— 目标周自己把自己搬走了。
 * 现在源周由代码从用户原话里认：改动词前的「12周 / 明天 / 这个星期」。
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
let T = null;
const reg = w.AI.registerTools;
w.AI.registerTools = (t) => { T = t; reg(t); };
w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8') + '\n' + [
  'window.__t115 = {',
  '  get CFG() { return CFG; },',
  '  setTT(v) { TT = v; },',
  '  setData(v) { DATA = v; },',
  '  occupancy: occupancy,',
  '  renderSchedule: renderSchedule,',
  '  currentWeek: currentWeek',
  '};'
].join('\n'));

const slots = (day, a, b) => { const o = []; for (let p = a; p <= b; p++) o.push({ day: day, period: p }); return o; };
const wk = (a, b) => { const o = []; for (let i = a; i <= b; i++) o.push(i); return o; };
const namesAt = (occ, day, p) => (occ[day + ':' + p] || []).map((c) => c.name);

(async () => {
  await new Promise((r) => setTimeout(r, 300));
  const A = w.__t115;

  // 今天是 2026-11-28（第 12 周），学期从 9-7 起算 —— 和用户手机上的周次一致。
  const seed = () => {
    A.setTT(null);
    A.setData({
      unitCount: 12,
      courses: [
        { code: 'lin', name: 'Linux操作系统', teacher: '张三', room: '数字经济实训大楼S307',
          weeks: wk(1, 16), slots: slots(4, 0, 1).concat(slots(2, 2, 3)) },
        { code: 'mar', name: '马克思主义基本原理', teacher: '李四', room: '麦三教3201',
          weeks: wk(1, 16), slots: slots(4, 2, 4) }
      ]
    });
    A.CFG.startDate = '2026-09-07';
    A.CFG.weeks = 17;
    A.CFG.courseEdits = {}; A.CFG.myCourses = []; A.CFG.hidden = [];
    // 用户此刻翻到了第 13 周看结果 —— 这正是「界面周次 ≠ 他说的那一周」的来源。
    A.CFG.view = 'week'; A.CFG.week = 13;
  };

  seed();
  A.renderSchedule();
  ok(A.currentWeek() === 12, '今天算出来是第 12 周（测试基准，实际 ' + A.currentWeek() + '）');

  // ---- 场景一：老师通知（AI 只拿到「调到周六、第13周」，没填源周）----
  const r1 = T.update_course({
    name: 'Linux操作系统', day: '周六', start_period: 1, end_period: 2, weeks: '13',
    said: '明天的课取消，调整到下周六，下周五与周六相同的当天上课时间，我到时候也会再通知'
  });
  const t1 = String(r1 && r1.text || '');
  ok(/第 12 周/.test(t1) && /补到第 13 周/.test(t1), '回话把源周和目标周说清了：' + t1);

  const w12 = A.occupancy(12), w13 = A.occupancy(13), w14 = A.occupancy(14);
  ok(namesAt(w12, 4, 0).indexOf('Linux操作系统') < 0, '12 周周五第 1 节（Linux）真的不上了');
  ok(namesAt(w12, 4, 2).indexOf('马克思主义基本原理') >= 0, '12 周周五第 3 节（马原）没被误删');
  ok(namesAt(w13, 5, 0).indexOf('Linux操作系统') >= 0, '13 周周六第 1 节补上了');
  ok(namesAt(w13, 4, 0).indexOf('Linux操作系统') >= 0, '13 周周五那节保持原样（用户点名要求）');
  ok(namesAt(w14, 4, 0).indexOf('Linux操作系统') >= 0, '14 周周五照旧');
  ok(namesAt(w12, 2, 2).indexOf('Linux操作系统') >= 0, 'Linux 12 周周三那节没被带走');

  // ---- 场景二：用户纠正「12周周五的改到13周周六，13周周五的不变」----
  seed(); A.renderSchedule();
  const r2 = T.update_course({
    name: 'Linux操作系统', day: '周六', start_period: 1, end_period: 2, weeks: '13',
    said: '你要把12周周五的改到13周周六，13周周五的不变'
  });
  const b12 = A.occupancy(12), b13 = A.occupancy(13);
  ok(namesAt(b12, 4, 0).indexOf('Linux操作系统') < 0, '纠正后 12 周周五那节没了');
  ok(namesAt(b13, 5, 0).indexOf('Linux操作系统') >= 0, '纠正后 13 周周六有了');
  ok(namesAt(b13, 4, 0).indexOf('Linux操作系统') >= 0, '纠正后 13 周周五仍在');
  ok(/第 13 周原本的课没动/.test(String(r2 && r2.text || '')), '回话点明目标周原本的课没动');

  // ---- 场景三：模型给了 from_week 时以它为准（老路径不能坏）----
  seed(); A.renderSchedule();
  T.update_course({ name: 'Linux操作系统', day: '周六', start_period: 1, end_period: 2,
    from_week: '12', weeks: '13', said: '随便' });
  const c12 = A.occupancy(12), c13 = A.occupancy(13);
  ok(namesAt(c12, 4, 0).indexOf('Linux操作系统') < 0 && namesAt(c13, 5, 0).indexOf('Linux操作系统') >= 0,
    'from_week 显式给了就按它走');

  // ---- 场景四：重复改一遍不许叠出第二条补课 ----
  T.update_course({ name: 'Linux操作系统', day: '周六', start_period: 1, end_period: 2,
    from_week: '12', weeks: '13', said: '再改一次' });
  const dup = A.occupancy(13);
  ok((dup['5:0'] || []).filter((c) => c.name === 'Linux操作系统').length === 1,
    '同一门课只留一条补课，不会越改越多');

  // ---- 场景五：整门课换时间（apply_all）仍然通 ----
  seed(); A.renderSchedule();
  T.update_course({ name: 'Linux操作系统', day: '周六', start_period: 1, end_period: 2,
    apply_all: true, said: '以后每周都改到周六' });
  const d12 = A.occupancy(12), d14 = A.occupancy(14);
  ok(namesAt(d12, 5, 0).indexOf('Linux操作系统') >= 0 && namesAt(d14, 5, 0).indexOf('Linux操作系统') >= 0,
    'apply_all 时每周都换到周六');

  // ---- 端到端：模型真的调了 update_course，但没填 from_week ----
  seed(); A.renderSchedule();
  let step = 0;
  w.NativeApi = { handle: async () => {
    step++;
    if (step === 1) {
      return { ok: true, data: { choices: [{ message: { role: 'assistant', content: '', tool_calls: [
        { id: 't1', type: 'function', function: { name: 'update_course',
          arguments: JSON.stringify({ name: 'Linux操作系统', day: '周六',
            start_period: 1, end_period: 2, weeks: '13' }) } }] } }] } };
    }
    return { ok: true, data: { choices: [{ message: { role: 'assistant', content: '改好了' } }] } };
  } };
  w.AI.setKey('sk-test'); w.AI.open(); w.AI.clear();
  await w.AI.chat('你要把12周周五的改到13周周六，13周周五的不变');
  await new Promise((r) => setTimeout(r, 150));
  const x12 = A.occupancy(12), x13 = A.occupancy(13);
  ok(namesAt(x12, 4, 0).indexOf('Linux操作系统') < 0, '端到端：模型没填源周，12 周周五也清掉了');
  ok(namesAt(x13, 5, 0).indexOf('Linux操作系统') >= 0, '端到端：13 周周六补上了');
  ok(namesAt(x13, 4, 0).indexOf('Linux操作系统') >= 0, '端到端：13 周周五那节没被动');

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
