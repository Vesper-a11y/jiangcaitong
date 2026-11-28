/* U104 回归：工具调用之后，后续每一句都必须带上完整的工具记录。
 * 旧版第一轮请求把关键字段丢了，服务方整段拒收，界面统一报「没连上服务」，
 * 而且坏记录留在本机，之后每句都失败、重启也清不掉。 */
const fs=require('fs');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
global.window=dom.window; global.document=dom.window.document;
const path=require('path');
window.eval(fs.readFileSync(path.join(__dirname,'..','web','ai.js'),'utf8'));
const AI=window.AI;
let pass=0, fail=0;
const ok=(c,m)=>{ if(c){pass++;console.log('[OK] '+m);} else {fail++;console.log('[FAIL] '+m);} };

let reqs=[];
window.NativeApi={
  handle: async(p,b)=>{
    reqs.push(b.payload);
    const msgs=b.payload.messages||[];
    const hasCalls=msgs.some(m=>m.tool_calls&&m.tool_calls.length);
    if(!hasCalls && reqs.length===1){
      return {ok:true,data:{choices:[{message:{role:'assistant',content:'',tool_calls:[
        {id:'t1',type:'function',function:{name:'get_schedule',arguments:'{}'}}
      ]}}]}};
    }
    return {ok:true,data:{choices:[{message:{role:'assistant',content:'回答'+reqs.length}}]}};
  }
};
AI.setKey('sk-test');
AI.registerTools({ get_schedule: ()=>({text:'第1节 高等数学（2404）'}) });
AI.open();

(async()=>{
  // 第 1 轮：触发一次工具调用（原问 + 回灌）
  await AI.chat('今天有什么课');
  ok(reqs.length===2, '工具调用产生两次请求，实际 '+reqs.length);
  ok(reqs[1].messages.some(m=>m.role==='tool'&&m.tool_call_id==='t1'), '回灌的工具结果带编号');

  // 第 2 轮：以前从这里开始每句都被拒
  await AI.chat('你好');
  const r3=reqs[reqs.length-1];
  const tools=r3.messages.filter(m=>m.role==='tool');
  ok(tools.length===1, '后续提问仍带上工具结果，实际 '+tools.length+' 条');
  ok(tools.every(m=>!!m.tool_call_id), '后续提问的工具结果都带编号');
  ok(r3.messages.some(m=>m.tool_calls&&m.tool_calls.length), '后续提问带上对应的工具调用');
  ok(!!tools[0] && tools[0].tool_call_id==='t1', '工具结果和工具调用编号对得上');

  // 老版本留下的不完整记录：装上新版本后要能自愈，不发给模型
  window.localStorage.setItem('zt.aichat', JSON.stringify([
    {role:'user',content:'以前问的'},
    {role:'assistant',content:'',tool_calls:[{id:'x1',type:'function',function:{name:'get_schedule',arguments:'{}'}}]},
    {role:'tool',content:'孤儿结果'}
  ]));
  AI.open();
  reqs=[];
  window.NativeApi.handle=async(p,b)=>{
    reqs.push(b.payload);
    return {ok:true,data:{choices:[{message:{role:'assistant',content:'好'}}]}};
  };
  await AI.chat('你好');
  const r4=reqs[0];
  ok(!r4.messages.some(m=>m.role==='tool'&&!m.tool_call_id), '不完整工具记录不会发给模型');
  ok(!r4.messages.some(m=>m.tool_calls&&m.tool_calls.length), '没有对应结果的调用被降级成普通文字');

  // 失败提示说人话，不再笼统写「没连上服务」
  window.NativeApi.handle=async()=>({ok:false,status:400,error:'这次没答上来，清空对话再试一次'});
  await AI.chat('再问一句').catch(()=>{});
  const hist=JSON.parse(window.localStorage.getItem('zt.aichat')||'[]');
  const last=hist[hist.length-1];
  ok(!!last && last.content.indexOf('没连上服务')<0, '失败提示不再笼统写「没连上服务」');
  ok(!!last && last.content.indexOf('清空对话')>=0, '失败提示给出能操作的建议');

  // 清空对话入口存在且能清掉本机记录
  const btn=document.getElementById('aiSetClearChat');
  ok(!!btn, '设置页有「清空对话记录」入口');
  if(btn) btn.click();
  ok((window.localStorage.getItem('zt.aichat')||'[]').indexOf('再问一句')<0, '点清空对话后本机记录被清掉');

  console.log('\n汇总：通过 '+pass+'，失败 '+fail);
  process.exit(fail?1:0);
})().catch(e=>{ console.error(e); process.exit(1); });
