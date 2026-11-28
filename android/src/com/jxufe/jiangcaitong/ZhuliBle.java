package com.jxufe.jiangcaitong;

import android.Manifest;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.webkit.WebView;

import org.json.JSONObject;

import java.util.List;
import java.util.UUID;

/**
 * 住理热水表的蓝牙管道（2026-11-24 1.3.15）。
 *
 * <p>🔴 这一层**不实现任何协议**，它只做三件事：连上、把一段 hex 写进去、
 * 把水表回的帧原样交回去。指令本身全是住理服务端生成的（见 app.js 的
 * zlWaterStart），签名、计费、CRC 都由服务端负责 —— 所以这里没有一行
 * 「解密」，也不需要。
 *
 * <p>时序对齐 FlandreSY（MIT，github.com/amamiyakazuki/FlandreSY）的
 * {@code ZhuliBleContract}：服务 ff12 / 写 ff01 / 通知 ff02，扫描 8 秒、
 * 连接 10 秒 ×3 次、等回帧 6 秒；帧类型取**第 3 个字节**（下标 2），
 * 240 表示设备报 CRC 错。
 *
 * <p>🔴 为什么不把 HTTP 也塞进 Java：那套签名（参数排序 + MD5 大写）已经
 * 在 native.js 里跑通了。在 Java 再写一份，等于两个地方各维护一份密钥规则，
 * 迟早对不上。所以这里只当管道，JS 一步一步驱动。
 */
public class ZhuliBle {

    private static final UUID SVC = UUID.fromString("0000ff12-0000-1000-8000-00805f9b34fb");
    private static final UUID CH_WRITE = UUID.fromString("0000ff01-0000-1000-8000-00805f9b34fb");
    private static final UUID CH_NOTIFY = UUID.fromString("0000ff02-0000-1000-8000-00805f9b34fb");
    private static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");

    private static final int SCAN_MS = 8000;
    private static final int CONNECT_MS = 10000;
    private static final int REPLY_MS = 6000;
    private static final int RETRY = 3;
    private static final int CRC_TYPE = 240;

    private final WebView web;
    private final Context ctx;
    private final Handler ui = new Handler(Looper.getMainLooper());

    private String reqId = "";
    private String wantName = "";
    private String wantMac = "";
    private int[] wantTypes = new int[0];

    private BluetoothAdapter adapter;
    private BluetoothLeScanner scanner;
    private BluetoothGatt gatt;
    private BluetoothGattCharacteristic chWrite;
    private BluetoothGattCharacteristic chNotify;

    private int attempts = 0;
    private boolean ready = false;
    private boolean closedByUs = false;
    private boolean gotFrame = false;

    private Runnable scanTimer;
    private Runnable connectTimer;
    private Runnable replyTimer;
    private Runnable retryTask;

    ZhuliBle(WebView web) {
        this.web = web;
        this.ctx = web.getContext();
    }

    // ---------------------------------------------------------------- 权限

    /** 当前系统版本真正需要**运行时**授予的那几条。 */
    static String[] neededPerms() {
        if (Build.VERSION.SDK_INT >= 31) {
            return new String[]{
                    Manifest.permission.BLUETOOTH_SCAN,
                    Manifest.permission.BLUETOOTH_CONNECT};
        }
        // Android 12 以下：BLUETOOTH / BLUETOOTH_ADMIN 是装机即给，
        // 但**扫描必须有定位**，没有的话 onScanResult 一条都不回（也不报错）。
        return new String[]{Manifest.permission.ACCESS_FINE_LOCATION};
    }

    static boolean hasPerm(Context c) {
        if (c == null) return false;
        for (String p : neededPerms()) {
            if (c.checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) return false;
        }
        return true;
    }

