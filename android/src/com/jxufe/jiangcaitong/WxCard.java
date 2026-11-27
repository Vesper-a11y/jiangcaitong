package com.jxufe.jiangcaitong;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.interfaces.RSAPrivateKey;
import java.security.interfaces.RSAPublicKey;

import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * 完美校园（17wanxiao）账号登录（2026-11-13，0.52.1 v15）。
 *
 * 这是用户**自己的手机号 + 密码**，登他自己的账号，看他自己的一卡通信息。
 * 凭据只在本机 Keystore 里，不上传、不转发、没有第二个出口。
 *
 * ---- 协议从哪来的 ----
 * 完美校园没有面向个人的公开接口（官方那条「AI 一卡通 MCP」要用户自己去第三方
 * 平台配 Key，塞不进 App）。这套是它 Android 客户端在用的：
 *
 *   ① RSA-1024 生成密钥对，公钥（**SPKI / X.509**，就是 `getEncoded()` 的原始
 *      字节，不要手拼）交给 `cam_iface46/exchangeSecretkey.action`，拿回 {session, key}
 *   ② 业务报文 == 3DES-EDE3-CBC（key = app_key 前 24 个字符，IV 固定 "66666666"，
 *      PKCS5 填充）→ base64，外层包成 {"session":..,"data":..}
 *   ③ 外层那串 JSON 的 **sha256（小写 hex）** 放进请求头 `campusSign`。
 *      少了它服务端直接回「缺少签名数据」（code 800111016）。
 *   ④ 密码是**逐字符**加密的：一个字符单独 3DES 一次，得到一个 base64 字符串，
 *      密码有几个字符就给几个元素 —— 不是整串一次性加密。
 *
 * ⚠️ 2026-11-13 实测（同一把密钥，只换编码方式）：
 *    SPKI 正常，**PKCS#1 回 200 + 空 body**，不报错、不看长度根本发现不了。
 *    之前这里写成了反过来的一句话（「必须 PKCS#1」），于是 v16 每次登录都
 *    卡在换密钥这一步，界面显示「那边回了个空的」。
 *
 * ---- 拿不到什么 ----
 * 余额、交易明细的接口名靠猜找不到（扫了 96 个候选全 404，App 有加固）。
 * 所以这里**只做登录和身份信息**；校园卡的账目走通知那条路（见 {@link Ding}）：
 * 完美校园自己的「消费通知」里就带金额和余额，比逆向可靠得多。
 *
 * 🔴 失败时**不重试**、不换密码反复撞：完美校园有风控，撞多了锁号。
 */
final class WxCard {

    private static final String HOST = "https://app.17wanxiao.com/campus/cam_iface46/";
    /** 一卡通账目那个口（余额 / 流水）。和登录不是一个域名、不是一套参数。 */
    private static final String YKT = "https://server.17wanxiao.com/YKT_Interface/xyk";
    /** 服务端认这个 UA（Dalvik 串，不是浏览器串）。换掉多半也能过，但没必要试。 */
    private static final String UA =
            "Dalvik/2.1.0 (Linux; U; Android 5.1.1; HUAWEI MLA-AL10 Build/HUAWEIMLA-AL10)";
    private static final byte[] IV = "66666666".getBytes();
    /** 登录成功后拿身份信息用。和登录不是同一个域名。 */
    private static final String URL_USER_INFO =
            "https://reportedh5.17wanxiao.com/api/clock/school/getUserInfo";

    private static final String PREF = "hyt_wxcard";

    /** 最近一次请求的 HTTP 状态（0 = 连都没连上）。只给诊断用。 */
    private static volatile int lastHttp = 0;
    /** 回包里出现了 `<html` —— 十有八九是校园网 portal 把请求接走了。 */
    private static volatile boolean lastHtml = false;

    private WxCard() {
    }

    // ------------------------------------------------------------------ 对外

    /** 现在登着没有：{on, name, stuNo, school, className, phone4, at}。 */
    static String state(Context c) {
        try {
            JSONObject o = new JSONObject();
            String raw = c.getSharedPreferences(PREF, Context.MODE_PRIVATE)
                    .getString("info", "");
            if (raw == null || raw.isEmpty()) {
                o.put("on", false);
                return o.toString();
            }
            JSONObject d = new JSONObject(raw);
            o.put("on", !d.optString("token").isEmpty());
            o.put("name", d.optString("name"));
            o.put("stuNo", d.optString("stuNo"));
            o.put("school", d.optString("school"));
            o.put("className", d.optString("className"));
            o.put("phone4", d.optString("phone4"));
            o.put("at", d.optLong("at"));
            o.put("last", lastText(c));
            return o.toString();
        } catch (Throwable t) {
            return "{\"on\":false}";
        }
    }

