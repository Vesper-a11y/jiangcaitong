"""中国大学 MOOC（icourse163.org）—— 只读的课程 / 作业待办。

和学习通那个模块一样，这里只做「看」：不签到、不刷课、不自动答题、不代交。

## 接口是怎么找到的（2026-11-04 抓包实录，别再重新摸一遍）

登录态用浏览器里的 cookie，不是账号密码：

    STUDY_SESS      .icourse163.org    HttpOnly，登录票据（真正决定登没登）
    NTESSTUDYSI     .icourse163.org    必须让页面 JS 也读得到，当 csrfKey 用在每个
                                       rpc 的 ?csrfKey= 上，**少了它就是「非法跨域请求」**
    STUDY_INFO      .icourse163.org    `邮箱|渠道|memberId|时间戳`，memberId 要当下面的 member
    STUDY_WTR       .icourse163.org    HttpOnly，配套票据

⚠️ 老文档里说的 `NTES_SESS` 现在**不一定有**：2026-11-04 实测登录成功后罐子里
   根本没有 NTES_SESS，判「登没登」要看 STUDY_SESS。按名字猜登录态这类坑本项目
   踩过三次了 —— 判据应该是「需要登录的接口返回 code 0」，不是某个 cookie 在不在。

三个接口（全部 POST，参数放**表单体**里）：

    /web/j/mocMemberFollowBean.userInfo.rpc                 登录态 / 昵称
    /web/j/learnerCourseRpcBean.getMyLearnedCoursePanelList.rpc
        type=30&p=1&psize=50&courseType=1                    我学的课
    /web/j/courseBean.getLastLearnedMocTermDto.rpc
        termId=<学期id>                                      整门课：章节 + 作业 + 测验

第三个一次就把整门课带回来（实测 193KB），所以一门课只发一个请求。
课程条目里 `termPanel.id` 才是 termId，`id` 是 courseId，**别弄反** ——
拿 courseId 去问 termId，接口会老老实实返回一门不存在的课。
"""
import json
import re
import threading
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://www.icourse163.org"

RPC_USER = "/web/j/mocMemberFollowBean.userInfo.rpc"
RPC_COURSES = "/web/j/learnerCourseRpcBean.getMyLearnedCoursePanelList.rpc"
RPC_TERM = "/web/j/courseBean.getLastLearnedMocTermDto.rpc"

# 两个请求之间至少隔这么久。一门课只有一个请求，这个值不用像学习通那么大，
# 但也不能连着发 —— 学校网出口是同一个 IP，全班一起用就成压测了。
REQ_GAP = 1.2

# 作业/测验/考试的类型值（test.type）
KIND_NAME = {1: "考试", 2: "测验", 3: "作业"}
KIND_KEY = {1: "exam", 2: "quiz", 3: "work"}

DONE_TRIES = 1        # usedTryCount ≥ 1 = 交过了（不等批改）


class MoocError(Exception):
    """带 kind 的异常。kind: auth（要重新登录）/ net（网络或限流）/ data（返回不对）。"""

    def __init__(self, msg, kind="net"):
        super().__init__(msg)
        self.kind = kind


# ------------------------------------------------------------------ 纯函数

def csrf_of(cookies):
    """从 cookie 里取 csrfKey。取不到返回空串（调用方当「没登录」处理）。"""
    for c in cookies or []:
        if c.get("name") == "NTESSTUDYSI" and c.get("value"):
            return str(c["value"])
    return ""


def member_of(cookies):
    """从 STUDY_INFO 里取 memberId。

    格式：`yd.xxx@163.com|8|12345678|1700000000000`
    第 3 段才是 memberId。**不猜**：段数不对就返回空串，让上层报「读不到账号」，
    而不是拿邮箱或时间戳去当 memberId 使 —— 那种错会安静地返回空列表，
    看起来像「你这学期没课」。
    """
    for c in cookies or []:
        if c.get("name") != "STUDY_INFO":
            continue
        v = str(c.get("value") or "").strip().strip('"')
        parts = v.split("|")
        if len(parts) >= 3 and parts[2].isdigit():
            return parts[2]
    return ""


def has_login(cookies):
    """有没有登录态。

    🔴 只看 STUDY_SESS 在不在，不拿它去猜「能不能用」。真正的判据是
       `me()` 里的那个接口返回 code 0 —— 见模块注释。
    """
    for c in cookies or []:
        if c.get("name") in ("STUDY_SESS", "STUDY_WTR") and c.get("value"):
            return True
    return False


