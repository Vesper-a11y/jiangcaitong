# -*- coding: utf-8 -*-
"""鸿蒙 HAP 打包：拷网页资源 → hvigor 编译 → 签名。

用法（工作区根目录）：

    python mobile/harmony/build_hap.py             # 测试版：debug profile
    python mobile/harmony/build_hap.py --release   # 正式版：release profile + 正式渠道
    python mobile/harmony/build_hap.py --copy      # 只拷资源
    python mobile/harmony/build_hap.py --no-sign   # 编译，不签名

🔴 两种包差在哪（别以为只是文件名不同）：
   · profile 模板：UnsgnedReleased 的 type=release、证书字段是
     `distribution-certificate`、签名别名是 `… profile release`；
     调试版是 type=debug + `development-certificate` + `… profile debug`。
   · 网页资源：正式版把 rawfile 里那份 app.js 的渠道改成 stable、
     v 序号归零、code 取「下一个正式版号」；**源文件一个字都不改**
     （源里的渠道归 Android 的发版流程管）。

🔴 签名这件事要说清楚：本脚本用的是 **OpenHarmony 公版调试密钥**
   （SDK 自带的 `toolchains/lib/OpenHarmony.p12`），装得上 OpenHarmony
   设备/模拟器；**装不上零售版 HarmonyOS 手机** —— 那个必须用华为开发者
   账号签（DevEco 里 File → Project Structure → Signing Configs → Automatically）。
   脚本只做能做的，不假装签出华为的包。

依赖（脚本会自己找，找不到就明说缺什么）：
    SDK   : $OHOS_SDK_HOME 或 local.properties 的 sdk.dir（含 12/ets 12/toolchains…）
    JDK   : $JAVA_HOME（要 bin/java.exe，签名工具是 jar）
    hvigor: $HVIGOR_JS 或工作区里的 _lab/out/_hvigor（开发机现成的那套）
"""
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MOB = os.path.dirname(HERE)
ROOT = os.path.dirname(MOB)
WEB = os.path.join(MOB, "web")
RAW = os.path.join(HERE, "entry", "src", "main", "resources", "rawfile", "web")
SIGN = os.path.join(HERE, ".sign")
OUT = os.path.join(HERE, "entry", "build", "default", "outputs", "default")

# 网页资源里**不该进包**的：数据台的钥匙（Android 正式版也不带，见 MEMORY.md）。
SKIP = {"stats.js", "stats.css"}

# OpenHarmony 公版调试密钥的口令是公开的固定值，不是我们的秘密。
KEY_PWD = "123456"
KEY_ALIAS_APP = "openharmony application release"
# profile 的签名别名两套不一样（debug / release），写在 sign() 里按类型选 ——
# 提成常量再按参数挑，等于把「两套素材」这件事藏起来了一半。


def say(s):
    print("  " + s)


# 手写 code 的**唯一**口子（`--code N`）。默认永远是「算出来的下一个正式版号」。
# 🔴 什么时候才配用它：算出来的那个号**已经装在某台测试机上了**，而这一版
#    必须能覆盖上去 —— 同一个 code 再装一次，手机上只会报
#    `install already exist`，用户看到的是「装不上」，不是「装上了但坏」。
#    2026-11-14 就是这个情形：160 已经在用户的 Mate 70 Pro 上，修完登录要发的是 161。
CODE_OVERRIDE = None


# ---------------------------------------------------------------- 资源

def copy_web(release=False):
    """把 mobile/web 拷成 rawfile/web。每次全量覆盖 —— 增量拷贝会留下
    上一版删掉的文件，而这种残留只有装到手机上才会发现。

    release=True 时顺手把这份副本改成正式版渠道（见 stable_target）。"""
    if os.path.isdir(RAW):
        shutil.rmtree(RAW)
    os.makedirs(RAW)
    n = 0
    for name in os.listdir(WEB):
        src = os.path.join(WEB, name)
        if not os.path.isfile(src) or name in SKIP:
            continue
        shutil.copy2(src, os.path.join(RAW, name))
        n += 1
    # index.html 里那四段 `<!-- @stats -->` 也要剪掉：只删文件的话，
    # 页面还会去 load 一个不存在的 stats.js（404 不报错，但控制台脏）。
    idx = os.path.join(RAW, "index.html")
    if os.path.exists(idx):
        s = io.open(idx, encoding="utf-8").read()
        s2 = re.sub(r"<!-- @stats -->.*?<!-- /@stats -->", "", s, flags=re.S)
        io.open(idx, "w", encoding="utf-8", newline="").write(s2)
    tail = ""
    if release:
        ver, code = inject_stable(os.path.join(RAW, "app.js"))
        tail = "，改成正式版 %s / %d" % (ver, code)
    say("网页资源：%d 个文件 → rawfile/web（已剪掉控制台%s）" % (n, tail))


