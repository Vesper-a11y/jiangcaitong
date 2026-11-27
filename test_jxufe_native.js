/* 用真实样本在 Node 里验证 jxufe_native.js 的解析器（不联网）。
   模拟 window + DOMParser + Android.reqJxufe，喂 jxufe_samples 里的 HTML。 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = (() => {
  try { return require('jsdom'); } catch (e) {
    console.log('SKIP: 需要 jsdom（npm i jsdom）');
    process.exit(0);
  }
})();

const ROOT = __dirname;
const SAMPLES = path.join(ROOT, 'jxufe_samples');

function readSample(name) {
  const p = path.join(SAMPLES, name);
  return fs.existsSync(p) ? fs.readFileSync(p, 'utf-8') : '';
}

// 构造一个极简 DOM 环境
const dom = new JSDOM('<!doctype html><html><body></body></html>');
global.window = dom.window;
global.document = dom.window.document;
global.DOMParser = dom.window.DOMParser;
global.TextEncoder = dom.window.TextEncoder || TextEncoder;
global.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
window.Android = { req: function () {}, reqJxufe: function () {} };

// 定义模拟响应：按 path 返回样本
const fixtures = {
  '/jw/common/showYearTerm.action': JSON.stringify({
    loginId: '2023123456', userCode: '201600012345', xn: '2026', xqM: '0', schoolCode: '10421'
  }),
  '/frame/droplist/getDropLists.action': JSON.stringify([
    { code: '2026-0', name: '2026-2027学年第一学期' }
  ]),
  '/student/wsxk.xskcb10319.jsp': readSample('wsxk.xskcb10319.html.utf8.txt'),
  '/student/xscj.stuckcj_data10421.jsp': readSample('grade_data.utf8.txt'),
  '/STU_BaseInfoAction.do': '<root><yhxh>201600040800</yhxh><xm>测试同学</xm>' +
    '<bjmc>金融学23-1班</bjmc><zymc>金融学</zymc><yxb>金融学院</yxb><rxnj>2025</rxnj></root>',
  '/student/data/classPath.xml': '<root><examArrangeInfo>exam.xml</examArrangeInfo></root>',
  '/STU_DynamicInitDataAction.do': '<root><item><kslcdm>3</kslcdm>' +
    '<kslcmc>期末考试</kslcmc></item></root>',
  '/student/ksap.ksapb_date.jsp': readSample('exam_table.utf8.txt'),
};

// 注入 reqJxufe 模拟：立刻回调 __jxufeDone
window.Android.reqJxufe = function (id, method, pathStr, body, referer, headers) {
  const key = Object.keys(fixtures).find((k) => pathStr.indexOf(k) === 0);
  const resp = { status: 200, body: key ? fixtures[key] : '', url: pathStr };
  setTimeout(() => window.__jxufeDone(id, JSON.stringify(resp)), 0);
};

// 加载模块
require(path.join(ROOT, 'web', 'jxufe_native.js'));

(async () => {
  const JX = window.JxufeApi;
  if (!JX) { console.log('FAIL: JxufeApi 未暴露'); process.exit(1); }
  let pass = 0, fail = 0;
  function check(name, cond, detail) {
    if (cond) { console.log('[OK] ' + name); pass++; }
    else { console.log('[FAIL] ' + name + ' :: ' + (detail || '')); fail++; }
  }

  // 触发一次同步（内部会 loadMainInfo → timetable → grades → exams → profile）
  let payload = null;
  try { payload = await JX.sync(); }
  catch (e) { console.log('[FAIL] sync 抛错: ' + e.message); fail++; }

  if (payload) {
    check('课表课程数 > 0', payload.courses && payload.courses.length > 0,
          'courses=' + (payload.courses || []).length);
    check('课表 unitCount=12', payload.unitCount === 12, String(payload.unitCount));
    check('成绩条数 > 0', payload.grades && payload.grades.courses.length > 0,
          'grades=' + ((payload.grades || {}).courses || []).length);
    check('绩点制 5.0', payload.grades && payload.grades.gpaScale === 5.0,
          String((payload.grades || {}).gpaScale));
    check('学期非空', payload.semester && payload.semester.name,
          JSON.stringify(payload.semester));
    // 抽查一门课结构
    if (payload.courses && payload.courses[0]) {
      const c = payload.courses[0];
      check('课程有 name/slots', !!c.name && Array.isArray(c.slots),
            JSON.stringify(c));
    }
  }

  // Cookie 解析测试
  const login = await JX.loginWithCookie('cookie: JSESSIONID=ABC123; other=1');
  check('Cookie 解析含 JSESSIONID 返回合理结果',
        login && typeof login.ok === 'boolean', JSON.stringify(login));

  console.log(`\n汇总：通过 ${pass}，失败 ${fail}`);
  process.exit(fail ? 1 : 0);
})();
