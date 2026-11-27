"""江财教务系统（金智 EAMS）客户端与解析器。

只做**读**：登录、取课表、取成绩、取学籍。没有任何提交动作。

实测要点（2026-10-18）：
  1. 密码 = SHA1('<salt>-' + 明文)，而 salt 是**每次 GET 登录页随机生成的 UUID**，
     必须从本次响应里现抓，写死常量会一直报「账号或密码错误」。
  2. GET 登录页后立刻 POST 会被「请不要过快点击」挡，中间必须停几秒。
  3. 登录页没有验证码。
  4. 课表是 JS 片段：`new TaskActivity(...)` + `index =D*unitCount+M;`
     ⇒ index = 星期 * unitCount + 节次（星期 0=周一，节次 0=第一节）。
"""

from __future__ import annotations

import hashlib
import html as _html
import re
import time
from html.parser import HTMLParser

import requests

BASE = "http://jwglxt.zua.edu.cn"
LOGIN_URL = BASE + "/eams/loginExt.action"
HOME_URL = BASE + "/eams/homeExt.action"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 两次请求之间的最小间隔（秒），别把学校服务器打疼
POLITE_DELAY = 1.2

# 学生内部编号：课表页里由 JS 拼进表单的那一行。
#   `bg.form.addInput(form,"ids","721812");`
# 🔴 2026-11-06 放宽：以前写死了逗号后没有空格、双引号，学校换个前后端版本
#    （多个空格 / 单引号）就一个都匹配不上，表现出来就是「拿不到编号」。
IDS_RE = re.compile(
    r'addInput\(\s*form\s*,\s*["\']ids["\']\s*,\s*["\'](\d+)["\']\s*\)')

# 同一页里还有两种写法也带这个编号，任何一条都够用：
#   var ids = "721812";        /  ids = '721812'
#   <input type="hidden" name="ids" value="721812">
# 🔴 2026-11-06：一个人一直绑不上，报的是「学校没给出课表」。多种写法各试一遍，
#    别把「正则没认出来」说成「学校没给」。
IDS_ALT_RES = (
    re.compile(r'\bids\s*=\s*["\'](\d+)["\']'),
    re.compile(r'name\s*=\s*["\']ids["\'][^>]*value\s*=\s*["\'](\d+)["\']'),
    re.compile(r'value\s*=\s*["\'](\d+)["\'][^>]*name\s*=\s*["\']ids["\']'),
)

# 编号这一行在、但值是空的：学生没有班级 / 课表还没排。
#    `addInput(form,"ids","");`
IDS_EMPTY_RE = re.compile(
    r'addInput\(\s*form\s*,\s*["\']ids["\']\s*,\s*["\']\s*["\']\s*\)')

# 🔴 2026-11-06 晚：课表页里那行 `ids` **有两支**，靠同一段 JS 的 if/else 分开。
#    真实页面（_lab/out/pg_timetable.html）长这样：
#      if (jQuery("#courseTableType").val()=="std") {
#          bg.form.addInput(form,"ids","721812");   ← 学生编号
#      } else {
#          bg.form.addInput(form,"ids","6587");     ← 班级编号
#      }
#    以前无脑取第一个数字，在普通学生身上碰巧对（学生那支写在前面）。
#    换个页面（辅修 / 双学位的课表页）顺序一变，拿到的是班级编号，
#    查出来就是一张空课表 —— 而界面会说「学校没给出课表」，等于把
#    「我们拿错了编号」赖给学校。这里明确按语义挑，不再靠顺序。
TABLE_TYPE_RE = re.compile(
    r'courseTableType[^;]{0,80}==\s*["\']([\w]+)["\']')