def stable_target(src_js):
    """正式版该用哪个版号 / code。

    🔴 不许手写。源里是 beta（0.52.2 / 159）时，正式版是**同一个版号**、
        code 取「比它和每个测试版都大 1」（160）—— 和 beta.py 的
        pick_code 是同一套算法，对不上就会出现「手机上装的是 160、
        清单写 161」，更新提示反复弹。
    🔴 源里**已经**是 stable（发版流程 set_ver 过了，正在打正式包）时
        直接用它，不能再 +1：重跑一次涨一格，而且全是没发出去的号。
    """
    ver = re.search(r"^const APP_VERSION = '([^']+)';", src_js, re.M)
    code = re.search(r"^const APP_CODE = (\d+);", src_js, re.M)
    ch = re.search(r"^const APP_CHANNEL = '([a-z]+)';", src_js, re.M)
    if not (ver and code and ch):
        raise SystemExit("app.js 里找不到 APP_VERSION / APP_CODE / APP_CHANNEL")
    if ch.group(1) == "stable":
        return ver.group(1), int(code.group(1))
    codes = [int(code.group(1))]
    p = os.path.join(MOB, "beta_log.json")
    if os.path.exists(p):
        d = json.load(io.open(p, encoding="utf-8"))
        codes.append(int((d.get("lastStable") or {}).get("code") or 0))
        codes += [int(b.get("code") or 0) for b in (d.get("betas") or [])]
    return ver.group(1), max(codes) + 1


def inject_stable(dst_js):
    """把 rawfile 里那份 app.js 改成正式版渠道。

    🔴 只改**拷贝出来的那一份**，不动 mobile/web/app.js —— 源里的渠道
       归 Android 的发版流程（beta.py release 的 set_ver）管，在这里顺手
       改掉会让两边的号各说各话，而且测试版回归会一起红。
    """
    s = io.open(dst_js, encoding="utf-8").read()
    ver, code = stable_target(s)
    if CODE_OVERRIDE is not None:
        say("code 用 --code 指定的 %d（算出来是 %d，那个号已经装在测试机上了）"
            % (CODE_OVERRIDE, code))
        code = CODE_OVERRIDE
    subs = [
        (r"^const APP_CHANNEL = '[a-z]+';", "const APP_CHANNEL = 'stable';"),
        (r"^const APP_BETA_SEQ = \d+;", "const APP_BETA_SEQ = 0;"),
        (r"^const APP_CODE = \d+;", "const APP_CODE = %d;" % code),
    ]
    for pat, rep in subs:
        s, n = re.subn(pat, rep, s, count=1, flags=re.M)
        if n != 1:
            raise SystemExit("改正式版渠道失败：%r 没命中（app.js 的常量写法变了？）" % pat)
    io.open(dst_js, "w", encoding="utf-8", newline="").write(s)
    return ver, code


# ---------------------------------------------------------------- 找工具

def _first_dir(*paths):
    for p in paths:
        if p and os.path.isdir(p):
            return p
    return None


def find_sdk():
    """SDK 根目录：里头要有 `12/ets` 和 `12/toolchains`。"""
    env = os.environ.get("OHOS_SDK_HOME")
    if env and os.path.isdir(os.path.join(env, "12", "ets")):
        return env
    lp = os.path.join(HERE, "local.properties")
    if os.path.exists(lp):
        for line in io.open(lp, encoding="utf-8"):
            m = re.match(r"\s*sdk\.dir\s*=\s*(.+)", line)
            if m:
                p = m.group(1).strip().replace("\\:", ":").replace("\\\\", "\\")
                if os.path.isdir(os.path.join(p, "12", "ets")):
                    return p
    for p in (os.path.join(ROOT, "_lab", "out", "_ohsdk", "sdk2"),
              r"C:\Program Files\Huawei\DevEco Studio\sdk"):
        if os.path.isdir(os.path.join(p, "12", "ets")):
            return p
    return None


