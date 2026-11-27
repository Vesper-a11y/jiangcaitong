import Foundation
import WebKit

/// 本机占用与缓存清理。对位安卓的 `Store.java`。
///
/// 为什么要问原生：App 装完几十 MB，真正的大头是网页视图在磁盘上的资源缓存，
/// 页面里那个 localStorage 只有几十 KB，而浏览器根本不让 JS 看磁盘。
enum LocalStore {

    /// 返回 `{"apk":…,"cache":…,"webview":…,"data":…,"total":…,"free":…}`（单位字节）。
    ///
    /// 字段名和安卓一字不差 —— `app.js` 那边按名字读，改名就等于「占用显示 0」。
    static func info() -> String {
        let fm = FileManager.default

        let appPath = Bundle.main.bundlePath
        let apk = size(of: appPath)

        let caches = fm.urls(for: .cachesDirectory, in: .userDomainMask).first
        let docs = fm.urls(for: .documentDirectory, in: .userDomainMask).first
        let lib = fm.urls(for: .libraryDirectory, in: .userDomainMask).first

        let cacheAll = caches.map { size(of: $0.path) } ?? 0

        // 网页视图的磁盘缓存单独拎出来：它通常在 Caches/WebKit 下。
        // 数不出来就报 0 —— 宁可少报，也不要把整个缓存目录重复算一遍。
        //
        // 🔴 类型必须写死 Int64。`size(of:)` 返回的是 Int64（目录大小按字节，
        //    几十 GB 用 Int 也不该溢出，但系统接口本来就是 Int64），
        //    而这里若留成 `var webview = 0`，编译器会推成 Int ——
        //    下一行 `cacheAll - webview` 立刻报「不能用 Int64 减 Int」。
        //    第一版就是这么红的，两条错误都在这一处。
        var webview: Int64 = 0
        if let c = caches {
            let wk = c.appendingPathComponent("WebKit")
            if fm.fileExists(atPath: wk.path) { webview = size(of: wk.path) }
        }

        // 普通缓存 = 缓存目录减去网页那部分，免得两块重叠加起来比实际还大
        let plainCache = max(Int64(0), cacheAll - webview)

        // 「应用数据」按系统设置那个口径：Documents + Library。
        // Library 里已经含 Caches，所以不能再加一次。
        let data = (docs.map { size(of: $0.path) } ?? 0) + (lib.map { size(of: $0.path) } ?? 0)

        var free: Int64 = 0
        if let v = try? URL(fileURLWithPath: NSHomeDirectory())
            .resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
           let n = v.volumeAvailableCapacityForImportantUsage {
            free = n
        }

        let o: [String: Any] = [
            "apk": apk,
            "cache": plainCache,
            "webview": webview,
            "data": data,
            "total": data + apk,
            "free": free,
        ]
        guard let d = try? JSONSerialization.data(withJSONObject: o),
              let s = String(data: d, encoding: .utf8) else { return "{}" }
        return s
    }

    /// 清网页视图在磁盘上的资源缓存。
    ///
    /// 🔴 不清 cookie、不清 localStorage —— 课表和登录态都在那儿。
    ///    清缓存不该逼人重新绑定，只是下次打开学校页面要重新下资源。
    ///
    /// 异步执行，调用方（`clearWebCache`）本来就不等返回值。
    static func clearWebCache(_ done: (() -> Void)? = nil) {
        let types: Set<String> = [
            WKWebsiteDataTypeDiskCache,
            WKWebsiteDataTypeMemoryCache,
            WKWebsiteDataTypeOfflineWebApplicationCache,
        ]
        WKWebsiteDataStore.default().removeData(ofTypes: types,
                                                modifiedSince: .distantPast) {
            done?()
        }
    }

    // MARK: - 目录大小

    /// 递归算一个目录占多大。算不出来的部分按 0 计 —— 不能因为一个读不到的文件就整体失败。
    private static func size(of path: String) -> Int64 {
        let fm = FileManager.default
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: path, isDirectory: &isDir) else { return 0 }

        if !isDir.boolValue {
            return fileSize(path)
        }

        guard let en = fm.enumerator(atPath: path) else { return 0 }
        var total: Int64 = 0
        for case let rel as String in en {
            let full = (path as NSString).appendingPathComponent(rel)
            total += fileSize(full)   // 目录条目本身报不出来，自然是 0
        }
        return total
    }

    private static func fileSize(_ path: String) -> Int64 {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
              let n = attrs[.size] as? Int64 else { return 0 }
        return n
    }
}
