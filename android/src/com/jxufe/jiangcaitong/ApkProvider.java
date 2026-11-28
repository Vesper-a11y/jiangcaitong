package com.jxufe.jiangcaitong;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Environment;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * 把下载好的安装包以 `content://` 借给系统安装器（2026-11-28 U125）。
 *
 * 🔴 为什么需要它：Android 7 起跨应用传 `file://` 直接抛 FileUriExposedException，
 *    安装器读不到文件，用户看到的是「解析软件包时出现问题」。项目手工打包、
 *    没有 androidx，所以和 ShareProvider 一样自己写一个最小号 FileProvider。
 *
 * 🔴 为什么不复用 ShareProvider：那个只借 `.ics` 且 MIME 写死成 text/calendar。
 *    安装器要是拿到 text/calendar，会当文本打开而不是弹安装。两个用途的
 *    门禁和类型完全不同，混在一个 provider 里迟早互相松绑。
 *
 * 文件放在 `getExternalFilesDir(DIRECTORY_DOWNLOADS)`：应用私有目录，
 * 不需要读写存储权限（Android 10 起公共目录受限，这里绕开那一整摊）。
 * 卸载 App 时这个目录跟着删 —— 正是我们要的，不留垃圾。
 */
public class ApkProvider extends ContentProvider {

    public static final String AUTHORITY = "com.jxufe.jiangcaitong.apk";
    public static final String MIME = "application/vnd.android.package-archive";

    /**
     * 只借这一类名字。🔴 必须钉死成 `jiangcaitong-<数字.数字>.apk`：
     *    名字是从 JS 传进来的字符串，不卡死就能拼出 `..%2F` 之类的越界路径，
     *    变成「网页让装哪个文件就装哪个文件」。
     */
    private static final String NAME_RE = "jiangcaitong-[0-9]+(\\.[0-9]+){0,2}\\.apk";

    /** 安装包放哪。下载方（NativeBridge）和读取方必须走同一个方法。 */
    public static File dir(Context c) {
        File d = null;
        try {
            d = c.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        } catch (Throwable t) {
            d = null;
        }
        // 外部存储没挂载（极少见）：退回内部目录，照样能装。
        if (d == null) d = new File(c.getFilesDir(), "apk");
        //noinspection ResultOfMethodCallIgnored
        if (!d.exists()) d.mkdirs();
        return d;
    }

    public static Uri uriFor(String name) {
        return Uri.parse("content://" + AUTHORITY + "/" + name);
    }

    /** 名字合法就返回对应文件，否则 null。 */
    public static File fileOf(Context c, String name) {
        if (name == null || name.contains("..") || !name.matches(NAME_RE)) return null;
        return new File(dir(c), name);
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    private File pick(Uri uri) {
        if (uri == null) return null;
        String n = uri.getLastPathSegment();
        Context c = getContext();
        if (c == null) return null;
        return fileOf(c, n);
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File f = pick(uri);
        if (f == null || !f.isFile()) throw new FileNotFoundException(String.valueOf(uri));
        // 只读：安装器只需要读，没有理由让任何应用改安装包。
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        File f = pick(uri);
        if (f == null || !f.isFile()) return null;
        if (projection == null) {
            projection = new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        }
        MatrixCursor mc = new MatrixCursor(projection, 1);
        Object[] row = new Object[projection.length];
        for (int i = 0; i < projection.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(projection[i])) {
                row[i] = f.getName();
            } else if (OpenableColumns.SIZE.equals(projection[i])) {
                row[i] = f.length();
            } else {
                row[i] = null;
            }
        }
        mc.addRow(row);
        return mc;
    }

    @Override
    public String getType(Uri uri) {
        return MIME;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }
}
