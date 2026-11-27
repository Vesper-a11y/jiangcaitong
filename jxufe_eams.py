"""江西财经大学教务系统（金智 KINGOSOFT）客户端与解析器。

接口和原学校版 `eams.py` 一一对应，bridge.py 换一行 import 就能用：
    login / ensure_login / timetable / grades / exams / semesters /
    profile / plan / todo / room_options / free_rooms
异常统一收敛到 EamsError（kind 取值和原学校版一致）。

和原学校（正方 EAMS）的根本区别：
  1. 原学校登录页 SHA1(salt-密码)；江财先过统一 CAS，教务这边是 3DES + MD5 token。
  2. 原学校课表是 JS（TaskActivity + index）；江财课表是**二维 HTML 表格**。
  3. 江财有两套学号：登录学号（例如 2023123456）和教务内部代码（例如 201600012345），
     课表接口的 `xh` 用后者，必须先从 SetMainInfo.jsp 取 G_USER_CODE。
  4. 江财绩点满分 5.0（原学校 4.0）；学期有 `xq=2 第二阶段`；考试要先选轮次。

实测依据：用户 2026-11-23 提供的已登录页面样本（homes / 课表 / 成绩 / 考试）。
登录 3DES 协议由公开脚本 loginbar.js + SetKingoEncypt.jsp 逆出，**尚未用真实账号实测**。
"""

from __future__ import annotations

import base64 as _b64
import hashlib
import html as _html
import re
import time
from html.parser import HTMLParser

import requests

BASE = "https://jwxt.jxufe.edu.cn"
CAS_BASE = "https://ssl.jxufe.edu.cn"
SCHOOL_CODE = "10421"

LOGIN_URL = BASE + "/cas/login.action"
HOME_URL = BASE + "/frame/homes.action"
MAIN_INFO_URL = BASE + "/custom/js/SetMainInfo.jsp"  # 旧路径，部分部署已 404
YEARTERM_URL = BASE + "/jw/common/showYearTerm.action"  # 江财现行：返回 userCode/xn/xqM

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

POLITE_DELAY = 1.2

GPA_SCALE = 5.0  # 🔴 江财是 5 分制，不是原学校的 4 分制


class EamsError(Exception):
    """登录 / 解析 / 服务异常，统一收敛。kind 取值与原学校 eams.EamsError 一致。"""

    def __init__(self, msg: str, kind: str = "", diag: str = "") -> None:
        super().__init__(msg)
        self.kind = kind
        self.diag = diag


# ---------------------------------------------------------------- 通用工具

class _TableParser(HTMLParser):
    """把页面所有顶层 <table> 抽成 list[list[list[str]]]（表 → 行 → 单元格）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            rows: list[list[str]] = []
            self._stack.append(rows)
            if len(self._stack) == 1:
                self.tables.append(rows)
        elif tag == "tr" and self._stack:
            self._row = []
            self._stack[-1].append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell, self._buf = True, []
        elif tag == "br" and self._cell:
            self._buf.append("\n")

    def handle_data(self, data):
        if self._cell:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell and self._row is not None:
            txt = re.sub(r"[ \t\r\f\v\u00a0]+", " ", "".join(self._buf)).strip()
            self._row.append(txt)
            self._cell, self._buf = False, []
        elif tag == "tr":
            self._row, self._cell = None, False
        elif tag == "table" and self._stack:
            self._stack.pop()
            self._row = None


def parse_tables(html: str) -> list[list[list[str]]]:
    p = _TableParser()
    try:
        p.feed(html or "")
    except Exception:  # noqa: BLE001 - 半截 HTML 也要能出结果
        pass
    return p.tables


def _plain_text(html: str) -> str:
    t = re.sub(r"<script.*?</script>", " ", html or "", flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[\s\u00a0]+", " ", t).strip()


def _b64encode(s: str) -> str:
    return _b64.b64encode(s.encode("utf-8")).decode("ascii")


def _b64decode(s: str) -> str:
    return _b64.b64decode(s).decode("utf-8", "replace")


# ---------------------------------------------------------------- 学期

def parse_semesters(html: str) -> list[dict]:
    """课表页的学期下拉：`<option value="2026-0">2026-2027学年第一学期</option>`。

    返回 `[{"id": "2026-0", "name": "...", "xn": "2026", "xq": "0"}]`。
    id 直接就是课表 URL 里的 xn-xq，不需要再转换。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for mo in re.finditer(
            r'<option[^>]*value="(\d{4}-[012])"[^>]*>([^<]*)</option>', html or ""):
        sid, name = mo.group(1), _html.unescape(mo.group(2)).strip()
        if not name or sid in seen:
            continue
        seen.add(sid)
        xn, xq = sid.split("-")
        out.append({"id": sid, "name": name, "xn": xn, "xq": xq})
    return out


