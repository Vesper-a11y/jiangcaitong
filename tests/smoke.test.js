/* 固化回归测试（U88 起，替代以前「用完就删」的 _smoke_uNN.js）。
 *
 * 跑法：npm test   （= node tests/smoke.test.js，必须在项目根目录跑，
 *                    jsdom 从根目录的 node_modules 解析）
 *
 * 覆盖的都是真实翻过车的地方：
 *   - 1.3.11 学习页整页空白（const 重赋值）        → 启动后正文必须非空
 *   - 签到被查成两条                                → 导航与卡片结构断言
 *   - 0 元订单把账单挤出窗口                        → （账单分段，JS 层去重）
 *   - U87 诊断落盘（U94 面板已删，只留本机记录）
 *   - U88 问题反馈：QQ 号 2183143768 必须出现在弹层里
 *   - U88 「未登录」文案 / 快递胶囊等宽 / boot 补画不抛异常
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

const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };
const $ = (s) => w.document.querySelector(s);
const click = (el) => {
  if (!el) throw new Error('要点的元素不存在');
  el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
};
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

w.eval(fs.readFileSync(path.join(ROOT, 'web/native.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8'));

w.NativeApi.handle = async (path) => {
  if (path === '/api/zl/login') {
    return { ok: false, kind: 'auth', error: '账号或密码不正确' };
  }
  return { ok: true };
};

(async () => {
  // ---- 启动：不抛异常、学习页非空（1.3.11 白屏事故的回归线）----
  await tick(400);                      // boot 的补画在 idle/150ms 里
  ok(!!$('#tabbar') || !!$('.tab'), '底部标签栏没渲染出来');
  const anyPage = w.document.querySelector('section:not([hidden]), .page:not([hidden])');
  ok(!!anyPage, '启动后没有任何可见页面（1.3.11 白屏回归线）');
  // 2026-11-25：默认进学习页，「作业」这一颗二级导航要带灰底（.on），
  // 不能是两颗纯文字 —— 用户截图对比「下面应该有灰框」。
  const workNav = w.document.querySelector('#exNavSub [data-view="work"]');
  ok(!!workNav && workNav.classList.contains('on'),
    '默认视图下二级「作业」应高亮（灰底），用户：下面应该有灰框');

  // ---- 我的 → 账号管理 ----
  const meTab = $('[data-tab="me"]') || $('#tabMe');
  if (meTab) click(meTab);
  await tick(20);
  click($('#rowAccounts'));
  await tick(60);

  // ---- 故意登录失败一次（住理）----
  const zlRow = $('[data-acc="zl-account"]');
  ok(!!zlRow, '账号管理里没有住理那一行');
  if (zlRow) click(zlRow);
  await tick(30);
  if ($('#inputVal')) { $('#inputVal').value = '13800000000'; click($('#inputYes')); }
  await tick(30);
  if ($('#inputVal')) { $('#inputVal').value = '123456'; click($('#inputYes')); }
  await tick(120);

  // U89：账号行上不再画红色失败小字（连接完断开是正常现象），
  // 但失败原因必须仍落进诊断里 —— 到「诊断」弹层里核对。
  ok(!$('#accList .acc-diag'), '账号行不应该再有红色失败小字（U89 删掉）');

  // ---- 教务同步失败也要留痕（/api/sync 不走 diagOfPath 前缀）----
  w.NativeApi.handle = async (path) => {
    if (path === '/api/sync') {
      return { ok: false, error: '教务系统这会儿连不上' };
    }
    return { ok: true };
  };
  const syncBtn = $('#btnSync');
  ok(!!syncBtn, '找不到同步按钮');
  if (syncBtn) click(syncBtn);
  await tick(150);

  // ---- 落盘（diagSet 有 1.5 秒防抖）----
  await tick(1700);
  const saved = JSON.parse(w.localStorage.getItem('zt.diag') || '{}');
  ok(saved && saved.zl && saved.zl.ok === false, 'zt.diag 没写进去');
  ok(saved && saved.jw && saved.jw.ok === false, '教务同步失败没进诊断');
  if (saved && saved.jw) {
    ok(/教务系统这会儿连不上/.test(saved.jw.msg || ''),
      '教务那条没写清原因：' + (saved.jw.msg || ''));
  }
  ok(saved && saved.wt && saved.wt.ok === true, '慧生活成功态没进诊断');

  // ---- U94：诊断面板整个删了（用户：这个东西没有用）----
  ok(!$('#accDiag'), '账号页不应该再有「诊断」那一行');
  ok(!$('#sheetBody .diag-row'), '诊断弹层不应该还能打开');

  // ---- 关于（2026-11-25）：QQ群 / 更新日志 / 版本，GitHub 不许回来 ----
  const meBack = $('#permBack');
  if (meBack) click(meBack);
  await tick(20);
  const aboutRow = $('#rowFb');
  ok(aboutRow && /关于/.test(aboutRow.textContent), '「问题反馈」没换成「关于」');
  const aboutCard = aboutRow && aboutRow.closest('.about-single');
  ok(!!aboutCard, '「关于」没单独成一行一张卡（用户：单独作为一行）');
  ok(!$('.rilic'), '「关于/我的」行不该有图标（用户：不要图标）');
  click(aboutRow);
  await tick(40);
  const ft = $('#sheetBody') ? $('#sheetBody').textContent : '';
  ok(/1135309695/.test(ft), '关于弹层里没有 QQ群 1135309695');
  ok(!!$('#fbQqCopy'), '关于弹层没有可点的群号');
  ok(/App 版本/.test(ft), '关于弹层没有 App 版本');
  ok(/更新日志/.test(ft), '关于弹层没有更新日志');
  ok(/GitHub/.test(ft), '关于弹层应有 GitHub 项目入口（2026-11-27 用户要求加上）');
  ok(!/2183143768/.test(ft), '关于弹层还留着旧的问题反馈 QQ 号');
  // 2026-11-25：「加入」是小胶囊贴在群号同行，不许再独占一整行把弹层撑高。
  const joinBtn = $('#fbQqJoin');
  ok(!!joinBtn && !!joinBtn.closest('.about-row'),
    '「加入」按钮应贴在群号同一行（弹层别撑太高）');
  ok(!$('.about-btns'), '关于弹层不该再有独占一行的按钮区');
  // 2026-11-25：「已是最新版本」这类状态应在版本号同一行右侧，不独占一行撑高。
  const stNode = $('#aboutUpdateState');
  ok(!!stNode && !!stNode.closest('.av'),
    '更新状态应贴在版本号同一行（.av）里，不该再独占一行');
  // Gitee + GitHub 两个项目入口：点一下跳浏览器，文案正常软件话术。
  const starGiteeBtn = $('#aboutStarGiteeBtn');
  const starGithubBtn = $('#aboutStarGithubBtn');
  ok(!!starGiteeBtn && /Star/.test(starGiteeBtn.textContent), '关于弹层缺少 Gitee 项目入口');
  ok(!!starGithubBtn && /Star/.test(starGithubBtn.textContent), '关于弹层缺少 GitHub 项目入口');
  ok(!/发我|找我/.test(ft), '关于弹层文案不能出现「发我 / 找我」');
  // ---- U95⑩（2026-11-28）：更新日志拆成独立弹层，入口 + 分类 + 当前版本标记 ----
  ok(!!$('#aboutLogBtn'), '关于弹层应留「更新日志 ›」入口行');
  ok(!$('#aboutLogBox'), '关于弹层不该再有内联的更新日志块（已拆成独立弹层）');
  if ($('#aboutLogBtn')) {
    click($('#aboutLogBtn'));
    await tick(20);
    const lf = $('#sheetBody') ? $('#sheetBody').textContent : '';
    ok(/更新日志/.test(lf), '独立更新日志弹层缺标题');
    ok(/新功能/.test(lf) && /修复/.test(lf), '更新日志缺「新功能 / 修复」分类小标题');
    ok(/当前版本/.test(lf) && !!$('#sheetBody .log-tag'), '更新日志缺「当前版本」标记');
    ok(!!$('#sheetBody .log-sheet, .sheet.log-sheet'), '更新日志弹层缺 .log-sheet 外壳标记');
    ok(!/接口|JSON|Cookie|token|userId|抓包|逆向/.test(lf), '更新日志文案混进了内部术语');
  }
  if ($('#fbQqCopy')) click($('#fbQqCopy'));

  // ---- 生活页：未登录文案 + 快递胶囊（U88）----
  const lifeTab = $('[data-tab="life"]') || $('#tabLife');
  if (lifeTab) click(lifeTab);
  await tick(80);
  const zlM = $('#lifeZlMonth');
  ok(!!zlM && zlM.textContent === '未登录',
    '住理没登录时本月支出应显示「未登录」，实际：' + (zlM && zlM.textContent));
  const chips = w.document.querySelectorAll('#lifeJumpGrid .jump-btn');
  ok(chips.length === 6, '快递胶囊应为 6 颗（2+4），实际 ' + chips.length);

  // ---- 智慧江财（U92）：真查询组件 + 只读 + 不编数 ----
  const campusTab = $('[data-tab="campus"]');
  if (campusTab) click(campusTab);
  await tick(60);
  ok(!!$('#lifeJfWidget'), '生活页没有智慧江财组件卡');
  const jfe = $('#lifeJfElec'), jfn = $('#lifeJfNet');
  ok(!!jfe && jfe.textContent === '—',
    '没查过时电费应写「—」，实际：' + (jfe && jfe.textContent));
  ok(!!jfn && jfn.textContent === '—',
    '没查过时网费到期应写「—」，实际：' + (jfn && jfn.textContent));
  ok(/网费到期/.test(w.document.body.textContent), '卡上网费标签应改为「网费到期」');
  if ($('#lifeJfOpen')) { click($('#lifeJfOpen')); await tick(40); }
  const jt = $('#sheetBody') ? $('#sheetBody').textContent : '';
  ok(/智慧江财/.test(jt) && /一卡通号/.test(jt), '智慧江财弹层没有「一卡通号」入口');
  ok(!/自动识别房间|换房间（/.test(jt),
    '「自动识别房间 / 换房间」独立按钮不应出现（1.5.4 整套删除）');
  ok(!!$('#jfQueryBtn') && !!$('#jfEditAcc') && !!$('#jfRoomBtn'),
    '智慧江财弹层缺按钮（查询 / 一卡通号 / 选宿舍）');
  // 🔴 1.5.4：流程只剩「填一卡通号 → 自己选宿舍」，「选宿舍」是常驻按钮，
  //    不再是自动识别失败后的隐藏退路。
  ok($('#jfRoomBtn') && /选宿舍/.test($('#jfRoomBtn').textContent),
    '「选宿舍」应是常驻按钮，文案要叫「选宿舍」');
  // 🔴 U92：用户明确删掉的入口不许回来 —— 官方页、底部关闭、学号措辞、充值。
  ok(!/打开官方页|官方页/.test(jt), '弹层里不能有「官方页」按钮（用户：没有用）');
  ok(!/id="jfClose"|>关闭</.test($('#sheetBody').innerHTML),
    '弹层底部不能有「关闭」按钮（上面已有叉）');
  ok(!/学号/.test(jt), '弹层不允许出现「学号」（用户：只说一卡通号）');
  ok(!/去充值|打开微信|记一下余额/.test(jt),
    '弹层里混进了充值相关入口（用户明确先不做付款）');
  // 🔴 U93：文案要像正常软件 —— 实现细节只说给开发者听的话不许上墙。
  ok(!/接口|登录串|不代充值|不碰你的钱|不会自己编/.test(jt),
    '弹层里出现了内部实现话术（这个软件还有别人在用）');
  ok(/数据来自学校官方系统/.test(jt), '弹层顶部没有正常软件该有的数据来源说明');
  // 卡上的快捷按钮是「查询」，不是去付款。
  ok($('#lifeJfGo') && $('#lifeJfGo').textContent.indexOf('充值') < 0,
    '卡片快捷按钮不应再是「去充值」');
  // 🔴 1.5.4：自动识别绑定房间整套删掉，不许留半截死代码。
  //    网关 /interface/ 真机 401，留着只会引发串宿舍、误删房间这些真机 bug。
  const natSrc = fs.readFileSync(path.join(ROOT, 'web/native.js'), 'utf8');
  const appSrc = fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8');
  const natCode = natSrc.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
  ok(!/queryBoundRooms|boundRoom\s*\(/.test(natCode),
    'native.js 里还有「按一卡通号自动识别房间」的调用（1.5.4 已删）');
  ok(!/jout\.room\s*=/.test(natCode),
    '/api/jf/query 不该再回传自动识别到的房间');
  ok(/电费还没选宿舍/.test(natSrc),
    '没选宿舍时 /api/jf/query 要明确提示点「选宿舍」');
  ok(!/id="jfAutoBtn"|async function jfAutoRoom/.test(appSrc),
    'app.js 里还有「自动识别房间」独立入口');
  ok(/async function jfPickRoom/.test(appSrc),
    '选宿舍 jfPickRoom 必须保留（唯一的房间来源）');
  ok(/bind\('jfRoomBtn', jfPickRoom\)/.test(appSrc),
    '「选宿舍」按钮的事件没绑上');
  // 🔴 U94：旧的问题反馈页（运行日志 / 打码 / 预览）删干净，别再露出内部话术。
  const htmlSrc = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
  ok(!/page-feedback|附带运行日志|看看要发什么/.test(htmlSrc),
    '旧问题反馈页没删干净（运行日志这类话不能给用户看）');
  ok(!/发我就行|找我/.test(jt) && !/发我就行|找我/.test(w.document.body.textContent),
    '界面里出现了「发我 / 找我」这类话');

  // ---- 1.5.4：没选宿舍就点查询 → 只给提示，绝不凭空落盘房间 ----
  //    接口即便（老版本那样）顺带回一个 room，前端也不许认它。
  w.localStorage.setItem('zt.jfacc', JSON.stringify('2202500000'));
  w.localStorage.removeItem('zt.jfroom');
  w.NativeApi.handle = async (path) => {
    if (path === '/api/jf/query') {
      return { ok: true, elec: null,
        // 故意塞一个接口回传的房间，验证 1.5.4 之后不再保存它
        room: { id: 6472, name: '接口顺带回的房间' },
        errs: ['电费还没选宿舍，点「选宿舍」选到你自己的宿舍'] };
    }
    if (path === '/api/jf/records') return { ok: true, rows: [], errs: [] };
    return { ok: true };
  };
  if (! $('#jfQueryBtn')) { if ($('#lifeJfOpen')) { click($('#lifeJfOpen')); await tick(40); } }
  if ($('#jfQueryBtn')) {
    click($('#jfQueryBtn'));
    await tick(120);
    const jr = JSON.parse(w.localStorage.getItem('zt.jfroom') || 'null');
    ok(!jr, '接口回传的房间被存了下来（1.5.4 房间只能来自自己选宿舍）');
    ok(/电费还没选宿舍/.test($('#sheetBody') ? $('#sheetBody').textContent : ''),
      '没选宿舍时没把「点选宿舍」的提示摆出来');
  }

  // ---- 1.5.4：输号只保存账号，不自动查询、不自动换房间 ----
  // 🔴 1.5.2 的回归仍要守住：jfAskAcc 绝不能删掉用户手选好的房间。
  {
    const m = /async function jfAskAcc\([\s\S]*?\n\}/.exec(appSrc);
    const bodyNoComment = m ? m[0].replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '') : '';
    ok(!!m && !/store\.del\(KEY\.jfRoom\)/.test(bodyNoComment),
      'jfAskAcc 里不应再删房间（删了会让已选房间丢失、电费查不出）');
  }
  // 没选宿舍时输一个号：只存账号，不发查询，也不凭空造房间。
  w.localStorage.setItem('zt.jfacc', JSON.stringify('2202500000'));
  w.localStorage.removeItem('zt.jfroom');
  let queryCalls = 0;
  w.NativeApi.handle = async (p) => {
    if (p === '/api/jf/query') { queryCalls++; return { ok: true, elec: null, errs: [] }; }
    if (p === '/api/jf/records') return { ok: true, rows: [], errs: [] };
    return { ok: true };
  };
  if ($('#lifeJfOpen')) { click($('#lifeJfOpen')); await tick(40); }
  const accBtn = $('#jfEditAcc');
  ok(!!accBtn, '电费弹层找不到「输入一卡通号」按钮');
  if (accBtn) {
    click(accBtn);
    await tick(30);
    const inp = $('#inputVal'), yes = $('#inputYes');
    ok(!!inp && !!yes, '输入一卡通号的弹层没弹出');
    if (inp && yes) {
      inp.value = '2202509999';
      click(yes);
      await tick(180);
      ok(w.localStorage.getItem('zt.jfacc') === JSON.stringify('2202509999'),
        '新一卡通号没存下');
      ok(queryCalls === 0,
        '没选宿舍时输完号不该自动发查询（1.5.4 只保存，选了宿舍才查）');
      ok(!w.localStorage.getItem('zt.jfroom'),
        '输号不该凭空造一个房间（房间只能来自自己选宿舍）');
    }
  }

  // 🔴 1.5.3 回归：房间带账号归属，换号后不能拿别人的房间去查。
  // 走真实流程：旧号+旧房间 → 点「改一卡通号」输入新号 → 弹层不该再显示旧房间名。
  w.localStorage.setItem('zt.jfacc', JSON.stringify('1111111111'));
  w.localStorage.setItem('zt.jfroom', JSON.stringify({ id: 4242, name: 'A号宿舍', acc: '1111111111' }));
  w.localStorage.setItem('zt.jfdata', JSON.stringify({ at: 1, elec: { bal: '88', room: 'A号宿舍', acc: '1111111111' }, errs: [] }));
  w.NativeApi.handle = async (path) => {
    if (path === '/api/jf/query') return { ok: true, elec: null, errs: [] };
    if (path === '/api/jf/records') return { ok: true, rows: [], errs: [] };
    return { ok: true };
  };
  if ($('#lifeJfOpen')) { click($('#lifeJfOpen')); await tick(40); }
  const accBtn3 = $('#jfEditAcc');
  if (accBtn3) {
    click(accBtn3); await tick(30);
    const inp3 = $('#inputVal'), yes3 = $('#inputYes');
    if (inp3 && yes3) {
      inp3.value = '2222222222'; click(yes3); await tick(200);
      ok(!/A号宿舍/.test($('#sheetBody') ? $('#sheetBody').textContent : ''),
        '换了账号后还显示上一个号的房间（串号了）');
      // 旧房间记录可以留在本机（换回旧号还能用），但 acc 必须还是旧号——
      // 这样 jfRoom() 在新号下不会认它。验证存储里的归属没被改写。
      const room3 = JSON.parse(w.localStorage.getItem('zt.jfroom') || 'null');
      ok(room3 && String(room3.acc) === '1111111111',
        '换号后旧房间的账号归属丢了（可能被误补成新号）');
    }
  }

  // 🔴 1.5.4 回归：查询只读接口余额，绝不改写用户手选的房间。
  w.localStorage.setItem('zt.jfroom', JSON.stringify({ id: 555, name: '用户手选的房间' }));
  w.NativeApi.handle = async (path) => {
    if (path === '/api/jf/query') return { ok: true, elec: null, errs: [] };
    if (path === '/api/jf/records') return { ok: true, rows: [], errs: [] };
    return { ok: true };
  };
  if ($('#lifeJfOpen')) { click($('#lifeJfOpen')); await tick(40); }
  const accBtn2 = $('#jfEditAcc');
  if (accBtn2) {
    click(accBtn2); await tick(30);
    const inp2 = $('#inputVal'), yes2 = $('#inputYes');
    if (inp2 && yes2) {
      inp2.value = '2202509999'; click(yes2); await tick(180);
      const kept = JSON.parse(w.localStorage.getItem('zt.jfroom') || 'null');
      ok(kept && kept.id === 555,
        '查询把用户手选的房间弄丢了（电费就查不出了）');
    }
  }

  // ---- 🔴 1.5.4：学分页面 / 培养计划整套删除，不许留悬空入口 ----
  ok(!/exViewCredit|crEmpty|crBody|goBind5/.test(htmlSrc),
    'index.html 里还留着「学分」页面的 DOM');
  const appCode = appSrc.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
  ok(!/renderCredit|creditProgress|creditOpen|CR_FIRST|passedOf|planIndex|gitag/.test(appCode),
    'app.js 里还留着学分进度 / 培养计划的函数或标签');
  ok(!/DATA\.plan|res\.plan|plan:\s*res\.plan/.test(appCode),
    'app.js 还在消费 DATA.plan（培养计划数据）');
  const natCode2 = natSrc.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
  ok(!/parsePlan|splitCourseCell|plan:\s*pl/.test(natCode2),
    'native.js 里还留着培养计划解析');
  const jxSrc = fs.readFileSync(path.join(ROOT, 'web/jxufe_native.js'), 'utf8');
  const jxCode = jxSrc.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
  ok(!/parsePlan|plan:\s*pl|await plan\(\)/.test(jxCode),
    'jxufe_native.js 里还留着培养计划解析');
  const pyEams = fs.readFileSync(path.join(ROOT, 'jxufe_eams.py'), 'utf8');
  ok(!/def parse_plan|def plan\(/.test(pyEams),
    'jxufe_eams.py 里还留着培养计划解析');
  ok(!fs.existsSync(path.join(ROOT, 'jxufe_samples/plan_data.utf8.txt')) &&
     !fs.existsSync(path.join(ROOT, 'jxufe_samples/plan_page.utf8.txt')),
    '培养计划样本文件没删干净');

  // ---- 🔴 1.5.4：学期设置默认校历 + 日历选完日期不丢（SEM_DRAFT） ----
  ok(/const SEM_DEFAULT = \{ enrollYear: 2025, startDate: '2026-09-07', weeks: 17 \}/.test(appSrc),
    '默认校历不是截图里那套 2025 级 / 2026-09-07 / 17 周');
  ok(/SEM_DRAFT\.start = ymd\(sel\)/.test(appSrc),
    '日历「完成」没把新日期写进草稿（真机症状：选了跟没选一样）');
  // 真走一遍：我的 → 学期设置 → 改总周数 → 点开学日期 → 日历选一天 → 完成 → 保存。
  // 原来这一趟回来周数会退回 CFG 的旧值；草稿态修的就是这个。
  const meTab2 = $('[data-tab="me"]') || $('#tabMe');
  if (meTab2) { click(meTab2); await tick(30); }
  if ($('#rowSemester')) {
    click($('#rowSemester'));
    await tick(40);
    ok(!!$('#setName') && !!$('#setWeeks') && !!$('#meStartDate'),
      '学期设置面板没打开');
    if ($('#setName') && $('#setWeeks') && $('#meStartDate')) {
      ok($('#setName').value === '2025', '默认入学年份不是 2025');
      ok($('#meStartDate').value === '2026-09-07', '默认学期开始日不是 2026-09-07');
      ok($('#setWeeks').value === '17', '默认总周数不是 17');
      $('#setWeeks').value = '20';
      $('#setWeeks').dispatchEvent(new w.Event('input', { bubbles: true }));
      await tick(20);
      click($('#meStartDate'));
      await tick(40);
      ok(!!$('#calDone'), '点「学期开始日」没打开自定义日历');
      const dayBtn = $('#sheetBody [data-day="2026-09-16"]') ||
                     w.document.querySelector('#sheetBody [data-day]');
      if (dayBtn) { click(dayBtn); await tick(20); }
      if ($('#calDone')) { click($('#calDone')); await tick(40); }
      ok($('#setWeeks') && $('#setWeeks').value === '20',
        '日历来回一趟把「总周数」弄丢了（草稿态没生效）');
      ok($('#meStartDate') && /^\d{4}-\d{2}-\d{2}$/.test($('#meStartDate').value || ''),
        '日历选完没把日期回填到「学期开始日」');
      if ($('#setSave')) {
        click($('#setSave'));
        await tick(40);
        const cfg = JSON.parse(w.localStorage.getItem('zt.settings') || '{}');
        ok(cfg.weeks === 20, '点保存后总周数没落盘（学期还是调不了）');
        ok(/^\d{4}-\d{2}-\d{2}$/.test(cfg.startDate || ''),
          '点保存后开学日期没落盘');
      }
    }
  }

  done();
})().catch((e) => { fails.push('抛异常：' + (e && e.stack || e)); done(); });

function done() {
  if (fails.length) {
    console.log('FAIL');
    fails.forEach((f) => console.log(' - ' + f));
    process.exit(1);
  }
  console.log('PASS：启动非空 + 诊断已删 + 问题反馈(QQ) + 未登录文案 + 快递胶囊 6 颗 + 智慧江财（输号→选宿舍）+ 学分页整套删除 + 学期设置默认校历与日历草稿态');
  process.exit(0);
}
