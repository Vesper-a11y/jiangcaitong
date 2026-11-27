package com.jxufe.jiangcaitong;

import android.app.AlarmManager;
import android.app.NotificationManager;
import android.app.job.JobScheduler;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.CalendarContract;
import android.provider.Settings;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpCookie;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * JS 与原生之间唯一的通道。
 *
 * 分工：这里**只负责发 HTTP 和带 cookie**，登录/解析全在 native.js 里。
 * 之所以不把解析也搬过来：WebView 有 DOMParser，按表头名字取列比在 Java 里
 * 手写 HTML 解析稳得多，而且解析逻辑只有一份（对照测试见 _lab/out/check_native_js.py）。
 *
 * 🔴 请求必须异步回：Android.req 是阻塞调用，如果让 JS 同步等，登录加同步
 *    要十几秒，整页会僵住 —— 转圈动画都不动，用户会以为死机了。
 *    所以这里丢线程池，完成后用 evaluateJavascript 回调 window.__nativeDone。
 */
public class NativeBridge {

    private static final String BASE = "https://jwxt.jxufe.edu.cn";
    private static final String UA =
            "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36";
    /** 导出的日历文件类型。日历 App 靠它认出「这是能导入的日程」。 */
    private static final String MIME_ICS = "text/calendar";

    private final WebView web;
    private final ExecutorService pool = Executors.newCachedThreadPool();

    public NativeBridge(WebView web) {
        this.web = web;
    }

    /** 发一个请求，结果异步回给 JS。参数与 native.js 里的 http() 一一对应。 */
    @JavascriptInterface
    public void req(final int id, final String method, final String path,
                    final String body, final String referer) {
        req2(id, method, path, body, referer, null);
    }

    /**
     * 带自定义请求头的版本。`headersJson` 形如 `{"authorization":"eyJ..."}`。
     *
     * 为什么需要单独一个方法而不是给 req 加参数：req 是既有接口，
     * native.js 里几十处调用都按 5 个参数写，改签名要全动一遍，风险不值得。
     *
     * 🔴 智慧门户的网关**只认请求头里的 `authorization`**（JWT 同时也在同名
     *    cookie 里，但只带 cookie 会 401「您正在尝试访问未授权的功能!」）。
     *    没有这个重载，门户的取数接口一个都调不通。
     */
    @JavascriptInterface
    public void req2(final int id, final String method, final String path,
                     final String body, final String referer, final String headersJson) {
        pool.execute(new Runnable() {
            @Override
            public void run() {
                // 不用「空白 final 在 try/catch 里各赋一次」的写法：javac 对
                // 这种 definite-assignment 的处理容易报莫名其妙的重复定义。
                String text;
                try {
                    text = doReq(method, path, body, referer, headersJson);
                } catch (Throwable t) {
                    text = fail(String.valueOf(t));
                }
                final String json = text;
                // evaluateJavascript 必须在 UI 线程
                web.post(new Runnable() {
                    @Override
                    public void run() {
                        web.evaluateJavascript(
                                "window.__nativeDone(" + id + "," + JSONObject.quote(json) + ")",
                                null);
                    }
                });
            }
        });
    }

    /** 江财数据层专用请求通道：回调到 window.__jxufeDone，和原学校那套 waiting 彻底隔离，
     *  避免两边 seq 撞号导致回调串到对方 Promise。参数和 req2 完全一致。 */
    @JavascriptInterface
    public void reqJxufe(final int id, final String method, final String path,
                         final String body, final String referer, final String headersJson) {
        pool.execute(new Runnable() {
            @Override
            public void run() {
                String text;
                try {
                    text = doReq(method, path, body, referer, headersJson);
                } catch (Throwable t) {
                    text = fail(String.valueOf(t));
                }
                final String json = text;
                web.post(new Runnable() {
                    @Override
                    public void run() {
                        web.evaluateJavascript(
                                "window.__jxufeDone(" + id + "," + JSONObject.quote(json) + ")",
                                null);
                    }
                });
            }
        });
    }

    // ---- 江财教务 Cookie（真人浏览器登录后粘贴，加密落盘） ----------------

    /** 从 App 内置 WebView 的 CookieManager 里读教务域的 cookie。
     *
     *  为什么需要它：统一身份认证可能要过**二次验证 / 验证码 / 扫码**，
     *  那几步只能人来做。用户在 App 里正常登录一次之后，WebView 那套罐子里
     *  已经有 JSESSIONID；但 android.webkit.CookieManager 和 java.net 的
     *  CookieStore 是**两个罐子**，取数走的是后者 —— 所以把字符串抄过来。
     *  返回形如 "JSESSIONID=xxx; route=xxx"；没登录过就返回空串。
     *
     *  🔴🔴 必须借 webCookiesOnMain() 进主线程（2026-11-23 v6 的坑，用户：
     *      「我登陆完成回来之后也没有用啊」）。
     *      `@JavascriptInterface` 跑在 WebView 的 **JavaBridge 线程**上，
     *      在那里直接调 `CookieManager.getCookie()` 会抛
     *      「A WebView method was called on thread 'JavaBridge'」——
     *      而下面那层 `catch (Throwable) { return ""; }` 会把它吞掉，
     *      于是**永远返回空串**。JS 侧看到空串就判「网页里还没登录成功」，
     *      界面一动不动 —— 和「用户根本没登录」长得一模一样。
     *      这正是本文件 webCookiesOnMain() 那段注释里写过的同一个坑，
     *      写这个方法时又踩了一遍。 */
    @JavascriptInterface
    public String eamsCookieFromWebView() {
        // 🔴 要问**多个路径**，不能只问根（2026-11-23）。
        //    `CookieManager.getCookie(url)` 只返回 path 匹配这个 url 的 cookie；
        //    教务的会话如果是挂在 `/jw/`、`/frame/`、`/jwxt/` 这类子路径上，
        //    问 `https://host/` 得到的就是**一条都匹配不上**，或者只剩一条早先
        //    留在根路径上的死 JSESSIONID —— 后者正是用户看到的
        //    「网页里自检是通的，搬到 App 就过期了」。
        //    按「浅 → 深」问，深路径的结果覆盖同名项：越具体的那条越该被采用。
        return mergedWebCookies(new String[] {
                "https://jwxt.jxufe.edu.cn/",
                "https://jwxt.jxufe.edu.cn/jw/",
                "https://jwxt.jxufe.edu.cn/frame/",
                "https://jwxt.jxufe.edu.cn/jwxt/",
                "https://jwxt.jxufe.edu.cn/cas/"
        });
    }

    /** 网页登录时实际用的 UA。
     *
     *  为什么要留一份（2026-11-23）：内置网页打开「登录教务系统」时走的是
     *  **电脑版 UA**（见 WebActivity.applyMode），会话就是在这个 UA 下创建的。
     *  而 java.net 取数用的是另一串手机 UA —— 只要学校把会话和 UA 绑在一起，
     *  表现就是「网页里明明通着，App 一取就说过期」。取数时改用它即可对齐。
     *  空串 = 没登录过，按原样用默认 UA。 */
    static volatile String jxWebUa = "";

    @JavascriptInterface
    public String webUa() {
        String u = jxWebUa;
        return u == null ? "" : u;
    }

    /** 把一组 url 各自的 cookie 合并成一行 `a=1; b=2`（同名取深路径那条）。 */
    private String mergedWebCookies(final String[] urls) {
        if (Looper.myLooper() == Looper.getMainLooper()) return mergeOnMain(urls);
        final String[] box = new String[1];
        final CountDownLatch latch = new CountDownLatch(1);
        try {
            new Handler(Looper.getMainLooper()).post(new Runnable() {
                @Override
                public void run() {
                    try {
                        box[0] = mergeOnMain(urls);
                    } catch (Throwable t) {
                        box[0] = "";
                    } finally {
                        latch.countDown();
                    }
                }
            });
            // 一次跳主线程问全部，比每个 url 跳一次省得多（那边每次要等 800ms）
            latch.await(1200, TimeUnit.MILLISECONDS);
        } catch (Throwable ignored) { }
        return box[0] == null ? "" : box[0];
    }

    private static String mergeOnMain(String[] urls) {
        java.util.LinkedHashMap<String, String> map =
                new java.util.LinkedHashMap<String, String>();
        for (String u : urls) {
            String line;
            try {
                line = CookieManager.getInstance().getCookie(u);
            } catch (Throwable t) {
                continue;
            }
            if (line == null || line.isEmpty()) continue;
            for (String piece : line.split(";")) {
                int eq = piece.indexOf('=');
                if (eq <= 0) continue;
                String k = piece.substring(0, eq).trim();
                String v = piece.substring(eq + 1).trim();
                if (k.isEmpty() || v.isEmpty()) continue;
                map.put(k, v);          // 深的路径在后面 ⇒ 覆盖浅的，正是「最具体」那条
            }
        }
        StringBuilder sb = new StringBuilder();
        for (java.util.Map.Entry<String, String> e : map.entrySet()) {
            if (sb.length() > 0) sb.append("; ");
            sb.append(e.getKey()).append('=').append(e.getValue());
        }
        return sb.toString();
    }

    /** 网页里对「这个会话到底通不通」的自检结论（见 WebActivity.PROBE_JS）。
     *
     *  为什么要把这个结论搬出来：用户在网页里扫码登进去了，回 App 却「读不出来」，
     *  这时只有两种可能 —— 网页里其实也没登上，或者网页里是通的、搬到 App 时丢了。
     *  两种情况在界面上长得一模一样，只能靠这条自检分家。
     *  空串 = 没测过（没走到教务首页）。 */
    static volatile String webProbe = "";

