# 参与贡献

欢迎提 Issue 和 PR。这是一个江西财经大学的**非官方个人项目**，几点约定请先看。

## 提 Issue

- 教务/学习通/住理/智慧江财的问题，请说明**在哪个页面、点了什么、期望什么、实际什么**。
- 🔴 **不要贴真实姓名、学号、一卡通号、宿舍号、成绩截图**。要贴就打码，或换成示例值（学号用 `2202500000`）。
- 登录 related 问题说明是扫码登录还是 Cookie 失效，不必给账号密码。

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
