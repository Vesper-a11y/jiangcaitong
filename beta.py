#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""beta.py —— 测试版 / 正式版两条腿走：改完先出测试版，攒够了再发正式版。

## 为什么要这个（2026-11-06，用户原话）

> 这两天更新有点太频繁了，降低些更新速度吧，改动完出新包后就标为那种测试版
> 之类的，暂时不发布，每次等到攒够一定量的时候再发布更新。

那几天的节奏是一天两三个版，每版都直接进 `version.json` —— 所有人都被推着
装一次，装完还得替我们试。这里把两件事拆开：

    改完 → `beta.py build`  → 测试版 APK，**上线但藏起来**
    攒够 → `beta.py release` → 正式版 + 测试版一起发布

## 两条通道（2026-11-07 细分）

    version.json                     正式版，明面上：公开页、历史列表、所有人检查更新
    beta/manifest.json               测试版，**页面上一个链接都没有**
    beta/jiangcaitong-<版号>-v<n>.apk

测试版 App 只认前一条（取不到才退回正式版清单），正式版 App 根本不知道
那条地址存在。所以「都发布、测试版藏起来」是同一个 `site/` 里两层目录，
不是一个开关。

用户原话：「发布时正式版和测试版都发布，但测试版隐藏起来，只能通过测试版的
检查更新下载」。所以 `build` 默认打完就推上去 —— 不然手机上的测试版
永远收不到下一版。

## 三条保证（都是硬门槛，不是口诀）

  1. **测试版进不了更新清单**：`build_apk.py --beta` 只写 `beta.json`，
     `version.json` 一个字节都不动；反过来，`--beta` 时如果发现 version.json
     里已经是这个版本号，打包直接失败。
  2. **渠道和包必须对上**：app.js 的 `APP_CHANNEL` 与打包参数不一致就停下。
     两边错开的后果很隐蔽 —— 界面写着「测试版」、包却是正式版（或者反过来）。
  3. **发版频率有账可查**：`status` 会算「距上次正式版几天 / 攒了几次改动」，
     没到量就 release 会先问一句（`--force` 才继续）。

## 用法

    python mobile/beta.py build --note "修：课表空白" --note "加：匿名上报"
    python mobile/beta.py build --target 0.49.0 --note "…"   # 这一轮第一次
    python mobile/beta.py status
    python mobile/beta.py release                 # 版本号用台账里记着的 target
    python mobile/beta.py release --force         # 没到量也发
    python mobile/beta.py publish                 # 只重推，不重新打包

## 版号怎么算（2026-11-07 起）

一整个开发周期**挂在同一个版号上**，测试版靠序号区分：

    0.49.0 测试版 v1 → v2 → v3 …（都是 0.49.0，versionCode 递增）
    攒够了 → 发 0.49.0 正式版：**号不变，只是去掉 v1**

用户原话：「在测试版里就采用 xxx v1 这样的形式，我使用后觉得可以的话就把
版本号改为 xxx 并发布正式版」。所以测试版挂的号**就是**最终那个正式版号 ——
发布时不用换号，包里内容也一字不改（只改了 APP_CHANNEL）。
第一次 build 用 `--target` 定号，之后台账里的 `target` 一直沿用，不用再给。

## 阈值

    MIN_BUILDS = 3   攒够 3 次改动
    MIN_DAYS   = 5   或者距上次正式版满 5 天（先到为准）

这两个数不是拍脑袋：一天一版的时候，用户装完还没用出问题下一版又来了；
隔几天一版，既能让修好的问题快点到人手上，又不至于天天打扰。
（2026-11-07 用户要求「每隔几天发布一个新版本」，7 天调成 5 天。）
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP_JS = os.path.join(HERE, "web", "app.js")
BUILD_PY = os.path.join(HERE, "android", "build_apk.py")
MAIN_JAVA = os.path.join(HERE, "android", "src", "com", "hobson", "jiangcaitong",
                         "MainActivity.java")
NOTES = os.path.join(HERE, "RELEASE-NOTES.md")
LEDGER = os.path.join(HERE, "beta_log.json")
BETA_JSON = os.path.join(HERE, "beta.json")
VERSION_JSON = os.path.join(HERE, "version.json")
DIST = os.path.join(HERE, "android", "dist")

# 测试版归档在这里。不放进 mobile/：那是要提交的源码目录，
# 一个包 600 KB，几个版本就把仓库撑大了。
ARCHIVE = os.path.join(ROOT, "_lab", "out", "beta")

# 发布链路要跑的两个脚本（都在工作区根目录）。
MAKE_HYT = os.path.join(ROOT, "make_hyt_site.py")
DEPLOY = os.path.join(ROOT, "deploy", "deploy.py")
CNB_APK = os.path.join(ROOT, "deploy", "upload_cnb_apk.py")

