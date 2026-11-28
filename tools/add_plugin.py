#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一个插件 .js 上架到 plugins/ —— 一条命令做完三件事：

  ① 复制成 plugins/<id>/<版本>.js（路径就是清单里 file 那一栏）
  ② 算出 size 和 sha256（App 下载后要核这两个数，对不上就不装）
  ③ 更新 plugins/index.json（同 id 就覆盖那一行，其余不动）

用法：
  python tools/add_plugin.py 插件文件.js --id demo --name 签到提醒 --tag 学习通 \
      --desc "上课前一键看一眼有没有签到" --ver 1.0.0 --code 1

  python tools/add_plugin.py --del demo        # 下架（只删清单行，文件留着）

🔴 size 必须写：老内核没有 crypto.subtle 时，大小是唯一一道校验。
🔴 code 用整数、每次 +1：App 靠它比新旧（1.0.10 和 1.0.9 才不会比反）。
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUG = os.path.join(ROOT, "plugins")
INDEX = os.path.join(PLUG, "index.json")


def load():
    if not os.path.exists(INDEX):
        return {"v": 1, "plugins": []}
    with io.open(INDEX, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d.get("plugins"), list):
        d["plugins"] = []
    return d


def save(d):
    with io.open(INDEX, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="插件 .js 路径")
    ap.add_argument("--id", help="插件 id：小写字母数字和 - _，2~31 位")
    ap.add_argument("--name")
    ap.add_argument("--tag")
    ap.add_argument("--desc")
    ap.add_argument("--ver")
    ap.add_argument("--code", type=int)
    ap.add_argument("--del", dest="drop", help="下架这个 id")
    a = ap.parse_args()

    d = load()

    if a.drop:
        before = len(d["plugins"])
        d["plugins"] = [p for p in d["plugins"] if p.get("id") != a.drop]
        save(d)
        print("已下架 %s（清单 %d → %d 条，文件没删）" % (a.drop, before, len(d["plugins"])))
        return 0

    if not (a.file and a.id and a.name and a.ver):
        print("缺参数：至少要 file / --id / --name / --ver", file=sys.stderr)
        return 2
    if not os.path.exists(a.file):
        print("找不到文件：%s" % a.file, file=sys.stderr)
        return 2

    with io.open(a.file, encoding="utf-8", newline="") as f:
        code = f.read()
    raw = code.encode("utf-8")
    # 🔴 App 那边核的是 JS 的 text.length —— UTF-16 码元数，不是字节数，也不是
    #    Python 的字符数（带 emoji 时后者会少算一半，装的时候就会说「大小对不上」）。
    size = len(code.encode("utf-16-le")) // 2
    sha = hashlib.sha256(raw).hexdigest()

    rel = "plugins/%s/%s.js" % (a.id, a.ver)
    dst = os.path.join(ROOT, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(a.file, dst)

    entry = {
        "id": a.id,
        "name": a.name[:20],
        "tag": (a.tag or "插件")[:10],
        "desc": (a.desc or "")[:90],
        "ver": a.ver,
        "code": a.code or 1,
        "file": rel,
        "size": size,
        "sha256": sha,
    }
    d["plugins"] = [p for p in d["plugins"] if p.get("id") != a.id] + [entry]
    save(d)

    print("已上架 %s v%s" % (a.name, a.ver))
    print("  文件   %s" % rel)
    print("  字符数 %d（App 按这个核）" % size)
    print("  sha256 %s" % sha)
    print("  清单   plugins/index.json（现在 %d 条）" % len(d["plugins"]))
    print("\n下一步：git add plugins && git commit && git push（用户说「推」才推）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
