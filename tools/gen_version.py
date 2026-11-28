"""发版用的 version.json / Release 正文生成器。

用法（在仓库根目录跑）：

    python3 tools/gen_version.py 1.0.4 6 https://github.com/.../jiangcaitong-1.0.4.apk

它做三件事，都在流水线里跑、不提交回仓库：

  1. 读仓库里那份 `version.json` 的 `changelog`（**手写的更新说明**）；
  2. 覆盖版本号 / versionCode / 下载地址，写回 `version.json`；
  3. 顺手写一份 `release-notes.md` 给 Release 正文用。

🔴 为什么更新说明不自动生成：Release 正文要是拼提交标题，会把内部说法
   （备份目录、跑批、接口名）漏到公开页上，措辞也不受控。
   所以说明一律以仓库 version.json 的 changelog 为准，这里只负责搬。

🔴 为什么 versionCode 也要传进来：它唯一来源是 web/app.js 的 APP_CODE
   （本地 build_apk.py 用的也是那个数）。两处各算各的，装完线上包再装本地包，
   更新检查就会一直提示有新版本。
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_notes(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as f:
            src = json.load(f)
    except FileNotFoundError:
        print("::warning::仓库里没有 version.json，更新说明会是空的")
        return []
    except Exception as e:                                    # noqa: BLE001
        print("::warning::version.json 读不了：%s" % e)
        return []
    notes = src.get("changelog") or src.get("notes") or []
    if isinstance(notes, str):
        notes = [line for line in notes.splitlines() if line.strip()]
    if not isinstance(notes, list):
        notes = []
    return [str(n).strip() for n in notes if str(n).strip()]


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    ver, code, url = argv[1], argv[2], argv[3]
    try:
        code_i = int(code)
    except ValueError:
        print("::error::versionCode 必须是数字，收到：%s" % code)
        return 2

    ver_json = os.path.join(ROOT, "version.json")
    notes = read_notes(ver_json)
    if not notes:
        print("::warning::changelog 是空的，用户点「检查更新」会看到空窗口")

    out = {"versionName": ver, "versionCode": code_i, "downloadUrl": url, "changelog": notes}
    with open(ver_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")

    body = ["## 更新内容", ""]
    body += ["- %s" % n for n in notes] or ["- 修复一些问题"]
    body += ["", "安装包：`%s`" % os.path.basename(url), ""]
    with open(os.path.join(ROOT, "release-notes.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    print("\n".join(body))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
