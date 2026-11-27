import UIKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {

    var window: UIWindow?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {

        // 🔴 Cookie 接收策略必须是 always。
        //    默认的 onlyFromMainDocumentDomain 只收「主文档同域」下的 Set-Cookie，
        //    而教务系统是登录时从 jwglxt.upstream.edu.cn 下发、后续跳转到别的路径，
        //    主文档域一变就漏收。漏收的表现是「登录成功但下一页说没登录」，
        //    查起来极其费劲 —— 因为每一句代码都是对的，只是 cookie 没进罐子。
        HTTPCookieStorage.shared.cookieAcceptPolicy = .always

        let win = UIWindow(frame: UIScreen.main.bounds)
        win.rootViewController = WebViewController()
        win.makeKeyAndVisible()
        window = win
        return true
    }
}
