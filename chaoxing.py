"""超星学习通 · 作业待办（只读）

这个模块只干一件事：用你自己的账号，把「哪些作业还没交、还剩多久」读出来。

--------------------------------------------------------------------------
四条硬规矩（都是实测踩出来的）

  1. 🔴 **只读。** 不签到、不刷课、不自动答题、不代交作业。
     这些是学术诚信红线；而且超星是分级风控 ——
     首次警告 → 冻结 24 小时 → 灰名单要人工申诉。脚本化操作真的会被封。

  2. 🔴 **失败不重试。** 登录失败一次就停。重试会把 needVcode 撞成 1，
     之后就要人做滑块验证，越弄越糟。

  3. 🔴 **「没读到」≠「没有作业」。** 某门课拿不到数据时，要如实记进 failed，
     不能让它悄悄变成 0 条 —— 否则你会以为真的没作业。

  4. 🔴 **不认识的作业状态不许兜底。** 实测状态有 6 种：
     未交 / 未开始 / 已完成 / 待批阅 / 已批阅 / 已互评。
     前两个算待办，中间三个算完成，「已互评」既不像待办也不像完成 ——
     单独列出来，不当成任何一边。

--------------------------------------------------------------------------
为什么每门课要两个请求（不搞懂就会一直「无权限」）

  第 1 个：进课程页，拿两样东西
      · stuenc  —— 藏在 302 之后的 URL 里，**每次进课程页都变**
      · workEnc —— 藏在 HTML 的 <input id="workEnc"> 里，课程级固定
  第 2 个：带着 stuenc + workEnc 去要作业列表

  🔴 两个 enc 必须来自**同一次**课程页加载。混用旧的和新的 → 「无权限的操作」。
     2026-10-22 在这个坑里耗了三轮：先猜旧接口（404），再裸请求（无权限），
     最后发现是打印 URL 时截断了 190 字符，enc 少了一位。

--------------------------------------------------------------------------
cookie 分两个域，混在一起就「无效的用户」

  passport2.chaoxing.com : JSESSIONID、route      ← 只属于登录站
  .chaoxing.com          : UID、_uid、_d、vc3、uf、cx_p_token、p_auth_token、
                           xxtenc、fid、DSSTASH_LOG

  把前两个也发给 mooc1.chaoxing.com，服务端会拿着不属于它的 session 去查用户。
  所以必须用 requests 的 cookie jar 按域自动匹配，**不能用 Cookie 头硬塞**。

--------------------------------------------------------------------------
代价

  32 门课 ≈ 64 个请求 ≈ 60 秒。学习通没有「一次拿全部作业」的接口，
  作业挂在每门课下面，只能一门一门来。所以手机上必须「后台跑 + 结果缓存 +
  写明更新于什么时候」，不能每次进页面都跑。
"""

from __future__ import annotations

import base64
import html
import json
import random
import re
import threading
import time

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# ------------------------------------------------------------------ 常量

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

AES_KEY = b"u2oh6Vu^HWe4_AES"      # 16 字节，同时当 key 和 iv（login.js 里写死的）

PASSPORT = "https://passport2.chaoxing.com"
LOGIN_PAGE = PASSPORT + "/login?fid=&refer="
LOGIN_API = PASSPORT + "/fanyalogin"
COURSE_API = "https://mooc1-api.chaoxing.com/mycourse/backclazzdata?view=json&rss=1"
COURSE_PAGE = ("https://mooc1.chaoxing.com/visit/stucoursemiddle"
               "?courseid=%s&clazzid=%s&cpi=%s&ismooc2=1&v=2")
WORK_LIST = ("https://mooc1.chaoxing.com/mooc2/work/list"
             "?courseId=%s&classId=%s&cpi=%s&ut=s&t=%d&stuenc=%s&enc=%s")

# 课程内的「活动」列表（签到 / 主题讨论 / 通知 / 作业 / 随堂练习 …）。
# 返回 JSON，比抓 HTML 稳得多。
# 🔴 fid 是**学校 id**（课程数据里的 belongSchoolId，江财是 1286），
#    不是课程页隐藏字段里那个 id="fid"（那个是 23264，别的东西）。
ACTIVE_LIST = ("https://mobilelearn.chaoxing.com/v2/apis/active/student/activelist"
               "?fid=%s&courseId=%s&classId=%s&showNotStartedActive=0&_=%d")

# 考试列表。参数照抄 course-stu.js 里点导航时的拼法 ——
# 缺任何一个都回「参数传递不正确」，而 HTTP 状态码还是 200。
EXAM_LIST = ("https://mooc1.chaoxing.com/exam-ans/mooc2/exam/exam-list"
             "?courseid=%s&clazzid=%s&cpi=%s&ut=s&t=%d&stuenc=%s")

# 收件箱 = 网页端右上角那个「通知中心」（不是私信 / 群聊）。
# 🔴 这是**逆向来的**接口：没有官方文档，参数和字段都可能随改版变。
#    两个独立来源对上了同一组参数（type / notice_type / lastValue / sort，
#    游标取返回里的 lastGetId），才敢接。接进来之后仍然只做加法：
#    解析不出来的条目直接丢，绝不因为一条脏数据让整个收件箱报错。
NOTICE_LIST = "https://notice.chaoxing.com/pc/notice/getNoticeList"

# activeType → 中文名。
# 🔴 这张表不是查来的，是**从学习通自己的任务列表页里挖出来的**：
#    _lab/cx/ml/active_page.html 的 Vue 模板里 aria-label 和
#    `v-if="active.activeType=='N'"` 一一对应，逐条抄下来。
ACTIVE_KIND = {
    "2": "签到", "4": "抢答", "5": "主题讨论", "11": "选人", "14": "问卷",
    "17": "直播", "19": "作业", "23": "评分", "34": "通知", "35": "分组任务",
    "42": "随堂练习", "43": "投票", "45": "通知", "46": "群聊", "47": "计时器", "48": "课程章节",
    "49": "白板", "50": "主题讨论", "51": "同步课堂", "56": "泛雅课堂",
    "57": "PPT文档", "58": "教案", "59": "抽签", "60": "教案", "61": "WORD文档",
    "62": "PDF文档", "63": "阅读任务", "68": "互动练习", "74": "签退",
    "75": "思政资源库", "77": "AI实践",
}

# 活动详情页地址：**按 activeType 分**。
# 🔴 这张表不是猜的，是从学习通自己的活动列表页模板里抄的 ——
#    `_lab/cx/ml/active_page.html` 的 `activeRowClick()` 里那个 switch。
#    参数只用课程对象里现成的 courseId / classId / fid（= schoolId）。
CX_BASE = "https://mobilelearn.chaoxing.com"
# 老版主题讨论（activeType=5）没有静态地址：学习通自己点它时先问这个接口
# （active_page.html 的 openDiscuss → /v2/apis/discuss/getTopicDiscussInfo）。
DISCUSS_INFO = CX_BASE + "/v2/apis/discuss/getTopicDiscussInfo"
ACTIVE_PAGE = {
    "2": "/page/sign/signIn",                                  # 签到
    "74": "/page/sign/signIn",                                 # 签退
    "50": "/page/topicDiscuss/student/stu_reply_discuss",      # 主题讨论
    "42": "/page/quiz/stu/answerQuestion2",                    # 随堂练习
    "14": "/page/quiz/stu/answerQuestion",                     # 问卷
    "43": "/page/qvote/stu/answerQvote",                       # 投票
    "68": "/page/interactivepractice/stu/answerQu",            # 互动练习
}

# 归成用户要的四类。用户 2026-10-22：
#   「学习通的作业信息也分成几种，比如主题讨论，签到信息，作业，考试」
# 考试不在这里 —— 它在单独的页上（EXAM_LIST），不在活动列表里。
KIND_BUCKET = {
    "sign": ("2", "74"),      # 签到 / 签退
    "topic": ("5", "50"),     # 主题讨论
    "work": ("19",),          # 作业（活动列表里也会有作业提醒）
    "notice": ("34", "45"),   # 通知。34 是老版本，45 是新版本 —— 同一件事的两种 type。
                              # 🔴 45 是南师侧 2026-10-23 拿原始响应实测出来的：那条记录的
                              #    content 里是 creatorRealName（老师名）、extraInfo 里是
                              #    noticeId（通知主键）。不是猜的。
                              #    只认 34 的后果：新版通知全落进「其他」，用户看不到。
    # 随堂练习 / 互动练习（0.39.0）。用户 2026-11-01 原话：
    #   「还很容易错过作业和考试，还有随堂练习」。
    # 以前它们落到 other，和「白板 / 教案 / PDF 文档 / 计时器」挤在同一格 ——
    # 那格本来就是垃圾桶，真正的任务淹没在里面，等于没提示。
    # 单独一类，排在做作业后面：这是**要在几分钟内做完**的东西，
    # 比作业还急，不该跟「老师传了个 PDF」平起平坐。
    "quiz": ("42", "68"),
}

# 分类的中文名 + 显示顺序。前端的分段条照这个顺序生成，两边别各写一份。
KIND_NAME = {
    "work": "作业", "exam": "考试", "quiz": "练习", "sign": "签到",
    "topic": "讨论", "notice": "通知", "other": "其他",
}
KIND_ORDER = ("work", "exam", "quiz", "sign", "topic", "notice", "other")

# 作业状态分三档。不认识的走「其他」，两边都不算。
# 🔴 「待互评」是**要我去评别人**，是要我动手的事 ⇒ 必须算 todo。
#    以前它两边都不在，落到「其他」并被排除在待办之外 —— 等于把一件该做的事藏了。
# 🔴 「待批阅 / 已批阅」是**我已经交了**、等老师 ⇒ 算 done，绝不能算要交。
#    （2026-10-23 南师侧实测：76 条作业里 63 条是这种，全算「要交」会让人一进门
#     看到 76，滑三屏才发现只有 7 条真没交 —— 那会训练用户忽略这个数字。）
TODO_STATES = ("未交", "未开始", "待互评")
DONE_STATES = ("已完成", "待批阅", "已批阅", "已互评")

# 🔴 2026-11-09：精确匹配之外再加一层**包含匹配**。
#    用户反馈「作业明明做完了，点重新检查还是显示没完成」。学习通给的状态
#    不止上表那几个词：同一套接口在不同学校/不同作业类型下还会回
#    「已提交」「已交」「已通过」「未提交」「已过期未交」……
#    精确匹配认不出来 ⇒ bucket() 返回 other ⇒ 这条既不算交、也不算没交，
#    界面上就**一直停在「未完成」那一支**（严格说是不显示，但用户看到的是
#    「它还在那儿，还催我交」）。这就是那句反馈的来源。
#
# 🔴 否定词必须**先判**：中文里「未完成」「未提交」都**包含**「完成」「提交」，
#    先判肯定词会把「未完成」判成已完成 —— 那比认不出来坏得多（把没做的事藏了）。
TODO_WORDS = ("未交", "未完成", "未提交", "未开始", "待提交", "待完成",
              "进行中", "未批阅")
# 「待批阅 / 已批阅」都是**我交了**，等老师。所以进 done。
# 「待互评」在 TODO_WORDS / TODO_STATES 里（要我去评别人），别混进来。
DONE_WORDS = ("已完成", "已提交", "已交", "已批阅", "待批阅", "已通过",
              "已互评", "已作答", "已参加")

# 考试状态。🔴 只把**明确表示已经考完/考过**的算 done；
#    没见过的取值一律留在待办里，并把原始状态文字原样显示出来。
#    宁可多显示一条，也不能把一场还没考的考试藏起来。
#
# 🔴 0.46.0 补「待批阅」。作业那边早就算 done（见上），考试这边漏了 ——
#    2026-11-06 用户截图：`2 条考试` 里挂着「23级开学测试 · 待批阅」和
#    「2024-9-12课堂笔记提交 · 待批阅」，两条都是两年前的、早就批完的卷子。
#    考试交卷后学习通给的就是「待批阅」（和他已经交上去这件事同义），
#    留在待办里等于天天提醒他去考一场已经考完的试。
#    ⚠️ 是「待批阅」不是「已批阅」：前者是卷子交上去了等老师，后者是分出来了。
#       两个都要算 done，少写一个就是一个少了三个字的漏项。
DONE_EXAM_STATES = ("已完成", "已结束", "已交卷", "已参加", "待批阅", "已批阅", "已过期")

# 每门课之间的间隔（秒）。加抖动，别让请求节奏整齐得像机器。
#
# 🔴 0.44.1：0.9 → 1.8。用户原话「点重新检查显示检查的太频繁了，你把频率
#    降低些」。原来「间隔 0.9 秒」是假的：一门课 4 个请求连发、中间不等，
#    然后才 sleep，所以真实节奏是「4 个请求挤在一瞬间，再停 1 秒」——
#    30 门课就是 120 个请求在四十来秒里砸过去，学习通直接 202。
#    现在请求之间也隔一下（_pace），课与课之间 1.8 秒，一轮约 2 分钟。
#    ⚠️ 必须和 web/native.js 的 CX_GAP / CX_JITTER 保持一致（对拍会验）。
SCAN_GAP = 1.8
SCAN_JITTER = 0.6

# 同一个会话里，两个学习通请求之间至少隔这么久（秒）。
# 为什么统一在 _pace() 里做：4 个请求散在 courses/course_meta/work_list/
# exam_list/active_list 五个方法里，逐个加 sleep 迟早漏一个，而漏掉的那个
# 正好就是「连发」的那一发。节流放在唯一的出口上，谁都绕不过去。
REQ_GAP = 0.45

# 被 202 拦下之后的冷静期（秒）。风控窗口是分钟级的，而前端手点的冷却只有
# 90 秒 —— 用户在窗口里点一次失败一次，每次失败还把窗口续上，越试好得越慢。
# 所以撞一次就把「几点之前别再试」记住，期间一个请求都不发。
BUSY_COOL = 15 * 60

# ---- 结课判定 ---------------------------------------------------------
# 一门课「四个请求全成功、三个列表全空」连续出现 REST_STREAK 次，且从第一次
# 空到现在已经过了 REST_SPAN，就认为它结课了；之后每 REST_GAP 才扫一次。
#
# 🔴 为什么不能「首次扫到空就判结课」：
#    开学第 1 周几乎所有课都还没发作业 —— 实测 32 门课里 13 门是空的，
#    其中「线性代数」「高等数学」都是**本学期**的课，只是作业还没留。
#    首次就判的话，开学第一周能把整个学期的课全判死，然后老师真发了作业，
#    用户永远看不到。这比「慢 90 秒」糟得多。
#
# 🔴 为什么是「降频」而不是「永不扫」：
#    判据总有出错的那天（老师换课、作业晚发、我们解析过时）。
#    永不扫 = 错了就永远发现不了；每 7 天扫一次 = 最坏晚 7 天。
#    用户的原话是「结课后不再具体扫描」，这里的「不扫」是「几乎不扫」，
#    留的这条低频线就是给他兜底的。
#
# 🔴 为什么「没读到」不算空：
#    502 / 202 / 超时 / 课程页没打开 —— 这些是「不知道」，不是「没有」。
#    拿它们当空，等于把被限流的课判成结课，然后它就再也不会被扫。
#    这跟我们刚修的「502 被当成没作业」是同一个病，只是后果更重：
#    那次是这一次显示错了，这次是**以后永远不查**。
REST_STREAK = 3
REST_SPAN = 7 * 86400
REST_GAP = 7 * 86400

