import Foundation

/// 注入到网页里的一层垫片，把 `Android.*` 换成 iOS 的实现。
///
/// ## 为什么同步方法要走 `prompt`
///
/// 桥上有两类方法，性质完全不同：
///   - `req` / `req2` 是**异步**的：发请求，稍后回 `window.__nativeDone(id, json)`
///   - 其余 32 个是**同步取值**的：`loadCred()` 当场要拿到字符串，`credState()`
///     当场要知道是 `"ok"` 还是 `"none"`
///
/// WKWebView 的 `postMessage` 只能异步，`addScriptMessageHandlerWithReply` 返回的
/// 也是 Promise —— 两者都满足不了同步。**唯一的同步通道是 `prompt()`**：
/// 它会被 `WKUIDelegate` 的 `runJavaScriptTextInputPanelWithPrompt` 接住，
/// 我们直接 `completionHandler(结果)`，既不弹框也不阻塞，值当场回到 JS。
///
/// 所以这里分两条路：
///   - 异步的走 `postMessage`
///   - 同步的走 `prompt("hyt:方法名\u0001参数1\u0001参数2")`
///
/// 用 `\u0001` 而不是 JSON 当分隔符：账号密码里可能有引号、反斜杠、中文，
/// 拼 JSON 要处理一层转义，错了就是「密码对但登录失败」这种最难查的问题。
/// `\u0001` 是控制字符，正常输入里绝不会出现。
///
/// ## 为什么敢覆盖 `window.prompt`
///
/// `app.js` 里**本来就不调** `prompt()` / `confirm()`（第 1801 行有注释说明：
/// 安卓 WebView 没设 `WebChromeClient`，调了会返回 null 卡死）。所以这个名字
/// 是空着的，拿来当同步通道不会撞车。
enum Shim {

