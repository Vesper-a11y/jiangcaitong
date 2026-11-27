package com.jxufe.jiangcaitong;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.provider.Settings;
import android.text.TextUtils;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 待取快递的取件码：从**系统通知**里认出来，存在本机。
 *
 * 为什么不是「查接口」：菜鸟/淘宝/拼多多/抖音/京东都没有公开的「我的待取件」
 * 接口，取件码只出现在它们自己推的通知里。系统给了
 * NotificationListenerService 这条正规通道 —— 用户授权一次（钉钉本来就要），
 * App 就能读到这几个 App 发出来的通知文本。
 *
 * 🔴 2026-11-24 U85：**短信读取和粘贴识别都删了**（用户：「没用，删掉」）。
 *    短信要额外授权、粘贴要人动手，两条都不如「通知 + 一点直达」干净。
 * 🔴 只在本机：码和驿站名留在 SharedPreferences，不出设备、不上传。
 * 🔴 认不出来就不写：宁可卡片上没有，也不要猜一串数字给用户看。
 */
final class Parcels {

    private static final String SP = "parcels";
    private static final String K_LIST = "list";
    /** 七天后自动过期：驿站一般三天不取就退回，留七天足够，也不会越堆越多。 */
    private static final long TTL_MS = 7L * 24 * 3600 * 1000;
    private static final int MAX = 12;

    /** 「取件码：8-1-2」「取货码 1234」「提货码：A-12」 */
    private static final Pattern P_LABEL = Pattern.compile(
            "(?:取件码|取货码|提货码|取件号|取货号)\\s*[:：]?\\s*([0-9A-Za-z][0-9A-Za-z\\-]{1,11})");
    /** 驿站常见格式 8-1-2 / 12-3-4567，前面不带「取件码」三个字也认。 */
    private static final Pattern P_DASH = Pattern.compile(
            "(?:^|[^0-9])(\\d{1,2}-\\d{1,2}-\\d{2,5})(?![0-9])");
    /** 这几句出现 = 已经取走了，把同码的那条删掉。 */
    private static final Pattern P_DONE = Pattern.compile(
            "已取出|已取件|取件成功|已签收|已完成取件|包裹已取出");
    /** 驿站名：「南昌经开区菜鸟驿站」「南区快递超市」「丰巢」这类。 */
    private static final Pattern P_SITE = Pattern.compile(
            "([\\u4e00-\\u9fa5A-Za-z0-9·\\-]{2,14}?(?:菜鸟驿站|驿站|快递超市|快递服务站|服务中心|丰巢|快递柜|快递点))");

    private Parcels() { }

    /** 通知来自哪个 App —— 只认这几家，别的一律不看（少读一点是一点）。 */
    static String appOf(String pkg) {
        if (pkg == null) return "";
        switch (pkg) {
            case "com.cainiao.wireless": return "菜鸟";
            case "com.taobao.taobao": return "淘宝";
            case "com.xunmeng.pinduoduo": return "拼多多";
            case "com.ss.android.ugc.aweme":
            case "com.ss.android.ugc.aweme.lite": return "抖音";
            case "com.jingdong.app.mall": return "京东";
            case "com.tencent.mm": return "微信";
            default: return "";
        }
    }

    /** 通知进来了：认码 → 存；认出「已取」→ 删。 */
    static synchronized void feed(Context c, String pkg, String title, String text) {
        String app = appOf(pkg);
        if (app.isEmpty()) return;
        ingest(c, app, title, text, System.currentTimeMillis());
    }

            /** 给 JS 的 JSON：`[{code,app,site,at}]`，at 是秒。 */
    static synchronized String dump(Context c) {
        SharedPreferences sp = c.getSharedPreferences(SP, Context.MODE_PRIVATE);
        List<Item> list = load(sp);
        long now = System.currentTimeMillis();
        JSONArray arr = new JSONArray();
        for (int i = 0; i < list.size(); i++) {
            Item it = list.get(i);
            if (now - it.at > TTL_MS) continue;
            try {
                JSONObject o = new JSONObject();
                o.put("code", it.code);
                o.put("app", it.app);
                o.put("site", it.site);
                o.put("at", it.at / 1000);
                arr.put(o);
            } catch (Throwable ignored) { }
        }
        return arr.toString();
    }

    /** 手动清空（卡片上「清掉」用）。 */
    static synchronized void clear(Context c) {
        c.getSharedPreferences(SP, Context.MODE_PRIVATE).edit().remove(K_LIST).apply();
    }

    /**
     * 有没有「通知使用权」。
     *
     * 🔴 这条权限没有运行时弹窗，只能读 Settings.Secure 里那份已授权列表 ——
     *    列表里出现本包名才算真的有。查不到一律当没有（宁可让用户去开一次）。
     */
    static boolean hasPerm(Context c) {
        try {
            String s = Settings.Secure.getString(c.getContentResolver(),
                    "enabled_notification_listeners");
            if (TextUtils.isEmpty(s)) return false;
            return s.contains(c.getPackageName());
        } catch (Throwable t) {
            return false;
        }
    }

