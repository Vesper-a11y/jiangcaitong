"""江财教务适配器 · 本地自测脚本

在你自己的电脑上运行，账号密码只在本机内存里用，不写文件、不联网传到别处。
脚本只把「解析出来的数据结构」打印到屏幕上，你看一眼确认字段对不对即可。

用法：
    python selftest_jxufe.py

会依次提示输入学号、密码（密码输入时不回显）。
"""

from __future__ import annotations

import getpass
import json
import os
import sys

import jxufe_eams as jx


def mask(value: str) -> str:
    """屏幕上脱敏：只留末 4 位。"""
    s = str(value or "")
    return s if len(s) <= 4 else "*" * (len(s) - 4) + s[-4:]


def main() -> int:
    print("江财教务适配器本地自测")
    print("密码只在本机内存使用，不会写入任何文件。\n")
    # 支持环境变量传入（便于本机一次性验证），仍不写入任何文件。
    username = os.environ.get("JXUFE_USER", "").strip() or input("学号: ").strip()
    password = os.environ.get("JXUFE_PASS", "") or getpass.getpass("密码: ")
    if not username or not password:
        print("学号和密码都不能为空")
        return 1

    client = jx.EamsClient()
    print("\n[1/5] 登录中...")
    try:
        client.login(username, password)
        print("      登录成功")
    except jx.EamsError as e:
        print("      登录失败: %s (kind=%s diag=%s)" % (e, e.kind, e.diag))
        return 2
    except Exception as e:  # noqa: BLE001
        print("      登录异常: %s" % e)
        return 2

    info = client._load_main_info()
    print("      内部代码: %s  学年: %s 学期: %s"
          % (mask(info.get("userCode", "")), info.get("xn", ""), info.get("xq", "")))

    checks = [
        ("课表", lambda: client.timetable()),
        ("成绩", lambda: client.grades()),
        ("考试", lambda: client.exams()),
        ("学期", lambda: client.semesters()),
        ("学籍", lambda: client.profile()),
    ]
    out: dict = {}
    for i, (label, fn) in enumerate(checks, start=2):
        print("\n[%d/5] 读取%s..." % (i, label))
        try:
            data = fn()
        except Exception as e:  # noqa: BLE001
            print("      失败: %s" % e)
            out[label] = {"error": str(e)}
            continue
        if label == "课表":
            print("      %d 门课, %d 周, %d 节/天"
                  % (len(data.get("courses", [])), data.get("totalWeeks", 0),
                     data.get("unitCount", 0)))
            for c in data.get("courses", [])[:3]:
                print("        - %s | %s | %s | 周%s | %s"
                      % (c.get("name"), c.get("teacher"), c.get("room"),
                         c.get("weeks"), c.get("slots")))
        elif label == "成绩":
            print("      %d 条成绩, 绩点制 %s" % (len(data.get("courses", [])),
                                              data.get("gpaScale")))
            for c in data.get("courses", [])[:3]:
                print("        - %s | 分%s | 绩点%s"
                      % (c.get("name"), c.get("score"), c.get("gpa")))
        elif label == "考试":
            print("      %d 场, %d 个轮次, 已发布=%s"
                  % (len(data.get("exams", [])), len(data.get("batches", [])),
                     data.get("published")))
        elif label == "学期":
            print("      %d 个学期, 当前=%s"
                  % (len(data), data[0].get("name") if data else ""))
        elif label == "学籍":
            print("      姓名=%s 学号=%s" % (mask(data.get("name", "")),
                                          mask(data.get("studentId", ""))))
        out[label] = "OK"

    print("\n=== 结论 ===")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("\n把上面每一项的『条数/字段』看一眼，字段都对就可以接入正式版了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