    @JavascriptInterface
    public String eamsWebProbe() {
        String p = webProbe;
        return p == null ? "" : p;
    }

    /** 供 WebActivity 写自检结论（跨 Activity，只能走静态）。
     *
     *  🔴 一旦读到过「通」的结论，就别让后面的「不通」覆盖它：
     *     onPageFinished 会一页一页地触发，最后那次探测的很可能是个正在跳走的
     *     中间页 —— 而用户真正想知道的是「他刚登进去那一下到底通没通」。 */
    static void setWebProbe(String p) {
        String s = p == null ? "" : p;
        String old = webProbe;
        if (old != null && old.indexOf("userCode") >= 0 && s.indexOf("userCode") < 0) return;
        webProbe = s;
    }

    /** 把 JS 解析好的 cookie 数组注入 java.net 全局 CookieManager。
     *  数组元素形如 {name,value,domain,path,secure}；返回注入成功的条数。 */
    @JavascriptInterface
    public int eamsCookieSet(String json) {
        int n = 0;
        try {
            java.net.CookieHandler h = java.net.CookieHandler.getDefault();
            if (!(h instanceof java.net.CookieManager)) return 0;
            java.net.CookieStore store = ((java.net.CookieManager) h).getCookieStore();
            JSONArray arr = new JSONArray(json);
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.optJSONObject(i);
                if (o == null) continue;
                String name = o.optString("name", ""), val = o.optString("value", "");
                if (name.isEmpty()) continue;
                try {
                    HttpCookie ck = new HttpCookie(name, val);
                    String dom = o.optString("domain", "jwxt.jxufe.edu.cn");
                    ck.setDomain(dom);
                    String p = o.optString("path", "/");
                    ck.setPath(p.isEmpty() ? "/" : p);
                    ck.setSecure(false);
                    String host = dom.startsWith(".") ? dom.substring(1) : dom;
                    store.add(new URI("https://" + host + "/"), ck);
                    n++;
                } catch (Throwable ignored) { /* 单条坏了跳过 */ }
            }
        } catch (Throwable ignored) { /* 整段坏了 */ }
        return n;
    }

    @JavascriptInterface
    public String eamsCookieSave(String raw) {
        String s = raw;
        // 学号密码登录（2026-11-23）：会话 cookie 是 HttpURLConnection 自己收进
        // CookieManager 的，JS 侧拿不到 Set-Cookie 原文。传空串表示「把当前罐子
        // 里的 JSESSIONID 落盘」，这样下次开机 restoreCookie() 还能复用。
        if (s == null || s.trim().isEmpty()) {
            try {
                java.net.CookieHandler h = java.net.CookieHandler.getDefault();
                if (h instanceof java.net.CookieManager) {
                    java.net.CookieStore store = ((java.net.CookieManager) h).getCookieStore();
                    StringBuilder sb = new StringBuilder();
                    for (java.net.HttpCookie c : store.getCookies()) {
                        if ("JSESSIONID".equals(c.getName())
                                && (c.getDomain() == null || c.getDomain().contains("jxufe"))) {
                            if (sb.length() > 0) sb.append("; ");
                            sb.append(c.getName()).append("=").append(c.getValue());
                        }
                    }
                    s = sb.toString();
                }
            } catch (Throwable t) {
                return "fail: 读取会话 cookie 失败：" + t;
            }
            if (s == null || s.isEmpty()) return "fail: 登录后没拿到会话 cookie";
        }
        return CredStore.saveEamsCookie(s);
    }

    @JavascriptInterface
    public String eamsCookieLoad() {
        return CredStore.loadEamsCookie();
    }

    @JavascriptInterface
    public void eamsCookieClear() {
        CredStore.clearEamsCookie();
        clearCookies();
    }

    /**
     * 原生定时器：到点回调网页里的 `window.__bgWake(id)`。
     *
     * 🔴 为什么非要有这一个（2026-11-16，用户第八次报「学习通还是没法后台检查」）：
     *    后台那个 WebView 不挂在任何窗口上，Chromium 把它当隐藏页面，
     *    **setTimeout 会被节流**（1 秒/次，隐藏久了 1 分钟/次）。而学习通扫描
     *    的节奏全在网页里：每门课之间等 1.8 秒、每个请求之间等 0.45 秒 ——
     *    三十多门课一轮，光等待就被拉成几十分钟，必然撞上 AutoJob 的硬超时，
     *    面板上写的就是「没查完（等太久），下一轮再试」。
     *    这个 Handler 在原生侧，网页藏起来也照样到点，不受节流影响。
     */
    @JavascriptInterface
    public void wake(final int id, final long ms) {
        web.postDelayed(new Runnable() {
            @Override
            public void run() {
                try {
                    web.evaluateJavascript("window.__bgWake(" + id + ")", null);
                } catch (Throwable ignored) {
                }
            }
        }, Math.max(0L, ms));
    }

    private String doReq(String method, String path, String body, String referer,
                         String headersJson) throws Exception {        // 传绝对地址（http:// 或 https:// 开头）就按原样发，否则拼在教务系统的 BASE 后面。
        // 为什么需要：校园公告要访问 www.upstream.edu.cn / jwc.upstream.edu.cn 等 6 个站，
        // 它们跟教务系统不是同一个域名，写死 BASE + path 根本发不出去。
        // 保持「相对路径拼 BASE」这条老行为不变，是为了不动现有的教务逻辑。
        boolean absolute = path != null
                && (path.startsWith("http://") || path.startsWith("https://"));
        String fullUrl = absolute ? path : BASE + path;

        HttpURLConnection c = (HttpURLConnection) new URL(fullUrl).openConnection();
        try {
            c.setRequestMethod(method == null || method.isEmpty() ? "GET" : method);
            c.setConnectTimeout(15000);
            c.setReadTimeout(30000);
            c.setInstanceFollowRedirects(true);
            c.setRequestProperty("User-Agent", UA);
            c.setRequestProperty("Accept-Language", "zh-CN,zh;q=0.9");
            c.setRequestProperty("Accept",
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8");
            if (referer != null && !referer.isEmpty()) {
                // 绝对地址的 referer 也按原样用，不然会拼成
                // http://jwglxt.upstream.edu.cnhttp://www.upstream.edu.cn 这种鬼东西
                boolean refAbsolute = referer.startsWith("http://") || referer.startsWith("https://");
                c.setRequestProperty("Referer", refAbsolute ? referer : BASE + referer);
            }
            // 🔴 自定义头必须在写 body **之前**设置：getOutputStream() 会触发
            //    connect，之后再 setRequestProperty 直接抛 IllegalStateException。
            boolean hasCt = applyHeaders(c, headersJson);
            if (body != null && !body.isEmpty()) {
                c.setDoOutput(true);
                if (!hasCt) {
                    c.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
                }
                byte[] raw = body.getBytes(StandardCharsets.UTF_8);
                c.setFixedLengthStreamingMode(raw.length);
                OutputStream os = c.getOutputStream();
                os.write(raw);
                os.close();
            }

            int code = c.getResponseCode();
            InputStream in = (code >= 400) ? c.getErrorStream() : c.getInputStream();
            String enc = charsetOf(c.getContentType());
            if (enc.isEmpty()) enc = pathCharset(fullUrl);
            String text = readAll(in, enc);
            String finalUrl = String.valueOf(c.getURL());

            // 兜底：HttpURLConnection 只自动跟 301/302/303（会把 POST 改成 GET），
            // 307/308 是不跟的。教务系统登录成功是 302，正常走不到这里；
            // 万一哪天换成 307，不兜这一层就会把「登录成功」误判成「登录失败」。
            if (code >= 300 && code < 400) {
                String loc = c.getHeaderField("Location");
                if (loc != null && !loc.isEmpty()) {
                    String next = new URL(new URL(finalUrl), loc).toString();
                    HttpURLConnection c2 = (HttpURLConnection) new URL(next).openConnection();
                    try {
                        c2.setConnectTimeout(15000);
                        c2.setReadTimeout(30000);
                        c2.setRequestProperty("User-Agent", UA);
                        c2.setRequestProperty("Accept-Language", "zh-CN,zh;q=0.9");
                        code = c2.getResponseCode();
                        String enc2 = charsetOf(c2.getContentType());
                        if (enc2.isEmpty()) enc2 = pathCharset(next);
                        text = readAll(code >= 400 ? c2.getErrorStream() : c2.getInputStream(), enc2);
                        finalUrl = String.valueOf(c2.getURL());
                    } finally {
                        c2.disconnect();
                    }
                }
            }

            JSONObject o = new JSONObject();
            o.put("status", code);
            o.put("body", text);
            o.put("url", finalUrl);
            // 响应头里的 Date（秒，没有就是 -1 或 0）。慧生活的积分签名有个
            // 30 秒的时间桶，手机时间偏十几秒就可能差一格 ⇒「验签失败」。
            // 官方客户端拿这个头校准，JS 侧用 `r.date` 记时差（见 wSignScore）。
            o.put("date", c.getDate());
            return o.toString();
        } finally {
            c.disconnect();
        }
    }

    /** 把 JS 传来的 `{"k":"v"}` 逐个设成请求头。返回是否设过 Content-Type。
     *
     *  解析失败就当没传 —— 宁可少一个头让请求正常失败（能看到 401），
     *  也不要整个请求炸掉。
     *
     *  Content-Type 允许覆盖：门户的一卡通接口要 `application/json`，
     *  而这里默认给的是表单类型，不覆盖就永远 415/400。
     */
    private static boolean applyHeaders(HttpURLConnection c, String headersJson) {
        boolean hasCt = false;
        if (headersJson == null || headersJson.isEmpty()) return hasCt;
        try {
            JSONObject o = new JSONObject(headersJson);
            java.util.Iterator<String> it = o.keys();
            while (it.hasNext()) {
                String k = it.next();
                String v = o.optString(k, "");
                if (k == null || k.isEmpty() || "content-length".equalsIgnoreCase(k)) continue;
                if ("content-type".equalsIgnoreCase(k)) hasCt = true;
                // 🔴 一条坏了只跳过这一条。以前整段一个 try，某一条值里带了
                //    非法字符（比如从网页抄来的长 Cookie 里夹了控制字符）就会让
                //    后面所有头一起失效 —— 表现是「请求发出去了但不带身份」，
                //    和「没登录」长得一模一样，极难查。
                try { c.setRequestProperty(k, v); } catch (Throwable ignored) { }
            }
        } catch (Throwable ignored) {
            // 见上：静默跳过，但请求本身照发
        }
        return hasCt;
    }

    /** 按响应头 charset 解码；没有 charset 时按调用方给的兜底编码。
     *  江财金智的 .jsp/.action 是 GBK，原学校老站是 UTF-8 —— 一个方法同时兼容两边。
     *
     *  🔴 1.5.2：响应头声明的编码**不一定可信**。教务 DataTable.jsp 这类页面实测过
     *  「头说 UTF-8、字节其实是 GBK」—— 解出来满屏「锘/绗/鏄」这种乱码，
     *  前端靠中文表头（课程/学分/类别）认表格就直接认不出来，用户看到「读不到」。
     *  所以解完做一次乱码体检：一旦命中，换另一种编码重解，取更干净的那份。 */
    private static String readAll(InputStream in, String fallbackCharset) throws Exception {
        if (in == null) return "";
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
        in.close();
        byte[] raw = bos.toByteArray();
        String cs = fallbackCharset;
        if (cs == null || cs.isEmpty()) cs = "UTF-8";
        String decoded;
        try {
            decoded = new String(raw, java.nio.charset.Charset.forName(cs));
        } catch (Exception e) {
            decoded = new String(raw, StandardCharsets.UTF_8);
        }
        if (!looksMoji(decoded)) return decoded;
        // 解出来像乱码：拿另一套编码重解，哪套「更像人话」用哪套。
        String other = "UTF-8".equalsIgnoreCase(cs) ? "GBK" : "UTF-8";
        try {
            String alt = new String(raw, java.nio.charset.Charset.forName(other));
            if (mojiScore(alt) < mojiScore(decoded)) return alt;
        } catch (Exception ignored) { }
        return decoded;
    }

    /** 乱码体检：U+FFFD 替换符 或「锘/绗/鏄/銆」这类 UTF-8 被当 GBK 读的典型字。 */
    private static boolean looksMoji(String s) {
        return mojiScore(s) >= 3;
    }

    private static int mojiScore(String s) {
        if (s == null || s.isEmpty()) return 0;
        int n = Math.min(s.length(), 6000), score = 0;
        for (int i = 0; i < n; i++) {
            char ch = s.charAt(i);
            if (ch == '\ufffd') score += 2;               // 解码失败的替换符
            else if (ch == '锘' || ch == '绗' || ch == '鏄' || ch == '鐨' || ch == '銆'
                  || ch == '涓' || ch == '锛' || ch == '鐗' || ch == '鍒' || ch == '鎴'
                  || ch == '瀛' || ch == '璇' || ch == '鍒' || ch == '鏁' || ch == '鏃') score += 1;
        }
        return score;
    }

    /** 从 Content-Type 里抠 charset；没写就返回空串。 */
    private static String charsetOf(String contentType) {
        if (contentType == null) return "";
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("charset\\s*=\\s*['\"]?([\\w-]+)", java.util.regex.Pattern.CASE_INSENSITIVE)
                .matcher(contentType);
        return m.find() ? m.group(1) : "";
    }

    /** 路径兜底编码：江财金智的 jsp/action/xml 是 GBK，其余按 UTF-8。 */
    private static String pathCharset(String url) {
        if (url == null) return "UTF-8";
        String u = url.toLowerCase(java.util.Locale.ROOT);
        if (u.contains("jxufe.edu.cn") && (u.contains(".jsp") || u.contains(".action") || u.contains(".xml"))) {
            return "GBK";
        }
        return "UTF-8";
    }

    private static String fail(String msg) {
        try {
            JSONObject o = new JSONObject();
            o.put("status", 0);
            o.put("body", "");
            o.put("url", "");
            o.put("error", msg);
            return o.toString();
        } catch (Exception e) {
            return "{\"status\":0,\"body\":\"\",\"url\":\"\",\"error\":\"unknown\"}";
        }
    }

    // ---------------------------------------------------------------- 凭据
    // 不走网络，直接同步返回，省得 JS 里再包一层 Promise。

    @JavascriptInterface
    public String loadCred() {
        return CredStore.load();
    }

    /**
     * 存凭据。
     *
     * 🔴 返回值从 boolean 改成了 String（2026-10-19）：以前存失败只有一句
     *    `false`，界面只能说「没存上」，看不出是哪一步坏的，也没法自救。
     *    现在空串 = 成功，其余是原因（"soft" / "fail: xxx"），
     *    界面必须把原因原样说出来 —— 不许再出现「提示保存成功、下次又要重输」。
     */
    @JavascriptInterface
    public String saveCred(String user, String pass) {
        return CredStore.save(user, pass);
    }

    /** "none" / "ok" / "broken"，给界面区分「没存过」和「存着但解不开」。 */
    @JavascriptInterface
    public String credState() {
        return CredStore.state();
    }

    @JavascriptInterface
    public void clearCred() {
        CredStore.clear();
    }

    // ---- 门户密码（和教务那份是两套账号，分开存）----
    // 存在的意义：门户的登录状态撑不过 7.2 小时，过期就得重新登录；
    // 密码留在本机，过期重登就只剩一道算术验证码要人算。

    @JavascriptInterface
    public String loadPortalCred() {
        return CredStore.loadPortal();
    }

    @JavascriptInterface
    public String savePortalCred(String user, String pass) {
        return CredStore.savePortal(user, pass);
    }

    @JavascriptInterface
    public String portalCredState() {
        return CredStore.portalState();
    }

    @JavascriptInterface
    public void clearPortalCred() {
        CredStore.clearPortal();
    }

    // ---- 存储占用 ----
    // 🔴 装完占五十多 MB 的账要算得清：前端「清除本机缓存」以前只删 localStorage
    //    里那几个键（几十 KB），磁盘上的 WebView 缓存（几十 MB）一动不动，
    //    按钮名不副实。这两个接口把真账交出去。

    /** 各目录字节数：{"cache":…,"webview":…,"data":…,"free":…}。 */
    @JavascriptInterface
    public String storageInfo() {
        try {
            return Store.info(web.getContext());
        } catch (Throwable t) {
            return "{}";
        }
    }

    /**
     * 清 WebView 在磁盘上的资源缓存（HTTP Cache / Code Cache）。
     *
     * 不清 cookie、不清 localStorage —— 课表和登录态都在那儿，
     * 清缓存不该逼人重新绑定。删完只是下次打开学校页面要重新下资源。
     *
     * 走 `web.post`：`clearCache` 必须在 UI 线程，而 JS 接口跑在
     * WebView 的 JavaBridge 线程上，直接调在部分机型上会抛
     * 「A WebView method was called on thread 'JavaBridge'」。
     */
    @JavascriptInterface
    public void clearWebCache() {
        final WebView v = web;
        if (v == null) return;
        v.post(new Runnable() {
            @Override
            public void run() {
                try {
                    v.clearCache(true);
                } catch (Throwable t) {
                    // 清不掉就算了：这不是功能，失败不该弹任何东西
                }
            }
        });
    }

    // ---- 慧生活798（宿舍接水）----
    // 🔴 这一段是 0.24.0 发布后补的。0.23.0 里 `CredStore` 早就写好了
    //    saveWater / loadWater / clearWater / waterState，但**这里漏了转发** ——
    //    于是 JS 调 `Android.saveWater(...)` 拿到 undefined，登录成功后报
    //    「token 存不进本机（none）」，关掉 App 就得重登。
    //    界面判据没抓到，因为假桥是**按 JS 的期望写的**：JS 调什么它实现什么。
    //    ⇒ 现在有 check_bridge_methods.py 拿两边的方法名对账（红线 50b）。
    //
    //    存的是整段 JSON（token / 手机号 / 设备列表 / 上次同步时间），
    //    加密方式复用 CredStore 的 savePlain —— 和 cookie 同一套，不另抄一份。

    /** 空串 = 存好了，其余是原因（和 saveCred 一样，界面必须原样说出来）。 */
    @JavascriptInterface
    public String saveWater(String json) {
        return CredStore.saveWater(json);
    }

    /** 取回 JSON。解不开返回空串（当作没登录），不把异常抛给 JS。 */
    @JavascriptInterface
    public String loadWater() {
        return CredStore.loadWater();
    }

    /** "none" / "ok" / "broken"，用来区分「没登录」和「存着但解不开」。 */
    @JavascriptInterface
    public String waterState() {
        return CredStore.waterState();
    }

    @JavascriptInterface
    public void clearWater() {
        CredStore.clearWater();
    }

    // ---- 财务处（校园统一支付平台）----
    // 和慧生活同一个形状：整段 JSON 进、整段 JSON 出，加密交给 CredStore。
    // 存的是学号 + 密码 + 上次查到的欠费 + 选过的房间（密码必须留：会话
    // 是 ASP.NET 的，二十分钟不动就过期，只存 cookie 等于每次都要重输）。

    /** 空串 = 存好了，其余是原因（和 saveCred 一样，界面必须原样说出来）。 */
    @JavascriptInterface
    public String saveCw(String json) {
        return CredStore.saveCw(json);
    }

    @JavascriptInterface
    public String loadCw() {
        return CredStore.loadCw();
    }

    /** "none" / "ok" / "broken"，区分「没登录」和「存着但解不开」。 */
    @JavascriptInterface
    public String cwState() {
        return CredStore.cwState();
    }

    @JavascriptInterface
    public void clearCw() {
        CredStore.clearCw();
    }

    // ---- 学习通（作业待办）----
    // 只有三件事在 Java 层：AES 加密、cookie 导出/还原、cookie 加密存盘。
    // 登录流程、课程/作业解析全在 native.js 里（有 DOMParser，比手写解析稳）。

    /**
     * 学习通登录用的 AES。失败返回空串。
     *
     * 🔴 返回空串必须由调用方当成「登录失败」处理，**不许**拿明文密码顶上去 ——
     *    学习通只认密文，发明文等于拿错的密码去撞，撞多了会触发风控。
     */
    @JavascriptInterface
    public String cxAes(String pwd) {
        try {
            return CxNet.aesB64(pwd == null ? "" : pwd);
        } catch (Throwable t) {
            return "";
        }
    }

    /** 罐子里所有 cookie 导成 JSON（App 退出前存下来，下次不用重登）。 */
    @JavascriptInterface
    public String cxDumpCookies() {
        return CxNet.dumpCookies();
    }

    /** 把 JSON 还原进罐子，返回还原条数。 */
    @JavascriptInterface
    public int cxLoadCookies(String json) {
        return CxNet.loadCookies(json);
    }

    /** 罐子里有没有登录态（看 `.chaoxing.com` 域的 UID）。 */
    @JavascriptInterface
    public boolean cxHasLogin() {
        return CxNet.hasLogin();
    }

    /** 加密存 cookie。空串 = 存好了，"soft" = 走备用方式，其余是失败原因。 */
    @JavascriptInterface
    public String cxSave(String cookiesJson) {
        return CredStore.saveCx(cookiesJson);
    }

    @JavascriptInterface
    public String cxLoad() {
        return CredStore.loadCx();
    }

    @JavascriptInterface
    public void cxClear() {
        CredStore.clearCx();
    }

    /** "none" / "ok" / "broken"。 */
    @JavascriptInterface
    public String cxState() {
        return CredStore.cxState();
    }

    /** 学习通账号密码（加密存本机）。返回值同 savePortalCred：'' / 'soft' / 'fail: 原因'。 */
    @JavascriptInterface
    public String saveCxPwd(String user, String pass) {
        return CredStore.saveCxPwd(user, pass);
    }

    /** 取回学习通账号密码，形如 {"u":"...","p":"..."}；没有或解不开返回空串。 */
    @JavascriptInterface
    public String loadCxPwd() {
        return CredStore.loadCxPwd();
    }

    @JavascriptInterface
    public String cxPwdState() {
        return CredStore.cxPwdState();
    }

    @JavascriptInterface
    public void clearCxPwd() {
        CredStore.clearCxPwd();
    }

    /* ---- 住理生活的账号密码（U79）------------------------------------
     * 住理 session 会过期（真机报 login_timeout）。存一份加密的账号密码，
     * 过期时静默续一次 —— 否则用户每次洗澡前都要重登一遍。
     * 明文密码只在 Keystore 加密后落盘，JS 侧只在续期那一刻取一次。
     */
    @JavascriptInterface
    public String saveZlPwd(String user, String pass) {
        return CredStore.saveZl(user, pass);
    }

    @JavascriptInterface
    public String loadZlPwd() {
        return CredStore.loadZl();
    }

    @JavascriptInterface
    public String zlPwdState() {
        return CredStore.zlPwdState();
    }

    @JavascriptInterface
    public void clearZlPwd() {
        CredStore.clearZl();
    }

    /**
     * 只清学习通那几个域在 **java.net 罐子**里的 cookie。返回清掉的条数。
     *
     * 🔴 不能用 clearCookiesFor：那个一旦清到东西，会把 **WebView 的罐子整个清掉**
     *    （它注释里写明了，是为「断开门户」服务的）。而学习通的请求全走 req2，
     *    用的是 java.net 那个罐子，压根不在 WebView 里。
     *    用它的话，「断开学习通」会顺手把门户的 CASTGC 也清掉 ——
     *    用户只是不想用学习通了，结果办事入口又要重新登录一次。
     */
    @JavascriptInterface
    public int cxClearCookies() {
        try {
            java.net.CookieHandler h = java.net.CookieHandler.getDefault();
            if (!(h instanceof java.net.CookieManager)) return 0;
            java.net.CookieStore store = ((java.net.CookieManager) h).getCookieStore();
            int n = 0;
            // 先复制一份：边遍历边 remove 会抛 ConcurrentModificationException
            for (java.net.URI u
                    : new java.util.ArrayList<java.net.URI>(store.getURIs())) {
                for (java.net.HttpCookie ck
                        : new java.util.ArrayList<java.net.HttpCookie>(store.get(u))) {
                    String dom = ck.getDomain();
                    // endsWith 就够了：`.chaoxing.com` / `passport2.chaoxing.com`
                    // / `mooc1.chaoxing.com` 全都命中
                    if (dom != null && dom.endsWith("chaoxing.com")) {
                        store.remove(u, ck);
                        n++;
                    }
                }
            }
            return n;
        } catch (Throwable t) {
            return 0;
        }
    }

    /**
     * 清掉教务系统的会话 cookie（不动本机存的账号密码）。
     *
     * 为什么需要它：会话还有效的时候去访问登录页，学校服务器会 302 到首页，
     * 返回的 HTML 里**没有加盐值** —— 于是「已经登录过的用户再点刷新」会撞上
     * 「没拿到加盐值」。要重新登录必须先把这个会话丢掉，否则拿不到真正的登录页。
     *
     * 直接操作全局 CookieManager（MainActivity 里 setDefault 的那个），
     * 不用 android.webkit.CookieManager —— 那一个是 WebView 的，跟
     * HttpURLConnection 用的不是同一个罐子。
     */
    @JavascriptInterface
    public boolean clearCookies() {
        try {
            java.net.CookieHandler h = java.net.CookieHandler.getDefault();
            if (h instanceof java.net.CookieManager) {
                ((java.net.CookieManager) h).getCookieStore().removeAll();
                return true;
            }
            return false;
        } catch (Throwable t) {
            return false;
        }
    }

    /**
     * 读某个域下某个 cookie 的值。读不到返回空串。
     *
     * 为什么需要：智慧门户的 JWT（`Authorization`）是服务器用 **Set-Cookie**
     * 下发的，不是放在跳转 URL 的 query 里 —— 所以 JS 那边拿不到，只能由
     * 原生这边从全局 CookieStore 里取。取到之后当**请求头**发给网关
     * （只带 cookie 会 401）。
     *
     * 直接操作全局 CookieManager（MainActivity 里 setDefault 的那个），
     * 理由同 clearCookies()：WebView 的 CookieManager 是另一个罐子。
     */
    @JavascriptInterface
    public String cookie(String url, String name) {
        if (url == null || name == null) return "";
        try {
            java.net.CookieHandler h = java.net.CookieHandler.getDefault();
            if (!(h instanceof java.net.CookieManager)) return "";
            java.util.List<java.net.HttpCookie> list =
                    ((java.net.CookieManager) h).getCookieStore()
                            .get(new java.net.URI(url));
            for (java.net.HttpCookie ck : list) {
                if (name.equals(ck.getName())) {
                    String v = ck.getValue();
                    return v == null ? "" : v;
                }
            }
        } catch (Throwable ignored) {
            // 读不到就当没有：调用方会把它当成「没登录」处理
        }
        return "";
    }

    /**
     * 读 **WebView 罐子**里某个域下某个 cookie 的值。读不到返回空串。
     *
     * ================================================================
     * 为什么必须单独有这么一个方法（2026-10-28 用户报「点校园页功能还要重登」）
     * ================================================================
     * 上面那个 `cookie()` 读的是 `java.net.CookieHandler` —— 那是
     * **HttpURLConnection 的罐子**。WebView 用的是另一个罐子
     * （`android.webkit.CookieManager`），两边**互不相通**。
     *
     * 于是出现一个很隐蔽的死结：
     *   用户在 App 内打开的网页里登录智慧门户（还勾了「7 天免登录」），
     *   CASTGC 落进的是 **WebView 罐**；而 `pfTgt()` 去 Java 罐里找，
     *   永远读到空 ⇒ 判定「本机没有门户凭据」⇒ 不换票 ⇒ 裸开 ⇒ 又落回
     *   CAS 登录页。用户看到的就是「明明登录过了，点功能还要再登一次」。
     *
     * 更坏的一种：WebView 罐里其实有 CASTGC，裸开时 CAS 认出来、发了票，
     * 但 service 串对不上 ⇒ 系统校验失败 ⇒ 再跳 CAS ⇒ 再发票……
     * 这个重定向环会**疯狂跑流量**，而页面一直白着 —— 用户报的第二个问题。
     *
     * 拿到这张 CASTGC 就能走 `POST /v1/tickets/{TGT}` 换票，那条路不经过
     * 任何罐子，两个罐子的分裂就不再是问题。
     */
    @JavascriptInterface
    public String webCookie(String url, String name) {
        if (url == null || url.isEmpty() || name == null || name.isEmpty()) return "";
        // getCookie 返回的是 "a=1; b=2; c=3" 这一整串，不是单条，要自己拆。
        // ⚠️ 值里可能出现 '='（base64 常见），所以只按**第一个** '=' 切。
        String all = webCookiesOnMain(url);
        if (all == null || all.isEmpty()) return "";
        for (String part : all.split(";")) {
            String s = part.trim();
            int eq = s.indexOf('=');
            if (eq <= 0) continue;
            if (name.equals(s.substring(0, eq).trim())) {
                return s.substring(eq + 1).trim();
            }
        }
        return "";
    }

    /**
     * 取 WebView 罐里**所有名字以 `prefix` 开头**的 cookie 值，一行一个。
     *
     * 🔴 为什么不继续用上面那个精确匹配的：CAS 的票 cookie 名字是**服务端实现
     *    细节**。Apereo 原版叫 `CASTGC`，但学校这套 `lyuapServer` 完全可能叫
     *    `CASTGC_xxx`、`CASTGC2`，或者带个环境后缀。精确匹配一旦对不上就返回
     *    空串 —— 而空串的表现和「确实没登录」一模一样，看不出区别。
     *
     * 用换行分隔而不是逗号：cookie 值里可能有逗号（base64 常见）。
     */
    @JavascriptInterface
    public String webCookieLike(String url, String prefix) {
        if (url == null || url.isEmpty()) return "";
        if (prefix == null) prefix = "";
        String all = webCookiesOnMain(url);
        if (all == null || all.isEmpty()) return "";
        StringBuilder sb = new StringBuilder();
        for (String part : all.split(";")) {
            String s = part.trim();
            int eq = s.indexOf('=');
            if (eq <= 0) continue;
            String k = s.substring(0, eq).trim();
            if (!k.startsWith(prefix)) continue;
            if (sb.length() > 0) sb.append('\n');
            sb.append(s.substring(eq + 1).trim());
        }
        return sb.toString();
    }

    /**
     * 取 WebView 罐里某个地址的**整串** cookie（`a=1; b=2` 原样返回）。
     *
     * 给中国大学 MOOC 用：它的 rpc 接口要一串 cookie 一起带，而且必须原样
     * 拼进 `Cookie` 请求头。按名字一条条取需要事先知道名字 —— 而 cookie 名
     * 是服务端实现细节（2026-11-04 实测：登录后写的是 `STUDY_SESS`，
     * 老文档说的 `NTES_SESS` 根本不存在）。名字猜错就静默拿到空串，
     * 表现成「明明登着却说没登录」。
     *
     * 直接拿整串就没有「猜名字」这一步，服务端加一条新 cookie 也不用改代码。
     */
    @JavascriptInterface
    public String webCookieAll(String url) {
        if (url == null || url.isEmpty()) return "";
        String all = webCookiesOnMain(url);
        return all == null ? "" : all;
    }

    /**
     * 在主线程上取 WebView 罐里某个地址的全部 cookie。
     *
     * 🔴 为什么不能在上面那个方法里直接调 `CookieManager.getCookie()`：
     *    CookieManager 的方法**只能在主线程调**，从别的线程调会抛
     *    IllegalStateException。而 `@JavascriptInterface` 跑在 WebView 的
     *    JavaBridge 线程上 —— **一定不是主线程**。
     *
     *    如果这里不调度、只靠外面 `catch (Throwable ignored)` 兜住，结果就是
     *    **永远读到空串**：页面照样落回登录页，日志里一个字都没有，看着跟
     *    「本机确实没凭据」一模一样。这种「静默失效」比直接崩还难查 ——
     *    2026-10-28 修这个问题时差点就这么埋进去。
     */
    private String webCookiesOnMain(final String url) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            try {
                return CookieManager.getInstance().getCookie(url);
            } catch (Throwable t) {
                return "";
            }
        }
        final String[] box = new String[1];
        final CountDownLatch latch = new CountDownLatch(1);
        try {
            new Handler(Looper.getMainLooper()).post(new Runnable() {
                @Override
                public void run() {
                    try {
                        box[0] = CookieManager.getInstance().getCookie(url);
                    } catch (Throwable t) {
                        box[0] = "";
                    } finally {
                        latch.countDown();
                    }
                }
            });
            // 超时就当没读到：调用方会退回「没有凭据」那条路，并如实说明。
            // 不无限等 —— 主线程真卡住时等下去只会让界面更僵。
            latch.await(800, TimeUnit.MILLISECONDS);
        } catch (Throwable ignored) {
        }
        return box[0] == null ? "" : box[0];
    }

    /**
     * 只清掉某个域名下的 cookie（断开门户时用）。
     *
     * 不能用 clearCookies() 代替：那个会把教务系统的会话一起清掉，
     * 用户只是「不想看一卡通了」，结果课表也要重新登录一次，太粗暴。
     */
    @JavascriptInterface
    public boolean clearCookiesFor(String host) {
        if (host == null || host.isEmpty()) return false;
        try {
            java.net.CookieHandler h = java.net.CookieHandler.getDefault();
            if (!(h instanceof java.net.CookieManager)) return false;
            java.net.CookieStore store = ((java.net.CookieManager) h).getCookieStore();
            boolean any = false;
            // 先复制一份 URI 列表：边遍历边 remove 会抛 ConcurrentModificationException
            for (java.net.URI u : new java.util.ArrayList<java.net.URI>(store.getURIs())) {
                for (java.net.HttpCookie ck
                        : new java.util.ArrayList<java.net.HttpCookie>(store.get(u))) {
                    String dom = ck.getDomain();
                    if (dom != null && dom.endsWith(host)) {
                        store.remove(u, ck);
                        any = true;
                    }
                }
            }
            // 🔴 还要清 WebView 那个罐子，否则「断开门户」是假的：
            //    界面上显示「没连门户」，点办事入口却还是登录态 —— 因为
            //    WebActivity 用的 `android.webkit.CookieManager` 会落盘，
            //    跟我们清的这个不是同一个罐子。
            //    WebKit 没有「按域名删」的接口，只能整个清。这里可以接受：
            //    WebView 的罐子里只有学校站点的 cookie（主界面跑在 app.local，
            //    不用 cookie），不会误伤别的。
            if (any) {
                try {
                    android.webkit.CookieManager wc =
                            android.webkit.CookieManager.getInstance();
                    wc.removeAllCookies(null);
                    wc.flush();
                } catch (Throwable ignored) { }
            }
            return any;
        } catch (Throwable t) {
            return false;
        }
    }

    /**
     * 在 App 内打开一个办事系统（带门户 cookie 的 WebView），见 WebActivity。
     *
     * 返回："" = 已经打开了；"fail: xxx" = 起不来。
     * JS 那边收到非空就退回系统浏览器 —— 办事入口是主功能之一，
     * 宁可多一次「要重新登录」，也不能点了没反应。
     *
     * 🔴 为什么不用 openUrl：那个交给**系统浏览器**，浏览器有它自己的
     *    cookie 罐子，门户的 CASTGC（身份证）不在里面，所以每次都要重登。
     *    要在 App 内开，才能把身份证倒进 WebView 的罐子里。
     */
    @JavascriptInterface
    public String openWeb(String url, String title) {
        // 老签名（0.6.0 起）：默认按「这个地址要登录」处理 —— 那时候只有办事入口走它。
        return openWebEx(url, title, true);
    }

    /**
     * 同 openWeb，多一个「要不要提示登录已过期」（0.17.3 起）。
     *
     * `warn = false` 用于**公开页**：公告正文（www / jwc / tuanwei 这些新闻站）、
     * 江财百科（wiki.upstream.fan）。这些页面没有 CASTGC 也照样能看，拿
     * 「门户登录已过期」去提示它们就是**假消息** —— 用户会以为看个公告还得
     * 重连门户，白跑一趟。
     *
     * 🔴 为什么新开一个方法而不是给 openWeb 加第三个参数：老版本 APK 里
     *    `openWeb` 只有两个参数，JS 传三个会匹配不上而抛异常 —— 而
     *    `NativeApi.openWeb` 的调用方会把异常当成「窗口起不来」，
     *    于是静默退回浏览器，**在旧机上表现为「怎么又跳浏览器了」**。
     */
    @JavascriptInterface
    public String openWebEx(String url, String title, boolean warn) {
        if (url == null) return "fail: 没有地址";
        String u = url.trim();
        if (!(u.startsWith("http://") || u.startsWith("https://"))) {
            return "fail: 不是 http/https";
        }
        try {
            Intent it = new Intent(web.getContext(), WebActivity.class);
            it.putExtra(WebActivity.EXTRA_URL, u);
            it.putExtra(WebActivity.EXTRA_TITLE, title == null ? "" : title);
            it.putExtra(WebActivity.EXTRA_WARN, warn);
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            web.getContext().startActivity(it);
            return "";
        } catch (Throwable t) {
            return "fail: " + t;
        }
    }

    // ---------------------------------------------------------------- 外部链接

    /**
     * 把一场考试放进系统日历：拉起日历 App 的「新建事件」界面，内容都填好。
     *
     * 🔴 为什么不自己往日历数据库里写（CalendarContract.Events.insert）：
     *    那要 WRITE_CALENDAR 运行时权限，还得让用户先选「写到哪个日历账户」，
     *    而且写进去就绕过了用户确认 —— 万一我们算错了时间，用户根本不知道
     *    日程是哪来的。用 ACTION_INSERT 是**把内容递给日历 App**，
     *    用户看一眼、点保存才真的写进去。少一个权限，也多一道人工确认。
     *
     * 参数 beginMs/endMs 是 JS 算好的毫秒时间戳（本地时区），
     * allDay 为 true 时按全天事件处理（日历只认日期）。
     *
     * 返回："" = 成功拉起；"fail: xxx" = 打不开（比如没装日历）。
     */
    @JavascriptInterface
    public String addCalendar(String title, String desc, String loc,
                              long beginMs, long endMs, boolean allDay) {
        if (beginMs <= 0) return "fail: 没有开始时间";
        if (endMs <= beginMs) endMs = beginMs + 2 * 60 * 60 * 1000L;
        try {
            Intent it = new Intent(Intent.ACTION_INSERT);
            it.setData(CalendarContract.Events.CONTENT_URI);
            it.putExtra(CalendarContract.Events.TITLE, title == null ? "" : title);
            if (desc != null && !desc.isEmpty()) {
                it.putExtra(CalendarContract.Events.DESCRIPTION, desc);
            }
            if (loc != null && !loc.isEmpty()) {
                it.putExtra(CalendarContract.Events.EVENT_LOCATION, loc);
            }
            it.putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, beginMs);
            it.putExtra(CalendarContract.EXTRA_EVENT_END_TIME, endMs);
            if (allDay) it.putExtra(CalendarContract.EXTRA_EVENT_ALL_DAY, true);
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            web.getContext().startActivity(it);
            return "";
        } catch (Throwable t) {
            // 没装日历 App（国产 ROM 也可能把它藏起来）⇒ 让界面说清楚，别静默失败
            return "fail: " + t;
        }
    }

    /**
     * 把一段文本存成 `.ics`，然后拉起系统的「分享到」，让用户挑日历 App 导入。
     *
     * 为什么不是「一条条加进日历」（见 {@link #addCalendar}）：那是给**单场考试**
     * 用的，一次弹一个确认框才合理。整学期课表是几百个事件，一条条弹等于让
     * 用户把课表手抄一遍。这里一次成文、一次导入。
     *
     * 🔴 为什么不能像网页那样 `<a download>`：APK 的 WebView 没有
     *    DownloadListener，`blob:` 链接点了**什么都不发生** —— 按钮看着像坏了。
     *
     * 🔴 分享走 `ACTION_SEND` 而不是写进日历数据库：写数据库要 WRITE_CALENDAR
     *    运行时权限，还得让用户选日历账户；递文件给日历 App，导入哪本、要不要
     *    导入都由用户自己决定。
     *
     * 返回："" = 已拉起分享；"fail: xxx" = 失败（界面上如实说）。
     */
    @JavascriptInterface
    public String shareFile(String name, String text, String mime) {
        if (text == null || text.isEmpty()) return "fail: 没有内容";
        // 一份课表几百个事件约 200KB，留到 4MB 已经很宽松。
        // 卡这个上限是防「界面算错了、把几兆字符串递进来」，不是防正常使用。
        if (text.length() > 4 * 1024 * 1024) return "fail: 内容太大";
        String n = (name == null ? "" : name.trim());
        if (!n.endsWith(".ics")) n = n + ".ics";
        // 和 ShareProvider 的白名单保持一致：那边放不行的名字，这里就别写了，
        // 否则文件落地了却分享不出去（用户看到的是「分享失败」）。
        if (!n.matches("[\\w.\\-\\u4e00-\\u9fa5]{1,80}")) return "fail: 文件名不合适";
        try {
            Context ctx = web.getContext();
            File f = new File(ShareProvider.dir(ctx), n);
            OutputStream os = new FileOutputStream(f);
            try {
                os.write(text.getBytes(StandardCharsets.UTF_8));
            } finally {
                os.close();
            }
            Uri u = ShareProvider.uriFor(n);
            Intent send = new Intent(Intent.ACTION_SEND);
            send.setType(MIME_ICS);
            send.putExtra(Intent.EXTRA_SUBJECT, n);
            send.putExtra(Intent.EXTRA_STREAM, u);
            send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            // 用 chooser 而不是直接 startActivity：有的 ROM 没给 .ics 设默认应用，
            // 直接发会抛 ActivityNotFoundException；给个选择框，用户自己挑。
            Intent it = Intent.createChooser(send, "导入课表");
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            // 🔴 读权限必须加在 **chooser** 上，只加在里面那个 send 上时，
            //    部分系统版本不会把授权传给被选中的 App —— 对方打开就是空文件。
            it.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            ctx.startActivity(it);
            return "";
        } catch (Throwable t) {
            return "fail: " + t;
        }
    }

    /**
     * 用系统浏览器打开一个链接（百科文档、学习通、公告附件都走这里）。
     *
     * 🔴 为什么不在 WebView 里直接跳：WebView 默认没有 DownloadListener，
     *    点一个 .apk 链接**什么都不会发生** —— 用户会以为按钮坏了。
     *    交给系统浏览器，下载、装包、未知来源授权都由系统自己管。
     *
     * 只放行 http/https：别的 scheme（file:、intent:、javascript:）一律不接，
     * 免得以后哪份版本清单被人改了，变成一个能执行任意 Intent 的后门。
     */
    @JavascriptInterface
    public void openUrl(final String url) {
        if (url == null) return;
        String u = url.trim();
        if (!(u.startsWith("http://") || u.startsWith("https://"))) return;
        try {
            Intent it = new Intent(Intent.ACTION_VIEW, Uri.parse(u));
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            web.getContext().startActivity(it);
        } catch (Throwable t) {
            // 没有浏览器 / 被系统拦了：静默失败。界面那边本来就给了下载页兜底，
            // 这里再弹一个 Java 异常框只会更糟。
        }
    }

    /**
     * 允许拉起的其它 App。**必须是白名单**。
     *
     * 🔴 不能「传什么包名就开什么」：那样一段被改过的网页就能让 App 去拉起
     *    任意应用（系统设置、安装器、带 intent: 的深链），等于在桥里焊了个后门。
     *    这份名单和 AndroidManifest 的 &lt;queries&gt; 是同一份，加包名**两边一起改** ——
     *    只改一边的表现是「明明装了却说没装」或「点了没反应」，都不报错。
     */
    private static final String[] ALLOWED_APPS = {
            "com.cloudora.android",   // 慧生活798 官方（宿舍接水）
            "com.water.widget",       // WaterWidget 第三方客户端（MIT）
            // 钉钉（0.52.1 v12）。学校在钉钉里发通知，酱菜通这边只做两件事：
            // 判断装没装、一键拉起来。**读不到里面任何数据** —— 那部分靠通知
            // 监听（见 Ding / DingListener），是另一条完全独立的路径。
            // 专有版（`rimet.zzhy` 之类）不在这个数组里，由 allowedApp 末尾那条
            // `Ding.isDing` 兜住 —— 名单只留「要人看一眼」的第三方 App。
            "com.alibaba.android.rimet",
            "com.alibaba.work.android",
            // 完美校园（0.52.1 v15）。校园卡账单/充值都走它，我们只做两件事：
            // 判断装没装、一键拉起来。**读不到里面任何数据** —— 通知那条路见
            // Ding / DingListener，查余额那条路见 WxCard（要用户自己的账号）。
            "com.newcapec.mobile.ncp",
            // 微信（U90）：智慧江财的电费/网费充值在微信小程序里。这里**只拉起
            // 微信**，不读它的数据、不模拟支付；付款由用户在官方小程序完成。
            "com.tencent.mm",
            // QQ（2026-11-25 关于页）：「加入群」那颗按钮只负责拉起 QQ，
            // 群号由界面复制到剪贴板、用户自己搜索加入 —— 不读 QQ 任何数据。
            "com.tencent.mobileqq"
    };

    /**
     * 快递取件快捷跳转允许的包名 + scheme（2026-11-24 U75）。
     *
     * 这份名单照搬 QuickJump（github.com/chuhe0719/QuickJump，纯本地、零权限）。
     * 它靠的是目标 App **公开的 deeplink**（ACTION_VIEW + setPackage 限定包名），
     * 不是私有 API，也不需要任何权限 —— 我们只做同一件事。
     *
     * 🔴 为什么要走原生、不在网页里直接跳：WebView 里 `location = "taobao://…"`
     *    会被当成未知协议**静默丢掉**（Chrome 只认 http/https/javascript），
     *    用户点下去一点反应都没有。只有 ACTION_VIEW 才真的能拉起别的 App。
     * 🔴 为什么 scheme 也要白名单：只认包名不够 —— `intent:` / `market:` 之类的
     *    构造能被拼成任意跳转。两边都拦，才只剩「打开那五个快递 App」这一种可能。
     */
    private static final String[] JUMP_PKGS = {
            "com.cainiao.wireless",        // 菜鸟
            "com.taobao.taobao",           // 淘宝
            "com.xunmeng.pinduoduo",       // 拼多多
            "com.ss.android.ugc.aweme",    // 抖音
            "com.ss.android.ugc.aweme.lite", // 抖音极速版
            "com.jingdong.app.mall"        // 京东
    };

    private static final String[] JUMP_SCHEMES = {
            "cainiao", "taobao", "tbopen", "pinduoduo",
            "snssdk1128", "snssdk2329", "openjd"
    };

    private static boolean jumpPkgOk(String pkg) {
        if (pkg == null) return false;
        for (String p : JUMP_PKGS) {
            if (p.equals(pkg)) return true;
        }
        return false;
    }

    private static boolean jumpSchemeOk(String uri) {
        if (uri == null) return false;
        int i = uri.indexOf(':');
        if (i <= 0) return false;
        String s = uri.substring(0, i).toLowerCase();
        for (String p : JUMP_SCHEMES) {
            if (p.equals(s)) return true;
        }
        return false;
    }

    /**
     * 按 deeplink 拉起目标 App 的某个页面。返回
     * `"ok"` / `"deny"`（包名或 scheme 不在白名单）/ `"no"`（打不开：没装、链接失效）。
     *
     * 🔴 失败**不抛异常也不弹框**：调用方（app.js 的 jumpGo）自己还有网页兜底，
     *    而且一条链上要连着试好几条链接 —— 中间弹一次框就等于打断整条链。
     */
    @JavascriptInterface
    public String openJump(final String pkg, final String uri) {
        if (!jumpPkgOk(pkg) || !jumpSchemeOk(uri)) return "deny";
        try {
            Intent it = new Intent(Intent.ACTION_VIEW, Uri.parse(uri));
            it.setPackage(pkg);
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_CLEAR_TOP
                    | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            web.getContext().startActivity(it);
            return "ok";
        } catch (ActivityNotFoundException e) {
            return "no";
        } catch (SecurityException e) {
            return "no";
        } catch (IllegalArgumentException e) {
            return "no";
        } catch (Throwable t) {
            return "no";
        }
    }

    private static boolean allowedApp(String pkg) {
        if (pkg == null) return false;
        for (String p : ALLOWED_APPS) {
            if (p.equals(pkg)) return true;
        }
        // 快递那五个 App：`hasApp` 要能查、`openApp` 要能兜底拉起来
        // （deeplink 失效时退成「打开 App 自己翻」）。
        if (jumpPkgOk(pkg)) return true;
        // 钉钉的包名跟 {@link Ding} 走同一份判断，不在这里再抄一遍名单：
        // 专有版是 `rimet.zzhy` 这种带后缀的，学校换一版就换个后缀 ——
        // 抄两份的结果是「能收通知，但说没装、点了没反应」。
        return Ding.isDing(pkg);
    }

    /**
     * 这个 App 装了没有。返回 "1" / "0"。
     *
     * 🔴 返回字符串、不返回 boolean：JS 那边要能分清「没装」和「这个壳根本没有
     *    这个接口」（老 APK / 网页版）。返回 boolean 的话两种都是 undefined，
     *    调用方只能猜，而猜错的代价是「装了却让人去下载」。
     */
    @JavascriptInterface
    public String hasApp(final String pkg) {
        if (!allowedApp(pkg)) return "0";
        try {
            Intent it = web.getContext().getPackageManager()
                    .getLaunchIntentForPackage(pkg);
            return it == null ? "0" : "1";
        } catch (Throwable t) {
            return "0";
        }
    }

    /**
     * 拉起另一个 App。返回 "ok" / "deny" / "notinstalled" / "fail: …"。
     *
     * 为什么不开一个新的 Activity 中转：切过去之后我们这边就退到后台了，
     * 用 FLAG_ACTIVITY_NEW_TASK 直接起对方的启动页，返回键自然回到我们这里。
     */
    @JavascriptInterface
    public String openApp(final String pkg) {
        if (!allowedApp(pkg)) return "deny";
        try {
            Intent it = web.getContext().getPackageManager()
                    .getLaunchIntentForPackage(pkg);
            if (it == null) return "notinstalled";
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            web.getContext().startActivity(it);
            return "ok";
        } catch (Throwable t) {
            return "fail: " + t;
        }
    }

    // ---------------------------------------------------------- 住理热水蓝牙（1.3.15）

    /** 懒创建：没开过热水的人不会因为这一行就去碰蓝牙适配器。 */
    private ZhuliBle zlBle;

    private ZhuliBle ble() {
        if (zlBle == null) zlBle = new ZhuliBle(web);
        return zlBle;
    }

    /**
     * 蓝牙权限的状态：`"1"` 有、`"0"` 没有、`"none"` 这台机器没有蓝牙。
     *
     * 🔴 必须能分清「没有蓝牙」和「没授权」：前者要让人换设备，
     *    后者只要弹一次授权框 —— 混成一个 0 的话，用户会对着授权框发呆。
     */
    @JavascriptInterface
    public String zlBlePerm() {
        try {
            return ZhuliBle.permState(web.getContext());
        } catch (Throwable t) {
            return "none";
        }
    }

    /** 弹系统授权框。返回 "granted" / "asked" / "none" / "fail"。 */
    @JavascriptInterface
    public String zlBleAskPerm() {
        try {
            return ZhuliBle.askPerm(web.getContext());
        } catch (Throwable t) {
            return "fail";
        }
    }

    /** 扫到并连上水表（结果从 window.__zlBle 回）。返回 "ok" / 错误码。 */
    @JavascriptInterface
    public String zlBleOpen(final String req, final String name, final String mac) {
        try {
            return ble().open(req, name, mac);
        } catch (Throwable t) {
            return "fail";
        }
    }

    /** 写一条 hex 并等回帧。返回 "ok" / "notready" / "badhex" … */
    @JavascriptInterface
    public String zlBleWrite(final String req, final String hex, final String types) {
        try {
            return ble().write(req, hex, types);
        } catch (Throwable t) {
            return "writefail";
        }
    }

    /** 断开。 */
    @JavascriptInterface
    public String zlBleClose(final String req) {
        try {
            return ble().close(req, true);
        } catch (Throwable t) {
            return "fail";
        }
    }

    // ---------------------------------------------------------- 钉钉通知（v12）

    /**
     * 钉钉通知的状态：`{granted, n}`。
     *
     * 🔴 授权与否**每次现查系统设置**，本地不存标记。存了会出事：用户在系统里
     *    收回权限、或者换台手机恢复备份，界面会一直显示"已开启"，
     *    而实际一条都收不到 —— 又是一个「看着都正常、就是不工作」。
     */
    @JavascriptInterface
    public String dingState() {
        try {
            return Ding.state(web.getContext());
        } catch (Throwable t) {
            return "{\"granted\":false,\"n\":0}";
        }
    }

    /** 已收到的钉钉通知（JSON 数组，最新在前）。没授权时是 "[]"。 */
    @JavascriptInterface
    public String dingList() {
        try {
            return Ding.list(web.getContext());
        } catch (Throwable t) {
            return "[]";
        }
    }

    /** 清空。用户自己点的，不弹确认（列表里就几十条，看一眼就知道清的是啥）。 */
    @JavascriptInterface
    public void dingClear() {
        try {
            Ding.clear(web.getContext());
        } catch (Throwable t) {
            // 清不掉不是错误。
        }
    }

    /**
     * 跳到系统的「通知使用权」列表页。
     *
     * 为什么不交给网页的 `openUrl`：那是一条 `android.settings.*` 的 action，
     * 不是 http(s)，而 `openUrl` 只放行 http/https（这是对的 —— 桥里不该让
     * 网页随便起 Intent）。所以单独开这一个口子，而且它只能跳那一页。
     */
    @JavascriptInterface
    public boolean dingGrant() {
        try {
            return Ding.openSettings(web.getContext());
        } catch (Throwable t) {
            return false;
        }
    }

    // ------------------------------------------------ 完美校园账号（0.52.1 v15）

    /** {on, name, stuNo, school, phone4, at}。见 {@link WxCard#state}。 */
    @JavascriptInterface
    public String wxState() {
        try {
            return WxCard.state(web.getContext());
        } catch (Throwable t) {
            return "{\"on\":false}";
        }
    }

    /**
     * 登录完美校园。**异步**：结果回给 `window.__wxDone(id, json)`。
     *
     * 为什么不用返回值：这是三次网络往返（换密钥 → 登录 → 取身份），
     * 同步做会把界面冻住两三秒，用户会以为按钮没反应，然后连点五次 ——
     * 完美校园有风控，连点是最容易被锁号的姿势。
     *
     * 🔴 密码**不落盘**。存的是 token；token 过期就再登一次。
     *    少存一份密码，就少一次「拿旧密码反复撞」。
     */
    @JavascriptInterface
    public void wxLogin(final int id, final String phone, final String pwd) {
        pool.execute(new Runnable() {
            @Override
            public void run() {
                String json;
                try {
                    json = WxCard.login(web.getContext(), phone, pwd);
                } catch (Throwable t) {
                    json = "{\"ok\":false,\"msg\":\"登录没成功\"}";
                }
                final String out = json;
                web.post(new Runnable() {
                    @Override
                    public void run() {
                        web.evaluateJavascript(
                                "window.__wxDone(" + id + "," + JSONObject.quote(out) + ")", null);
                    }
                });
            }
        });
    }

    /**
     * 发一条短信验证码。新设备第一次登录要用（见 {@link WxCard#sendSms}）。
     *
     * 同样的异步形状：换密钥 + 发码是两次往返，同步做界面会僵一下。
     */
    @JavascriptInterface
    public void wxSendSms(final int id, final String phone) {
        pool.execute(new Runnable() {
            @Override
            public void run() {
                String json;
                try {
                    json = WxCard.sendSms(web.getContext(), phone);
                } catch (Throwable t) {
                    json = "{\"ok\":false,\"msg\":\"验证码没发出去\"}";
                }
                final String out = json;
                web.post(new Runnable() {
                    @Override
                    public void run() {
                        web.evaluateJavascript(
                                "window.__wxDone(" + id + "," + JSONObject.quote(out) + ")", null);
                    }
                });
            }
        });
    }

    /** 用短信验证码登录。结果和密码登录回同一条通道。 */
    @JavascriptInterface
    public void wxLoginSms(final int id, final String phone, final String code) {
        pool.execute(new Runnable() {
            @Override
            public void run() {
                String json;
                try {
                    json = WxCard.loginSms(web.getContext(), phone, code);
                } catch (Throwable t) {
                    json = "{\"ok\":false,\"msg\":\"登录没成功\"}";
                }
                final String out = json;
                web.post(new Runnable() {
                    @Override
                    public void run() {
                        web.evaluateJavascript(
                                "window.__wxDone(" + id + "," + JSONObject.quote(out) + ")", null);
                    }
                });
            }
        });
    }

    /**
     * 查一卡通余额（见 {@link WxCard#cardInfo}）。
     *
     * 一次网络往返，照样丢给 pool —— 校园网慢的时候同步做会把界面定住。
     */
    @JavascriptInterface
    public void wxCard(final int id) {
        pool.execute(new Runnable() {
            @Override
            public void run() {
                String json;
                try {
                    json = WxCard.cardInfo(web.getContext());
                } catch (Throwable t) {
                    json = "{\"ok\":false,\"msg\":\"余额没查到\"}";
                }
                final String out = json;
                web.post(new Runnable() {
                    @Override
                    public void run() {
                        web.evaluateJavascript(
                                "window.__wxDone(" + id + "," + JSONObject.quote(out) + ")", null);
                    }
                });
            }
        });
    }

    @JavascriptInterface
    public void wxLogout() {
        try {
            WxCard.logout(web.getContext());
        } catch (Throwable t) {
            // 清不掉不是错误。
        }
    }

    // ------------------------------------------------------------ 权限自检

    /**
     * 「这台手机上，酱菜通要的几样东西开了没有」。2026-11-15 加。
     *
     * 用户 2026-11-15：「在我的界面加一个能看酱菜通需要的权限是否都打开的界面，
     * 方便查看」。他要的不是一个申请权限的向导 —— 权限早就申请过了，
     * 问题在于**有的机型装完就被省电策略关掉**，而 App 里没有任何地方能看出来。
     *
     * 返回 JSON（值一律是布尔，查不出来就是 false，不编造「大概开了」）：
     *   notify   通知权限（24 起可以问）
     *   alarm    精确闹钟（31 起才有这个开关；更老的系统恒 true）
     *   battery  已加入「不受省电限制」白名单（我们在里面 = true）
     *   job      后台任务真的排上了（JobScheduler 里还有我们的一单）
     *
     * 🔴 `job` 单独一项是因为它和上面三条**不是一回事**：权限全开、开关没开，
     *    后台照样一次不跑。这一项直接问系统，比看我们的配置文件可信。
     * 🔴 自启动查不到（没有公开 API，厂商各写各的），这一项由 JS 侧
     *    按机型给一句提示 + 一个按钮，不在这里编一个真假。
     */
    @JavascriptInterface
    public String permState() {
        JSONObject o = new JSONObject();
        Context c = web.getContext();
        try {
            boolean notify = true;
            try {
                NotificationManager nm = (NotificationManager)
                        c.getSystemService(Context.NOTIFICATION_SERVICE);
                if (nm != null && Build.VERSION.SDK_INT >= 24) {
                    notify = nm.areNotificationsEnabled();
                }
            } catch (Throwable ignored) { }
            o.put("notify", notify);

            boolean alarm = true;
            boolean alarmKnown = false;
            if (Build.VERSION.SDK_INT >= 31) {
                alarmKnown = true;
                try {
                    AlarmManager am = (AlarmManager)
                            c.getSystemService(Context.ALARM_SERVICE);
                    alarm = am == null || am.canScheduleExactAlarms();
                } catch (Throwable t) {
                    alarm = false;
                }
            }
            o.put("alarm", alarm);
            o.put("alarmKnown", alarmKnown);

            boolean battery = false;
            try {
                PowerManager pm = (PowerManager) c.getSystemService(Context.POWER_SERVICE);
                battery = pm != null
                        && pm.isIgnoringBatteryOptimizations(c.getPackageName());
            } catch (Throwable ignored) { }
            o.put("battery", battery);

            boolean jobCx = false, jobWt = false;
            try {
                // 🔴 常量叫 JOB_SCHEDULER_SERVICE，不是 JOB_SERVICE —— 后者不存在，
                //    写错是**编译期**报错（这一版第一遍打包就卡在这儿）。
                //    getPendingJob 是 API 24 才有的，minSdk 是 23：低版本上
                //    走到这里会 NoSuchMethodError，它是 Error 不是 Exception，
                //    所以外面那层 catch 收的是 Throwable，而这里再加一道版本闸。
                JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
                if (js != null && Build.VERSION.SDK_INT >= 24) {
                    jobCx = js.getPendingJob(Auto.JOB_CX) != null
                            || js.getPendingJob(Auto.JOB_CX_NOW) != null;
                    jobWt = js.getPendingJob(Auto.JOB_WT) != null
                            || js.getPendingJob(Auto.JOB_WT_NOW) != null;
                }
            } catch (Throwable ignored) { }
            o.put("jobCx", jobCx);
            o.put("jobWt", jobWt);
            o.put("job", jobCx || jobWt);
        } catch (Throwable t) {
            // 一项都查不出来也不能让这一页炸掉：界面会显示「读不到」。
        }
        return o.toString();
    }

    /**
     * 把用户送到能改这一项的那一页。`what` = notify / alarm / battery / auto。
     *
     * 返回：'' = 跳过去了；其余 = 一句话（跳到系统设置首页也算成功，
     * 但要说清「到了之后点什么」—— 见 PowerJump 的注释）。
     *
     * 🔴 只跳转，**不替用户改**。没有任何公开 API 能关掉省电限制或打开自启动，
     *    偷偷绕过去的做法不能用在要上架的 App 里。
     */
    @JavascriptInterface
    public String permOpen(final String what) {
        Context c = web.getContext();
        String w = what == null ? "" : what.trim();
        try {
            if ("battery".equals(w) || "auto".equals(w)) {
                // 厂商那一页（按品牌挑），失败才退回应用信息页 —— 逻辑都在 PowerJump。
                return PowerJump.open(c);
            }
            if ("notify".equals(w)) {
                if (Build.VERSION.SDK_INT >= 26) {
                    Intent i = new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, c.getPackageName());
                    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    c.startActivity(i);
                    return "";
                }
                Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                        .setData(Uri.parse("package:" + c.getPackageName()));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                c.startActivity(i);
                return "找到「通知」打开它";
            }
            if ("alarm".equals(w)) {
                if (Build.VERSION.SDK_INT >= 31) {
                    Intent i = new Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM)
                            .setData(Uri.parse("package:" + c.getPackageName()));
                    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    c.startActivity(i);
                    return "";
                }
                return "这个系统不需要单独开";
            }
        } catch (Throwable t) {
            return "打不开系统设置，可以在手机设置里找「应用 → 酱菜通」";
        }
        return "没有这一项";
    }

    /* ---- 快递取件码（2026-11-24 U78）----------------------------------
     * 数据来自通知监听（DingListener → Parcels），这里只做「读本机缓存」。
     * 全部同步返回：SharedPreferences 里就十几条，比走一次异步回调简单。
     */
    @JavascriptInterface
    public String parcels() {
        try { return Parcels.dump(web.getContext()); } catch (Throwable t) { return "[]"; }
    }

    @JavascriptInterface
    public String parcelsPerm() {
        try { return Parcels.hasPerm(web.getContext()) ? "1" : "0"; } catch (Throwable t) { return "0"; }
    }

    @JavascriptInterface
    public void parcelsAsk() {
        try { Parcels.ask(web.getContext()); } catch (Throwable ignored) { }
    }

    @JavascriptInterface
    public void parcelsClear() {
        try { Parcels.clear(web.getContext()); } catch (Throwable ignored) { }
    }

    /** 让监听服务立刻扫一遍通知栏里还挂着的通知（授权后第一次进页面用）。 */
    @JavascriptInterface
    public void parcelsScan() {
        try { DingListener.scanNow(); } catch (Throwable ignored) { }
    }
}
