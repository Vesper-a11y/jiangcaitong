# -*- coding: utf-8 -*-
"""财务处 · 校园统一支付平台（cwcfw.zua.edu.cn）—— 欠费 / 电费余额。

## 这是什么

学费、住宿费、空调电费都在这个平台里。公众号「江西财经大学财务处」→
「学生缴费」点开的就是它，**网页端和公众号是同一套接口**，公网可访问（只有 http，
443 没开 —— 和教务一样）。

账号是**财务处自己的**：学号 + 财务处密码。不是教务那套，也不是门户那套。

## 🔴 只读，不碰钱

做：查欠费合计、查欠费明细、查某个房间的电费余额、列楼栋房间。

**不做**（和 `water.py` 的规矩一致）：

  · 充值 / 下单 / 支付 —— 涉及真钱。连「去支付」的步骤都不模拟。
    要看余额、想充钱，点「去平台」用官方页面。

## 🔴 接口两套口径，别混

这个站同时开着新旧两套端点，参数形状不一样：

    旧  POST /xysf/aAppPage/index.aspx/BaseInfo   {"methodName":"ht01","jsonStr":""}
        → 返回 {"d": "<json 字符串>"}，再 json.loads 一次
    旧  POST /xysf/aAppPage/index.aspx/GetRechargeInfo  {"rybh": "<json 串>", "category":"ElecRoomIs"}
        → 同上，{"d": ...}
    新  POST /xysf/api/user/ElecRoomIs/GetOption  {"key":"area","option":{...}}
        → 直接返回 JSON；**要带 x-csrfToken 头**（先 POST /xysf/api/Token/Csrf 取）

页面里 `isBdApi = 'True' === 'True'` ⇒ 登录走**新**口径 `/xysf/api/User/App/Login`，
字段**裸的**（不包 jobj）。用旧口径 `apiService/User/App.aspx/Login` 包 `jobj` 也能回
「0000 成功」，但那是另一条路，别对着两条路各写一半。

⚠️ 旧端点 `index.aspx/*` 有的在 `apiService/` 下 404、有的只有旧路径 —— 上面这张表
   是实测结果，不是猜的。

## 🔴 加密：标准 PKCS#1 v1.5，和门户那套**不一样**

明文是 `密码 + RandomStr`（页面上的隐藏域），拿 `#RSATemp`（1024 位十六进制模数）
做 PKCS#1 v1.5 加密，密文**字节直接 base64**。指数固定 65537。

    门户（sfrz）：jsbn 老式，**小端 + 无填充**
    这里（cwcfw）：**标准 PKCS#1 v1.5，随机填充**

两边搞混的结果都是「账号或密码错误」—— 服务端不会告诉你哪里错了。

## 🔴 判「登录过期」看 code

ht01 成功 `code="0000"`；会话没了回 `code="2000"`（页面里 2000 就是「回登录页」）。
别只看 HTTP 200 —— 过期也是 200。
"""
from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import re
import threading
import urllib.error
import urllib.request

SITE = "http://cwcfw.zua.edu.cn/xysf/"
PAGE = SITE + "aAppPage/index.aspx?type=1"
OLD = SITE + "aAppPage/index.aspx/"
API = SITE + "api/"

UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Mobile Safari/537.36")

TIMEOUT = 20
_LOCK = threading.Lock()


class CwError(Exception):
    """带 kind 的业务错误。kind ∈ auth / net / data / noauth。"""

    def __init__(self, msg: str, kind: str = "data"):
        super().__init__(msg)
        self.kind = kind


# ---------------------------------------------------------------- 会话

class _Sess:
    """一次调用用的会话：CookieJar + 一对 post 方法。"""

    def __init__(self, cookies: dict | None = None):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar))
        for k, v in (cookies or {}).items():
            self.jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=k, value=v, port=None, port_specified=False,
                domain="cwcfw.zua.edu.cn", domain_specified=True,
                domain_initial_dot=False, path="/", path_specified=True,
                secure=False, expires=None, discard=False, comment=None,
                comment_url=None, rest={}))

    def cookies(self) -> dict:
        return {c.name: c.value for c in self.jar}

    def get(self, url: str) -> str:
        r = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with self.op.open(r, timeout=TIMEOUT) as f:
                return f.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise CwError("平台打不开（HTTP %s）" % exc.code, "net") from exc
        except Exception as exc:  # noqa: BLE001
            raise CwError("连不上财务处：%s" % exc, "net") from exc

    def post(self, url: str, payload, csrf: str = "") -> tuple:
        # 🔴 payload 是 None 时要发**空 body**，不能发字面量 `null`
        #    （json.dumps(None) == "null"）—— Token/Csrf 会回 500。
        body = b"" if payload is None else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        hdr = {
            "User-Agent": UA,
            "Content-Type": "application/json; charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": PAGE,
        }
        if csrf:
            hdr["x-csrfToken"] = csrf
        r = urllib.request.Request(url, data=body, headers=hdr)
        try:
            with self.op.open(r, timeout=TIMEOUT) as f:
                return f.status, f.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            # 500 也常带业务话（比如 GetOption 参数不对），交给调用方判
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            raise CwError("连不上财务处：%s" % exc, "net") from exc


