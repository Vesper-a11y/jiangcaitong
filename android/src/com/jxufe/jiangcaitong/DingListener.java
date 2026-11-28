package com.jxufe.jiangcaitong;

import android.app.Notification;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

/**
 * 收钉钉 / 完美校园的通知。用户授权「通知使用权」后由系统绑定（2026-11-13）。
 *
 * 这个类**只做一件事**：把这两家的那条通知抄一份存进 {@link Ding}，别的一概不管。
 * 不弹自己的通知、不改对方的通知、不上传、不统计。
 *
 * 🔴 顺序不能调：**先比包名，再碰任何字段**。
 *    这个权限能看到全机的通知（微信、短信验证码、银行……），系统会把每一条都
 *    送进这个方法。下面那句 `Ding.srcOf(pkg)` 为 null 就 return 是整条隐私边界的
 *    全部实现 —— 挪到取 extras 之后，就等于先把别人的短信读进内存了。
 *
 * 🔴 整个方法包在 try 里，异常一律吞掉：这里是系统的通知分发路径，
 *    我们抛一个异常有可能让**别的 App 的通知也不显示**。宁可少记一条。
 *
 * 钉钉的通知长这样（实测两类的 extras 不同）：
 *   · 单聊/群聊：`android.conversationTitle` = 群名或发件人，`android.text` = 内容
 *   · 汇总：「N 条新消息」，标题是钉钉，正文只有 `android.subText`
 * 所以下面按「会话名 → 大标题 → 标题」和「大正文 → 正文 → ticker → 子标题」
 * 两串依次兜底，全空就不记（常驻/系统类通知都是全空）。
 */
public class DingListener extends NotificationListenerService {

    /** 这两个不是 Notification 的公开常量，只能写字面量。 */
    private static final String EXTRA_CONV = "android.conversationTitle";
    private static final String EXTRA_TITLE_BIG = "android.title.big";

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        handle(sbn);
    }

    @Override
    public void onListenerConnected() {
        // 连接成功不用做事：钉钉通知靠 onNotificationPosted 实时收。
    }

    private void handle(StatusBarNotification sbn) {
        try {
            if (sbn == null) return;
            String pkg = sbn.getPackageName();

            String src = Ding.srcOf(pkg);
            if (src == null) return;

            // 常驻通知（"钉钉正在运行"、通话中等）不是消息，记下来只会占位置。
            if (sbn.isOngoing()) return;
            Notification n = sbn.getNotification();
            if (n == null) return;
            Bundle b = n.extras;
            if (b == null) return;

            String title = str(b.getCharSequence(EXTRA_CONV));
            if (title.isEmpty()) title = str(b.getCharSequence(EXTRA_TITLE_BIG));
            if (title.isEmpty()) title = str(b.getCharSequence(Notification.EXTRA_TITLE));

            String text = str(b.getCharSequence(Notification.EXTRA_BIG_TEXT));
            if (text.isEmpty()) text = str(b.getCharSequence(Notification.EXTRA_TEXT));
            if (text.isEmpty()) text = str(n.tickerText);
            if (text.isEmpty()) text = str(b.getCharSequence(Notification.EXTRA_SUB_TEXT));

            if (title.isEmpty() && text.isEmpty()) return;

            Ding.add(getApplicationContext(), sbn.getKey(), src, title, text, sbn.getPostTime());
        } catch (Throwable t) {
            // 见类注释：这里的异常不能往系统那边冒。
        }
    }

    /**
     * 用户取消授权 / 服务被系统解绑。
     *
     * 什么都不做：授权状态是每次现查系统设置的（见 {@link Ding#granted}），
     * 在这里再存一个"已断开"的标记，只会和真实状态打架 ——
     * 用户重新勾上之后那个标记还在，界面就一直显示"没开"。
     */
    @Override
    public void onListenerDisconnected() {
        try {
            super.onListenerDisconnected();
        } catch (Throwable t) {
            // 部分 ROM 上这个方法内部会抛（服务已经被解绑），吞掉。
        }
    }

    private static String str(CharSequence cs) {
        return cs == null ? "" : cs.toString().trim();
    }
}
