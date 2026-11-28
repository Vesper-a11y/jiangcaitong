# 酱菜通 · 鸿蒙版

## 结论先放前面

**包能打出来了，但不是华为签的。**

- 本机（2026-11-14 装齐 SDK + hvigor + JDK 后）跑 `python mobile/harmony/build_hap.py`，
  会得到 `entry/build/default/outputs/default/jiangcaitong-signed.hap`（约 2.0 MB），
  签名工具自验通过（`verify-app → Verify success`）。
- 加 `--release` 打的是**正式版口径**：profile 换成 release 类型、渠道 stable、
  不带 v 序号、code 取「下一个正式版号」。产物是
  `jiangcaitong-signed-release.hap`。
- 两种包签的都是 **OpenHarmony 公版密钥**（SDK 自带 `toolchains/lib/OpenHarmony.p12`）。
  它们能装到 **OpenHarmony 设备 / 模拟器**上。
- **装到零售版 HarmonyOS 手机，要用「重签」这条路**：把这个 HAP 丢给带自己证书的
  签名工具（会用自己的华为账号证书重新签一遍），签完直接装。
  2026-11-14 在 HUAWEI Mate 70 Pro 上实测到「签名完成 → 开始安装」，安装那步报
  `code:9568276 error: install already exist`（同一个版本已经装过，不是签名问题）。
  ⇒ 零售机**可以装**，前提是先卸掉同名旧版本，或者把包内 code 提上去当升级。
- 真机（鸿蒙手机或 OpenHarmony 板子）我这台机器上**一台都没有**，所以下面的
  「已验证」只到编译与签名；「装上去跑起来」由用户在自己的 Mate 70 Pro 上验。

## 一、这版是什么

和 Android 版**同一份网页**，外面套一个鸿蒙的 Web 容器：

```
entry/src/main/ets/pages/Index.ets       Web 容器（全屏，mixedMode=All）
entry/src/main/ets/bridge/ReqBridge.ets  原生请求桥（Android.req / Android.req2）
entry/src/main/ets/entryability/…        入口
resources/rawfile/web/                   网页资源（打包时从 mobile/web 现拷，10 个文件）
```

网页里那 1500 行数据层（`native.js`）**一行没改**就复用了。它只依赖一个原生能力，
鸿蒙这侧照抄协议，逐字段与 Android 的 `NativeBridge.doReq` 一致：

```
JS   → Android.req2(id, method, path, body, referer, headers)
原生 → window.__nativeDone(id, "{\"status\":…,\"body\":…,\"url\":…,\"date\":…}")
```

为什么值得绕这一圈：登录、抓课表、认「这页是不是教务的报错页」这些规则，是这个
App 唯一被真实账号反复验证过的部分（吃了很多亏才对上）。在 ArkTS 里重写一遍，
等于把这些经验全部作废。

## 二、有什么、没什么

| 能力 | Android | 鸿蒙 |
|---|---|---|
| 课表 / 成绩 / 学习通 / 饮水 / 积分 | 有 | 有（同一条数据链路） |
| NFC 碰卡接水 | 有 | **没有** |
| 桌面小组件 | 有 | **没有** |
| 后台自动（JobScheduler） | 有 | **没有** |
| 通知提醒 | 有 | **没有** |

界面上这些入口会自动降级成「仅手机版可用」—— `app.js` 里每个原生对象都做了
`window.AndroidXxx &&` 判空，缺哪个就少哪块，不会白屏。

## 三、怎么出包

### 用 DevEco（推荐，也只有这条能签出华为的包）