    /** 上一次没成功是卡在哪一步。给用户看一句人话，不含账号、不含密码。 */
    private static String lastText(Context c) {
        try {
            String raw = c.getSharedPreferences(PREF, Context.MODE_PRIVATE)
                    .getString("last", "");
            int p = raw.indexOf('|');
            if (p <= 0) return "";
            long ts = Long.parseLong(raw.substring(p + 1));
            // 24 小时前的失败不再提：那和现在这次多半没关系，摆出来只会误导。
            if (System.currentTimeMillis() - ts > 24L * 3600 * 1000) return "";
            String when = android.text.format.DateFormat
                    .format("MM-dd HH:mm", ts).toString();
            return stageText(raw.substring(0, p)) + "（" + when + "）";
        } catch (Throwable t) {
            return "";
        }
    }

    /**
     * 阶段码 → 人话。
     *
     * 🔴 这几句必须分得开：用户下一步要做的事完全不同。
     *    「网络没通」= 去看网络；「被拦」= 校园网要先登录；
     *    「账号被拒」= 改密码/换手机号；「读不懂」= 等我们更新。
     *    合成一句「登录失败」，他只能一遍遍重试同一个错。
     */
    private static String stageText(String stage) {
        if (stage == null) return "";
        if (stage.startsWith("net")) return "网络没通";
        if (stage.startsWith("blocked")) return "网络被拦了（校园网可能要先登录）";
        if (stage.startsWith("secret")) return "没拿到会话";
        if (stage.startsWith("refuse")) return "完美校园那边没认";
        if (stage.startsWith("parse")) return "返回的内容读不懂";
        return "";
    }

    private static void note(Context c, String stage) {
        try {
            c.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit()
                    .putString("last", stage + "|" + System.currentTimeMillis()).apply();
        } catch (Throwable t) {
            // 记不下来不影响登录本身。
        }
    }

    static void logout(Context c) {
        try {
            c.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit().remove("info").apply();
        } catch (Throwable t) {
            // 删不掉不是错误，下次 save 会覆盖。
        }
    }

    /**
     * 密码登录。返回三种形状之一：
     *   `{ok:true, name, stuNo, school}` 登上了
     *   `{ok:false, needSms:true, msg, phone4}` 要短信验证码（见下）
     *   `{ok:false, msg}` 别的失败
     *
     * 🔴 needSms 不是「密码错」。完美校园认**设备**（deviceId）：这台手机
     *    第一次登录时它一律要短信验证码，服务端原话是「您正在新设备上使用
     *    完美校园，请使用验证码进行验证登录」。以前我们每次都拿一个新的随机
     *    UUID 当 deviceId —— 于是**每一次都是新设备**，密码再对也永远过不去。
     *    2026-11-13 用真账号实测撞出这句话，才把这条补上（见 deviceId）。
     *
     * 🔴 这个方法会阻塞（三次网络往返），只许在后台线程调 —— 见
     *    NativeBridge.wxLogin 里的 pool。
     */
    static String login(Context c, String phone, String pwd) {
        if (phone == null || phone.trim().isEmpty()) return err("先填手机号");
        if (pwd == null || pwd.isEmpty()) return err("先填密码");
        phone = phone.trim();
        try {
            JSONObject s = secret(c);
            String appKey = s.optString("key");

            // 逐**字符**加密密码。
            //
            // 🔴 是字符，不是字节。以前按 UTF-8 字节遍历：纯数字字母的密码
            //    恰好一字符一字节，看不出问题；密码里有一个汉字/全角符号
            //    就会拆成三个元素，元素个数和长度都对不上，服务端只会回
            //    「手机号或密码错误」—— 用户会一遍遍去改那本来没错的密码。
            JSONArray plist = new JSONArray();
            for (int i = 0; i < pwd.length(); ) {
                int cp = pwd.codePointAt(i);
                plist.put(b64(des3(new String(Character.toChars(cp)).getBytes("UTF-8"), appKey)));
                i += Character.charCount(cp);
            }

            JSONObject inner = base(c);
            inner.put("password", plist);
            inner.put("type", "1");
            inner.put("userName", phone);
            inner.put("requestMethod", "cam_iface46/loginnew.action");

            JSONObject r = call(c, s, "loginnew.action", inner);
            String code = String.valueOf(r.opt("code_"));
            if (!"0".equals(code)) {
                String msg = r.optString("message_");
                if (isNewDevice(msg)) {
                    // 不是密码错：这台手机它没见过。照实说，并让网页去要验证码。
                    note(c, "sms:" + code);
                    return needSms(msg, phone);
                }
                // 密码错、账号不存在、风控 —— 一律照服务端原话转给用户，
                // 不自己编原因（编错了用户会去改一个本来没错的东西）。
                note(c, "refuse:" + code);
                return err(msg.isEmpty() ? ("登录失败（" + code + "）") : msg);
            }
            return save(c, s, r, phone);
        } catch (Stop e) {
            return err(e.getMessage());
        } catch (Throwable t) {
            return broke(c, t);
        }
    }

