import EventKit
import EventKitUI
import Foundation
import SafariServices
import UIKit
import WebKit

/// JS 与原生之间唯一的通道（iOS 版）。对位安卓的 `NativeBridge.java`。
///
/// 分工完全一样：这边**只负责发 HTTP 和管 cookie**，登录与解析全在 `native.js`。
/// 好处是两个平台跑同一份解析代码，量一次就对两边都算数。
///
/// ## 两条通道，一条同步一条异步
///
/// - **异步**（只有 `req` / `req2`）：JS 调
///   `window.webkit.messageHandlers.hyt.postMessage({k:'req',…})`，
///   这边发完请求再 `evaluateJavaScript("window.__nativeDone(id, json)")` 回去。
/// - **同步**（其余 36 个）：JS 调 `window.prompt("hyt:方法名\u0001参数…")`。
///
/// ### 为什么同步必须走 prompt
///
/// `postMessage` 是单向的；`addScriptMessageHandlerWithReply` 回的是 Promise。
/// 两者都**没法让 JS 当场拿到值** —— 而 `loadCred()` / `credState()` / `cookie()`
/// 这类调用在 `native.js` 里是直接当值用的（`if (!loadCred()) throw …`），
/// 改成异步要动几百处调用点，风险远大于收益。
///
/// `window.prompt()` 是 WKWebView 里**唯一**能让 JS 同步拿到返回值的口子：
/// 它会被 `WKUIDelegate` 的 `runJavaScriptTextInputPanelWithPrompt` 接住，
/// 我们直接 `completionHandler(结果)` —— 既不弹框也不阻塞。
///
/// 敢这么用还有一个前提：`app.js` 里**本来就不调** `prompt()` / `confirm()`
/// （安卓 WebView 没设 `WebChromeClient`，调了会返回 null 卡死，所以早就避开了）。
/// 这个名字是空着的，拿来当通道不会撞车。
final class Bridge: NSObject, WKScriptMessageHandler {

    /// 弹 Safari、弹日历都要用到它。
    weak var host: UIViewController?
    private weak var webView: WKWebView?

    /// 目录大小算起来慢（要递归），缓存一份。60 秒内的重复调用直接给旧值。
    private var storageCache: String = ""
    private var storageAt: Date = .distantPast