def find_id_candidates(html: str) -> list[dict]:
    """课表页里所有可能的编号，按可信度排序并去重。

    返回 `[{"id": "721812", "kind": "std"}, {"id": "6587", "kind": "class"}]`。
    `kind`：`std` = 学生编号（首选），`class` = 另一支（班级编号），
    `""` = 页面里没有 if/else 可依，说不清是哪种。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for m in IDS_RE.finditer(html or ""):
        vid = m.group(1)
        last = None
        for t in TABLE_TYPE_RE.finditer(html, 0, m.start()):
            last = t
        kind = ""
        if last is not None:
            # 同一个 if 的 else 分支里没有判断式，只能看「到这一行为止有没有 else」。
            # else 那支是「不是这个值」的那一类：判断写着 std ⇒ else 是班级；
            # 判断写着 class ⇒ else 反而是学生。写死「else 就是班级」会判反。
            after = html[last.end():m.start()]
            kv = last.group(1) or ""
            is_else = re.search(r"\belse\b", after) is not None
            if kv == "std":
                kind = "class" if is_else else "std"
            else:
                kind = "std" if is_else else "class"
        if vid not in seen:
            seen.add(vid)
            out.append({"id": vid, "kind": kind})
    if not out:
        # 别的写法（var ids / input value）只在前面的全没出现时才排得上
        for alt in IDS_ALT_RES:
            m = alt.search(html or "")
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                out.append({"id": m.group(1), "kind": ""})
                break
    out.sort(key=lambda c: 0 if c["kind"] == "std" else (1 if not c["kind"] else 2))
    return out


# 课表页里除了编号，还有三样东西能决定「这一次查得到查不到」：
#   ① `#courseTableType` 的选项 —— 有人只有「班级课表」这一支；
#   ② `name="semester.id"` 的当前值 —— 页面上写着这学期是哪个 id，比空串可靠；
#   ③ `name="project.id"` —— 有的学期挂在某个「项目」下，不带它就查不出东西。
# 这三样以前一个都没用，恒定按「std + 空学期」发。正常学生碰巧对，
# 谁的那一页稍微不一样，就查出一张空表 —— 而界面会说「还没有课表」。
_COURSE_TYPE_SEL_RE = re.compile(
    r"<select[^>]*id\s*=\s*[\"']courseTableType[\"'][^>]*>(.*?)</select>", re.I | re.S)
_OPTION_VAL_RE = re.compile(r"<option[^>]*value\s*=\s*[\"']([^\"']*)[\"']", re.I)
_SEM_HIDDEN_RE = re.compile(
    r"name\s*=\s*[\"']semester\.id[\"'][^>]*value\s*=\s*[\"']([^\"']*)[\"']", re.I)
_SEM_HIDDEN_RE2 = re.compile(
    r"value\s*=\s*[\"']([^\"']*)[\"'][^>]*name\s*=\s*[\"']semester\.id[\"']", re.I)
_PROJECT_SEL_RE = re.compile(
    r"<select[^>]*name\s*=\s*[\"']project\.id[\"'][^>]*>(.*?)</select>", re.I | re.S)
# 学期日历控件里写着这家学校「当前是哪个学期」：{...,value:"297"}
_SEM_JS_RE = re.compile(r"semesterCalendar\s*\(\s*\{[^}]*value\s*:\s*[\"'](\d+)[\"']")


def page_hints(html: str) -> dict:
    """课表页里那三样「决定参数」的东西。取不到就是空，不猜。"""
    src = html or ""
    kinds: list[str] = []
    m = _COURSE_TYPE_SEL_RE.search(src)
    if m:
        for v in _OPTION_VAL_RE.findall(m.group(1)):
            if v and v not in kinds:
                kinds.append(v)
    sem = ""
    for rex in (_SEM_HIDDEN_RE, _SEM_HIDDEN_RE2, _SEM_JS_RE):
        mm = rex.search(src)
        if mm and (mm.group(1) or "").strip():
            sem = mm.group(1).strip()
            break
    proj = ""
    m = _PROJECT_SEL_RE.search(src)
    if m:
        for v in _OPTION_VAL_RE.findall(m.group(1)):
            if v and v.strip():
                proj = v.strip()
                break
    return {"kinds": kinds, "semester": sem, "project": proj}

# 🔴 怎么判断「拿到的是课表页」而不是「被踢回登录页 / 网关吐了个空页面」：
#    正方这页一定有表单和它自己那套 JS 助手，随便哪个都在。
#    少了这个判断，「服务器 502 返回一句请先登录」会被当成「学校没排课」，
#    于是会话失效不再触发重登 —— 用户看到的是一张永远空着的课表（2026-11-06 踩到）。
TABLE_PAGE_RE = re.compile(r"courseTableForStd|bg\.form|TaskActivity|<form", re.I)

# 🔴 2026-11-13：**「查完了，这学期真没课」和「我们没读到」长得一模一样** ——
#    两者都没有 TaskActivity。以前一律当成空课表，屏幕上写「还没有课表」，
#    还顺手把本机上一份真课表覆盖掉了（有人报「课表和个人信息都没了」，
#    而那个人根本没有辅修专业，跟多编号那条路无关）。
#    区别在**响应里有没有课表骨架**：真渲染出来的课表一定有 `new CourseTable(...)`
#    / `marshalTable(...)` / `unitCount`（空学期也有，网格本身要画）。
#    一个都没有 ⇒ 那是空响应 / 网关错误页 / 参数不对被弹回来，**不是没课**。
#    🔴 2026-11-13 实测收紧：**「CourseTable」这个大字本身不算骨架。**
#    教务出错时返回的是一张 Struts2 报错页（HTTP 500），堆栈里写着模板路径
#    `/edu/zzhy/eams/teach/schedule/web/action/courseTableForStd/prompt.ftl`，
#    于是一个**报错页**命中了「骨架」，被当成「这学期没课」——正是要防的那件事。
#    所以要咬真正画表格的那几行代码：`new CourseTable(` / `.marshalTable(` / `unitCount=数字`。
TABLE_RENDER_RE = re.compile(
    r"new\s+CourseTable\s*\(|\.\s*marshalTable\s*\(|unitCount\s*=\s*\d", re.I)

# 教务的报错页（Struts2 / Freemarker）。它可能是 200 也可能是 500 ——
# 别只看状态码，正文里这几句才是它的身份证。
ERROR_PAGE_RE = re.compile(r"错误路径|Exception|Template not found|stack trace", re.I)


class EamsError(Exception):
    """登录失败 / 解析失败 / 服务器异常，都收敛成这一个。

    `kind` 用来区分「凭据问题」和「其他问题」：
      "auth"  —— 学号密码不对（或者改过密码了）。界面应该请用户重新输入。
      "net"   —— 网络没通 / 学校服务器出错。让用户等一会儿或换网络。
      "page"  —— 登录页认不出来（真的是页面变了）。
      ""      —— 其他。

    `diag` 是给排查用的证据串（形如 `page_200_5969`），不上屏。
    规则和 native.js 的 EamsError 一致 —— 两边各写一套，必须对得上。
    """

    def __init__(self, msg: str, kind: str = "", diag: str = "") -> None:
        super().__init__(msg)
        self.kind = kind
        self.diag = diag


# ---------------------------------------------------------------- 登录页
# 加盐值的两种写法（和 native.js 的 SALT_LIT / SALT_VAR 一一对应）：
#   A) CryptoJS.SHA1('<盐>-' + form['password'].value)
#   B) var pwdEncryptSalt = '<盐>'; … SHA1(pwdEncryptSalt + pwd)
# 🔴 以前写死 `[0-9a-fA-F\-]{36}`：盐只要不是 36 位 UUID，页面没改也会
#    被判成「拿不到盐值」。现在按「字面量原样 + 密码」算，连接符是什么都行。
SALT_LIT_RE = re.compile(r"(?:CryptoJS\.)?SHA1\s*\(\s*(['\"])([^'\"]{4,80})\1\s*\+")
SALT_VAR_RE = re.compile(r"pwdEncryptSalt\s*=\s*(['\"])([^'\"]{4,80})\1")
# 最后一道：字面量本身就带连接符（`SHA1('3f2a…-')`，后面不写 + 密码）。
# 现实里的页面会跟上 `+ form['password'].value`，但换个写法我们就认不出，
# 而「认不出」直接等于用户绑不上 —— 宁可多认一种。
SALT_BARE_RE = re.compile(r"(?:CryptoJS\.)?SHA1\s*\(\s*(['\"])([^'\"]{4,80}-)\1\s*\)")
CSRF_RE = re.compile(r"name=[\"']?_csrf[\"']?[^>]*value=[\"']([^\"']+)[\"']")

# 取登录页最多试几次。理由见 native.js 的 LOGIN_TRIES（同桌一份规矩）。
LOGIN_TRIES = 3


def salt_of(body: str) -> tuple[str, str]:
    """登录页里的「盐值前缀」。返回 (pre, how)，取不到是 ("", "")。

    优先取「后面紧跟 password 的那个 SHA1」—— 页面别处也可能有哈希调用，
    抓错了会一直报密码不对，比报错还糟。
    """
    s = body or ""
    first = ""
    for m in SALT_LIT_RE.finditer(s):
        tail = s[m.start():m.start() + len(m.group(0)) + 90]
        if "password" in tail.lower():
            return m.group(2), "lit"
        if not first:
            first = m.group(2)
    if first:
        return first, "lit"
    v = SALT_VAR_RE.search(s)
    if v:
        return v.group(2), "var"
    b = SALT_BARE_RE.search(s)
    if b:
        return b.group(2), "bare"
    return "", ""


def looks_home(status: int, url: str, body: str) -> bool:
    """这一页是不是「已经登录了，登录页被跳走了」。和 native.js 的 looksHome 一致。"""
    if "homeExt" in (url or ""):
        return True
    b = body or ""
    return (0 < len(b) < 900
            and bool(re.search(r"homeExt\.action|home\.action", b))
            and not re.search(r"name=['\"]?password", b, re.I))


def looks_portal(url: str, body: str) -> bool:
    """校园网网关把请求截到了它自己的登录页（srun 那套）。"""
    if re.search(r"srun|/portal|ac_id=|eportal|wlanuserip", url or "", re.I):
        return True
    return bool(re.search(r"srun_portal|上网认证|认证上网|校园网登录|网络已断开", body or ""))


def login_page_error(resp, why: str) -> EamsError:
    """登录页这件事到底出了什么。文案只说看到的事实，不猜。

    和 native.js 的 loginPageError 是同一套判断、同一批话 —— 两份实现
    必须说一样的话，否则同一个问题在网页版和 APK 上会给出两种解释。
    """
    st = resp.status_code if resp is not None else 0
    body = getattr(resp, "text", "") or ""
    ln = len(body)
    if why == "home":
        e = EamsError("上次的登录状态清不掉，重新打开一次应用再试", "session")
    elif why == "portal":
        e = EamsError("网络被校园网的登录页拦住了，先连上校园网再试", "net")
    elif why == "net" or st == 0:
        e = EamsError("连不上教务系统，请检查网络后重试", "net")
    elif st >= 500:
        e = EamsError("学校教务系统出错（%d），请稍后再试" % st, "net")
    elif re.search(r"维护|升级|暂停服务|系统繁忙|正在维护", body):
        # 页面自己写着在维护 —— 这是**它说的**，不是我们猜的。
        e = EamsError("学校教务系统在维护，请稍后再试", "net")
    elif ln == 0:
        e = EamsError("学校教务系统无返回，请稍后再试", "net")
    elif st >= 400:
        e = EamsError("教务系统的登录地址打不开了（%d）" % st, "page")
    else:
        e = EamsError("登录页的样子变了，这个版本还不认识。请点下面的"
                      "在「问题反馈」里附上日志", "page")
    e.diag = "%s_%d_%d" % (why, st, min(ln, 9999))
    return e


# ---------------------------------------------------------------- HTML 表格

class _Table:
    __slots__ = ("rows",)

    def __init__(self) -> None:
        self.rows: list[list[str]] = []


class _TableParser(HTMLParser):
    """把页面里所有顶层 <table> 抽成 list[list[str]]，够用就好。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_Table] = []
        self._stack: list[_Table] = []
        self._row: list[str] | None = None
        self._in_cell = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            t = _Table()
            self._stack.append(t)
            if len(self._stack) == 1:
                self.tables.append(t)
        elif tag == "tr" and self._stack:
            self._row = []
            self._stack[-1].rows.append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._in_cell = True
            self._buf = []
        elif tag == "br" and self._in_cell:
            self._buf.append("\n")

    def handle_data(self, data):
        if self._in_cell:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell and self._row is not None:
            txt = "".join(self._buf)
            txt = re.sub(r"[ \t\r\f\v\u00a0]+", " ", txt).strip()
            self._row.append(txt)
            self._in_cell = False
            self._buf = []
        elif tag == "tr":
            self._row = None
            self._in_cell = False
        elif tag == "table" and self._stack:
            self._stack.pop()
            self._row = None


def parse_tables(html: str) -> list[list[list[str]]]:
    p = _TableParser()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - 半截 HTML 也要能出结果
        pass
    return [t.rows for t in p.tables]


# ---------------------------------------------------------------- 课表解析