MIN_BUILDS = 3
# 多久没发就发一版（用户 2026-11-07：「以后就按照时间在发布，每隔几天发布一个
# 新版本」）。原来是 7 天 —— 等于一周才动一次，攒着的问题要等太久。
MIN_DAYS = 5


def die(msg: str, code: int = 1):
    sys.stderr.write("beta: %s\n" % msg)
    sys.exit(code)


def read(p: str) -> str:
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p: str, s: str):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


# ------------------------------------------------------------------ 版本号读写

def get_ver() -> tuple[str, int, str]:
    js = read(APP_JS)
    m = re.search(r"^const APP_VERSION = '([^']+)';", js, re.M)
    c = re.search(r"^const APP_CODE = (\d+);", js, re.M)
    ch = re.search(r"^const APP_CHANNEL = '([a-z]+)';", js, re.M)
    if not (m and c and ch):
        die("app.js 里读不到 APP_VERSION / APP_CODE / APP_CHANNEL")
    return m.group(1), int(c.group(1)), ch.group(1)


def get_seq() -> int:
    """测试版序号（v1 / v2 …）。0 = 正式版。"""
    m = re.search(r"^const APP_BETA_SEQ = (\d+);", read(APP_JS), re.M)
    return int(m.group(1)) if m else 0


def set_ver(version: str, code: int, channel: str, seq: int | None = None):
    """把版本号写进四个地方。少一处打包脚本会拒（那是有意的门槛）。

    `seq` 是测试版序号（v1 / v2…）：打正式版时传 0，打测试版时传序号。
    传 None 表示不动它（老调用点不用改）。
    """
    js = read(APP_JS)
    js = re.sub(r"^const APP_VERSION = '[^']*';",
                "const APP_VERSION = '%s';" % version, js, count=1, flags=re.M)
    js = re.sub(r"^const APP_CODE = \d+;",
                "const APP_CODE = %d;" % code, js, count=1, flags=re.M)
    js = re.sub(r"^const APP_CHANNEL = '[a-z]+';",
                "const APP_CHANNEL = '%s';" % channel, js, count=1, flags=re.M)
    if seq is not None:
        js = re.sub(r"^const APP_BETA_SEQ = \d+;",
                    "const APP_BETA_SEQ = %d;" % seq, js, count=1, flags=re.M)
    write(APP_JS, js)

    py = read(BUILD_PY)
    py = re.sub(r'^VERSION_NAME = "[^"]*"', 'VERSION_NAME = "%s"' % version,
                py, count=1, flags=re.M)
    py = re.sub(r"^VERSION_CODE = \d+", "VERSION_CODE = %d" % code,
                py, count=1, flags=re.M)
    write(BUILD_PY, py)

    java = read(MAIN_JAVA)
    # 🔴 先查「锚点在不在」，再判「改完有没有变化」。
    #    原来的写法是 `if java2 == java: die(...)`，看着等价，其实不等价：
    #    上一次 build 走到这里就中断了、版本号已经是**目标号**时，
    #    re.sub 换出来和原文一模一样 ⇒ 被判成「找不到锚点」，整条命令报错退出。
    #    2026-11-07 打 0.47.8 时就撞上了（第一次被中断，重跑直接 die）。
    #    set_ver 必须是**幂等**的：重跑一次不该报错，也不该吃掉版本号。
    if re.search(r"JiangCaiTong/[\d.]+", java) is None:
        die("MainActivity.java 里找不到 JiangCaiTong/<版本> 这个 UA 后缀")
    java2 = re.sub(r"JiangCaiTong/[\d.]+", "JiangCaiTong/" + version, java, count=1)
    write(MAIN_JAVA, java2)


def bump_patch(version: str) -> str:
    a, b, c = (version.split(".") + ["0", "0"])[:3]
    return "%s.%s.%d" % (a, b, int(c) + 1)


def next_minor(version: str) -> str:
    a, b, _ = (version.split(".") + ["0", "0"])[:3]
    return "%s.%d.0" % (a, int(b) + 1)


# ------------------------------------------------------------------ 更新说明

