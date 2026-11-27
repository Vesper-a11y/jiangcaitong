"""江财教务 · Cookie 版取数验证（跳过登录，不碰密码）。

用法：
  1) 浏览器登录 https://jwxt.jxufe.edu.cn （完成安全验证）
  2) F12 -> Network -> 刷新一下课表页 -> 点任意 jwxt.jxufe.edu.cn 请求
     -> Headers -> Request Headers -> 找到 `cookie:` 一行，整行复制
  3) 设置环境变量 JXUFE_COOKIE 后运行本脚本；也可直接运行后粘贴

只在本机内存使用，不写入任何文件。
"""
from __future__ import annotations
import os, re, json, getpass, urllib3, requests
import jxufe_eams as jx
urllib3.disable_warnings()

def parse_cookie(raw: str) -> dict:
    """支持 'cookie: a=b; c=d' 整行 / 纯 'a=b; c=d' / JSON 对象。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except Exception:
            pass
    # 去掉可能的 "cookie:" / "Cookie:" 前缀
    raw = re.sub(r"^\s*cookie\s*:\s*", "", raw, flags=re.I)
    jar = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k:
                jar[k] = v
    return jar

def mask(s):
    s = str(s or "")
    return s if len(s) <= 4 else "*" * (len(s) - 4) + s[-4:]

def main() -> int:
    raw = os.environ.get("JXUFE_COOKIE", "").strip()
    if not raw:
        print("请粘贴浏览器复制的 Cookie（含 JSESSIONID），输入后回车：")
        raw = input("Cookie: ").strip()
    jar = parse_cookie(raw)
    print("Cookie 字段: %d 个, JSESSIONID=%s" % (len(jar), mask(jar.get("JSESSIONID", ""))))

    client = jx.EamsClient()
    for k, v in jar.items():
        client.s.cookies.set(k, v, domain="jwxt.jxufe.edu.cn", path="/")
    client.s.verify = False

    print("\n[验证] 检查会话是否有效...")
    try:
        info = client._load_main_info()
    except Exception as e:
        print("      读取主页失败: %s" % e); return 2
    if not info or not info.get("userCode"):
        print("      没读到内部学号 userCode —— Cookie 可能过期或域名不对")
        print("      info=%s" % info)
        return 2
    print("      内部学号=%s 学年=%s 学期=%s 姓名=%s"
          % (mask(info.get("userCode", "")), info.get("xn", ""), info.get("xq", ""),
             mask(info.get("userName", ""))))

    checks = [
        ("学期", lambda: client.semesters()),
        ("课表", lambda: client.timetable()),
        ("成绩", lambda: client.grades()),
        ("考试", lambda: client.exams()),
        ("学籍", lambda: client.profile()),
    ]
    result = {}
    for label, fn in checks:
        print("\n[%s] 读取..." % label)
        try:
            data = fn()
        except Exception as e:
            print("      失败: %s" % e)
            result[label] = {"error": str(e)}
            continue
        if label == "学期":
            print("      %d 个, 当前=%s" % (len(data), data[0].get("name") if data else ""))
        elif label == "课表":
            cs = data.get("courses", [])
            print("      %d 门, %d 周, %d 节/天, 学期=%s"
                  % (len(cs), data.get("totalWeeks", 0), data.get("unitCount", 0), data.get("semester", "")))
            for c in cs[:3]:
                print("        - %s | %s | %s | 周%s | %s"
                      % (c.get("name"), c.get("teacher"), c.get("room"), c.get("weeks"), c.get("slots")))
        elif label == "成绩":
            cs = data.get("courses", [])
            print("      %d 条, 绩点制=%s" % (len(cs), data.get("gpaScale")))
            for c in cs[:3]:
                print("        - %s | 分%s | 绩点%s" % (c.get("name"), c.get("score"), c.get("gpa")))
        elif label == "考试":
            print("      %d 场, %d 轮次, 已发布=%s"
                  % (len(data.get("exams", [])), len(data.get("batches", [])), data.get("published")))
        elif label == "学籍":
            print("      姓名=%s 学号=%s" % (mask(data.get("name", "")), mask(data.get("studentId", ""))))
        result[label] = "OK"

    print("\n=== 结论 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