def parse_semester_json(text: str) -> list[dict]:
    """解析 /frame/droplist/getDropLists.action 返回的学期 JSON。"""
    import json
    try:
        data = json.loads(text or "[]")
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        xn, xq = semester_parts(sid)
        if xn and name:
            out.append({"id": sid, "name": name, "xn": xn, "xq": xq})
    return out


def semester_parts(semester_id: str) -> tuple[str, str]:
    """`"2026-0"` → `("2026", "0")`；空/异常时返回 ("", "")。"""
    m = re.fullmatch(r"(\d{4})-([012])", (semester_id or "").strip())
    return (m.group(1), m.group(2)) if m else ("", "")


# ---------------------------------------------------------------- 课表

# 二维表一个格子的课程文本，形如：
#   <div style="..."><font ...>线性代数(工)</font><br>孙翠英 <br>2-17[1-2]<br>麦三教3302(麦庐园校区)</div>
# 也可能 `2-17 双 [6-7]`（单双周）、`2-17[3-5]`（跨节）、多段 `<div>`。
_CELL_NAME_RE = re.compile(r"<font[^>]*>\s*(.*?)\s*</font>", re.S | re.I)
_WEEK_SLOT_RE = re.compile(
    r"(\d{1,2})\s*(?:-\s*(\d{1,2}))?\s*(单|双)?\s*\[\s*(\d{1,2})\s*-\s*(\d{1,2})\s*\]")
_ROOM_CAMPUS_RE = re.compile(r"^(.*?)\s*[（(]\s*([^（）()]*校区)\s*[）)]\s*$")

# 二维表里的大节行标签 → 该行的基准节次（用于格子文本没有 [x-y] 时兜底）
_ROW_BASE_PERIOD = {"一": 1, "二": 3, "三": 6, "四": 8, "五": 10}


def _course_from_cell(cell_html: str, day: int, row_period: int) -> dict | None:
    """解析一个课表格子。day: 0=周一；row_period: 该行基准节次（1 起）。"""
    raw = cell_html or ""
    # 课表格子已经过 HTMLParser，是「课程名\n教师\n周次[节次]\n教室」的纯文本
    plain_parts = [_html.unescape(x).strip()
                   for x in re.split(r"[\n\r]+|<br\s*/?>", raw, flags=re.I)]
    plain_parts = [x for x in plain_parts if x]
    if not plain_parts:
        return None

    mname = _CELL_NAME_RE.search(raw)
    if mname:
        name = _html.unescape(re.sub(r"<[^>]+>", "", mname.group(1))).strip()
    else:
        name = plain_parts[0]
    if not name:
        return None

    # 教师 / 周次节次 / 教室：课程名之后的几行按内容特征认
    after = plain_parts[1:] if mname else plain_parts[1:]
    teacher, week_text, room = "", "", ""
    for p in after:
        if _WEEK_SLOT_RE.search(p):
            week_text = p
            continue
        # 教室行：带「校区/楼/教室/场/馆/实训」等，且周次已出现过之后的行
        if week_text and re.search(r"校区|教学楼|教室|大楼|[楼主][\dA-Z]|场[CT\d]|馆|实训", p):
            room = p
        elif not teacher:
            teacher = p

    weeks: list[int] = []
    periods: list[int] = []
    parity = ""
    mws = _WEEK_SLOT_RE.search(week_text or raw)
    if mws:
        w0 = int(mws.group(1))
        w1 = int(mws.group(2)) if mws.group(2) else w0
        parity = mws.group(3) or ""
        p0, p1 = int(mws.group(4)), int(mws.group(5))
        if w1 < w0:
            w0, w1 = w1, w0
        weeks = list(range(w0, w1 + 1))
        periods = list(range(p0, p1 + 1))
    else:
        periods = [row_period] if row_period else [1]
        weeks = []

    if parity:
        keep_odd = parity == "单"
        weeks = [w for w in weeks if (w % 2 == 1) == keep_odd]

    code = ""
    mc = re.match(r"^\[([^\]]+)\]", name)
    if mc:
        code = mc.group(1)

    campus = ""
    mroom = _ROOM_CAMPUS_RE.match(room)
    if mroom:
        room, campus = mroom.group(1).strip(), mroom.group(2).strip()

    return {
        "name": name,
        "code": code,
        "teacher": teacher,
        "room": room,
        "campus": campus,
        "weeks": weeks,
        "parity": parity,
        "periods": periods,
        "day": day,
    }