def insert_notes(version: str, notes: list[str], detail: list[str] | None = None):
    """在 RELEASE-NOTES.md 里插一节 `## <version>`。

    插在**第一个 `## ` 之前**（文件开头那段说明后面），保持「最新的在最上面」。
    `detail` 写进 `<!-- 细节 -->` 注释块 —— build_apk 的 read_notes 会跳过它。

    ⚠️ 如果这一版已经有一节（上一次 build 没走完，重试时沿用了同一个版本号），
       先把旧的删掉再插。不删的话会同时存在两节同号，而 read_notes 只认**第一节**，
       改过的说明看着像没生效。
    """
    src = read(NOTES)
    lines = src.splitlines()

    # 先摘掉同号的那一节（从 `## x.y.z` 到下一个 `## ` 之前）
    out_lines: list[str] = []
    skipping = False
    for s in lines:
        if s.startswith("## "):
            if s[3:].strip().split()[0:1] == [version]:
                skipping = True
                continue
            skipping = False
        if skipping:
            continue
        out_lines.append(s)
    lines = out_lines

    at = next((i for i, s in enumerate(lines) if s.startswith("## ")), len(lines))
    block = ["## " + version, ""]
    block += ["- " + n for n in notes]
    if detail:
        block += ["", "<!-- 细节："] + ["     · " + d for d in detail] + ["-->"]
    out = lines[:at] + block + ([""] if at < len(lines) else ["", ""]) + lines[at:]
    text = "\n".join(out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    if not text.endswith("\n"):
        text += "\n"
    write(NOTES, text)


# ------------------------------------------------------------------ 台账

def ledger() -> dict:
    if os.path.exists(LEDGER):
        try:
            d = json.loads(read(LEDGER))
        except Exception as e:
            die("beta_log.json 读不了：%s" % e)
        d.setdefault("betas", [])
        d.setdefault("lastStable", {})
        return d
    # 第一次用：把当前发布中的版本当作「上一版正式版」记下来
    try:
        v = json.loads(read(VERSION_JSON))
        last = {"version": v.get("version"), "code": v.get("code"),
                "date": v.get("date")}
    except Exception:
        last = {}
    return {"lastStable": last, "betas": []}


def save_ledger(d: dict):
    write(LEDGER, json.dumps(d, ensure_ascii=False, indent=2) + "\n")


def pending(d: dict) -> list[dict]:
    return [b for b in d["betas"] if not b.get("published")]


def days_since(d: dict) -> int:
    s = (d.get("lastStable") or {}).get("date") or ""
    try:
        was = datetime.date.fromisoformat(s)
    except Exception:
        return 0
    return (datetime.date.today() - was).days


# ------------------------------------------------------------------ 子命令

def cmp_ver(a: str, b: str) -> int:
    """'0.52.0' vs '0.51.2' → 正数。字符串比较会把 0.9 排到 0.10 后面。"""
    def num(s):
        return [int(x) if x.isdigit() else 0 for x in (s or "0").split(".")]
    x, y = num(a), num(b)
    n = max(len(x), len(y))
    x += [0] * (n - len(x))
    y += [0] * (n - len(y))
    return (x > y) - (x < y)


def pick_target(d: dict, explicit: str | None, version: str, channel: str = "beta") -> str:
    """这一轮要发的版号。

    🔴 用户 2026-11-09 定的规矩：
        「测试版不需要升级前面的版本号，只升后面的 v1、v2 这些就行了，
          如果正式版是 0.51.0，那测试版就一直保持 0.52.0 v几，
          直到 0.52.0 正式版发布。」
       也就是说**测试版的版号 = 下一个正式版的号，从头到尾不变**，
       变的是 v 后面的序号。以前每打一次测试版就可能动一次号，
       用户那边看到「0.51.0 v3 → 0.51.1 v1」，会以为又发了个新版本。

    所以不指定 --target 时，直接沿用程序里已经写着的版号（就是本轮那个号）；
    显式指定 = 开新一轮，必须比现在大 —— 免得手一抖把用户手上的包改回旧号。
    """
    last = d.get("lastStable") or {}
    if explicit:
        if channel == "beta" and cmp_ver(explicit, version) < 0:
            die("--target %s 比程序里现在的 %s 还小。\n"
                "  测试版只升 v 序号，不改前面的版号；真要开新版号得比现在大。"
                % (explicit, version))
        return explicit
    if channel == "beta":
        return version
    return d.get("target") or next_minor(last.get("version") or version)


def merge_notes(mine: list, new: list, replace: bool = False) -> list[str]:
    """同一版号下打第二版时，更新说明要**累积**。

    v2 的包里已经包含 v1 改的所有东西，只写 v2 那几条的话，
    用户装上去看到的更新说明是残缺的（而且他不会知道 v1 也存在过）。

    `replace=True` 时只留这次给的几条（`--replace-notes`）。什么时候用：
    累积到几十条、而且里面已经有被后来条目**取代**的旧说法时
    （2026-11-16：0.53.0 攒到 33 条，同一条入口前后名字都不一样了）。
    不收敛的话，用户点「检查更新」看到的是两套说法拼在一起。
    """
    if replace:
        return list(dict.fromkeys(new))
    out: list[str] = []
    for b in mine:
        for n in (b.get("notes") or []):
            if n not in out:
                out.append(n)
    for n in new:
        if n not in out:
            out.append(n)
    return out


def pick_seq(target: str, version: str, channel: str, cur_seq: int,
             mine: list, building: dict | None = None) -> tuple[int, bool]:
    """这一版用第几号测试版（v1 / v2 …）。返回 (序号, 是不是接上次没打完的)。

    `mine` 是台账里**同一个 target 版号**下已有的那些测试版。

    🔴 「接上次」只认台账里的 `building` 标记（见 cmd_build）。
       原来拿「程序里的序号 + 台账里没有」当依据，结果是：手改过一次
       APP_VERSION 之后，新号下的 v1 被当成「上次没打完的第 8 版」，
       沿用第 8 版那个 code —— 两台手机上都叫 v8，而新那个根本推不出去。
       2026-11-09 现场撞过一次，靠下面的 code 硬闸才没发出去。
    """
    used = {b.get("seq") or 0 for b in mine}
    if building and building.get("version") == target:
        s = int(building.get("seq") or 0)
        if s > 0 and s not in used:
            return s, True
    return (max(used) if used else 0) + 1, False


def cmd_build(args):
    version, code, channel = get_ver()
    cur_seq = get_seq()
    if not args.note:
        die("至少给一条 --note（写人话，一句一件事）。\n"
            "  例如：--note \"修：课表空白\"")
    d = ledger()
    # 这一轮的目标版号：一整个开发周期**挂在同一个号上**（用户 2026-11-07：
    # 「以 0.49.2 的测试版 v1，v2 之类的来称呼非正式版本」）。
    target = pick_target(d, args.target, version, channel)

    # 新周期：版号切上去了，这时候「开一轮」这件事本身要让人看见一眼。
    if target != version:
        print("开新一轮：%s（上一轮的测试版挂在 %s 上）" % (target, version))

    building = d.get("building") or {}
    mine = [b for b in d["betas"] if b.get("version") == target]
    seq, resumed = pick_seq(target, version, channel, cur_seq, mine, building)

    if resumed:
        nv, nc = target, int(building.get("code") or (code + 1))
        print("接上次没打完的那一版：%s 测试版 v%d（code %d）" % (nv, seq, nc))
    else:
        nv, nc = target, code + 1
        print("测试版：%s 测试版 v%d（code %d）" % (nv, seq, nc))

    # 🔴 code 必须**严格大于**每一个已经发出去的测试版，否则手机上「检查更新」
    #    根本看不见这一版 —— 装上去也覆盖不了，用户那边毫无动静，而这里一切正常。
    #    2026-11-09 就撞上一次：手改 app.js 的版本号让 pick_seq 以为「接着上一版打」，
    #    结果 0.51.2 拿到了和 0.51.1 一样的 112。全靠这条才当场拦住。
    seen = [b.get("code") or 0 for b in d["betas"]] + [(d.get("lastStable") or {}).get("code") or 0]
    if nc <= max(seen or [0]):
        die("这一版的 code（%d）不比已经发出去的测试版大（最大 %d）——\n"
            "  手机上会看不见这次更新。手改过 APP_VERSION/APP_CODE 的话先撤回，\n"
            "  再只用 --target 指定版号重打。" % (nc, max(seen)))

    set_ver(nv, nc, "beta", seq)
    insert_notes(nv, merge_notes(mine, list(args.note),
                                 replace=bool(getattr(args, "replace_notes", False))))

    # 记下「我正在打这一版」。打包中途断了（Ctrl-C、编译报错）时，
    # 下一次 build 靠它沿用同一个 seq/code，而不是再吃掉一个号。
    d = ledger()
    d["building"] = {"version": nv, "seq": seq, "code": nc}
    write(LEDGER, json.dumps(d, ensure_ascii=False, indent=2) + "\n")

    print("\n打包（--beta：不写 version.json）…")
    rc = subprocess.call([sys.executable, os.path.join(HERE, "android", "build_apk.py"),
                          "--beta"], cwd=HERE)
    if rc != 0:
        die("打包失败（退出码 %d）。工程还停在 %s / beta 状态，"
            "改完再跑一次，或者用 git checkout 撤回。" % (rc, nv))

    apk = os.path.join(DIST, "jiangcaitong-%s-v%d.apk" % (nv, seq))
    if not os.path.exists(apk):
        die("打包说成功了，但没找到 %s" % apk)
    info = json.loads(read(BETA_JSON))

    # 归档一份：dist/ 会被下一次打包覆盖，而「用户报的问题对应哪一版」以后要查
    dst_dir = os.path.join(ARCHIVE, "%s-v%d" % (nv, seq))
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(apk))
    shutil.copy2(apk, dst)

    d = ledger()
    d["target"] = nv
    d.pop("building", None)          # 这一版已经落账，不再是「打到一半」
    # 上一次 release 打包失败会留下 releaseCode；不适用于新打的测试版，
    # 留着会让下一次发版沿用那个号（可能比现在的 code 还小）。
    d.pop("releaseCode", None)
    d["betas"].append({
        "version": nv, "code": nc, "seq": seq,
        "date": datetime.date.today().isoformat(),
        "size": info.get("size"), "sha256": info.get("sha256"),
        "notes": list(args.note), "published": False,
    })
    save_ledger(d)

    print("\n✅ 测试版好了：%s" % dst)
    print("   %d 字节 / sha256 %s" % (info.get("size", 0), info.get("sha256", "")[:16] + "…"))
    print("   装上之后「我的 → 检查更新」那一行会显示「v%s · 测试版 v%d」。"
          % (nv, seq))
    print("   正式版用户检查更新看到的还是 %s —— 这一版他们拿不到。"
          % ((d.get("lastStable") or {}).get("version") or "上一版"))

    if args.no_publish:
        print("\n   （--no-publish：只打了包，没上线。"
              "要上线就跑 python mobile/beta.py publish）")
        return 0

    # 打完就上线：手机上「检查更新」能直接下到这一版（测试版清单那条隐藏地址）。
    publish(beta_only=True)
    print("\n✅ 已上线：测试版 App 里「检查更新」能拿到 %s v%d；"
          "正式版用户看不到。" % (nv, seq))
    print("   攒够了再发：python mobile/beta.py status")
    return 0


