/* U97 回归：周五 3-5 节「计算社会科学」整门漏掉的两种成因。
 *   ① 同一格里有多行周次（分段上课）→ 原来只留最后一段；
 *   ② 周次写法认不出 → weeks 空数组，被 app.js 的周次过滤整门丢掉。
 * 用构造的教务课表 HTML 走完整 JxufeApi.sync()，不联网。
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.join(__dirname, '..');
const SAMPLES = path.join(ROOT, 'jxufe_samples');
const sample = (n) => {
  const p = path.join(SAMPLES, n);
  return fs.existsSync(p) ? fs.readFileSync(p, 'utf-8') : '';
};

/* 一行 9 列：[时段, 大节, 周一..周日]。
   周五（d=4）第 3-5 节那格故意放两个 <div>，各写一段周次。 */
function row(label, days) {
  return '<tr><td>上午</td><td><b>' + label + '</b></td>' +
    days.map((d) => '<td>' + (d || '<div></div>') + '</td>').join('') + '</tr>';
}
function course(name, teacher, weekLine, room) {
  return '<div><font>' + name + '</font><br>' + teacher + '<br>' +
    weekLine + '<br>' + room + '</div>';
}
const blank = '<div></div>';
const timetableHtml = '<html><body><table id="mytable">' +
  '<tr class="H"><td colspan="2"></td>' +
    ['星期一','星期二','星期三','星期四','星期五','星期六','星期日']
      .map((d) => '<td>' + d + '</td>').join('') + '</tr>' +
  // 「一」大节：周五 1-2 节程序设计基础；周一放单周课防回归
  row('一', [
    course('线性代数(工)', '孙翠英', '2-16单周[1-2]', '麦三教3302(麦庐园校区)'),
    blank, blank, blank,
    course('程序设计基础(数智)', '杨波3', '2-17周[1-2]', '数字经济实训大楼S404(麦庐园校区)'),
    blank, blank
  ]) +
  // 「二」大节：周五 3-5 节计算社会科学，两段周次分成两个块
  row('二', [
    // 周三：周次写法没有方括号，走「认不出」兜底
    course('高等数学I', '李敏', '2-17周 第3-5节', '麦二教2409(麦庐园校区)'),
    '', '', '',
    course('计算社会科学', '王老师', '1-9周[3-5]', '数字经济实训大楼S304(麦庐园校区)') +
      course('计算社会科学', '王老师', '11-17周[3-5]', '数字经济实训大楼S304(麦庐园校区)'),
    blank, blank
  ]) +
  // 「三」大节：周五 6-7 节
  row('三', [
    blank, blank, blank, blank,
    course('习近平新时代中国特色社会主义思想概论', '夏刚', '2-17周[6-7]', '麦三教3302(麦庐园校区)'),
    blank, blank
  ]) +
  '</table></body></html>';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
global.window = dom.window;
global.document = dom.window.document;
global.DOMParser = dom.window.DOMParser;
global.TextEncoder = dom.window.TextEncoder || TextEncoder;
global.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
window.Android = { req: function () {}, reqJxufe: function () {} };

const fixtures = {
  '/jw/common/showYearTerm.action': JSON.stringify({
    loginId: '2023123456', userCode: '201600012345', xn: '2026', xqM: '0', schoolCode: '10421'
  }),
  '/frame/droplist/getDropLists.action': JSON.stringify([
    { code: '2026-0', name: '2026-2027学年第一学期' }
  ]),
  '/student/wsxk.xskcb10319.jsp': timetableHtml,
  '/student/xscj.stuckcj_data10421.jsp': sample('grade_data.utf8.txt'),
  '/STU_BaseInfoAction.do': '<root><yhxh>201600040800</yhxh><xm>测试同学</xm>' +
    '<bjmc>测试班</bjmc><zymc>测试专业</zymc><yxb>测试学院</yxb><rxnj>2025</rxnj></root>',
  '/student/data/classPath.xml': '<root><examArrangeInfo>exam.xml</examArrangeInfo></root>',
  '/STU_DynamicInitDataAction.do': '<root><item><kslcdm>3</kslcdm>' +
    '<kslcmc>期末考试</kslcmc></item></root>',
  '/student/ksap.ksapb_date.jsp': sample('exam_table.utf8.txt'),
};
window.Android.reqJxufe = function (id, method, pathStr) {
  const key = Object.keys(fixtures).find((k) => pathStr.indexOf(k) === 0);
  const resp = { status: 200, body: key ? fixtures[key] : '', url: pathStr };
  setTimeout(() => window.__jxufeDone(id, JSON.stringify(resp)), 0);
};

require(path.join(ROOT, 'web', 'jxufe_native.js'));

(async () => {
  let pass = 0, fail = 0;
  const check = (name, cond, detail) => {
    if (cond) { console.log('[OK] ' + name); pass++; }
    else { console.log('[FAIL] ' + name + ' :: ' + (detail || '')); fail++; }
  };

  let payload = null;
  try { payload = await window.JxufeApi.sync(); }
  catch (e) { console.log('[FAIL] sync 抛错: ' + e.message); fail++; }

  if (payload) {
    const cs = payload.courses || [];
    const find = (n) => cs.find((c) => c.name.indexOf(n) >= 0);
    check('课表课程数 >= 4', cs.length >= 4, 'courses=' + cs.length);

    const calc = find('计算社会科学');
    check('周五第 3-5 节的计算社会科学被解析出来', !!calc,
      'names=' + cs.map((c) => c.name).join(' / '));
    if (calc) {
      check('第 12 周在该课周次内（多行周次不再互相覆盖）',
        calc.weeks.indexOf(12) >= 0, 'weeks=' + calc.weeks.join(','));
      check('第 10 周不在周次内（两段之间确实跳过了）',
        calc.weeks.indexOf(10) < 0, 'weeks=' + calc.weeks.join(','));
      const periods = calc.slots.filter((s) => s.day === 4).map((s) => s.period);
      check('周五占第 3/4/5 节',
        [2, 3, 4].every((p) => periods.indexOf(p) >= 0),
        'slots=' + JSON.stringify(calc.slots));
      check('教室读到 S304',
        (calc.room || '').indexOf('S304') >= 0, 'room=' + calc.room);
    }

    const math = find('高等数学I');
    check('周次写法认不出时课程仍在（整学期兜底）', !!math,
      'names=' + cs.map((c) => c.name).join(' / '));
    if (math) check('兜底课程第 12 周可见', math.weeks.indexOf(12) >= 0,
      'weeks=' + math.weeks.join(','));

    const lin = find('线性代数');
    if (lin) check('单周课仍只落在奇数周',
      lin.weeks.length > 0 && lin.weeks.every((w) => w % 2 === 1),
      'weeks=' + lin.weeks.join(','));
  }

  console.log('\n汇总：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