def find_java():
    """签名要用 java（hap-sign-tool 是 jar），要 JDK 17 以上。"""
    cands = [os.environ.get("JAVA_HOME")]
    cands += glob.glob(os.path.join(ROOT, "_lab", "out", "_jdk", "jdk-*"))
    cands += [r"C:\Program Files\Huawei\DevEco Studio\jbr"]
    for c in cands:
        if c and os.path.exists(os.path.join(c, "bin", "java.exe")):
            return c
    return None


def find_hvigor():
    """hvigor 的入口 js。开发机上那套是手工 npm 装的（_lab/out/_hvigor）。"""
    env = os.environ.get("HVIGOR_JS")
    if env and os.path.exists(env):
        return env, None
    p = os.path.join(ROOT, "_lab", "out", "_hvigor", "node_modules",
                     "@ohos", "hvigor", "bin", "hvigor.js")
    if os.path.exists(p):
        return p, os.path.join(ROOT, "_lab", "out", "_hvigor", "node_modules")
    p = r"C:\Program Files\Huawei\DevEco Studio\tools\hvigor\bin\hvigorw.bat"
    if os.path.exists(p):
        return p, None
    return None, None


# ---------------------------------------------------------------- 编译

def write_local_props(sdk):
    """hvigor 只认 local.properties 的 sdk.dir（或环境变量）。路径是这台机器
    的，不进仓库（.gitignore 里写了）。"""
    io.open(os.path.join(HERE, "local.properties"), "w", encoding="utf-8",
            newline="\n").write("sdk.dir=" + sdk.replace(":", "\\:") + "\n")


def sync_version():
    """版本号只有一个来源：进包的那份 app.js（`rawfile/web/app.js`），
    正式版时它已经被 inject_stable 改过号。`AppScope/app.json5` 里那份
    是给鸿蒙打包器看的副本 —— 每次打包前对齐。

    🔴 不手改、不复用上一版的号：对不上就会出现「我的」里写 v0.52.2、
       系统设置里写 158 那种两处打架（Android 侧的 UA_TAG 已经吃过一次）。
    """
    js = io.open(os.path.join(RAW, "app.js"), encoding="utf-8").read()
    ver = re.search(r"^const APP_VERSION = '([^']+)';", js, re.M)
    code = re.search(r"^const APP_CODE = (\d+);", js, re.M)
    if not (ver and code):
        return None
    p = os.path.join(HERE, "AppScope", "app.json5")
    s = io.open(p, encoding="utf-8").read()
    s2 = re.sub(r'"versionCode":\s*\d+', '"versionCode": %s' % code.group(1), s, count=1)
    s2 = re.sub(r'"versionName":\s*"[^"]*"',
                '"versionName": "%s"' % ver.group(1), s2, count=1)
    if s2 != s:
        io.open(p, "w", encoding="utf-8", newline="\n").write(s2)
    return ver.group(1), int(code.group(1))


def build(sdk, java, hvigor, mod_path):
    env = dict(os.environ)
    env["JAVA_HOME"] = java
    env["PATH"] = os.path.join(java, "bin") + os.pathsep + env.get("PATH", "")
    env["OHOS_BASE_SDK_HOME"] = sdk
    env["DEVECO_SDK_HOME"] = sdk
    if mod_path:
        # hvigorfile.ts 里 import 的插件在共享 node_modules 里，得让它找得到
        env["NODE_PATH"] = mod_path
    if hvigor.endswith(".bat"):
        cmd = [hvigor, "assembleHap", "--mode", "module"]
    else:
        cmd = [env.get("NODE_BIN", "node"), hvigor, "--mode", "module"]
    cmd += ["-p", "product=default", "assembleHap", "--no-daemon"]
    say("跑 " + " ".join(cmd[:3]) + " …")
    r = subprocess.run(cmd, cwd=HERE, env=env)
    return r.returncode == 0


# ---------------------------------------------------------------- 签名

def _run(java, jar, *args):
    cmd = [os.path.join(java, "bin", "java.exe"), "-jar", jar] + list(args)
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    ok = r.returncode == 0 and "success" in (r.stdout or "")
    if not ok:
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-6:]
        say("  签名工具说：")
        for line in tail:
            say("    " + line)
    return ok