def run_step(argv, what):
    print("\n--- %s ---" % what)
    rc = subprocess.call(argv, cwd=ROOT)
    if rc != 0:
        die("%s 失败（退出码 %d）。包已经打好了，修完可以单独推一次：\n"
            "   python mobile/beta.py publish" % (what, rc))


def beta_key():
    """测试版那一层的口令（deploy/beta_key.txt，已 gitignore）。

    2026-11-10 起 `/jiangcaitong/beta/` 要口令，所以**核对线上**的地址也得带上它，
    否则核对永远是 403，等于这一层从此没人验过。
    """
    p = os.path.join(ROOT, "deploy", "beta_key.txt")
    try:
        return open(p, encoding="utf-8").read().strip()
    except OSError:
        return ""


def verify_live(pairs):
    """取一次线上那份清单，和本地逐字节比。

    🔴 取不到**不算发布失败**：本机走代理时对 hobson.cc 经常回 502，
    而线上其实好好的（2026-11-06 记过一次）。这里只把「取不到」和
    「取到了但对不上」分开说，后者才是真的要管的事。
    """
    import urllib.request
    # 🔴 必须带浏览器 UA：Cloudflare 会把**没有 UA 的请求**直接 403 掉
    #    （实测 python-urllib 默认头取 hobson.cc 全是 403，同一条 URL 带 UA 就 200）。
    #    不带的话这里每次都报「取不到」，而线上其实好好的 ——
    #    假警报多了，真出问题那一次就没人看了。
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过系统代理
    k = beta_key()
    for url, path in pairs:
        if "/jiangcaitong/beta/" in url and k:
            url = url + ("&" if "?" in url else "?") + "k=" + k
        try:
            # 🔴 防缓存参数要和已有的查询串**接上**，不能无脑再加一个 `?`。
            #    口令是 `?k=…`，再拼成 `?k=…?t=…` 的话，服务端读到的 k
            #    变成 `…?t=…` 一整串 ⇒ 必定 403。（2026-11-10 第一次带口令发版
            #    就是这么红的：明明是好的，却报「取不到（403）」。）
            sep = "&" if "?" in url else "?"
            req = urllib.request.Request(url + sep + "t=%d" % os.getpid(), headers={
                "Cache-Control": "no-store", "User-Agent": UA})
            with op.open(req, timeout=15) as r:
                got = r.read()
        except Exception as e:
            print("   [??] %s 取不到（%s）—— 不代表没发上去，用浏览器核一下"
                  % (url, getattr(e, "code", "") or type(e).__name__))
            continue
        want = open(path, "rb").read()
        if got == want:
            print("   [ok] %s 和本地一致（%d 字节）" % (url, len(got)))
        else:
            die("线上 %s 和本地不一致（线上 %d 字节 / 本地 %d 字节）—— "
                "多半是 CDN 还缓存着旧的那份，等一会儿再核" % (url, len(got), len(want)))