    /**
     * 发一条短信验证码。新设备第一次登录必须走这一步。
     *
     * 协议（2026-11-13 实测两个接口都还在）：
     *   POST cam_iface46/gainMatrixCaptcha.action
     *   {action:"registAndLogin", deviceId, mobile, type:"sms", requestMethod:...}
     * 回 `{"code_":0,"result_":true,"message_":"聚合验证码发送成功。"}`
     *
     * 🔴 一天只能发几条（服务端会限），所以界面那边 60 秒内只许点一次，
     *    按钮要真的禁用 —— 连点换来的不是更快，是「今天没得发了」。
     */
    static String sendSms(Context c, String phone) {
        if (phone == null || phone.trim().isEmpty()) return err("先填手机号");
        phone = phone.trim();
        try {
            JSONObject s = secret(c);
            JSONObject inner = base(c);
            inner.put("action", "registAndLogin");
            inner.put("mobile", phone);
            inner.put("type", "sms");
            inner.put("requestMethod", "cam_iface46/gainMatrixCaptcha.action");
            JSONObject r = call(c, s, "gainMatrixCaptcha.action", inner);
            if (!"0".equals(String.valueOf(r.opt("code_")))) {
                String m = r.optString("message_");
                note(c, "refuse:" + r.opt("code_"));
                return err(m.isEmpty() ? "验证码未发出，请稍后再试" : m);
            }
            return "{\"ok\":true}";
        } catch (Stop e) {
            return err(e.getMessage());
        } catch (Throwable t) {
            return broke(c, t);
        }
    }

    /** 用短信验证码登录。成功后的处理和密码登录**完全一样**（同一个 save）。 */
    static String loginSms(Context c, String phone, String sms) {
        if (phone == null || phone.trim().isEmpty()) return err("先填手机号");
        if (sms == null || sms.trim().isEmpty()) return err("先填验证码");
        phone = phone.trim();
        sms = sms.trim();
        try {
            JSONObject s = secret(c);
            JSONObject inner = base(c);
            inner.put("sms", sms);
            inner.put("mobile", phone);
            inner.put("requestMethod", "cam_iface46/registerUsersByTelAndLoginNew.action");
            JSONObject r = call(c, s, "registerUsersByTelAndLoginNew.action", inner);
            if (!"0".equals(String.valueOf(r.opt("code_")))) {
                String m = r.optString("message_");
                note(c, "refuse:" + r.opt("code_"));
                return err(m.isEmpty() ? "验证码不对" : m);
            }
            return save(c, s, r, phone);
        } catch (Stop e) {
            return err(e.getMessage());
        } catch (Throwable t) {
            return broke(c, t);
        }
    }

