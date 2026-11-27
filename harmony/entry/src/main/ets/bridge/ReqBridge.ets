import { http, socket } from '@kit.NetworkKit';
import { webview } from '@kit.ArkWeb';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { Want } from '@kit.AbilityKit';
import { util } from '@kit.ArkTS';
import { fileIo as fs } from '@kit.CoreFileKit';
import { appContext, aesCbc, b64Of, rawDel, rawGet, rawHas,
  sealGet, sealPut, sealState } from './Store';

const TAG: string = 'HYT-Net';
const BASE: string = 'http://jwglxt.upstream.edu.cn';
const UA: string =
  'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'Chrome/131.0.0.0 Mobile Safari/537.36';

/* 原生请求桥。协议照抄 Android 的 `NativeBridge`：
 *
 *      JS 侧  Android.req2(id, method, path, body, referer, headers)
 *      原生  发请求 → window.__nativeDone(id, "<json 字符串>")
 *
 *  json = {status, body, url, date}
 *
 *  🔴 为什么必须沿用这个协议：`native.js` 里那一千多行登录/解析逻辑
 *     （学习通、教务、慧生活）全靠它。协议一换，那套代码就得在鸿蒙上
 *     重写一遍 —— 而它是这个 App 唯一被真实账号验证过的部分。
 *
 *  🔴🔴 状态全部放**模块级**，`req`/`req2` 里一个 `this` 都不许出现。
 *     这两个方法由 ArkWeb 从 JS 侧回调进来，**谁也不知道它调用时绑不绑
 *     `this`**。写 `this.doReq(...)`，一旦那边是「掏出函数直接调」，
 *     就是 TypeError，请求一条都发不出去，而且 JS 侧只看到一个永远
 *     不会回来的 Promise（界面就是一直转圈）。
 *
 *  ⚠️ 与 Android 的三处硬差别（2026-11-14 用户报「还是完全不行」时补的）：
 *     ① Java 那边靠系统 CookieHandler 自动带 cookie；鸿蒙的 http 不带，
 *        这里自己维护罐子 —— 而且必须**按域名 + 路径**匹配，因为教务、
 *        学习通、慧生活三个域名都用 `JSESSIONID`，只按名字存必然串；
 *        学习通的 cookie 还挂在 `.chaoxing.com` 上，要能带给所有子域。
 *     ② Java 的 HttpURLConnection 自动跟 301/302/303；鸿蒙这边不保证，
 *        而教务登录成功就是 302 —— 不跟的话拿到的是一张小跳转页，
 *        正文里没有首页标记，判成「登录失败」。所以自己跟。
 *     ③ 学习通密码要 AES 加密（`cxAes`）。Android 是 Java 里算的，
 *        这边原来**整块没实现**，于是学习通直接报「密码加密失败」。
 */

/** 回调要往哪个 Web 容器里送。Index.ets 建好组件时设一次。 */
let CTL: webview.WebviewController | null = null;

export function setWebController(c: webview.WebviewController): void {
  CTL = c;
}

// ------------------------------------------------------------------ cookie 罐子

interface JarCookie {
  name: string;
  value: string;
  /** 小写，不带前导点。`hostOnly` 为假时表示「这个域及其所有子域」。 */
  domain: string;
  hostOnly: boolean;
  path: string;
  /** 秒；0 = 这次会话有效。 */
  expires: number;
}

let JAR: JarCookie[] = [];

/** cookie 属性名。这些不能当 cookie 存 —— `Path=/` 不是一条 cookie。 */
const ATTRS: string[] = ['path', 'domain', 'expires', 'max-age', 'httponly',
  'secure', 'samesite', 'version', 'comment', 'priority'];

function isAttr(k: string): boolean {
  return ATTRS.indexOf(k.toLowerCase()) >= 0;
}