def publish(beta_only: bool):
    """重新生成 /jiangcaitong/ 并推到线上。

    正式版发版（beta_only=False）时**两个包一起上去**：
    测试版的清单和包在 /jiangcaitong/beta/ 下，页面上没有入口；
    正式版的清单和包在明面上。这就是「都发布、测试版藏起来」。

    ⚠️ 这里跑的是 make_hyt_site.py，不是 make_site.py —— 后者会 rmtree
       整个 site/ 再重建（连桌面端那几个包一起），一个测试版不值当。
       前提是 site/ 已经存在（上一次发版留下的）。
    """
    run_step([sys.executable, MAKE_HYT], "生成站点（含测试版那一层）")
    if not beta_only:
        run_step([sys.executable, CNB_APK], "传正式版到 CNB（国内线路）")
    run_step([sys.executable, DEPLOY], "发布到 hobson.cc")

    print("\n核对线上清单 …")
    base = "https://hobson.cc/jiangcaitong"
    pairs = [(base + "/version.json", VERSION_JSON)]
    if os.path.exists(BETA_JSON):
        pairs.append((base + "/beta/manifest.json", BETA_JSON))
    verify_live(pairs)


def cmd_status(args):
    d = ledger()
    last = d.get("lastStable") or {}
    ps = pending(d)
    gap = days_since(d)

    print("上一版正式版：%s（code %s，%s）"
          % (last.get("version") or "?", last.get("code") or "?", last.get("date") or "?"))
    print("距今天：%d 天" % gap)

    # 两条通道各是什么，一眼要看出来 —— 发版时最容易犯的错就是
    # 「以为在发正式版，其实只推了测试版那层」。
    v, c, ch = get_ver()
    print("当前工程：%s / code %d / 渠道 %s%s"
          % (v, c, ch, (" 测试版 v%d" % get_seq()) if ch == "beta" and get_seq() else ""))
    if os.path.exists(BETA_JSON):
        b = json.loads(read(BETA_JSON))
        print("测试版清单：%s v%s（code %s）→ 发在 /jiangcaitong/beta/，页面上没有入口"
              % (b.get("version"), b.get("seq"), b.get("code")))
    else:
        print("测试版清单：没有（`build` 之后才会有）")
    print()

    if not ps:
        print("还没有未发布的测试版。")
        print("改完东西先打一版：python mobile/beta.py build --note \"修：…\"")
        return 0

    cur_target = d.get("target") or ""
    if cur_target:
        print("这一轮的目标版号：%s（攒够了就发它）" % cur_target)
        print()

    print("未发布的测试版 %d 个：" % len(ps))
    for b in ps:
        seq = b.get("seq") or 0
        print("  %-8s %s  测试版 v%s  %s"
              % (b.get("version"), b.get("date"), seq or "?",
                 " / ".join(b.get("notes") or [])))
    print()

    # 「够不够量」= 攒够次数 或 拖够天数，先到为准。
    enough = len(ps) >= MIN_BUILDS or gap >= MIN_DAYS
    if enough:
        why = ("攒了 %d 次改动（≥%d）" % (len(ps), MIN_BUILDS)
               if len(ps) >= MIN_BUILDS else "距上一版 %d 天（≥%d）" % (gap, MIN_DAYS))
        print("✅ %s，可以发正式版了：" % why)
        print("   python mobile/beta.py release")
    else:
        print("还不到发版的量：%d/%d 次改动，%d/%d 天。"
              % (len(ps), MIN_BUILDS, gap, MIN_DAYS))
        print("继续攒。急着发（比如线上有个必须马上修的问题）就加 --force：")
        print("   python mobile/beta.py release --force")
    return 0