    /**
     * 一卡通余额（0.52.1 v18）。
     *
     * 这是「登录完美校园有什么用」的答案：登过之后，卡里的钱在这儿就能看，
     * 不用再去开完美校园那个 App、也不用等它推送。
     *
     * ---- 协议（开源项目 + 2026-11-13 实测确认）----
     *   POST https://server.17wanxiao.com/YKT_Interface/xyk
     *   body     token=…&method=XYK_BASE_INFO&param={}
     *   Referer  …/YKT_Interface/v2/index.html?…&customerId=504&systemType=Android&token=…
     *
     * 🔴 三样缺一不可：body 里的 token、Referer 里的 token、Referer 里的 customerId。
     *    少任何一样都只回「无效的参数」（code_ 55）—— **不是 404**。
     *    我 2026-11 初扫了 96 个候选全判成「接口不存在」，就是因为只看路径、
     *    没带 Referer：路径是对的，缺的是参数。
     * 🔴 token 用的是**换密钥那一步的 session**，不是登录返回的 user.token。
     *    两者都是长串、都叫凭证，混了只会得到「token验证失败!」（code_ 4001）。
     */
    static String cardInfo(Context c) {
        try {
            JSONObject info = new JSONObject(c.getSharedPreferences(PREF, Context.MODE_PRIVATE)
                    .getString("info", "{}"));
            String sess = info.optString("sess");
            // v17 及以前登录的用户没存 sess（那时还不知道账目口要它）。
            // 先拿 token 顶一次 —— 万一两者等价，用户就不用重登一遍。
            boolean guessed = false;
            if (sess.isEmpty()) {
                sess = info.optString("token");
                guessed = true;
            }
            if (sess.isEmpty()) return err("再登录一次就能看余额");
            String cust = info.optString("custId");
            JSONObject r = ykt(sess, cust, "XYK_BASE_INFO", "{}");
            if (r == null) {
                note(c, "net:ykt");
                return err("连不上完美校园，请检查网络后重试");
            }
            // customerId 存下来的那个不一定对（它可能是校内编号）。
            // 退到开源项目里那个通用值再问一次 —— 只读查询，多问一次不涉风控。
            if (!r.optBoolean("result_") && !cust.isEmpty() && !"504".equals(cust)) {
                JSONObject r2 = ykt(sess, "504", "XYK_BASE_INFO", "{}");
                if (r2 != null && r2.optBoolean("result_")) { r = r2; guessed = false; }
            }
            // 拿猜的凭证去问、结果被拒：别把「token验证失败」摆给用户看 ——
            // 他能做的只有一件事，就是重新登一次。
            if (!r.optBoolean("result_") && guessed) {
                note(c, "refuse:ykt");
                return err(KICKED);
            }
            String body = r.optString("body");
            if (!r.optBoolean("result_") || body.isEmpty()) {
                String m = r.optString("message_");
                note(c, "refuse:ykt");
                // 🔴 2026-11-13：完美校园**一次只认一台设备**（用户实测：
                //    APP 和酱菜通各自登录会互相顶）。token 失效几乎都是
                //    「他在手机上又登了一次」，不是我们查错了。
                //    这时候说「余额没查到」等于什么都没说 —— 他会一直点重试。
                if (tokenDead(r, m)) return err(KICKED);
                return err(m.isEmpty() ? "余额没查到" : ("余额没查到：" + m));
            }
            JSONObject b = new JSONObject(body);
            String bal = pickNum(b);
            if (bal == null) {
                // 查到了、结构不认识。别编一个数，把原文给用户看一眼（他可以截图），
                // 下一版就知道该取哪个字段。
                note(c, "parse:ykt");
                return err("查到了，但读不出余额：" + body);
            }
            JSONObject ok = new JSONObject();
            ok.put("ok", true);
            ok.put("balance", bal);
            return ok.toString();
        } catch (Throwable t) {
            return broke(c, t);
        }
    }

    /**
     * 在余额回包里找那个「像钱」的字段。
     *
     * 各校一卡通不是同一套系统，字段名不一样（mainFare / main_fare / cardBalance…）。
     * 先认常见的，再退到「名字里带 fare/balance/money 且值是数字」——
     * 比写死一个名字、别的学校全部显示「读不懂」要好。
     */
    private static String pickNum(JSONObject o) {
        String[] keys = {"mainFare", "main_fare", "cardBalance", "card_balance",
                "balance", "mainfare", "money", "fare"};
        for (String k : keys) {
            String v = o.optString(k);
            if (isNum(v)) return v;
        }
        java.util.Iterator<String> it = o.keys();
        while (it.hasNext()) {
            String k = it.next();
            String lk = k.toLowerCase();
            if (lk.contains("fare") || lk.contains("balance") || lk.contains("money")) {
                String v = o.optString(k);
                if (isNum(v)) return v;
            }
        }
        return null;
    }

    private static boolean isNum(String v) {
        return v != null && !v.isEmpty() && v.matches("-?\\d+(\\.\\d+)?");
    }