def _split_js_args(src: str) -> list[str]:
    out, cur, quote, i = [], [], None, 0
    while i < len(src):
        c = src[i]
        if quote:
            if c == "\\":
                cur.append(src[i:i + 2])
                i += 2
                continue
            if c == quote:
                quote = None
            cur.append(c)
        elif c in "\"'":
            quote = c
            cur.append(c)
        elif c == ",":
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    if cur:
        out.append("".join(cur).strip())
    return out


def _unquote(tok: str) -> str:
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    return tok


def _eval_index(expr: str, unit_count: int) -> int | None:
    e = expr.replace("unitCount", str(unit_count)).replace(" ", "")
    if not re.fullmatch(r"[0-9*+\-()]+", e):
        return None
    try:
        return int(eval(e, {"__builtins__": {}}, {}))  # noqa: S307 - 已限定字符集
    except Exception:  # noqa: BLE001
        return None


def parse_timetable(html: str) -> dict:
    """把课表 JS 片段解析成结构化数据。"""
    m = re.search(r"unitCount\s*=\s*(\d+)", html)
    unit_count = int(m.group(1)) if m else 10

    # 学期的周次范围由页面里的 `table0.marshalTable(from, startWeek, endWeek)` 给出。
    # 🔴 别用「最后一门课结束的周次」当学期总周数：江财实测 marshalTable(2,1,24)，
    #    即第 1~24 周，而最后一门课只排到第 18 周。以前取 max(课程周次) 会得到 18，
    #    导致周次选择器翻不到第 19 周以后——后半个学期的课直接看不见。
    #    （这个参数是 2026-10-18 拆 WakeUp 的 eams 适配器时顺着 table0 找到的。）
    week_start, week_end = 1, 0
    mw = re.search(r"marshalTable\s*\(\s*\d+\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", html)
    if mw:
        week_start, week_end = int(mw.group(1)), int(mw.group(2))

    # 事件流：活动定义 / 格子下标，按出现顺序配对
    events: list[tuple[int, str, str]] = []
    act_spans: list[tuple[int, int, str]] = []
    for mm in re.finditer(r"new\s+TaskActivity\((.*?)\)\s*;", html, re.S):
        events.append((mm.start(), "act", mm.group(1)))
        act_spans.append((mm.start(), mm.end(), mm.group(1)))
    for mm in re.finditer(r"index\s*=\s*([^;]+);", html):
        events.append((mm.start(), "idx", mm.group(1)))
    events.sort()

    # 教师名不是字面量，而是 actTeacherName.join(',')。
    # 每个活动前面紧挨着一段 `var actTeachers = [{id:..,name:"徐璐",lab:false}];`
    # 只能按「上一个活动结束 → 本活动开始」切片去找，不能全局搜。
    teacher_of: dict[int, str] = {}
    prev_end = 0
    for start, end, _ in act_spans:
        chunk = html[prev_end:start]
        last = None
        for m2 in re.finditer(r"actTeachers\s*=\s*\[(.*?)\]\s*;", chunk, re.S):
            last = m2
        if last:
            names = re.findall(r'name:\s*"([^"]*)"', last.group(1))
            teacher_of[start] = ",".join(dict.fromkeys(n for n in names if n))
        prev_end = end

    raw: list[dict] = []
    cur: dict | None = None
    cur_pos = -1
    for pos, kind, payload in events:
        if kind == "act":
            args = _split_js_args(payload)
            if len(args) < 7:
                cur = None
                continue
            weeks_bits = _unquote(args[6])
            # 🔴 位图的下标**就是周次本身**（char[0] 对应第 0 周，恒为 0）。
            #    早期按 i+1 算会整体偏一周，靠浏览器渲染的真实格子才核对出来。
            weeks = [i for i, ch in enumerate(weeks_bits) if ch == "1" and i >= 1]
            full_name = _unquote(args[3])
            nm = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", full_name)
            cur = {
                "name": (nm.group(1) if nm else full_name).strip(),
                "code": (nm.group(2) if nm else "").strip(),
                "teacher": teacher_of.get(pos, _unquote(args[1])),
                "room": _unquote(args[5]),
                "weeks": weeks,
                "slots": [],
            }
            cur_pos = pos
            raw.append(cur)
        else:
            if cur is None:
                continue
            idx = _eval_index(payload, unit_count)
            if idx is None or idx < 0:
                continue
            day, period = divmod(idx, unit_count)
            if 0 <= day < 7 and 0 <= period < unit_count:
                slot = {"day": day, "period": period}
                if slot not in cur["slots"]:
                    cur["slots"].append(slot)

    # 「停课」是调课记录，不是真上课，别画到格子里
    for c in raw:
        if c["room"] == "停课":
            c["slots"] = []

    # 同一门课（同名/同教师/同教室/同周次）合并
    merged: dict[tuple, dict] = {}
    for c in raw:
        if not c["slots"]:
            continue
        key = (c["name"], c["code"], c["teacher"], c["room"], tuple(c["weeks"]))
        if key in merged:
            for s in c["slots"]:
                if s not in merged[key]["slots"]:
                    merged[key]["slots"].append(s)
        else:
            merged[key] = c

    courses = sorted(merged.values(), key=lambda c: (c["slots"][0]["day"], c["slots"][0]["period"]))
    # 学期总周数：页面给的 endWeek 优先，课程周次只作兜底（页面万一没有 marshalTable）
    total_weeks = max(
        week_end,
        max((max(c["weeks"]) for c in courses if c["weeks"]), default=0),
    )

    semester = ""
    for c in courses:
        mm = re.match(r"^(\d{2})(\d{2})(\d)\.", c["code"])
        if mm:
            semester = "20%s-20%s 学年第 %s 学期" % (mm.group(1), mm.group(2), mm.group(3))
            break

    return {
        "unitCount": unit_count,
        "courses": courses,
        "totalWeeks": total_weeks,
        "weekStart": week_start,
        "semester": semester,
    }


def _merge_timetable(a: dict, b: dict) -> dict:
    """两张课表并成一张。同一门课（同名/同教师/同教室/同周次）算一条，格子取并集。

    用在「一个人挂了多个编号」的情形：主修和辅修的课各自查得出来，
    但学生要看到的是**一张**课表。去重规则必须和 `parse_timetable` 里那条一致，
    否则同一门课会在界面上出现两遍。
    """
    out = dict(a or {})
    merged: dict[tuple, dict] = {}
    for c in list((a or {}).get("courses") or []) + list((b or {}).get("courses") or []):
        if not c.get("slots"):
            continue
        key = (c["name"], c["code"], c["teacher"], c["room"], tuple(c["weeks"]))
        if key in merged:
            for s in c["slots"]:
                if s not in merged[key]["slots"]:
                    merged[key]["slots"].append(s)
        else:
            keep = dict(c)
            keep["slots"] = list(c["slots"])
            merged[key] = keep
    courses = sorted(merged.values(),
                     key=lambda c: (c["slots"][0]["day"], c["slots"][0]["period"]))
    out["courses"] = courses
    out["totalWeeks"] = max(
        (a or {}).get("totalWeeks") or 0,
        (b or {}).get("totalWeeks") or 0,
        max((max(c["weeks"]) for c in courses if c.get("weeks")), default=0),
    )
    if not out.get("semester"):
        out["semester"] = (b or {}).get("semester") or ""
    out.pop("empty", None)
    return out


# ---------------------------------------------------------------- 成绩解析

def parse_grades(html: str) -> dict:
    tables = parse_tables(html)

    summary: list[dict] = []
    detail: list[dict] = []

    for rows in tables:
        for r in rows:
            cells = [c for c in r]
            if len(cells) == 5 and re.fullmatch(r"\d{4}-\d{4}", cells[0] or ""):
                summary.append({
                    "year": cells[0],
                    "term": cells[1],
                    "count": cells[2],
                    "credits": cells[3],
                    "gpa": cells[4],
                })
            elif len(cells) >= 10 and re.match(r"^\d{4}-\d{4}", cells[0] or ""):
                detail.append({
                    "term": cells[0],
                    "code": cells[1],
                    "seq": cells[2],
                    "name": cells[3],
                    "category": cells[4],
                    "nature": cells[5],
                    "credit": cells[6],
                    "score": cells[7],
                    "final": cells[8],
                    "gpa": cells[9],
                })

    overall = None
    for rows in tables:
        for r in rows:
            if "在校" in (r[0] or "") and len(r) == 4:
                overall = {"count": r[1], "credits": r[2], "gpa": r[3]}
            elif "在校" in (r[0] or "") and len(r) == 5:
                overall = {"count": r[2], "credits": r[3], "gpa": r[4]}
    return {"summary": summary, "overall": overall, "courses": detail}