    static let source: String = #"""
    (function () {
      var M = window.webkit && window.webkit.messageHandlers
              && window.webkit.messageHandlers.hyt;
      var SEP = '\u0001';

      function send(o) { if (M) { M.postMessage(o); } }

      // 同步调用：拼成 hyt:方法名\u0001参数… 交给原生，返回值当场回来。
      function sync(name) {
        var s = 'hyt:' + name;
        for (var i = 1; i < arguments.length; i++) {
          var v = arguments[i];
          s += SEP + (v === null || v === undefined ? '' : String(v));
        }
        var r = window.prompt(s);
        return (r === null || r === undefined) ? '' : String(r);
      }
      function syncBool(name) { return sync.apply(null, arguments) === '1'; }
      function syncInt(name) { return parseInt(sync.apply(null, arguments), 10) || 0; }

      // 异步：发出去就不管了，结果由原生调 window.__nativeDone 回来。
      function fire(id, method, path, body, referer, headers) {
        send({
          k: 'req',
          id: id || 0,
          method: method || 'GET',
          path: path || '',
          body: (body === null || body === undefined) ? '' : String(body),
          referer: (referer === null || referer === undefined) ? '' : String(referer),
          headers: (headers === null || headers === undefined) ? '' : String(headers)
        });
      }

      window.Android = {
        // ---------------------------------------------------------- 网络
        req: function (id, method, path, body, referer) {
          fire(id, method, path, body, referer, '');
        },
        req2: function (id, method, path, body, referer, headers) {
          fire(id, method, path, body, referer, headers);
        },

        // 原生定时器（对应 Android 的 NativeBridge.wake）。
        // 🔴 iOS 没有后台跑批，这个方法只为了「网页那一份 native.js 能同一套写」：
        //    后台 WebView 在 Android 上是隐藏页面，setTimeout 会被节流，所以那边
        //    等原生回调；iOS 永远在前台，用一个普通定时器就是等价的行为。
        //    不加的话，页面调 Android.wake 会走进 catch，后台扫描那一轮白白重试。
        wake: function (id, ms) {
          window.setTimeout(function () {
            if (window.__bgWake) window.__bgWake(id);
          }, Math.max(0, Number(ms) || 0));
        },

        // ---------------------------------------------------------- 凭据
        loadCred: function () { return sync('loadCred'); },
        saveCred: function (u, p) { return sync('saveCred', u, p); },
        credState: function () { return sync('credState'); },
        clearCred: function () { return sync('clearCred'); },

        loadPortalCred: function () { return sync('loadPortalCred'); },
        savePortalCred: function (u, p) { return sync('savePortalCred', u, p); },
        portalCredState: function () { return sync('portalCredState'); },
        clearPortalCred: function () { return sync('clearPortalCred'); },

        // ---------------------------------------------------------- 本机存储
        storageInfo: function () { return sync('storageInfo'); },
        clearWebCache: function () { return sync('clearWebCache'); },
        saveWater: function (j) { return sync('saveWater', j); },
        loadWater: function () { return sync('loadWater'); },
        waterState: function () { return sync('waterState'); },
        clearWater: function () { return sync('clearWater'); },
        // 财务处（缴费）
        saveCw: function (j) { return sync('saveCw', j); },
        loadCw: function () { return sync('loadCw'); },
        cwState: function () { return sync('cwState'); },
        clearCw: function () { return sync('clearCw'); },

        // ---------------------------------------------------------- 学习通
        cxAes: function (pwd) { return sync('cxAes', pwd); },
        cxDumpCookies: function () { return sync('cxDumpCookies'); },
        cxLoadCookies: function (j) { return syncInt('cxLoadCookies', j); },
        cxHasLogin: function () { return syncBool('cxHasLogin'); },
        cxSave: function (j) { return sync('cxSave', j); },
        cxLoad: function () { return sync('cxLoad'); },
        cxClear: function () { return sync('cxClear'); },
        cxState: function () { return sync('cxState'); },
        saveCxPwd: function (u, p) { return sync('saveCxPwd', u, p); },
        loadCxPwd: function () { return sync('loadCxPwd'); },
        cxPwdState: function () { return sync('cxPwdState'); },
        clearCxPwd: function () { return sync('clearCxPwd'); },
        cxClearCookies: function () { return syncInt('cxClearCookies'); },

        // ---------------------------------------------------------- Cookie
        clearCookies: function () { return syncBool('clearCookies'); },
        cookie: function (url, name) { return sync('cookie', url, name); },
        webCookie: function (url, name) { return sync('webCookie', url, name); },
        webCookieLike: function (url, prefix) { return sync('webCookieLike', url, prefix); },
        webCookieAll: function (url) { return sync('webCookieAll', url); },
        clearCookiesFor: function (host) { return syncBool('clearCookiesFor', host); },

        // ---------------------------------------------------------- 打开外部
        openWeb: function (url, title) { return sync('openWeb', url, title); },
        openWebEx: function (url, title, warn) {
          return sync('openWebEx', url, title, warn ? '1' : '0');
        },
        openUrl: function (url) { return sync('openUrl', url); },
        // 🔴 之前这里写错成 sync('openUrl', pkg) —— 参数是包名，不是地址，
        //    转发给 openUrl 会被当成非法地址静默丢弃，界面上就是「点了没反应」。
        openApp: function (pkg) { return sync('openApp', pkg); },
        // iOS 上没有那些安卓 App，按函数是否存在分派：返回 "0" 让界面
        // 走「没装」那条路（给下载页），而不是"这个壳没这个接口"那条。
        hasApp: function (name) { return syncBool('hasApp', name); },

        // ---------------------------------------------------------- 钉钉通知
        // iOS 上**没有**这条路：读别的 App 的通知是 Android 那个
        // NotificationListenerService 才有的能力，iOS 的沙箱不给。
        // 四个方法仍然要给，否则 app.js 里的 dingPaint 会拿到 undefined
        // （`NativeApi.ding.state()` 直接抛），整块不该显示的东西反而会显示。
        // state 返回空串 → JSON.parse 抛 → dingApi 拿到 null → 整块 hidden，
        // 这正是我们要的：iOS 上这一块不存在，一个像素都不占。
        // 🔴 别"顺手"返回 '{granted:true}' —— 那样 iOS 上会显示一块永远空的列表。
        dingState: function () { return ''; },
        dingList: function () { return '[]'; },
        dingClear: function () {},
        dingGrant: function () { return false; },

        // ------------------------------------------------------ 完美校园账号
        // iOS 上**没有**这条路：登录要连完美校园的接口、还要把 token 落盘，
        // iOS 侧没做（Android 见 WxCard）。同样必须给这三个方法，
        // 否则 wxPaint 拿到 undefined 就把 renderAccounts 打断。
        // state 返回空串 → JSON.parse 抛 → wxApi 拿到 null → 整块 hidden。
        wxState: function () { return ''; },
        wxLogin: function () {},
        wxSendSms: function () {},
        wxLoginSms: function () {},
        wxCard: function () {},
        wxLogout: function () {},

        // ---------------------------------------------------------- 权限自查
        // iOS 上没做这块（Android 见 NativeBridge.permState / permOpen）。
        // 🔴 permState 必须返回空串，不能返回 {} 或 {notify:true}：
        //    app.js 的 permState() 解析不出对象就返回 null，权限那一行整块
        //    不出现；返回空对象会让它渲染成「四项全没开」—— 那是假话。
        // 🔴 permOpen 返回一句能看懂的话（不是 'none'）：用户点了总得有个回话。
        permState: function () { return ''; },
        permOpen: function () { return '这个版本打不开系统设置'; },

        // ---------------------------------------------------------- 日历
        addCalendar: function (title, desc, loc, beginMs, endMs, allDay) {
          return sync('addCalendar', title, desc, loc, beginMs, endMs, allDay ? '1' : '0');
        },
        // 整学期课表导出（0.52.0 v13）：写临时文件 + 系统共享面板。
        // 🔴 不能不给 —— app.js 的 shareCalendar 会先问这个方法在不在，
        //    缺了它 iOS 上就只剩「下载 .ics」那条路，而 WKWebView 里点
        //    带 download 属性的 <a> 同样是**没反应**的。
        shareFile: function (name, text, mime) { return sync('shareFile', name, text); }
      };
    })();
    """#
}