# ---- 冷课判定（0.39.0）------------------------------------------------
# 上面的规则要求「三个列表全空」—— 但历史学期的课**不空**：它们还挂着
# 上学期的作业和活动。于是 37 门课里 22 门一个学期都判不出结课，
# 每次刷新仍然全量扫，用户的原话是「每次都需要全量扫描」。
#
# 换个证据：**有内容，但最新那条已经是 COLD_DAY 之前的事**。
#   真读到了内容（不是「没读到」），只是那些内容全都旧了 ⇒ 这门课没在动。
#
# 🔴 为什么冷课可以「一次就降频」，而空课必须攒 3 次 + 7 天：
#    空 = 没有证据（老师可能只是还没发作业）；冷 = 有证据（东西都在，都很旧）。
#    两件事的可信度差一档，处理也不该一样。
COLD_DAY = 90 * 86400

# 0.44.0：**没有教务课表**时用的冷课门槛，比上面那条更狠。
# 有课表时「这门课不在本学期名单里」本身就是证据，90 天够了；没课表时
# 只剩内容时间这一条证据，那就得多要一点：一学期通常 20 周，上学期的课
# 到 11 月，最新一条内容至少也是 5 个月前的事；而本学期还在上的课，
# 哪怕老师只在开学第一周发过东西，也在 150 天以内。
# ⚠️ 过了这道线只是**每 7 天扫一次**（REST_GAP），不是永远不扫。
# ⚠️ 必须和 native.js 的 CX_COLD_NO_WHITE 一致。
COLD_NO_WHITE = 150 * 86400

# ---- 0.47.0：只看本学期的课 -------------------------------------------
# 用户 2026-11-06：「听学长说教务系统可以直接看本学期课程……现在我每次都
# 还是需要扫 37 个课程，但我这个学期只有十几个课程」。
#
# 上面两条判据（三个列表全空 / 最新内容 90 天前）对历史学期的课**判不动**：
#   它们不空 —— 上学期的作业还挂在里面；而「最新一条 90 天前」对开学才两个
#   月的课也不成立。于是 37 门课里二十多门一个学期都判不出结课，每轮全量扫。
#
# 现在换一个更硬的证据：**教务系统里本学期在修的课表**。
#   · 课表里有的课      → 每轮全扫（4 个请求）
#   · 课表里没有的课    → 判结课，只花 1 个请求看活动列表（签到在里面）
# 为什么结课的课还要留那 1 个请求：签到的时效是「几分钟」，漏了就没了；
# 而「点名专用」这类课教务课表里根本没有，它唯一的内容恰恰就是签到。
ACT_PROBE_GAP = 7 * 86400     # 结课的课多久探一次活动列表
WARM_ACT = 30 * 86400         # 探到这么久以内还有活动 ⇒ 下一轮恢复全扫
STALE_END = 7 * 86400         # 早已结束的作业/考试，不再列进待办

# 连续几门课都「跳到登录页」才算会话真的没了。与 native.js 的
# CX_AUTH_STREAK 同一个数、同一个理由（见 scan() 里的 auth_n）。
AUTH_STREAK = 3


class CxError(Exception):
    """kind: 'auth' 登录/登录态有问题，'net' 网络，'deny' 被拒，'parse' 结构变了。

    busy: 这一条是不是「查得太频繁，被风控拦了」（HTTP 202）。
          🔴 0.44.0 新增。和 net 的区别不是文案，是**要不要接着问**：
          202 意味着再问下去只会封得更久，所以扫描要整轮停手；
          别的网络错误只影响这一门课。
    """

    def __init__(self, msg: str, kind: str = "net", busy: bool = False) -> None:
        super().__init__(msg)
        self.kind = kind
        self.busy = bool(busy)


def _status_ok(r, what: str) -> None:
    """非 200 一律当服务端/网络错误。

    🔴 为什么必须显式判：`requests` 默认**不**对 4xx/5xx 抛异常，
       所以 502 页会被当成正常响应交给解析器。而 `parse_work` 找不到 `<ul>`
       就返回 `[]` —— 「服务器挂了」于是被静默当成「这门课没有作业」。

    实测（`_lab/cx/probe_502.py`，两门课全部 502）修之前的样子：
        work   → code=noentry 「没给作业入口」      ← 假话
        exam   → code=noentry 「没给考试入口」      ← 假话
        active → code=auth    「登录态可能失效了」  ← 假话，而且更糟：
                  `scan()` 见到 auth 就 break，**第二门课根本不查、也不记录**，
                  界面上它凭空消失了。

    2026-10-23 修。南师侧同一天真撞上「一门课 502 让整场扫描崩掉」
    （他的 `except CxError` 漏了网络层的 `NynuError`）。我这边不崩，
    但静默少数据 —— 两个都不是「说真话」。

    🔴 2026-10-24 补：**202 不是「服务器出错」，是「查得太密被限流」。**
       用户真机上 17 门课一起返回 202（截图 2026-10-24）。202 = Accepted，
       请求收到了但没给内容 —— 学习通那边通常就是风控在排队。
       这时候「等几分钟再试」是有用的；「不是你的问题」听着像没事，
       用户就不会等，反而接着点，越点越封。
       所以 202 单独一句文案，别的非 200 才说「服务器出错了」。
    """
    code = int(getattr(r, "status_code", 0) or 0)
    # 🔴 2026-10-25 补：401 是「未授权」，不是「服务器出错」。
    #    以前 401 走下面那条「学习通服务器出错，非本机问题」——
    #    那句话会让用户干等着，而他真正该做的是点「重新连接」。
    #    而且 401 判成 net 之后，`scan()` 不会 break，剩下 25 门课
    #    会各发一次注定 401 的请求 —— 白打 25 次，还加重风控。
    #    ⚠️ 403 仍然算 net：学习通的 403 多半是风控拦的，不是账号问题。
    #    ⚠️ 必须和 `web/native.js` 的 `cxStatusOk()` 逐字一致（对拍会验）。
    if code == 401:
        # 🔴 2026-11-02：文案跟着 web/native.js 改了 —— 原来写「返回 401（登录态
        #    过期了…）」，HTTP 码和「登录态」这种词用户看不懂，而且屏幕上一句
        #    话里出现「401」只会让人以为是自己操作错了。JS 那边早就是这个说法，
        #    对拍判据一直在红（两边不一致），这次补上。
        raise CxError("%s打不开，登录过期了，点「重新连接」再试一次"
                      % what, "auth")
    if code == 202:
        # 🔴 0.44.0：带 busy=True 抛出去，让 scan() 整轮停手。
        #    以前继续下一门，31 门课各撞一次墙，界面上写「31 门课没查到」——
        #    事实是「查到第 4 门就被拦了，剩下 27 门一个请求都没发」。
        raise CxError("%s查得太频繁了，等 15 分钟再试" % what, "net", busy=True)
    # 🔴 status 0 = 请求**根本没发出去**（断网、DNS、被网关拦、超时）。
    #    以前它和真的 500 走同一句「学习通服务器出错，非本机问题」——
    #    那句会让人什么都不做地等着。真正该做的是先看网络。
    #    ⚠️ 和 `web/native.js` 的 `cxStatusOk()` 同一个位置、同一句话。
    if code == 0:
        raise CxError("连不上学习通：网络没通，或者校园网还没登录", "net")
    if code != 200:
        raise CxError("%s返回 %d（学习通服务器出错，非本机问题）"
                      % (what, code), "net")


def _looks_logged_out(text: str) -> bool:
    """这段内容是不是「登录页」而不是接口数据。

    ⚠️ 必须和 `web/native.js` 的 `cxLooksLoggedOut()` 逐字对齐
       （`_lab/cx/check_cx_js.py` 拿同一份输入对拍）。

    🔴 为什么需要它：没登录时学习通会把接口 302 到登录页，回来的是 200 +
       一大坨 HTML。只判「JSON 解析失败」的话，会报「接口可能变了」——
       那是**假话**，用户按它理解只能等更新，而真正该做的是重连。

    🔴 先判「像不像 JSON」再判关键字：正常数据里 courseName 完全可能带
       「用户登录」四个字（一门叫《用户登录与鉴权》的课），
       只看关键字会把正常数据判成没登录 —— 比反过来更糟，
       因为它会让人白白重连一次、还可能把密码撞错。
    """
    s = "" if text is None else str(text)
    if not s:
        return False
    head = s.lstrip()[:1]
    if head in ("{", "["):
        return False                       # 是 JSON 就不可能是登录页
    return ("passport2.chaoxing.com" in s) or ("用户登录" in s)


def _json_logged_out(j) -> bool:
    """解析出来的 JSON 是不是「没登录」。

    ⚠️ 必须和 `web/native.js` 的 `cxJsonLoggedOut()` 逐字对齐
       （`_lab/cx/check_cx_js.py` 拿同一批输入对拍）。

    🔴 2026-11-12 实测：没登录时 `backclazzdata` 回的是 **200 + 合法 JSON**
       `{"result":0,"msg":"请重新登录","status":false,"errorMsg":"请重新登录"}`。
       上面那个 `_looks_logged_out()` 只认 HTML 登录页（见到 `{` 就 false），
       于是它掉进「没有 channelList」那条，报成「课程列表结构变了」——
       假话，而且归成 parse：不停扫描、不提示重连，用户只能干等更新。

    ⚠️ 必须同时满足「失败态」和「文案里有登录」：`result:0` 也可能是别的
       业务失败（课程被归档之类），只看 `result` 会让人白白重连一次。
    """
    if not isinstance(j, dict):
        return False
    bad = (j.get("status") is False) or (j.get("result") in (0, "0"))
    if not bad:
        return False
    s = str(j.get("msg") or j.get("errorMsg") or j.get("message") or "")
    return ("登录" in s) or ("login" in s.lower())


def rest_next(st: dict, ok_all: bool, is_empty: bool, now: int,
              newest=None) -> dict:
    """算一门课这一轮扫描之后的「结课判定」状态。**纯函数**。

    st       上一轮的状态（可能没有）
    ok_all   这一轮四个请求是不是全部成功（课程页 + 作业 + 考试 + 活动）
    is_empty 三个列表是不是都为空
    now      现在（unix 秒）
    newest   这门课最新一条内容的时间（截止时间 / 活动开始时间，unix 秒）。
             None = 有内容但都没给时间，或者一条都没有。见 COLD_DAY。

    🔴 为什么单独抽出来：APK 里没有 Python，`native.js` 的 `cxRestNext` 是
       **另一份实现**。两份必须逐条对拍（`_lab/cx/check_cx_js.py`）——
       只改一边不报错，只是手机上和网页上「哪些课算结课」会得出不同答案，
       而且用户看不出来是哪一边错了。

    🔴 ok_all=False 时**原样返回**：既不加次数（那是拿「没读到」当「没有」），
       也不清零（那会让好不容易攒的次数白费）。这是整个判据里最要紧的一条。
    """
    st = st if isinstance(st, dict) else {}
    if not ok_all:
        return dict(st)
    if is_empty:
        streak = int(st.get("streak") or 0) + 1
        since = int(st.get("since") or now)
        return {
            "streak": streak, "since": since, "lastAt": now,
            # 次数够 + 跨度够 才真的停扫。只满足一条不算：用户一天点三次刷新
            # 也能凑够 3 次，但那才过了半天，分不清「结课」和「今天还没发作业」。
            "resting": bool(streak >= REST_STREAK and now - since >= REST_SPAN),
        }
    # 有内容，但最新那条已经是 COLD_DAY 之前的事 ⇒ 冷课，这一轮就降频。
    #   历史学期的课全都落在这里：它们不是空的（上学期的作业还挂着），
    #   只是**全都旧了**。以前正是因为「不空」，这些课永远判不出结课。
    # 🔴 newest 不是数字（结构变了 / 页面没给）时**不判冷**：那是「不知道多旧」，
    #    不是「很旧」。宁可多扫一轮，也不要把一门课误判死。
    cold = False
    if newest is not None:
        try:
            cold = (now - int(newest)) >= COLD_DAY
        except (TypeError, ValueError):
            cold = False
    if cold:
        return {"streak": int(st.get("streak") or 0) + 1,
                "since": int(st.get("since") or now), "lastAt": now,
                "resting": True}
    # 有内容且是新的 ⇒ 立刻恢复。这是判错时的自我纠错：判成结课以后
    # 低频扫到东西，下一轮就回到每轮都扫。
    return {"streak": 0, "since": now, "lastAt": now, "resting": False}


def unique_courses(courses) -> list:
    """按 `(courseId, classId)` 去重。**纯函数**，和 `native.js` 的逐条对齐。

    🔴 0.39.0 之前只按 `courseId` 去重，注释写着「不同 classId 的作业/考试/活动
       都是同一套」—— 那句是错的。同一个 courseId 下面可能挂着**不同学期**的
       教学班（实测这个账号的「大学物理实验」同时有 26-27-1 和 25-26-2 两个班，
       「高等数学（理工类）」有三个班跨三个学期）。
       压成一个 ⇒ 只查了其中**一个**班（留下的是先出现的那个），
       顺序一换，本学期的作业就一条都不在了，而界面上一切正常。
    """
    seen, out = set(), []
    for c in (courses or []):
        if not isinstance(c, dict):
            continue
        k = (c.get("courseId"), c.get("classId"))
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def rest_key(course_id, class_id) -> str:
    """结课状态的 key：`<courseId>:<classId>`。**纯函数**。

    🔴 为什么必须带 classId：同一个 courseId 下面可能挂着**不同学期**的教学班
       （实测这个账号的「高等数学（理工类）」同时有 25-26-1 / 25-26-2 / 非当前
       三个班）。只按 courseId 存，就等于让三个班共用一份「结没结课」的结论 ——
       本学期的班会被上学期的班判定成结课，然后真的作业就再也不会被查。
    和 `native.js` 的 `cxRestKey` 逐字对齐。
    """
    return "%s:%s" % (course_id, class_id if class_id is not None else "")


# ---- 教务课表白名单 ---------------------------------------------------
# 归一化：全角转半角、去掉所有空白和标点、罗马数字统一、砍掉学期后缀。
# 目标是让「大学英语Ⅰ（二）」和「大学英语I(二)」能对上，而不是做分词。
_FULL2HALF = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
_FULL2HALF[0x3000] = 0x20
_ROMAN = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V",
          "Ⅵ": "VI", "Ⅶ": "VII", "Ⅷ": "VIII"}


def _norm_name(s) -> str:
    """课名归一化。认不出就返回空串（空串永远不参与匹配）。"""
    t = str(s or "").translate(_FULL2HALF)
    for a, b in _ROMAN.items():
        t = t.replace(a, b)
    t = re.sub(r"[\s\u3000·・,，。、;；:：!！?？'\"“”‘’()（）\[\]【】<>《》/\\|+*#-]", "", t)
    t = t.upper()
    return t


