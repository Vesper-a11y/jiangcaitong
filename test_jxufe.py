"""用用户提供的真实样本验证江财解析器 + 与原始 JS 对拍加密。

只读本地样本文件，不联网、不碰账号。运行：python test_jxufe.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import jxufe_eams as jx

SAMPLES = r"E:\JiangCaitong\.work-hangyuantong\jxufe_samples"


def read(name: str) -> str:
    path = os.path.join(SAMPLES, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


def check(label: str, cond: bool, detail: str = "") -> bool:
    mark = "OK  " if cond else "FAIL"
    print("[%s] %s%s" % (mark, label, ("  -> " + detail) if detail else ""))
    return cond


def test_timetable() -> bool:
    print("\n=== 课表解析 ===")
    html = read("wsxk.xskcb10319.html.utf8.txt")
    out = jx.parse_timetable(html)
    cs = out["courses"]
    check("解析出课程数 >= 8", len(cs) >= 8, "实际 %d 门" % len(cs))
    check("unitCount=12", out["unitCount"] == 12)
    names = [c["name"] for c in cs]
    check("含 线性代数(工)", any("线性代数" in n for n in names), str(names[:4]))
    check("含 计算机网络（双周课）", any("计算机网络" in n for n in names))
    net = next((c for c in cs if "计算机网络" in c["name"] and c.get("parity")), None)
    if net:
        check("双周标记被识别", net.get("parity") == "双", "parity=%r weeks=%s" % (net.get("parity"), net.get("weeks")))
        check("双周只剩偶数周", all(w % 2 == 0 for w in net["weeks"]), str(net["weeks"]))
    check("教室带校区被拆出", any(c.get("campus") for c in cs),
          str([(c["room"], c["campus"]) for c in cs[:3]]))
    check("节次 0 起且 <=11", all(0 <= s["period"] <= 11
                                 for c in cs for s in c["slots"]))
    print("样例课程:", json.dumps(cs[0], ensure_ascii=False) if cs else "无")
    return len(cs) >= 8


def test_grades() -> bool:
    print("\n=== 成绩解析 ===")
    html = read("grade_data.utf8.txt")
    out = jx.parse_grades(html)
    cs = out["courses"]
    ok1 = check("解析出成绩记录 >= 5", len(cs) >= 5, "实际 %d 条" % len(cs))
    ok2 = check("绩点满分标记=5.0", out.get("gpaScale") == 5.0)
    sample = next((c for c in cs if "国家安全" in c["name"]), None)
    ok3 = check("课程代码已拆出", bool(sample and sample["code"]),
                json.dumps(sample, ensure_ascii=False) if sample else "无")
    return ok1 and ok2 and ok3


def test_exams() -> bool:
    print("\n=== 考试解析 ===")
    html = read("exam_table.utf8.txt")
    out = jx.parse_exams(html)
    ok1 = check("空考试表识别为未发布", out["published"] is False and not out["exams"])
    xml = (
        "<root><item><kslcdm>3</kslcdm><kslcmc>2026-2027学年第一学期期末考试（校考）</kslcmc></item>"
        "<item><kslcdm>4</kslcdm><kslcmc>2026-2027学年第一学期期末考试（院考）</kslcmc></item></root>"
    )
    rounds = jx.parse_exam_rounds(xml)
    ok2 = check("轮次 XML 解析出 2 项", len(rounds) == 2, str(rounds))
    return ok1 and ok2


def test_semesters() -> bool:
    print("\n=== 学期解析 ===")
    html = read("xkjg.wdkb.html.utf8.txt")
    out = jx.parse_semesters(html)
    ok1 = check("解析出学期 >= 10", len(out) >= 10, "实际 %d 项" % len(out))
    ok2 = check("当前学期 2026-0 在列", any(s["id"] == "2026-0" for s in out))
    ok3 = check("semester_parts 正确", jx.semester_parts("2026-0") == ("2026", "0"))
    return ok1 and ok2 and ok3


def test_crypto() -> bool:
    print("\n=== 3DES 与原 JS 对拍 ===")
    script = r"E:\JiangCaitong\.work-hangyuantong\jxufe_scripts\jkingo.des.js"
    if not os.path.exists(script):
        check("原始 JS 存在", False, script)
        return False
    cases = [
        ("hello", "abcd1234"),
        ("u=1234567890;;123", "a1B2c3D4"),
        ("_u123=YWJj&_p123=c2VjcmV0&randnumber=8888", "KEY7xyz9"),
    ]
    all_ok = True
    for data, key in cases:
        js = (
            "var fs=require('fs');"
            "eval(fs.readFileSync(%s,'utf8'));"
            "process.stdout.write(strEnc(%s,%s,null,null));"
        ) % (json.dumps(script), json.dumps(data), json.dumps(key))
        try:
            expect = subprocess.run(["node", "-e", js], capture_output=True,
                                    text=True, timeout=15).stdout.strip()
        except Exception as e:  # noqa: BLE001
            check("node 调用", False, str(e))
            return False
        got = __import__("jxufe_crypto").str_enc(data, key)
        all_ok &= check("strEnc(%r)" % data[:18], got == expect,
                        "py=%s js=%s" % (got[:16], expect[:16]))
    return all_ok


if __name__ == "__main__":
    results = {
        "课表": test_timetable(),
        "成绩": test_grades(),
        "考试": test_exams(),
        "学期": test_semesters(),
        "加密": test_crypto(),
    }
    print("\n=== 汇总 ===")
    for k, v in results.items():
        print("  %s: %s" % (k, "通过" if v else "失败"))
    sys.exit(0 if all(results.values()) else 1)