def parse_timetable(html: str) -> dict:
    """金智二维课表 → 与原学校版同形状的结构。

    格子所在列决定 day，行内 `[x-y]` 决定节次（period = 节次-1，0 起）。
    `unitCount` 取江财一天最大节次 12（1-2/3-4/6-7/8-9/10-12，无第 5 节）。
    """
    tables = parse_tables(html)
    # 第一张表是「学号/姓名/班级」，第二张才是课表网格（表头含星期一…日）
    grid: list[list[str]] = []
    for t in tables:
        if any("星期一" in (c or "") for r in t for c in r[:2]):
            grid = t
            break
    if not grid:
        grid = tables[1] if len(tables) > 1 else []

    raw_courses: list[dict] = []
    for row in grid:
        # 跳过表头（星期一…日）
        if any("星期" in (c or "") for c in row):
            continue
        # 前两格是「上午/下午」+ 节次标签（一/二/三/四/五）→ 定基准节次
        base = 0
        for c in row[:2]:
            t = (c or "").replace("\n", "")
            if t in _ROW_BASE_PERIOD:
                base = _ROW_BASE_PERIOD[t]
                break
            one = re.fullmatch(r"[一二三四五]", t)
            if one:
                base = _ROW_BASE_PERIOD[t]
                break
        # 行结构：['上\n午','一',周一..周日] 或 ['二',周一..周日]（上午跨行时少一格）
        # 从右往左取 7 格最稳：末尾恰好是周日…周一
        cells = [c for c in row if c is not None]
        day_cells = cells[-7:] if len(cells) >= 7 else []
        if len(cells) >= 9:
            day_cells = cells[2:9]
        for i, cell in enumerate(day_cells[:7]):
            if not cell:
                continue
            c = _course_from_cell(cell, i, base)
            if c:
                raw_courses.append(c)

    # 同一门课（同名/同教师/同教室/同周次/同单双周）合并，格子取并集
    merged: dict[tuple, dict] = {}
    for c in raw_courses:
        key = (c["name"], c["teacher"], c["room"], tuple(c["weeks"]), c["parity"])
        if key not in merged:
            merged[key] = {
                "name": c["name"], "code": c["code"], "teacher": c["teacher"],
                "room": c["room"], "campus": c["campus"], "weeks": c["weeks"],
                "parity": c["parity"], "slots": [],
            }
        out = merged[key]
        for p in c["periods"]:
            slot = {"day": c["day"], "period": p - 1}
            if slot not in out["slots"]:
                out["slots"].append(slot)

    courses = sorted(merged.values(),
                     key=lambda x: (x["slots"][0]["day"], x["slots"][0]["period"])
                     if x["slots"] else (9, 9))

    total_weeks = max((max(c["weeks"]) for c in courses if c["weeks"]), default=20)

    xn = ""
    mxn = re.search(r'id="xn"[^>]*value="(\d{4})"', html or "")
    if mxn:
        xn = mxn.group(1)
    xq = ""
    mxq = re.search(r'id="xq_m"[^>]*value="(\d)"', html or "")
    if mxq:
        xq = mxq.group(1)

    out = {
        "unitCount": 12,
        "courses": courses,
        "totalWeeks": total_weeks,
        "weekStart": 1,
        "semester": "",
    }
    if xn:
        out["semester"] = _semester_name(xn, xq)
    return out


def _semester_name(xn: str, xq: str) -> str:
    xn2 = str(int(xn) + 1) if xn else ""
    names = {"0": "第一学期", "1": "第二学期", "2": "第二阶段"}
    return "%s-%s学年%s" % (xn, xn2, names.get(xq, ""))


# ---------------------------------------------------------------- 成绩