def pick_notes(ps, limit: int = 4) -> list[str]:
    """从待发的测试版里挑出更新弹窗要显示的那几条（最多 limit 条）。

    名额分配：**最新那一版给 2 条，再往前每版 1 条**，满 limit 就停；
    还剩位置（版本少、或某版说明不够）就按从新到旧把剩下的补上。

    ⚠️ 两个错法都踩过：
      · 按时间顺序截前 limit 条（`merged[:4]`）—— 那是**最早**的改动，
        最新一版最该让用户知道的反而被挤进注释块。0.48.0 发版前就是这样：
        加课和「检查更新不再转圈」一条都没进弹窗，弹窗里全是 0.47.7 的匿名上报。
      · 每版平均分名额 —— 内容对，但顺序是 A1/B1/C1/A2，本版两条被上上版隔开，
        用户读到第 3 条才发现「这版还改了加课」。
    最新那版多给一条，它的事就能连着说在前面。
    """
    head: list[str] = []

    def take(b, n):
        for x in (b.get("notes") or []):
            if len(head) >= limit or n <= 0:
                return
            if x not in head:
                head.append(x)
                n -= 1

    for i, b in enumerate(reversed(ps)):
        if len(head) >= limit:
            break
        take(b, 2 if i == 0 else 1)

    # 版本少、某版说明不够，名额没用完 —— 再按从新到旧补一轮（每版一条）
    while len(head) < limit:
        added = False
        for b in reversed(ps):
            for x in (b.get("notes") or []):
                if x not in head:
                    head.append(x)
                    added = True
                    break
            if len(head) >= limit:
                break
        if not added:
            break
    return head