    /** 凭证被顶掉时给用户看的那句话（不出现技术词，说清发生了什么、该做什么）。
     *  完美校园一次只认一台设备：APP 那边登录，这边就失效，反过来也一样。 */
    private static final String KICKED =
        "完美校园在别处登录了，这边要重新连一次（那边会退出去）";

    /** 这个失败是不是「凭证失效」。
     *  code_ 4001 + message_「token验证失败!」是实测到的形态（见类注释）；
     *  另外几个词是它可能换的说法，一并认上 —— 认不出来时走原来的通用文案，
     *  不会把「密码错」误说成「在别处登录」。 */
    private static boolean tokenDead(JSONObject r, String msg) {
        try {
            if ("4001".equals(String.valueOf(r.opt("code_")))) return true;
            String s = (msg == null ? "" : msg) + " " + r.optString("message_");
            s = s.toLowerCase();
            boolean tok = s.contains("token") || s.contains("凭证") || s.contains("登录状态");
            boolean bad = s.contains("验证失败") || s.contains("失效")
                    || s.contains("过期") || s.contains("重新登录");
            return tok && bad;
        } catch (Throwable t) {
            return false;
        }
    }

    /** 一卡通那个口。回包外层是 {code_, result_, message_, body}，body 是**字符串**。 */
    private static JSONObject ykt(String tok, String cust, String method, String param) {
        String ref = "https://server.17wanxiao.com/YKT_Interface/v2/index.html"
                + "?utm_source=app&utm_medium=card&UAinfo=wanxiao&versioncode=10552101"
                + "&customerId=" + cust + "&systemType=Android&token=" + enc(tok);
        String form = "token=" + enc(tok) + "&method=" + method + "&param=" + enc(param);
        String out = send(YKT, form, "application/x-www-form-urlencoded", null, ref);
        if (out == null || out.isEmpty()) return null;
        try {
            return new JSONObject(out);
        } catch (Throwable t) {
            return null;
        }
    }

    private static String enc(String s) {
        try {
            return java.net.URLEncoder.encode(s == null ? "" : s, "UTF-8");
        } catch (Throwable t) {
            return "";
        }
    }

    // ------------------------------------------------------- 三条路共用

    /** 中途「注定失败」时抛这个，带一句能直接给用户看的话。 */
    private static final class Stop extends RuntimeException {
        Stop(String m) {
            super(m);
        }
    }

    /** 校园网没认证时 gateway 会拿一个登录页顶包。这句话三处都用，写一份。 */
    private static final String BLOCKED =
            "连不上完美校园：网络可能被拦截，请先确认能否打开其他网站（校园网可能需要登录）";

    /** 换会话密钥。失败一律 Stop，文案是给用户看的。 */
    private static JSONObject secret(Context c) throws Exception {
        KeyPairGenerator g = KeyPairGenerator.getInstance("RSA");
        g.initialize(1024, new SecureRandom());
        KeyPair kp = g.generateKeyPair();

        // 🔴 这里要发 **SPKI**（X.509，`getEncoded()` 原样），不是手拼的 PKCS#1。
        //
        //    2026-11-13 实测，同一把密钥只换编码方式：
        //      PKCS#1（DER 140 / base64 188）→ HTTP 200 + **空 body**
        //      SPKI  （DER 162 / base64 216）→ HTTP 200 + 172 字节正常密文
        //    服务端多半是拿 X509EncodedKeySpec 解公钥的（它自己就是安卓客户端）。
        //    空 body 不报错，看上去像「接口挂了」，很容易往网络上去查 ——
        //    用户看到的那句「那边回了个空的」就是这么来的。
        String pub = b64(kp.getPublic().getEncoded());

        String resp = post(HOST + "exchangeSecretkey.action", "{\"key\":\"" + pub + "\"}");
        if (resp != null && resp.isEmpty()) {
            // 兜底：万一哪天它改回只认 PKCS#1。换密钥这一步不带账号、不涉风控，
            // 多发一次没有锁号风险（和登录不同）。
            resp = post(HOST + "exchangeSecretkey.action",
                    "{\"key\":\"" + b64(pkcs1((RSAPublicKey) kp.getPublic())) + "\"}");
        }
        if (resp == null) {
            note(c, "net:secret");
            throw new Stop("连不上完美校园，请检查网络后重试");
        }
        if (resp.isEmpty()) {
            note(c, "secret:empty");
            throw new Stop("完美校园返回为空，请稍后再试");
        }
        if (isHtml(resp)) {
            note(c, "blocked:secret");
            throw new Stop(BLOCKED);
        }
        Cipher rsa = Cipher.getInstance("RSA/ECB/PKCS1Padding");
        rsa.init(Cipher.DECRYPT_MODE, (RSAPrivateKey) kp.getPrivate());
        JSONObject sec = new JSONObject(new String(rsa.doFinal(unb64(resp.trim())), "UTF-8"));
        if (sec.optString("session").isEmpty() || sec.optString("key").length() < 24) {
            note(c, "secret:short");
            throw new Stop("没取到会话，请稍后再试");
        }
        return sec;
    }