def parse_grades(html: str) -> dict:
    """成绩明细 `xscj.stuckcj_data10421.jsp`。

    表头（11 列）：序号/课程环节/学分/类别/修读性质/考核方式/成绩/获得学分/绩点/学分绩点/备注。
    课程名带 `[代码]` 前缀。表头上方写着「学年学期：2025-2026学年第一学期」。
    """
    tables = parse_tables(html)
    detail: list[dict] = []
    term_label = ""
    mt = re.search(r"学年学期[：:]\s*([^\s<]+(?:\s*学年第?[^\s<]+)?)", _plain_text(html))
    if mt:
        term_label = mt.group(1)

    for rows in tables:
        head = [(c or "").strip() for c in rows[0]] if rows else []
        if "课程" not in "".join(head) or "成绩" not in "".join(head):
            continue
        idx: dict[str, int] = {}
        for i, h in enumerate(head):
            if "课程" in h and "name" not in idx:
                idx["name"] = i
            elif h == "学分" and "credit" not in idx:
                idx["credit"] = i
            elif h == "成绩" and "score" not in idx:
                idx["score"] = i
            elif h == "绩点" and "gpa" not in idx:
                idx["gpa"] = i
            elif "获得学分" in h and "earned" not in idx:
                idx["earned"] = i
            elif "修读性质" in h and "nature" not in idx:
                idx["nature"] = i
            elif "考核" in h and "form" not in idx:
                idx["form"] = i
            elif "类别" in h and "category" not in idx:
                idx["category"] = i
            elif "学分绩点" in h:
                idx["creditGpa"] = i
        if "name" not in idx or "score" not in idx:
            continue

        for r in rows[1:]:
            if len(r) < 3:
                continue
            nm = (r[idx["name"]] if idx["name"] < len(r) else "").strip()
            if not nm:
                continue
            mc = re.match(r"^\[([^\]]+)\]\s*(.*)$", nm)
            code, name = (mc.group(1), mc.group(2).strip()) if mc else ("", nm)
            rec = {
                "term": term_label,
                "code": code,
                "name": name,
                "credit": (r[idx["credit"]] if "credit" in idx and idx["credit"] < len(r) else ""),
                "score": (r[idx["score"]] if idx["score"] < len(r) else ""),
                "gpa": (r[idx["gpa"]] if "gpa" in idx and idx["gpa"] < len(r) else ""),
                "earned": (r[idx["earned"]] if "earned" in idx and idx["earned"] < len(r) else ""),
                "nature": (r[idx["nature"]] if "nature" in idx and idx["nature"] < len(r) else ""),
                "form": (r[idx["form"]] if "form" in idx and idx["form"] < len(r) else ""),
                "category": (r[idx["category"]] if "category" in idx and idx["category"] < len(r) else ""),
            }
            # 非数值成绩（优秀/良好/合格/免修）也保留，前端自己显示
            detail.append(rec)

    return {"summary": [], "overall": None, "courses": detail,
            "empty": not detail, "gpaScale": GPA_SCALE}


# ---------------------------------------------------------------- 考试

def parse_exam_rounds(xml: str) -> list[dict]:
    """考试轮次 XML（ClassPathLoader `examArrangeInfo`）→ `[{"id","name"}]`。"""
    out: list[dict] = []
    for item in re.findall(r"<item[^>]*>(.*?)</item>", xml or "", re.S):
        mid = re.search(r"<kslcdm>\s*(.*?)\s*</kslcdm>", item, re.S)
        mnm = re.search(r"<kslcmc>\s*(.*?)\s*</kslcmc>", item, re.S)
        if mid or mnm:
            out.append({
                "id": _html.unescape(mid.group(1)) if mid else "",
                "name": _html.unescape(mnm.group(1)) if mnm else "",
            })
    return out


_EXAM_COLS = {
    "课程": "name", "课程名称": "name", "课程序号": "code",
    "考试类别": "category", "考试形式": "form",
    "考试日期": "date", "考试时间": "time", "考试地点": "room",
    "考场": "room", "座位号": "seat", "考场座位号": "seat",
    "考试情况": "status", "备注": "note",
}


def parse_exams(html: str) -> dict:
    """考试安排表 `ksap.ksapb_date.jsp`。没排考时页面写「没有检索到记录」。"""
    if "没有检索到记录" in (html or ""):
        return {"exams": [], "published": False}
    for rows in parse_tables(html):
        if not rows:
            continue
        head = [(c or "").strip() for c in rows[0]]
        if not any(k in "".join(head) for k in ("考试", "课程", "地点")):
            continue
        idx: dict[str, int] = {}
        for i, h in enumerate(head):
            key = _EXAM_COLS.get(h)
            if key and key not in idx:
                idx[key] = i
        out = []
        for r in rows[1:]:
            if not any((c or "").strip() for c in r):
                continue
            rec = {}
            for k, i in idx.items():
                rec[k] = (r[i] or "").strip() if i < len(r) else ""
            if not rec.get("name"):
                continue
            out.append(rec)
        return {"exams": out, "published": bool(out)}
    return {"exams": [], "published": False}