def _keytool_export(java, p12, alias, out):
    cmd = [os.path.join(java, "bin", "keytool.exe"), "-exportcert", "-rfc",
           "-alias", alias, "-keystore", p12, "-storepass", KEY_PWD,
           "-storetype", "PKCS12", "-file", out]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0 and os.path.exists(out)


def sign(sdk, java, bundle_name, release=False, ver=None, code=None):
    """release=True 走正式版那一套（profile type=release + distribution 证书）；
    否则是调试版。两套素材都在 SDK 里，别名不同、证书字段也不同，
    混用会得到 `verify certificate chain failed` 或一个装不上的包。"""
    lib = os.path.join(sdk, "12", "toolchains", "lib")
    p12 = os.path.join(lib, "OpenHarmony.p12")
    jar = os.path.join(lib, "hap-sign-tool.jar")
    tpl = os.path.join(lib, "UnsgnedReleasedProfileTemplate.json" if release
                       else "UnsgnedDebugProfileTemplate.json")
    prof_cert = os.path.join(lib, "OpenHarmonyProfileRelease.pem" if release
                             else "OpenHarmonyProfileDebug.pem")
    prof_alias = ("openharmony application profile release" if release
                  else "openharmony application profile debug")
    # 调试版写的是「开发证书」字段，正式版写的是「分发证书」字段 —— 名字不同，
    # 内容都是同一张 CA 签过的 leaf（公钥指纹比过）。
    cert_key = "distribution-certificate" if release else "development-certificate"
    for p in (p12, jar, tpl, prof_cert):
        if not os.path.exists(p):
            say("SDK 里缺签名材料：%s" % p)
            return None

    unsigned = os.path.join(OUT, "entry-default-unsigned.hap")
    if not os.path.exists(unsigned):
        say("没找到未签名的包：%s" % unsigned)
        return None

    if not os.path.isdir(SIGN):
        os.makedirs(SIGN)

    # ① 应用证书链：leaf（模板里那张 CA 签过的）+ CA。
    #    🔴 不能直接 keytool 导 alias 里那张 —— 那张是**自签**的，
    #       leaf 和 CA 对不上，工具会报 "verify certificate chain failed"。
    prof = json.load(io.open(tpl, encoding="utf-8"))
    leaf = prof["bundle-info"][cert_key].strip()
    ca = os.path.join(SIGN, "ca.pem")
    if not _keytool_export(java, p12, "openharmony application ca", ca):
        say("导不出 CA 证书")
        return None
    ca_pem = io.open(ca, encoding="utf-8").read().strip()
    chain = os.path.join(SIGN, "app-chain.pem")
    io.open(chain, "w", encoding="utf-8", newline="\n").write(leaf + "\n" + ca_pem + "\n")

    # ② profile：把模板改成我们的包名，有效期拉到十年。
    #    🔴 模板自带的 not-after 是 2024-01（早过期了），照抄会装不上。
    #    🔴 还要删 debug-info.device-ids —— 那是绑 UDID 的，别人装不上。
    now = int(time.time())
    prof["bundle-info"]["bundle-name"] = bundle_name
    prof["bundle-info"][cert_key] = leaf + "\n" + ca_pem + "\n"
    prof["validity"] = {"not-before": now - 3600, "not-after": now + 3600 * 24 * 365 * 10}
    prof.pop("debug-info", None)
    prof["uuid"] = "9f3c1a52-7a11-4f0e-bc4d-2c1f9a6e5b77"
    if release:
        # 正式版模板里带这两个字段（调试版模板没有）。不写就是模板里的
        # 2.0.0 / 2 —— 系统设置里会和包内的真实版本对不上。
        prof["type"] = "release"
        if ver:
            prof["version-name"] = ver
        if code:
            prof["version-code"] = code
    else:
        prof["type"] = "debug"
    prof_json = os.path.join(SIGN, "profile.json")
    io.open(prof_json, "w", encoding="utf-8", newline="\n").write(
        json.dumps(prof, ensure_ascii=False, indent=2))

    p7b = os.path.join(SIGN, "profile.p7b")
    if os.path.exists(p7b):
        os.remove(p7b)
    ok = _run(java, jar, "sign-profile",
              "-keyAlias", prof_alias, "-signAlg", "SHA256withECDSA",
              "-mode", "localSign", "-profileCertFile", prof_cert,
              "-inFile", prof_json, "-keystoreFile", p12, "-outFile", p7b,
              "-keyPwd", KEY_PWD, "-keystorePwd", KEY_PWD)
    if not ok or not os.path.exists(p7b):
        say("profile 没签出来")
        return None

    signed = os.path.join(OUT, "jiangcaitong-signed-release.hap" if release
                          else "jiangcaitong-signed.hap")
    if os.path.exists(signed):
        os.remove(signed)
    ok = _run(java, jar, "sign-app",
              "-keyAlias", KEY_ALIAS_APP, "-signAlg", "SHA256withECDSA",
              "-mode", "localSign", "-appCertFile", chain, "-profileFile", p7b,
              "-inFile", unsigned, "-outFile", signed, "-keystoreFile", p12,
              "-keyPwd", KEY_PWD, "-keystorePwd", KEY_PWD)
    if not ok or not os.path.exists(signed):
        say("HAP 没签出来")
        return None

    ok = _run(java, jar, "verify-app", "-inFile", signed,
              "-outCertChain", os.path.join(SIGN, "verify.cer"),
              "-outProfile", os.path.join(SIGN, "verify.p7b"))
    if not ok:
        say("签出来了，但自验没过 —— 这种包不要发出去")
        return None
    return signed


