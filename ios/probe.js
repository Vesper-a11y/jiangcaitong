// 酱菜通 · iOS 探测（Scriptable）
//
// 怎么用：
//   1. App Store 搜 "Scriptable"（免费，作者 Simon Støvring，别装错）
//   2. 打开它，点右上角 ＋
//   3. 把本文件全部内容粘进去，左上角 Done 保存
//   4. 点这个脚本运行
//
// 一运行就先弹出一页空表，**每跑完一项当场填一行**。
// 所以如果它卡住了，你能直接看见卡在第几项 —— 不用猜。
//
// 只探四件 —— 正好是「把现有界面搬过来」绕不过去的四道关：
//   网络：能不能带自己的请求头、能不能不跟跳转、能不能读到服务器下的 Cookie
//   钥匙串：密码存在哪（安卓那边是加密存储，这边要看有没有对应的地方）
//   网页视图：整个界面是装在这里面的，还要能跟外面互相说话
//   文件：兜底存储
//
// 🔴 每一项都有硬超时。上一版只给单次请求设了超时、脚本本身没有兜底，
//    结果在手机上转了两分钟还没停（2026-11-05）。探测脚本自己会挂住，
//    那它就没有意义了 —— 它存在的目的就是「把不确定变成确定」。
//
// 🔴 计时要走 Timer，不能用 setTimeout —— 见下面 hasTimer 那里的说明。

const HOST = "https://www.zua.edu.cn/";

// 每一项的上限。加起来最坏 30 秒左右，必然出结果。
const T_NET = 6000;

// 页面回调脚本是否打得通（两条通道哪个先响记哪个）
let bridgeHit = "";
// 结果页是否已经活过来（能应答 JavaScript）
let pageAlive = false;

let wv = null;
let total = 0;
let pass = 0;
const rows = [];          // 结果先攒在内存里：页面出不来时靠它兜底

// 🔴 Scriptable 跑在 JavaScriptCore 上，**没有 setTimeout / setInterval**。
//    它们属于 DOM，不属于 JavaScript 本身 —— 浏览器有，这里没有。
//    宿主给的是 Timer.schedule(毫秒, 是否重复, 回调)。
//
//    2026-11-05 在 iPhone X 上撞到：整个 withTimeout 建在 setTimeout 上，
//    于是四个网络项全在第一行抛 `Can't find variable: setTimeout`，
//    一条真数据都没拿到 —— 而钥匙串、文件那两项没套它，所以是全绿的。
//    这个错在本地永远复现不出来（Node 有 setTimeout），真机上才现形。
function hasTimer() {
  return typeof Timer !== "undefined" && typeof Timer.schedule === "function";
}

/** 等一会儿。没有计时器就立刻返回 —— 宁可不准，也不要崩。 */
function sleep(ms) {
  return new Promise(function (resolve) {
    if (!hasTimer()) { resolve(); return; }
    Timer.schedule(ms, false, resolve);
  });
}

/** 给任意 Promise 套一个硬超时。到点就抛，不管对面还没回来。 */
function withTimeout(p, ms) {
  if (!hasTimer()) return p;      // 没有计时器就不判超时，好过整个脚本废掉
  return Promise.race([
    p,
    new Promise(function (_, rej) {
      Timer.schedule(ms, false, function () { rej(new Error("超时")); });
    }),
  ]);
}

/** 往结果页填一行。填不进去也不要紧 —— 不能因为显示失败把探测带崩。 */
async function row(name, state, detail) {
  total += 1;
  if (state === "通") pass += 1;
  rows.push({ n: String(name), s: String(state), d: String(detail) });
  if (!wv) return;
  try {
    await withTimeout(
      wv.evaluateJavaScript(
        "addRow(" + JSON.stringify(String(name)) + "," +
        JSON.stringify(String(state)) + "," +
        JSON.stringify(String(detail)) + ")"), 3000);
  } catch (e) { /* 显示失败不影响结论 */ }
}