    func attach(webView: WKWebView, host: UIViewController) {
        self.webView = webView
        self.host = host
        // 网页视图建好后先算一次占用，等用户真去点「清除缓存」时就不用等了
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 3) { [weak self] in
            self?.warmStorage()
        }
    }

    // MARK: - 异步通道

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.name == "hyt" else { return }
        guard let d = message.body as? [String: Any] else { return }
        guard (d["k"] as? String) == "req" else { return }

        let id = (d["id"] as? NSNumber)?.intValue ?? 0
        let method = (d["method"] as? String) ?? "GET"
        let path = (d["path"] as? String) ?? ""
        let body = (d["body"] as? String) ?? ""
        let referer = (d["referer"] as? String) ?? ""
        let headers = (d["headers"] as? String) ?? ""

        // 请求在后台线程发（URLSession 本来就是并发的），回包再切回主线程
        // 去执行 JS —— evaluateJavaScript 必须在主线程。
        Net.send(method: method, path: path, body: body,
                 referer: referer, headersJSON: headers) { [weak self] r in
            DispatchQueue.main.async {
                self?.deliver(id: id, json: r.json)
            }
        }
    }

    private func deliver(id: Int, json: String) {
        guard let wv = webView else { return }
        let js = "window.__nativeDone(\(id), \(Bridge.jsLiteral(json)));"
        wv.evaluateJavaScript(js, completionHandler: nil)
    }

    /// 把任意字符串变成安全的 JS 字面量。
    ///
    /// 🔴 必须自己转义，不能拼 `"'\(s)'"`：回包里是整页 HTML，里面一定有
    ///    引号、反斜杠、换行。少转义一样就是把一个语法错误喂给页面 ——
    ///    而 `evaluateJavaScript` 的失败是**静默**的（completionHandler 里
    ///    没人看），表现成「请求发出去了，界面一直在转圈」。
    ///
    /// `\u2028` / `\u2029` 也要转：它们在 JSON 里合法，在 JS 源码里是换行。
    static func jsLiteral(_ s: String) -> String {
        var out = "\""
        for u in s.unicodeScalars {
            switch u {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case "\u{2028}": out += "\\u2028"
            case "\u{2029}": out += "\\u2029"
            default:
                if u.value < 0x20 {
                    out += String(format: "\\u%04x", u.value)
                } else {
                    out.unicodeScalars.append(u)
                }
            }
        }
        return out + "\""
    }

    // MARK: - 同步通道

    /// 接住 `window.prompt("hyt:…")`，当场把值送回去。
    ///
    /// 只认 `hyt:` 前缀 —— 别的 prompt 一律回 nil，免得以后页面真要用它时行为变了。
    func handleSync(_ prompt: String) -> String {
        guard prompt.hasPrefix("hyt:") else { return "" }
        var parts = String(prompt.dropFirst(4)).components(separatedBy: "\u{01}")
        guard !parts.isEmpty else { return "" }
        let name = parts.removeFirst()
        return dispatch(name, parts)
    }

    private func dispatch(_ name: String, _ a: [String]) -> String {
        func arg(_ i: Int) -> String { i < a.count ? a[i] : "" }

        switch name {

        // ---------------------------------------------------------- 教务账号
        case "loadCred": return CredStore.load(.jw)
        case "saveCred": return CredStore.save(.jw, user: arg(0), pass: arg(1))
        case "credState": return CredStore.state(.jw)
        case "clearCred": CredStore.clear(.jw); return ""

        // ---------------------------------------------------------- 门户账号
        case "loadPortalCred": return CredStore.load(.portal)
        case "savePortalCred": return CredStore.save(.portal, user: arg(0), pass: arg(1))
        case "portalCredState": return CredStore.state(.portal)
        case "clearPortalCred": CredStore.clear(.portal); return ""

        // ---------------------------------------------------------- 本机存储
        case "storageInfo": return storageInfo()
        case "clearWebCache": LocalStore.clearWebCache(); return ""
        case "saveWater": return CredStore.saveText(.water, arg(0))
        case "loadWater": return CredStore.loadText(.water)
        case "waterState": return CredStore.textState(.water)
        case "clearWater": CredStore.clear(.water); return ""
        // 财务处（缴费）。和安卓 saveCw/loadCw/cwState/clearCw 同名同义。
        case "saveCw": return CredStore.saveText(.cw, arg(0))
        case "loadCw": return CredStore.loadText(.cw)
        case "cwState": return CredStore.textState(.cw)
        case "clearCw": CredStore.clear(.cw); return ""

        // ---------------------------------------------------------- 学习通
        case "cxAes": return CxCrypto.aesB64(arg(0))
        case "cxDumpCookies": return CxCrypto.dumpCookies()
        case "cxLoadCookies": return "\(CxCrypto.loadCookies(arg(0)))"
        case "cxHasLogin": return CxCrypto.hasLogin() ? "1" : "0"
        case "cxSave": return CredStore.saveText(.cxCookie, arg(0))
        case "cxLoad": return CredStore.loadText(.cxCookie)
        case "cxClear": CredStore.clear(.cxCookie); return ""
        case "cxState": return CredStore.textState(.cxCookie)
        case "saveCxPwd": return CredStore.save(.cxPwd, user: arg(0), pass: arg(1))
        case "loadCxPwd": return CredStore.load(.cxPwd)
        case "cxPwdState": return CredStore.state(.cxPwd)
        case "clearCxPwd": CredStore.clear(.cxPwd); return ""
        case "cxClearCookies": return "\(CxCrypto.clearCookies())"

        // ---------------------------------------------------------- Cookie
        case "clearCookies": return CookieJar.clearAll() ? "1" : "0"
        case "cookie": return CookieJar.cookie(url: arg(0), name: arg(1))
        case "webCookie":
            WebCookieCache.shared.refresh()
            return WebCookieCache.shared.value(url: arg(0), name: arg(1))
        case "webCookieLike":
            WebCookieCache.shared.refresh()
            return WebCookieCache.shared.values(url: arg(0), prefix: arg(1))
        case "webCookieAll":
            WebCookieCache.shared.refresh()
            return WebCookieCache.shared.header(url: arg(0))
        case "clearCookiesFor": return CookieJar.clear(host: arg(0)) ? "1" : "0"

        // ---------------------------------------------------------- 打开外部
        case "openWeb": return openWeb(url: arg(0), title: arg(1))
        case "openWebEx": return openWeb(url: arg(0), title: arg(1))
        case "openUrl": openURL(arg(0)); return ""
        case "openApp": return "notinstalled"
        case "hasApp": return "0"
        case "addCalendar":
            return addCalendar(title: arg(0), desc: arg(1), loc: arg(2),
                               beginMs: Double(arg(3)) ?? 0,
                               endMs: Double(arg(4)) ?? 0,
                               allDay: arg(5) == "1")
        case "shareFile":
            return shareFile(name: arg(0), text: arg(1))

        default:
            // 认不出的名字**不能崩**，也不能返回一句含糊的话。空串会让调用方
            // 当成「没拿到值」退回自己的兜底路径，这是最安全的失败方式。
            return ""
        }
    }

    // MARK: - 存储占用

    private func storageInfo() -> String {
        if !storageCache.isEmpty, Date().timeIntervalSince(storageAt) < 60 {
            return storageCache
        }
        return computeStorage()
    }

    private func warmStorage() {
        _ = computeStorage()
    }

    private func computeStorage() -> String {
        let s = LocalStore.info()
        if s != "{}" {
            storageCache = s
            storageAt = Date()
        }
        return s
    }

    // MARK: - 外部网页

    /// 开一个应用内的浏览器，返回空串 = 成功（和安卓一致）。
    ///
    /// 用 `SFSafariViewController` 而不是自己再搭一个 WKWebView：
    /// 它有地址栏、有返回、能存密码 —— 用户在学校页面里登录时不会迷路。
    private func openWeb(url: String, title: String) -> String {
        let u = url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard u.hasPrefix("http://") || u.hasPrefix("https://"),
              let real = URL(string: u) else {
            return "fail: 不是 http/https"
        }
        guard let host = host else { return "fail: 界面还没准备好" }

        let cfg = SFSafariViewController.Configuration()
        cfg.entersReaderIfAvailable = false
        let vc = SFSafariViewController(url: real, configuration: cfg)
        vc.preferredBarTintColor = .white
        vc.preferredControlTintColor = UIColor(red: 0.13, green: 0.45, blue: 0.86, alpha: 1)

        // prompt 的回调还在等我们返回，不能在这里同步 present ——
        // 先回空串，下一轮 runloop 再弹。
        DispatchQueue.main.async {
            host.present(vc, animated: true)
        }
        return ""
    }

    private func openURL(_ url: String) {
        let u = url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard u.hasPrefix("http://") || u.hasPrefix("https://"),
              let real = URL(string: u) else { return }
        DispatchQueue.main.async {
            UIApplication.shared.open(real, options: [:], completionHandler: nil)
        }
    }

    // MARK: - 日历

    /// 把一场考试放进系统日历：拉起日历 App 的「新建事件」界面，内容都填好。
    ///
    /// 🔴 不直接往日历数据库里写：那要多一层写入权限，而且写进去就绕过了用户确认 ——
    ///    万一我们算错了时间，用户根本不知道这条日程是哪来的。
    ///    走 `EKEventEditViewController` 是**把内容递给日历**，用户看一眼、
    ///    点保存才真的写进去。少一个权限，也多一道人工确认。
    ///
    /// 返回空串 = 成功拉起，`fail: …` = 打不开。
    private func addCalendar(title: String, desc: String, loc: String,
                             beginMs: Double, endMs: Double, allDay: Bool) -> String {
        guard beginMs > 0 else { return "fail: 没有开始时间" }
        guard let host = host else { return "fail: 界面还没准备好" }

        let begin = Date(timeIntervalSince1970: beginMs / 1000)
        var end = Date(timeIntervalSince1970: max(endMs, beginMs) / 1000)
        if end <= begin { end = begin.addingTimeInterval(2 * 60 * 60) }

        let store = EKEventStore()
        let ev = EKEvent(eventStore: store)
        ev.title = title
        ev.startDate = begin
        ev.endDate = end
        ev.isAllDay = allDay
        if !desc.isEmpty { ev.notes = desc }
        if !loc.isEmpty { ev.location = loc }

        let vc = EKEventEditViewController()
        vc.eventStore = store
        vc.event = ev
        vc.editViewDelegate = self

        DispatchQueue.main.async {
            host.present(vc, animated: true)
        }
        return ""
    }

    /// 把一段文本存成 `.ics`，交给系统「共享」面板（对位安卓的 `NativeBridge.shareFile`）。
    ///
    /// 为什么不是一条条 `addCalendar`：那是给**单场考试**用的，一次弹一个确认框
    /// 才合理。整学期课表是几百个事件，一条条弹等于让用户把课表手抄一遍。
    ///
    /// 临时文件写在 `temporaryDirectory`：系统会自己清，用户手机里不留垃圾。
    /// 返回空串 = 已拉起，`fail: …` = 失败（界面上如实说）。
    private func shareFile(name: String, text: String) -> String {
        guard !text.isEmpty else { return "fail: 没有内容" }
        guard text.utf8.count <= 4 * 1024 * 1024 else { return "fail: 内容太大" }
        guard let host = host else { return "fail: 界面还没准备好" }

        var n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if !n.hasSuffix(".ics") { n += ".ics" }
        // 和安卓 `ShareProvider` 的白名单保持一致：只认字母数字、点、横线、下划线、汉字。
        guard n.range(of: "^[\\w.\\-\\u{4e00}-\\u{9fa5}]{1,80}$",
                      options: .regularExpression) != nil else {
            return "fail: 文件名不合适"
        }

        let url = FileManager.default.temporaryDirectory.appendingPathComponent(n)
        do {
            try text.write(to: url, atomically: true, encoding: .utf8)
        } catch {
            return "fail: \(error.localizedDescription)"
        }

        // prompt 的回调还在等我们返回，不能在这里同步 present —— 先回空串，下一轮再弹。
        DispatchQueue.main.async {
            let vc = UIActivityViewController(activityItems: [url], applicationActivities: nil)
            // iPad 上不给 sourceView 会崩（popover 没有锚点）。
            if let pop = vc.popoverPresentationController {
                pop.sourceView = host.view
                pop.sourceRect = CGRect(x: host.view.bounds.midX,
                                        y: host.view.bounds.midY, width: 0, height: 0)
                pop.permittedArrowDirections = []
            }
            host.present(vc, animated: true)
        }
        return ""
    }
}

// MARK: - 日历界面的关闭

extension Bridge: EKEventEditViewDelegate {
    func eventEditViewController(_ controller: EKEventEditViewController,
                                 didCompleteWith action: EKEventEditViewAction) {
        controller.dismiss(animated: true, completion: nil)
    }
}
