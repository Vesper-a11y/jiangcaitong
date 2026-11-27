"""把 mobile/ 打包成 APK「酱菜通 0.1.0」。

不用 Gradle —— 本机没有 Android Studio，而且这点代码用不上它。
走官方那套命令行：aapt2 → javac → d8 → zipalign → apksigner。

前置（都在 _lab/out/sdk/ 下；2026-11-23 起用 sdkmanager 装，标准布局）：
    build-tools/34.0.0/            aapt2 / d8 / zipalign / apksigner
    platforms/android-33/android.jar
    JDK 17                        没有就退回系统的 JAVA_HOME
    （早期手工摆的扁平布局 android-14/ + android-13/android.jar 也照样认）

签名证书：_lab/out/keystore/jiangcaitong.keystore（不进仓库，见 .gitignore）。
🔴 这把钥匙丢了以后就只能卸载重装 —— 换钥匙的包装不上去，数据全没。

用法：
    python build_apk.py                # 打包
    python build_apk.py --clean        # 先清 build/ 再打
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent          # mobile/android
MOBILE = HERE.parent                            # mobile
WEB = MOBILE / "web"
BUILD = HERE / "build"
DIST = HERE / "dist"

# Android SDK / JDK 的位置。本机放在工作区的 _lab/out/sdk 下，**不随仓库分发**。
# 🔴 这里以前写的是本机绝对路径（带用户目录的那种）。公开仓库里带着用户名
#    既没意义（别人用不了这个路径），也没必要露。现在按「环境变量 → 仓库相对位置」找。
def _sdk_dir():
    env = os.environ.get("HYT_SDK")
    if env:
        return Path(env)
    # 2026-11-23：两个位置都认 —— 仓库里的 _lab/（现在这么装，_lab 已进 .gitignore）
    # 和工作区上一层的 _lab/（原来的装法）。
    for near in (MOBILE / "_lab" / "out" / "sdk",
                 MOBILE.parent / "_lab" / "out" / "sdk"):
        if near.exists():
            return near
    sys.exit("找不到 Android SDK：把 HYT_SDK 指到 build-tools 那层，"
             "或者按文件开头「前置」里说的放到 _lab/out/sdk")

SDK = _sdk_dir()


def _first(*cands: Path) -> Path:
    """第一个存在的；都不存在就返回第一个（让 check_env 去报缺什么）。"""
    for c in cands:
        if c.exists():
            return c
    return cands[0]


# SDK 的两种摆法都认：
#   ① sdkmanager 装出来的标准布局（2026-11-23 起本机就是这种）
#   ② 早期手工摆的扁平布局（build-tools 整个摊在 android-14/ 下）
BT = _first(SDK / "build-tools" / "34.0.0", SDK / "android-14")
ANDROID_JAR = _first(SDK / "platforms" / "android-33" / "android.jar",
                     SDK / "android-13" / "android.jar")

# JDK 17：优先用 SDK 里那份（原项目的摆法），没有就退回系统的 JAVA_HOME。
_jdk_cands = [SDK / "zulu17.60.17-ca-jdk17.0.16-win_x64"]
if os.environ.get("JAVA_HOME"):
    _jdk_cands.append(Path(os.environ["JAVA_HOME"]))
JDK = _first(*_jdk_cands)

JAVA = JDK / "bin" / "java.exe"
JAVAC = JDK / "bin" / "javac.exe"
KEYTOOL = JDK / "bin" / "keytool.exe"

# 国内线路（腾讯云 CNB）上这个包的地址前缀 —— 换它等于换一条下载线路，
# 只在这里写一次，version.json 和发布页都从这儿派生。
CNB_APK_BASE = ("https://maven.cnb.cool/Hobson2233/campus-login"
                "/-/packages/cnb/jiangcaitong")
CNB_APK_NAME = "酱菜通.apk"

# 🔴 2026-11-23 起，版本号**只在这里写一次**。
#    以前还要手工同步 web/app.js 的两行和 MainActivity 的 UA，漏一处就打不出包。
#    现在 sync_version() 会在打包前把这两处按这里的值重写（见那个函数的注释）。
VERSION_NAME = "1.0.1"
VERSION_CODE = 2
PKG = "com.jxufe.jiangcaitong"

# 2026-11-01：`--debug` 让 APK 带 debuggable=true（aapt2 的 --debug-mode）。
# 只为一件事：真机上开 WebView 远程调试，量界面尺寸。
# 🔴 打包时的 versionCode 与正式版**保持一致** —— 高了以后装不回正式包，
#    同签名同 code 才能覆盖安装并保留账号数据。
DEBUG_BUILD = False
MIN_SDK = 23
TARGET_SDK = 33

KEYSTORE = SDK.parent / "keystore" / "jiangcaitong.keystore"
KS_PASS = os.environ.get("JCT_KS_PASS", "jiangcaitong")


def run(cmd, cwd=None, quiet=False):
    """跑一条命令。🔴 用 subprocess 而不是 shell 拼串 —— 路径里有空格，
    拼串在 Windows 上会被拆错。"""
    if not quiet:
        print("  $ " + " ".join(str(c) for c in cmd[:3]) + (" …" if len(cmd) > 3 else ""))
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                       capture_output=True)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    if p.returncode != 0:
        print(out[-4000:])
        raise SystemExit("命令失败（%d）：%s" % (p.returncode, cmd[0]))
    return out


def check_env():
    for f in (JAVA, JAVAC, BT / "aapt2.exe", BT / "zipalign.exe",
              BT / "lib" / "d8.jar", BT / "lib" / "apksigner.jar", ANDROID_JAR):
        if not f.exists():
            raise SystemExit("缺文件：%s\n（先跑 _lab/out/sdk 的下载步骤）" % f)


def read_beta_seq() -> int:
    """测试版序号（v1 / v2 …）。从 app.js 读 —— 和版本号用同一个来源。

    0 = 正式版。测试版一整个周期挂在同一个版号上（0.49.2），
    只靠序号区分「这是第几版」，所以文件名里必须带上它，
    否则 dist/ 里的包会互相覆盖，出事时找不到用户装的是哪一版。
    """
    js = (WEB / "app.js").read_text(encoding="utf-8")
    m = re.search(r"^const APP_BETA_SEQ = (\d+);", js, re.M)
    return int(m.group(1)) if m else 0


def sync_version():
    """把版本号从本文件写进另外两个地方（打包前自动做，不用再手工改）。

    🔴 2026-11-23 改的：原来是「四处必须手工一致，对不上就拒绝打包」。
       可「检查更新」模块已经整块删掉了 —— version.json 没人读、正式/测试渠道
       也没有意义了。那道门槛原来挡的是「设置里显示 0.1.0、装上去是 0.1.1」，
       现在改成**脚本自己写对**：既不会不一致，也不必每次出包改四个文件。

    写两处：
      1. web/app.js 的 APP_VERSION / APP_CODE / APP_CHANNEL（界面自己读它）
      2. MainActivity 的 UA 尾巴 `JiangCaiTong/<版本>`（服务端日志用它分辨版本）
    """
    changed = []

    p = WEB / "app.js"
    js = p.read_text(encoding="utf-8")
    js2 = re.sub(r"^const APP_VERSION = '[^']+';",
                 "const APP_VERSION = '%s';" % VERSION_NAME, js, count=1, flags=re.M)
    js2 = re.sub(r"^const APP_CODE = \d+;",
                 "const APP_CODE = %d;" % VERSION_CODE, js2, count=1, flags=re.M)
    js2 = re.sub(r"^const APP_CHANNEL = '[a-z]+';",
                 "const APP_CHANNEL = 'stable';", js2, count=1, flags=re.M)
    if js2 != js:
        p.write_text(js2, encoding="utf-8")
        changed.append("web/app.js")

    p = HERE / "src" / "com" / "jxufe" / "jiangcaitong" / "MainActivity.java"
    java = p.read_text(encoding="utf-8")
    java2 = re.sub(r'(UA_TAG = " JiangCaiTong/)[\d.]+(")',
                   r"\g<1>%s\g<2>" % VERSION_NAME, java, count=1)
    if java2 != java:
        p.write_text(java2, encoding="utf-8")
        changed.append("MainActivity.java")

    # 3. harmony/AppScope/app.json5（U88 加的）：之前要手工跟着改，容易漏
    #    （有几次鸿蒙版本号落后于 APK，幸亏那个包还没发）。
    hp = HERE.parent / "harmony" / "AppScope" / "app.json5"
    if hp.exists():
        h5 = hp.read_text(encoding="utf-8")
        h52 = re.sub(r'"versionCode":\s*\d+', '"versionCode": %d' % VERSION_CODE, h5)
        h52 = re.sub(r'"versionName":\s*"[^"]+"', '"versionName": "%s"' % VERSION_NAME, h52)
        if h52 != h5:
            hp.write_text(h52, encoding="utf-8")
            changed.append("harmony/app.json5")

    if changed:
        print("  版本号已同步到：%s" % "、".join(changed))


def clean_dist(keep=3, protect=None):
    """dist 里只留最近 keep 个版本的 APK（+.idsig），其余删掉（U88）。

    到 1.3.24 为止已经堆了 80 多个文件、约 47 MB —— 全是历史版本，
    手机上装的是最新那份，旧的没有任何消费者。按版本号排序（字符串排序
    对 1.3.10 这种两段号会乱，所以拆成整数元组），留最大的 keep 个。
    🔴 只删 `jiangcaitong-*.apk` 和它的 `.idsig`，别的文件一律不碰。
    🔴 2026-11-25：`protect` 是本次刚打出来的版本号（如 (1,0,0)）。
       版本重置时新版本号比历史包小（1.0.0 < 1.5.4），按「留最大的」排序
       会把**刚打出来的包**当成最旧的删掉 —— 首发版本发行时包直接消失。
       所以本次版本无条件保留，只清比它老的其它历史包。
    """
    dist = HERE / "dist"
    if not dist.exists():
        return
    pat = re.compile(r"^jiangcaitong-(\d+)\.(\d+)\.(\d+)\.apk(?:\.idsig)?$")
    groups = {}
    for f in dist.iterdir():
        m = pat.match(f.name)
        if not m:
            continue
        key = tuple(int(x) for x in m.groups())
        groups.setdefault(key, []).append(f)
    keys = sorted(groups)
    doomed = keys[:-keep] if len(keys) > keep else []
    if protect is not None:
        doomed = [k for k in doomed if k != tuple(protect)]
    for key in doomed:
        for f in groups[key]:
            try:
                f.unlink()
                print("  清理旧包：%s" % f.name)
            except OSError as e:
                print("  清不掉 %s：%s（留着，不影响打包）" % (f.name, e))


def check_version_consistency():
    """写完再读一遍 —— 防手滑，也防哪天有人把 sync_version 绕过去。

    核对（都只在打包前，几毫秒）：
    1. app.js 的 APP_VERSION / APP_CODE == 本文件的 VERSION_NAME / VERSION_CODE
    2. MainActivity 的 User-Agent 后缀 == VERSION_NAME
    3. strings.xml 里的 app_name（不含版本，但顺手确认没被改成别的名字）
    4. index.html 里 id="meVersion" 那个占位元素还在（界面靠它显示版本）

    ⚠️ 原来这里还核 version.json 和「渠道」，2026-11-23 一起删了：
       检查更新已经不存在，那份清单和正式/测试渠道都没有任何消费者了。
    """
    bad = []

    js = (WEB / "app.js").read_text(encoding="utf-8")
    m = re.search(r"^const APP_VERSION = '([^']+)';", js, re.M)
    got = m.group(1) if m else "(找不到)"
    if got != VERSION_NAME:
        bad.append("app.js 的 APP_VERSION 是 %s，应为 %s" % (got, VERSION_NAME))
    m = re.search(r"^const APP_CODE = (\d+);", js, re.M)
    gotc = int(m.group(1)) if m else -1
    if gotc != VERSION_CODE:
        bad.append("app.js 的 APP_CODE 是 %s，应为 %d" % (gotc, VERSION_CODE))

    java = (HERE / "src" / "com" / "jxufe" / "jiangcaitong" / "MainActivity.java").read_text(encoding="utf-8")
    m = re.search(r"JiangCaiTong/([\d.]+)", java)
    got = m.group(1) if m else "(找不到)"
    if got != VERSION_NAME:
        bad.append("MainActivity 的 UA 后缀是 %s，应为 %s" % (got, VERSION_NAME))

    strings = (HERE / "res" / "values" / "strings.xml").read_text(encoding="utf-8")
    if 'name="app_name">酱菜通<' not in strings:
        bad.append("strings.xml 里的 app_name 不是「酱菜通」")

    # 界面里的版本那一行还在不在（不写死数字，但占位元素不能被删）
    html = (WEB / "index.html").read_text(encoding="utf-8")
    if 'id="meVersion"' not in html:
        bad.append("index.html 里没有 id=meVersion（「我的」页显示版本那一行）")

    if bad:
        print("  [!!] 版本/名称不一致：")
        for b in bad:
            print("       " + b)
        raise SystemExit("先统一版本号再打包（这是有意的门槛，别跳过）")
    print("  版本一致性 OK（%s / code %d）" % (VERSION_NAME, VERSION_CODE))


def read_notes() -> list[str]:
    """更新内容：mobile/RELEASE-NOTES.md 里 **本版本那一节** 的「- 」行。

    单独一个文件而不是写在代码里：发版时只想改文案，不想碰打包脚本。
    🔴 只取 `## <当前版本>` 到下一个 `##` 之间的行 —— 不然历史版本的
       更新内容会一起被塞进弹窗，用户会看到一堆早就装上的旧改动。
    """
    p = MOBILE / "RELEASE-NOTES.md"
    if not p.exists():
        return []
    out = []
    inside = False
    # 🔴 0.47.2：`<!-- 细节：… -->` 这种注释块是写给维护者的，不是弹窗内容。
    #    原来的规则是「本段里出现任何非 `- ` 的非空行就报错」，注释块正好撞在
    #    上面 —— 于是**当前版本只要写了细节注释就打不出包**（0.47.2 实测：
    #    第 29 行 `<!-- 细节：` 直接 SystemExit，而 APK 已经打好了，
    #    只有 version.json 的 size/sha256 没跟着更新 —— 清单和包对不上，
    #    下载页给的校验值会是上一版的）。
    #    这里跳过注释块；注释块**外面**的折行照旧报错，那条门槛一点不松。
    in_comment = False
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        if s.startswith("<!--"):
            # 单行注释 `<!-- … -->` 与多行注释块都认
            if "-->" not in s:
                in_comment = True
            continue
        if s.startswith("## "):
            inside = (s[3:].strip().split()[0] if s[3:].strip() else "") == VERSION_NAME
            continue
        if inside and s.startswith("- "):
            out.append(s[2:].strip())
            continue
        # 🔴 本版本段落里出现「既不是标题也不是 - 开头」的非空行 ⇒ 报错。
        #    这条是踩出来的：把一条更新说明折成两行写，第二行会被静默丢掉，
        #    用户看到的弹窗就少半句话（"……打印中心这些，" 后面直接没了）。
        #    静默截断比写错更坏 —— 写错能看出来，截断看着像「本来就这么多」。
        if inside and s:
            raise SystemExit(
                "RELEASE-NOTES.md 第 %d 行：\n    %s\n"
                "这一行会被丢掉（只收以「- 」开头的行）。\n"
                "更新说明一条一行，别折行 —— 折了用户看到的弹窗就少半句。" % (lineno, line))
    return out


def write_manifest(apk: Path):
    """把 version.json 里的「事实」部分按这个 APK 重写。

    为什么由打包脚本写：size 和 sha256 只能从真实的包算，
    手填迟早会出现「清单里的 sha256 和下载到的文件对不上」。
    notes / 下载地址这些文案性的东西留在 version.json 里，这里只覆盖事实。
    """
    mf = MOBILE / "version.json"
    info = {}
    if mf.exists():
        try:
            info = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            info = {}
    raw = apk.read_bytes()
    info["name"] = "酱菜通"
    info["version"] = VERSION_NAME
    info["code"] = VERSION_CODE
    info["size"] = len(raw)
    info["sha256"] = hashlib.sha256(raw).hexdigest()
    # 🔴 打包当天。以前**根本没人写这个字段** —— 它一路沿用上一版那天的值，
    #    于是今天打的包，清单里写着上一版的日期。字段在、值不对、零报错。
    #    （2026-10-27 南师侧在他那边翻出来的，同一个形状；我这边也一样，
    #      上一版 0.17.3 的日期是我手工改的，不是脚本写的。）
    info["date"] = datetime.date.today().isoformat()
    # 放在 /dl/ 子目录下：_headers 只支持尾部通配（/jiangcaitong/dl/*），
    # 放在 /jiangcaitong/ 根上就没法给 .apk 单独一条长缓存规则。
    info["apk"] = ("https://hobson.cc/jiangcaitong/dl/jiangcaitong-%s.apk"
                   % VERSION_NAME)
    # 国内线路（腾讯云 CNB）。和上面那条是**同一个包**，只是线路不同 ——
    # 主站走 Cloudflare，国内下起来慢，App 的「立即更新」优先用这条。
    # ⚠️ 这条地址要求**已经用 upload_cnb.py 传过这个版本**（见 README「发版顺序」）。
    # ⚠️ 拼的时候用 + 不用 %：`quote("酱菜通.apk")` 的结果里全是 `%E8%88%AA…`，
    #    拿去 `%` 格式化会被当成格式符，报 "not enough arguments for format string"。
    info["apk_cn"] = (CNB_APK_BASE + "/" + VERSION_NAME + "/"
                      + urllib.parse.quote(CNB_APK_NAME))
    info["page"] = "https://hobson.cc/jiangcaitong/"
    notes = read_notes()
    if notes:
        info["notes"] = notes
    # 顺序固定，diff 好看
    order = ["name", "version", "code", "date", "size", "sha256",
             "apk", "apk_cn", "page", "notes"]
    out = {k: info[k] for k in order if k in info}
    for k in info:
        if k not in out:
            out[k] = info[k]
    mf.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                  encoding="utf-8", newline="\n")
    print("  version.json 已更新（%s / code %d / %d 字节）"
          % (VERSION_NAME, VERSION_CODE, len(raw)))


def write_beta_manifest(apk: Path):
    """测试版只写 mobile/beta.json —— **绝不碰 version.json**。

    version.json 是所有人「检查更新」读的那份清单。测试版写进去的那一刻，
    它就等于被发布了。

    2026-11-07 起这份清单**要发到线上去**（藏在 /jiangcaitong/beta/ 下，
    页面上没有任何链接，只有测试版 App 知道地址）。所以它得和 version.json
    长得一样能直接拿来下载：apk / size / sha256 缺一不可。

    🔴 `seq` 也要带上：同一版号下有几个包，清单里没有序号的话，
       「手上这个是第几版」就只能靠猜。
    """
    mf = MOBILE / "beta.json"
    raw = apk.read_bytes()
    info = {
        "name": "酱菜通",
        "channel": "beta",
        "version": VERSION_NAME,
        "code": VERSION_CODE,
        # 同一版号下的第几版（v1 / v2 …）。不写进去的话，几份 beta.json 放一起
        # 只按 version 看是一模一样的，对不出「手上这个到底是哪一版」。
        "seq": read_beta_seq(),
        "date": datetime.date.today().isoformat(),
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        # 下载地址固定指向隐藏目录。文件名带版本号和序号，所以永远不会互相覆盖。
        "apk": "https://hobson.cc/jiangcaitong/beta/%s" % apk.name,
        "notes": read_notes(),
    }
    mf.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n",
                  encoding="utf-8", newline="\n")
    print("  beta.json 已更新（%s v%d / code %d / %d 字节）—— "
          "**没有**写 version.json，正式版用户看不到它"
          % (VERSION_NAME, read_beta_seq(), VERSION_CODE, len(raw)))


STATS_KEY_PLACEHOLDER = "const STATS_KEY = '';"

# 数据台的钥匙**不在这个仓库里**（`mobile/` 是公开仓库，CNB 上匿名可 clone）。
# 它只在工作区的 deploy/stats_key.txt（gitignore）里，打包时读一次塞进包里。
# 找不到就成了「测试版数据台永远 403」，而这种坏法页面上看不出来，所以下面硬拦。
# 2026-11-13 起这一行只在 web/stats.js 里（控制台拆成了独立文件，正式版不进包）。
STATS_KEY_FILE = MOBILE.parent / "deploy" / "stats_key.txt"

# 测试版那一层的口令（2026-11-10）。和钥匙同一套做法：
# 源码留空，打测试版时才从 deploy/beta_key.txt（gitignore）读出来塞进去。
BETA_KEY_PLACEHOLDER = "const BETA_KEY = '';"
BETA_KEY_FILE = MOBILE.parent / "deploy" / "beta_key.txt"


def strip_stats_key(text):
    """把钥匙抹空。**源码里**该是这个状态，测试版的包里由 inject 补回来。

    2026-11-10 才发现：0.49.0 正式版和 v7 测试版的 app.js 里是同一把，
    而且这行一直躺在**公开仓库**里（谁 clone 谁有）。
    2026-11-13 之后正式版连 stats.js 都不进包，这里留着是**纵深**：
    测试版注入前先抹一遍，源码里万一又被谁写回一把，包里的只会是工作区那把。
    """
    return re.sub(r"^const STATS_KEY = '[^']*';",
                  STATS_KEY_PLACEHOLDER, text, flags=re.M)


def inject_stats_key(text):
    """测试版：把 deploy/stats_key.txt 里那把塞进 stats.js。返回改过的文本。

    🔴 读不到就直接停，不许打出一个「能装、但数据台永远没数据」的包 ——
       那种包和好包长得一模一样，只有点开那一页才知道是 403。
    """
    try:
        key = STATS_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        raise SystemExit("测试版要带数据台的钥匙，但读不到 %s"
                         % STATS_KEY_FILE)
    if not key:
        raise SystemExit("%s 是空的，测试版的数据台会一直 403"
                         % STATS_KEY_FILE)
    if STATS_KEY_PLACEHOLDER not in text:
        raise SystemExit("app.js 里没有 `%s` 这一行，注入不了"
                         % STATS_KEY_PLACEHOLDER)
    return text.replace(STATS_KEY_PLACEHOLDER,
                        "const STATS_KEY = '%s';" % key, 1)


def inject_beta_key(text):
    """测试版：把 `deploy/beta_key.txt` 里那一串塞进 app.js。

    🔴 读不到就停。不发一个「装得上、但再也检查不到新版本」的测试包 ——
       那种包只会让人以为「测试版通道坏了」，然后回头重装正式版。

    正式版走 strip：一句话都不带（正式版根本不请求 /beta/ 那条路径）。
    """
    try:
        key = BETA_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        raise SystemExit("测试版要带测试通道的口令，但读不到 %s" % BETA_KEY_FILE)
    if not key:
        raise SystemExit("%s 是空的，测试版会检查不到更新" % BETA_KEY_FILE)
    if BETA_KEY_PLACEHOLDER not in text:
        raise SystemExit("app.js 里没有 `%s` 这一行，注入不了" % BETA_KEY_PLACEHOLDER)
    return text.replace(BETA_KEY_PLACEHOLDER,
                        "const BETA_KEY = '%s';" % key, 1)


def channel():
    """app.js 里写着的渠道。打包和判据都以它为准，不另立一套判断。"""
    js = (WEB / "app.js").read_text(encoding="utf-8")
    m = re.search(r"^const APP_CHANNEL = '([a-z]+)';", js, re.M)
    if not m:
        raise SystemExit("web/app.js 里找不到 APP_CHANNEL")
    return m.group(1)


def strip_console_html(text):
    """剪掉 index.html 里被 `<!-- @stats -->` 包着的几段。

    2026-11-13（用户：「把控制台拆出来发正式版，不要让控制台的令牌流入正式版」）：
    控制台不再是「装进去靠 STATS_ON 藏起来」，正式版的包里**没有**这一页 ——
    入口按钮、`#page-stats`、stats.css 的 <link>、stats.js 的 <script> 四段
    都在标记里，一次剪干净。

    幂等：剪过一遍再剪还是它（标记都没了）。
    """
    return re.sub(r"[ \t]*<!-- @stats -->.*?<!-- /@stats -->\n?",
                  "", text, flags=re.S)


# 控制台那两个文件。正式版不复制，测试版复制之后再把钥匙注入 stats.js。
CONSOLE_FILES = ("stats.js", "stats.css")

# 正式版里**一个字节都不许有**的东西：钥匙头的名字、那个接口、接口常量，
# 以及那一页的入口/页面本身（`@stats` 片段没剪干净时它们会留下来）。
# 🔴 别拿 "STATS_KEY" 去搜 —— app.js 里还有一段注释提到这个名字（讲设备码
#    那处「和 STATS_KEY 同一套做法」），搜它会把好包判坏。
CONSOLE_LEAK_RE = re.compile(r"x-hyt-key|hobson\.cc/stats|const\s+STATS_API"
                             r"|statsBtn|page-stats|statsbtn")


def check_console(dst, beta):
    """打完自己查一遍：控制台该在的都在，该没有的一个字节都没有。

    🔴 这条是硬线，不是「顺便看看」：钥匙漏进正式版是 2026-11-10 真发生过的事，
       而且漏出去之后页面上一点异常都没有（正式版根本不显示那一页）。
       以前拦它靠「抹成空串」，漏掉一个地方就看不见；现在靠「文件在不在」。
    """
    if beta:
        for need in CONSOLE_FILES:
            if not (dst / need).exists():
                raise SystemExit("测试版里缺 %s（数据台会打不开）" % need)
        sj = (dst / "stats.js").read_text(encoding="utf-8")
        if "const STATS_KEY = '';" in sj:
            raise SystemExit("测试版的 stats.js 里钥匙还是空的（注入那步没跑到）")
        return
    left = sorted(f.name for f in dst.iterdir() if f.name in CONSOLE_FILES)
    if left:
        raise SystemExit("正式版的 assets/web 里还有控制台的文件：%s" % "、".join(left))
    for f in dst.iterdir():
        if f.suffix not in (".js", ".html", ".css"):
            continue
        s = f.read_text(encoding="utf-8", errors="ignore")
        if CONSOLE_LEAK_RE.search(s):
            raise SystemExit("正式版的 %s 里还留着控制台的接口/钥匙头" % f.name)
        if "@stats" in s:
            raise SystemExit("正式版的 %s 里还有没剪掉的 @stats 片段" % f.name)


def stage_assets():
    """把 web/ 整个搬进 assets/web/ —— APK 里的页面和网页版是同一份代码。

    唯一的例外是控制台（数据台）：正式版**不搬**它，还要把 index.html 里
    引着它的那几段剪掉（见 strip_console_html）。
    """
    dst = BUILD / "assets" / "web"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    ch = channel()
    beta = ch == "beta"
    for f in WEB.iterdir():
        if not f.is_file():
            continue
        if f.name in CONSOLE_FILES and not beta:
            continue                       # 正式版：控制台整个不进包
        shutil.copy2(f, dst / f.name)
    # 索引页里那四段（入口按钮 / 那一页 / 样式 / 脚本）一起剪掉。
    if not beta:
        p = dst / "index.html"
        p.write_text(strip_console_html(p.read_text(encoding="utf-8")),
                     encoding="utf-8", newline="")
        print("  正式版：控制台（stats.js / stats.css / 那一页）没有打进包里")
    # 渠道口令：正式版一句话都不带（正式版根本不请求 /beta/ 那条路径），
    # 测试版才从 deploy/beta_key.txt 读。
    js = (dst / "app.js").read_text(encoding="utf-8")
    if beta:
        out = inject_beta_key(js)
        print("  测试版：通道口令已注入（来自 deploy/beta_key.txt）")
        # 数据台的钥匙只在这一个文件里。先抹再注入：万一谁往源码里写过一把，
        # 这一步保证包里只有工作区那一把（`mobile/` 是公开仓库）。
        sp = dst / "stats.js"
        sj = inject_stats_key(strip_stats_key(sp.read_text(encoding="utf-8")))
        sp.write_text(sj, encoding="utf-8", newline="")
        print("  测试版：数据台钥匙已注入（来自 deploy/stats_key.txt）")
    else:
        out = js
        print("  正式版：通道口令为空")
    with open(dst / "app.js", "w", encoding="utf-8", newline="") as f:
        f.write(out)
    check_console(dst, beta)
    names = sorted(x.name for x in dst.iterdir())
    print("  assets/web: " + ", ".join(names))
    # 网页版那套 Python 服务不在包里，但页面里引用了它 —— 少个文件会白屏，
    # 所以这里硬检查一遍必需文件，别等到手机上才发现。
    for need in ("index.html", "app.js", "app.css", "native.js", "icon-180.png"):
        if not (dst / need).exists():
            raise SystemExit("assets/web 里缺 %s" % need)


def compile_res():
    gen = BUILD / "gen"
    if gen.exists():
        shutil.rmtree(gen)
    gen.mkdir(parents=True)
    res_zip = BUILD / "res.zip"
    if res_zip.exists():
        res_zip.unlink()

    run([BT / "aapt2.exe", "compile", "--dir", HERE / "res", "-o", res_zip])

    out_apk = BUILD / "base.apk"
    # ⚠️ 编译产物要当**位置参数**传，不能用 -R：-R 是「覆盖层（overlay）」语义，
    #    会要求每条资源都去覆盖一个已存在的定义，于是每条都报
    #    "does not override an existing resource"。
    run([BT / "aapt2.exe", "link",
         "-o", out_apk,
         "-I", ANDROID_JAR,
         "--manifest", HERE / "AndroidManifest.xml",
         "-A", BUILD / "assets",
         "--java", gen,
         "--min-sdk-version", str(MIN_SDK),
         "--target-sdk-version", str(TARGET_SDK),
         "--version-code", str(VERSION_CODE),
         "--version-name", VERSION_NAME,
         ] + (["--debug-mode"] if DEBUG_BUILD else []) + [
         res_zip])
    return out_apk, gen


def compile_java(gen):
    # 🔴 输出到**每次全新的临时目录**，不写 build/classes。
    #    0.49.0 打包时连续失败三次，都是同一个原因：工作区有一条
    #    「一次删掉超过 50 个文件要确认」的保护（按整轮对话累计算），
    #    而 build/classes 里正好 55 个 .class。整目录 rmtree 被拦之后，
    #    javac 的 -d 目录还在、里面还混着上一次的旧类，于是报
    #    「编译 Java 这一步失败」，而日志里一句 javac 报错都没有 ——
    #    看报错完全猜不到是删文件被拦了。
    #    换个新目录 = 根本不需要删，也就不可能被拦。
    #    ⚠️ 不清理临时目录：删也是 55 个文件，一样会被拦。系统自己会收。
    classes = Path(tempfile.mkdtemp(prefix="hyt-classes-"))

    srcs = sorted(str(p) for p in (HERE / "src").rglob("*.java"))
    srcs += sorted(str(p) for p in gen.rglob("*.java"))
    print("  javac: %d 个源文件" % len(srcs))
    # --release 8：安卓这边只需要 Java 8 语法；d8 对更高版本的字节码支持有限。
    # 🔴 -encoding utf-8 不能省：Windows 上 javac 默认按 GBK 读源文件，
    #    源码里的中文注释会让它报一串「不可映射字符」并编译失败。
    run([JAVAC, "--release", "8", "-nowarn", "-encoding", "utf-8",
         "-classpath", ANDROID_JAR, "-d", classes] + srcs)
    return classes


def dex(classes):
    out = BUILD / "dex"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # ⚠️ d8 不认「目录」这种输入（会报 Unsupported source file type），
    #    必须逐个 .class 传进去，或者先打成 jar。
    class_files = sorted(classes.rglob("*.class"))
    if not class_files:
        raise SystemExit("javac 没产出任何 .class")
    print("  待转 dex 的 class：%d 个" % len(class_files))
    run([JAVA, "-cp", BT / "lib" / "d8.jar", "com.android.tools.r8.D8",
         "--release", "--min-api", str(MIN_SDK), "--lib", ANDROID_JAR,
         "--output", out] + class_files)
    dx = out / "classes.dex"
    if not dx.exists():
        raise SystemExit("d8 没产出 classes.dex")
    print("  classes.dex %.1f KB" % (dx.stat().st_size / 1024))
    return dx


def add_dex(apk: Path, dx: Path) -> Path:
    """把 classes.dex 塞进 aapt2 产出的包里。
    （zipfile 追加即可；顺序不影响安装，zipalign 后面会统一对齐）"""
    with zipfile.ZipFile(apk, "a", zipfile.ZIP_DEFLATED) as z:
        z.write(dx, "classes.dex")
    return apk


def sign(apk: Path) -> Path:
    if not KEYSTORE.exists():
        KEYSTORE.parent.mkdir(parents=True, exist_ok=True)
        print("  生成签名密钥（只在本地，不进 git）：%s" % KEYSTORE)
        run([KEYTOOL, "-genkeypair", "-keystore", KEYSTORE,
             "-alias", "jiangcaitong", "-keyalg", "RSA", "-keysize", "2048",
             "-validity", "10000",
             "-storepass", KS_PASS, "-keypass", KS_PASS,
             "-dname", "CN=JiangCaiTong, OU=Mobile, O=JXUFE, L=Nanchang, ST=Jiangxi, C=CN"])

    aligned = BUILD / "aligned.apk"
    run([BT / "zipalign.exe", "-f", "4", apk, aligned])

    DIST.mkdir(parents=True, exist_ok=True)
    # 调试包单独一个名字：不能把正式包覆盖掉（正式包要传线上）
    # 测试版带上序号（-v2）：同一版号下会打很多版，不带序号会互相覆盖。
    seq = read_beta_seq()
    label = "-v%d" % seq if (seq > 0 and not DEBUG_BUILD) else ""
    out = DIST / ("jiangcaitong-%s%s%s.apk"
                  % (VERSION_NAME, label, "-debug" if DEBUG_BUILD else ""))
    if out.exists():
        out.unlink()
    run([JAVA, "-jar", BT / "lib" / "apksigner.jar", "sign",
         "--ks", KEYSTORE, "--ks-pass", "pass:" + KS_PASS,
         "--key-pass", "pass:" + KS_PASS,
         "--min-sdk-version", str(MIN_SDK),
         "--out", out, aligned])
    return out


def verify(apk: Path):
    print("\n校验")
    v = run([JAVA, "-jar", BT / "lib" / "apksigner.jar", "verify",
             "--print-certs", "--verbose", apk])
    for line in v.splitlines():
        if ("Verified using" in line or "Signer #1 certificate DN" in line
                or "SHA-256 digest" in line or "Number of signers" in line):
            print("  " + line.strip())

    badging = run([BT / "aapt2.exe", "dump", "badging", apk])
    for line in badging.splitlines():
        if (line.startswith("package:") or "application-label" in line
                or "sdkVersion" in line or "targetSdkVersion" in line
                or "launchable-activity" in line or "uses-permission" in line):
            print("  " + line.strip())

    # 关键内容在不在（装完打不开往往就是少了个 assets 文件）
    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
    need = ["classes.dex", "AndroidManifest.xml",
            "assets/web/index.html", "assets/web/app.js",
            "assets/web/native.js", "assets/web/app.css"]
    missing = [n for n in need if n not in names]
    print("  APK 内文件 %d 个" % len(names))
    if missing:
        raise SystemExit("APK 里缺：%s" % missing)
    print("  必需文件齐全（classes.dex / assets/web/*）")
    return apk


def main():
    global DEBUG_BUILD
    ap = argparse.ArgumentParser(description="打包 酱菜通 APK")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--beta", action="store_true",
                    help="打测试用的包（文件名带 vN 序号，方便区分）。"
                         "2026-11-23 起不再有「渠道门槛」：检查更新已删，"
                         "写不写 version.json 都不影响任何人")
    ap.add_argument("--manifest", action="store_true",
                    help="打完顺手重写 version.json（默认不写：App 已经不查更新了）")
    ap.add_argument("--debug", action="store_true",
                    help="打成 debuggable（真机上开 WebView 调试量尺寸用），"
                         "versionCode 不变，能覆盖安装并保留数据")
    args = ap.parse_args()
    DEBUG_BUILD = bool(args.debug)
    beta = bool(args.beta)

    check_env()
    # 版本号只写在本文件顶部，剩下两处打包前自动对齐（2026-11-23）
    sync_version()
    check_version_consistency()
    if args.clean and BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True, exist_ok=True)

    print("\n1/6 收网页资源")
    stage_assets()
    print("2/6 编译资源（aapt2）")
    apk, gen = compile_res()
    print("3/6 编译 Java")
    classes = compile_java(gen)
    print("4/6 转 dex（d8）")
    dx = dex(classes)
    print("5/6 打包 + 对齐 + 签名")
    add_dex(apk, dx)
    out = sign(apk)
    print("6/6 校验")
    verify(out)

    size = out.stat().st_size
    # 2026-11-23：检查更新模块删掉之后，version.json / beta.json 已经没有任何
    # 地方读它们了（App 不查更新，发布页也不再对外）。所以默认不写 ——
    # 少一步、也少一个「RELEASE-NOTES 里没有本版本那一节」的打不出包的理由。
    # 哪天真要对外发，加 --manifest 手动写。
    if DEBUG_BUILD:
        print("\n（调试包：不写任何清单）")
    elif args.manifest:
        if beta:
            write_beta_manifest(out)
        else:
            write_manifest(out)
    else:
        print("\n（跳过 version.json：检查更新已删，那份清单没人读；要写加 --manifest）")
    print("\n完成：%s（%.2f MB）" % (out, size / 1048576))
    print("  版本 %s（versionCode %d），包名 %s%s"
          % (VERSION_NAME, VERSION_CODE, PKG, "，测试版" if (beta and not DEBUG_BUILD) else ""))
    return 0


if __name__ == "__main__":
    rc = main()
    if rc == 0:
        # U88：清理历史 APK；2026-11-25 起保护本次版本（版本重置时新号比历史小）。
        vm = re.match(r"^(\d+)\.(\d+)\.(\d+)$", VERSION_NAME)
        clean_dist(protect=tuple(int(x) for x in vm.groups()) if vm else None)
    sys.exit(rc)