/** 跑一项，异常/超时都变成一行「没过」，绝不往外抛。 */
async function step(name, fn) {
  try {
    await fn();
  } catch (e) {
    const msg = String((e && e.message) || e);
    if (msg === "超时") {
      await row(name, "超时", "这段时间内没回来，可能是网络卡住了");
    } else {
      await row(name, "没通", msg);
    }
  }
}

const SHELL = `<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>
  body{margin:0;padding:calc(16px + env(safe-area-inset-top)) 16px
       calc(20px + env(safe-area-inset-bottom));
       font:15px/1.5 -apple-system,sans-serif;color:#1a1a1a;background:#fff}
  h2{margin:0 0 2px;font-size:19px}
  #sum{margin:0 0 14px;color:#8a8a8a;font-size:13px}
  table{border-collapse:collapse;width:100%}
  td{padding:9px 8px;border-bottom:1px solid #eee;vertical-align:top}
  td.n{width:38%;font-weight:500}
  td.s{width:22%;font-weight:600;white-space:nowrap}
  td.d{font-size:12.5px;color:#666}
  .ok{color:#0a7d32}.bad{color:#b3261e}.warn{color:#a06000}.wait{color:#bbb}
  #tip{margin:16px 0 0;color:#8a8a8a;font-size:12px;line-height:1.6}
</style>
<h2>探测结果</h2>
<p id=sum>正在开始…</p>
<table id=tb></table>
<p id=tip>跑完把这一页截图发回去就行。<br>
每跑完一项就填一行，卡住的话能看见停在哪。</p>
<script>
function addRow(n, s, d){
  var tb = document.getElementById('tb');
  var tr = document.createElement('tr');
  var cls = s === '通' ? 'ok' : (s === '超时' || s === '测不出') ? 'warn' : 'bad';
  tr.innerHTML = '<td class="n"></td><td class="s ' + cls + '"></td><td class="d"></td>';
  tr.children[0].textContent = n;
  tr.children[1].textContent = s;
  tr.children[2].textContent = d;
  tb.appendChild(tr);
  var ok = tb.querySelectorAll('.ok').length;
  document.getElementById('sum').textContent =
    '已跑 ' + tb.children.length + ' 项 · 通过 ' + ok;
}
function done(){
  document.getElementById('sum').textContent =
    '跑完了 · 通过 ' + document.querySelectorAll('.ok').length +
    ' / ' + document.getElementById('tb').children.length + ' 项';
  document.getElementById('tip').textContent = '把这一页截图发回去就行。';
}
</script>`;