# ---------------------------------------------------------------- 学籍 / 主页

def parse_main_info(html: str) -> dict:
    """从 SetMainInfo.jsp 抽学号 / 姓名 / 学校代码 / 学年学期。"""
    out: dict = {}
    pats = {
        "loginId": r"G_LOGIN_ID\s*=\s*['\"]([^'\"]+)['\"]",
        "userCode": r"G_USER_CODE\s*=\s*['\"]([^'\"]+)['\"]",
        "userName": r"G_USER_NAME\s*=\s*['\"]([^'\"]+)['\"]",
        "schoolCode": r"G_SCHOOL_CODE\s*=\s*['\"]([^'\"]+)['\"]",
        "xn": r"_currentXn\s*=\s*['\"]([^'\"]+)['\"]",
        "xq": r"_currentXq\s*=\s*['\"]([^'\"]+)['\"]",
    }
    for k, p in pats.items():
        m = re.search(p, html or "")
        if m:
            out[k] = m.group(1)
    return out


def parse_yearterm_info(text: str) -> dict:
    """解析 /jw/common/showYearTerm.action 的 JSON（江财现行主信息源）。"""
    import json
    try:
        d = json.loads(text or "{}")
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(d, dict):
        return {}
    return {
        "loginId": str(d.get("loginId") or d.get("gh") or ""),
        "userCode": str(d.get("userCode") or ""),
        "xn": str(d.get("xn") or ""),
        "xq": str(d.get("xqM") or d.get("xq") or ""),
        "yearTermId": str(d.get("jwUserYeartermId") or ""),
        "schoolCode": str(d.get("schoolCode") or SCHOOL_CODE),
    }


def parse_baseinfo_xml(xml: str) -> dict:
    """解析 /STU_BaseInfoAction.do?hidOption=InitData 的学籍 XML。"""
    def tag(name: str) -> str:
        m = re.search(r"<%s>(.*?)</%s>" % (name, name), xml or "", re.S)
        return _html.unescape(m.group(1)).strip() if m else ""
    out = {
        "studentId": tag("yhxh") or tag("xh"),
        "name": tag("xm"),
        "className": tag("bjmc"),
        "major": tag("zymc"),
        "college": tag("yxb"),
        "gender": tag("xb"),
        "nativePlace": tag("sysf"),
        "birthday": tag("csrq"),
        "admissionYear": tag("rxnj"),
        "counselor": tag("fdy"),
        "phone": tag("dh"),
        "email": tag("dzyx"),
    }
    return {k: v for k, v in out.items() if v}


def parse_profile(html: str) -> dict:
    """学籍 / 课表首页表格里的「学号 / 姓名 / 所在班级」。"""
    out: dict = {}
    for rows in parse_tables(html):
        for r in rows:
            for i, c in enumerate(r):
                t = (c or "").strip()
                if t.startswith("学号") and ":" in t:
                    out["studentId"] = t.split(":", 1)[1].strip()
                elif t.startswith("学号") and "：" in t:
                    out["studentId"] = t.split("：", 1)[1].strip()
                elif t.startswith("姓名"):
                    v = re.sub(r"^姓名[：:]\s*", "", t)
                    if v and v != t:
                        out["name"] = v
                elif t.startswith("所在班级"):
                    v = re.sub(r"^所在班级[：:]\s*", "", t)
                    if v and v != t:
                        out["className"] = v
    return out


# ---------------------------------------------------------------- 客户端