def prefill_manifest(version: str, code: int):
    """打包前把 version.json 的 version / code 先推向目标版本。

    为什么需要：`build_apk.py` 的 `check_version_consistency` 在**打包前**就要求
    更新清单已经是这一版（这条门槛挡的是「改了 app.js 忘了改清单」），
    但清单里的 size / sha256 只有打完包才知道 —— 于是首次发正式版必然自锁：
    清单还是上一版 ⇒ 报「应为 0.x.0」⇒ 永远打不出来。
    （0.48.0 发版时撞上；0.47.5 那次是手工补的，没留下痕迹。）

    这里只推 version / code，其余字段（notes、下载地址）原样保留；
    打包成功后 `write_manifest` 会把 size / sha256 / date 一并重写。
    打包失败要回滚 —— 清单停在「说自己是新版、包却不存在」比停在上一版更糟。
    """
    p = VERSION_JSON
    info = {}
    if os.path.exists(p):
        try:
            info = json.loads(read(p))
        except Exception:
            info = {}
    info["version"] = version
    info["code"] = code
    write(p, json.dumps(info, ensure_ascii=False, indent=2) + "\n")


def pick_code(target: str, cur: str, curcode: int, ps, saved=None) -> int:
    """正式版这一版用哪个 versionCode。

    规则：**比当前 code 和每个测试版都大 1**。测试版之间已经是一路 +1 过来的，
    正式版再 +1 才排在最前 —— 不是「内容一样就共用一个号」：
    号是判「谁更新」的唯一依据，共号会让「beta 清单取不到 → 退回正式版清单」
    这条路断掉（同为 88，永远不判为新版，用户就卡在测试版上再也回不来）。

    🔴 必须幂等：release 被打断后重跑，版本号**已经**是 target 了，
       这时候要沿用当初算出来的 code，不能再 +1。
       否则重跑一次涨一格、重跑三次涨三格，而且全是没发出去的号；
       更麻烦的是「清单里的 code」和「手机上装的 code」对不上，
       更新会反复提示同一版。
       所以「算过一次」这件事记在台账的 `releaseCode` 里（`saved`），
       不靠「cur == target」去猜 —— 打测试版时 cur 也等于 target，
       那个判断分不出「还没发」和「发到一半」。
    """
    if saved:
        return int(saved)
    return max([curcode] + [b.get("code") or 0 for b in ps]) + 1


def cmd_publish(args):
    """不重新打包，只把现有的 site/jiangcaitong/ 重新生成并推上去。

    发布链路某一步挂了（wrangler 抖、CNB 超时）时用这个续上，
    免得为了重推一遍又打一次包 —— 一打就涨 code，涨了又和已发布的对不上。
    """
    print("当前工程：%s（code %s）· 渠道 %s" % get_ver())
    if os.path.exists(BETA_JSON):
        b = json.loads(read(BETA_JSON))
        print("测试版清单：%s v%s（code %s）"
              % (b.get("version"), b.get("seq"), b.get("code")))
    else:
        print("测试版清单：没有（beta/ 那一层会是空的）")
    if not os.path.exists(os.path.join(ROOT, "site")):
        die("site/ 不存在 —— 先跑 python make_site.py 把站点重建出来")
    publish(beta_only=bool(args.beta_only))
    print("\n✅ 推完了")
    return 0


