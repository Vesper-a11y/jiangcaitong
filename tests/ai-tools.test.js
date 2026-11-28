/* U105 回归：AI 能改课表、能真查电费、气泡不再是一条长条、下载地址规则。 */
const fs=require('fs');
const {JSDOM}=require('jsdom');
const path=require('path');
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
global.window=dom.window; global.document=dom.window.document;
const WEB=path.join(__dirname,'..','web');
const aiSrc=fs.readFileSync(path.join(WEB,'ai.js'),'utf8');
window.eval(aiSrc);
const AI=window.AI;
let pass=0, fail=0;
const ok=(c,m)=>{ if(c){pass++;console.log('[OK] '+m);} else {fail++;console.log('[FAIL] '+m);} };

let reqs=[];
window.NativeApi={ handle: async(p,b)=>{
  reqs.push(b.payload);
  return {ok:true,data:{choices:[{message:{role:'assistant',content:'好'}}]}};
} };
AI.setKey('sk-test'); AI.clear(); AI.open();

(async()=>{
  // 1) 工具清单：三个改课表的工具都在白名单里
  for (const n of ['add_course','update_course','delete_course']) {
    ok(aiSrc.indexOf("name: '"+n+"'")>=0, '工具清单里有 '+n);
    ok(aiSrc.indexOf(n+': { risk: false')>=0, n+' 在白名单且不需确认');
  }

  await AI.chat('你好');
  const sys=reqs[0].messages[0].content;
  ok(sys.indexOf('需要数据就直接调工具')>=0, '提示要求必须自己调工具');
  ok(sys.indexOf('不许让用户自己去别的页面看')>=0, '提示禁止把用户推给别的页面');
  ok(sys.indexOf('加课、改课')>=0, '提示写明能改课表');

  // 2) 气泡：微信 / QQ 那种带头像、按行的结构
  const rows=document.querySelectorAll('.ai-row');
  ok(rows.length>=2, '消息按行渲染，实际 '+rows.length+' 行');
  ok(!!document.querySelector('.ai-row.me .ai-av.me'), '自己一侧有头像');
  ok(!!document.querySelector('.ai-row.bot .ai-av.bot'), '助手一侧有头像');
  ok(!!document.querySelector('.ai-bub.ai-me'), '自己的气泡仍在');
  const css=fs.readFileSync(path.join(WEB,'app.css'),'utf8');
  ok(css.indexOf('width: fit-content; max-width: 72%')>=0, '气泡按内容收缩，不再占满一行');
  ok(css.indexOf('.ai-row.me { flex-direction: row-reverse; }')>=0, '自己的消息靠右');

  // 3) app.js：电费字段修对、三个工具接到原有保存函数
  const app=fs.readFileSync(path.join(WEB,'app.js'),'utf8');
  ok(app.indexOf('const elec = r.elec || null')>=0, '电费读的是 elec.bal');
  ok(app.indexOf('r.data.balance')<0, '旧的错误字段已删掉');
  ok(app.indexOf('电费没查到：')>=0, '查不到时给出具体原因而不是推给用户');
  for (const n of ['add_course:','update_course:','delete_course:']) {
    ok(app.indexOf(n)>=0, '实现里有 '+n);
  }
  ok(app.indexOf('saveMyCourses(list)')>=0, '改课表走原有的保存函数');
  ok(app.indexOf('myCourses().filter((c) => c.code !== mine.code)')>=0, '删课只删自己加的');

  // 4) U106：模型必须知道「现在第几周」和「课表里有哪些课」
  ok(app.indexOf('if (!room || !room.id)')>=0, '电费分清「没填一卡通号」和「没选宿舍」');
  ok(app.indexOf('这周|本周|当周|这一周')>=0, '「这周」按当前教学周算，不再默认整学期');
  ok(app.indexOf('const aiCanon')>=0, '课程名对到已有课程全名');
  ok(app.indexOf('AI.setContext(() =>')>=0, 'app.js 注入当前周次与课程清单');
  ok(aiSrc.indexOf('setContext: setContext')>=0, 'AI 暴露 setContext');

  AI.setContext(() => ({
    today:'11 月 28 日', weekday:'六', week:12, total:17,
    courses:[{name:'线性代数(工)',teacher:'孙翠英',room:'麦三教 3302'},
             {name:'数据结构与算法',teacher:'毛阿敏',room:'麦三教 3213'}]
  }));
  reqs.length=0;
  await AI.chat('这周加一节数学课');
  const sys2=reqs[0].messages[0].content;
  ok(sys2.indexOf('第 12 周')>=0, '上下文告诉模型现在是第 12 周');
  ok(sys2.indexOf('线性代数(工)')>=0, '上下文带上已有课程全名');
  ok(sys2.indexOf('不要反问第几周')>=0, '提示禁止反问周次');
  ok(sys2.indexOf('选最接近的那一门')>=0, '提示要求把「数学课」对到全名');
  ok(sys2.indexOf('孙翠英')>=0, '只带老师/教室这类最小字段');

  // 6) U107/U108：真开热水与饮水机、学校课本机调整、查询占位不留在历史、能操控软件
  ok(aiSrc.indexOf("name: 'open_dispenser'")>=0, '有开饮水机工具');
  ok(aiSrc.indexOf('open_dispenser: { risk: true')>=0, '开饮水机要确认');
  ok(aiSrc.indexOf('open_water: { risk: true')>=0, '开热水要确认');
  for (const n of ['stop_water','stop_dispenser','open_page','get_grades','get_exams','sync_school']) {
    ok(aiSrc.indexOf("name: '"+n+"'")>=0, '有 '+n+' 工具');
  }
  ok(aiSrc.indexOf('m.tool_calls && !String(m.content')>=0, '「正在查询」不再留在历史里');
  ok(aiSrc.indexOf('function plain')>=0, '气泡里去掉 ** 之类的符号');
  ok(app.indexOf('function withEdit')>=0 && app.indexOf('const edits = courseEdits()')>=0, '学校课走本机覆盖');
  ok(app.indexOf('saveCourseEdit(key, base)')>=0, '改学校课只写本机覆盖');
  // U109：课程号为空的行用「课名+老师」兜底，改完自检，声称成功必须有据
  ok(app.indexOf('function courseKey(c)')>=0, '课程键有兜底');
  ok(app.indexOf('if (!saveCourseEdit(key, base))')>=0, '写不进去要如实说');
  ok(app.indexOf('没改上：课表还是原来的样子')>=0, '改完自检没生效就说没改');
  ok(app.indexOf('gotoWeek(wk)')>=0, '改完跳到受影响的那一周');
  ok(aiSrc.indexOf('function forcedTool')>=0, '开水这类动作有代码兜底');
  ok(aiSrc.indexOf('function claimsDone')>=0, '能识别「改好了」这类话');
  ok(aiSrc.indexOf("return runCalls([fakeCall(ft)], '')")>=0, '模型不调工具就由代码直接执行');
  ok(aiSrc.indexOf('课表其实一点没动')>=0, '只回文字时会逼它真调工具');
  ok(aiSrc.indexOf('不要等确认')>=0, '提示词要求改课直接改');
  // U110：下单成功就算开成功（水表回报有延迟）；空回答要有兜底
  ok(app.indexOf('ok: true, unsure')>=0, '开水不再因为没回报就报失败');
  ok(app.indexOf('水表还没回报')>=0, '开水措辞如实但不误报失败');
  ok(aiSrc.indexOf('这次没答上来')>=0, '空回答重问一次再明说');
  ok(aiSrc.indexOf("'好了。'")>=0, '回答为空时用工具结果顶上');
  ok(aiSrc.indexOf('撤销|撤回|重做')>=0, '「撤销/重新执行」也走改课表');
  // U110：老师说「明天这节课挪到下周六」= 只调这一次，别的周不能跟着变
  ok(app.indexOf('e.skipWeeks.indexOf(week)')>=0, '被跳过的那一周不画原课');
  ok(app.indexOf("'my:mv:' + key")>=0, '单次调课补一条同名课');
  ok(app.indexOf('原本的课没动')>=0, '单次调课说清楚目标周原课没动');
  ok(app.indexOf("keep.length !== before.length")>=0, '撤销/删课会清掉单次调课那条');
  ok(app.indexOf('unsure: reallyRunning === false')>=0, '开水返回明确结果');
  ok(app.indexOf('open_dispenser: async')>=0, '饮水机实现接上了');
  ok(app.indexOf('open_page: (args)')>=0, '能打开任意页面');

  // U112：老师临时调课只改这一周；模型不传周次也不许整门课搬家
  ok(app.indexOf('const onlyOnce = hasPos && !wantsAll')>=0, '默认按单次调课判断');
  ok(app.indexOf('const moveWeeks = (args.weeks ? aiWeeks(args.weeks) : [shownWeek()])')>=0,
     '没传周次时只改当前这一周');
  ok(app.indexOf('skipSlots.forEach((s) => {')>=0, '只跳过被挪的那几节');
  ok(app.indexOf('.some((x) => x && x.w === w && x.d === s.day && x.p === s.period)')>=0,
     '节级跳过不重复写同一条');
  ok(app.indexOf('.some((c) => c.code === mvCode)')>=0, '复核补课是否真的画出来');
  ok(aiSrc.indexOf('apply_all')>=0, '工具支持「整门课换时间」开关');
  ok(aiSrc.indexOf('只改他点名的那一周')>=0, '提示词要求只改点名那一周');
  ok(aiSrc.indexOf('工具已经执行完了（水开了、课改了）')>=0
     || aiSrc.indexOf('水开了、课改了')>=0, '模型没答上来时用工具结果顶上');

  // U113：源周与目标周分开算、网费、快递跳转
  // U115：源周不再退回目标周，改为从原话里认（模型不填 from_week 时也不会动目标周）
  ok(app.indexOf('const guessed = fromWeeks.length ? 0 : aiSrcWeek(args.said);')>=0,
     '没给 from_week 时从原话里认源周');
  ok(app.indexOf('const srcWeeks = fromWeeks.length ? fromWeeks')>=0, '源周和目标周分开算');
  ok(aiSrc.indexOf("name === 'update_course' || name === 'move_day'")>=0,
     '改课时把用户原话递进去');
  ok(app.indexOf('const aiSrcWeek = (said) =>')>=0 && app.indexOf('const aiSrcDay = (said) =>')>=0,
     '能从原话认源周和源天');
  ok(app.indexOf('args.from_week')>=0, '改课支持「原来那一周」');
  ok(aiSrc.indexOf('from_week')>=0, '工具暴露 from_week');
  ok(aiSrc.indexOf('from_week=12')>=0, '提示词给了明确例子');
  ok(app.indexOf("api('/api/jf/records'")>=0, '电费工具顺手查网费');
  ok(app.indexOf('jfNetExpire(rr.rows')>=0, '网费按充值记录推到期');
  ok(app.indexOf('openJumpSheet')>=0 && app.indexOf('快递不是独立页面')>=0,
     '「打开快递」接到生活页取件弹层');
  ok(aiSrc.indexOf('网费、宽带、')>=0, '工具描述写明网费');

  // U114：「把这个星期的星期五调到下个星期六」= 整天全挪，不是挑一门；
  //       「跳到淘宝身份码」= 直接跳，不是回「打不开」
  ok(aiSrc.indexOf("name: 'move_day'")>=0, '有整天调课工具 move_day');
  ok(aiSrc.indexOf('move_day: { risk: false')>=0, '整天调课在白名单里');
  ok(app.indexOf('move_day: (args) =>')>=0, '整天调课实现接上了');
  ok(app.indexOf('const mvKey = isMyCourse({ code: key })')>=0, '自己加的课补课不套娃');
  ok(app.indexOf('cells.push({ w: src, d: s.day, p: s.period })')>=0,
     '只跳过那一天那几节，同门课这周别的天照上');
  ok(app.indexOf('cellSkip.some((x) => x && x.w === week')>=0, 'occupancy 认节级跳过');
  ok(app.indexOf('都挪到第 ')>=0, '整天挪完如实说挪了哪些课');
  ok(aiSrc.indexOf('不要为了挪去一门一门调 update_course')>=0, '提示词禁止一门一门挪');
  ok(aiSrc.indexOf('照他这一句重新改一遍')>=0, '用户说「不对」要重改，不许回头查课表');
  ok(aiSrc.indexOf("'(课|节|教室|周|星期|礼拜)'")>=0 || aiSrc.indexOf('|星期|礼拜)')>=0,
     '「星期」这类说法也算改课表');
  ok(aiSrc.indexOf('function forcedPage')>=0, '要跳页面没调工具时由代码兜底');
  ok(app.indexOf('const app = aiJumpApp(want);')>=0 && app.indexOf('jumpGo(app)')>=0,
     '说了哪个快递 App 就直接跳过去');
  ok(aiSrc.indexOf('说了哪个 App 就把那个填进 name')>=0, '工具描述写明快递能跳');

  // 5) 发版流程：对外下载地址固定
  const handoff=fs.readFileSync(path.join(__dirname,'..','..','交接包.md'),'utf8');
  ok(handoff.indexOf('对外下载地址一律用 GitHub 直链')>=0, '下载地址规则已写进交接包');

  // ---- U116：撤销是一个真工具，不是让模型再改一遍 ----
  ok(aiSrc.indexOf("undo: { risk: false, label: '撤销改课' }")>=0, 'undo 在白名单里');
  ok(aiSrc.indexOf("name: 'undo'")>=0, 'undo 有工具定义');
  ok(aiSrc.indexOf('function wantsUndo')>=0, '「都撤销」这句话由代码认，不靠模型翻译');
  ok(app.indexOf("undo: (args) => {")>=0, 'app.js 里有 undo 的实现');
  ok(app.indexOf("'add_course', 'update_course', 'delete_course', 'move_day'")>=0,
     '改课表的工具动手前都存快照');
  ok(app.indexOf('aiHasCell(wks0[0]')>=0, '「改回原来的星期」有单独路径并复核');

  console.log('\n汇总：通过 '+pass+'，失败 '+fail);
  process.exit(fail?1:0);
})().catch(e=>{ console.error(e); process.exit(1); });