def _old_call(s: _Sess, method: str, payload) -> dict:
    """旧口径：包在 {"d": "<json>"} 里。"""
    st, raw = s.post(OLD + method, payload)
    if st != 200:
        raise CwError("平台返回 %s" % st, "net")
    try:
        d = json.loads(raw)["d"]
        return json.loads(d) if isinstance(d, str) else (d or {})
    except Exception as exc:  # noqa: BLE001
        raise CwError("平台返回看不懂的内容（可能改版了）", "data") from exc


def _csrf(s: _Sess) -> str:
    st, raw = s.post(API + "Token/Csrf", None)
    if st != 200:
        raise CwError("取令牌失败（%s）" % st, "net")
    return raw.strip().strip('"')


def _dead(st: int, raw: str) -> bool:
    """这一发是不是「会话没了」。

    🔴 2026-11-16 用户原话：「在这无论点什么都会提示取楼栋房间失败，稍后再试」
       —— 真因是 ASP.NET 会话二十分钟不动就没了，`/xysf/api/` 那几个接口回
       **401 + `{"Message":"已拒绝为此请求授权。"}`**。`Token/Csrf` 没会话也回 200，
       所以看着像「令牌拿到了、下一步莫名失败」，而代码把它当网络抖动说
       「稍后再试」 —— 用户点一百次也是这句话，永远好不了。
       这是 auth，不是 net：上层要拿存着密码重登一次。
    """
    if st in (401, 403):
        return True
    return "已拒绝为此请求授权" in (raw or "") or "重新登录" in (raw or "")


# ---------------------------------------------------------------- 加密

def _rsa_pkcs1(text: str, mod_hex: str, exp: int = 65537) -> bytes:
    n = int(mod_hex, 16)
    k = (n.bit_length() + 7) // 8
    m = text.encode("utf-8")
    if k < len(m) + 11:
        raise CwError("密码太长了", "data")
    need = k - len(m) - 3
    ps = b""
    while len(ps) < need:
        ps += os.urandom(need - len(ps))
    ps = bytes(b or 1 for b in ps)[:need]        # 填充字节不许是 00
    em = b"\x00\x02" + ps + b"\x00" + m
    return pow(int.from_bytes(em, "big"), exp, n).to_bytes(k, "big")


def encrypt_pwd(pwd: str, mod_hex: str, random_str: str) -> str:
    """密码 + RandomStr → base64 密文。"""
    return base64.b64encode(_rsa_pkcs1(pwd + random_str, mod_hex)).decode()


# ---------------------------------------------------------------- 登录

def login(xh: str, pwd: str) -> dict:
    """登录。成功返回 {cookies, name, xh, at}，失败抛 CwError('auth')。"""
    xh = (xh or "").strip()
    if not xh or not pwd:
        raise CwError("学号和密码都要填", "auth")

    s = _Sess()
    html = s.get(PAGE)
    mod = re.search(r'id="RSATemp"[^>]*value="([0-9A-Fa-f]+)"', html)
    rnd = re.search(r'id="RandomStr"[^>]*value="([^"]*)"', html)
    if not (mod and rnd):
        raise CwError("登录页变了，找不到加密参数（可能改版了）", "data")

    st, raw = s.post(API + "User/App/Login", {
        "xh": xh,
        "pwd": encrypt_pwd(pwd, mod.group(1), rnd.group(1)),
        "rsaStr": rnd.group(1),
        "ltyp": "id",
    })
    if st != 200:
        raise CwError("登录请求失败（%s）" % st, "net")
    try:
        d = json.loads(raw)
        if isinstance(d, dict) and isinstance(d.get("d"), dict):
            d = d["d"]
    except Exception as exc:  # noqa: BLE001
        raise CwError("登录返回看不懂的内容", "data") from exc

    code = str((d or {}).get("code") or "")
    # 🔴 成功的码是字符串 "0000"。别写成 `not code` 或 `code != 0`。
    if code != "0000":
        msg = str((d or {}).get("msg") or "").strip() or "登录没通过"
        raise CwError(msg, "auth")

    return {"cookies": s.cookies(), "xh": xh, "at": 0}