1. 装 [DevEco Studio](https://developer.huawei.com/consumer/cn/deveco-studio/) 5.0 以上
2. 打开 `mobile/harmony`
3. File → Project Structure → Signing Configs → 勾「Automatically generate signature」
   （要登录华为开发者账号；调试包还要先把手机 UDID 加进去）
4. Build → Build Hap(s)

### 命令行（本机已验证）

```bash
python mobile/harmony/build_hap.py            # 测试版：debug profile
python mobile/harmony/build_hap.py --release  # 正式版：release profile + 正式渠道
python mobile/harmony/build_hap.py --no-sign  # 只编译
python mobile/harmony/build_hap.py --copy     # 只拷资源
```

### 正式版和测试版差在哪

不是只有文件名不同。混用会得到一个「签得上、装不上」或者「装上但版本对不上」的包：

| | 测试版（默认） | 正式版（`--release`） |
|---|---|---|
| profile 模板 | `UnsgnedDebugProfileTemplate.json` | `UnsgnedReleasedProfileTemplate.json` |
| `type` | `debug` | `release` |
| 证书字段 | `development-certificate` | `distribution-certificate` |
| profile 签名别名 | `… profile debug` | `… profile release` |
| 包内渠道 | 跟 `app.js` 一致（现在 `beta`） | 改成 `stable`、v 序号归 0 |
| 包内 code | 跟 `app.js` 一致 | 「下一个正式版号」 |

版本号仍然只有一个来源：**进包的那份 `app.js`**。正式版时脚本在
`rawfile/web/app.js` 这份**副本**上改渠道与 code，`mobile/web/app.js` 一个字不动
（源里的渠道归 Android 的发版流程 `beta.py release` 管）—— 在那里顺手改，
测试版回归会一起红，而且两边的号各说各话。

「下一个正式版号」的算法和 `beta.py` 的 `pick_code` 一样：比当前 code 和每个
测试版都大 1。所以现在（源里 `0.52.2 / 159`、台账里还有 158）打出来是
**`0.52.2 / 160`**。源里已经是 `stable` 时直接用它的号，不再 +1 —— 否则重跑
一次涨一格，全是没发出去的号。

🔴 正式版包里**没有数据台**（`stats.js` / `stats.css` 不进包，`index.html` 里
四段 `<!-- @stats -->` 剪掉）。这一点测试版也一样：控制台从来不进 HAP。

脚本自己找三样东西，找不到就明说缺什么，不硬编：

| 需要 | 从哪找 |
|---|---|
| SDK（要 `12/ets`、`12/toolchains`） | `$OHOS_SDK_HOME` → `local.properties` 的 `sdk.dir` → 工作区 `_lab/out/_ohsdk/sdk2` |
| JDK 17（签名工具是 jar） | `$JAVA_HOME` → 工作区 `_lab/out/_jdk/jdk-*` → DevEco 的 `jbr` |
| hvigor | `$HVIGOR_JS` → 工作区 `_lab/out/_hvigor` → DevEco 的 `hvigorw.bat` |

`local.properties`、`.sign/`、`entry/build/` 都是本机的，不进仓库。

## 四、签名这件事的三个坑（都踩过了）

1. **`keytool -exportcert -alias "openharmony application release"` 导出来的是自签证书**，
   和 CA 对不上，`sign-app` 会报 `verify certificate chain failed! Signature does not
   match.`。正确做法是用 `UnsgnedDebugProfileTemplate.json` 里那张（CA 签过的）leaf，
   再从 p12 里导出 `openharmony application ca` 拼成链。
2. **模板里的 `validity.not-after` 是 2024-01，早就过期了**，照抄会装不上。
   脚本每次按当天时间写 `now-1h ~ now+10y`。
3. **模板带 `debug-info.device-ids`**（绑 UDID）。不删掉，只有那两台设备能装。
   脚本里 `prof.pop("debug-info")`。
4. **两套素材是一整套，不能拆开混用**：模板 / 证书字段 / 签名别名 / `type`
   四样必须同属 debug 或同属 release。只换模板不换别名，`sign-profile` 照样
   报 success，但签出来的 profile 里那张 `distribution-certificate` 和
   `sign-app` 用的私钥不是一对 —— 装的时候才报签名校验失败，很绕。
   另外 release 模板自带 `version-name: 2.0.0` / `version-code: 2`，不写就是我们
   的包在系统设置里显示 2.0.0，而「我的」里写着 0.52.2。

## 五、真机第一件事：验这四条

> 2026-11-14 更新：这四条当时是「都还没验」。当天下午用户就在 Mate 70 Pro 上
> 报了一句「**所有账号都完全无法登录**」—— 命门果然是第一条和第三条一起踩了。
> 修完（code 161）装上去，用户报的变成了两句很具体的话：
> 「密码加密失败，手机系统的加密模块不可用」（学习通）和
> 「登录没成功，学校那边没给原因」（教务）。第二轮修的是 ④ 和一处从没实现的桥：
> 学习通的 `cxAes` 压根没注册；教务那边 POST 没带上会话 cookie。
> 现在 ①②③④ 都有判据（`check_hap.py` 的 [D]/[E] 组，配 15 条变异），
> 但「在真机上点一遍」这件事，仍然得有人拿着手机做。

1. **`window.Android` 到底在不在** —— 它不在，`native.js` 开头那句
   `if (!window.Android …) return;` 会让整个数据层**一声不响地不加载**：
   界面看着正常，点登录走的是网页版那条路（App 里没有 /api），于是**一个
   账号都登不上，而且不报错**。现在 `Index.ets` 在 `onPageEnd` 里自查一次，
   没挂上就手动 `registerJavaScriptProxy` 再刷新一次；桥的 `req`/`req2`
   也改成**不用 `this`**（ArkWeb 回调不保证绑 `this`，绑不上就 TypeError）。
2. **`window.__nativeDone` 有没有到** —— `ReqBridge.finish()` 那句 `runJavaScript`
   是命门。它不通，界面上所有数据都是空的，而且**不报错**（Promise 永远挂着）。
   现在回执送不到会隔 300ms 再试一次，仍然失败就写 hilog。
3. **明文 HTTP 有没有被拦** —— 教务是 http 明文，鸿蒙默认只放
   https，而且 `INTERNET` 权限**管不了这件事**（两回事）。
   ⚠️ 2026-11-14 第三轮之后：`network_config.json` + metadata 那套**照旧留着，但不再
   指望它** —— 各家文档对 metadata 的 `name` 写法互相矛盾（GitHub 上 18 个真实工程
   用了七种），配了也不一定生效。现在 `http://` 的请求**自己拼 HTTP/1.1 走 TCP**
   （`ReqBridge.ets` 的 `wireOnce`），明文拦截在 `@ohos.net.http` 这层，socket 不经过它。
   真机要看的只有一件事：**绑学号那一屏还报不报「连不上教务系统」**。
   如果还报，去「我的 → 问题反馈 → 预览」，日志里有 `接口失败 /api/…` 那一行的原文
   （会写成 `明文通道 …；系统通道 …`），把那句拍照发来就能定位是「被拦」还是「真到不了」。
4. **cookie 罐子够不够** —— 鸿蒙的 `http` 不带 CookieHandler，这里自己存。
   三件事缺一不可：按**域名 + 路径**匹配（教务/学习通/慧生活都用 `JSESSIONID`；
   `.chaoxing.com` 还要能带给所有子域）、把 `Path=/` 这类**属性**从 cookie 里剔掉、
   响应头的多条 `set-cookie` 要能拆开。判据咬的是「认不认子域」。
   ⚠️ 一个可复现的旁证：拿假账号直连教务，**带 cookie 回 200 + 6040 字节**，
   **不带 cookie 回 404 + 1874 字节** —— 界面上一律是「学校那边没给原因」。
5. **一屏装下** —— 鸿蒙字体度量比 Android 略宽，`check_one_screen_real`（360×712）
   要在真机重量一遍。

## 五之二、原生实现了哪些方法

只实现 `req`/`req2` 是不够的 —— 网页那边调一个不存在的原生方法，`try/catch`
一兜就变成一句人话（「密码加密失败」「还没连」），看着像功能没做，其实整条路断了。
现在这些都有：网络 `req/req2`，学习通 `cxAes/cxDumpCookies/cxLoadCookies/
cxHasLogin/cxSave/cxLoad/cxClear/cxClearCookies`，账号密码
`loadCred/saveCred/credState/clearCred`（教务）、`loadPortalCred/…`（门户）、
`loadCxPwd/…`（学习通），cookie `cookie/clearCookies/clearCookiesFor`，
系统 `openUrl`。

`Index.ets` 里那张 `METHODS` 名单和 `ReqBridge` 的方法**必须一一对应**，
判据直接比这两个集合 —— 名单少一个，那个方法在 JS 里就是 `undefined`，
而且没有任何报错。

## 六、不建议走的捷径

- ❌ 用 ArkTS 重写界面 —— 几十条像素级判据会全部失效，且改一处忘一处。
- ❌ 把 `native.js` 翻成 ArkTS —— 那是唯一验证过的部分，翻译一遍就没了。
- ❌ 给没实现的桥一个假实现 —— 不报错、只是永远不动，用户以为坏了。
  宁可让入口写着「仅手机版可用」。
