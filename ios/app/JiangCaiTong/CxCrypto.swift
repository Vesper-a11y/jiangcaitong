import CommonCrypto
import Foundation

/// 学习通那几件只有原生才做得来的事：AES 加密、cookie 罐导出与还原。
///
/// 对位安卓的 `CxNet.java`。登录流程和课程/作业解析全在 `native.js` 里
/// （那边有 DOMParser，比手写 HTML 解析稳得多），原生只做这三样。
enum CxCrypto {

    /// 学习通前端的 `encryptByAES` 用的密钥。
    ///
    /// 🔴 **key 同时当 iv**，而且 iv 是固定的 —— 这不是偷懒，是照抄前端：
    ///    服务端就按这个解。改成随机 iv 会直接登录失败（密码对也不认）。
    ///
    /// 🔴 这是「AES-128-CBC + PKCS7 + Base64」的标准组合，
    ///    和门户那边「裸 RSA 不填充」是两套完全不同的规矩，别互相套。
    private static let key = "u2oh6Vu^HWe4_AES"

    /// 学习通登录用的密文。失败返回空串。
    ///
    /// 🔴 返回空串必须由调用方当成**登录失败**处理，不许拿明文顶上 ——
    ///    学习通只认密文，发明文等于拿错的密码去撞，撞多了会触发风控。
    static func aesB64(_ pwd: String) -> String {
        let k = Array(key.utf8)
        guard k.count == kCCKeySizeAES128 else { return "" }
        let input = Array(pwd.utf8)
        guard !input.isEmpty else { return "" }

        let cap = input.count + kCCBlockSizeAES128
        var buf = [UInt8](repeating: 0, count: cap)
        var moved = 0

        let st = CCCrypt(CCOperation(kCCEncrypt),
                         CCAlgorithm(kCCAlgorithmAES),
                         CCOptions(kCCOptionPKCS7Padding),
                         k, k.count,
                         k,                      // iv = key
                         input, input.count,
                         &buf, cap,
                         &moved)
        guard st == kCCSuccess, moved > 0 else { return "" }
        return Data(buf[0..<moved]).base64EncodedString()
    }

    // MARK: - cookie 罐

    /// 把原生罐里**所有** cookie 导成 JSON。
    ///
    /// 每条都带 `domain`：`.chaoxing.com` 这种跨子域的必须原样保留 ——
    /// 丢了 Domain 就退化成「仅此域名」，换个二级域就看不见了，
    /// 表现是「明明登录过，进去又说没登录」。
    static func dumpCookies() -> String {
        var arr: [[String: Any]] = []
        for c in CookieJar.all {
            if c.name.isEmpty || c.value.isEmpty { continue }
            arr.append([
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path.isEmpty ? "/" : c.path,
                "secure": c.isSecure,
            ])
        }
        guard let d = try? JSONSerialization.data(withJSONObject: arr),
              let s = String(data: d, encoding: .utf8) else { return "[]" }
        return s
    }

    /// 把 JSON 还原进原生罐，返回还原成功的条数。
    ///
    /// 🔴 单条坏了必须跳过，不能让一条异常带走整批 ——
    ///    否则一条残缺 cookie 就等于「全部没还原」，看着又像登录失效。
    static func loadCookies(_ json: String) -> Int {
        guard !json.isEmpty,
              let d = json.data(using: .utf8),
              let arr = (try? JSONSerialization.jsonObject(with: d)) as? [[String: Any]]
        else { return 0 }

        var n = 0
        for o in arr {
            let name = (o["name"] as? String) ?? ""
            let val = (o["value"] as? String) ?? ""
            if name.isEmpty || val.isEmpty { continue }

            var dom = (o["domain"] as? String) ?? ""
            if dom.hasPrefix(".") { dom = String(dom.dropFirst()) }
            if dom.isEmpty { dom = "chaoxing.com" }

            var props: [HTTPCookiePropertyKey: Any] = [
                .name: name,
                .value: val,
                .domain: dom,
                .path: normalizePath(o["path"] as? String),
            ]
            if (o["secure"] as? Bool) == true { props[.secure] = "TRUE" }

            if let ck = HTTPCookie(properties: props) {
                CookieJar.add(ck)
                n += 1
            }
        }
        return n
    }

    /// 罐子里有没有登录态。看 `UID` —— 那是 `.chaoxing.com` 域上跨站有效的那份。
    static func hasLogin() -> Bool {
        for c in CookieJar.all {
            if c.name != "UID" && c.name != "_uid" { continue }
            if !c.value.isEmpty { return true }
        }
        return false
    }

    /// 只清学习通那一片，别碰门户的票。
    ///
    /// 🔴 安卓那边特意把这条和 `clearCookiesFor` 分开写，理由一样：
    ///    「断开学习通」不该把门户的 CASTGC 顺手清掉 ——
    ///    用户只是不想用学习通了，结果办事入口又要重新登录一次。
    static func clearCookies() -> Int {
        CookieJar.clearCount(domainSuffix: "chaoxing.com")
    }

    private static func normalizePath(_ p: String?) -> String {
        guard let p = p, !p.isEmpty else { return "/" }
        return p.hasPrefix("/") ? p : "/" + p
    }
}