class EamsClient:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            # 江财金智对 iframe 内页面做来源校验，缺这些会被打到 405.jsp
            "Referer": HOME_URL,
            "Sec-Fetch-Dest": "iframe",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
        })
        self.username = ""
        self.user_code = ""       # 教务内部代码（课表 xh 用）
        self._last = 0.0
        self._logged_in = False
        self._semesters: list[dict] = []
        self._exam_rounds: list[dict] = []
        self._profile_cache: dict = {}

    # -- 内部请求
    def _get(self, path: str, **kw) -> requests.Response:
        gap = time.time() - self._last
        if gap < POLITE_DELAY:
            time.sleep(POLITE_DELAY - gap)
        r = self.s.get(path if path.startswith("http") else BASE + path,
                       timeout=30, **kw)
        r.encoding = "gbk" if "jsp" in path or "action" in path else r.encoding
        self._last = time.time()
        return r

    def _post(self, path: str, data: dict, referer: str = "",
              headers: dict | None = None) -> requests.Response:
        gap = time.time() - self._last
        if gap < POLITE_DELAY:
            time.sleep(POLITE_DELAY - gap)
        hs = {"Origin": BASE}
        if referer:
            hs["Referer"] = referer if referer.startswith("http") else BASE + referer
        if headers:
            hs.update(headers)
        headers = hs
        r = self.s.post(path if path.startswith("http") else BASE + path,
                        data=data, timeout=40, headers=headers,
                        allow_redirects=True)
        r.encoding = "gbk"
        self._last = time.time()
        return r

    @property
    def logged_in(self) -> bool:
        return self._logged_in

    def _login_page(self) -> tuple[str, str, str, str]:
        """取教务登录页，返回 (deskey, ssessionid, randnumber)。

        登录页里写着 `var _sessionid = "..."`，3DES 密钥由
        `/frame/homepage?method=getTempDeskey` 动态下发（不用写死）。
        """
        try:
            r = self._get(LOGIN_URL, headers={"Cache-Control": "no-cache"})
        except requests.RequestException:
            raise EamsError("连不上教务系统，请检查网络后重试", "net") from None
        body = r.text or ""
        if not body or r.status_code >= 500:
            raise EamsError("学校教务系统出错，请稍后再试", "net", "login_%d" % r.status_code)
        ms = re.search(r'var\s+_ssessionid\s*=\s*["\']([^"\']+)["\']', body)
        if not ms:
            ms = re.search(r'var\s+_sessionid\s*=\s*["\']([^"\']+)["\']', body)
        ssid = ms.group(1) if ms else ""
        mr = re.search(r'id="randnumber"[^>]*value="([^"]*)"', body)
        rand = mr.group(1) if mr else ""
        try:
            dk = self._get("/frame/homepage?method=getTempDeskey").text.strip()
        except requests.RequestException:
            dk = ""
        # getEncParams 里的 timestamp 来自同一接口，token=md5(md5(params)+md5(timestamp))
        try:
            nowtime = self._get("/frame/homepage?method=getTempNowtime").text.strip()
        except requests.RequestException:
            nowtime = ""
        if not ssid or not dk or not nowtime:
            raise EamsError("教务登录页没给全加密参数，请稍后重试", "page",
                            "ssid_%d_dk_%d_ts_%d" % (len(ssid), len(dk), len(nowtime)))
        return dk, ssid, rand, nowtime

    def login(self, username: str, password: str) -> None:
        """金智教务登录：用户名 base64(user;;ssid)，密码 3DES(params) + MD5 token。

        3DES 用 jkingo.des.js 的 `strEnc`（3 密钥 ECB + base64 + 十六进制串）。
        这里用 pycryptodome 实现；没装会给出明确提示。
        """
        dk, ssid, rand, nowtime = self._login_page()
        try:
            from jxufe_crypto import enc_params  # 本地模块，避免主文件依赖加密库
        except ImportError:
            raise EamsError("缺少加密模块 jxufe_crypto.py，请检查安装", "page") from None

        uname_enc = _b64encode(username + ";;" + ssid)
        params = (
            "_u%s=%s&_p%s=%s&randnumber=%s&isPasswordPolicy=0"
            "&txt_mm_expression=&txt_mm_length=&txt_mm_userzh="
            "&hid_flag=1&hidlag=1&hid_dxyzm="
        ) % (rand, uname_enc, rand, password, rand)
        body = enc_params(params, dk, nowtime) + "&deskey=" + dk + "&ssessionid=" + ssid
        try:
            r = self._post("/cas/logon.action", data=self._parse_qs(body),
                           referer=LOGIN_URL)
        except requests.RequestException:
            raise EamsError("连不上教务系统，请检查网络后重试", "net") from None
        raw = r.text or ""
        landed = r.url or ""
        # 成功：服务端回 JSON {status:"200", result:"..."} 或直接跳到 frame/homes
        ok = False
        try:
            import json
            data = json.loads(raw)
            if str(data.get("status")) == "200":
                ok = True
                dest = data.get("result") or ""
                if dest:
                    self._get(dest if dest.startswith("http") else BASE + dest)
        except Exception:  # noqa: BLE001
            ok = ("homes.action" in landed or "frame/homes" in raw
                  or "SetMainInfo" in raw)
        if not ok:
            if re.search(r"密码|口令", raw) and re.search(r"错误|不对|不正确|有误", raw):
                raise EamsError("学号或密码不对", "auth")
            if "验证码" in raw:
                raise EamsError("学校要求输入验证码，本版本暂不支持", "page")
            raise EamsError("登录未成功，请稍后再试", "page",
                            "login_%d_%d" % (r.status_code, len(raw)))

        self.username = username
        self._logged_in = True
        self._load_main_info()
        # 预取学期列表，考试/课表翻学期要用
        self._semesters = self._fetch_semesters()

    @staticmethod
    def _parse_qs(s: str) -> dict:
        out = {}
        for part in (s or "").split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k] = v
        return out

    def _fetch_semesters(self) -> list[dict]:
        """江财现行学期列表来自 droplist 接口，旧版藏在课表页 option 里。"""
        try:
            r = self._post(
                "/frame/droplist/getDropLists.action",
                {"comboBoxName": "StMsXnxqDxDesc", "paramValue": "", "isYXB": "0",
                 "isCDDW": "0", "isXQ": "0", "isDJKSLB": "0", "isZY": "0"},
                referer="/student/xkjg.wdkb.jsp?menucode=S20301",
                headers={"X-Requested-With": "XMLHttpRequest",
                         "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors"},
            )
            out = parse_semester_json(r.content.decode("utf-8", "replace"))
            if out:
                return out
        except Exception:  # noqa: BLE001
            pass
        try:
            h = self._get("/student/xkjg.wdkb.jsp?menucode=S20301").text
            return parse_semesters(h)
        except Exception:  # noqa: BLE001
            return []

    def _load_main_info(self) -> dict:
        """取教务内部学号 / 当前学年学期。

        江财现行部署由 showYearTerm.action 返回 JSON；老版 SetMainInfo.jsp
        路径在部分环境已 404，保留作兜底。
        """
        try:
            info = parse_yearterm_info(
                self._post("/jw/common/showYearTerm.action", data={})
                .content.decode("utf-8", "replace"))
            if info.get("userCode"):
                self.user_code = info["userCode"]
                return info
        except Exception:  # noqa: BLE001
            pass
        fallback: dict = {}
        for path in (MAIN_INFO_URL, "/frame/js/SetMainInfo.jsp",
                     "/js/SetMainInfo.jsp", HOME_URL):
            try:
                text = self._get(path).text or ""
            except Exception:  # noqa: BLE001
                continue
            info = parse_main_info(text)
            if info.get("userCode") or info.get("loginId"):
                self.user_code = info.get("userCode") or ""
                return info
            if not fallback and info:
                fallback = info
        self.user_code = fallback.get("userCode") or ""
        return fallback

    def ensure_login(self, username: str, password: str) -> None:
        if not self._logged_in:
            self.login(username, password)

    def _tt_params(self, semester_id: str) -> tuple[str, str, str]:
        xn, xq = semester_parts(semester_id)
        if not xn:
            info = self._load_main_info()
            xn = info.get("xn") or "2026"
            xq = info.get("xq") or "0"
        xh = self.user_code or self._load_main_info().get("userCode") or ""
        return xn, xq, xh

    def timetable(self, semester_id: str = "", semester_name: str = "") -> dict:
        xn, xq, xh = self._tt_params(semester_id)
        if not xh:
            raise EamsError("没拿到教务内部学号，请稍后重试", "readfail")
        params = _b64encode("xn=%s&xq=%s&xh=%s" % (xn, xq, xh))
        r = self._get("/student/wsxk.xskcb10319.jsp?params=" + params)
        body = r.text or ""
        if re.search(r'name="password"|cas/login', body, re.I):
            raise EamsError("登录状态已过期，请重新绑定", "auth")
        if "课表" not in body and "mytable" not in body:
            raise EamsError("课表这次没读到，请稍后重试", "readfail",
                            "blank_%d" % len(body))
        out = parse_timetable(body)
        if semester_name:
            out["semester"] = semester_name
        elif not out.get("semester"):
            out["semester"] = _semester_name(xn, xq)
        return out

    def grades(self) -> dict:
        xn, xq, xh = self._tt_params("")
        try:
            admission = self.profile().get("admissionYear") or ""
        except Exception:  # noqa: BLE001
            admission = ""
        admission = admission or str(int(xn) - 1 if xn.isdigit() else 2025)
        # 江财成绩页靠 setPageParams() 填一堆 hidden，少一个就返回空壳
        data = {
            "xn": xn, "xn1": str(int(xn) + 1) if xn.isdigit() else "",
            "xq": xq, "xh": xh,
            "ysyxS": "on", "sjxzS": "on",
            "ysyx": "yxcj",      # 有效成绩
            "sjxz": "sjxz1",     # 入学以来全部
            "zx": "1", "fx": "0", "wz": "0",
            "rxnj": admission, "nj": admission,
            "zxC": "on", "xsjd": "1",
        }
        r = self._post("/student/xscj.stuckcj_data10421.jsp", data,
                       referer="/student/xscj.stuckcj.jsp?menucode=S40303")
        body = r.text or ""
        if re.search(r'name="password"|cas/login', body, re.I):
            raise EamsError("登录状态已过期，请重新绑定", "auth")
        if "成绩" not in body:
            raise EamsError("成绩页这次没读到，请稍后重试", "readfail")
        out = parse_grades(body)
        if out["empty"]:
            out["empty"] = True
        return out

    def profile(self) -> dict:
        if self._profile_cache:
            return self._profile_cache
        try:
            r = self._post(
                "/STU_BaseInfoAction.do?hidOption=InitData&menucode_current=S10101",
                {}, referer="/student/stu.xsxj.xjda.jbxx.html?menucode=S10101",
                headers={"X-Requested-With": "XMLHttpRequest",
                         "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors"},
            )
            p = parse_baseinfo_xml(r.content.decode("gbk", "replace"))
            if p.get("studentId"):
                self._profile_cache = p
                return p
        except Exception:  # noqa: BLE001
            pass
        try:
            h = self._get("/student/xkjg.wdkb.jsp?menucode=S20301").text
            p = parse_profile(h)
            info = self._load_main_info()
            if info.get("loginId"):
                p.setdefault("studentId", info["loginId"])
            if info.get("userName"):
                p.setdefault("name", info["userName"])
            if p.get("studentId"):
                self._profile_cache = p
                return p
        except Exception:  # noqa: BLE001
            pass
        raise EamsError("学籍信息没读到，请稍后重试", "readfail")

    def _exam_meta(self) -> list[dict]:
        """考试轮次：classPath.xml → STU_DynamicInitDataAction.do 的 XML。"""
        if self._exam_rounds:
            return self._exam_rounds
        try:
            cp_xml = self._get("/student/data/classPath.xml").content.decode("gbk", "replace")
            m = re.search(r"<examArrangeInfo>(.*?)</examArrangeInfo>",
                          cp_xml, re.S | re.I)
            if m:
                cp = m.group(1).strip()
                r = self._post(
                    "/STU_DynamicInitDataAction.do?classPath=" + cp, {},
                    referer="/student/ksap.ksapb.html?menucode=S20403",
                    headers={"X-Requested-With": "XMLHttpRequest",
                             "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors"},
                )
                self._exam_rounds = parse_exam_rounds(
                    r.content.decode("gbk", "replace"))
        except Exception:  # noqa: BLE001
            self._exam_rounds = []
        return self._exam_rounds

    def exams(self, semester_id: str = "") -> dict:
        xn, xq, xh = self._tt_params(semester_id)
        rounds = self._exam_meta()
        all_rows: list[dict] = []
        seen = set()
        targets = [r_["id"] for r_ in rounds if r_.get("id")] or [""]
        for rid in targets:
            data = {"xn": xn, "xq": xq, "xh": xh, "xxlc": rid}
            r = self._post("/student/ksap.ksapb_date.jsp", data,
                           referer="/student/ksap.ksapb.html?menucode=S20403")
            got = parse_exams(r.text or "")
            for e in got["exams"]:
                key = (e.get("name"), e.get("date"), e.get("room"))
                if key in seen:
                    continue
                seen.add(key)
                all_rows.append(e)
        return {
            "exams": all_rows,
            "published": bool(all_rows),
            "batches": rounds,
            "semesters": self._semesters,
        }

    def semesters(self) -> list[dict]:
        if not self._semesters:
            self._semesters = self._fetch_semesters()
        return self._semesters

    # -- 原学校有、江财暂无对应入口的功能：如实说不支持，不伪装成「没有」
    def todo(self) -> dict:
        return {"items": []}

    def room_options(self) -> dict:
        raise EamsError("江财教务暂未提供空闲教室查询入口", "nottable")

    def free_rooms(self, *a, **kw) -> dict:
        raise EamsError("江财教务暂未提供空闲教室查询入口", "nottable")


__all__ = [
    "EamsClient", "EamsError", "BASE", "GPA_SCALE",
    "parse_timetable", "parse_grades", "parse_exams", "parse_exam_rounds",
    "parse_semesters", "parse_profile", "parse_main_info",
    "parse_tables", "semester_parts",
]