# ---------------------------------------------------------------- 学籍解析

# 「拿到的到底是不是学籍页」。空响应 / 网关错误页 / 被弹回登录页时，
# 一个字段都解析不出来 —— 那不是「学籍没建档」，是**这次没读到**。
# 以前两者压成一句，用户（2026-11-13）报「个人信息查不到」，
# 而本地那份学籍被这次空结果覆盖掉了。
PROFILE_PAGE_RE = re.compile(r"学籍|stdDetail|姓名", re.I)

_PROFILE_KEYS = {
    "学号": "studentId",
    "姓名": "name",
    "性别": "gender",
    "年级": "grade",
    "院系": "dept",
    "专业": "major",
    "方向": "direction",
    "所属班级": "className",
    "所属校区": "campus",
    "学籍状态": "status",
    "学历层次": "level",
    "学制": "duration",
    "入校时间": "enrolledAt",
    "毕业时间": "graduateAt",
}


def parse_profile(html: str) -> dict:
    # 表格是「一行塞两对」的布局：['学号：','2023123456','姓名：','张三','']
    # ⇒ 必须按 2 步长走，只看 [0]/[1] 会漏掉一半字段。
    out: dict[str, str] = {}
    for rows in parse_tables(html):
        for r in rows:
            for i in range(0, len(r) - 1, 2):
                key = (r[i] or "").strip().rstrip("：:")
                val = (r[i + 1] or "").strip()
                if key in _PROFILE_KEYS and val and val != "*":
                    out.setdefault(_PROFILE_KEYS[key], val)
    return out


# ---------------------------------------------------------------- 考试安排
#
# 真实结构（2026-10-18 抓的）：
#   列表页 /eams/stdExamTable.action 里有一段
#     bg.Go('/eams/stdExamTable!examTable.action?examBatch.id=&semester.id=297','contentDiv')
#   ⇒ 明细必须带上 semester.id，否则 Hibernate 报
#     IllegalArgumentException: id to load is required for loading（HTTP 500）。
#   批次下拉 name="examBatch.id"，目前只有一个「平时结课考试」= 1482。
#
# 🔴 截至 2026-10-18 学校**还没发布排考**：表头齐全但 tbody 是空行。
#    所以这里按「表头名字定位列」写，不按下标——列顺序变了也不会错位。

_EXAM_COLS = {
    "课程序号": "code",
    "课程名称": "name",
    "考试类别": "category",
    "考试形式": "form",
    "考试周次与星期": "weekText",
    "考试日期": "date",
    "考试安排": "arrange",
    "考试地点": "room",
    "考场座位号": "seat",
    "考试情况": "status",
    "其它说明": "note",
}


def parse_exams(html: str) -> dict:
    """从考试明细页里抠出考试安排。没有排考时返回空列表（不是报错）。"""
    for rows in parse_tables(html):
        if not rows:
            continue
        head = [(c or "").strip() for c in rows[0]]
        if "考试日期" not in head and "考试地点" not in head:
            continue
        idx = {}
        for i, name in enumerate(head):
            key = _EXAM_COLS.get(name)
            if key and key not in idx:
                idx[key] = i

        out = []
        for r in rows[1:]:
            if not any((c or "").strip() for c in r):
                continue
            rec = {}
            for key, i in idx.items():
                rec[key] = (r[i] or "").strip() if i < len(r) else ""
            # 课程名都没有的行是空壳（学校空表里就是这种），丢掉
            if not rec.get("name"):
                continue
            out.append(rec)
        return {"exams": out, "published": bool(out)}
    return {"exams": [], "published": False}


