import SafariServices
import UIKit
import WebKit

/// 唯一的一页：一个铺满全屏的 WKWebView，里面跑 `web/` 那套界面。
///
/// 和安卓的 `MainActivity` 是对应的。界面一行都不用改 —— 两个平台跑的是同一份
/// `index.html` / `app.js` / `native.js`，差别只在底下接的桥。
final class WebViewController: UIViewController {

    private var webView: WKWebView!
    private var bridge: Bridge!

    /// 加载失败时压在页面上的一层字。空着比白屏好 —— 白屏没法判断是卡住了还是崩了。
    private var failLabel: UILabel?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .white
        buildWebView()
        loadApp()
    }

    // 浅色底，状态栏用深色字。不设这个，刘海那块会是白字白底，看不见。
    override var preferredStatusBarStyle: UIStatusBarStyle { .darkContent }

    // MARK: - 搭起来

    private func buildWebView() {
        let cfg = WKWebViewConfiguration()

        // 页面里没有音视频，但留着这两句防止以后加了被静音。
        cfg.allowsInlineMediaPlayback = true
        cfg.mediaTypesRequiringUserActionForPlayback = []

        let ucc = WKUserContentController()
        // 🔴 注入时机必须是 documentStart：native.js 第 20 行一进来就检查
        //    `window.Android` 在不在，不在就整层 return。晚一步注入等于没注入。
        ucc.addUserScript(WKUserScript(source: Shim.source,
                                       injectionTime: .atDocumentStart,
                                       forMainFrameOnly: true))

        bridge = Bridge()
        ucc.add(bridge, name: "hyt")
        cfg.userContentController = ucc

        // 网页视图自己那个 cookie 罐，挂到同步快照上去 —— 页面的
        // `webCookie*` 系列要同步取值，而 WKHTTPCookieStore 只有异步接口。
        WebCookieCache.shared.bind(cfg.websiteDataStore.httpCookieStore)

        webView = WKWebView(frame: .zero, configuration: cfg)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.customUserAgent = UserAgent.value
        webView.isOpaque = false
        webView.backgroundColor = .white
        webView.allowsBackForwardNavigationGestures = false

        // 🔴 关掉系统自动调整：页面 CSS 自己用 env(safe-area-inset-*)
        //    给刘海和小白条留了位置。让系统再塞一次，顶部会多空出一条。
        webView.scrollView.contentInsetAdjustmentBehavior = .never

        webView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            webView.topAnchor.constraint(equalTo: view.topAnchor),
            webView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])

        bridge.attach(webView: webView, host: self)
    }

    private func loadApp() {
        // XcodeGen 里配的是 folder reference，bundle 里就是 web/index.html。
        // 多试一个路径：不同打包方式下它可能落在子目录里。
        let candidates: [URL?] = [
            Bundle.main.url(forResource: "web", withExtension: nil),
            Bundle.main.resourceURL?.appendingPathComponent("web"),
        ]
        guard let dir = candidates.compactMap({ $0 }).first(where: {
            FileManager.default.fileExists(atPath: $0.appendingPathComponent("index.html").path)
        }) else {
            showFatal("找不到内嵌页面：包里没有 web/index.html")
            return
        }
        let index = dir.appendingPathComponent("index.html")
        webView.loadFileURL(index, allowingReadAccessTo: dir)
    }

    private func showFatal(_ text: String) {
        let l = UILabel()
        l.numberOfLines = 0
        l.textAlignment = .center
        l.font = .systemFont(ofSize: 15)
        l.textColor = UIColor(white: 0.35, alpha: 1)
        l.text = text
        l.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(l)
        NSLayoutConstraint.activate([
            l.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            l.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            l.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 32),
            l.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -32),
        ])
        failLabel = l
    }

    // MARK: - 外部网页

    /// 用系统浏览器打开。应用内嵌一个网页会让「返回」变得很别扭，
    /// 而且那类页面（学习通、门户）本来就该在浏览器里登录。
    fileprivate func openInSafari(_ url: URL) {
        let cfg = SFSafariViewController.Configuration()
        cfg.entersReaderIfAvailable = false
        let vc = SFSafariViewController(url: url, configuration: cfg)
        vc.preferredBarTintColor = .white
        vc.preferredControlTintColor = UIColor(red: 0.13, green: 0.45, blue: 0.86, alpha: 1)
        present(vc, animated: true)
    }
}

// MARK: - 导航

extension WebViewController: WKNavigationDelegate {

    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {

        guard let url = navigationAction.request.url else {
            decisionHandler(.allow)
            return
        }

        // 本地页面（file://）和 about:blank 照常放行
        if url.isFileURL || url.scheme == "about" || url.scheme == "data" {
            decisionHandler(.allow)
            return
        }

        // 其余一律交给系统：应用内跳出去会丢掉本地页面的状态。
        if url.scheme == "http" || url.scheme == "https" {
            UIApplication.shared.open(url)
            decisionHandler(.cancel)
            return
        }

        // tel: / mailto: / 自定义 scheme 交给系统判断
        UIApplication.shared.open(url)
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView,
                 didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        let e = error as NSError
        // -999 是「主动取消」（上面 decidePolicy 拦下来的），不是故障
        if e.code == NSURLErrorCancelled { return }
        showFatal("页面没能载入：\(e.localizedDescription)")
    }
}

// MARK: - 同步通道 + 防崩

extension WebViewController: WKUIDelegate {

    /// 🔴 这是整个桥的关键。32 个同步方法全靠它把值当场送回 JS。
    ///
    /// 只有带 `hyt:` 前缀的才拦 —— 别的 prompt 一律回 nil，
    /// 免得以后页面真要用 prompt 时行为变了。
    func webView(_ webView: WKWebView,
                 runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        guard prompt.hasPrefix("hyt:") else {
            completionHandler(nil)
            return
        }
        completionHandler(bridge.handleSync(prompt))
    }

    /// 页面里其实不调 alert（app.js 有注释说明为什么），但**必须实现**：
    /// 不实现的话，任何一处漏网的 alert 会让 WebView 直接崩，而且不报原因。
    func webView(_ webView: WKWebView,
                 runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping () -> Void) {
        let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "好", style: .default) { _ in completionHandler() })
        present(a, animated: true)
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "取消", style: .cancel) { _ in completionHandler(false) })
        a.addAction(UIAlertAction(title: "确定", style: .default) { _ in completionHandler(true) })
        present(a, animated: true)
    }
}

// MARK: - 请求头里的身份

enum UserAgent {
    /// 🔴 和 `Android.req` 发出去的那个 UA **一模一样**，哪怕它写着 "Linux; Android"。
    ///
    /// 教务系统按 UA 分派页面模板，而 `native.js` 里所有表格列序、表单字段名
    /// 都是照安卓那个模板量的。这边改说自己是 iPhone Safari，学校会不会换一套
    /// 结构我们说了不算 —— 而对不上时的表现是「登录成功了，但一页数据都没有」，
    /// 是最难查的那类故障。
    ///
    /// 代价只有一条：网页里 `navigator.userAgent` 也会这么报。页面本身没有
    /// 按 UA 分支的逻辑（用的是 NATIVE / envText 那套开关），所以不受影响。
    static let value = Net.UA
}