def _norm_white(active_names) -> list:
    """教务课表 → 可比较的「本学期在修」清单。

    每一项是 `{"n": 归一化课名, "t": 归一化老师名}`。
    0.47.0 起前端会把**老师名一起**传下来 —— 光靠课名分不清哪一门是本学期的：
    实测这个账号的「高等数学（理工类）」同时挂着 25-26-1 / 25-26-2 / 非当前
    三个教学班，课名一模一样，只有老师不一样。

    兼容老调用方：纯字符串、`{"name":…, "teacher":…}`、`{"n":…, "t":…}` 都认。
    """
    if not isinstance(active_names, (list, tuple)):
        return []
    out = []
    for x in active_names:
        if isinstance(x, dict):
            n = _norm_name(x.get("name") or x.get("n") or "")
            t = _norm_name(x.get("teacher") or x.get("t") or "")
        else:
            n, t = _norm_name(x), ""
        if len(n) >= 2 or len(t) >= 2:
            out.append({"n": n, "t": t})
    return out


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4}


def cur_term(semester: str):
    """「2026-2027 学年第 1 学期」→ (26, 27, 1)。认不出返回 None。**纯函数**。"""
    s = str(semester or "")
    m = re.search(r"(20\d{2})\s*[-—–~至]\s*(20\d{2})", s)
    if not m:
        return None
    y1 = int(m.group(1))
    n = re.search(r"第\s*([12一二])\s*学期", s)
    k = 1
    if n:
        g = n.group(1)
        k = int(g) if g.isdigit() else _CN_NUM.get(g, 1)
    return (y1 % 100, (y1 + 1) % 100, k)