# ---------------------------------------------------------------- 欠费

def bill(st: dict) -> dict:
    """欠费合计 + 明细。

    返回 {"total": "800.00", "count": 1, "items": [{"period","name","due","paid"}]}
    🔴 金额一律**字符串**（平台给的就是字符串，别 float 化 —— 800.00 会变 800.0）。
    """
    s = _Sess(st.get("cookies") or {})
    d = _old_call(s, "BaseInfo", {"methodName": "ht01", "jsonStr": ""})
    if str(d.get("code")) == "2000":
        raise CwError("财务处登录过期了，重新登一次", "auth")
    if str(d.get("code")) != "0000":
        raise CwError(str(d.get("msg") or "查不到欠费"), "data")

    ctx = d.get("context") or []
    row = ctx[0] if isinstance(ctx, list) and ctx else {}
    total = str(row.get("qfzje") or "0.00")
    count = int(row.get("qfZnumS") or 0)

    items = []
    # 明细要另走 ht02。它给的 zje/znums 与 ht01 一致，但项目名只在 ht02 里。
    try:
        d2 = _old_call(s, "BaseInfo", {"methodName": "ht02", "jsonStr": ""})
        for grp in (d2.get("context") or []):
            for it in (grp.get("detil") or []):
                due = str(it.get("qfje") or "0.00")
                if due in ("0.00", "0", ""):
                    continue
                items.append({
                    "period": str(grp.get("sfqjmc") or ""),
                    "name": str(it.get("sfxmmc") or ""),
                    "due": due,
                })
    except CwError:
        pass          # 明细是附赠的，拿不到不影响合计

    return {"total": total, "count": count, "items": items,
            "name": str((d.get("ExData") or {}).get("xm") or "")}


# ---------------------------------------------------------------- 电费

def elec_options(st: dict, key: str, areaid: str = "1",
                 buildid: str = "-1", roomid: str = "-1") -> dict:
    """楼栋 / 房间选项。key ∈ area / build / room。

    返回 {"options": [{"value","label"}], "bound": bool}
    """
    s = _Sess(st.get("cookies") or {})
    csrf = _csrf(s)
    option = {"areaid": str(areaid), "buildid": str(buildid),
              "roomid": str(roomid), "IsLxr": False, "IsDefault": False,
              "IsFirst": False, "Cxid": ""}
    stc, raw = s.post(API + "user/ElecRoomIs/GetOption",
                      {"key": key, "option": option}, csrf)
    if _dead(stc, raw):
        raise CwError("财务处登录过期了", "auth")
    if stc != 200:
        raise CwError("取楼栋房间失败（%s）" % stc, "net")
    try:
        d = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise CwError("楼栋房间返回看不懂的内容", "data") from exc
    if not d.get("IsSuccess"):
        raise CwError(str(d.get("RetMsg") or "取楼栋房间失败"), "data")

    opts = []
    for it in (d.get("Content") or []):
        v = str(it.get("value") or "")
        if v == "-1":
            continue
        # 🔴 标签里带一长串尾部空格（平台就是这样给的），折掉——
        #    否则「1-101空调（未缴费）」会撑出格子。
        opts.append({"value": v,
                     "label": re.sub(r"\s+", " ", str(it.get("label") or "")).strip()})
    return {"options": opts,
            "bound": bool((d.get("ExData") or {}).get("IsBind"))}


