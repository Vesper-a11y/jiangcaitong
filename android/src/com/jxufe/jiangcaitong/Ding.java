package com.jxufe.jiangcaitong;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.provider.Settings;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 钉钉 / 完美校园通知的收纳盒（2026-11-13）。
 *
 * 类名还叫 Ding 是因为它是钉钉那轮建的；现在收的是两家，见下。
 *
 * 为什么只有这一条路：学校用的是钉钉**专有版**（`com.alibaba.android.rimet.zzhy`，
 * 桌面上叫「江西财经大学」），学校只是它的一个组织，任何"学校配置的微应用"都跑在
 * 钉钉自己的后端里，没有对外的数据接口；包体里也没有任何学校域名（`AndroidManifest`
 * 里只有 alidocs/dingtalk/taobao 这些阿里自己的 host）。所以酱菜通**读不到**群消息
 * 之外的任何东西 —— 审批、打卡、家校本都只能进去点。
 *
 * 唯一能拿到的是**系统通知**：用户在系统设置里勾一下「通知使用权」，
 * 钉钉推出来的那条通知（发件人 + 内容摘要）我们就能看到。
 *
 * 2026-11-13 起同一条路也收**完美校园**（`com.newcapec.mobile.ncp`）：
 * 它的「校园卡通知」会把消费和充值推一条带金额的到通知栏，我们照抄一份，
 * 于是「食堂刷了多少钱」也能进公告页。两条来源共用这一个盒子，
 * 每条上标一个 {@code s} 字段区分（`ding` / `wanxiao`）。
 *
 * 🔴 隐私边界，代码里必须守住三条：
 *    1. {@link DingListener} 里先比包名，不是这两家的通知**连读都不读** ——
 *       不解析、不落盘、不留痕。这个权限能看全机通知，能守的只有自己这层过滤。
 *    2. 只存在本机 SharedPreferences，**不上传**（没有上报代码，也不需要）。
 *    3. 最多 {@link #MAX} 条，超了丢最旧的；用户可以一键清空。
 */
final class Ding {

    /**
     * 认的包名。通知字段是同一套（钉钉专有版 dex 里同样是
     * `android.conversationTitle`，实测过；完美校园用的是标准 title/text，
     * 走下面那串兜底就行）。
     *
     *  - `com.alibaba.android.rimet`        钉钉官方通用版
     *  - `com.alibaba.android.rimet.zzhy`   江财专有版（学校发的就是这个，
     *                                        图标名字叫「江西财经大学」，里面是钉钉 8.1.10）
     *  - `com.alibaba.work.android`         阿里郎/专属版
     *  - `com.newcapec.mobile.ncp`          完美校园（校园卡消费/充值提醒）
     */
    static final String[] PKGS = {
            "com.alibaba.android.rimet",
            "com.alibaba.android.rimet.zzhy",
            "com.alibaba.work.android",
            "com.newcapec.mobile.ncp",
    };

    /**
     * 专有版会在 `rimet` 后面挂学校代号（`zzhy` = 江西财经大学），学校换一版就可能换个后缀。
     *
     * 🔴 只认死 `rimet` 一个名字，学校手机上就是「装了钉钉却说没装、开着授权却一条也
     *    收不到」—— 2026-11-13 用户拿真机 APK 来问「兼容好了吗」，根因就在这。
     * 🔴 前缀只放这三段（各自自己的命名空间），不要写成「含 rimet 就算」：
     *    这个权限能看全机通知，认宽了就是把别人的消息也抄进来。
     */
    private static final String[] PREFIXES = {
            "com.alibaba.android.rimet.",
            "com.alibaba.work.android.",
            "com.newcapec.",
    };

    private static final String PREF = "hyt_ding";
    private static final String KEY = "list";
    /**
     * 最多留几条。
     *
     * 60 条 ≈ 20 KB 的 JSON。再多没有意义：通知是「这两天谁找过我」，
     * 不是归档；真要翻历史得去钉钉。
     */
    private static final int MAX = 60;

    private Ding() {
    }

    static boolean isDing(String pkg) {
        if (pkg == null) return false;
        for (String p : PKGS) {
            if (p.equals(pkg)) return true;
        }
        for (String p : PREFIXES) {
            if (pkg.startsWith(p)) return true;
        }
        return false;
    }

    /**
     * 这条通知是谁的：`"ding"` / `"wanxiao"`，不认识的包返回 null。
     *
     * 界面上要按来源分开显示 —— 钉钉的是「谁找我」，完美校园的是「花了多少钱」，
     * 混在一列里既不好认也不好读。
     */
    static String srcOf(String pkg) {
        if (!isDing(pkg)) return null;
        return pkg.startsWith("com.newcapec.") ? "wanxiao" : "ding";
    }

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREF, Context.MODE_PRIVATE);
    }

    /**
     * 收一条。
     *
     * @param dedup 系统给这条通知的 key（`StatusBarNotification.getKey()`）。
     *              钉钉更新同一条通知（"张三：在吗" → "张三：在吗，急"）时 key 不变，
     *              所以用它做去重 —— 按标题正文去重会把后一句当成新消息。
     * @param src   来源，见 {@link #srcOf}。网页按它分列显示。
     */
    static synchronized void add(Context c, String dedup, String src,
                                 String title, String text, long when) {
        try {
            String t = clip(title, 60);
            String x = clip(text, 160);
            // 常驻通知（"钉钉正在后台运行"之类）没有正文，不记。
            if (t.isEmpty() && x.isEmpty()) return;

            JSONArray old = new JSONArray(prefs(c).getString(KEY, "[]"));
            JSONArray out = new JSONArray();
            JSONObject item = new JSONObject();
            item.put("k", dedup == null ? "" : dedup);
            item.put("s", src == null ? "ding" : src);
            item.put("t", t);
            item.put("x", x);
            item.put("w", when);
            out.put(item);

            int n = 0;
            for (int i = 0; i < old.length() && n < MAX - 1; i++) {
                JSONObject o = old.optJSONObject(i);
                if (o == null) continue;
                // 同一条被更新：只留新的那份，位置也提到最前（刚发生过的事）。
                if (!item.optString("k").isEmpty()
                        && item.optString("k").equals(o.optString("k"))) {
                    continue;
                }
                out.put(o);
                n++;
            }
            prefs(c).edit().putString(KEY, out.toString()).apply();
        } catch (Throwable th) {
            // 存不下就算了：一条通知而已，绝不能因为写盘失败让系统的通知分发卡住。
        }
    }

    /** 给网页看的列表（最新在前）。 */
    static String list(Context c) {
        try {
            JSONArray a = new JSONArray(prefs(c).getString(KEY, "[]"));
            JSONArray out = new JSONArray();
            for (int i = 0; i < a.length() && i < MAX; i++) {
                JSONObject o = a.optJSONObject(i);
                if (o != null) out.put(o);
            }
            return out.toString();
        } catch (Throwable t) {
            return "[]";
        }
    }

    static void clear(Context c) {
        try {
            prefs(c).edit().remove(KEY).apply();
        } catch (Throwable t) {
            // 同上，清不掉不是错误。
        }
    }

    static int count(Context c) {
        try {
            return new JSONArray(prefs(c).getString(KEY, "[]")).length();
        } catch (Throwable t) {
            return 0;
        }
    }

    /**
     * 用户有没有在系统设置里勾上「通知使用权」。
     *
     * 🔴 只能查系统设置，**不能**查自己有没有被绑定：`NotificationListenerService`
     *    被绑定时我们这边拿不到任何回调之外的信息，而没授权时
     *    {@code getActiveNotifications()} 会抛异常而不是返回空 —— 拿它当判断
     *    依据，授权和"暂无通知"就分不开。
     */
    static boolean granted(Context c) {
        try {
            String flat = Settings.Secure.getString(
                    c.getContentResolver(), "enabled_notification_listeners");
            if (flat == null || flat.isEmpty()) return false;
            String me = c.getPackageName();
            for (String s : flat.split(":")) {
                ComponentName cn = ComponentName.unflattenFromString(s);
                if (cn != null && me.equals(cn.getPackageName())) return true;
            }
        } catch (Throwable t) {
            // 读不到设置（极少数定制系统）就当没授权 —— 让用户去点一下，
            // 最多是多点一次，比"显示成已开启但其实收不到"好。
        }
        return false;
    }

    /** 状态给网页：{granted, n}。 */
    static String state(Context c) {
        try {
            JSONObject o = new JSONObject();
            o.put("granted", granted(c));
            o.put("n", count(c));
            return o.toString();
        } catch (Throwable t) {
            return "{\"granted\":false,\"n\":0}";
        }
    }

    /**
     * 跳到系统的「通知使用权」设置页。
     *
     * 为什么不用 `ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS` 直接落到我们这一项：
     * 那个常量许多国产 ROM 没实现，跳过去是白屏或者直接抛 ActivityNotFound；
     * 列表页一定能打开，用户按名字找得到（列表里就几个 App）。
     */
    static boolean openSettings(Context c) {
        try {
            Intent it = new Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS");
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            c.startActivity(it);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    private static String clip(String s, int max) {
        if (s == null) return "";
        String v = s.replace('\n', ' ').trim();
        return v.length() <= max ? v : v.substring(0, max);
    }
}