    /** 发一条业务报文，回包解析成 JSON。异常一律 Stop。 */
    private static JSONObject call(Context c, JSONObject sec, String path, JSONObject inner)
            throws Exception {
        String session = sec.optString("session");
        String appKey = sec.optString("key");
        String data = b64(des3(inner.toString().getBytes("UTF-8"), appKey));
        // 🔴 外层必须手拼。签名是对这串字节算的，JSONObject 的字段顺序
        //    不由我们决定 —— 顺序一变，服务端算出来的 hash 就不一样。
        String body = "{\"session\":" + JSONObject.quote(session)
                + ",\"data\":" + JSONObject.quote(data) + "}";
        String out = postSigned(HOST + path, body, sha256hex(body));
        if (out == null) {
            note(c, "net:" + path);
            throw new Stop("连不上完美校园，请检查网络后重试");
        }
        if (out.isEmpty()) {
            note(c, "net:" + path);
            throw new Stop("完美校园没有响应，稍后再试");
        }
        if (isHtml(out)) {
            note(c, "blocked:" + path);
            throw new Stop(BLOCKED);
        }
        return new JSONObject(out);
    }

    /** 三个接口共用的字段。 */
    private static JSONObject base(Context c) throws Exception {
        JSONObject inner = new JSONObject();
        inner.put("appCode", "M002");
        inner.put("deviceId", deviceId(c));
        inner.put("netWork", "wifi");
        inner.put("qudao", "guanwang");
        inner.put("shebeixinghao", "LGE-AN10");
        inner.put("systemType", "android");
        inner.put("telephoneInfo", "12");
        inner.put("telephoneModel", "LGE-AN10");
        inner.put("wanxiaoVersion", 10552101);
        inner.put("yunyingshang", "07");
        return inner;
    }

    /**
     * 这台手机在完美校园那边的「设备号」：15 位数字，第一次生成后**永不换**。
     *
     * 🔴 这是整条登录路上最容易写错的一处。完美校园按 deviceId 认设备，
     *    没见过就要求短信验证码。以前这里每次 `UUID.randomUUID()`：
     *      · 同一个账号，在这台手机上永远登不上（每次都「新设备」）；
     *      · 服务端那边还留下一堆幽灵设备；
     *      · 用户看到的是「登录不上」，我们查协议怎么查都是对的。
     *    2026-11-13 用户真账号实测：code_=5，
     *    「您正在新设备上使用完美校园，请使用验证码进行验证登录」。
     *
     * 号段照完美校园客户端的 IMEI 规则（TAC 86 + 12 位数字 + 校验位），
     * 只求稳定、不撞别人的真机。
     */
    private static String deviceId(Context c) {
        try {
            android.content.SharedPreferences p =
                    c.getSharedPreferences(PREF, Context.MODE_PRIVATE);
            String d = p.getString("dev", "");
            if (d != null && d.length() >= 14) return d;
            StringBuilder sb = new StringBuilder("86");
            SecureRandom r = new SecureRandom();
            for (int i = 0; i < 12; i++) sb.append(r.nextInt(10));
            String data = sb.toString();
            int sum = 0;
            for (int i = 0; i < data.length(); i++) {
                int v = data.charAt(i) - '0';
                if ((i & 1) == 1) v *= 2;
                while (v > 0) {
                    sum += v % 10;
                    v /= 10;
                }
            }
            String dev = data + ((sum * 9) % 10);
            p.edit().putString("dev", dev).apply();
            return dev;
        } catch (Throwable t) {
            // 存不进去也别让登录整个失败：这一次用临时的，下次再生成。
            return "86" + String.valueOf(System.currentTimeMillis()).substring(5);
        }
    }

    /** 服务端是不是在说「你是新设备，要验证码」。 */
    private static boolean isNewDevice(String msg) {
        return msg != null && (msg.indexOf("新设备") >= 0 || msg.indexOf("验证码") >= 0);
    }