def course_term(text: str):
    """从课名 / 学习通的班级名里认「这是哪个学期」。认不出返回 None。**纯函数**。

    三种写法都是实测见过的（`_lab/cx/courses_raw.json` 里 37 门课）：
        25-26-2 / 2026-2027-1        学年-学期
        26年秋 / 25级秋 / 2026秋      年级（学年起点）+ 季节
        2026.9                       年月

    🔴 这是「结课」判据里唯一一条**语义级**证据：课名只能说明「是同一门课」，
       说明不了「是不是这个学期」。实测「高等数学（理工类）」同时挂着
       25-26-1 / 25-26-2 / 非当前 三个教学班，课名一模一样。
    🔴 认不出就返回 None，**绝不当成「不是本学期」** —— 那是把不知道说成知道。
    """
    s = str(text or "")
    m = re.search(r"(?<!\d)(?:20)?(\d{2})\s*[-–—]\s*(?:20)?(\d{2})\s*[-–—]\s*([12])(?!\d)", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(?<!\d)(?:20)?(\d{2})\s*(?:级|年)?\s*([春秋])", s)
    if m:
        y = int(m.group(1))
        return (y, (y + 1) % 100, 1 if m.group(2) == "秋" else 2)
    m = re.search(r"(?<!\d)(20\d{2})\.(\d{1,2})(?!\d)", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        k = 1 if 8 <= mo <= 12 else 2
        y1 = y if k == 1 else y - 1
        return (y1 % 100, (y1 + 1) % 100, k)
    return None


def is_active_course(course: dict, white, term=None) -> bool:
    """这门课是不是「本学期教务课表里在修」。**纯函数**。

    三条**正向**证据，命中任意一条就算在修：
      ① 课名互相包含 / 前 4 个字相同（0.39.0 的老口径，故意宽松）
      ② 老师名出现在学习通的**班级名**里 —— 「25计科1-3班-于占军-大物2」这种
      ③ 教务课名整段出现在班级名里（老师把课名写进班级名的情况）

    一条**反向**证据（`term` 给得出时才用）：
      ④ 课名或班级名里写着学期，而且**不是当前学期** ⇒ 直接判结课。
         实测这一条能准确剔掉「高等数学 25-26-2」「大学英语I(一) 25级秋」
         这类课名一模一样、只是学期不同的班。

    🔴 为什么以「宁可多认」为准：误认 = 多扫一门，几十秒；
       漏认 = 这门课的作业**再也不会被查**。代价差得太远。
    🔴 为什么 ④ 可以反向：学期是**写出来的事实**，不是猜的。
       但它只在 term 认得出时才生效 —— `semester` 没传或格式变了，就等于没有这条。
    ⚠️ 和 native.js 的 `cxIsActiveCourse` 逐条对拍。只改一边不报错，
       只是手机上和网页上「哪些课算结课」会得出不同答案，用户还看不出来。
    """
    if not white:
        return False
    title = course.get("title") or ""
    alias = course.get("alias") or ""
    t = _norm_name(title)
    a = _norm_name(alias)
    if term is not None:
        for cand in (course_term(title), course_term(alias)):
            if cand is not None and cand != term:
                return False
    for w in white:
        n, tea = w.get("n") or "", w.get("t") or ""
        if n and t:
            if n == t or n in t or t in n:
                return True
            if len(n) >= 4 and len(t) >= 4 and n[:4] == t[:4]:
                return True
        if len(tea) >= 2 and a and tea in a:
            return True
        if len(n) >= 4 and a and n in a:
            return True
    return False


def is_stale(item: dict, now: float = None, span: int = STALE_END) -> bool:
    """这条是不是「早就结束了」—— 不再往界面上摆。**纯函数**。

    用户 2026-11-06：「已经结束一段时间了的就别显示了，除非是需要互评的作业」。

    判据只有一条：**有明确的截止时间，而且已经过去 span 以上**。
    🔴 必须要求 endAt 存在：实测真有「未交但没写截止时间」的作业，
       那种不是「早就结束」，是「不知道什么时候结束」—— 不能藏。
    🔴 要回去互评的不算：截止之后还得去评别人的作业，藏起来就是漏了一件事
       （和 TODO_STATES 里把「待互评」算成 todo 是同一条道理）。
    🔴 只管作业和考试：签到、讨论、通知各有各的时间口径，另算。
    """
    if item.get("kind") not in ("work", "exam"):
        return False
    if "互评" in str(item.get("status") or ""):
        return False
    end = item.get("endAt")
    if not end:
        return False
    now = time.time() if now is None else now
    return (now - end) >= span


def _progress(cb, i: int, n: int, title: str, phase: str = "") -> None:
    """调前端传进来的进度回调。

    老调用方只接 3 个参数（没有 phase），多传一个会 TypeError —— 退回 3 参数再试。
    回调本身出错绝不能连累扫描：它只是个显示器，坏了不该让整件事停下。
    """
    if not cb:
        return
    try:
        cb(i + 1, n, title, phase)
    except TypeError:
        try:
            cb(i + 1, n, title)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ 纯函数（可单测）

def aes_b64(text: str) -> str:
    """照抄 login.js 的 encryptByAES：AES-CBC + PKCS7，key 当 iv，输出 Base64。

    iv 是固定的 ⇒ 同一个密码每次算出同一串密文。验收脚本靠这条性质反推
    「用的到底是哪份密码」。
    """
    c = AES.new(AES_KEY, AES.MODE_CBC, AES_KEY)
    return base64.b64encode(c.encrypt(pad(text.encode("utf-8"), 16))).decode()


def parse_left(text: str):
    """「剩余32小时54分钟」→ 1974（分钟）。认不出来返回 None。

    🔴 不兜底成 0：0 分钟意味着「已经到期了」，那是完全相反的意思。
       认不出来就是认不出来，调用方自己决定怎么显示。
    """
    if not text:
        return None
    s = str(text)
    if "剩余" not in s:
        return None
    d = re.search(r"(\d+)\s*天", s)
    h = re.search(r"(\d+)\s*小时", s)
    m = re.search(r"(\d+)\s*分", s)
    if not (d or h or m):
        return None
    return ((int(d.group(1)) * 1440 if d else 0)
            + (int(h.group(1)) * 60 if h else 0)
            + (int(m.group(1)) if m else 0))


def parse_due(text, now: float = None):
    """「结束时间：10-20 15:00」→ unix 秒。认不出返回 None。**纯函数**。

    作业 / 考试的列表页在**有绝对时间**时会写这一行；倒计时那行只在还没到
    截止时才有，**过期以后就只剩这一行**。以前这里只认「剩余 X」，于是
    过期作业的 endAt 是 None —— 界面上既说不出截止时间，也就判不出
    「早就结束了」，那些两年前的作业会永远挂在待办里。

    只认两种写法：`2026-10-20 15:00` 和 `10-20 15:00`（跨年自己猜）。
    🔴 猜年份的规矩：差半年以上就归到相邻的那一年。1 月看到「12-31」是去年，
       12 月看到「01-05」是明年 —— 别的可能都不成立。
    """
    if not text:
        return None
    s = str(text)
    # 🔴 考试页的 `.time` 也可能是「开始时间：…」。那是**开始**，不是截止，
    #    当成截止会把一场还没开始的考试排到「已过期」去。
    if "开始" in s and "结束" not in s and "截止" not in s:
        return None
    # 两行并在一个格子里时（「开始时间：… 结束时间：…」），只认结束那一半。
    # 直接拿第一个日期会取到开始时间 —— 那会把一场还没开始的考试
    # 判成「早就结束了」。
    for kw in ("结束时间", "截止时间", "结束", "截止"):
        i = s.find(kw)
        if i >= 0:
            s = s[i:]
            break
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})[\sT]+(\d{1,2}):(\d{2})", s)
    if m:
        y, mo, d, h, mi = (int(x) for x in m.groups())
        try:
            return int(time.mktime((y, mo, d, h, mi, 0, 0, 0, -1)))
        except (ValueError, OverflowError):
            return None
    m = re.search(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    mo, d, h, mi = (int(x) for x in m.groups())
    now = time.time() if now is None else now
    year = time.localtime(now).tm_year
    t = None
    try:
        t = int(time.mktime((year, mo, d, h, mi, 0, 0, 0, -1)))
    except (ValueError, OverflowError):
        return None
    if t > now + 180 * 86400 or t < now - 180 * 86400:
        try:
            t = int(time.mktime((year - 1 if t > now else year + 1,
                                 mo, d, h, mi, 0, 0, 0, -1)))
        except (ValueError, OverflowError):
            return None
    return t


def parse_courses(j: dict) -> list:
    """把 backclazzdata 的 JSON 变成课程列表。

    🔴 字段位置是实测出来的：
       courseId = content.course.data[0].id
       classId  = content.id
       cpi      = channel.cpi（同一个人的所有课都是同一个数）
       title    = data[0].name（干净课名）
       alias    = content.name（班级课名，带老师/周次，不能当课名用）
       schoolId = data[0].belongSchoolId（学校 id，活动列表接口要这个；
                  江财是 1286。🔴 别拿课程页里 id="fid" 那个数，那是别的东西）
    """
    out = []
    for ch in (j.get("channelList") or []):
        ct = ch.get("content") or {}
        rows = ((ct.get("course") or {}).get("data")) or []
        if not rows:
            continue
        d = rows[0]
        out.append({
            "courseId": d.get("id"),
            "classId": ct.get("id"),
            "cpi": ch.get("cpi") or ct.get("cpi"),
            "title": d.get("name") or ct.get("name") or "",
            "alias": ct.get("name") or "",
            "schoolId": d.get("belongSchoolId") or "",
        })
    return out


def _end_from_left(left_min, now=None):
    """「还剩 2 小时 10 分钟」→ 绝对截止时间（unix 秒）。

    列表页**只给倒计时，不给绝对时间**（作业页和考试页都是这样），
    所以只能在这一刻换算：`now + left_min*60`。

    为什么要存绝对时间而不是只存分钟：
      1. 界面上要同时给「还剩」和「截止 10-23 23:59」—— 后者得有个来源；
      2. 只存分钟的话，缓存放两小时，那个「还剩 2 小时」还在原地不动，
         和同一行里算出来的截止时间自相矛盾。
    精度就是原文的精度（分钟），换算误差不到 1 分钟。
    """
    if left_min is None:
        return None
    return int((time.time() if now is None else now) + left_min * 60)


def _txt(frag) -> str:
    """HTML 片段 → 一行纯文本（去标签、解实体、折空白）。

    🔴 只在解析器里用。和 `_plain()` 的区别：那个带长度截断、是给 diag
       用的；这里要的是**原文一字不差**（标题、状态要能跟 aria-label 对上）。
    """
    s = re.sub(r"<[^>]+>", " ", str(frag or ""))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_work(html: str, now: float = None) -> list:
    """解析作业列表页。结构见模块注释上面的 work_truth.html。

    每条 li：
      aria-label = "标题 ; 状态"    ← 最干净的来源（标题和状态一次拿全）
      <p class="status fl">        ← 状态（和 aria-label 冗余，做交叉校验用）
      <div class="time notOver">   ← 「剩余32小时54分钟」，没有这个 div 就没有倒计时
      data="..."                   ← 详情页地址（点进去在浏览器里看）

    🔴 2026-11-16：**aria-label 不再是唯一的路**。
       用户报「很多学习通作业都读取错误了」。考试页（`parse_exam`）从一
       开始就写了「没有 aria-label 就退回读 p.overHidden2 / p.status」，
       作业页却只有 aria-label 一条 —— `if not lab: continue`。
       于是学习通一改版（或某门课走的是另一套模板），整页作业**一条都读
       不出来**，而 `work_list()` 拿到的是一份 200 的正常 HTML：不报错、
       不抛异常，这门课在界面上就是「没有作业」。
       同一个产品里两个解析器，一个有兜底一个没有，这是最不该有的差别。
       现在作业页和考试页走同一套三步：aria-label → aria-label 拆不开就
       读文字 → 标题实在没有才跳过（不拿 id、时间当标题）。
    """
    m = re.search(r"<ul[^>]*>([\s\S]*?)</ul>", html)
    if not m:
        return []
    out = []
    for li in re.findall(r"<li[^>]*>[\s\S]*?</li>", m.group(1)):
        # 🔴 只收「任务条目」：要么带 aria-label，要么有 right-content 那一块。
        #    页面里还有导航、筛选、翻页的 <li>，它们没有这两样。
        if "right-content" not in li and "aria-label" not in li:
            continue

        title, status = "", ""
        lab = re.search(r'aria-label="([^"]*)"', li)
        if lab:
            # 🔴 rsplit 而不是 split：万一标题里带分号，状态仍然是最后一段
            parts = lab.group(1).rsplit(";", 1)
            title = parts[0].strip()
            status = parts[1].strip() if len(parts) > 1 else ""
        # aria-label 没有 / 拆出来是空的 ⇒ 读页面上的字（和 parse_exam 一致）。
        if not title:
            mm = re.search(r'<p[^>]*class="[^"]*overHidden2[^"]*"[^>]*>([\s\S]*?)</p>', li)
            if mm:
                title = _txt(mm.group(1))
        if not status:
            mm = re.search(r'<p[^>]*class="[^"]*status[^"]*"[^>]*>([\s\S]*?)</p>', li)
            if mm:
                status = _txt(mm.group(1))
        if not title:
            continue

        # 🔴 0.47.0：原来写的是 `class="time "`（后面必须还有东西），
        #    `<div class="time">结束时间：10-20 15:00` 这种**一个都匹配不上** ——
        #    过期作业的截止时间就是这么丢的。
        raw_left = ""
        tm = re.search(r'<div class="time([^"]*)"[^>]*>([\s\S]*?)</div>', li)
        if tm:
            raw_left = _txt(tm.group(2))
        else:
            # 有的模板 `.time` 是 <p>/<span>（考试页早就认这一种，作业页没认）
            tm = re.search(r'<(p|span) class="time([^"]*)"[^>]*>([\s\S]*?)</\1>', li)
            if tm:
                raw_left = _txt(tm.group(3))

        href = re.search(r'data="([^"]*)"', li)
        left_min = parse_left(raw_left)
        # 有绝对时间就用绝对的；没有才拿倒计时换算（换算会随读取时刻漂移）。
        end_at = parse_due(raw_left, now)
        if end_at is None:
            end_at = _end_from_left(left_min, now)
        elif left_min is None:
            # 只有绝对时间、没有倒计时 ⇒ 自己算剩余。过期作业走的就是这条路：
            # 算出来是负数，sort_rank 才会把它分到「已过期」那一档。
            left_min = int((end_at - (time.time() if now is None else now)) // 60)
        out.append({
            "kind": "work",
            "kindName": "作业",
            "title": title,
            "status": status,
            "state": bucket(status),
            "left": raw_left,                      # 原文，给人看
            "leftMin": left_min,                   # 分钟，用来排序；None = 没有倒计时
            "startAt": None,
            "endAt": end_at,
            "url": (href.group(1).replace("&amp;", "&") if href else ""),
        })
    return out


def done_items_of(items, now: float = None, limit: int = 60) -> list:
    """已经做完的那批（作业 / 考试 / 练习），最近做完的排前面。**纯函数**。

    🔴 2026-11-09 抽出来是为了能单测：这段原来写在 scan() 里，
       而 scan() 要发几十个请求才跑得到 —— 判据不可能为了一行过滤去起会话，
       于是那行逻辑就一直没人验。界面上的「已完成 N 条」全指着它。
    🔴 早就结束的不算「做完了」：is_stale 那批是「没做也没戏了」，
       混进来会让用户以为那些也交过。
    """
    out = [x for x in items
           if x.get("state") == "done"
           and x.get("kind") in ("work", "exam", "quiz")
           and not is_stale(x, now)]
    out.sort(key=lambda x: -(x.get("endAt") or x.get("startAt") or 0))
    return out[:limit]


def downgrade_auth(failed) -> list:
    """把「疑似登录失效」的失败记录降级成「这一门没读到」。**纯函数**。

    只在探针（`session_alive`）证明会话还活着之后调用。

    🔴 为什么要降级：`auth_stop` 的每一条证据都是字符串特征（页面上出现
       「用户登录」、不是 JSON、401），一个维护页 / 防护页 / 归档课空壳页就
       能同时骗过连续三门课。那时候界面写着「登录态过期了」，而用户明明
       还登着 —— 他唯一能做的就是再登一次，登完发现本来就没掉。
    🔴 降成 lookauth 而不是直接删掉：这些课**真的没扫到**，界面上要照实算进
       「没查到」。删掉的话 scanned 会把它们算成「查过了」，
       用户看到的是「都查了、都没作业」—— 那正是这个产品最不能犯的错。
    🔴 2026-11-15 补第二件：那些**个别**课的失败记录（part 是 work / exam /
       course，不是 auth）本身没被标成「掉了」，但它们的 `why` 原样抄的是
       CxError 的原文 —— 很可能就写着「登录态过期」。探针刚证明会话是好的，
       界面却把「登录态过期」逐门列出来，用户看到的还是同一句假话
       （「没查到」的明细里写着过期，他只会去重登）。
       所以 why 里带「过期 / 失效 / 重新登录」的一律改成中性说法。
    ⚠️ 必须和 native.js 的 `cxDowngradeAuth()` 逐条对齐（对拍会验）。
    """
    out = []
    for f in (failed or []):
        if not f:
            continue
        why = str(f.get("why") or "")
        if f.get("part") == "auth":
            out.append({"course": f.get("course"),
                        "why": "这一门没读到（疑似跳了登录页）",
                        "part": "lookauth", "code": "lookauth"})
            continue
        if any(w in why for w in ("过期", "失效", "重新登录")):
            g = dict(f)
            g["why"] = "这一门没读到"
            out.append(g)
            continue
        out.append(f)
    return out


def bucket(status: str) -> str:

    """状态归三档。不认识的返回 'other' —— 不往任何一边凑。

    🔴 2026-11-09：以前只做**精确**匹配。学习通的状态词不是只有那几个：
        「已提交」「已交」「未提交」都能出现，精确匹配认不出就落 other，
        而 other 既不算交、也不算没交 —— 用户那句话「做完了还显示没完成」
        说的就是这儿（那条一直挂在待办里，怎么看都不像做完了）。
        现在补一层包含匹配，但**否定词先判**：中文的「未完成」「未提交」
        含「完成」「提交」这两个肯定词，反过来判会把没做的说成做完了。
    """
    s = (status or "").strip()
    if not s:
        return "other"
    if s in TODO_STATES:
        return "todo"
    if s in DONE_STATES:
        return "done"
    if any(w in s for w in TODO_WORDS):
        return "todo"
    if any(w in s for w in DONE_WORDS):
        return "done"
    return "other"


def kind_of(active_type) -> str:
    """activeType → 用户要的分类（work / exam / sign / topic / notice / other）。

    🔴 类型值可能是 int 也可能是 str（学习通自己返回的 JSON 不统一），
       先归一成字符串再比；认不出来就归 'other'，不往任何一类里凑。
    """
    t = str(active_type if active_type is not None else "").strip()
    if t.endswith(".0"):                       # 万一给了 5.0 这种
        t = t[:-2]
    for k, types in KIND_BUCKET.items():
        if t in types:
            return k
    return "other"


SIGNED_WORDS = ("已签",)                  # 已签到 / 已签退 都算签了
UNSIGNED_WORDS = ("未签", "缺勤")          # 未签到 / 未签退 / 缺勤


def sign_state(text: str):
    """签到的「我到底签了没」→ True / False / None。

    🔴 认不出来就返回 None，**绝不兜底成 False**。兜成「没签」等于凭空
       告诉用户「你漏签了」—— 这是会让人白跑一趟教务处的那种错。
       学习通的状态文字是会变的（老版本「未签到」、新版本可能给「缺勤」），
       所以只认这几个明确的词，其余一律 None（前端显示成状态原文）。
    """
    t = str(text or "").strip()
    if not t:
        return None
    if any(w in t for w in SIGNED_WORDS):
        return True
    if any(w in t for w in UNSIGNED_WORDS):
        return False
    return None


def _ms_to_sec(v):
    """毫秒时间戳 → 秒。空/0/非数字返回 None。

    🔴 学习通的 0 表示「没有这个时间」，不是 1970 年。当成 0 会算出一个
       五十多年前的倒计时，然后被当成「早就过期」悄悄丢掉。
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return int(n // 1000) if n > 0 else None


def _abs_url(u: str) -> str:
    """补全协议。学习通给的多半是 `//mobilelearn.chaoxing.com/...`。"""
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith(("http://", "https://")):
        return u
    return ""                                   # 相对路径 / javascript: 一律不要


def _content_stuurl(a: dict) -> str:
    """活动 JSON 的 `content` 里带的 `stuUrl`（学习通自己用的兜底地址）。

    🔴 `content` 有时是对象、有时是 JSON 字符串 —— 两种都认。模板里
       `switch(activeType)` 的 `default` 分支就是取这个字段，所以它比
       我们自己拼的路径更可信，有就用它。
    """
    c = a.get("content")
    if isinstance(c, str):
        try:
            c = json.loads(c)
        except (ValueError, TypeError):
            return ""
    if not isinstance(c, dict):
        return ""
    return _abs_url(c.get("stuUrl") or "")


def discuss_page_url(d: dict, cid: str, aid: str = "") -> str:
    """discuss 接口的 `data` → 讨论详情页地址。纯函数，便于对拍。

    格式照抄 `active_page.html` 的 `buildStuDiscussUrl()`（非 V3 那一支）：
        <groupwebDomain>/course/topicDiscuss/info?uuid=…&activeId=…
            &startTime=…&timeLong=…&activeStatus=…&courseId=…
    `groupwebDomain` 不回来就退回 groupweb.chaoxing.com（模板里同一个兜底）。
    """
    d = d or {}
    uuid = str(d.get("uuid") or "").strip()
    if not uuid:
        return ""
    # 🔴 用 `is None` 判空，不要 `or ""`：`timeLong` 合法值可以是 0，
    #    写成 `or` 会把它吞成空串，页面那头就少一个参数。
    def s(k, dflt=""):
        v = d.get(k)
        return dflt if v is None else str(v)
    dom = str(d.get("groupwebDomain") or "").strip().rstrip("/") \
        or "https://groupweb.chaoxing.com"
    return (dom + "/course/topicDiscuss/info?uuid=" + uuid +
            "&activeId=" + (s("activeId") or str(aid or "")) +
            "&startTime=" + s("startTime") +
            "&timeLong=" + s("timeLong") +
            "&activeStatus=" + s("activeStatus") +
            "&courseId=" + str(cid or ""))


def active_url(a: dict, course: dict) -> str:
    """一条活动 → 详情页地址。拼不出来就返回空串（界面就不显示小箭头）。

    路径不是猜的：从学习通自己的活动列表页模板里逐条抄的
    （`_lab/cx/ml/active_page.html` 的 `activeRowClick()`，190 KB 真实抓取）：

        case '2' / '74'   /page/sign/signIn?courseId&classId&activeId&fid
        case '50'         /page/topicDiscuss/student/stu_reply_discuss?…&cpi
        case '42'         /page/quiz/stu/answerQuestion2?…
        case '14'         /page/quiz/stu/answerQuestion?…
        case '43'         /page/qvote/stu/answerQvote?…
        case '68'         /page/interactivepractice/stu/answerQu?…
        default           content.stuUrl

    🔴 只做**上面这些参数就够**的类型。直播(17)、分组任务(35)、在线课堂(56)
       要先调别的接口拿 liveId / uuid，资源型(75/63)要把 content 解出来 ——
       这些宁可**不给链接**，也别给一个点开就报错的地址。
    """
    u = _content_stuurl(a)
    if u:
        return u
    path = ACTIVE_PAGE.get(str(a.get("activeType")).strip())
    if not path:
        return ""
    cid = str((course or {}).get("courseId") or "").strip()
    clid = str((course or {}).get("classId") or "").strip()
    fid = str((course or {}).get("schoolId") or "").strip()
    aid = str(a.get("id") or "").strip()
    if not (cid and clid and fid and aid):
        return ""                               # 缺一个参数就拼不出能用的地址
    q = "courseId=%s&classId=%s&activeId=%s&fid=%s" % (cid, clid, aid, fid)
    if path.endswith("stu_reply_discuss"):
        cpi = str((course or {}).get("cpi") or "").strip()
        if cpi:
            q += "&cpi=" + cpi
        q += "&showOnScreenShare=true"
    return CX_BASE + path + "?" + q


def parse_active(j: dict, now: float = None, course: dict = None) -> list:
    """活动列表 JSON → 条目（签到 / 主题讨论 / 通知 / 作业提醒 …）。

    字段名不是猜的，是从学习通自己的任务列表页模板里逐条抄的
    （_lab/cx/ml/active_page.html，191 KB 的真实抓取）：

        active.nameOne    标题
        active.nameTwo    状态文字（可能为空，模板里是 v-if="nameTwo"）
        active.nameFour   倒计时文字，🔴 只有 status=='1' 才渲染
        active.startTime / active.endTime   毫秒时间戳
        active.status     '1' 进行中 / '2' 已结束 / 其他 未开始
        active.activeType 活动类型，映射见 ACTIVE_KIND

    响应外层是 {result: 1, data: {activeList: [...]}}，result != 1 时
    把 errorMsg 带出去 —— 不能让它变成「这门课没有活动」。
    """
    if not isinstance(j, dict):
        return []
    if j.get("result") not in (1, "1", True):
        msg = str(j.get("errorMsg") or j.get("msg") or "").strip()
        raise CxError(msg or "活动列表返回失败", "deny")
    data = j.get("data")
    rows = data.get("activeList") if isinstance(data, dict) else None
    if not rows:
        return []

    now = time.time() if now is None else now
    out = []
    for a in rows:
        if not isinstance(a, dict):
            continue
        at = a.get("activeType")
        title = str(a.get("nameOne") or "").strip()
        if not title:
            continue                        # 没标题的条目画出来是一行空白，跳过
        st = str(a.get("status") if a.get("status") is not None else "").strip()
        # status 是**活动本身**的进度（1 进行中 / 2 已结束），
        # nameTwo 才是「我做完了没」（已签到 / 待批阅 / 已完成）。两件事。
        st_text = str(a.get("nameTwo") or "").strip()
        name4 = str(a.get("nameFour") or "").strip()
        start = _ms_to_sec(a.get("startTime"))
        end = _ms_to_sec(a.get("endTime"))

        # 倒计时：优先用页面自己给的文字（最准），没有就从 endTime 算。
        left_min = None
        if st == "1":
            left_min = parse_left(name4)
            if left_min is None and end:
                # 🔴 **不夹 0**。学习通把「已经过点」的活动在列表里多留一阵，
                #    status 还是 1、endTime 已经过去 —— 夹成 0 就等于把它说成
                #    「还剩不到 1 分钟」，是在催一件已经办不了的事。
                #    留负数，让 sort_rank() 把它分到「已过期」那一档，
                #    前端 cxMinLeft 也返回负数、写「已截止」。
                #    （2026-10-23 南师侧也踩了同一个，两边一致。）
                left_min = int((end - now) / 60)

        if st == "1":
            state = "live"                  # 正在进行
        elif st == "2":
            state = "ended"                 # 已结束
        else:
            state = "pending"               # 未开始

        k = kind_of(at)
        # 🔴 2026-11-09：随堂练习 / 互动练习的「做完没」不在 status 里。
        #    status=2 只说明**这场活动结束了**，做没做要看 nameTwo
        #    （「已完成」/「未完成」）。以前一律按 ended 处理，于是做过的
        #    练习和没做的练习长得一模一样，都被 `is_open` 排掉、也都进不了
        #    「已完成」那一批 —— 用户只能凭记忆。
        #    只改 quiz 这一类：签到/讨论的 nameTwo 是另一套口径，动它们会连累
        #    已经稳定的签到卡。
        # 🔴 2026-11-15：**作业也按 nameTwo 认**（不只练习）。
        #    「很多已经完成的作业还是不会自动变成已完成」—— 课程活动列表里
        #    每份作业都有一条（activeType 19），活动结束了就是 ended，
        #    而 nameTwo 明明白白写着「已完成」。只认 quiz 的话，这些交过的作业
        #    既进不了待办、也进不了「已完成」，等于从界面上消失；判错一点
        #    （nameTwo 空、status 还是 1）就会继续挂在待办里天天催。
        #    2026-11-09 只开了 quiz 是因为签到/讨论的 nameTwo 是另一套口径，
        #    作业和练习是同一套（已完成 / 未完成 / 待批阅），所以一起开。
        if k in ("quiz", "work") and bucket(st_text) == "done":
            state = "done"
            left_min = None                 # 做完了就不再喊还剩几分钟
        # 🔴 通知的 endTime 是**活动自己的生命周期终点**，不是待办截止时间。
        #    直接拿它渲染「截止 X」，一条公告就会显示成「已截止 2025-09-30」，
        #    看着像漏了一件事。通知整块不该有时间位。
        #    （2026-10-23 南师侧实测踩到，我这边同样中招。）
        if k == "notice":
            end, left_min = None, None

        out.append({
            "kind": k,
            "kindName": ACTIVE_KIND.get(str(at).strip(), "活动"),
            "title": title,
            "status": str(a.get("nameTwo") or "").strip(),
            "state": state,
            "left": name4,
            "leftMin": left_min,
            "startAt": start,
            "endAt": end,
            # 只有签到/签退才有这个字段；别的活动一律 None。
            # True 签了 / False 没签 / None 认不出来（见 sign_state 的说明）。
            "signed": sign_state(a.get("nameTwo")) if k == "sign" else None,
            "url": active_url(a, course),
            # 活动 id。老版讨论（activeType=5）拼不出静态地址，要靠它现问一次
            # 接口（见 Chaoxing.discuss_url）；别的类型用不上。
            "aid": str(a.get("id") or "").strip(),
        })
    return out


def parse_exam(html: str, now: float = None) -> list:
    """解析考试列表页（exam-ans/mooc2/exam/exam-list）。

    结构不是猜的：从这一页自己的样式表里反推出来的（_lab/cx/ml/exam-list.css，
    127 个类名），和作业列表页**同构**：

        .task-list .bottomList ul li
          .tag                        图标（icon-exam / self-exam / selfTestPreview）
          .right-content
            p.overHidden2             标题
            p.status                  状态（未开始 / 进行中 / 已结束 …）
            .label                    可能是「限时 60 分钟」这类补充
          .time / .time.notOver       「剩余…」或「开始时间：…」

    🔴 有 aria-label 就用它（作业页就是这么干的，一次拿全标题+状态），
       没有就退回读 p.overHidden2 / p.status 的文字。两条路都留着 ——
       页面改版时至少还有一条能撑住，而不是静默返回 0 条。
    """
    if not html:
        return []
    out = []
    # 🔴 正则必须把 <li ...> 的**开标签**也包进来 —— aria-label 和 data
    #    两个属性都挂在开标签上，只取标签内容的话这两条路就全废了，
    #    而且不会报错，只会静默退回到读文字（标题里会带上「限时 90 分钟」）。
    for li in re.findall(r"<li[^>]*>[\s\S]*?</li>", html):
        if "right-content" not in li:
            continue                        # 筛选栏、翻页那些 li 都不是任务

        title, status = "", ""
        lab = re.search(r'aria-label="([^"]*)"', li)
        if lab:
            parts = lab.group(1).rsplit(";", 1)
            title = parts[0].strip()
            status = parts[1].strip() if len(parts) > 1 else ""
        if not title:
            m = re.search(r'<p class="[^"]*overHidden2[^"]*"[^>]*>([\s\S]*?)</p>', li)
            if m:
                title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if not status:
            m = re.search(r'<p class="[^"]*status[^"]*"[^>]*>([\s\S]*?)</p>', li)
            if m:
                status = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if not title:
            continue

        raw_left = ""
        tm = re.search(r'<div class="time([^"]*)"[^>]*>([\s\S]*?)</div>', li)
        if tm:
            raw_left = re.sub(r"<[^>]+>", "", tm.group(2)).strip()
        elif not tm:
            # 有的页面 .time 是 <p> 或者 <span>
            tm = re.search(r'<(p|span) class="time([^"]*)"[^>]*>([\s\S]*?)</\1>', li)
            if tm:
                raw_left = re.sub(r"<[^>]+>", "", tm.group(3)).strip()

        href = re.search(r'data="([^"]*)"', li)
        done = any(s in status for s in DONE_EXAM_STATES)
        left_min = parse_left(raw_left)
        end_at = parse_due(raw_left, now)
        if end_at is None:
            end_at = _end_from_left(left_min, now)
        elif left_min is None:
            left_min = int((end_at - (time.time() if now is None else now)) // 60)
        out.append({
            "kind": "exam",
            "kindName": "考试",
            "title": title,
            "status": status,
            "state": "done" if done else "todo",
            "left": raw_left,
            "leftMin": left_min,
            "startAt": None,
            "endAt": end_at,
            "url": (href.group(1).replace("&amp;", "&") if href else ""),
        })
    return out


def _plain(text, limit: int = 400) -> str:
    """HTML 片段 → 一行能读的纯文本。

    🔴 收件箱条目的 `content` **就是全文**（不用再请求详情页），里面带着
       换行、<a>、<p>、实体字符。原样塞进界面会把一条消息写成一片源码。
    """
    t = str(text or "")
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", t)
    t = re.sub(r"(?is)<br\s*/?>|</p>|</div>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\r\f\v\u3000]+", " ", t)
    t = re.sub(r"\n[ \t]*(\n[ \t]*)+", "\n\n", t).strip()
    return t[:limit]


def _notice_time(v):
    """completeTime 可能是毫秒/秒时间戳，也可能是 `2026-11-13 10:22:31`。

    返回 (unix 秒, 原样字符串)。两个都拿不到就是 (0, "") —— 界面上不排日期，
    但这条消息仍然要显示（丢掉的是一条真消息，用户会以为老师没发）。
    """
    s = str(v or "").strip()
    if not s:
        return 0, ""
    if s.isdigit():
        n = int(s)
        if n > 10 ** 12:
            n //= 1000
        return (n, "") if n > 10 ** 9 else (0, s)
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})", s)
    if m:
        try:
            y, mo, d, hh, mi = (int(m.group(i)) for i in range(1, 6))
            return int(time.mktime((y, mo, d, hh, mi, 0, 0, 0, -1))), s
        except Exception:
            pass
    return 0, s


def _is_cx_url(u: str) -> bool:
    """这个地址是不是学习通自己的。

    🔴 消息正文是**老师发的、别人写的 HTML** —— 里面塞一个外部地址就是一条
       钓鱼链接。我们只负责把学习通自己的地址还给它：认不出来就不给按钮。
       少一颗按钮不会让人漏做，点错一次才会。改这里必须同步 native.js 的 cxIsCx。
    """
    m = re.match(r"(?i)^https?://([^/:?#]+)", str(u or ""))
    if not m:
        return False
    h = m.group(1).lower()
    return h == "chaoxing.com" or h.endswith(".chaoxing.com")


# 一条消息里那条任务自己的地址 —— 有就返回，没有就是空串。
# 用户 2026-11-14：「收件箱也有对应作业的链接，你能不能把这个对应作业和
# 随堂练习的跳转链接也放进收件箱对应消息」。
#
# 🔴 这个接口是纯逆向的，字段名各版本叫法不一。所以**只做加法**：挨个名字试，
#    正文里再兜一次 href / 裸地址；全认不出来就当没有。反过来（写死一个字段名）
#    在改版那天会让所有消息一起没按钮。
# 🔴 顺序：字段在前、正文在后。字段是学习通自己算出来的跳转，比老师手写的可靠。
NOTICE_LINK_KEYS = ("url", "linkUrl", "link", "jumpUrl", "jumpLink",
                    "targetUrl", "href")


def _notice_url(x: dict) -> str:
    if not isinstance(x, dict):
        return ""
    # ① attachment：真实接口里地址就在这里，是一段 JSON **字符串**
    #    （2026-11-14 真号抓 80 条验证：73 条作业/考试都带 att_web.url，
    #     形如 https://mooc1-api.chaoxing.com/examApi/intoexamorwork?…&enc=…）。
    #    🔴 只认子对象里的 `url`；att_web 还有个 `logo`（图片地址），
    #       拿「任意字符串」当地址会把课件图标当成作业页。
    raw = x.get("attachment")
    arr = None
    if isinstance(raw, str) and raw.strip():
        try:
            arr = json.loads(raw)
        except Exception:
            arr = None
    elif isinstance(raw, list):
        arr = raw
    if isinstance(arr, list):
        for a in arr:
            if not isinstance(a, dict):
                continue
            for k, sub in a.items():
                if not isinstance(sub, dict):
                    continue
                au = _abs_url(str(sub.get("url") or ""))
                if au and _is_cx_url(au):
                    return au
    # ② 顶层字段（真实接口里没有，留着兜别的通知形态）
    for k in NOTICE_LINK_KEYS:
        u = _abs_url(str(x.get(k) or ""))
        if u and _is_cx_url(u):
            return u
    c = str(x.get("content") or x.get("body") or "")
    if not c:
        return ""
    m = re.search(r"""(?i)href\s*=\s*["']([^"']+)["']""", c)
    if m:
        hu = _abs_url(m.group(1))
        if hu and _is_cx_url(hu):
            return hu
    # 有的通知把地址直接贴成文字（没有 <a>）。先去掉标签再找，
    # 否则 img 的 src、link 的 href 也会被当成「任务地址」。
    txt = re.sub(r"(?s)<[^>]*>", " ", c)
    for raw in re.findall(r"""(?i)(?:https?:)?//[^\s"'<>()]+""", txt):
        tu = _abs_url(raw)
        if tu and _is_cx_url(tu):
            return tu
    return ""


def parse_notice_page(j):
    """通知中心一页 → (条目列表, 下一页游标, 是否最后一页)。

    🔴 `isread` 是字符串 `'0'`（未读）/ `'1'`（已读）。裸真值判断会把
       未读的 `'0'` 当真值 —— 结果是「永远 0 条未读」。必须按字符串比。
    """
    if not isinstance(j, dict):
        return [], "", True
    body = j.get("notices")
    if not isinstance(body, dict):
        # 有的版本把 list 直接挂在根上；两种都认，认不出就当这一页是空的
        body = j if isinstance(j.get("list"), list) else {}
    rows = body.get("list")
    if not isinstance(rows, list):
        rows = []
    last = str(body.get("lastGetId") or "")
    done = bool(body.get("lastPage"))
    out = []
    for x in rows:
        if not isinstance(x, dict):
            continue
        at, when = _notice_time(
            x.get("completeTime") or x.get("createTime") or x.get("time"))
        title = _plain(x.get("title") or x.get("subject"), 80)
        out.append({
            "kind": "notice",
            "kindName": "消息",
            "id": str(x.get("id") or x.get("noticeId") or x.get("nid") or ""),
            "t": title or "（无标题）",
            "c": _plain(x.get("content") or x.get("body"), 400),
            "w": _plain(x.get("createrName") or x.get("senderName")
                        or x.get("createName") or x.get("sender"), 30),
            "at": at,
            "when": when,
            # 这条消息指向的任务（作业 / 随堂练习）。空串 = 这一条没有可去的页面，
            # 界面上就不长按钮 —— 不做「猜一个地址」这种事。
            "u": _notice_url(x),
            "r": str(x.get("isread", "1")) != "0",
        })
    return out, last, done


def is_open(item: dict) -> bool:
    """这条还值不值得摆到人面前。

    作业/考试：todo 才算（done 已经交过/考过，other 是没见过的状态 ——
    作业那边的 other 由调用方单独说明，见 scan()）。
    活动：live / pending 算，ended 不算。
    """
    return item.get("state") in ("todo", "live", "pending")


def sort_rank(item: dict, now: float = None) -> int:
    """待办列表的排序档位。越小越靠前。

        0  还能赶上的   —— 按剩余分钟升序
        1  没有截止时间 —— 垫底但不等于过期（实测真有「未交但没写截止」的作业）
        2  已经过期的   —— **绝不能排最前面**

    🔴 为什么需要这个函数：以前只按「剩余分钟」升序排，过期那条的 endAt 在过去、
       算出来是负数，负数最小 ⇒ 一年前的通知占着首屏第一行，真正要交的作业
       掉到第三屏。2026-10-23 南师侧在真机上撞到，我这边同一个毛病。
    🔴 前端 app.js 的 cxSortRank 是同一套口径，改这里必须同步改那边。
    """
    if item.get("leftMin") is None:
        return 1
    end = item.get("endAt")
    if end and end < (time.time() if now is None else now):
        return 2
    return 0


# ------------------------------------------------------- 随堂练习快查

LIVE_MAX = 4        # 最多查几门（一天十几门的排法会撞风控）
LIVE_TTL = 45       # 同一次打开里多久内不重复问


def live_check(cx, names, semester: str = "", limit: int = LIVE_MAX) -> dict:
    """只查「今天有课的那几门」的课堂活动 —— 给正在进行的随堂练习留一条快路。

    用户 2026-11-14：「很多随堂练习只有几分钟持续时间，当场刷新完全来不及」。

    全量扫描三十多门课、四十秒起步，等它回来这一节已经下课了；而随堂练习只可能
    出现在**今天正在上的那几门**里。拿教务课表算出今天有哪几门，只问这几门的
    活动列表，通常 1~3 个请求、两秒内回来。

    🔴 只发 activelist：它不需要 stuenc，是这条链上最便宜的那个请求。
    🔴 最多 `limit` 门：实训周那种「一天十几门」会变成十几个请求，那正是
       学习通风控认的形状 —— 宁可少查，不许把账号撞进冷静期。
    🔴 这不是一次扫描：不写缓存、不动扫描进度、不进 failed 名单。
       查到的只是「此刻正在进行的」，作业的完整列表仍以全量扫描为准。
    ⚠️ 与 web/native.js 的 cxLiveCheck 逐条对齐。
    """
    white = _norm_white(names)
    if not white:
        return {"at": 0, "items": [], "signs": [], "checked": 0}
    term = cur_term(semester)
    picked = [c for c in unique_courses(cx.courses())
              if is_active_course(c, white, term)][:limit]
    items: list[dict] = []
    signs: list[dict] = []
    for i, c in enumerate(picked):
        try:
            rows = cx.active_list(c)
        except CxError as e:
            # auth ⇒ 真的掉线了，交给调用方；其余（改版 / 网络 / 这门课没开）
            # 都只当这一门没读到，不影响另外几门。
            if e.kind == "auth":
                raise
            continue
        for r in rows:
            one = dict(r)
            one["course"] = c.get("title", "")
            (signs if one.get("kind") == "sign" else items).append(one)
        if i < len(picked) - 1:
            time.sleep(0.4)
    return {"at": int(time.time()), "items": items, "signs": signs,
            "checked": len(picked)}


# ------------------------------------------------------------------ 会话

class Chaoxing:
    """一个学习通会话。只读。"""

    def __init__(self, cookies=None) -> None:
        self.s = requests.Session()
        self.s.trust_env = False                   # 🔴 不吃环境变量里的代理
        self.s.headers["User-Agent"] = UA
        self.user = ""
        self.lock = threading.RLock()
        self._last_req = 0.0                       # _pace() 的水位（time.time）
        # 老版讨论（activeType=5）的详情地址要现问一次接口（见 discuss_url）。
        # 按 activeId 缓存：同一次扫描里同一场讨论只问一次；换个会话也还在。
        self._dcache: dict = {}
        if cookies:
            self.load_cookies(cookies)

    def _pace(self) -> None:
        """两个学习通请求之间至少隔 REQ_GAP 秒。

        🔴 0.44.1 新增。和 web/native.js 的 cxHttp 节流是同一件事，
           两边各写一份（APK 里没有 Python，网页版里没有 Java）。
           一门课 4 个请求原来连发，服务端看到的是「同一会话每秒问四次」，
           这是 202 的主要来源。
        """
        wait = REQ_GAP - (time.time() - self._last_req)
        if wait > 0:
            time.sleep(wait)
        self._last_req = time.time()

    # -- cookie 存取 -------------------------------------------------

    def load_cookies(self, rows) -> None:
        for r in rows or []:
            try:
                self.s.cookies.set(
                    str(r.get("name") or ""), str(r.get("value") or ""),
                    domain=str(r.get("domain") or ".chaoxing.com"),
                    path=str(r.get("path") or "/"))
            except Exception:
                continue                            # 单条坏了不影响别的

    def dump_cookies(self) -> list:
        with self.lock:
            return [{"name": c.name, "value": c.value,
                     "domain": c.domain, "path": c.path}
                    for c in self.s.cookies]

    def has_login(self) -> bool:
        """有没有登录态。看 `.chaoxing.com` 域下的 UID —— 那才是跨站有效的那份。"""
        with self.lock:
            for c in self.s.cookies:
                if c.name in ("UID", "_uid") and c.value:
                    return True
        return False

    # -- 登录 --------------------------------------------------------

    def login(self, user: str, pwd: str) -> dict:
        """账号密码登录。🔴 失败不重试，直接抛。"""
        with self.lock:
            try:
                self._pace()
                self.s.get(LOGIN_PAGE, timeout=15)
            except Exception as e:
                raise CxError("连不上学习通：%s" % str(e)[:60])

            data = {
                "fid": "-1",
                "uname": user,
                "password": aes_b64(pwd),
                "refer": "http%3A%2F%2Fi.mooc.chaoxing.com",
                "t": "true",                       # 🔴 只有 true 时前端才加密密码
                "forbidotherlogin": "0",
                "validate": "",                    # 首次登录不需要验证码
                "_blank": "1",
                "retainlogin": "2",
                "pcxxtpage": "0",
                "needVcode": "",
            }
            try:
                self._pace()
                r = self.s.post(LOGIN_API, data=data, timeout=20,
                                headers={"Referer": LOGIN_PAGE,
                                         "X-Requested-With": "XMLHttpRequest"})
            except Exception as e:
                raise CxError("登录请求失败：%s" % str(e)[:60])

            try:
                j = r.json()
            except Exception:
                raise CxError("登录返回的不是 JSON，接口可能变了", "parse")

            ok = bool(j.get("status")) or str(j.get("status")).lower() == "true"
            if not ok:
                mes = str(j.get("mes") or "").strip()
                # 服务端要验证码时 needVcode 会被置 1，这时只能人工处理
                if "验证码" in mes or j.get("needVcode"):
                    raise CxError("学习通要求验证码了，得手动登录一次", "auth")
                raise CxError(mes or "账号或密码不对", "auth")

            self.user = user
            return {"ok": True, "cookies": len(self.dump_cookies())}

    # -- 课程 --------------------------------------------------------

    def courses(self) -> list:
        with self.lock:
            try:
                self._pace()
                r = self.s.get(COURSE_API, timeout=20, headers={
                    "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"})
            except Exception as e:
                raise CxError("没取到课程列表：%s" % str(e)[:60])
        _status_ok(r, "课程列表")
        if not r.text.strip():
            raise CxError("课程列表是空的 —— 登录态可能失效了", "auth")
        # 登录页要单独认出来，别让它掉进下面「不是 JSON」那条报假话
        if _looks_logged_out(r.text):
            raise CxError("学习通的登录态过期了，点「重新连接」再试一次", "auth")
        try:
            j = r.json()
        except Exception:
            raise CxError("课程列表不是 JSON，接口可能变了", "parse")
        # 🔴 2026-11-12 补：没登录回的是合法 JSON（见 _json_logged_out），
        #    不判这条会报「结构变了」，归成 parse ⇒ 不停扫描、不提示重连。
        if _json_logged_out(j):
            raise CxError("学习通的登录态过期了，点「重新连接」再试一次", "auth")
        rows = parse_courses(j)
        if not rows and not j.get("channelList"):
            raise CxError("课程列表结构变了（没有 channelList）", "parse")
        return rows

    def session_alive(self):
        """会话到底还在不在：True 活着 / False 确实掉了 / None 问不出来。

        🔴 2026-11-15（用户第七次说「明明已经正常登录学习通了，还是显示登录
           过期」）：这是一道**证伪**，不是又一次调阈值。

           `auth_stop` 的每一条证据全是**字符串特征**：页面上出现「用户登录」、
           出现 login、返回不是 JSON、或者 401。学习通的维护页 / 云防护页 /
           归档课的空壳页都可能同时骗过连续三门课 —— 阈值调到几都是在猜。
           反过来，会话真掉了的时候，最便宜的那个认证接口（课程列表）一定
           也回登录页。所以「连续三门像登录页」只是**怀疑**，抛之前必须拿
           一个独立请求去证实；证实不了就不许对用户说「你的登录过期了」。

        ⚠️ 只在怀疑之后发这一个请求，正常扫描一次都不多花。
        ⚠️ 必须和 native.js 的 `cxSessionAlive()` 逐条对齐（对拍会验）。
        """
        try:
            self.courses()
            return True
        except CxError as e:
            if e.kind == "auth":
                return False
            return None

    # -- 收件箱（通知中心）------------------------------------------

    def notices(self, limit: int = 20, pages: int = 2) -> list:
        """学习通「收件箱」—— 网页端右上角那个通知中心。

        🔴 这是全项目唯一一个**纯逆向**的接口（见 NOTICE_LIST 那段注释）。
           所以规矩和别处相反：别的接口「读不到就报错」，这里「读不到就少几条」。
           一条脏数据不该让整个收件箱变红，那会把一条真消息也说成没有。

        🔴 只有「登录态失效」必须抛 —— 那是用户唯一能自己解决的那种。
           平台改版（不是 JSON / 结构变了）/ 网络抖动：返回已经拿到的部分，
           前端照常显示，不会因此挡住作业和考试。

        为什么只翻 2 页：一页约 20 条，2 页就是「最近一个月」。再往下翻
        是去年的通知，对「有没有新消息」没有任何帮助，只是白打服务器。

        为什么不做关键词搜索：服务端的 kw 参数实测无效（传了照旧全量返回），
        客户端又只有这两页 —— 在这种范围里搜出来的「没有」是骗人的。
        """
        out, seen, last = [], set(), ""
        for _ in range(max(1, min(pages, 5))):
            form = {
                "type": 0, "notice_type": 0, "lastValue": last, "sort": 0,
                "folderUUID": "", "kw": "", "startTime": "", "endTime": "",
                "gKw": "", "gName": "", "year": "", "tag": "", "fidsCode": "",
                "filterSenderPuids": "", "filterTags": "",
            }
            with self.lock:
                self._pace()
                try:
                    r = self.s.post(NOTICE_LIST, data=form, timeout=20, headers={
                        "Referer": "https://notice.chaoxing.com/pc/notice/index",
                        "X-Requested-With": "XMLHttpRequest"})
                except Exception as e:
                    raise CxError("连不上学习通：%s" % str(e)[:60])
            _status_ok(r, "收件箱")
            txt = (r.text or "").strip()
            # 没登录时这里会 302 到登录页，跟随后是一段 HTML —— 先认出它，
            # 否则会掉进「不是 JSON」那条报假话（说接口变了，其实是掉登录）。
            if _looks_logged_out(txt):
                raise CxError("学习通的登录态过期了，点「重新连接」再试一次", "auth")
            if not txt:
                # 空 body 和「登录态失效」是同一种表现（见完美校园那条教训），
                # 这里按 auth 报，让用户有机会自己重连。
                raise CxError("收件箱返回是空的 —— 登录态可能失效了", "auth")
            try:
                j = r.json()
            except Exception:
                raise CxError("收件箱不是 JSON，接口可能变了", "parse")
            rows, nxt, done = parse_notice_page(j)
            for it in rows:
                key = it.get("id") or (it.get("t") + "|" + it.get("when"))
                if key in seen:
                    continue
                seen.add(key)
                out.append(it)
            last = nxt
            if done or not last or len(out) >= limit:
                break
        return out[:limit]

    # -- 单门课 ------------------------------------------------------

    def course_meta(self, c: dict) -> dict:
        """进课程页，拿 stuenc + workEnc。两个必须来自同一次加载。"""
        url = COURSE_PAGE % (c["courseId"], c["classId"], c["cpi"])
        with self.lock:
            try:
                self._pace()
                r = self.s.get(url, timeout=25, allow_redirects=True)
            except Exception as e:
                raise CxError("进课程页失败：%s" % str(e)[:50])
        _status_ok(r, "课程页")
        stu = re.search(r"[?&]enc=([0-9a-fA-F]{32})", r.url)
        work = re.search(r'id="workEnc"[^>]*value="([0-9a-fA-F]{32})"', r.text)
        return {
            "stuenc": stu.group(1) if stu else "",
            "workEnc": work.group(1) if work else "",
            "referer": r.url,
        }

    def work_list(self, c: dict, meta: dict) -> list:
        if not meta.get("stuenc") or not meta.get("workEnc"):
            # 🔴 kind 用 "noentry" 而不是 "deny"：这两种在界面上是**完全不同**的话。
            #    deny = 被拒绝（可能账号有问题），noentry = 这门课的页面里压根没这个
            #    入口（多半已结课归档，或者老师换了新版课程页）。混成一种说法，
            #    用户就只能看到「拿不到入口」这种他没法处理的字。
            raise CxError("没给作业入口", "noentry")
        url = WORK_LIST % (c["courseId"], c["classId"], c["cpi"],
                           int(time.time() * 1000),
                           meta["stuenc"], meta["workEnc"])
        with self.lock:
            try:
                self._pace()
                r = self.s.get(url, timeout=25, headers={"Referer": meta["referer"]})
            except Exception as e:
                raise CxError("取作业列表失败：%s" % str(e)[:50])
        _status_ok(r, "作业列表")
        if "无权限的操作" in r.text or "无效的用户" in r.text:
            raise CxError("作业列表被拒（enc 不配对或登录态失效）", "deny")
        return parse_work(r.text)

    def active_list(self, c: dict) -> list:
        """一门课的活动列表（签到 / 主题讨论 / 通知 / 作业提醒）。

        🔴 这个接口**不需要 stuenc**，所以不用先进课程页 —— 省一次请求。
           它要的是学校 id（course.schoolId），不是课程页里那个 id="fid"。
        """
        if not c.get("schoolId"):
            raise CxError("这门课没带学校 id，活动查不了", "parse")
        url = ACTIVE_LIST % (c["schoolId"], c["courseId"], c["classId"],
                             int(time.time() * 1000))
        with self.lock:
            try:
                self._pace()
                r = self.s.get(url, timeout=20, headers={
                    "Referer": COURSE_PAGE % (c["courseId"], c["classId"], c["cpi"]),
                    "X-Requested-With": "XMLHttpRequest",
                })
            except Exception as e:
                raise CxError("取活动列表失败：%s" % str(e)[:50])
        _status_ok(r, "活动列表")
        # 没登录时会 302 到登录页，回来的是一大坨 HTML 而不是 JSON
        if not r.text.lstrip().startswith(("{", "[")):
            # 🔴 2026-10-23 改：非 JSON **不再一律判 auth**。
            #    原来只要不是 JSON 就说「登录态可能失效了」，可 `auth` 会让
            #    `scan()` 直接 break —— 于是一个维护页 / 接口改版，就能让
            #    后面所有课**一门都不查**，而用户看到的是「登录失效」。
            #    现在只有真的看到登录页特征才判 auth，其余判 parse（照常往下查）。
            head = r.text[:4000]
            if "用户登录" in head or "passport2" in head or "login" in head.lower():
                raise CxError("活动列表跳到登录页了（登录态失效）", "auth")
            raise CxError("活动列表返回的不是 JSON（接口可能变了）", "parse")
        try:
            j = r.json()
        except Exception:
            raise CxError("活动列表不是 JSON，接口可能变了", "parse")
        # 详情页地址要 courseId / classId / fid，而接口**不回**这三个
        # （它本来就是按课查的），所以把课程对象带进去自己拼。
        items = parse_active(j, course=c)
        # 老版讨论（activeType=5）拼不出静态地址 —— 学习通自己点它时也是先问一次
        # 接口。不补这一步，那些「讨论」在界面上是**点不开的死行**（用户
        # 2026-11-16 报的正是这个：学习页里讨论点不动）。
        for it in items:
            if it.get("kind") == "topic" and not it.get("url") and it.get("aid"):
                it["url"] = self.discuss_url(c, it["aid"])
        return items

    def discuss_url(self, c: dict, aid: str) -> str:
        """老版讨论（activeType=5）→ 详情页地址；问不出来就返回空串。

        地址不是猜的：学习通活动列表页点讨论走的是 `openDiscuss(activeId,
        topicId)`（`_lab/cx/ml/active_page.html`，190 KB 真实抓取）——
        先 `GET /v2/apis/discuss/getTopicDiscussInfo?activeId=` 拿
        uuid / startTime / timeLong / activeStatus / groupwebDomain，
        再按 `buildStuDiscussUrl()` 的非 V3 那一支拼：

            <groupwebDomain>/course/topicDiscuss/info?uuid=…&activeId=…
                &startTime=…&timeLong=…&activeStatus=…&courseId=…

        🔴 只做**非 V3** 那一支：V3（`buildTopicV3DiscussUrl`）还要先查 bbsid，
           多一次请求而且只在部分学校生效，我们这边用不上。
        🔴 一次扫描最多问 8 条：讨论一学期十几场，每条一次请求会把请求次数
           顶上去（风控按次数认，见 CX_GAP）。超了就维持「没有地址」，
           界面照旧显示那一行，只是点不开 —— 比整轮扫描被 202 强。
        """
        aid = str(aid or "").strip()
        if not aid:
            return ""
        if aid in self._dcache:
            return self._dcache[aid]
        if len(self._dcache) >= 8:
            return ""
        self._dcache[aid] = ""
        url = ""
        try:
            with self.lock:
                self._pace()
                r = self.s.get(DISCUSS_INFO, params={"activeId": aid}, timeout=20,
                               headers={"Referer": "https://mooc1.chaoxing.com/",
                                        "X-Requested-With": "XMLHttpRequest"})
            j = r.json() if r.status_code == 200 else {}
            if str(j.get("result")) in ("1", "True", "true"):
                url = discuss_page_url(j.get("data") or {},
                                       str((c or {}).get("courseId") or ""), aid)
        except Exception:      # noqa: BLE001
            url = ""           # 问不到就是没有地址，别把这门课整条活动列表拖红
        self._dcache[aid] = url
        return url

    def exam_list(self, c: dict, meta: dict) -> list:
        """一门课的考试列表。需要 stuenc，所以必须在 course_meta 之后调。"""
        if not meta.get("stuenc"):
            raise CxError("没给考试入口", "noentry")
        url = EXAM_LIST % (c["courseId"], c["classId"], c["cpi"],
                           int(time.time() * 1000), meta["stuenc"])
        with self.lock:
            try:
                self._pace()
                r = self.s.get(url, timeout=25, headers={"Referer": meta["referer"]})
            except Exception as e:
                raise CxError("取考试列表失败：%s" % str(e)[:50])
        _status_ok(r, "考试列表")
        if "参数传递不正确" in r.text:
            raise CxError("考试页说参数不对（接口拼法可能变了）", "parse")
        if "无权限的操作" in r.text or "无效的用户" in r.text:
            raise CxError("考试列表被拒（登录态失效）", "deny")
        if "用户登录" in r.text[:3000]:
            raise CxError("考试页跳到登录了（登录态失效）", "auth")
        return parse_exam(r.text)

    # -- 全量扫描 ----------------------------------------------------

    def scan(self, on_progress=None, gap: float = SCAN_GAP,
             prev_rest=None, force: bool = False,
             pre_courses=None, active_names=None, semester: str = "",
             skip_keys=None) -> dict:
        """把每门课的**作业 + 考试 + 课堂活动**都读一遍。

        on_progress(已完成门数, 总门数, 当前课名, 阶段名) —— 给前端显示进度用。
        🔴 阶段名是第 4 个参数，老调用方只传 3 个也能跑（多传的那个忽略掉）。

        prev_rest
            上一轮返回的 `rest`（每门课的结课判定状态）。不传就等于「第一次扫」，
            所有课都扫。判定规则见 REST_STREAK / REST_SPAN / REST_GAP / COLD_DAY。

        force
            True = 无视「已结课」，全部重扫一遍。给用户手动点「全部重扫」用。
            🔴 判据一定会错，所以必须留一个「我不信你，全查一遍」的出口。
               没有这个出口的话，判错一次用户就只能卸载重装。

        为什么一次全查、不分模块点：
          用户要的是「按紧急度排」—— 作业和考试混在一起才谈得上谁最急。
          分模块查的话，前端就没法给出一个统一的「最急的是这个」。
          代价是每门课 4 个请求（课程页 + 作业 + 考试 + 活动），32 门课约 1 分钟。

        pre_courses
            `/api/cx/scan` 在「cookie 里没 UID」时先探一次课程列表，探到的结果
            从这里传进来，省一次请求（风控认的就是请求次数）。
            和 `web/native.js` 的 `cxScanAll(prevRest, force, preCourses)` 对齐。

        active_names
            本学期**教务课表里在修**的课。每项可以是纯课名（老格式），
            也可以是 `{"name": …, "teacher": …}` —— 0.47.0 起前端传后者。
            命中的课每轮全扫；没命中的判结课，只花 1 个请求看签到。
            教务课表是「这门课现在真的在上」的唯一权威来源。
            🔴 传空 / 没传（没绑教务、课表没拿到）⇒ **不降级成只查签到**，
               退回 0.44 的老口径「内容够旧才少扫」，其余照旧全扫。
               没有权威名单时少查一门就可能漏一门作业。

        semester
            教务给的当前学期（「2026-2027 学年第 1 学期」）。只用来判
            「学习通这个班是不是别的学期开的」。认不出就退回不用这条，
            绝不当成「不是本学期」。
        """
        courses = (pre_courses
                   if isinstance(pre_courses, list) and pre_courses
                   else self.courses())

        # 🔴 0.39.0：按 (courseId, classId) 去重，不再只按 courseId。
        #    以前那句「不同 classId 的作业/考试/活动都是同一套」是错的 ——
        #    同一个 courseId 下面可能挂着**不同学期**的教学班，压成一个
        #    就只查了其中一个班，顺序一换本学期的作业一条都不在了。
        #    详见 unique_courses() 的注释。
        uniq = unique_courses(courses)

        items, failed = [], []
        # 🔴 2026-11-23：用户屏蔽的课（往期为主），一个请求都不发。
        #    往期课每轮都要探一遍签到，课多的人一轮几十个请求 ——
        #    这是「学习通同步很慢」的主要来源。按 (courseId, classId) 记，
        #    和 rest 的 key 同口径（rest_key），同一 courseId 下不同学期的班互不影响。
        skip_set = {str(k) for k in (skip_keys or []) if k}
        skip_n = len([c for c in uniq
                      if rest_key(c["courseId"], c.get("classId")) in skip_set])
        n_all = len(uniq) - skip_n   # 学习通里这一轮**要查**的教学班数（已扣掉屏蔽）
        now = int(time.time())
        prev_rest = prev_rest if isinstance(prev_rest, dict) else {}
        # 先整体继承上一轮的状态，再逐门覆盖扫过的课。
        # 🔴 不能只写「这次扫过的课」：中途登录态失效 break 之后，剩下的课没扫，
        #    状态要是丢了，它们好不容易攒的 streak 就白攒了 —— 等于每次从零数。
        # ⚠️ 只继承新格式的 key（带 `:`）。0.38 及以前按裸 courseId 存，
        #    那个口径本身就是错的（一个 courseId 压了多个学期的班），
        #    丢掉重攒一次比继承下来更安全。
        rest = {k: v for k, v in prev_rest.items()
                if isinstance(v, dict) and ":" in str(k)}
        paused = []          # 这次因为「已结课」而没扫的课
        all_courses = []     # 全部教学班 + 这一轮怎么处置的（给「屏蔽课程」表用）
        white = _norm_white(active_names)
        term = cur_term(semester)
        # 🔴 0.47.2：进度条的分母也按**这学期**算。
        #    用户 2026-11-06：「仍然是类似于 12/37 这样，后面的总数也改成
        #    当前学期的」。以前 n = 全部教学班（37），哪怕实际只细查 17 门，
        #    界面上仍写「第 12 / 37 门课」—— 看着就像范围没收窄。
        #    有课表 ⇒ 分母 = 这学期在修的课数，分子只数全扫过的课；
        #    没有课表 ⇒ 分母照旧是全部（那时确实一门一门全扫）。
        scope = "term" if white else "all"
        n = (sum(1 for c in uniq if is_active_course(c, white, term)
                 and rest_key(c["courseId"], c.get("classId")) not in skip_set)
             if white else n_all)
        if n <= 0:                 # 课表对不上任何一门（换学期/课名全改了）
            n, scope = n_all, "all"
        # 全被屏蔽时留 1，免得界面上出现「第 0 / 0 门」这种除法出来的怪数
        if n < 1:
            n = 1
        # 🔴 2026-11-09：**手动「重新检查」时，分母回到全部**。
        #    这一轮 force 会把结课的课也全扫（见下面 tier 那段的改动），
        #    真实要查的就是 n_all 门。分母还按课表算的话，进度会走到
        #    「第 30 / 17 门」—— 一个看着就错的数。
        #    这不是把 0.47.2 改回去：自动扫（进页面那条路）仍然按课表收范围。
        if white and force:
            n, scope = n_all, "all"
        done = 0             # 进度分子：只数「全扫」的课，探签到的不算
        # 0.47.0：全扫了几门 / 只看了签到几门。
        deep_n = act_n = 0

        def tick(title, phase=""):
            """报一帧进度。

            `_progress` 拿到的是**从 0 开始的序号**（它自己 +1 再给前端），
            所以这里传 `done - 1`。done 是「已查完几门」的计数，
            传 done 进去的话第一门就写成「第 2 / 1 门」—— 看着像坏了。
            done 还是 0（正在探结课那批）时按 1 显示，别出现「第 0 / 17 门」。
            """
            _progress(on_progress, max(done - 1, 0), n, title, phase)
        # 0.44.0：这一轮撞上「查得太频繁」就整轮停手（见 _status_ok 的 202）。
        busy_out = False
        # 见 hit_auth()：连续几门 auth 才认「会话没了」。
        auth_n = 0
        auth_stop = False
        for i, c in enumerate(uniq):
            cid, title = c["courseId"], c["title"]
            clid = c.get("classId")
            key = rest_key(cid, clid)

            # ---- 这门课要全扫，还是只看一眼签到 ----
            # 🔴 0.47.0：判据从「三个列表全空攒 3 次 / 最新内容 90 天前」换成
            #    **教务系统里本学期在修的课表**。
            #    老判据对历史学期的课判不动 —— 它们不空（上学期的作业还挂着），
            #    于是 37 门课一个学期都判不出结课，每轮全量扫。
            #    用户 2026-11-06：「我这个学期只有十几个课程」。
            #
            #   deep = 全扫（课程页 + 作业 + 考试 + 活动，4 个请求）
            #   act  = 判它结课了，只查活动列表（1 个请求）—— 签到在里面
            #
            # 🔴 没有教务课表时（没绑 / 没同步）**不降级成 act**，退回 0.44 的
            #    老口径「内容够旧才少扫」，其余照旧全扫。没有权威名单的时候少查
            #    一门就可能漏一门作业，那比慢几十秒严重得多。
            # 🔴 用户屏蔽的课：任何请求都不发，直接跳。
            if key in skip_set:
                all_courses.append({"course": title, "courseId": cid,
                                    "classId": clid, "tier": "skip"})
                tick(title, "已屏蔽，跳过")
                continue

            st = prev_rest.get(key) or {}
            if white:
                # 🔴 2026-11-09：force 时**结课的课也要全扫**。
                #    0.47.2 那会儿是「课表优先于 force」，理由是用户点完
                #    看到「共 37 门课」以为范围没收窄。但那条规则有个更坏的
                #    副作用，是 2026-11-09 用户报出来的：
                #        「做完作业后点重新检查，还是显示没有完成」
                #    act 档**只探签到，一个作业请求都不发** —— 课表一判错
                #    （课名对不上、学期串了），那门课的作业状态就永远停在
                #    上一轮，点一百次「重新检查」也不会变。
                #    用户手点这一下要的就是「现在给我重问一遍」，所以 force
                #    一律 deep；自动扫（进页面那条路）仍然按课表收范围，
                #    0.47.2 真正想省的那 90 秒一秒没少。
                tier = ("deep" if (force or is_active_course(c, white, term))
                        else "act")
            elif force:
                tier = "deep"
            else:
                tier = "act" if st.get("cold") is True else "deep"

            # 结课的课也不是每轮都探。探过一次就隔 ACT_PROBE_GAP 再来，
            # 期间连那 1 个请求都不发。
            # 🔴 认的是 `st["act"]` 这个标记（= 已经按新办法探过一次），
            #    不是 lastAt：从老版本升上来时 rest 里没有这个键，
            #    照着 lastAt 直接跳过的话，那批课 7 天内一次签到也看不到。
            if tier == "act" and st.get("act") is True and not force \
                    and now - int(st.get("lastAt") or 0) < ACT_PROBE_GAP:
                # 🔴 why 是给界面选句子用的，不是给人看的字：
                #    term = 教务课表里这学期没有这门课（有依据的「结课」）
                #    cold = 没有课表，只能靠「内容很旧」判（说得软一点）
                paused.append({"course": title, "courseId": cid,
                               "classId": clid,
                               "lastAt": int(st.get("lastAt") or 0),
                               "why": "term" if white else "cold"})
                all_courses.append({"course": title, "courseId": cid,
                                    "classId": clid, "tier": "act"})
                # 进度照报，但**分子不动**：这学期要查的课已经查完了，
                # 这里只是顺手看一眼结课那批的签到。分母是 17，分子就停在 17，
                # 别让它跑到「第 37 / 17 门」这种看着就错的地方去。
                tick(title, "已结课，跳过")
                continue

            all_courses.append({"course": title, "courseId": cid,
                                "classId": clid, "tier": tier})
            mine = []            # 这门课这一轮读到的条目，用来算「最新内容时间」

            def take(rows):
                for r in rows:
                    r["course"] = title
                    r["courseId"] = cid
                    items.append(r)
                    mine.append(r)


            def note(why, part="other", code=""):
                """记一条「这门课的某一块没读到」。

                🔴 part / code 是给**界面**用的分类键，不是给人看的字：
                     part = work / exam / active / course / auth
                     code = "noentry"（页面里根本没这个入口）或 ""（别的原因）
                   界面拿它们把「20 门课各缺作业 + 各缺考试」合成一句人话。
                   把 `why` 直接甩给用户，他只会看到「拿不到入口」这种
                   既不知道是什么、也不知道该干什么的词。
                """
                failed.append({"course": title, "why": str(why)[:60],
                               "part": part, "code": str(code or "")})

            def hit_auth(why="登录态失效，没查"):
                """登录态失效要跳出循环时，把**后面没查的课**都记进 failed。

                🔴 2026-10-23 补。不记的话那些课在界面上**凭空消失** ——
                   用户看到的是「N 门课都查完了，其中几门没读到作业」，
                   实际只查了前一两门。这比「整场崩掉」更难查：
                   崩了至少知道出事了。
                   （实测见 `_lab/cx/probe_502.py`：两门课全 502 时，
                   修之前第二门课一个字都没留下。）
                """
                nonlocal auth_n, auth_stop
                auth_n += 1
                # 🔴 0.52.3 v6（用户 2026-11-14：「明明登录着学习通，上面还是
                #    显示登录过期」）：以前**任何一处** auth 都当场把剩下的课
                #    全标成「登录态失效」—— 一门归档课的活动列表跳了登录页，
                #    整页就变成「登录态过期了」+ 三十门没查，而会话其实好好的。
                #    会话还在不在，只有「连续几门都 auth」才说得准：真掉线时
                #    课程列表那一步就已经失败了，走不到这里。
                #    所以个别课 auth 只记成这一门没查到，连续 AUTH_STREAK 门才认。
                if auth_n < AUTH_STREAK:
                    return False
                for rest in uniq[i + 1:]:
                    failed.append({"course": rest["title"], "why": why,
                                   "part": "auth", "code": "auth"})
                auth_stop = True
                return True

            # ---- 结课的课：只看一眼签到 ----
            # 作业和考试**一个请求都不发** —— 教务课表里没有这门课，判它结课了。
            # 唯一还可能变的是签到：它是个几分钟的窗口，而且「点名专用」这类课
            # 教务课表里根本没有、唯一的内容就是签到，所以必须留这一眼。
            if tier == "act":
                warm = False
                try:
                    rows = self.active_list(c)
                    take(rows)
                    newest_act = None
                    for r in rows:
                        t2 = r.get("startAt") or r.get("endAt")
                        if t2 and (newest_act is None or t2 > newest_act):
                            newest_act = t2
                    warm = bool(newest_act) and (now - newest_act) < WARM_ACT
                except CxError as e:
                    note(str(e), "active", e.kind)
                    if e.kind == "auth":
                        if hit_auth():
                            break
                        continue
                    if e.busy:
                        busy_out = True
                        break

                # 探到「最近还有活动」⇒ 这门课其实还在动，下一轮恢复全扫。
                # 不清 resting 的话，它会被自己的结课结论永远锁在 act 档 ——
                # 老师真发了东西，用户却再也看不到。
                if warm:
                    rest[key] = {"streak": 0, "since": now, "lastAt": now,
                                 "resting": False, "title": title,
                                 "cold": False, "act": True}
                else:
                    nxt = dict(st)
                    nxt.update({"lastAt": now, "title": title, "act": True})
                    # cold 只在「真的读到过内容且都旧」时才算证据。
                    # 活动列表一条都没有 ≠ 内容旧 —— 那是「没动静」，
                    # 和作业页「没读到」是两回事，别混成一种判断。
                    if "cold" not in nxt:
                        nxt["cold"] = True
                    rest[key] = nxt
                act_n += 1
                tick(title, "已结课，只看了签到")
                if i < n_all - 1:
                    time.sleep(gap + random.random() * SCAN_JITTER)
                continue

            meta = None
            # 这一轮三个模块各自「读到了没有、读到几条」。
            # 🔴 只有三个都 ok 才谈得上「这门课是空的」—— 少一个都不算，
            #    因为没读到的那块可能正好装着作业。
            w_ok = e_ok = a_ok = False
            w_n = e_n = a_n = 0

            try:
                meta = self.course_meta(c)
            except CxError as e:
                if e.kind == "auth":
                    note(e, "course", e.kind)
                    if hit_auth():
                        break
                    # 只这一门打不开（归档课常见）：跳过它，接着看下一门。
                    if i < n_all - 1:
                        time.sleep(gap + random.random() * SCAN_JITTER)
                    continue
                # 被风控拦下：第一门课就停，不再挨个去撞同一堵墙。
                if e.busy:
                    busy_out = True
                    break
                note(e, "course", e.kind)

            # 三个模块各查各的：一个挂了不能连累另外两个。
            # 🔴 每个失败都要记进 failed —— 否则「没读到」会被当成「没有」。
            if meta is not None:
                try:
                    rows = self.work_list(c, meta)
                    w_n, w_ok = len(rows), True
                    take(rows)
                except CxError as e:
                    note(str(e), "work", e.kind)
                    if e.kind == "auth":
                        if hit_auth():
                            break
                        continue
                    if e.busy:
                        busy_out = True
                        break

                try:
                    rows = self.exam_list(c, meta)
                    e_n, e_ok = len(rows), True
                    take(rows)
                except CxError as e:
                    note(str(e), "exam", e.kind)
                    if e.kind == "auth":
                        if hit_auth():
                            break
                        continue
                    if e.busy:
                        busy_out = True
                        break
            else:
                # 课程页没打开 ⇒ 作业和考试也查不了（两个都要课程页里的 stuenc）。
                # 🔴 必须**分别**记下来。只写一句「进不了课程页」的话，界面上
                #    「作业」和「考试」两项就静默消失了，用户会以为这门课没有。
                #    course_meta 那条错上面已经记过，这里记的是「因此没查」的部分。
                # code=nocourse 是给界面看的：这两条和 course 那条是**同一件事**，
                # 界面上要并成一句，不能数成「17 门 + 34 门」。
                note("课程页没打开，没查", "work", "nocourse")
                note("课程页没打开，没查", "exam", "nocourse")

            # 活动列表不需要 stuenc，课程页挂了也能查
            try:
                rows = self.active_list(c)
                a_n, a_ok = len(rows), True
                take(rows)
            except CxError as e:
                note(str(e), "active", e.kind)
                if e.kind == "auth":
                    if hit_auth():
                        break
                    continue
                if e.busy:
                    busy_out = True
                    break

            # ---- 结课判定 ----
            # 🔴 只有「课程页打开 + 作业/考试/活动三个列表全部成功返回」才算一次
            #    有效的观察。失败、noentry、课程页没打开**都不算空** —— 那是
            #    「没读到」。拿没读到当空，会把被限流的课判成结课，然后它
            #    再也不会被扫，用户永远不知道老师发了作业。
            #
            # 判定本身在 rest_next() 里，那是纯函数 —— 和 native.js 的
            # cxRestNext 逐条对拍。只改一边不报错，只是两边结论会不一样。
            #
            # newest = 这门课最新一条内容的时间。0.39.0 起它是「冷课」的证据：
            #   历史学期的课**不是空的**（上学期的作业还挂着），只按「全空」
            #   永远判不出结课，于是每次刷新都全量扫 —— 用户的原话是
            #   「每次都需要全量扫描」。改成看「最新一条有多旧」。
            #   只认 endAt / startAt，两者都没有就是 None（= 不知道多旧，不判冷）。
            if meta is not None and w_ok and e_ok and a_ok:
                newest = None
                for r in mine:
                    t = r.get("endAt") or r.get("startAt")
                    if t and (newest is None or t > newest):
                        newest = t
                nxt = rest_next(st, True, w_n + e_n + a_n == 0, now, newest)
                nxt["title"] = title
                # 0.44.0：单独记一份「够冷」的证据，给「没有教务课表」那条路用。
                # 不放进 rest_next() 里，是因为那个函数要和 native.js 对拍，
                # 动它的返回值就得同时动两边；这个字段只是扫描器的备注。
                nxt["cold"] = bool(newest) and (now - newest) >= COLD_NO_WHITE
                rest[key] = nxt
            # 没读到 ⇒ rest 里已经继承了上一轮的原值，什么都不用做。

            deep_n += 1
            done += 1
            tick(title, "作业 · 考试 · 课堂活动")
            if i < n_all - 1:
                time.sleep(gap + random.random() * SCAN_JITTER)

        # 🔴 0.44.0：撞上风控就整轮作废，**不返回半份结果**。
        #    以前继续往下扫，每门课各记一条「查得太频繁」—— 于是
        #    「查到第 4 门被拦」在界面上变成「31 门课没查到」，用户以为
        #    31 门都查过了、都坏了，实际上剩下 27 门一个请求都没发。
        #    抛出去之后上层不落盘，界面上留着上次那份完整数据 + 一句
        #    「这次没查成：学习通查得太频繁了，过几分钟再试」—— 这是事实。
        if busy_out:
            raise CxError("查得太频繁，等 15 分钟再试",
                      "busy", busy=True)

        # 🔴 0.53.0 v4（2026-11-15）：怀疑不等于结论 —— 先拿课程列表证一次。
        #    · 探针说「活着」⇒ 刚才那些是误判（维护页 / 防护页 / 归档课壳页），
        #      把它们从「登录失效」降级成「这门没读到」，照常返回这份数据；
        #    · 探针说「掉了」⇒ 真过期，照旧抛，界面给重连按钮；
        #    · 探针自己也问不出来（断网 / 被拦）⇒ 不能说人家过期，抛探针那个
        #      错误 —— 是什么就说什么，用户才能做对的事。
        #    ⚠️ 和 native.js 的 cxScanAll 末尾逐条对齐（对拍会验）。
        if auth_stop:
            alive = self.session_alive()
            if alive is False:
                raise CxError("学习通的登录态过期了，点「重新连接」再试一次", "auth")
            if alive is None:
                raise CxError("这次没查成：连不上学习通", "net")
            # 活着 ⇒ 降级。part 换成 lookauth：这些课**确实没扫到**，要照实
            # 算进「没查到」，只是不能再叫「登录失效」—— 账号是好的。
            failed = downgrade_auth(failed)
            auth_stop = False

        # ---- 归类 ----
        # 签到单独留一份**完整**的（含已结束）。
        # 为什么不能只留正在进行的：用户问的是「我漏了哪几次」——
        # 那只有看历史才知道。只留 live 的话，「查看签到」等于什么都没查。
        # 🔴 仍然只读：这里只是把读到的排出来，不代签、不点任何按钮。
        signs = [x for x in items if x.get("kind") == "sign"]
        # 新的排前面。没有 startAt 的垫底，别当 0 —— 那会排到最后变成「最老」。
        signs.sort(key=lambda x: -(x.get("startAt") or 0))

        # 作业里状态认不出来的（比如「已互评」）不往任何一类里凑，单独说清楚。
        odd = [x for x in items if x["kind"] == "work" and x["state"] == "other"]
        odd_ids = set(id(x) for x in odd)       # 按对象身份比，别按值比（慢且会误伤）
        # 🔴 0.47.0：**早就结束的**不再列出来。
        #    用户 2026-11-06：「已经结束一段时间了的就别显示了，除非是需要互评的作业」。
        #    判据只认「有截止时间且过去 7 天以上」，见 is_stale() 的三条红线。
        #    这类和「已交 / 已结束」不同：那些是**做完了**，这些是**没做也没戏了**，
        #    一直挂在待办里只会把真正要交的挤下去 —— 截图里 31 条里大半是这种。
        stale = [x for x in items if is_stale(x, now)]
        stale_ids = set(id(x) for x in stale)
        # 🔴 签到不进「待办」列表 —— 它在上面那张签到卡里，单独一个位置。
        #    为什么分开：签到的时效是「几分钟」，跟「还剩 3 天的作业」排在一起
        #    会被挤到下面去；而且同一条会同时出现在卡里和列表里，像重复了。
        open_items = [x for x in items if is_open(x) and id(x) not in odd_ids
                      and id(x) not in stale_ids and x.get("kind") != "sign"]

        # 🔴 2026-11-09：把「已经做完的」也带出去，单独一批。
        #    用户原话：「在每个作业和随堂练习旁边都加上已完成和未完成」。
        #    待办列表天然只会有没做完的（is_open 只放 todo/live/pending），
        #    所以不单独给这一批的话，界面上永远只能看到「未完成」——
        #    那条「做完了还显示没完成」的反馈也就永远没法自证。
        #    ⚠️ 不进 items：items 的条数就是「还要做几条」，混进 done 会把
        #       顶部那个大数字和分类药丸的计数全带偏。前端单独折叠一段。
        done_items = done_items_of(items, now)

        # 🔴 三段排序，不是两段：
        #      rank 0  还能赶上的   —— 按剩余分钟升序
        #      rank 1  没有截止时间 —— 按标题（垫底 ≠ 已过期：实测真有「未交但没写截止」的）
        #      rank 2  已经过期的   —— 按标题，**绝不能排最前面**
        #    以前只有「有/没有」两段：过期那条的 endAt 在过去，算出来是负数，
        #    负数最小 ⇒ 一年前的通知排在首屏第一行，真正要交的作业掉到第三屏。
        #    （2026-10-23 南师侧实测踩到，我这边同一个毛病。）
        open_items.sort(key=lambda x: (sort_rank(x),
                                       x["leftMin"] if sort_rank(x) == 0 else 0,
                                       x.get("title") or ""))

        counts = {k: 0 for k in KIND_ORDER}
        for x in open_items:
            k = x.get("kind") or "other"
            counts[k] = counts.get(k, 0) + 1

        done_n = len([x for x in items if x.get("state") == "done"])
        ended_n = len([x for x in items if x.get("state") == "ended"])

        return {
            "ok": True,
            "at": int(time.time()),
            # 🔴 0.47.2：这里给的是**分母那个数**（有课表时就是这学期的门数），
            #    不是学习通里一共有多少教学班。界面靠它说「本学期 N 门」，
            #    给全部的话又会变成「几十门里查了十几门」，正是这次要修掉的。
            "courses": n,
            # 🔴 按**课**去重：一门课可能同时缺作业和考试，failed 里会记两条，
            #    但它是同一门课。以前按 failed 条数减，20 门课能算出 scanned = 12。
            # ⚠️ lookauth 也算「没扫到」（0.53.0 v4）：那是「像登录页、但账号
            #    其实是好的」，课没读到就是没读到 —— 不算进来的话 scanned 会把
            #    没查的那几门一起算成查过了。和 native.js 同一处逐条对齐。
            "scanned": n - len(set(f["course"] for f in failed
                                   if f.get("part") in ("auth", "lookauth"))),
            "total": len(items),
            "todo": len(open_items),
            "counts": counts,
            # 0.47.0：全扫了几门 / 只看了签到几门。
            # 0.47.2：`scope` 说明分母是「这学期」还是「全部」——
            #    term = 教务课表收过范围，界面说「本学期 N 门」；
            #    all  = 没有课表，只能一门一门全查，说「共 N 门课」。
            "scope": scope,
            "deep": deep_n,
            "act": act_n,
            "skipped": {"done": done_n, "ended": ended_n, "odd": len(odd),
                        "stale": len(stale)},
            "failed": failed,
            # 已经做完的那批（作业 / 考试 / 练习）。前端在列表下面折叠成一段，
            # 每条带「已完成」标 —— 用户就是靠它来对「学习通到底认不认我交了」。
            "doneItems": done_items,
            "items": open_items,
            # 每门课的结课判定状态。下一轮扫描要原样传回来（`prev_rest`），
            # 断了就每次都从零数，永远判不出结课。
            "rest": rest,
            # 这次因为「已结课」而没扫的课。空列表 ≠ 没有结课 ——
            # 也可能是这一轮全都到点了、全扫了。
            "paused": paused,
            # 全部教学班 + 这一轮怎么处置的（deep=本学期在修 / act=往期只探签到 /
            # skip=用户屏蔽）。「屏蔽课程」那张表直接拿它画。
            "allCourses": all_courses,
            # 全部签到（含已结束），给「签到」卡用。老前端没有这个字段也能跑。
            "signs": signs,
            # 老字段，前端 0.12 及以前在用，留着别删
            "other": len(odd),
            "otherItems": odd,
        }


# ------------------------------------------------------------------ 结果缓存

_STORE_LOCK = threading.RLock()
_LAST = {}          # {"at": ts, "data": {...}}  内存里留一份，进程内复用


def cache_get():
    with _STORE_LOCK:
        return _LAST.get("data")


def cache_set(data, at=None):
    """存一份扫描结果。

    `at` 是「这份结果是什么时候查的」。默认取现在，但**落盘恢复时必须传** ——
    否则重启以后 `cache_age()` 会报「0 秒前刚查的」，把一份三天前的作业
    说成刚扫的。那比不显示更糟：用户会照着它判断「今晚没事」。
    """
    with _STORE_LOCK:
        _LAST["at"] = int(at or time.time())
        _LAST["data"] = data


def cache_clear() -> None:
    """连缓存一起扔掉。断开账号时必须调 —— 否则别人拿到这个手机还能看到你的作业。"""
    with _STORE_LOCK:
        _LAST.clear()


def cache_age() -> int:
    """上次扫描到现在多少秒。没扫过返回 -1。"""
    with _STORE_LOCK:
        at = _LAST.get("at")
    return -1 if not at else max(0, int(time.time()) - at)
