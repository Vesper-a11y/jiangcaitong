import Foundation

/// 发 HTTP 的那一层。对位安卓的 `NativeBridge.doReq()`。
///
/// 只做三件事：拼地址、带请求头、把响应原样交回去。
/// **解析一行都不在这里** —— 那部分和安卓共用同一份 `native.js`。
enum Net {

    /// 教务系统根地址。和安卓**完全一致**。
    static let BASE = "http://jwglxt.upstream.edu.cn"

    /// 🔴 这里故意说自己是安卓 Chrome，不是 iPhone Safari。
    ///
    /// 教务系统按 UA 分派页面模板。安卓那边用这个串跑了一年多，
    /// `native.js` 里所有表格列序、表单字段名都是照这个模板量的。
    /// 换成 iPhone 的 UA，学校要不要返回移动版、要不要换一套结构，
    /// 我们说了不算 —— 而它对不上时的样子是「登录成功了，但一页数据都没有」，
    /// 是最难查的那类故障。
    ///
    /// 跨平台复用同一份解析代码，UA 就必须一致。这不是糊弄，是拿
    /// 「解析能对上」换「身份好看」。
    static let UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
        + "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

    /// 和安卓的 `CookieHandler.setDefault(new CookieManager())` 对应：
    /// 全局只留一个罐子，`URLSession` 和 `cookie()` 读的是同一份。
    static let session: URLSession = {
        let c = URLSessionConfiguration.default
        c.httpCookieStorage = HTTPCookieStorage.shared
        c.httpShouldSetCookies = true
        // 教务系统换了内容却不改 Last-Modified 是常有的事，走缓存会拿到过期的课表
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        c.timeoutIntervalForRequest = 30      // 对位安卓的 readTimeout
        c.timeoutIntervalForResource = 90
        return URLSession(configuration: c)
    }()

    struct Result {
        var status: Int
        var body: String
        var url: String

        /// 回给 JS 的 JSON。字段名和安卓一模一样（`status` / `body` / `url`），
        /// 这样 `window.__nativeDone` 的解析两边通用。
        var json: String {
            let o: [String: Any] = ["status": status, "body": body, "url": url]
            guard let d = try? JSONSerialization.data(withJSONObject: o, options: []),
                  let s = String(data: d, encoding: .utf8) else {
                return "{\"status\":0,\"body\":\"\",\"url\":\"\"}"
            }
            return s
        }
    }

    /// 发一个请求。
    ///
    /// - path: 绝对地址（`http://` / `https://` 开头）就原样发，否则拼在 BASE 后面。
    ///   校园公告要访问 6 个不同域名，写死 BASE 根本发不出去。
    static func send(method: String, path: String, body: String,
                     referer: String, headersJSON: String,
                     done: @escaping (Result) -> Void) {

        let absolute = path.hasPrefix("http://") || path.hasPrefix("https://")
        let full = absolute ? path : BASE + path

        guard let url = URL(string: full) else {
            done(Result(status: 0, body: "fail: 地址不对", url: full))
            return
        }

        var req = URLRequest(url: url)
        req.httpMethod = method.isEmpty ? "GET" : method
        req.setValue(UA, forHTTPHeaderField: "User-Agent")
        req.setValue("zh-CN,zh;q=0.9", forHTTPHeaderField: "Accept-Language")
        req.setValue("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                     forHTTPHeaderField: "Accept")

        if !referer.isEmpty {
            // absolute 的 referer 也按原样用 —— 否则会拼成
            // `http://jwglxt.upstream.edu.cnhttp://www.upstream.edu.cn` 这种鬼东西
            let ra = referer.hasPrefix("http://") || referer.hasPrefix("https://")
            req.setValue(ra ? referer : BASE + referer, forHTTPHeaderField: "Referer")
        }

        var hasContentType = false
        if let d = headersJSON.data(using: .utf8),
           let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] {
            for (k, v) in o {
                let s = (v as? String) ?? "\(v)"
                if k.lowercased() == "content-type" { hasContentType = true }
                req.setValue(s, forHTTPHeaderField: k)
            }
        }

        if !body.isEmpty {
            req.httpBody = body.data(using: .utf8)
            // 门户的一卡通接口要 `application/json`，那条走 headersJSON 覆盖
            if !hasContentType {
                req.setValue("application/x-www-form-urlencoded",
                             forHTTPHeaderField: "Content-Type")
            }
        }

        // URLSession 自己跟 301/302/303/307/308，比安卓那边还全
        // （安卓的 HttpURLConnection 不跟 307/308，得手写一层兜底）。
        // 最终地址从 `response.url` 取 —— native.js 判断登录成功就靠它。
        session.dataTask(with: req) { data, resp, err in
            if let e = err {
                let ns = e as NSError
                // -999 是我们自己取消的，不该当成「学校抽风」
                if ns.code == NSURLErrorCancelled { return }
                done(Result(status: 0,
                            body: "fail: " + e.localizedDescription,
                            url: full))
                return
            }
            let http = resp as? HTTPURLResponse
            let code = http?.statusCode ?? 0
            done(Result(status: code,
                        body: decode(data),
                        url: http?.url?.absoluteString ?? full))
        }.resume()
    }

    /// 把响应字节解成文字。
    ///
    /// 先按 UTF-8 解，解不出来再按 GB18030 —— 学校有几个老站点还是 GBK，
    /// 硬按 UTF-8 解会得到一屏乱码，而乱码在 `native.js` 那边表现为
    /// 「页面结构不认识」，最后报出来的是一句含糊的「没拿到数据」。
    static func decode(_ data: Data?) -> String {
        guard let d = data, !d.isEmpty else { return "" }
        if let s = String(data: d, encoding: .utf8) { return s }

        let gb = CFStringConvertEncodingToNSStringEncoding(
            CFStringEncoding(CFStringEncodings.GB_18030_2000.rawValue))
        if let s = String(data: d, encoding: String.Encoding(rawValue: gb)) { return s }

        // 都解不出来：至少别丢内容，替换坏字节
        return String(data: d, encoding: .utf8) ?? ""
    }
}
