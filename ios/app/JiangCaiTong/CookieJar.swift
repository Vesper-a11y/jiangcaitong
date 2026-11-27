import Foundation
import WebKit

/// 两个 cookie 罐子。
///
/// 安卓那边有两套罐子，iOS 这边**正好一一对应**：
///
/// | 安卓 | iOS | 谁在用 |
/// |---|---|---|
/// | `java.net.CookieHandler`（全局） | `HTTPCookieStorage.shared` | `Android.req` 发出的所有请求 |
/// | `WebView` 的 `CookieManager` | `WKHTTPCookieStore` | 网页视图里那些页面 |
///
/// 为什么要分开：门户的 CASTGC 可能**只落在其中一个罐子里**。
/// 只读一个，表现就是「明明登录了却说没登录」—— 这种空串和「确实没有」
/// 长得一模一样，最难查。
enum CookieJar {

    // MARK: - 原生罐（和 `Android.cookie()` 对应）

    /// 读某个地址下某个 cookie 的值。读不到返回空串。
    ///
    /// 门户的 `Authorization` 是服务器用 `Set-Cookie` 下发的，JS 读不到，
    /// 只能从这儿取。取到之后要当**请求头**发给网关（只带 cookie 会 401）。
    static func cookie(url: String, name: String) -> String {
        guard let u = URL(string: url), !name.isEmpty else { return "" }
        guard let list = HTTPCookieStorage.shared.cookies(for: u) else { return "" }
        for c in list where c.name == name {
            return c.value
        }
        return ""
    }

    /// 清空原生罐。`clearCookies()` 用。
    static func clearAll() -> Bool {
        guard let all = HTTPCookieStorage.shared.cookies else { return false }
        for c in all { HTTPCookieStorage.shared.deleteCookie(c) }
        return true
    }

    /// 按域名清原生罐。`clearCookiesFor(host)` 用，返回「清到过东西没有」。
    ///
    /// 🔴 只动原生罐。安卓那边特意把这条和 `cxClearCookies` 分开写，
    ///    就是为了「断开学习通」不会顺手把门户的票也清掉。
    static func clear(host: String) -> Bool {
        guard !host.isEmpty else { return false }
        guard let all = HTTPCookieStorage.shared.cookies else { return false }
        var any = false
        for c in all where matches(cookieDomain: c.domain, host: host) {
            HTTPCookieStorage.shared.deleteCookie(c)
            any = true
        }
        return any
    }

    /// 清掉某个域名后缀下的全部 cookie，返回清掉的条数。
    static func clearCount(domainSuffix: String) -> Int {
        guard let all = HTTPCookieStorage.shared.cookies else { return 0 }
        var n = 0
        for c in all where c.domain.hasSuffix(domainSuffix) {
            HTTPCookieStorage.shared.deleteCookie(c)
            n += 1
        }
        return n
    }

    static var all: [HTTPCookie] { HTTPCookieStorage.shared.cookies ?? [] }

    static func add(_ c: HTTPCookie) { HTTPCookieStorage.shared.setCookie(c) }

    /// 域名匹配：`.chaoxing.com`、`chaoxing.com`、`passport2.chaoxing.com` 都算命中 `chaoxing.com`。
    private static func matches(cookieDomain: String, host: String) -> Bool {
        let d = cookieDomain.hasPrefix(".") ? String(cookieDomain.dropFirst()) : cookieDomain
        let h = host.hasPrefix(".") ? String(host.dropFirst()) : host
        return h == d || h.hasSuffix("." + d)
    }
}

/// 网页视图那个罐子的**同步读缓存**。
///
/// ## 为什么需要它
///
/// `WKHTTPCookieStore` 的所有读接口都是异步回调，而页面那头是
/// `window.prompt` 同步要值 —— 中间没法等。
///
/// 所以这里维护一份快照：原生侧在合适的时机刷新，页面侧同步读快照。
/// 代价是最多晚几秒看到新 cookie。对「读门户票」这个用途来说可以接受，
/// 因为票是登录时写进去的，那时候正好会有一次刷新。
final class WebCookieCache {

    static let shared = WebCookieCache()

    private var cookies: [HTTPCookie] = []
    private let lock = NSLock()
    private weak var store: WKHTTPCookieStore?
    private var refreshing = false

    private init() {}

    /// 网页视图建好之后绑上来。
    func bind(_ s: WKHTTPCookieStore) {
        lock.lock(); store = s; lock.unlock()
        refresh()
    }

    /// 拉一次最新快照。可以随便调，正在拉的时候重复调用会被忽略。
    func refresh() {
        lock.lock()
        let s = store
        if refreshing || s == nil { lock.unlock(); return }
        refreshing = true
        lock.unlock()

        s?.getAllCookies { [weak self] list in
            guard let self = self else { return }
            self.lock.lock()
            self.cookies = list
            self.refreshing = false
            self.lock.unlock()
        }
    }

    /// 某个地址下的全部 cookie，拼成 `a=1; b=2`。读不到返回空串。
    func header(url: String) -> String {
        guard let u = URL(string: url) else { return "" }
        lock.lock()
        let list = cookies
        lock.unlock()

        var parts: [String] = []
        for c in list where CookieJar.match(c.domain, u.host ?? "") && pathOK(c.path, u.path) {
            parts.append("\(c.name)=\(c.value)")
        }
        return parts.joined(separator: "; ")
    }

    /// 按名字精确取。
    func value(url: String, name: String) -> String {
        guard let u = URL(string: url), !name.isEmpty else { return "" }
        lock.lock()
        let list = cookies
        lock.unlock()

        for c in list where c.name == name
            && CookieJar.match(c.domain, u.host ?? "") && pathOK(c.path, u.path) {
            return c.value
        }
        return ""
    }

    /// 名字以 `prefix` 开头的全部值，一行一个。
    ///
    /// 用换行分隔而不是逗号：cookie 值里可能有逗号（base64 常见）。
    func values(url: String, prefix: String) -> String {
        guard let u = URL(string: url) else { return "" }
        lock.lock()
        let list = cookies
        lock.unlock()

        var out: [String] = []
        for c in list where c.name.hasPrefix(prefix)
            && CookieJar.match(c.domain, u.host ?? "") && pathOK(c.path, u.path) {
            out.append(c.value)
        }
        return out.joined(separator: "\n")
    }

    private func pathOK(_ cookiePath: String, _ urlPath: String) -> Bool {
        if cookiePath.isEmpty || cookiePath == "/" { return true }
        return urlPath.hasPrefix(cookiePath)
    }
}

extension CookieJar {
    /// 给 `WebCookieCache` 用的域名匹配（同一套规则，别写两遍）。
    static func match(_ cookieDomain: String, _ host: String) -> Bool {
        let d = cookieDomain.hasPrefix(".") ? String(cookieDomain.dropFirst()) : cookieDomain
        let h = host.hasPrefix(".") ? String(host.dropFirst()) : host
        if d.isEmpty { return false }
        return h == d || h.hasSuffix("." + d)
    }
}