def main():
    global CODE_OVERRIDE
    release = "--release" in sys.argv
    if "--code" in sys.argv:
        i = sys.argv.index("--code")
        if i + 1 >= len(sys.argv):
            print("[停] --code 后面要跟一个数字")
            return 2
        CODE_OVERRIDE = int(sys.argv[i + 1])
    print("== 鸿蒙打包（%s）==" % ("正式版" if release else "测试版"))
    copy_web(release)
    v = sync_version()
    if v:
        say("版本：%s / %d（从 app.js 同步到 AppScope）" % v)
    if "--copy" in sys.argv:
        return 0

    sdk = find_sdk()
    java = find_java()
    hvigor, mod_path = find_hvigor()

    missing = []
    if not sdk:
        missing.append("SDK（$OHOS_SDK_HOME，里头要有 12/ets、12/toolchains）")
    if not java:
        missing.append("JDK（$JAVA_HOME，签名工具是 jar）")
    if not hvigor:
        missing.append("hvigor（$HVIGOR_JS，或 DevEco 里的 hvigorw）")
    if missing:
        print("\n[停] 缺东西：")
        for m in missing:
            print("     · " + m)
        print("\n     资源已经拷好了。装上 DevEco Studio 之后，"
              "用 DevEco 打开 mobile/harmony，")
        print("     Build → Build Hap(s) 即可；签名在 File → Project Structure → Signing Configs。")
        return 2

    say("SDK  %s" % sdk)
    say("JDK  %s" % java)
    write_local_props(sdk)

    app = json.load(io.open(os.path.join(HERE, "AppScope", "app.json5"),
                            encoding="utf-8"))["app"]
    if not build(sdk, java, hvigor, mod_path):
        print("\n[失败] 编译没通过（看上面的报错）")
        return 1

    unsigned = os.path.join(OUT, "entry-default-unsigned.hap")
    if "--no-sign" in sys.argv:
        say("产物（未签名）：%s  %.0f KB" % (unsigned, os.path.getsize(unsigned) / 1024.0))
        return 0

    signed = sign(sdk, java, app["bundleName"], release=release,
                  ver=(v[0] if v else None), code=(v[1] if v else None))
    if not signed:
        say("产物（未签名）：%s" % unsigned)
        print("\n[半成] 编译过了，签名没成。这个包**不能装** —— 上面写了为什么。")
        return 3

    say("产物：%s  %.0f KB" % (signed, os.path.getsize(signed) / 1024.0))
    print("\n签名方：OpenHarmony 公版%s密钥。" % ("正式（release profile）" if release
                                                 else "调试"))
    print("  · OpenHarmony 设备 / 模拟器：可以直接装（hdc install \"%s\"）" % signed)
    print("  · 零售版 HarmonyOS 手机：要用带自己证书的工具重签一次再装"
          "（系统只认本机证书）。")
    if release:
        print("\n这份是正式版口径：渠道 stable、不带 v 序号、不含数据台、"
              "profile 是 release 类型。")
        print("下一步：python make_hyt_site.py 把它放进站点，再 deploy/deploy.py --verify。")
        print("（版本号从包里读，别在页面或清单里另写一份。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
