/* U116 回归（真实行为）：撤销 + 「把周六那节改回周五」。
 *
 * 用户 2026-11-28 截图：
 *   ①「把你做的所有操作都撤销」→ AI 回「改好了：Linux操作系统（周六第1-2节…）」，
 *      课表一个字没动；
 *   ②「13周的周六上午Linux改回周五」→ 又是同一句「改好了：周六第1-2节…」，
 *      课表还是没动。他说「改不了」。
 *
 * 根因两条：
 *   A. 根本没有撤销这一步 —— 模型只能再调一次 update_course，参数跟上一句一样。
 *   B. aiFind 把「单次调课补的那一条」（my:mv:xxx）当成自己加的课，
 *      改还是改成同一天同一节，写回同一条记录 → 回「改好了」但没变化。
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
  'window.__t116 = {',
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
  const A = w.__t116;

  const seed = () => {
    A.setTT(null);
    A.setData({
      unitCount: 12,
      courses: [
        { code: 'lin', name: 'Linux操作系统', teacher: '陶小绪', room: '数字经济实训大楼S307',
          weeks: wk(1, 16), slots: slots(4, 0, 1) },
        { code: 'mar', name: '马克思主义基本原理', teacher: '王振华', room: '麦三教3201',
          weeks: wk(1, 16), slots: slots(4, 2, 4) },
        { code: 'net', name: '计算机网络', teacher: '蒋剑', room: '麦三教3114',
          weeks: wk(1, 16), slots: slots(4, 8, 9) }
      ]
    });
    A.CFG.startDate = '2026-09-07';
    A.CFG.weeks = 17;
    A.CFG.courseEdits = {}; A.CFG.myCourses = []; A.CFG.hidden = [];
    A.CFG.view = 'week'; A.CFG.week = 13;
  };

  // 用户手机上那一步：老师通知「周五的挪到周六」，12 周 → 13 周
  const moveToSat = () => T.update_course({
    name: 'Linux操作系统', day: '周六', start_period: 1, end_period: 2, weeks: '13',
    said: '明天的课取消，调整到下周六，下周五与周六相同的当天上课时间'
  });

  seed(); A.renderSchedule();

  // ---- ① 撤销：真的把课表退回去 ----
  const r1 = moveToSat();
  ok(/第 12 周/.test(String(r1 && r1.text || '')), '先做一次 12 周周五 → 13 周周六的调课');
  ok(namesAt(A.occupancy(13), 5, 0).indexOf('Linux操作系统') >= 0, '调完 13 周周六有了');
  ok(namesAt(A.occupancy(12), 4, 0).indexOf('Linux操作系统') < 0, '调完 12 周周五没了');

  const u1 = T.undo({ said: '把你做的所有操作都撤销', all: true });
  ok(/都撤了/.test(String(u1 && u1.text || '')), '撤销回的是「都撤了」而不是「改好了」：' + String(u1 && u1.text || ''));
  const u12 = A.occupancy(12), u13 = A.occupancy(13);
  ok(namesAt(u12, 4, 0).indexOf('Linux操作系统') >= 0, '撤销后 12 周周五那节回来了');
  ok(namesAt(u13, 5, 0).indexOf('Linux操作系统') < 0, '撤销后 13 周周六那节没了');
  ok(namesAt(u13, 4, 0).indexOf('Linux操作系统') >= 0, '撤销后 13 周周五保持原样');
  ok((A.CFG.myCourses || []).length === 0, '撤销后补课记录也清干净了，没留孤儿');

  // 撤到底之后再说撤销：不许装作撤了
  const u2 = T.undo({ said: '再撤销一次', all: true });
  ok(/还没改过课表|没有可撤的/.test(String(u2 && u2.text || '')), '没东西可撤时直说，不假装');

  // ---- ②「13周的周六上午Linux改回周五」：模型只传了个课程名 ----
  seed(); A.renderSchedule();
  moveToSat();
  const r2 = T.update_course({ name: 'Linux操作系统', said: '13周的周六上午Linux改回周五' });
  const t2 = String(r2 && r2.text || '');
  const v12 = A.occupancy(12), v13 = A.occupancy(13);
  ok(namesAt(v13, 4, 0).indexOf('Linux操作系统') >= 0, '13 周周五第 1 节回来了');
  ok(namesAt(v13, 5, 0).indexOf('Linux操作系统') < 0, '13 周周六那节没了');
  ok(!/改好了/.test(t2), '回话不再是一句空的「改好了」：' + t2);
  ok(/回到/.test(t2), '回话说明回到原来的时间：' + t2);
  ok(namesAt(v12, 4, 2).indexOf('马克思主义基本原理') >= 0, '周五别的课（马原）没被顺手挪走');
  ok(namesAt(v13, 4, 8).indexOf('计算机网络') >= 0, '13 周周五晚上那节计网也没被动');

  // ---- ③ 什么都不说的改课：不许回「改好了」 ----
  seed(); A.renderSchedule();
  const r3 = T.update_course({ name: 'Linux操作系统', said: '改一下Linux' });
  ok(/没改上/.test(String(r3 && r3.text || '')), '只给课程名时直接说没改上，不假装：' + String(r3 && r3.text || ''));

  // ---- ④ 撤销只退一步：两次改动分两次退 ----
  seed(); A.renderSchedule();
  T.update_course({ name: 'Linux操作系统', day: '周六', start_period: 1, end_period: 2,
    weeks: '13', from_week: '12', said: '把12周周五的改到13周周六' });
  T.update_course({ name: '马克思主义基本原理', day: '周日', start_period: 3, end_period: 5,
    weeks: '13', from_week: '13', said: '把13周周五的马原改到周日' });
  const one = T.undo({ said: '撤销' });
  ok(/撤回一步/.test(String(one && one.text || '')), '只说「撤销」只退一步');
  const s12 = A.occupancy(12), s13 = A.occupancy(13);
  ok(namesAt(s12, 4, 0).indexOf('Linux操作系统') < 0, '第一步（Linux 挪走）还在，只退了马原那步');
  ok(namesAt(s13, 4, 2).indexOf('马克思主义基本原理') >= 0, '马原退回到周五了');
  T.undo({ said: '都撤销', all: true });
  ok(namesAt(A.occupancy(12), 4, 0).indexOf('Linux操作系统') >= 0, '再说「都撤销」把剩下的也退了');

  // ---- ⑤ 装了这个版本之前就改乱了（没有快照）：用「把12周的改回周五」手工恢复 ----
  seed(); A.renderSchedule();
  moveToSat();
  const r5 = T.update_course({ name: 'Linux操作系统', said: '把12周的Linux改回周五' });
  const y12 = A.occupancy(12), y13 = A.occupancy(13);
  ok(namesAt(y12, 4, 0).indexOf('Linux操作系统') >= 0, '老版本留下的乱账也能一句话恢复（12 周周五回来了）');
  ok(namesAt(y13, 5, 0).indexOf('Linux操作系统') < 0, '周六那条补课一起收掉了');
  ok(/回到/.test(String(r5 && r5.text || '')), '回话说明回到原来的时间：' + String(r5 && r5.text || ''));

  // ---- 端到端：模型把「都撤销」翻成 update_course，代码照样执行真撤销 ----
  seed(); A.renderSchedule();
  moveToSat();
  let step = 0;
  w.NativeApi = { handle: async () => {
    step++;
    if (step === 1) {
      return { ok: true, data: { choices: [{ message: { role: 'assistant', content: '', tool_calls: [
        { id: 'u1', type: 'function', function: { name: 'update_course',
          arguments: JSON.stringify({ name: 'Linux操作系统' }) } }] } }] } };
    }
    return { ok: true, data: { choices: [{ message: { role: 'assistant', content: '改好了：Linux操作系统（周六第1-2节）' } }] } };
  } };
  w.AI.setKey('sk-test'); w.AI.open(); w.AI.clear();
  const ans = await w.AI.chat('把你做的所有操作都撤销');
  await new Promise((r) => setTimeout(r, 150));
  const e12 = A.occupancy(12), e13 = A.occupancy(13);
  ok(namesAt(e12, 4, 0).indexOf('Linux操作系统') >= 0, '端到端：模型调错了工具，代码还是真撤销了（12 周周五回来了）');
  ok(namesAt(e13, 5, 0).indexOf('Linux操作系统') < 0, '端到端：13 周周六的补课没了');
  ok(!/改好了/.test(String(ans || '')), '端到端：最后一句不再是「改好了」：' + String(ans || ''));

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
