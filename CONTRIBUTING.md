# 参与贡献

欢迎提 Issue 和 PR。这是一个江西财经大学的**非官方个人项目**，几点约定请先看。

## 提 Issue

- 教务/学习通/住理/智慧江财的问题，请说明**在哪个页面、点了什么、期望什么、实际什么**。
- 🔴 **不要贴真实姓名、学号、一卡通号、宿舍号、成绩截图**。要贴就打码，或换成示例值（学号用 `2202500000`）。
- 登录 related 问题说明是扫码登录还是 Cookie 失效，不必给账号密码。

## 交插件（不用改 App 源码）

插件就是一段 `plugins/<id>/<版本>.js`，App 从本仓库下载后当代码跑，所以规矩比普通 PR 更硬：

1. 在 App 里点「插件市场 → 我也做一个」，会直接打开本仓库 `plugins/` 的新建文件页；
   也可以手动在 GitHub 上新建 `plugins/<id>/<版本>.js`。提交时 GitHub 会自动开 PR。
2. 插件能干三件事，用哪个都行，也可以一起用：
   - **生活页画一张卡**：`html`（字符串或函数）+ `css`；
   - **有自己的整页界面**：`page: { title, html, css, on }` —— 点卡片进去就是它自己的页面；
   - **改 App 外观**：`style`（全局 CSS，卸载自动还原）。

   CSS 会自动圈在你自己那块里（卡片写 `body{…}` 也只影响自己），外观的 `style`
   是唯一故意不圈的。点击用 `data-act="名字"` 接到 `on` 里同名函数。

   插件是**放开的**：`plugin.host` 里是整套宿主 ——
   路由 `route.go / back / cur / add / remove`（`add` 能注册一整页）、
   底部导航 `nav.add / remove / setLabel / setIcon`、配置 `cfg.get / set / save`、
   本机存储 `store`、原生能力 `native`、还有 `win` / `doc` / `$` / `$$`。
   想加一整页、想在底部多一格、想换配色，都不用改 App 源码。
   （原来的 `plugin.toast / data / week / total / today / back` 照旧能用。）

   🔴 两条底线，App 里是硬的，不是口头约定：
   ① **不能删、覆盖 App 本体的源码和安装包** —— 这两样在手机上是只读的，
      插件也没有任何写文件 / 删文件的入口；
   ② **卸载时全部复原** —— 插件经 `host` 写过的每个存储键都留了安装前的原值，
      卸载时逐个还回去；页面、底部导航、塞进界面的东西靠卸载后重载一次清掉。
      放心改，用户删掉就回原样。

   HTML 里的 `<script>`、`onclick` 这类行内事件会被摘掉（插件自己有代码入口，
   不必借 HTML 再插一段）。
   不写 `html` / `page` 的老写法（`stats` / `state` / `action`，由 App 画）也还支持。
   这些样子都在 `plugins/_template.js` 里，照抄改改就能用。
3. 文件开头写上这几行注释，市场卡片就用它们（不写也能上架，名字退回用目录名、
   标签写「插件」）：

   ```js
   /* @name 空教室
    * @tag 教室
    * @desc 今天哪几间空着，点一下记住 */
   ```

4. **不用自己写 `plugins/index.json`**。合并进 `main` 之后，仓库用
   `tools/sync_index.py` 算出字符数和 sha256，把
   `id / name / tag / desc / ver / code / file / size / sha256 / by / at`
   一起写进清单。清单里的数必须和文件对得上，否则 App 不装 —— 这一步就是替你算，
   所以别手改清单，手写的也会被脚本覆盖。
5. 版本号就是文件名：`plugins/<id>/1.0.0.js`。改了内容要换新版本号（新文件名），
   旧的可以留着。`code` 由脚本在内容变化时自动 +1，App 靠它比新旧。
6. 合并进 `main` 就等于上架。**维护者会先看一遍再合** —— 这个仓库里有 App 全部源码，
   一个 PR 能改的不只是插件。

## 提 PR

### 先说清楚红线

以下几处改动会触发自动检查，也会人工逐行审：

1. **`version.json` / 更新地址**：`downloadUrl` 只能指向本仓库的 GitHub / Gitee Release。
   指向外部域名一律不合并——这是更新劫持风险。
2. **`.github/workflows/`**：尤其 `sync-to-gitee.yml` 用了 `GITEE_TOKEN` 令牌。
   任何把令牌打印、外发、写进日志的改动都会被拒绝。
3. **`web/app.js` 的 `UPDATE_*` 常量**：更新源地址，同上。
4. **隐私字面值**：代码、测试、注释里都不要出现真实身份信息，CI 会扫描并 fail。

### 开发环境

```bash
npm install
npm test                        # smoke + update 两套，必须全绿
node --check web/app.js
node --check web/native.js
node --check web/jxufe_native.js
```

改教务解析 logic 时，优先用 `jxufe_samples/` 的离线样本回归（样本含隐私、不进公开仓库），比等真机快。

### 版本号

**贡献者不用改版本号**。版本号只由维护者在 `android/build_apk.py` 一处维护，打包时自动同步其余三处。

### 流程

1. Fork → 建分支 → 改动 → 本地跑通测试。
2. 提 PR 到 `main`，勾选下方清单。
3. CI 全绿 + 维护者 review 后合并。合并到 `main` 会自动同步到 Gitee。

### 🔴 给维护者：合并 PR 后必须同步回原仓库

本项目是双仓库结构：开发在 `.work-hangyuantong`（含隐私，永不 push），
公开的是从它导出的干净快照 `github-ready`。**PR 改的是 GitHub 上的快照，原仓库并不知道。**

所以每次合并 PR 后，维护者必须把改动**手动应用回 `.work-hangyuantong`**，
否则下次发新版跑 `重建公开快照.py` 时，PR 的改动会被整个覆盖，白合并。

推荐：合并后用 `Squash and merge`（一个功能一个提交，方便 cherry-pick 回原仓库）。

## 行为准则

- 不讨论政治、学校立场等无关话题。
- 不提交任何可用于绕过学校系统鉴权、批量抓取他人数据的代码。
- 项目定位是"帮你看，不替你改"，只读不改的边界请保持。

---

有问题先开 Issue 讨论，避免白做。