def parse_exam_page(html: str) -> dict:
    """列表页：抠出 semester.id 和考试批次下拉。"""
    sid = ""
    m = re.search(r"stdExamTable!examTable\.action\?[^\"']*semester\.id=(\d+)", html)
    if m:
        sid = m.group(1)

    batches = []
    mb = re.search(r'<select[^>]*name="examBatch\.id"[^>]*>(.*?)</select>', html, re.S)
    if mb:
        for mo in re.finditer(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', mb.group(1)):
            batches.append({"id": mo.group(1).strip(), "name": mo.group(2).strip()})

    return {"semesterId": sid, "batches": batches}


def parse_semester_list(html: str) -> list[dict]:
    """学期下拉：`<option value="297">2026-2027学年1学期</option>`。

    只留纯数字 value（学期 id）；空 value 那种「请选择」的占位项要丢掉，
    否则界面上会多一个点不动的空按钮。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for mo in re.finditer(r'<option[^>]*value="(\d+)"[^>]*>([^<]*)</option>', html):
        sid, name = mo.group(1), mo.group(2).strip()
        if not name or sid in seen:
            continue
        seen.add(sid)
        out.append({"id": sid, "name": name})
    return out


def _cells(tr_html: str) -> list[str]:
    """一行 `<td>` 里的纯文本（去标签、解实体、压空白）。

    🔴 必须解 HTML 实体。教务系统的表格里到处是 `&nbsp;`：
       课程名常写成 `&nbsp;1&nbsp;大学生职业生涯规划`，
       不解的话名字前面会挂着 `&nbsp;1&nbsp;`，跟 JS 版（DOMParser 自动解码）
       对不上 —— 而两边不一致，同一个账号在手机和网页上会看到不同的课程名。
    """
    out = []
    for td in re.findall(r"<td[^>]*>(.*?)</td>", tr_html, re.S):
        t = _html.unescape(re.sub(r"<[^>]+>", "", td))
        # \s 在 Python 里不覆盖 \u00a0（NO-BREAK SPACE 属于 Zs 但不是 \s）
        t = re.sub(r"[\s\u00a0]+", " ", t)
        out.append(t.strip())
    return out


def parse_free_rooms(html: str) -> dict:
    """空闲教室结果表 → `{"rooms": [...], "notice": [...], "raw": n}`。

    表格结构（2026-10-19 实测）：
        <table class="gridtable"> → <tbody> → <tr> → 6 个 <td>
        序号 / 名称 / 教学楼 / 校区 / 教室设备配置 / 容量

    🔴 第一行经常是一条 **`停课`** —— 名称是「停课」、没有教学楼、容量 0。
       那不是教室，是学校塞在结果里的一个状态行。**不能当教室画出来**
       （界面上会出现一个叫「停课」的教室），也不能一声不吭地删掉：
       单独放进 `notice`，界面在「没查到教室」时可以说清楚是学校给的标记。
    """
    m = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, re.S)
    if not m:
        return {"rooms": [], "notice": [], "raw": 0}

    rooms: list[dict] = []
    notice: list[str] = []
    raw = 0
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
        tds = _cells(tr)
        if len(tds) < 6:
            continue
        raw += 1
        _no, name, building, campus, kind, cap = tds[:6]
        if not name:
            continue
        if name == "停课" or (not building and not kind and cap in ("", "0")):
            # 状态行，不是教室
            if name not in notice:
                notice.append(name)
            continue
        try:
            capacity = int(cap)
        except ValueError:
            capacity = 0
        rooms.append({
            "name": name,
            "building": building,
            "campus": campus,
            "type": kind,
            "capacity": capacity,
        })
    return {"rooms": rooms, "notice": notice, "raw": raw}


def parse_room_options(html: str) -> dict:
    """空闲教室查询表单里的三个下拉：教室类型 / 校区 / 教学楼。

    空 value 的「...」占位项丢掉 —— 否则界面上多一个点了没用的按钮。
    """
    def opts(name: str) -> list[dict]:
        m = re.search(r'<select[^>]*name="%s"[^>]*>(.*?)</select>' % re.escape(name),
                      html, re.S)
        if not m:
            return []
        out: list[dict] = []
        seen: set[str] = set()
        for mo in re.finditer(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', m.group(1)):
            rid, rname = mo.group(1).strip(), mo.group(2).strip()
            if not rid or not rname or rid in seen:
                continue
            seen.add(rid)
            out.append({"id": rid, "name": rname})
        return out

    return {
        "types": opts("classroom.type.id"),
        "campuses": opts("classroom.campus.id"),
        "buildings": opts("classroom.building.id"),
    }


# ---------------------------------------------------------------- 该办的事

def _plain_text(html: str) -> str:
    """页面正文：去脚本、去样式、解实体、压空白。"""
    t = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[\s\u00a0]+", " ", t).strip()


def _select_options(html: str, name: str) -> list[dict] | None:
    """某个 `<select>` 里的选项。**None 表示页面里压根没这个下拉**
    （跟「下拉在但没选项」是两件事，调用方要分开处理）。"""
    m = re.search(r'<select[^>]*name="%s"[^>]*>(.*?)</select>' % re.escape(name), html, re.S)
    if not m:
        return None
    out: list[dict] = []
    for mo in re.finditer(r"<option[^>]*value=\"([^\"]*)\"[^>]*>(.*?)</option>", m.group(1), re.S):
        rid = mo.group(1).strip()
        nm = _html.unescape(re.sub(r"<[^>]+>", "", mo.group(2))).strip()
        if not rid or not nm:
            continue
        out.append({"id": rid, "name": nm})
    return out


def parse_elect(html: str) -> dict:
    """选课（`/eams/stdElectCourse.action`）。

    🔴 只认「明确的关门话」，认不出就报 unknown。
       反过来「没看到关门话 ⇒ 开着」是危险的：页面改版、被踢回登录页、
       甚至加载失败，都会让那个判断变成「选课开了」的假警报。
       用户兴冲冲点进教务系统发现没开，这软件就不可信了。
    """
    t = _plain_text(html)
    m = re.search(r"现在未到选课时间[^！!。]*", t)
    if m:
        return {"state": "closed", "text": "还没到选课时间", "detail": m.group(0)}
    if re.search(r"选课已结束|已过选课时间|选课时间已过", t):
        return {"state": "closed", "text": "选课已经结束", "detail": ""}
    if "选课" in t:
        return {"state": "unknown", "text": "状态未识别，请到教务系统查看",
                "detail": t[:100]}
    return {"state": "unknown", "text": "页面没认出选课内容", "detail": t[:100]}


def parse_free_listen(html: str) -> dict:
    """免听（`/eams/teach/freeListen/freeListenApply.action`）。

    判据是 `select[name=freeListenSwitchId]` 里有没有批次 ——
    实测「2026-2027-1正常免听」「2026-2027-1重修免听」两个。
    """
    opts = _select_options(html, "freeListenSwitchId")
    if opts is None:
        return {"state": "unknown", "text": "页面里没找到免听批次", "detail": ""}
    if not opts:
        return {"state": "closed", "text": "这学期没有免听批次", "detail": ""}
    return {"state": "open", "text": "、".join(o["name"] for o in opts), "detail": ""}


def parse_exemption(html: str) -> dict:
    """免修（`/eams/exemptionApplyNew.action`）。

    ⚠️ 这个页面只有「免修类型」下拉（考核/替代/退役复学/主辅修四种），
       **那是固定的类型，不代表现在能申请**。没有可靠的开关信号，
       所以状态一律报 unknown，只把有哪几种类型摆出来。
    """
    opts = _select_options(html, "type.id")
    if opts is None:
        return {"state": "unknown", "text": "页面里没找到免修类型", "detail": ""}
    if not opts:
        return {"state": "closed", "text": "没有可申请的免修类型", "detail": ""}
    return {
        "state": "unknown",
        "text": "有 %d 种免修类型，开没开要去教务系统看" % len(opts),
        "detail": "、".join(o["name"] for o in opts),
    }


def parse_evaluate(html: str) -> dict:
    """量化评教（`/eams/quality/stdEvaluateNew.action`）。

    ⚠️ 这个页面**没抓到过样本**（2026-10-20 只从首页菜单里看到入口）。
       所以只认两种最不容易误判的信号，其余一律 unknown。
       **不许**因为「没看到待评」就说已评完 —— 那正是「没读到」
       被说成「没有」的经典翻车方式。
    """
    t = _plain_text(html)
    if re.search(r"已评完|评价已?完成|已完成评价", t) and not re.search(r"待评|未评", t):
        return {"state": "closed", "text": "看起来已经评完了", "detail": t[:100]}
    if re.search(r"待评|未评", t):
        return {"state": "open", "text": "有课等着评", "detail": t[:100]}
    return {"state": "unknown", "text": "状态未识别，请到教务系统查看", "detail": t[:100]}


# 该办的事：key / 名字 / 路径 / 解析函数
TODO_DEFS = [
    ("elect", "选课", "/eams/stdElectCourse.action", parse_elect),
    ("evaluate", "评教", "/eams/quality/stdEvaluateNew.action", parse_evaluate),
    ("freeListen", "免听申请", "/eams/teach/freeListen/freeListenApply.action", parse_free_listen),
    ("exemption", "免修申请", "/eams/exemptionApplyNew.action", parse_exemption),
]


# ---------------------------------------------------------------- 客户端

class EamsClient:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self.username = ""
        self._last = 0.0
        self._ids = ""
        # 课表页里全部候选编号（见 find_id_candidates）。辅修 / 双学位的学生，
        # 页面上可能同时挂着不止一个，逐个试才知道哪个查得出课表。
        self._cands: list[dict] = []
        # 课表页里那三样「决定参数」的东西（见 page_hints）。空 = 页面里没写。
        self._hints: dict = {"kinds": [], "semester": "", "project": ""}
        self._logged_in = False
        # 考试列表页是否已经访问过 —— 学期下拉接口依赖它（见 semesters()）
        self._exam_page_seen = False
        # 这个会话里有没有「project 上下文」—— 空闲教室依赖它（见 _ensure_project）
        self._project_ready = False

    # -- 内部
    def _get(self, path: str, **kw) -> requests.Response:
        gap = time.time() - self._last
        if gap < POLITE_DELAY:
            time.sleep(POLITE_DELAY - gap)
        r = self.s.get(BASE + path, timeout=30, **kw)
        r.encoding = "utf-8"
        self._last = time.time()
        return r

    def _post(self, path: str, data: dict, referer: str = "") -> requests.Response:
        gap = time.time() - self._last
        if gap < POLITE_DELAY:
            time.sleep(POLITE_DELAY - gap)
        headers = {"Origin": BASE}
        if referer:
            headers["Referer"] = BASE + referer
        r = self.s.post(BASE + path, data=data, timeout=40, headers=headers, allow_redirects=True)
        r.encoding = "utf-8"
        self._last = time.time()
        return r

    # -- 对外
    @property
    def logged_in(self) -> bool:
        return self._logged_in

    def _login_page(self) -> tuple[str, str]:
        """取一张能用的登录页，返回 (盐值前缀, csrf)。

        拿不到就按**实际看到的东西**报错（见 login_page_error）。为什么重试：
        2026-11-11 连续取 24 次登录页全部正常，可当天有三位用户报「绑不上」，
        其中两位一次都没取到 —— 那不是学校改版，是那一刻没给。
        网络抖一下就让用户重来一遍，是最没必要的一种失败。

        `Cache-Control: no-cache`：别让中间代理把上一张登录页端回来。那张页面
        里的盐值是上一次会话的，拿去提交必然「密码不对」，而用户会以为自己
        记错了密码。
        """
        last = None
        why = ""
        home_seen = 0
        for i in range(LOGIN_TRIES):
            try:
                last = self._get("/eams/loginExt.action",
                                 headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            except requests.RequestException as exc:
                last = None
                why = "net"
                self._last = time.time()
                if i + 1 < LOGIN_TRIES:
                    time.sleep(0.9 + i * 0.9)
                _ = exc
                continue
            body = last.text or ""
            pre, _how = salt_of(body)
            if pre:
                m = CSRF_RE.search(body)
                return pre, (m.group(1) if m else "")

            if looks_home(last.status_code, last.url, body):
                home_seen += 1
                if home_seen == 1:
                    # 带着有效 cookie 要登录页，学校只会把你送回首页。
                    self.s.cookies.clear()
                    self._logged_in = False
                    continue
                why = "home"
                break
            if last.status_code == 0:
                why = "net"
            elif last.status_code >= 500:
                why = "err"
            elif looks_portal(last.url, body):
                why = "portal"
            else:
                why = "page"
            if i + 1 < LOGIN_TRIES:
                time.sleep(0.9 + i * 0.9)
        raise login_page_error(last, why)

    def login(self, username: str, password: str) -> None:
        # 不在这里做「已经登过就跳过」：这个类什么时候该重登由调用方决定
        # （bridge.py 建客户端时就登一次）。真带着有效 cookie 去要登录页，
        # _login_page 会认出「被跳回首页」再丢掉会话重来 —— 那条路是通。
        pre, csrf = self._login_page()
        # 页面自己怎么拼就怎么拼（`<盐>-` + 密码 或者 `<盐>` + 密码），不替它猜。
        hashed = hashlib.sha1((pre + password).encode("utf-8")).hexdigest()

        # 立刻提交会被「请不要过快点击」挡掉
        time.sleep(3.0)
        try:
            r2 = self._post(
                "/eams/loginExt.action",
                {
                    "needModify": "0",
                    "_csrf": csrf,
                    "username": username,
                    "password": hashed,
                    "session_locale": "zh_CN",
                    "login_from": "login_from",
                },
                referer="/eams/loginExt.action",
            )
        except requests.RequestException:
            # 提交这一步压根没通到服务器。以前它会被下面那句「学校网站这会儿
            # 不太正常」收掉 —— 网络断了说成学校的问题，用户就不会去查网络。
            raise EamsError("连不上教务系统，请检查网络后重试", "net", "post_net") from None
        body = r2.text or ""
        landed = r2.url or ""
        ok_home = ("homeExt" in landed
                   or ("homeExt.action" in body
                       and not re.search(r"name=['\"]?password", body, re.I)))
        if not ok_home:
            if "请不要过快点击" in body:
                raise EamsError("学校服务器提示「请不要过快点击」，稍等几秒再试", "net")
            # 2026-11-05：认证失败的说法不止「密码错误」一种。
            # 「密码不正确」「用户名或密码有误」以前都会掉进「系统抽风」，
            # 用户以为与自己无关，于是一直重试 —— 重试到账号被锁。
            # 2026-11-11：只按**学校页面上写着的话**判。学校说密码不对就是
            # 密码不对；也不许反过来把网络问题说成密码问题。
            if "密码" in body and re.search(r"(错误|不正确|有误|不对)", body):
                raise EamsError("学号或密码不对", "auth", "auth_txt")
            if re.search(r"(锁定|已停用|已禁用|冻结)", body):
                raise EamsError("账号被学校锁了，先找教务处解锁", "auth", "locked_txt")
            # 剩下的这一档：学校回了页面，但不是首页，也没说清是什么。
            # 如实说「没登上、没给原因」，别编一个理由。
            if not body:
                raise EamsError("学校教务系统无返回，请稍后再试", "net", "no_body")
            raise EamsError("登录未成功，学校未给出原因，请稍后再试", "page",
                            "no_home_%d_%d" % (r2.status_code, min(len(body), 9999)))
        self.username = username
        self._logged_in = True

    def ensure_login(self, username: str, password: str) -> None:
        if not self._logged_in:
            self.login(username, password)

    def student_id_candidates(self) -> list[dict]:
        """课表页里所有编号，学生那一支排最前。认不出任何一个时按原因分档报错。"""
        if self._cands:
            return self._cands
        r = self._get("/eams/courseTableForStd.action")
        cands = find_id_candidates(r.text)
        if not cands:
            # 🔴 2026-11-06：这句以前是「没拿到学生内部编号，可能登录态过期了」，
            #    把两种情况压成了一句，用户（截图 2026-11-05）看到之后一直重输密码：
            #      ① 页面被退回登录页 ⇒ 会话没了，重输一次有用
            #      ② 页面开了但里面没有那行编号 ⇒ 学校没给出课表页（改版 / 还没排课），
            #         重输一百次也没用
            #    顺带核实过：学号顶替不了这个编号 —— 拿学号当 ids 查课表，一条都查不到。
            #    所以没有「换个参数再试」的捷径，只能把话分成两档说清。
            #
            # 🔴 2026-11-13 实测：还有**第三档**，而且它最会骗人 ——
            #    教务出错时回的 Struts2 报错页（HTTP 500）里有模板路径
            #    「…/courseTableForStd/prompt.ftl」，正好命中下面的
            #    `TABLE_PAGE_RE`，于是报错页被说成「学校这次没给出课表」
            #    （nottable）—— 用户看到的就是「查不到课表」，而且因为
            #    nottable 是「学校的锅」，我们连参数都不再试一次。
            #    报错页必须先认出来：那是「没读到」，不是「学校没给」。
            self._sane_page(r, "课表页", "ids")
            if re.search(r'name="password"|loginExt\.action', r.text):
                raise EamsError("登录状态未保持，请重新输入密码")
            # 编号那一行在、值是空串：这个人还没分班 / 这学期还没排课。
            # 和「页面不认识」分开说 —— 前者等学校，后者是我们没跟上改版。
            if IDS_EMPTY_RE.search(r.text):
                raise EamsError("学校尚未提供课表，可能还未分班或未排课",
                                kind="nottable")
            if TABLE_PAGE_RE.search(r.text):
                raise EamsError("学校这次没给出课表，请稍后重试", kind="nottable")
            # 连课表页都不像（空响应 / 网关错误页 / 只说一句「请先登录」）⇒ 别赖学校，
            # 当成页面没打开：上层会重登一次，那是真能救回来的情况。
            raise EamsError("课表页没打开，可能登录状态没了，稍后再试一次")
        self._cands = cands
        self._hints = page_hints(r.text)
        self._ids = cands[0]["id"]
        return self._cands

    def student_ids(self) -> str:
        return self.student_id_candidates()[0]["id"]

    def _sane_page(self, r, what: str, diag: str) -> None:
        """这一页**是不是真的那个页面**（而不是教务的报错页 / 网关页）。

        🔴 2026-11-13 实测出来的：教务出错时回的是 Struts2 / Freemarker 的
           报错页（HTTP **500**），正文里写着模板路径
           `…/courseTableForStd/prompt.ftl` 和「错误路径: /stdDetail.action」。
           而 `courseTableForStd`、`stdDetail`、`courseTable` 这些字，正好就是
           我们用来判断「这页对不对」的词 —— 于是**报错页一路骗过所有判断**：

             · 课表 → 「页面里有 courseTable，是张空课表」→ 界面说「还没排课」
             · 学籍 → 「页面里有 stdDetail，是个学籍页」→ 姓名/学院全空

           然后前端照单全收，把本机那份真数据覆盖掉。

           所以顺序必须是：先认报错页，再谈内容。状态码和报错页特征两道都过，
           才有资格去问「里面有没有课 / 有没有学号」。
        """
        body = r.text or ""
        if r.status_code != 200 or ERROR_PAGE_RE.search(body):
            raise EamsError("%s这次打不开，请稍后重试" % what,
                            kind="readfail",
                            diag="http_%d_%s" % (r.status_code, diag))

    def timetable(self, semester_id: str = "", semester_name: str = "") -> dict:
        """某个学期的课表。`semester_id` 为空 = 教务系统的当前学期。

        🔴 2026-10-31 起支持翻历史学期。表单里那个 `semester.id` 一直存在
           （以前恒为空串），传上学期的 id 教务系统就会返回那一学期的课表。
           **同一个接口，不需要额外的页面** —— 别去猜 `xskbcx` 那套老接口。

        ⚠️ 「空学期」和「登录态过期」都会让响应里没有 TaskActivity，
           但处理方式完全相反：前者该显示「这学期没有课表」，后者该请用户重登。
           区分办法看有没有登录页的特征（密码框 / loginExt），
           有 ⇒ 过期；没有 ⇒ 真的是空课表。

        🔴 2026-11-06 晚：**辅修 / 双学位**的学生可能挂不止一个编号（见
           `find_id_candidates`）。先用「学生」那几支查，一支都查不出课来才去试
           「班级」那支 —— 班级编号配学生课表接口通常查不出东西，只在别无选择时试。
           正常学生只命中一支，请求次数和以前一样，别为了这点兼容让所有人多等一次。

        🔴 2026-11-13：**同一个人也可能「参数不对」而不是「没课」。** 以前恒定按
           `setting.kind=std` + `semester.id=""` 发，页面上写着的那几个值
           （`#courseTableType` 的选项、隐藏的 `semester.id`、`project.id`）一个都没用。
           现在：第一次照旧，**只有它没查出课**才按页面上的值再试 ——
           最多再多两次请求，正常学生一次都不多花。
        """
        cands = self.student_id_candidates()
        primary = [c for c in cands if c["kind"] != "class"]
        others = [c for c in cands if c["kind"] == "class"]
        if not primary:
            primary, others = others, []

        variants = self._tt_variants(semester_id)
        best = None
        first_err = None
        for group in (primary, others):
            for c in group:
                for v in variants:
                    try:
                        out = self._timetable_one(c["id"], v["semester"],
                                                  semester_name, v["kind"],
                                                  v["project"])
                    except EamsError as e:
                        # 会话没了要立刻往外抛：拿另一支的空课表把它盖住，
                        # 用户看到的又是一张永远空着的课表（2026-11-06 踩过一次）。
                        if getattr(e, "kind", "") == "session":
                            raise
                        if first_err is None:
                            first_err = e
                        continue
                    except Exception as e:  # noqa: BLE001
                        # 网络抖、解析炸了一种编号，别连累另一种
                        if first_err is None:
                            first_err = e
                        continue
                    if out.get("courses"):
                        best = out if best is None or not best.get("courses") \
                            else _merge_timetable(best, out)
                        break        # 这一支查出了课，换参数没意义
                    elif best is None:
                        best = out
            if best is not None and best.get("courses"):
                break
        if best is None:
            raise first_err or EamsError("课表没读到，请稍后重试", kind="readfail")
        return best

    def _tt_variants(self, semester_id: str) -> list[dict]:
        """查课表时可以试的几组参数，**第一组永远是以前那一组**。

        顺序：① std + 调用方给的学期（老行为）② 页面上写着的 semester.id
              ③ 页面上 `#courseTableType` 里非 std 的那一支。
        去重后最多三组；页面里什么都没写时就只有一组。
        """
        h = self._hints or {}
        out: list[dict] = []
        seen: set[tuple] = set()

        def add(kind: str, sem: str, proj: str) -> None:
            key = (kind, sem, proj)
            if key in seen:
                return
            seen.add(key)
            out.append({"kind": kind, "semester": sem, "project": proj})

        add("std", semester_id, "")
        if not semester_id and h.get("semester"):
            add("std", h["semester"], "")
        for k in (h.get("kinds") or []):
            if k and k != "std":
                add(k, semester_id or h.get("semester") or "", "")
        # 项目只在「这一学期确实挂在某个项目下」时才有意义：
        # 页面里选了项目才带，否则不带（带了反而可能查出空表）。
        if h.get("project"):
            for v in list(out):
                if v["kind"] == "std" and v["semester"]:
                    add(v["kind"], v["semester"], h["project"])
                    break
        return out[:3]

    def _timetable_one(self, ids: str, semester_id: str,
                       semester_name: str, kind: str = "std",
                       project_id: str = "") -> dict:
        """拿指定编号 + 一组参数查一次课表（不含多候选）。"""
        data = {
            "ignoreHead": "1",
            "setting.kind": kind or "std",
            "startWeek": "",
            "semester.id": semester_id,
            "ids": ids,
        }
        if project_id:
            data["project.id"] = project_id
        r = self._post(
            "/eams/courseTableForStd!courseTable.action",
            data,
            referer="/eams/courseTableForStd.action",
        )
        body = r.text or ""
        if re.search(r'name="password"|loginExt\.action', body):
            # kind="session"：多候选循环里要立刻往外抛，不许被另一支的空课表盖掉
            raise EamsError("课表页返回里没有课程数据，可能登录态过期了",
                            kind="session")
        # 🔴 2026-11-13 实测补的两道闸。
        #    教务出错时回的是一张 Struts2 报错页（HTTP **500**），正文里写着
        #    模板路径 `/…/courseTableForStd/prompt.ftl` —— 含 "courseTable"，
        #    于是「骨架」判据会被一张报错页骗过去，当成「这学期没课」。
        #    状态码和报错页特征必须排在骨架之前，两道都过不了才谈有没有课。
        self._sane_page(r, "课表页", kind or "std")
        if "TaskActivity" not in body:
            # 🔴 2026-11-13：**只有「响应里确实有课表骨架」才叫空学期。**
            #    空响应 / 网关错误页 / 参数不对被弹回来，一个骨架都没有 ——
            #    以前这些都被当成「没有课」，界面照实说「教务还没排课」，
            #    还顺手把用户本机那份真课表覆盖成空。读不到就说读不到。
            if not TABLE_RENDER_RE.search(body):
                raise EamsError("课表这次没读到，请稍后重试",
                                kind="readfail",
                                diag="blank_%d_%s" % (len(body), kind or "std"))
            # 空学期：单元数仍然解析得出来（页面骨架还在），课程为空
            out = parse_timetable(body)
            out["courses"] = []
            out["empty"] = True
            if semester_name:
                out["semester"] = semester_name
            return out
        out = parse_timetable(body)
        if not out["courses"]:
            # 🔴 「页面上有课块，但我们一条都没解析出来」= 我们的事，不是学校没课。
            #    以前这里安静返回空，界面说「还没有课表」—— 用户只能去找一个
            #    并不存在的开关（2026-11-13 报的就是这个）。
            raise EamsError("课表页拿到了，但没解析出课程，可能页面改版了",
                            kind="readfail",
                            diag="act_%d_%d" % (body.count("TaskActivity"), len(body)))
        # 课表里的学期名是从**课程代码前缀**推出来的（`25261.`），
        # 课程代码不规整时会是空串。调用方手里有教务给的规范名字，优先用那个。
        if semester_name:
            out["semester"] = semester_name
        return out

    def grades(self) -> dict:
        """历年成绩。

        🔴 2026-11-01 修：**「没有成绩」不是「登录态过期」**。
           以前只要解析不出课程就抛 EamsError，结果 2026 级新生（第一学期
           还没出分、历年成绩页本来就是空表）账号密码全对也绑不上 ——
           提示还写着「可能登录态过期了」，人会一直怀疑自己密码打错了。
           这里改用课表那套判断：响应里有登录页特征 ⇒ 真过期；
           没有 ⇒ 就是空表，原样交出去，界面说「还没有成绩」。
        """
        self._get("/eams/teach/grade/course/person.action")
        r2 = self._get(
            "/eams/teach/grade/course/person!historyCourseGrade.action?projectType=",
            headers={"Referer": BASE + "/eams/teach/grade/course/person.action"},
        )
        if re.search(r'name="password"|loginExt\.action', r2.text):
            # kind="auth"：只有这一种失败是「重输一次就能解决」的。
            raise EamsError("成绩页被退回登录页，可能登录态过期了", kind="auth")
        # 报错页解析出来是「没有成绩」，界面就会把本机历年成绩清空。
        self._sane_page(r2, "成绩页", "grades")
        data = parse_grades(r2.text)
        if not data["courses"]:
            # 空 ≠ 过期。带个标记，免得调用方又把空当成故障。
            data["empty"] = True
        return data

    def profile(self) -> dict:
        r = self._get("/eams/stdDetail.action")
        # 报错页里有「错误路径: /stdDetail.action」，会骗过下面那句
        # 「像不像学籍页」；被骗过之后会走到「登录名像学号就当学籍拿到了」，
        # 于是姓名/学院/专业全空 —— 用户看到的就是「个人信息没了」。
        self._sane_page(r, "学籍页", "profile")
        p = parse_profile(r.text)
        if p.get("studentId"):
            return p

        # 2026-11-05：三种情况不能压成一句「可能登录过期了」——
        #   ① 被踢回登录页 ⇒ 会话没了，重登有用
        #   ② 页面开了但没有学号 ⇒ 学籍没建档，重登一百次也没用
        #   ③ 登录名本身就不像学号 ⇒ 只能如实说
        # 老版一律报「过期」，用户就一直重输密码。
        if re.search(r'name="password"|loginExt\.action', r.text):
            raise EamsError("学籍页被退回登录页了，登录状态没保住")

        # 2026-11-13：页面根本不像学籍页（空响应 / 网关错误页）⇒ 这次没读到。
        # 不能拿「没有学号」去下结论 —— 那是把「我们没拿到」说成「你没建档」。
        if not PROFILE_PAGE_RE.search(r.text or ""):
            raise EamsError("学籍页这次没读到，请稍后重试",
                            kind="readfail", diag="blank_%d" % len(r.text or ""))

        # 学校是拿学号当登录名的：学籍页缺那一行也不该拦人，
        # 课表 / 成绩 / 考试都不依赖它。
        if re.fullmatch(r"\d{8,14}", self.username or ""):
            p["studentId"] = self.username
            p["fromLogin"] = "1"
            return p

        raise EamsError("学籍页无学号，可能未建档，问教务处")

    def todo(self) -> dict:
        """「该办的事」：选课 / 评教 / 免听 / 免修的开放状态。

        🔴 任何一项取失败都**只影响它自己**，别让整块变成「全都未知」；
           同时也要把失败原因写进那一项，不能安静地显示成「没开放」。
        """
        items = []
        for key, name, path, parse in TODO_DEFS:
            try:
                r = self._get(path)
                st = parse(r.text)
                items.append({
                    "key": key, "name": name, "state": st["state"],
                    "text": st["text"], "detail": st.get("detail", ""), "path": path,
                })
            except Exception as e:  # noqa: BLE001
                items.append({
                    "key": key, "name": name, "state": "unknown",
                    "text": "没查到：%s" % e, "detail": "", "path": path,
                })
        return {"items": items}

    def exams(self, semester_id: str = "") -> dict:
        """考试安排。`semester_id` 为空 = 教务系统默认（当前学期）。

        🔴 学校没排考时**不是错误**，返回空列表 + published=False，
           界面照实说「还没发布」，别把「没数据」伪装成「没有考试」。
        """
        path = "/eams/stdExamTable.action"
        if semester_id:
            # 带上 semester.id 服务端就切到那个学期：页面里的批次下拉
            # 和明细链接都跟着变（实测 257 / 277 / 297 各不相同）。
            path += "?semester.id=%s" % semester_id
        page = self._get(path)
        self._exam_page_seen = True
        # 报错页里一个学期 id / 批次都没有，会走到下面那句「可能登录态过期了」；
        # 但它其实不是过期，而且界面会当成「这学期没有考试」。
        self._sane_page(page, "考试页", "exams")
        meta = parse_exam_page(page.text)
        # 🔴 页面里既没有学期 id 也没有批次下拉 ⇒ 十有八九被踢回登录页了。
        #    这种情况**必须报错**：安静返回 0 条，界面就会说「这学期没有考试安排」，
        #    等于把「没读到」说成「没有」（2026-10-19 实测会话过期就是这样）。
        #    抛 EamsError 之后 _with_client 会重登一次再试。
        if not meta["semesterId"] and not meta["batches"]:
            raise EamsError("考试页没打开，可能登录态过期了")
        sid = semester_id or meta["semesterId"]
        batches = [b for b in meta["batches"] if b["id"]]

        if not sid:
            # 拿不到学期 id 就没法查明细，但也不能报「没有考试」
            return {
                "exams": [],
                "published": False,
                "batches": batches,
                "reason": "没找到学期编号，可能页面结构变了",
            }

        all_rows: list[dict] = []
        seen: set[tuple] = set()
        tried = []
        # 先按批次查；批次全空时再试「不指定批次」，学校两种写法都出现过
        targets = [b["id"] for b in batches] + [""]
        for bid in targets:
            tried.append(bid)
            r = self._get(
                "/eams/stdExamTable!examTable.action?examBatch.id=%s&semester.id=%s" % (bid, sid)
            )
            got = parse_exams(r.text)
            for e in got["exams"]:
                key = (e.get("code"), e.get("date"), e.get("room"), e.get("name"))
                if key in seen:
                    continue
                seen.add(key)
                all_rows.append(e)

        return {
            "exams": all_rows,
            "published": bool(all_rows),
            "batches": batches,
            "semesterId": sid,
            "tried": tried,
        }

    def semesters(self) -> list[dict]:
        """全部学期（教务系统给 53 个，界面自己筛成「我在读的那几个」）。

        🔴 必须**先访问一次考试页**，否则这个接口回空 —— 2026-10-19 实测：
           新会话里直接 GET dataQuery.action?dataType=semester 拿到的是空列表，
           先 GET /eams/stdExamTable.action 之后同样的请求就有 53 项。
           所以这里判断 `_exam_page_seen`，考试页已经走过就不再重复打一次。
        """
        if not self._exam_page_seen:
            self._get("/eams/stdExamTable.action")
            self._exam_page_seen = True
        r = self._get("/eams/dataQuery.action?dataType=semester")
        return parse_semester_list(r.text)

    # -- 空闲教室 ------------------------------------------------------
    #
    # 🔴 这一块有个跟「考试页要先摸一次」同类的坑：
    #    `/eams/classroom/apply/free.action` 是**受限动作**（RestrictionSupportAction），
    #    服务端要求 session 里有 project（培养层次，江财是「本科」）。
    #    只访问 homeExt 不算；必须先访问 stdDetail / courseTableForStd 这类页面。
    #    2026-10-19 实测：不先摸，一律 500 `project not selected`；
    #    摸过之后同样的请求就是 200 + 结果表。
    #
    # 🔴 第二个坑：`free!search.action` 默认只回 **20 行**（grid 分页），
    #    而且要传 `pageSize`（不是 page.size / rows / pageSize 之外那些名字）。
    #    不传就只能看到按容量升序的前 20 个 —— 全是常年空着的小实验室，
    #    换节次、换日期看起来都「一模一样」，会让人以为筛选没生效。

    def _ensure_project(self) -> None:
        if self._project_ready:
            return
        self._get("/eams/stdDetail.action")
        self._project_ready = True

    def room_options(self) -> dict:
        """教室类型 / 校区 / 教学楼三个下拉的选项。"""
        self._ensure_project()
        r = self._get("/eams/classroom/apply/free.action")
        if "project not selected" in r.text:
            raise EamsError("空闲教室页没打开，可能登录态过期了")
        return parse_room_options(r.text)

    def free_rooms(self, date_begin: str, date_end: str,
                   period_begin: int = 1, period_end: int = 2,
                   building_id: str = "", campus_id: str = "",
                   type_id: str = "", seats: str = "", name: str = "",
                   page_size: int = 1000) -> dict:
        """查某天某几节的空闲教室。

        date_begin/date_end 必填（不填服务端直接 500）；
        period_begin/period_end 是**节次编号 1~12**（roomApplyTimeType=0 走小节）。

        🔴 page_size 默认给 1000，不是 200：这个参数服务端**认**，
           实测 1000 一次就回全 756 行（755 间教室 + 1 行「停课」）。
           以前用 200 只能拿到「按容量升序」的前 200 个 —— 全是 6 座的小实验室，
           换节次换日期看起来都一模一样，会让人以为筛选没生效。
        """
        if not date_begin or not date_end:
            raise EamsError("要查哪一天？日期不能空")
        if not (1 <= period_begin <= period_end <= 12):
            raise EamsError("节次要在 1~12 之间，且开始不晚于结束")
        self._ensure_project()
        data = {
            "classroom.type.id": type_id,
            "classroom.campus.id": campus_id,
            "classroom.building.id": building_id,
            "seats": seats,
            "classroom.name": name,
            "cycleTime.cycleCount": "1",
            "cycleTime.cycleType": "1",          # 1=按天
            "cycleTime.dateBegin": date_begin,
            "cycleTime.dateEnd": date_end,
            "roomApplyTimeType": "0",            # 0=按小节
            "timeBegin": str(period_begin),
            "timeEnd": str(period_end),
            "pageSize": str(page_size),
        }
        r = self._post("/eams/classroom/apply/free!search.action", data,
                       referer="/eams/classroom/apply/free.action")
        if r.status_code != 200:
            m = re.search(r"错误原因:.*?<pre>(.*?)</pre>", r.text, re.S)
            why = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else "HTTP %d" % r.status_code
            raise EamsError("空闲教室查询失败：%s" % why)
        # 🔴 会话被踢时学校回的**也是 HTTP 200**，正文只有一句话：
        #    「本次会话已经被过期（可能是由于重复登录）」（20 字节）。
        #    不认这一条的话，parse 出来就是 0 间，界面会说「这一节没有空闲教室」
        #    —— 把错误说成事实，比直接报错更糟。
        #    抛 EamsError 还能让 _with_client 重登一次再试（见 bridge.Service）。
        #    正常的「查不到」返回里是有这张 gridtable 的（只是 tbody 为空），
        #    所以这个判据不会把真·空结果误判成失败。
        if "gridtable" not in r.text:
            why = re.sub(r"<[^>]+>", " ", r.text)
            why = re.sub(r"\s+", " ", why).strip()[:60]
            raise EamsError("空闲教室查询没返回结果表：%s" % (why or "可能登录态过期了"))
        out = parse_free_rooms(r.text)
        out["dateBegin"] = date_begin
        out["dateEnd"] = date_end
        out["periodBegin"] = period_begin
        out["periodEnd"] = period_end
        # 服务端回的行数拿满了 page_size 就说明可能还有更多 —— 界面上要照实说，
        # 不能让用户以为「全校就这么多空教室」。
        # ⚠️ 用 raw（含「停课」那种状态行）而不是 rooms 来判：状态行也占一个名额。
        out["truncated"] = out["raw"] >= page_size
        return out