    /** 跳到系统的「通知使用权」列表，用户自己勾。 */
    static void ask(Context c) {
        try {
            Intent i = new Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS");
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            c.startActivity(i);
        } catch (Throwable t) {
            try {
                Intent i = new Intent(Settings.ACTION_SETTINGS);
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                c.startActivity(i);
            } catch (Throwable ignored) { }
        }
    }

        /* ---------------- 内部 ---------------- */

    /** 认码 → 落盘。返回 true = 这条里有码（新增或删除了一条）。 */
    private static boolean ingest(Context c, String app, String title, String all, long atMs) {
        String text = ((title == null ? "" : title) + " " + (all == null ? "" : all)).trim();
        if (text.isEmpty()) return false;

        String code = pick(text);
        if (code.isEmpty()) return false;

        boolean done = P_DONE.matcher(text).find();
        SharedPreferences sp = c.getSharedPreferences(SP, Context.MODE_PRIVATE);
        List<Item> list = load(sp);

        // 同一个码只留一条：后到的通知更新来源和时间。
        for (int i = list.size() - 1; i >= 0; i--) {
            if (list.get(i).code.equalsIgnoreCase(code)) list.remove(i);
        }
        if (!done) {
            Item it = new Item();
            it.code = code;
            it.app = app;
            it.site = siteOf(text, title, app);
            it.at = atMs;
            list.add(it);
        }

        // 新的在前，超量的丢最旧的。
        Collections.sort(list, new Comparator<Item>() {
            @Override public int compare(Item a, Item b) { return Long.compare(b.at, a.at); }
        });
        while (list.size() > MAX) list.remove(list.size() - 1);

        save(sp, list);
        return true;
    }

    private static String pick(String s) {
        Matcher m = P_LABEL.matcher(s);
        if (m.find()) {
            String v = trimCode(m.group(1));
            if (!v.isEmpty()) return v;
        }
        // 没有「取件码」三个字也认 8-1-2 这种格式 —— 但要躲开日期（2026-11-24
        // 同样长成「数字-数字-数字」，写进卡片就是一条假取件码）。
        m = P_DASH.matcher(s);
        while (m.find()) {
            String v = trimCode(m.group(1));
            if (v.isEmpty()) continue;
            if (v.matches("\\d{4}-\\d{1,2}-\\d{1,2}")) continue;
            return v;
        }
        return "";
    }

    /** 去掉尾部的标点；太短的残句不当码。 */
    private static String trimCode(String raw) {
        if (raw == null) return "";
        String v = raw.replaceAll("^[^0-9A-Za-z]+|[^0-9A-Za-z]+$", "");
        if (v.length() < 3 || v.length() > 12) return "";
        return v;
    }

    /**
     * 驿站名。优先从正文里认「XX驿站」—— 那才是用户真正要找的地方；
     * 正文里没有才退到通知标题；标题也是泛泛的（「取件通知」/纯时间）就用 App 名。
     */
    private static String siteOf(String all, String title, String app) {
        Matcher m = P_SITE.matcher(all);
        if (m.find()) {
            String v = m.group(1).trim();
            if (v.length() > 2) return v;
        }
        String t = title == null ? "" : title.trim();
        if (t.length() >= 2 && t.length() <= 14 && !t.matches("[\\d\\s:：\\-]+")) return t;
        return app;
    }

    private static List<Item> load(SharedPreferences sp) {
        List<Item> out = new ArrayList<>();
        String raw = sp.getString(K_LIST, "");
        if (TextUtils.isEmpty(raw)) return out;
        try {
            JSONArray arr = new JSONArray(raw);
            long now = System.currentTimeMillis();
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.optJSONObject(i);
                if (o == null) continue;
                Item it = new Item();
                it.code = o.optString("code", "");
                it.app = o.optString("app", "");
                it.site = o.optString("site", "");
                it.at = o.optLong("at", 0);
                if (it.code.isEmpty() || it.at <= 0) continue;
                if (now - it.at > TTL_MS) continue;
                out.add(it);
            }
        } catch (Throwable ignored) { }
        return out;
    }

    private static void save(SharedPreferences sp, List<Item> list) {
        JSONArray arr = new JSONArray();
        for (int i = 0; i < list.size(); i++) {
            Item it = list.get(i);
            try {
                JSONObject o = new JSONObject();
                o.put("code", it.code);
                o.put("app", it.app);
                o.put("site", it.site);
                o.put("at", it.at);
                arr.put(o);
            } catch (Throwable ignored) { }
        }
        sp.edit().putString(K_LIST, arr.toString()).apply();
    }

    private static final class Item {
        String code = "";
        String app = "";
        String site = "";
        long at = 0;
    }
}