async function main() {
  // ---------------------------------------------------------------- 结果页
  // 🔴 顺序是这里唯一的坑，2026-11-05 在 iPhone X 上栽过一次：
  //    WebView.loadHTML() 返回的 Promise **要等页面真正呈现出来之后才完成**。
  //    写成 `await loadHTML(...)` 再 `present()`，那个 await 永远等不到 ——
  //    6 秒后超时、wv 被判成 null，页面一次都没出现过，最后只剩一句弹窗。
  //    正确顺序是：把页面塞进去（不 await）→ present（await）→ 再往里填。
  try {
    wv = new WebView();

    // 桥的两条通道，都要在 present 之前挂好
    try {
      wv.shouldAllowRequest = (req) => {
        const u = String((req && req.url) || "");
        if (u.indexOf("hyt-probe://") === 0) {
          bridgeHit = "scheme:" + u;
          return false;          // 拦下来 = 不发出去。桥可以建在这上面。
        }
        return true;
      };
    } catch (e) { /* 不支持就靠下面那条 */ }

    try {
      wv.messageHandlers.hyt = (msg) => {
        bridgeHit = "msg:" + String(msg);
        return "ok";
      };
    } catch (e) { /* 不支持就用 scheme 拦 */ }

    wv.loadHTML(SHELL).catch(function () {});
    try { wv.present(false); } catch (e) { /* 下面靠页面能不能应答来判断 */ }

    // ⚠️ 不去 await present() 的返回值：它在 Scriptable 里到底什么时候完成
    //    没有保证（万一要等用户关掉视图，就白等一场）。
    //    直接问页面「你活着吗」更可靠 —— 能应答就等于显示了、也能执行脚本了。
    for (let i = 0; i < 8 && !pageAlive; i++) {
      await sleep(900);
      try {
        const v = await withTimeout(wv.evaluateJavaScript("1+1"), 2000);
        if (v === 2) pageAlive = true;
      } catch (e) { /* 还没起来，继续等 */ }
    }
    if (!pageAlive) wv = null;
  } catch (e) {
    wv = null;
  }

  await row("网页视图装得进", wv ? "通" : "装不进",
      wv ? "结果页已经显示出来了" : "页面起不来，结果改用系统弹窗给你");

  // ------------------------------------------------------------------ 网络
  // ① 能不能连出去（这一步不通，后面全免谈）
  await step("能连出去", async () => {
    const r = new Request(HOST);
    r.method = "GET";
    r.timeoutInterval = 6;
    r.headers = { "User-Agent": "JiangCaiTong/0.45.2" };
    const body = await withTimeout(r.loadString(), T_NET);
    await row("能连出去", r.response.statusCode > 0 ? "通" : "没通",
        "状态 " + r.response.statusCode + " · " + body.length + " 字节");
  });

  // ② 请求头带得过去吗（学校系统要 Referer，缺了会被当爬虫）
  //    拿回显服务看 —— 学校页面看不出头到底送到没有。
  await step("请求头带得过去", async () => {
    const r = new Request("https://httpbin.org/headers");
    r.timeoutInterval = 6;
    r.headers = { "User-Agent": "JiangCaiTong/0.45.2", "Referer": HOST };
    const txt = await withTimeout(r.loadString(), T_NET);
    const sent = txt.indexOf("JiangCaiTong") >= 0 && txt.indexOf("zua.edu.cn") >= 0;
    await row("请求头带得过去", sent ? "通" : "没带上",
        sent ? "服务端收到了自定义 UA 和 Referer" : "回显里找不到，可能被改写");
  });

  // ③ 能不能「不跟跳转」—— 教务登录成功与否全看 302，跟了跳转就分不清了
  await step("能不跟跳转", async () => {
    const r = new Request("https://httpbin.org/redirect/2");
    r.timeoutInterval = 6;
    r.followRedirects = false;
    await withTimeout(r.loadString(), T_NET);
    const code = r.response.statusCode;
    await row("能不跟跳转", code === 302 ? "通" : "没生效",
        "拿到状态 " + code + "（要的是 302）");
  });

  // ④ 能不能读到服务器下的 Cookie —— 登录态就靠它
  await step("能读到 Cookie", async () => {
    const r = new Request("https://httpbin.org/cookies/set/hyt_probe/1");
    r.timeoutInterval = 6;
    r.followRedirects = false;
    await withTimeout(r.loadString(), T_NET);
    const h = r.response.headers || {};
    let sc = h["set-cookie"] || h["Set-Cookie"] || "";
    if (Array.isArray(sc)) sc = sc.join("; ");
    await row("能读到 Cookie", sc ? "通" : "读不到",
        sc ? String(sc).slice(0, 60) : "响应头里没有 set-cookie");
  });

  // ------------------------------------------------------------------ 钥匙串
  // 密码放这儿。安卓那边是加密存本机，这边看有没有对应的能力。
  await step("钥匙串", async () => {
    if (typeof Keychain === "undefined") {
      await row("钥匙串", "没有", "这台机器没有钥匙串，密码只能放文件");
      return;
    }
    const k = "hyt.probe." + Date.now();
    Keychain.set(k, "v1");
    const back = Keychain.get(k);
    Keychain.remove(k);
    await row("钥匙串", back === "v1" ? "通" : "存进去读不回",
        back === "v1" ? "能存能读能删" : ("读回的是 " + back));
  });

  // ------------------------------------------------------------------ 文件
  await step("文件读写", async () => {
    const fm = FileManager.local();
    const p = fm.joinPath(fm.documentsDirectory(), "hyt-probe.txt");
    fm.writeString(p, "probe " + Date.now());
    const back = fm.readString(p);
    await row("文件读写", "通", "写入后读回 " + back.length + " 字节");
  });

  // ------------------------------------------------------ 网页视图（后半截）
  // 界面要跑在这上面，还要能跟外面互相说话。三项缺一不可。
  if (wv) {
    await step("能读回页面里的值", async () => {
      const v = await withTimeout(wv.evaluateJavaScript("1+1"), 4000);
      await row("能读回页面里的值", v === 2 ? "通" : "值不对", "读到 " + v);
    });

    // 页面回调脚本 —— 桥优先走这条，因为是 Scriptable 自己的通道，最稳
    await step("页面能回调脚本", async () => {
      bridgeHit = "";
      try {
        await withTimeout(wv.evaluateJavaScript(
          "window.webkit.messageHandlers.hyt.postMessage('ping')"), 4000);
      } catch (e) { /* 没这条路就看下面 scheme 那条 */ }
      await sleep(500);
      const ok = bridgeHit.indexOf("msg:") === 0;
      await row("页面能回调脚本", ok ? "通" : "没通",
          ok ? "脚本收到了 " + bridgeHit : (bridgeHit || "页面没有这个通道"));
    });

    // 拦自定义跳转 —— 上面那条不通时的备选，用 iframe 触发，
    // 这样即使拦不住也只是夹层加载失败，结果页本身不会被导航走。
    await step("能拦住自定义跳转", async () => {
      bridgeHit = "";
      try {
        await withTimeout(wv.evaluateJavaScript(
          "var f=document.createElement('iframe');" +
          "f.src='hyt-probe://ping';" +
          "document.body.appendChild(f);'ok'"), 4000);
      } catch (e) { /* 忽略 */ }
      await sleep(700);
      const ok = bridgeHit.indexOf("scheme:") === 0;
      await row("能拦住自定义跳转", ok ? "通" : "没拦住",
          ok ? bridgeHit : "这个没触发也不致命，上一条通就够");
    });
  }

  // ---------------------------------------------------------------- 收尾
  // 🔴 这两句不能少。2026-11-05 第一次改这里时，整段 main() 被替换掉了，
  //    收尾跟着一起没了 —— 用户那边表现为「跑完什么都不给」，
  //    前面跑出来的结果全部作废。模拟器一跑就露出来了。
  if (wv) {
    try { await withTimeout(wv.evaluateJavaScript("done()"), 3000); }
    catch (e) { /* 收尾失败无所谓，行已经填进去了 */ }
  } else {
    await showInAlert();
  }
}

/** 页面起不来时的兜底：把已经跑出来的结果用系统弹窗一页页给你。 */
async function showInAlert() {
  const per = 5;
  const parts = [];
  for (let i = 0; i < rows.length; i += per) parts.push(rows.slice(i, i + per));
  if (!parts.length) parts.push([]);
  for (let i = 0; i < parts.length; i++) {
    const a = new Alert();
    a.title = "探测结果 " + pass + "/" + total + "（" + (i + 1) + "/" + parts.length + "）";
    const body = parts[i].map((r) => r.n + " · " + r.s + "\n" + r.d).join("\n\n");
    a.message = body || "一项都没跑出来";
    a.addAction(i === parts.length - 1 ? "好" : "下一页");
    await a.present();
  }
}

try {
  await main();
} catch (e) {
  const a = new Alert();
  a.title = "探测中断";
  a.message = String((e && e.message) || e);
  a.addAction("好");
  await a.present();
}