def _ms_to_sec(v):
    """毫秒时间戳 → 秒。0 / None / 明显不是毫秒的都返回 None。

    🔴 不兜底成「现在」：没有截止时间就是没有，编一个出来会让待办表里
       平白多出一条「今天截止」的假任务。
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    # 毫秒是 13 位。真拿到 10 位的秒级也认（除以 1000 会变成 1970 年）
    return n // 1000 if n > 10 ** 11 else n


def parse_courses(j):
    """课程列表 → [{courseId, termId, name, shortName, startAt, endAt}]。

    字段来源：result.result[] 每一项里
      id        = courseId
      name      = 课程名
      shortName = 学校内的课程编号（拼链接用）
      termPanel = 这一学期，里面 id 才是 termId
    """
    out = []
    res = (j or {}).get("result")
    rows = (res or {}).get("result") if isinstance(res, dict) else None
    for c in rows or []:
        tp = c.get("termPanel") or {}
        tid = tp.get("id")
        if not tid:
            continue                      # 没学期就没地方查作业，跳过
        out.append({
            "courseId": c.get("id"),
            "termId": tid,
            "name": str(c.get("name") or "").strip(),
            "shortName": str(c.get("shortName") or "").strip(),
            "startAt": _ms_to_sec(tp.get("startTime")),
            "endAt": _ms_to_sec(tp.get("endTime")),
        })
    return out


def item_state(t, now=None):
    """一条作业/测验处于什么状态。

    三条依据，顺序不能反：
      1. `usedTryCount ≥ 1` 或 `userScore` 有值 ⇒ 交过了（**不等批改**）
      2. 截止时间已过 ⇒ 过期（没交）
      3. 其余 ⇒ 待办
    """
    now = time.time() if now is None else now
    try:
        tried = int(t.get("usedTryCount") or 0)
    except (TypeError, ValueError):
        tried = 0
    if tried >= DONE_TRIES or t.get("userScore") is not None:
        return "done"
    end = _ms_to_sec(t.get("deadline"))
    if end and end < now:
        return "expired"
    return "todo"


def parse_term(j, course=None):
    """整门课的详情 → 作业/测验/考试条目列表。"""
    course = course or {}
    term = ((j or {}).get("result") or {}).get("mocTermDto") or {}
    cname = course.get("name") or str(term.get("courseName") or "")
    cid, tid = course.get("courseId") or term.get("courseId"), term.get("id")
    url = "%s/learn/icourse-%s?tid=%s#/learn/testlist" % (BASE, cid, tid)

    now = time.time()
    out = []
    for ch in term.get("chapters") or []:
        chname = str(ch.get("name") or "").strip()
        groups = ((ch.get("homeworks") or [], "work"),
                  (ch.get("quizs") or [], "quiz"),
                  ([ch["exam"]] if ch.get("exam") else [], "exam"))
        for rows, kind in groups:
            for row in rows:
                t = row.get("test") or {}
                title = str(t.get("name") or row.get("name") or "").strip()
                if not title:
                    continue
                # 类型以服务端给的 test.type 为准；没给就退回分组推出来的
                k = KIND_KEY.get(t.get("type"), kind)
                score = t.get("userScore")
                out.append({
                    "kind": k,
                    "kindName": KIND_NAME.get(t.get("type"), "作业"),
                    "title": title,
                    "course": cname,
                    "courseId": cid,
                    "termId": tid,
                    "chapter": chname,
                    "state": item_state(t, now),
                    "endAt": _ms_to_sec(t.get("deadline")),
                    "score": None if score is None else float(score),
                    "total": t.get("totalScore"),
                    "url": url,
                })
    # 没截止时间的排最后：它们不催人，但也不能丢
    out.sort(key=lambda it: (it["endAt"] or 10 ** 12, it["title"]))
    return out


def summarize(items):
    """抽屉里那几个数字：待办 / 过期 / 已交。"""
    n = {"todo": 0, "expired": 0, "done": 0}
    for it in items:
        n[it.get("state") or "todo"] = n.get(it.get("state") or "todo", 0) + 1
    return n


# ------------------------------------------------------------------ 会话

class Mooc:
    """一个 MOOC 会话。只读。"""

    def __init__(self, cookies=None):
        self.s = requests.Session()
        self.s.trust_env = False                 # 🔴 不吃环境变量里的代理
        self.s.headers.update({"User-Agent": UA, "Referer": BASE + "/",
                               "Origin": BASE})
        self.lock = threading.RLock()
        self._last = 0.0
        if cookies:
            self.load_cookies(cookies)

    # -- cookie ------------------------------------------------------

    def load_cookies(self, rows):
        for r in rows or []:
            try:
                self.s.cookies.set(
                    str(r.get("name") or ""), str(r.get("value") or ""),
                    domain=str(r.get("domain") or ".icourse163.org"),
                    path=str(r.get("path") or "/"))
            except Exception:
                continue                          # 坏了一条不影响别的

    def dump_cookies(self):
        with self.lock:
            return [{"name": c.name, "value": c.value,
                     "domain": c.domain, "path": c.path}
                    for c in self.s.cookies]

    def _rows(self):
        return [{"name": c.name, "value": c.value}
                for c in self.s.cookies]

    def _meta(self):
        rows = self._rows()
        si, member = csrf_of(rows), member_of(rows)
        if not si:
            raise MoocError("登录状态读不到了，请重新登录中国大学 MOOC", "auth")
        return si, member

    # -- 请求 --------------------------------------------------------

    def _pace(self):
        wait = REQ_GAP - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _rpc(self, path, body=""):
        """POST 一个 rpc。返回解析后的 dict。

        🔴 三种失败必须分开：-1002 是 csrfKey 不对（会话失效），
           code 0 以外是业务拒绝，连不上是网络。混成一句「出错了」
           用户就不知道该重新登录还是该换网。
        """
        si, _ = self._meta()
        url = "%s%s?csrfKey=%s" % (BASE, path, si)
        self._pace()
        try:
            r = self.s.post(url, data=body, timeout=20,
                            headers={"Content-Type":
                                     "application/x-www-form-urlencoded"})
        except Exception as exc:
            raise MoocError("连不上中国大学 MOOC：%s" % str(exc)[:60], "net")
        if r.status_code != 200:
            raise MoocError("MOOC 返回了 %s" % r.status_code, "net")
        try:
            j = r.json()
        except Exception:
            raise MoocError("MOOC 返回的不是能读的内容", "data")
        code = j.get("code")
        if code == 0:
            return j
        msg = str(j.get("message") or "")
        if code == -1002:
            raise MoocError("登录状态过期了，请重新登录中国大学 MOOC", "auth")
        raise MoocError(msg or ("MOOC 返回了错误码 %s" % code), "data")

    # -- 业务 --------------------------------------------------------

    def me(self):
        """当前登录的是谁。顺带当登录态探测 —— 这个接口要登录才给数据。"""
        j = self._rpc(RPC_USER)
        res = j.get("result") or {}
        return {"id": res.get("id"), "name": res.get("nickName") or "",
                "school": res.get("schoolName") or ""}

    def courses(self):
        j = self._rpc(RPC_COURSES, "type=30&p=1&psize=50&courseType=1")
        return parse_courses(j)

    def term(self, tid):
        j = self._rpc(RPC_TERM, "termId=%s" % int(tid))
        return j

    def scan(self, cb=None):
        """把所有课的作业扫一遍。

        一门课一个请求，2 门课也照走循环 —— 以后课变多不用改代码。
        某一门出错**不能**让整轮作废：那门记进 failed，其余照常给。
        """
        courses = self.courses()
        items, failed = [], []
        for i, c in enumerate(courses):
            if cb:
                cb(i, len(courses), c.get("name") or "")
            try:
                items.extend(parse_term(self.term(c["termId"]), c))
            except MoocError as exc:
                # 登录态没了就没必要接着扫 —— 后面的课只会一样失败
                if exc.kind == "auth":
                    raise
                failed.append({"course": c.get("name") or "", "why": str(exc)})
        return {
            "at": int(time.time()),
            "courses": courses,
            "items": items,
            "failed": failed,
            "count": summarize(items),
        }


# ------------------------------------------------------------------ 缓存

_LOCK = threading.RLock()
_LAST = {}


def cache_get():
    with _LOCK:
        return _LAST.get("data")


def cache_set(data, at=None):
    with _LOCK:
        _LAST["at"] = int(at or time.time())
        _LAST["data"] = data


def cache_clear():
    with _LOCK:
        _LAST.clear()


def cache_age():
    with _LOCK:
        at = _LAST.get("at")
    return -1 if not at else max(0, int(time.time()) - at)