/** `http://a.b/c` → `a.b`。取不到就返回空串。 */
function hostOf(url: string): string {
  const m: RegExpMatchArray | null = url.match(/^[a-zA-Z]+:\/\/([^\/?#]+)/);
  if (m === null) {
    return '';
  }
  let h: string = m[1].toLowerCase();
  const colon: number = h.indexOf(':');
  if (colon > 0) {
    h = h.substring(0, colon);
  }
  return h;
}

function pathOf(url: string): string {
  const m: RegExpMatchArray | null = url.match(/^[a-zA-Z]+:\/\/[^\/?#]*([^?#]*)/);
  const p: string = (m === null || m[1].length === 0) ? '/' : m[1];
  return p;
}

function hostMatch(host: string, c: JarCookie): boolean {
  if (c.hostOnly) {
    return host === c.domain;
  }
  return host === c.domain || host.endsWith('.' + c.domain);
}

/** 相对 Location → 绝对地址。 */
function absUrl(base: string, loc: string): string {
  if (loc.startsWith('http://') || loc.startsWith('https://')) {
    return loc;
  }
  const m: RegExpMatchArray | null = base.match(/^([a-zA-Z]+:\/\/[^\/?#]+)/);
  const root: string = m === null ? '' : m[1];
  if (loc.startsWith('/')) {
    return root + loc;
  }
  let dir: string = pathOf(base);
  const cut: number = dir.lastIndexOf('/');
  dir = cut >= 0 ? dir.substring(0, cut + 1) : '/';
  return root + dir + loc;
}

/**
 * 一条 `Set-Cookie` 原文 → 若干段。
 *
 * 🔴 不能直接 `split(';')` 了事：属性也在里面（`Path=/`）。要靠**属性名表**
 *    把「cookie」和「属性」分开，否则会往罐子里塞一堆叫 `path` 的垃圾，
 *    下一次请求又把它们当 cookie 发出去。
 * 🔴 也不能只按 `\n` 切：多条 cookie 有可能被合并成 `a=1, b=2`。
 *    逗号只在「后面紧跟 name=」的地方切 —— `Expires=Wed, 21 Oct …` 里的
 *    那个逗号后面是数字，不会被切开。
 */
function splitSetCookie(raw: string): string[] {
  const parts: string[] = [];
  const lines: string[] = raw.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line: string = lines[i];
    const re: RegExp = /,(?=\s*[A-Za-z_][A-Za-z0-9_.\-]*\s*=)/g;
    let start: number = 0;
    let m: RegExpExecArray | null = re.exec(line);
    while (m !== null) {
      parts.push(line.substring(start, m.index));
      start = m.index + 1;
      m = re.exec(line);
    }
    parts.push(line.substring(start));
  }
  return parts;
}

/** 把一条 `name=value; Path=/; …` 记进罐子。 */
function putSetCookie(raw: string, host: string): void {
  const segs: string[] = raw.split(';');
  if (segs.length === 0) {
    return;
  }
  const head: string = segs[0].trim();
  const eq: number = head.indexOf('=');
  if (eq <= 0) {
    return;
  }
  const name: string = head.substring(0, eq).trim();
  const value: string = head.substring(eq + 1).trim();
  if (name.length === 0 || isAttr(name)) {
    return;
  }
  let domain: string = host;
  let hostOnly: boolean = true;
  let path: string = '';
  let expires: number = 0;
  for (let i = 1; i < segs.length; i++) {
    const s: string = segs[i].trim();
    const e: number = s.indexOf('=');
    if (e <= 0) {
      continue;
    }
    const k: string = s.substring(0, e).trim().toLowerCase();
    const v: string = s.substring(e + 1).trim();
    if (k === 'domain' && v.length > 0) {
      domain = v.replace(/^\./, '').toLowerCase();
      hostOnly = false;
    } else if (k === 'path' && v.length > 0) {
      path = v;
    } else if (k === 'max-age') {
      const n: number = parseInt(v, 10);
      if (!isNaN(n)) {
        expires = Math.floor(Date.now() / 1000) + n;
      }
    } else if (k === 'expires') {
      const t: number = Date.parse(v);
      if (!isNaN(t)) {
        expires = Math.floor(t / 1000);
      }
    }
  }

  // 同名同域的后写覆盖先写；`Max-Age<=0` 是「删掉这条」。
  const now: number = Math.floor(Date.now() / 1000);
  const keep: JarCookie[] = [];
  for (let i = 0; i < JAR.length; i++) {
    const c: JarCookie = JAR[i];
    const same: boolean = c.name === name && c.domain === domain;
    if (!same) {
      keep.push(c);
    }
  }
  JAR = keep;
  if (expires > 0 && expires <= now) {
    return;
  }
  JAR.push({
    name: name,
    value: value,
    domain: domain,
    hostOnly: hostOnly,
    path: path.length > 0 ? path : '/',
    expires: expires
  });
}

/** 发给某个地址的 `Cookie` 头。 */
function cookieHeader(url: string): string {
  const host: string = hostOf(url);
  const path: string = pathOf(url);
  const now: number = Math.floor(Date.now() / 1000);
  const parts: string[] = [];
  for (let i = 0; i < JAR.length; i++) {
    const c: JarCookie = JAR[i];
    if (c.expires > 0 && c.expires <= now) {
      continue;
    }
    if (!hostMatch(host, c)) {
      continue;
    }
    if (c.path !== '/' && !path.startsWith(c.path)) {
      continue;
    }
    parts.push(c.name + '=' + c.value);
  }
  return parts.join('; ');
}

function headerValue(rsp: http.HttpResponse, name: string): string {
  try {
    const h: Record<string, string> = rsp.header as Record<string, string>;
    const keys: string[] = Object.keys(h);
    for (let i = 0; i < keys.length; i++) {
      if (keys[i].toLowerCase() === name) {
        return String(h[keys[i]]);
      }
    }
  } catch (e) {
    return '';
  }
  return '';
}

/** 响应里的 cookie 全收进罐子。 */
function eatCookies(rsp: http.HttpResponse, url: string): void {
  const host: string = hostOf(url);
  const raws: string[] = [];
  // ① 专门的 `cookies` 字段（多条 set-cookie 合在一起）。优先用它：
  //    `header` 里同名响应头怎么合并，各版本行为不一致。
  try {
    const c: string = rsp.cookies;
    if (c !== undefined && c !== null && c.length > 0) {
      raws.push(c);
    }
  } catch (e) {
    hilog.warn(0x0000, TAG, 'cookies 字段读不到');
  }
  // ② 兜底：header 里的 set-cookie。两条都收，重复的会被覆盖，不会多出来。
  const h: string = headerValue(rsp, 'set-cookie');
  if (h.length > 0) {
    raws.push(h);
  }
  for (let i = 0; i < raws.length; i++) {
    const one: string[] = splitSetCookie(raws[i]);
    for (let j = 0; j < one.length; j++) {
      putSetCookie(one[j], host);
    }
  }
}

// ------------------------------------------------------------------ 回执

/** 发给 JS 的回执。`id` 是 JS 侧给的序号，认不出来就当没人等。 */
function finish(id: number, json: string): void {
  const c = CTL;
  if (c === null) {
    hilog.error(0x0000, TAG, '没有 Web 容器，回执丢掉（id=%{public}d）', id);
    return;
  }
  // 🔴 第二次 stringify 是给 JS 看的：第一次得到结果 JSON，
  //    第二次把它变成一个**字符串字面量**，嵌进表达式里。
  const call: string = 'window.__nativeDone(' + id + ',' + JSON.stringify(json) + ')';
  c.runJavaScript(call).catch((e: Error) => {
    hilog.warn(0x0000, TAG, '回执第一次没送到（%{public}s），再试一次', e.message);
    setTimeout(() => {
      c.runJavaScript(call).catch((e2: Error) => {
        hilog.error(0x0000, TAG, '回执送不到：%{public}s', e2.message);
      });
    }, 300);
  });
}

function fail(msg: string): string {
  const o: Record<string, Object> = {
    'status': 0,
    'body': '',
    'url': '',
    'date': 0,
    'error': msg
  };
  return JSON.stringify(o);
}

// ------------------------------------------------------------------ 请求

/** `{"k":"v"}` → 逐个设头。解析失败就当没传（同 Android 的处理）。 */
function applyHeaders(header: Record<string, string>, json: string): void {
  if (json === undefined || json.length === 0) {
    return;
  }
  try {
    const o: Record<string, string> = JSON.parse(json) as Record<string, string>;
    const keys: string[] = Object.keys(o);
    for (let i = 0; i < keys.length; i++) {
      const k: string = keys[i];
      if (k.length === 0) {
        continue;
      }
      if (k.toLowerCase() === 'content-length') {
        continue;
      }
      header[k] = String(o[k]);
    }
  } catch (e) {
    hilog.warn(0x0000, TAG, '请求头解析失败，按没有处理');
  }
}

// ------------------------------------------------------------------ 明文 HTTP

/**
 * 🔴🔴 为什么教务这条路要自己走 TCP（2026-11-14 第三次「还是完全不行」之后）：
 *
 *   `https` 的学习通、完美校园都通，只有教务连不上 —— 而教务是**唯一的 http**
 *   （实测 `https://jwglxt.upstream.edu.cn:443` 直接连不上，学校没开 443）。
 *   HarmonyOS 默认禁明文，官方说法是配 `network_config.json` + module.json5 里的
 *   metadata。问题在于**meta 的 name 到底叫什么，七种写法都在流传**：
 *     network_security_config / networkSecurityConfig / ohos.network.config /
 *     ohos.net.network_config / ohos.net.network.security.config / network_config …
 *   我在 GitHub 上按 `$profile:network_config` 搜了 18 个真实工程，七种各有一堆，
 *   说明**大部分是抄错的**、抄了也不生效。我们两条都写了，真机上仍然被拦。
 *
 *   所以不再跟这套配置赌：明文拦截是 `@ohos.net.http` 这层（错误码 2300997
 *   `Cleartext traffic not permitted`）里的策略，**TCP socket 是字节流，不过那道闸**。
 *   这里就自己拼 HTTP/1.1 发出去。只做我们真正用到的那一小块：
 *   GET / POST、`Connection: close`、Content-Length 与 chunked 两种收法、
 *   按 Content-Type 里的 charset 解码。**不发 Accept-Encoding**，所以不用管 gzip。
 */

interface Shot {
  status: number;
  text: string;
  loc: string;
  date: string;
}

/** 把错的原文摊开：`2300997 Cleartext traffic not permitted`。
 *  只进日志和诊断，不进用户面前那句话（见 native.js 那条注释）。 */
function errText(e: Error): string {
  const be = e as BusinessError;
  let code: number = 0;
  try {
    code = Number(be.code);
  } catch (x) {
    code = 0;
  }
  const msg: string = (be.message === undefined || be.message === null) ? '' : be.message;
  return code === 0 ? msg : (String(code) + ' ' + msg);
}

/** 系统 http 模块发一次。不跟跳转 —— 跳转由 doReq 统一处理。 */
async function httpOnce(m: string, url: string, header: Record<string, string>,
                        send: string): Promise<Shot> {
  const opt: http.HttpRequestOptions = {
    method: m === 'POST' ? http.RequestMethod.POST : http.RequestMethod.GET,
    header: header,
    connectTimeout: 15000,
    readTimeout: 30000,
    expectDataType: http.HttpDataType.STRING
  };
  if (send.length > 0) {
    opt.extraData = send;
  }
  const req = http.createHttp();
  try {
    const rsp: http.HttpResponse = await req.request(url, opt);
    eatCookies(rsp, url);
    const text: string = typeof rsp.result === 'string' ? rsp.result : '';
    return {
      status: Number(rsp.responseCode),
      text: text,
      loc: headerValue(rsp, 'location'),
      date: headerValue(rsp, 'date')
    };
  } finally {
    req.destroy();
  }
}

interface WireUrl {
  host: string;
  port: number;
  /** 请求行里那一段：`/path?query`。 */
  target: string;
}

function parseWireUrl(u: string): WireUrl {
  let rest: string = u;
  const at: number = rest.indexOf('://');
  if (at >= 0) {
    rest = rest.substring(at + 3);
  }
  let host: string = rest;
  let target: string = '/';
  const slash: number = rest.indexOf('/');
  if (slash >= 0) {
    host = rest.substring(0, slash);
    target = rest.substring(slash);
  }
  let port: number = 80;
  const colon: number = host.lastIndexOf(':');
  if (colon > 0) {
    const p: number = parseInt(host.substring(colon + 1), 10);
    if (!isNaN(p)) {
      port = p;
      host = host.substring(0, colon);
    }
  }
  if (host.length === 0) {
    throw new Error('地址看不懂：' + u);
  }
  return { host: host, port: port, target: target };
}

function concatBytes(parts: Uint8Array[], total: number): Uint8Array {
  const out: Uint8Array = new Uint8Array(total);
  let at: number = 0;
  for (let i = 0; i < parts.length; i++) {
    out.set(parts[i], at);
    at += parts[i].length;
  }
  return out;
}

/** 找 `\r\n\r\n`，返回头结束位置（含那四个字节）。找不到 −1。 */
function headEndAt(b: Uint8Array): number {
  for (let i = 3; i < b.length; i++) {
    if (b[i] === 10 && b[i - 1] === 13 && b[i - 2] === 10 && b[i - 3] === 13) {
      return i + 1;
    }
  }
  return -1;
}

function textOf(b: Uint8Array, charset: string): string {
  try {
    return util.TextDecoder.create(charset).decodeToString(b);
  } catch (e) {
    return util.TextDecoder.create('utf-8').decodeToString(b);
  }
}

interface HeadParts {
  status: number;
  charset: string;
  chunked: boolean;
  /** −1 = 没有 Content-Length（那就读到对端关连接）。 */
  length: number;
  loc: string;
  date: string;
  cookies: string[];
}

function parseHead(head: string): HeadParts {
  const lines: string[] = head.split('\r\n');
  let status: number = 0;
  if (lines.length > 0) {
    const seg: string[] = lines[0].split(' ');
    if (seg.length > 1) {
      const n: number = parseInt(seg[1], 10);
      if (!isNaN(n)) {
        status = n;
      }
    }
  }
  let charset: string = 'utf-8';
  let chunked: boolean = false;
  let length: number = -1;
  let loc: string = '';
  let date: string = '';
  const cookies: string[] = [];
  for (let i = 1; i < lines.length; i++) {
    const ln: string = lines[i];
    const c: number = ln.indexOf(':');
    if (c <= 0) {
      continue;
    }
    const k: string = ln.substring(0, c).trim().toLowerCase();
    const v: string = ln.substring(c + 1).trim();
    if (k === 'content-type') {
      const m = v.match(/charset=([^;]+)/i);
      if (m !== null) {
        charset = m[1].trim().toLowerCase();
      }
    } else if (k === 'transfer-encoding') {
      chunked = v.toLowerCase().indexOf('chunked') >= 0;
    } else if (k === 'content-length') {
      const n: number = parseInt(v, 10);
      if (!isNaN(n)) {
        length = n;
      }
    } else if (k === 'location') {
      loc = v;
    } else if (k === 'date') {
      date = v;
    } else if (k === 'set-cookie') {
      // 我们自己用 `Connection: close`，一个响应头一行，不用拆逗号。
      cookies.push(v);
    }
  }
  return {
    status: status, charset: charset, chunked: chunked, length: length,
    loc: loc, date: date, cookies: cookies
  };
}

function dechunk(b: Uint8Array): Uint8Array {
  const out: Uint8Array = new Uint8Array(b.length);
  let len: number = 0;
  let i: number = 0;
  while (i < b.length) {
    let eol: number = -1;
    for (let j = i; j + 1 < b.length; j++) {
      if (b[j] === 13 && b[j + 1] === 10) {
        eol = j;
        break;
      }
    }
    if (eol < 0) {
      break;
    }
    const spec: string = textOf(b.subarray(i, eol), 'utf-8').split(';')[0].trim();
    const n: number = parseInt(spec, 16);
    if (isNaN(n) || n <= 0) {
      break;
    }
    const from: number = eol + 2;
    const to: number = Math.min(from + n, b.length);
    for (let k = from; k < to; k++) {
      out[len] = b[k];
      len++;
    }
    i = to + 2;
  }
  return out.subarray(0, len);
}

/** 把攒下来的字节解析成结果。**只解析一次**，收齐了再调。 */
function parseWire(all: Uint8Array, url: string): Shot {
  const he: number = headEndAt(all);
  if (he < 0) {
    throw new Error('响应没收全（没有响应头结尾）');
  }
  const h: HeadParts = parseHead(textOf(all.subarray(0, he), 'utf-8'));
  let body: Uint8Array = all.subarray(he);
  if (h.chunked) {
    body = dechunk(body);
  } else if (h.length >= 0 && body.length > h.length) {
    body = body.subarray(0, h.length);
  }
  const host: string = hostOf(url);
  for (let i = 0; i < h.cookies.length; i++) {
    putSetCookie(h.cookies[i], host);
  }
  return {
    status: h.status,
    text: textOf(body, h.charset),
    loc: h.loc,
    date: h.date
  };
}

async function wireOnce(m: string, url: string, header: Record<string, string>,
                        send: string): Promise<Shot> {
  const pu: WireUrl = parseWireUrl(url);
  const body: Uint8Array = new util.TextEncoder('utf-8').encodeInto(
    send.length > 0 ? send : '');

  const lines: string[] = [];
  lines.push(m + ' ' + pu.target + ' HTTP/1.1');
  lines.push('Host: ' + pu.host);
  const ks: string[] = Object.keys(header);
  for (let i = 0; i < ks.length; i++) {
    const k: string = ks[i];
    const lk: string = k.toLowerCase();
    // 这几个我们自己写：Host 要带端口口径、长度按真实字节算、连接必须关。
    if (lk === 'host' || lk === 'connection' || lk === 'content-length') {
      continue;
    }
    lines.push(k + ': ' + header[k]);
  }
  lines.push('Content-Length: ' + body.length);
  lines.push('Connection: close');
  const headBytes: Uint8Array =
    new util.TextEncoder('utf-8').encodeInto(lines.join('\r\n') + '\r\n\r\n');
  const payload: Uint8Array = new Uint8Array(headBytes.length + body.length);
  payload.set(headBytes, 0);
  payload.set(body, headBytes.length);

  const sock: socket.TCPSocket = socket.constructTCPSocketInstance();
  return new Promise<Shot>((resolve, reject) => {
    const parts: Uint8Array[] = [];
    let total: number = 0;
    let done: boolean = false;
    let timer: number = -1;

    const settle = (err: string): void => {
      if (done) {
        return;
      }
      done = true;
      if (timer !== -1) {
        clearTimeout(timer);
      }
      sock.close().catch((e: Error) => {
        hilog.warn(0x0000, TAG, '关连接失败：%{public}s', e.message);
      });
      if (err.length > 0) {
        reject(new Error(err));
        return;
      }
      try {
        resolve(parseWire(concatBytes(parts, total), url));
      } catch (e) {
        reject(new Error((e as Error).message));
      }
    };

    /** 收够了就收工，别等对端关连接（有些服务器关得慢）。 */
    const enough = (): boolean => {
      if (total === 0) {
        return false;
      }
      const all: Uint8Array = concatBytes(parts, total);
      const he: number = headEndAt(all);
      if (he < 0) {
        return false;
      }
      const h: HeadParts = parseHead(textOf(all.subarray(0, he), 'utf-8'));
      if (h.chunked || h.length < 0) {
        return false;
      }
      return all.length - he >= h.length;
    };

    sock.on('message', (info: socket.SocketMessageInfo) => {
      const b: Uint8Array = new Uint8Array(info.message);
      parts.push(b);
      total += b.length;
      if (enough()) {
        settle('');
      }
    });
    sock.on('close', () => {
      settle('');
    });
    sock.on('error', (e: BusinessError) => {
      settle(errText(e as Error));
    });
    timer = setTimeout(() => {
      settle('读超时');
    }, 25000);

    sock.connect({ address: { address: pu.host, port: pu.port }, timeout: 15000 })
      .then((): void => {
        const opt: socket.TCPSendOptions = { data: payload.buffer as ArrayBuffer };
        sock.send(opt).catch((e: Error) => {
          settle('发不出去：' + errText(e));
        });
      })
      .catch((e: Error) => {
        settle('连接失败：' + errText(e));
      });
  });
}

/** 真正干活的那一段。**不碰 `this`**，见文件头。 */
async function doReq(method: string, path: string, body: string,
                     referer: string, headersJson: string): Promise<string> {
  const absolute: boolean =
    path !== undefined && (path.startsWith('http://') || path.startsWith('https://'));
  let url: string = absolute ? path : BASE + path;
  let m: string = (method === undefined || method.length === 0) ? 'GET' : method;
  let send: string = (body === undefined) ? '' : body;

  const base: Record<string, string> = {
    'User-Agent': UA,
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
  };
  if (referer !== undefined && referer.length > 0) {
    const refAbs: boolean =
      referer.startsWith('http://') || referer.startsWith('https://');
    base['Referer'] = refAbs ? referer : BASE + referer;
  }
  applyHeaders(base, headersJson);
  if (send.length > 0 && base['Content-Type'] === undefined) {
    base['Content-Type'] = 'application/x-www-form-urlencoded';
  }

  let hops: number = 0;
  while (true) {
    const header: Record<string, string> = {};
    const bk: string[] = Object.keys(base);
    for (let i = 0; i < bk.length; i++) {
      header[bk[i]] = base[bk[i]];
    }
    const cookie: string = cookieHeader(url);
    if (cookie.length > 0) {
      header['Cookie'] = cookie;
    }

    // 🔴 `http://` 先走自己的 TCP 通道：明文拦截在 http 模块里，socket 不在那道闸下。
    //    它要是也失败（真的连不上），再退回系统模块碰一次运气，两条的原文都记着，
    //    这样「被拦」和「网络到不了」在日志里能分开。
    let shot: Shot;
    if (url.startsWith('http://')) {
      try {
        shot = await wireOnce(m, url, header, send);
      } catch (e1) {
        const w1: string = errText(e1 as Error);
        try {
          shot = await httpOnce(m, url, header, send);
        } catch (e2) {
          throw new Error('明文通道 ' + w1 + '；系统通道 ' + errText(e2 as Error));
        }
      }
    } else {
      shot = await httpOnce(m, url, header, send);
    }

    // 自己跟跳转：教务登录成功是 302 → homeExt，鸿蒙这边不保证会跟。
    if (shot.status >= 300 && shot.status < 400 && hops < 5 && shot.loc.length > 0) {
      url = absUrl(url, shot.loc);
      hops++;
      if (shot.status === 301 || shot.status === 302 || shot.status === 303) {
        m = 'GET';
        send = '';
      }
      continue;
    }

    const date: number = Date.parse(shot.date);
    const o: Record<string, Object> = {
      'status': shot.status,
      'body': shot.text,
      'url': url,
      'date': isNaN(date) ? 0 : Math.floor(date / 1000)
    };
    return JSON.stringify(o);
  }
}

/** 一次请求的完整流程：发 → 回来把结果送给 JS。任何一步抛错都变成失败回执。 */
function start(id: number, method: string, path: string, body: string,
               referer: string, headersJson: string): void {
  doReq(method, path, body, referer, headersJson)
    .then((json: string) => finish(id, json))
    .catch((e: Error) => {
      // 带上原始 message：界面上「网络不通」和「权限被拦」得能分开，
      // 不然下一个人报上来还是同一句「登不上」。
      hilog.error(0x0000, TAG, '请求失败 %{public}s：%{public}s', path, e.message);
      finish(id, fail(e.message));
    });
}

// ------------------------------------------------------------------ 学习通密码

/** 学习通的密码加密：AES-128-CBC/PKCS7，**密钥和 IV 是同一个串**，
 *  结果 base64（不带换行）。和 Android 的 `CxNet.aesB64` 逐字对齐 ——
 *  差一个字服务端就判「密码不对」，而且它不会告诉你是加密错了。
 *  密钥是公开的（学习通网页版自己就把它写在 JS 里），不是秘密。 */
const CX_AES_KEY: string = 'u2oh6Vu^HWe4_AES';

function bytesOf(s: string): Uint8Array {
  return new util.TextEncoder('utf-8').encodeInto(s);
}

function cxAesB64(pwd: string): string {
  const k: Uint8Array = bytesOf(CX_AES_KEY);
  return b64Of(aesCbc(true, k, k, bytesOf(pwd)));
}

// ------------------------------------------------------------------ 凭据

interface Cred {
  u: string;
  p: string;
}

interface CxCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
}

const K_CRED: string = 'cred';
const K_PORTAL: string = 'cred.portal';
const K_CXCOOKIE: string = 'cx.cookies';
const K_CXPWD: string = 'cx.pwd';
/** 完美校园那套状态（账号 / token / 设备列表）。安卓叫 saveWater/loadWater。 */
const K_WATER: string = 'water';
/** 财务处（校园统一支付平台）。**独立一格** —— 四套账号混一起必然串号。 */
const K_CW: string = 'cw';

function credJson(u: string, p: string): string {
  const o: Cred = { u: u, p: p };
  return JSON.stringify(o);
}

function isCx(c: JarCookie): boolean {
  return c.domain === 'chaoxing.com' || c.domain.endsWith('.chaoxing.com');
}

function dropCx(): void {
  const keep: JarCookie[] = [];
  for (let i = 0; i < JAR.length; i++) {
    if (!isCx(JAR[i])) {
      keep.push(JAR[i]);
    }
  }
  JAR = keep;
}

function cxHasLoginNow(): boolean {
  const now: number = Math.floor(Date.now() / 1000);
  for (let i = 0; i < JAR.length; i++) {
    const c: JarCookie = JAR[i];
    if (!isCx(c)) {
      continue;
    }
    if (c.expires > 0 && c.expires <= now) {
      continue;
    }
    if (c.name === 'UID') {
      return true;
    }
  }
  return false;
}

/**
 * 注入到 `window.Android` 的那个对象。
 *
 * 方法体里**不许出现 `this`** —— 见文件头那段。要加方法，先看那条规矩。
 */
// ------------------------------------------------------------------ 本机占用

function filesDirOf(): string {
  const ctx = appContext();
  return (ctx === null || ctx.filesDir === undefined) ? '' : ctx.filesDir;
}

function cacheDirOf(): string {
  const ctx = appContext();
  return (ctx === null || ctx.cacheDir === undefined) ? '' : ctx.cacheDir;
}

/** 目录里所有文件加起来多大。`depth` 是往下走几层（防止软链绕圈）。
 *  数不出来就按 0 —— 这是给「清缓存」提示用的数，不该因为它抛错把整页弄挂。 */
function dirBytes(dir: string, depth: number): number {
  if (dir.length === 0 || depth < 0) {
    return 0;
  }
  let names: string[] = [];
  try {
    names = fs.listFileSync(dir);
  } catch (e) {
    return 0;
  }
  let total: number = 0;
  for (let i = 0; i < names.length; i++) {
    const p: string = dir + '/' + names[i];
    try {
      const st = fs.statSync(p);
      if (st.isDirectory()) {
        total += dirBytes(p, depth - 1);
      } else {
        total += st.size;
      }
    } catch (e) {
      // 某个文件读不到不影响别的
    }
  }
  return total;
}

export class ReqBridge {
  // ---- 网络 ----

  /** native.js 老壳走这个（没有自定义头）。 */
  req(id: number, method: string, path: string, body: string, referer: string): void {
    start(id, method, path, body, referer, '');
  }

  req2(id: number, method: string, path: string, body: string,
       referer: string, headersJson: string): void {
    start(id, method, path, body, referer, headersJson);
  }

  // ---- 学习通 ----

  /** 密码加密。失败返回空串（JS 那边会如实说「加密失败」，不拿明文顶）。 */
  cxAes(pwd: string): string {
    try {
      return cxAesB64(pwd === undefined || pwd === null ? '' : pwd);
    } catch (e) {
      const err = e as Error;
      hilog.error(0x0000, TAG, '密码加密失败：%{public}s', err.message);
      return '';
    }
  }

  cxDumpCookies(): string {
    const out: CxCookie[] = [];
    for (let i = 0; i < JAR.length; i++) {
      const c: JarCookie = JAR[i];
      if (!isCx(c)) {
        continue;
      }
      out.push({
        name: c.name,
        value: c.value,
        domain: c.hostOnly ? c.domain : '.' + c.domain,
        path: c.path
      });
    }
    return JSON.stringify(out);
  }

  cxLoadCookies(json: string): number {
    try {
      const arr: CxCookie[] = JSON.parse(json) as CxCookie[];
      let n: number = 0;
      for (let i = 0; i < arr.length; i++) {
        const o: CxCookie = arr[i];
        if (o === null || o === undefined || o.name === undefined || o.name.length === 0) {
          continue;
        }
        const dom: string = (o.domain === undefined || o.domain.length === 0)
          ? 'chaoxing.com' : o.domain;
        JAR.push({
          name: o.name,
          value: o.value === undefined ? '' : o.value,
          domain: dom.replace(/^\./, '').toLowerCase(),
          hostOnly: !dom.startsWith('.'),
          path: (o.path === undefined || o.path.length === 0) ? '/' : o.path,
          expires: 0
        });
        n++;
      }
      return n;
    } catch (e) {
      hilog.warn(0x0000, TAG, 'cookie 还原失败');
      return 0;
    }
  }

  cxHasLogin(): boolean {
    return cxHasLoginNow();
  }

  /** 学习通 cookie 落盘（加密）。'' / 'soft' / 'fail: …' */
  cxSave(json: string): string {
    return sealPut(K_CXCOOKIE, json);
  }

  cxLoad(): string {
    return sealGet(K_CXCOOKIE);
  }

  cxClear(): void {
    rawDel(K_CXCOOKIE);
    dropCx();
  }

  cxClearCookies(): void {
    dropCx();
  }

  // ---- 教务 / 门户 / 学习通凭据 ----

  loadCred(): string {
    return sealGet(K_CRED);
  }

  /** '' = 存好了；'soft' = 存了但用的是本机密钥；'fail: …' = 没存上。 */
  saveCred(user: string, pass: string): string {
    return sealPut(K_CRED, credJson(user, pass));
  }

  credState(): string {
    return sealState(K_CRED);
  }

  clearCred(): void {
    rawDel(K_CRED);
  }

  loadPortalCred(): string {
    return sealGet(K_PORTAL);
  }

  savePortalCred(user: string, pass: string): string {
    return sealPut(K_PORTAL, credJson(user, pass));
  }

  portalCredState(): string {
    return sealState(K_PORTAL);
  }

  clearPortalCred(): void {
    rawDel(K_PORTAL);
  }

  loadCxPwd(): string {
    return sealGet(K_CXPWD);
  }

  saveCxPwd(user: string, pass: string): string {
    return sealPut(K_CXPWD, credJson(user, pass));
  }

  cxPwdState(): string {
    return sealState(K_CXPWD);
  }

  clearCxPwd(): void {
    rawDel(K_CXPWD);
  }

  // ---- cookie 维护 ----

  /** 罐子里某个名字的值（门户拿 `Authorization` 用）。 */
  cookie(url: string, name: string): string {
    const u: string = (url === undefined || url === null) ? '' : url;
    const host: string = hostOf(u);
    const path: string = pathOf(u);
    const now: number = Math.floor(Date.now() / 1000);
    for (let i = 0; i < JAR.length; i++) {
      const c: JarCookie = JAR[i];
      if (c.name !== name) {
        continue;
      }
      if (c.expires > 0 && c.expires <= now) {
        continue;
      }
      if (!hostMatch(host, c)) {
        continue;
      }
      if (c.path !== '/' && !path.startsWith(c.path)) {
        continue;
      }
      return c.value;
    }
    return '';
  }

  clearCookies(): boolean {
    JAR = [];
    return true;
  }

  /** 只清某个域名（含其子域）的 cookie。解绑门户别把教务会话一起清了。 */
  clearCookiesFor(host: string): boolean {
    const h: string = (host === undefined ? '' : host).replace(/^\./, '').toLowerCase();
    if (h.length === 0) {
      return false;
    }
    const keep: JarCookie[] = [];
    for (let i = 0; i < JAR.length; i++) {
      const c: JarCookie = JAR[i];
      const hit: boolean =
        c.domain === h || c.domain.endsWith('.' + h) || h.endsWith('.' + c.domain);
      if (!hit) {
        keep.push(c);
      }
    }
    JAR = keep;
    return true;
  }

  // ---- 完美校园（饮水）的账号存盘 ----
  //
  // 🔴 2026-11-14 用户截图报的「此版本不记住登录（需 0.24.0 以上）」就是这里漏了：
  //    完美校园那边**登录其实成功了**（拿到 token 了），紧接着要 `Android.saveWater`
  //    把状态存下来 —— 桥里没有这个方法，`wSave` 返回 'none'，于是抛出那句
  //    「此版本不记住登录」。屏幕上看像是「没登上」，实际是「存不下」。
  //    安卓那边一模一样的坑（NativeBridge 里那段注释），这里又踩了一次 ——
  //    说明「方法名单写两遍必然漏一处」这条规矩得靠判据守，不能靠记性。
  //
  //    存的是整段 JSON（账号、token、设备列表、上次同步时间），加密复用 Store，
  //    和账号密码同一套，不另抄一份。

  /** 空串 = 存好了；'soft' = 存了但用的是本机密钥（界面要能如实说）；其余是原因。 */
  saveWater(json: string): string {
    try {
      return sealPut(K_WATER, json === undefined || json === null ? '{}' : json);
    } catch (e) {
      return 'fail: ' + (e as Error).message;
    }
  }

  /** 取回 JSON。解不开返回空串（当作没登录），不把异常抛给 JS。 */
  loadWater(): string {
    try {
      return sealGet(K_WATER);
    } catch (e) {
      return '';
    }
  }

  /** "none" 没存过 / "ok" 存着且解得开 / "broken" 存着但解不开。 */
  waterState(): string {
    try {
      return sealState(K_WATER);
    } catch (e) {
      return 'none';
    }
  }

  clearWater(): void {
    rawDel(K_WATER);
  }

  // ---- 财务处（校园统一支付平台）----
  // 和完美校园同一个形状：整段 JSON 进、整段 JSON 出，加密复用 Store。
  // 存学号 + 密码 + 上次欠费快照 + 选过的房间。密码必须留：会话是 ASP.NET 的，
  // 二十分钟不动就过期，只存 cookie 等于每次打开都要重输。

  /** 空串 = 存好了；'soft' = 存了但用的是本机密钥；其余是原因。 */
  saveCw(json: string): string {
    try {
      return sealPut(K_CW, json === undefined || json === null ? '{}' : json);
    } catch (e) {
      return 'fail: ' + (e as Error).message;
    }
  }

  loadCw(): string {
    try {
      return sealGet(K_CW);
    } catch (e) {
      return '';
    }
  }

  /** "none" 没存过 / "ok" 存着且解得开 / "broken" 存着但解不开。 */
  cwState(): string {
    try {
      return sealState(K_CW);
    } catch (e) {
      return 'none';
    }
  }

  clearCw(): void {
    rawDel(K_CW);
  }

  // ---- 系统能力 ----

  /** 本机占用。给「清除本机缓存」那张卡用；数不出来就回 `{}`，
   *  界面那边 `storageUsage()` 会当成「没有这个能力」，不显示数字（和网页版一样）。 */
  storageInfo(): string {
    try {
      const info: Record<string, number> = {
        'apk': 0,
        'cache': dirBytes(cacheDirOf(), 3),
        'webview': 0,
        'data': dirBytes(filesDirOf(), 3),
        'total': 0,
        'free': 0
      };
      info['total'] = info['data'];
      return JSON.stringify(info);
    } catch (e) {
      return '{}';
    }
  }

  /** 交给系统浏览器（App 里点下载链接要用）。 */
  openUrl(url: string): void {
    const ctx = appContext();
    if (ctx === null || url === undefined || url === null || url.length === 0) {
      return;
    }
    try {
      const want: Want = {
        action: 'ohos.want.action.viewData',
        entities: ['entity.system.browsable'],
        uri: url
      };
      ctx.startAbility(want).catch((e: Error) => {
        hilog.error(0x0000, TAG, '打不开浏览器：%{public}s', e.message);
      });
    } catch (e) {
      const err = e as Error;
  /** 权限自查（通知 / 精确定时 / 省电白名单 / 后台任务）。
   *
   *  鸿蒙这边还没做（安卓见 NativeBridge.permState）。
   *  🔴 回**空串**，不能回 `{}`：app.js 的 permState() 解析不出对象就返回 null，
   *     权限那一行整块不出现；回空对象会让页面写「四项全没开」—— 那是假话，
   *     而这正是本项目最不能犯的错（没读到 ≠ 没有）。 */
  permState(): string {
    return '';
  }

  /** 跳到能改这一项的系统页（安卓见 NativeBridge.permOpen）。
   *  鸿蒙这边没做；返回一句能看懂的话 —— 用户点下去总得有个回话。 */
  permOpen(what: string): string {
    return '这个版本打不开系统设置';
  }
}
