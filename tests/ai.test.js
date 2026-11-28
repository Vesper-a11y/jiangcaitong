const fs=require('fs');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
global.window=dom.window; global.document=dom.window.document;
const path = require('path');
window.eval(fs.readFileSync(path.join(__dirname, '..', 'web', 'ai.js'), 'utf8'));
const AI=window.AI;
let pass=0, fail=0;
const ok=(c,m)=>{ if(c){pass++;console.log('[OK] '+m);} else {fail++;console.log('[FAIL] '+m);} };

// 模拟模型返回：第一次带 tool_calls，第二次给最终回答
let callCount=0;
let sentModel='';
const calls=[
  {id:'c1', type:'function', function:{name:'get_schedule', arguments:'{}'}},
  {id:'c2', type:'function', function:{name:'delete_all', arguments:'{}'}},   // 白名单外
  {id:'c3', type:'function', function:{name:'open_water', arguments:'{}'}},   // 高危
];
const executed=[];
window.NativeApi={
  handle: async(path,body)=>{
    callCount++;
    const payload=body.payload;
    if(callCount===1){
      sentModel=payload.model;
      return {ok:true, data:{choices:[{message:{role:'assistant',content:'',tool_calls:calls}}]}};
    }
    // 第二次（回灌后）：检查回灌的 tool 消息
    const toolMsgs=payload.messages.filter(m=>m.role==='tool');
    const contents=toolMsgs.map(m=>m.content).join(' | ');
    return {ok:true, data:{choices:[{message:{role:'assistant',content:'TOOLRESULTS:'+contents}}]}};
  }
};
AI.setKey('sk-test'); AI.open();
AI.registerTools({
  get_schedule: ()=>({text:'第1节 高等数学（2404）'}),
  open_water: ()=>{ executed.push('open_water'); return {text:'已开水'}; }
});
// 模拟用户点「取消」高危确认：不自动点确认，让 confirmRisk 的 Promise 挂着
// 为测完整流程，这里主动触发确认条的「取消」
AI.chat('今天有什么课').then(()=>{}).catch(()=>{});
setTimeout(()=>{
  // 确认条应出现（open_water 触发）
  const bar=document.getElementById('aiConfirm');
  ok(!!bar && !bar.hidden, '高危工具弹出确认条');
  if(bar){
    const yes=document.getElementById('aiConfYes');
    if(yes) yes.click();
  }
  setTimeout(()=>{
    // 检查回灌结果
    ok(executed.indexOf('open_water')>=0, '用户确认后 open_water 才执行');
    const histText=window.localStorage.getItem('zt.aichat')||'';
    ok(histText.indexOf('不在允许范围内')>=0, '白名单外工具被拒');
    ok(histText.indexOf('高等数学')>=0, '查课表结果进了对话');
    ok(histText.indexOf('TOOLRESULTS')>=0, '工具结果回灌给模型');
    ok(sentModel==='deepseek-flash', '请求里用的模型是 deepseek-flash（实际发出 '+sentModel+'）');
    // 设置面板（2026-11-28 反馈：去掉系统弹窗的本地网址）
    const setPanel=document.getElementById('aiSet');
    ok(!!setPanel, 'AI 设置面板存在（不用系统 prompt）');
    if(setPanel){
      const cardText=setPanel.textContent;
      ok(cardText.indexOf('DeepSeek')>=0, '面板显示服务商 DeepSeek');
      ok(cardText.indexOf('https://api.deepseek.com')>=0, '面板显示 DeepSeek 服务地址');
      ok(cardText.indexOf('deepseek-flash')>=0, '面板显示默认模型 deepseek-flash（V4.1 Flash）');
      ok(cardText.indexOf('deepseek-chat')>=0, '面板保留旧版模型兼容项');
      ok(document.body.textContent.indexOf('app.local')<0, '面板不出现本地网址 app.local');
      ok(!!document.getElementById('aiSetTest'), '面板有「测试」按钮');
      ok(!!document.getElementById('aiSetClear'), '面板有「清除」按钮');
      // 2026-11-28 反馈：设置改成独立界面 + 头部按钮不再共用 .ai-set 类名
      ok(!!document.getElementById('aiSettingsRoot'), '设置是独立全屏界面');
      ok(!!document.querySelector('.ai-setbtn'), '头部设置按钮用独立类名（不再误触）');
      const pick=document.getElementById('aiModelPick');
      ok(!!pick && pick.querySelectorAll('.ai-model-opt').length===2, '模型有两个可选按钮');
      ok(!!pick && pick.querySelector('.ai-model-opt.on') && pick.querySelector('.ai-model-opt.on').getAttribute('data-model')==='deepseek-flash', '默认选中 deepseek-flash');
    }
    console.log('\n汇总：通过 '+pass+'，失败 '+fail);
    process.exit(fail?1:0);
  }, 200);
}, 100);