    /** 已经授权返回 "1"，否则 "0"，这个壳没有蓝牙返回 "none"。 */
    static String permState(Context c) {
        if (c == null) return "none";
        if (!c.getPackageManager().hasSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE)) {
            return "none";
        }
        return hasPerm(c) ? "1" : "0";
    }

    /** 弹系统授权框。只能在 Activity 上调。 */
    static String askPerm(Context c) {
        if (!(c instanceof Activity)) return "noactivity";
        Activity a = (Activity) c;
        if (hasPerm(c)) return "granted";
        try {
            a.requestPermissions(neededPerms(), 62);
            return "asked";
        } catch (Throwable t) {
            return "fail";
        }
    }

    // ---------------------------------------------------------------- 对外

    /** 扫到并连上、开好通知 → 回调 {ev:"ready"}。 */
    String open(final String req, final String name, final String mac) {
        if (!hasPerm(ctx)) return "noperm";
        close("", false);
        reqId = req == null ? "" : req;
        wantName = name == null ? "" : name.trim();
        wantMac = mac == null ? "" : mac.trim().replace(":", "").toUpperCase();
        if (wantName.isEmpty() && wantMac.isEmpty()) return "notarget";

        BluetoothManager mgr = (BluetoothManager) ctx.getSystemService(Context.BLUETOOTH_SERVICE);
        adapter = mgr == null ? null : mgr.getAdapter();
        if (adapter == null) return "nobt";
        if (!adapter.isEnabled()) return "btoff";
        scanner = adapter.getBluetoothLeScanner();
        if (scanner == null) return "noscan";

        attempts = 0;
        ready = false;
        closedByUs = false;
        startScan();
        return "ok";
    }

    /**
     * 写一条 hex，然后等一条**类型匹配**的回帧 → 回调 {ev:"frame", hex:"..."}。
     *
     * @param types 逗号分隔的期望类型，例如 "1" 或 "3,64"。
     */
    String write(final String req, final String hex, final String types) {
        if (!ready || gatt == null || chWrite == null) return "notready";
        if (!reqId.equals(req == null ? "" : req)) return "wrongreq";
        wantTypes = parseInts(types);
        if (wantTypes.length == 0) return "notype";

        final byte[] data = hexToBytes(hex);
        if (data == null) return "badhex";
        gotFrame = false;
        armReplyTimer();
        try {
            boolean queued;
            if (Build.VERSION.SDK_INT >= 33) {
                queued = gatt.writeCharacteristic(
                        chWrite, data, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) ==
                        BluetoothGatt.GATT_SUCCESS;
            } else {
                chWrite.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
                chWrite.setValue(data);
                queued = gatt.writeCharacteristic(chWrite);
            }
            if (!queued) {
                clearReplyTimer();
                return "writefail";
            }
        } catch (Throwable t) {
            clearReplyTimer();
            return "writefail";
        }
        return "ok";
    }

    /** 断开。传 req 且不匹配时什么都不做（避免一次流程把另一次掐掉）。 */
    String close(String req, boolean check) {
        if (check && req != null && !req.isEmpty() && !reqId.equals(req)) return "wrongreq";
        closedByUs = true;
        stopScan();
        clearScanTimer();
        clearConnectTimer();
        clearReplyTimer();
        if (retryTask != null) {
            ui.removeCallbacks(retryTask);
            retryTask = null;
        }
        if (gatt != null) {
            try {
                if (chNotify != null) gatt.setCharacteristicNotification(chNotify, false);
            } catch (Throwable ignored) {
            }
            try {
                gatt.disconnect();
            } catch (Throwable ignored) {
            }
            try {
                gatt.close();
            } catch (Throwable ignored) {
            }
        }
        gatt = null;
        chWrite = null;
        chNotify = null;
        ready = false;
        reqId = "";
        return "ok";
    }

    // ---------------------------------------------------------------- 扫描

    private final ScanCallback scanCb = new ScanCallback() {
        @Override
        public void onScanResult(int callbackType, ScanResult result) {
            if (result == null || result.getDevice() == null) return;
            BluetoothDevice d = result.getDevice();
            String addr = d.getAddress() == null ? "" : d.getAddress().replace(":", "").toUpperCase();
            String name = "";
            try {
                if (result.getScanRecord() != null) name = result.getScanRecord().getDeviceName();
            } catch (Throwable ignored) {
            }
            if (name == null || name.isEmpty()) {
                try {
                    name = d.getName();
                } catch (Throwable ignored) {
                }
            }
            if (name == null) name = "";

            boolean hit;
            if (!wantMac.isEmpty()) {
                hit = wantMac.equalsIgnoreCase(addr);
                // 给了 MAC 也用名字兜一次：部分手机上报的地址带随机位。
                if (!hit && !wantName.isEmpty()) hit = wantName.equalsIgnoreCase(name.trim());
            } else {
                hit = wantName.equalsIgnoreCase(name.trim());
            }
            if (!hit) return;

            stopScan();
            clearScanTimer();
            connect(d);
        }

        @Override
        public void onScanFailed(int errorCode) {
            clearScanTimer();
            fail("扫描失败（" + errorCode + "）");
        }
    };

    private void startScan() {
        try {
            ScanSettings st = new ScanSettings.Builder()
                    .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build();
            scanner.startScan(null, st, scanCb);
        } catch (Throwable t) {
            fail("开不了扫描：" + t);
            return;
        }
        scanTimer = new Runnable() {
            @Override
            public void run() {
                stopScan();
                fail("8 秒内没扫到这台水表 —— 站近一点，或确认表上有电");
            }
        };
        ui.postDelayed(scanTimer, SCAN_MS);
    }

    private void stopScan() {
        if (scanner == null) return;
        try {
            scanner.stopScan(scanCb);
        } catch (Throwable ignored) {
        }
    }

    private void clearScanTimer() {
        if (scanTimer != null) {
            ui.removeCallbacks(scanTimer);
            scanTimer = null;
        }
    }

    // ---------------------------------------------------------------- 连接

    private void connect(final BluetoothDevice device) {
        try {
            gatt = device.connectGatt(ctx, false, gattCb, BluetoothDevice.TRANSPORT_LE);
        } catch (Throwable t) {
            fail("连接失败：" + t);
            return;
        }
        if (gatt == null) {
            fail("连接失败：系统没给连接句柄");
            return;
        }
        clearConnectTimer();
        connectTimer = new Runnable() {
            @Override
            public void run() {
                if (ready) return;
                retry(device);
            }
        };
        ui.postDelayed(connectTimer, CONNECT_MS);
    }

    /** 连不上就重来（对齐 legacy：3 次，间隔 1 秒）。 */
    private void retry(final BluetoothDevice device) {
        clearConnectTimer();
        if (closedByUs) return;
        if (attempts + 1 >= RETRY) {
            attempts++;
            fail("连不上水表（试了 " + attempts + " 次）—— 可能已被别的手机占用");
            return;
        }
        attempts++;
        if (gatt != null) {
            try {
                gatt.close();
            } catch (Throwable ignored) {
            }
            gatt = null;
        }
        retryTask = new Runnable() {
            @Override
            public void run() {
                retryTask = null;
                if (!closedByUs) connect(device);
            }
        };
        ui.postDelayed(retryTask, 1000);
    }

    private void clearConnectTimer() {
        if (connectTimer != null) {
            ui.removeCallbacks(connectTimer);
            connectTimer = null;
        }
    }

    private final BluetoothGattCallback gattCb = new BluetoothGattCallback() {
        @Override
        public void onConnectionStateChange(BluetoothGatt g, int status, int newState) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                try {
                    g.discoverServices();
                } catch (Throwable t) {
                    fail("发现服务失败：" + t);
                }
                return;
            }
            if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                clearConnectTimer();
                if (closedByUs) return;
                if (!ready) {
                    retry(g.getDevice());
                    return;
                }
                // U95②：连上之后**意外掉线**（不是我们主动关），原来什么都不做，
                // JS 只能干等到 12 秒超时才知道出事。这里立刻报错，让 JS 走收尾
                // （关水 / 提示），少等十几秒，也不会误报成「开水成功」。
                ready = false;
                fail("蓝牙中途断了 —— 站近一点，别锁屏，再试一次");
            }
        }

        @Override
        public void onServicesDiscovered(BluetoothGatt g, int status) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                retry(g.getDevice());
                return;
            }
            BluetoothGattService svc = null;
            List<BluetoothGattService> list = g.getServices();
            if (list != null) {
                for (BluetoothGattService s : list) {
                    if (SVC.equals(s.getUuid())) {
                        svc = s;
                        break;
                    }
                }
            }
            if (svc == null) {
                fail("这块表不是住理的（没找到 ff12 服务）");
                return;
            }
            chWrite = svc.getCharacteristic(CH_WRITE);
            chNotify = svc.getCharacteristic(CH_NOTIFY);
            if (chWrite == null || chNotify == null) {
                fail("水表的读写通道不完整 —— 可能固件不兼容");
                return;
            }
            try {
                g.setCharacteristicNotification(chNotify, true);
                BluetoothGattDescriptor d = chNotify.getDescriptor(CCCD);
                if (d == null) {
                    fail("水表不接受通知订阅");
                    return;
                }
                if (Build.VERSION.SDK_INT >= 33) {
                    g.writeDescriptor(d, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
                } else {
                    d.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
                    g.writeDescriptor(d);
                }
            } catch (Throwable t) {
                fail("订阅通知失败：" + t);
            }
        }

        @Override
        public void onDescriptorWrite(BluetoothGatt g, BluetoothGattDescriptor d, int status) {
            if (!CCCD.equals(d.getUuid())) return;
            if (status != BluetoothGatt.GATT_SUCCESS) {
                fail("订阅通知失败");
                return;
            }
            ready = true;
            clearConnectTimer();
            JSONObject o = base("ready");
            emit(o);
        }

        @Override
        public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic ch) {
            if (Build.VERSION.SDK_INT < 33) {
                handleFrame(ch.getValue());
            }
        }

        @Override
        public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic ch,
                                            byte[] value) {
            handleFrame(value);
        }
    };

    // ---------------------------------------------------------------- 回帧

    private void handleFrame(byte[] v) {
        if (v == null || v.length < 3) return;
        if (!CH_NOTIFY.equals(chNotify == null ? null : chNotify.getUuid())) return;
        int type = v[2] & 0xff;
        if (type == CRC_TYPE) {
            gotFrame = true;
            clearReplyTimer();
            JSONObject o = base("crc");
            emit(o);
            return;
        }
        for (int t : wantTypes) {
            if (t == type) {
                gotFrame = true;
                clearReplyTimer();
                JSONObject o = base("frame");
                try {
                    o.put("hex", bytesToHex(v));
                    o.put("type", type);
                } catch (Throwable ignored) {
                }
                emit(o);
                return;
            }
        }
        // 其它类型（费率 / 旧订单 / 心跳）不喂给调用方，继续等。
    }

    private void armReplyTimer() {
        clearReplyTimer();
        replyTimer = new Runnable() {
            @Override
            public void run() {
                if (gotFrame) return;
                JSONObject o = base("error");
                try {
                    o.put("msg", "水表 6 秒内没回话");
                } catch (Throwable ignored) {
                }
                emit(o);
            }
        };
        ui.postDelayed(replyTimer, REPLY_MS);
    }

    private void clearReplyTimer() {
        if (replyTimer != null) {
            ui.removeCallbacks(replyTimer);
            replyTimer = null;
        }
    }

    // ---------------------------------------------------------------- 工具

    private JSONObject base(String ev) {
        JSONObject o = new JSONObject();
        try {
            o.put("ev", ev);
            o.put("req", reqId);
        } catch (Throwable ignored) {
        }
        return o;
    }

    private void fail(String msg) {
        JSONObject o = base("error");
        try {
            o.put("msg", msg);
        } catch (Throwable ignored) {
        }
        emit(o);
        close("", false);
    }

    private void emit(final JSONObject o) {
        final String s = o.toString();
        ui.post(new Runnable() {
            @Override
            public void run() {
                try {
                    web.evaluateJavascript("window.__zlBle&&window.__zlBle(" + s + ");", null);
                } catch (Throwable ignored) {
                }
            }
        });
    }

    private static int[] parseInts(String s) {
        if (s == null || s.trim().isEmpty()) return new int[0];
        String[] parts = s.split(",");
        int[] out = new int[parts.length];
        int n = 0;
        for (String p : parts) {
            try {
                out[n++] = Integer.parseInt(p.trim());
            } catch (Throwable ignored) {
            }
        }
        if (n == parts.length) return out;
        int[] cut = new int[n];
        System.arraycopy(out, 0, cut, 0, n);
        return cut;
    }

    private static byte[] hexToBytes(String hex) {
        if (hex == null) return null;
        String s = hex.replace(" ", "").replace("\n", "").trim();
        if (s.length() == 0 || s.length() % 2 != 0) return null;
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) {
            int hi = Character.digit(s.charAt(i * 2), 16);
            int lo = Character.digit(s.charAt(i * 2 + 1), 16);
            if (hi < 0 || lo < 0) return null;
            out[i] = (byte) ((hi << 4) | lo);
        }
        return out;
    }

    private static String bytesToHex(byte[] b) {
        StringBuilder sb = new StringBuilder(b.length * 2);
        for (byte x : b) {
            int v = x & 0xff;
            if (v < 16) sb.append('0');
            sb.append(Integer.toHexString(v));
        }
        return sb.toString();
    }
}
