import Foundation
import Security

/// 本机存的那几样东西。对位安卓的 `CredStore.java`。
///
/// 安卓那边是「Android Keystore 加密 + 写文件」，因为它的 Keystore 只提供密钥，
/// 不提供存储。iOS 这边有**钥匙串**，本身就是加密存储，所以不用自己再包一层 ——
/// 少一层自研加密，就少一个「加密模块在某些机器上不可用」的坑。
///
/// ## 为什么分成四条而不是一个大 JSON
///
/// 教务、智慧门户、学习通，是**三套完全不同的账号**。安卓最早塞在同一个 JSON 里，
/// 结果出现过「改教务密码，门户密码跟着没了」。分开存之后互不影响。
///
/// ## 免费签名重签之后还在吗
///
/// 在。钥匙串条目的访问组默认是 `<team id>.<bundle id>`，
/// 同一个 Apple ID 重签时两个都不变，所以续签不会丢数据。
/// **卸载重装**才会丢 —— 这一点和安卓一样。
enum CredStore {

    /// 六个独立条目。
    enum Field: String {
        case jw = "hyt.cred.jw"              // 教务（学号 + 密码）
        case portal = "hyt.cred.portal"      // 智慧门户
        case cxPwd = "hyt.cred.cxpwd"        // 学习通账号密码
        case cxCookie = "hyt.cred.cxcookie"  // 学习通 cookie 罐（整段 JSON）
        case water = "hyt.cred.water"        // 饮水机登录态
        // 财务处（缴费）：学号 + 密码 + 欠费快照 + 选过的房间。
        // 🔴 独立一格 —— 四套账号混一起必然串号（门户踩过一次）。
        case cw = "hyt.cred.cw"
    }

    private static let service = "com.jxufe.jiangcaitong"

    // MARK: - 对外（与安卓同名同义）

    /// 存账号密码。**空串 = 成功**，其余是原因 —— 和安卓 0.19 改过的那套一致。
    ///
    /// 🔴 不能返回 bool：以前存失败只有一句 `false`，界面只能说「没存上」，
    ///    看不出是哪一步坏的。现在界面会把原因原样说出来。
    static func save(_ f: Field, user: String, pass: String) -> String {
        guard !user.isEmpty else { return "fail: 账号是空的" }
        let o: [String: Any] = ["u": user, "p": pass]
        guard let d = try? JSONSerialization.data(withJSONObject: o, options: []),
              let s = String(data: d, encoding: .utf8) else {
            return "fail: 拼不出内容"
        }
        return put(f, s)
    }

    /// 取账号密码。返回 `{"u":"…","p":"…"}`，取不到返回空串。
    static func load(_ f: Field) -> String {
        let raw = get(f)
        guard !raw.isEmpty else { return "" }
        guard let d = raw.data(using: .utf8),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
              let u = o["u"] as? String, !u.isEmpty,
              let p = o["p"] as? String, !p.isEmpty else {
            return ""
        }
        let out: [String: Any] = ["u": u, "p": p]
        guard let d2 = try? JSONSerialization.data(withJSONObject: out, options: []),
              let s = String(data: d2, encoding: .utf8) else { return "" }
        return s
    }

    /// `"none"` / `"ok"` / `"broken"` —— 界面靠它区分「没存过」和「存着但读不出来」。
    static func state(_ f: Field) -> String {
        let raw = get(f)
        if raw.isEmpty { return "none" }
        return load(f).isEmpty ? "broken" : "ok"
    }

    static func clear(_ f: Field) {
        SecItemDelete(baseQuery(f) as CFDictionary)
    }

    // MARK: - 整段文本（cookie 罐、饮水机状态）

    /// 存一段原文。空串 = 成功。
    static func saveText(_ f: Field, _ text: String) -> String {
        if text.isEmpty { return "fail: 没有可存的内容" }
        return put(f, text)
    }

    static func loadText(_ f: Field) -> String { get(f) }

    static func textState(_ f: Field) -> String {
        get(f).isEmpty ? "none" : "ok"
    }

    // MARK: - 钥匙串读写

    private static func baseQuery(_ f: Field) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: f.rawValue,
        ]
    }

    private static func get(_ f: Field) -> String {
        var q = baseQuery(f)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne

        var item: CFTypeRef?
        let st = SecItemCopyMatching(q as CFDictionary, &item)
        guard st == errSecSuccess, let d = item as? Data else { return "" }
        return String(data: d, encoding: .utf8) ?? ""
    }

    private static func put(_ f: Field, _ text: String) -> String {
        guard let d = text.data(using: .utf8) else { return "fail: 编码不对" }

        // 🔴 AfterFirstUnlock 而不是 WhenUnlocked：
        //    开机后没解锁过就要能读（饮水机小部件、后台刷新），
        //    WhenUnlocked 那种在锁屏时读会直接失败。
        let attrs: [String: Any] = [
            kSecValueData as String: d,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]

        let st = SecItemUpdate(baseQuery(f) as CFDictionary, attrs as CFDictionary)
        if st == errSecSuccess { return "" }
        if st == errSecItemNotFound {
            var q = baseQuery(f)
            for (k, v) in attrs { q[k] = v }
            let add = SecItemAdd(q as CFDictionary, nil)
            if add == errSecSuccess { return "" }
            return "fail: 存不进钥匙串（\(add)）"
        }
        return "fail: 存不进钥匙串（\(st)）"
    }
}