def elec_balance(st: dict, areaid: str, buildid: str, roomid: str) -> dict:
    """某房间的**剩余电量**。返回 {"balance": "17.73", "room": "1-101空调", "cxid": ...}

    🔴 `balance` 这个数是 **度（kWh）**，不是元 —— 2026-11-16 用户纠正过一次：
       「宿舍电费你搞错了，那个剩余的是度，不是元」。平台给的是剩余电量，
       充值那一步才按元结算。键名留着平台原样（`CzThirdInfo.Balance`），
       但界面上必须写「度」。判据 `check_cw` 咬这件事。
    🔴 读不到时要 **抛**，不要返回 "0.00" —— 「没读到」和「没电了」是两件事，
       界面上一个 0.00 会让用户跑去充钱。
    """
    s = _Sess(st.get("cookies") or {})
    search = {"areaid": str(areaid), "buildid": str(buildid),
              "roomid": str(roomid), "IsLxr": False, "IsDefault": False,
              "IsFirst": False, "Cxid": ""}
    d = _old_call(s, "GetRechargeInfo",
                  {"rybh": json.dumps(search, ensure_ascii=False),
                   "category": "ElecRoomIs"})
    if str(d.get("RetCode")) == "N":
        raise CwError("财务处登录过期了，重新登一次", "auth")
    if str(d.get("RetCode")) != "T":
        raise CwError(str(d.get("RetMsg") or "查不到这个房间").strip(), "data")
    info = ((d.get("Content") or {}).get("CzThirdInfo") or {})
    bal = info.get("Balance")
    if bal is None:
        raise CwError("这个房间没有余额数据", "data")
    return {"balance": "%.2f" % float(bal),
            "room": str(info.get("Czxm") or ""),
            "cxid": str(info.get("Cxid") or "")}


# ---------------------------------------------------------------- 记录（只读）

RECORD_SIZE = 10      # 平台一页 10 条（页面里 pageSize 从 1 起）


def _num2(v) -> str:
    """金额/电量统一两位小数字符串。拿不到就空串 —— 别编 0。"""
    try:
        return "%.2f" % float(v)
    except (TypeError, ValueError):
        return ""


def records(st: dict, cxid: str, kind: str = "pay", page: int = 1) -> dict:
    """缴费（购电）记录 / 用电记录。**只读**。

    kind = "pay" → `GetPayRecord`：订单号 `Custsn` / 购电日期 `PayDate` /
                   充值金额 `Tranamt`（**元**）
    kind = "use" → `GetUseRecord`：日期 `UseDate` / 用电量 `DeltaUsedAmp`（**度**）

    字段名不是猜的：平台自己的模板 `ViewPages/HomeMainChildPage/Recharge/`
    `ElecRoomIsJfSec.html` 与 `ElecRoomIsUse.html` 里就是这几个绑定。

    🔴 `cxid` 是**房间**的 id（`GetRechargeInfo` 回的 `Cxid`），不是学号。
       空的 cxid 平台只回一个「没有记录」—— 那看着像「你没交过钱」，
       比报错糟得多，所以这里先拦住。
    🔴 不传日期条件（`dateType` 留空 = 平台默认）：只取第一页。
       乱猜 `oneMonthSec` 是「本月」还是「近一月」，会把窗口说错；
       界面只写「最近 N 条」，多的请去平台看。
    """
    if not cxid:
        raise CwError("先选好房间、读一次剩余电量，再来看记录", "data")
    s = _Sess(st.get("cookies") or {})
    csrf = _csrf(s)
    if kind == "use":
        meth = "GetUseRecord"
        body = {"cxid": cxid, "page": int(page), "startDate": "", "endDate": "",
                "dateType": "", "type": 0}
    else:
        meth = "GetPayRecord"
        body = {"cxid": cxid, "page": int(page), "startDate": "", "endDate": "",
                "dateType": ""}
    stc, raw = s.post(API + "user/ElecRoomIs/" + meth, body, csrf)
    if _dead(stc, raw):
        raise CwError("财务处登录过期了", "auth")
    if stc != 200:
        raise CwError("查记录失败（%s）" % stc, "net")
    try:
        d = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise CwError("记录返回看不懂的内容（可能改版了）", "data") from exc
    if not isinstance(d, dict):
        raise CwError("记录返回看不懂的内容（可能改版了）", "data")
    # 会话没了：新口径不认 2000，只把 IsSuccess 置 false 并给一句 RetMsg。
    if str(d.get("RetCode")) == "N" and "登录" in str(d.get("RetMsg") or ""):
        raise CwError("财务处登录过期了，重新登一次", "auth")
    if not d.get("IsSuccess"):
        raise CwError(str(d.get("RetMsg") or "查不到记录").strip(), "data")

    rows = d.get("Content") or []
    items = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        if kind == "use":
            items.append({"at": str(it.get("UseDate") or ""),
                          "num": _num2(it.get("DeltaUsedAmp"))})
        else:
            items.append({"at": str(it.get("PayDate") or ""),
                          "num": _num2(it.get("Tranamt")),
                          "sn": str(it.get("Custsn") or "")})
    return {"items": items, "more": len(rows) >= RECORD_SIZE}