def cmd_release(args):
    d = ledger()
    ps = pending(d)
    if not ps:
        die("没有待发的测试版。先 python mobile/beta.py build --note \"…\"")
    gap = days_since(d)
    if len(ps) < MIN_BUILDS and gap < MIN_DAYS and not args.force:
        die("还不到发版的量：%d/%d 次改动、%d/%d 天。\n"
            "  确认要现在发就加 --force（这个门槛就是防「一天一版」的）。"
            % (len(ps), MIN_BUILDS, gap, MIN_DAYS))

    cur, curcode, _ = get_ver()
    # 优先用这一轮已经挂在台账上的目标版号 —— 测试版一路打的都是它，
    # 发版时再算一次的话会和测试版对不上（用户看到的号变了）。
    target = pick_target(d, args.version, cur, "stable")
    if not re.match(r"^\d+\.\d+\.\d+$", target):
        die("版本号要写成 0.48.0 这样：%r" % target)
    code = pick_code(target, cur, curcode, ps, saved=d.get("releaseCode"))

    # 更新弹窗里放不下太多行（320dp 要一屏放得下，历史上超过 4 条就红），
    # 所以最多留 4 条；多出来的进注释块，给维护者看。
    # 挑哪 4 条见 pick_notes() —— 别改成 merged[:4]，那是很早以前的错法。
    merged: list[str] = []
    for b in ps:
        for n in (b.get("notes") or []):
            if n not in merged:
                merged.append(n)
    head = pick_notes(ps)
    rest = [n for n in merged if n not in head]

    detail = []
    for b in ps:
        detail.append("%s（%s）：%s" % (b.get("version"), b.get("date"),
                                       " / ".join(b.get("notes") or [])))
    if rest:
        detail.append("弹窗里没放下的：" + " / ".join(rest))

    print("正式发版：%s（code %d），合并 %d 个测试版" % (target, code, len(ps)))
    # 🔴 先落盘再打包：中途失败重跑时 pick_code 从这里取回同一个号。
    #    打包成功后再和 target 一起清掉。
    d["releaseCode"] = code
    save_ledger(d)
    set_ver(target, code, "stable", 0)
    insert_notes(target, head, detail)

    print("\n打包（正式版：写 version.json）…")
    # 先把清单的 version / code 推上去 —— 打包前的门槛会核对它，
    # 而 size / sha256 要等打完包才由 write_manifest 写。打包失败就回滚。
    mf = VERSION_JSON
    before = read(mf) if os.path.exists(mf) else None
    prefill_manifest(target, code)
    rc = subprocess.call([sys.executable, os.path.join(HERE, "android", "build_apk.py")],
                         cwd=HERE)
    if rc != 0:
        if before is not None:
            write(mf, before)
            print("   （更新清单已回滚到打包前的样子）")
        die("打包失败（退出码 %d）" % rc)

    for b in ps:
        b["published"] = True
    d["lastStable"] = {"version": target, "code": code,
                       "date": datetime.date.today().isoformat()}
    # 这一轮结束了：下一轮重新按「上一版正式版 +1 位」算目标版号，
    # 不接着用同一个号 —— 否则下个周期还是这个号，正式版会撞车。
    d.pop("target", None)
    d.pop("releaseCode", None)
    save_ledger(d)

    # 🔴 beta.json **不清**（2026-11-07 起）。它现在是线上测试版清单的来源：
    #    发版会把测试版一起传上去（藏在 /jiangcaitong/beta/），
    #    清掉的话线上那一层要么消失、要么留着一份没人更新的旧清单 ——
    #    手上的测试版 App 就只能退回正式版清单，白丢一条通道。
    #    它记的就是「这一轮最后一个测试版」，内容和刚发的正式版一样。

    if args.no_publish:
        print("\n✅ 正式版打好了，但**还没有传上线**（--no-publish）。\n"
              "   要上线就跑 python mobile/beta.py publish")
        return 0

    publish(beta_only=False)
    print("\n✅ 发出去了：")
    print("   正式版 %s（code %d）→ 公开页 / 历史 / 正式版检查更新"
          % (target, code))
    if os.path.exists(BETA_JSON):
        b = json.loads(read(BETA_JSON))
        print("   测试版 %s v%s（code %s）→ /jiangcaitong/beta/，"
              "只有测试版 App 拿得到" % (b.get("version"), b.get("seq"), b.get("code")))
    print("\n   下一轮：python mobile/beta.py build --note \"…\"（版号会自动 +1 位）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="酱菜通测试版 / 正式版管理")
    sub = ap.add_subparsers(dest="cmd")

    b = sub.add_parser("build", help="打一版测试版（不发布）")
    b.add_argument("--note", action="append", default=[],
                   help="这次改了什么，一句一条（可重复）")
    b.add_argument("--target",
                   help="这一轮要发的版号，例如 0.49.0。只在第一次 build 时给；"
                        "之后每次 build 都沿用台账里记着的那个")
    b.add_argument("--no-publish", action="store_true",
                   help="打完不上线（默认打完就推上去，手机上检查更新能直接下到）")
    b.add_argument("--replace-notes", action="store_true",
                   help="更新说明只留这次给的几条，不跟同版号的前几版累积。\n"
                        "累积是默认行为（v3 的包确实包含 v1/v2 的改动），但累积到\n"
                        "几十条、里面还有被后来条目取代的旧说法时，就该用它收一次。")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("status", help="看攒了多少、够不够发")
    s.set_defaults(func=cmd_status)

    r = sub.add_parser("release",
                       help="把攒的测试版并成正式版，正式版和测试版一起发布")
    r.add_argument("--version", help="正式版本号，例如 0.49.0（默认用台账里的目标版号）")
    r.add_argument("--force", action="store_true", help="没到量也发")
    r.add_argument("--no-publish", action="store_true", help="只打包，不推上线")
    r.set_defaults(func=cmd_release)

    pu = sub.add_parser("publish", help="把已经打好的这一版重新推上线（不重新打包）")
    pu.add_argument("--beta-only", action="store_true",
                    help="只推测试版（跳过 CNB 那条国内线路）")
    pu.set_defaults(func=cmd_publish)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