    /** 密码对了但设备是新的：告诉网页去要一次验证码，别让用户以为密码错了。 */
    private static String needSms(String msg, String phone) {
        try {
            JSONObject o = new JSONObject();
            o.put("ok", false);
            o.put("needSms", true);
            o.put("msg", msg == null || msg.isEmpty()
                    ? "本机首次登录，需短信验证码验证" : msg);
            o.put("phone4", phone.length() >= 4
                    ? phone.substring(phone.length() - 4) : phone);
            return o.toString();
        } catch (Throwable t) {
            return "{\"ok\":false,\"needSms\":true,\"msg\":\"需短信验证码验证\"}";
        }
    }

    /** 登上了：解出用户对象，存 token 和身份（**不存密码**），回报给网页。 */
    private static String save(Context c, JSONObject sec, JSONObject r, String phone)
            throws Exception {
        String appKey = sec.optString("key");
        String plain = new String(des3dec(unb64(r.optString("data")), appKey), "UTF-8")
                .replace("\r", "");
        JSONObject root = new JSONObject(plain);
        JSONObject user = root.optJSONObject("user");
        if (user == null) user = root;

        String token = user.optString("token");
        JSONObject info = new JSONObject();
        info.put("token", token);
        // 🔴 查余额要的是**换密钥那一步的 session**，不是登录返回的 user.token。
        //    两个都是长串、都叫「凭证」，混了只会得到「token验证失败!」。
        info.put("sess", sec.optString("session"));
        info.put("ecardId", user.optString("ecardCustomerid"));
        info.put("custId", user.optString("customId"));
        info.put("phone4", phone.length() >= 4 ? phone.substring(phone.length() - 4) : phone);
        info.put("at", System.currentTimeMillis());

        // 身份信息（失败不影响登录：token 已经拿到了）
        fillUserInfo(info, token);

        c.getSharedPreferences(PREF, Context.MODE_PRIVATE)
                .edit().putString("info", info.toString()).remove("last").apply();

        JSONObject ok = new JSONObject();
        ok.put("ok", true);
        ok.put("name", info.optString("name"));
        ok.put("stuNo", info.optString("stuNo"));
        ok.put("school", info.optString("school"));
        return ok.toString();
    }

    /**
     * 中途抛了（解不开回包、回包不是 JSON…）。
     *
     * 这一句以前是 `登录没成功：BadPaddingException` —— 那是给写代码的人看的，
     * 用户读到只会以为手机坏了。
     */
    private static String broke(Context c, Throwable t) {
        String tail = t.getClass().getSimpleName();
        if (lastHtml) {
            note(c, "blocked:" + tail);
            return err(BLOCKED);
        }
        if (lastHttp == 0) {
            note(c, "net:" + tail);
            return err("连不上完美校园，请检查网络后重试");
        }
        note(c, "parse:" + tail);
        return err("完美校园回的内容读不懂，可能是这一版协议变了");
    }

    /** 补学号/姓名/学院。拿不到就算了 —— 不能因为这个把「登录成功」变成失败。 */
    private static void fillUserInfo(JSONObject info, String token) {
        try {
            String body = "token=" + java.net.URLEncoder.encode(token, "UTF-8");
            String r = postForm(URL_USER_INFO, body);
            if (r == null || r.isEmpty()) return;
            JSONObject d = new JSONObject(r).optJSONObject("userInfo");
            if (d == null) return;
            info.put("name", d.optString("username"));
            info.put("stuNo", d.optString("stuNo"));
            info.put("school", d.optString("schoolName"));
            info.put("className", d.optString("classDescription"));
        } catch (Throwable t) {
            // 见上。
        }
    }

    // ------------------------------------------------------------- 加解密

    /**
     * RSA 公钥 → **PKCS#1** DER（SEQUENCE { INTEGER n, INTEGER e }）。
     *
     * ⚠️ 这是**兜底**用的，不是主路。服务端认的是 SPKI（见 {@link #secret}）；
     *    拿这个发过去会得到 **200 + 空 body** —— 2026-11-13 就是这么写反了，
     *    主路用了它，用户「一直登不上」。
     *    留着是因为它不花什么代价，而「只认某一种编码」这种事实在是会变。
     */
    private static byte[] pkcs1(RSAPublicKey k) {
        byte[] n = k.getModulus().toByteArray();
        byte[] e = k.getPublicExponent().toByteArray();
        byte[] seq = cat(tlv(0x02, n), tlv(0x02, e));
        return tlv(0x30, seq);
    }

    private static byte[] tlv(int tag, byte[] v) {
        byte[] len;
        if (v.length < 0x80) {
            len = new byte[]{(byte) v.length};
        } else if (v.length < 0x100) {
            len = new byte[]{(byte) 0x81, (byte) v.length};
        } else {
            len = new byte[]{(byte) 0x82, (byte) (v.length >> 8), (byte) v.length};
        }
        byte[] out = new byte[1 + len.length + v.length];
        out[0] = (byte) tag;
        System.arraycopy(len, 0, out, 1, len.length);
        System.arraycopy(v, 0, out, 1 + len.length, v.length);
        return out;
    }

    private static byte[] cat(byte[] a, byte[] b) {
        byte[] out = new byte[a.length + b.length];
        System.arraycopy(a, 0, out, 0, a.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }

    private static byte[] des3(byte[] data, String appKey) throws Exception {
        Cipher c = Cipher.getInstance("DESede/CBC/PKCS5Padding");
        c.init(Cipher.ENCRYPT_MODE, key(appKey), new IvParameterSpec(IV));
        return c.doFinal(data);
    }

    private static byte[] des3dec(byte[] data, String appKey) throws Exception {
        Cipher c = Cipher.getInstance("DESede/CBC/PKCS5Padding");
        c.init(Cipher.DECRYPT_MODE, key(appKey), new IvParameterSpec(IV));
        return c.doFinal(data);
    }

    private static SecretKeySpec key(String appKey) {
        // app_key 是 32 个十六进制字符，协议只取**前 24 个字符**当 3DES 密钥
        // （24 字节 ASCII，不是把 hex 解成 12 字节）。
        return new SecretKeySpec(appKey.substring(0, 24).getBytes(), "DESede");
    }

    private static String sha256hex(String s) throws Exception {
        byte[] d = MessageDigest.getInstance("SHA-256").digest(s.getBytes("UTF-8"));
        StringBuilder sb = new StringBuilder();
        for (byte b : d) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    private static String b64(byte[] b) {
        return android.util.Base64.encodeToString(b, android.util.Base64.NO_WRAP);
    }

    private static byte[] unb64(String s) {
        return android.util.Base64.decode(s, android.util.Base64.DEFAULT);
    }

    // --------------------------------------------------------------- 网络

    private static String post(String url, String json) {
        return send(url, json, "application/json;charset=UTF-8", null);
    }

    private static String postSigned(String url, String json, String sign) {
        return send(url, json, "application/text;charset=UTF-8", sign);
    }

    private static String postForm(String url, String form) {
        return send(url, form, "application/x-www-form-urlencoded", null);
    }

    private static String send(String url, String body, String ct, String sign) {
        return send(url, body, ct, sign, null);
    }

    /** 多一个 Referer：一卡通那个口把 token 和 customerId 塞在 Referer 里传。 */
    private static String send(String url, String body, String ct, String sign, String ref) {
        HttpURLConnection c = null;
        lastHttp = 0;
        lastHtml = false;
        try {
            c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(12000);
            c.setReadTimeout(20000);
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setRequestProperty("User-Agent", UA);
            c.setRequestProperty("Content-Type", ct);
            if (sign != null) c.setRequestProperty("campusSign", sign);
            if (ref != null) c.setRequestProperty("Referer", ref);
            byte[] b = body.getBytes("UTF-8");
            c.setFixedLengthStreamingMode(b.length);
            OutputStream os = c.getOutputStream();
            os.write(b);
            os.close();
            int code = c.getResponseCode();
            lastHttp = code;
            InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
            if (in == null) return "";
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
            in.close();
            String out = new String(bo.toByteArray(), "UTF-8");
            lastHtml = out.toLowerCase().contains("<html");
            return out;
        } catch (Throwable t) {
            return null;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /** 回包是不是一个网页。校园网未认证时 portal 就是这么把请求接走的。 */
    private static boolean isHtml(String s) {
        return s != null && s.toLowerCase().contains("<html");
    }

    private static String err(String msg) {
        try {
            JSONObject o = new JSONObject();
            o.put("ok", false);
            o.put("msg", msg);
            return o.toString();
        } catch (Throwable t) {
            return "{\"ok\":false,\"msg\":\"登录失败\"}";
        }
    }
}
