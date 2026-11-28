/* 酱菜通插件模板 —— 复制这个文件，改掉里面的字就是一个插件。
 *
 * 插件能干三件事，想用哪个用哪个，也可以一起用：
 *
 *   ① 在生活页画一张卡      html / css        （写函数就每次重画都重新求值）
 *   ② 有自己的整页界面      page{ html, css, on, title, refresh }
 *   ③ 改 App 的外观         style             （全局 CSS，卸载自动还原）
 *
 * 底下是「①②一起用」的例子。只想要外观就从 ③ 那段抄。
 *
 * 能用的接口（都是只读的，改了也不会影响 App 别处）：
 *   plugin.toast(文字)      弹一句提示
 *   plugin.data(键, 值)     存 / 读自己的数据（只写一个参数就是读）
 *   plugin.week()           现在是第几周
 *   plugin.total()          这学期一共几周
 *   plugin.today()          今天 { m, d, w, text }，text 形如「11月28日 周六」
 *   plugin.back()           从自己那一页返回生活页
 *
 * 交互怎么写：HTML 里给元素写 data-act="名字"，再在 on 里放同名函数。
 * 点完 App 会自动重画一次 —— 在 on 里改完 plugin.data，界面自己就更新了，
 * 不用自己去刷。
 *
 * 样式怎么写：
 *   · 卡片和页面的 css 会自动加前缀，写 body{…} 只影响你自己，写 .x 只有你认；
 *     @media 里照样生效。想贴边就把 css 写成 .plgv{padding:0} / .plgpg{padding:0}。
 *   · 想跟 App 配色一致就用这些变量（浅色深色自动切）：
 *       var(--text) 正文   var(--sub) 次要字   var(--line) 分隔线
 *       var(--card) 卡面   var(--accent) 主色  var(--accent-soft) 主色淡底
 *   · 也能直接用 App 现成的类：class="btn primary block"（整行主按钮）、
 *     class="card"、class="row"…… 长得就和 App 自己的一模一样。
 *   · 外观插件的 style **不做**作用域（要改就改整页），写好之后自己先看一眼。
 *
 * HTML 里不能带 <script>、onclick 这类行内事件（会被摘掉）。
 * 不联网。要联网取数据的话，先提需求，App 里加好再给你用。
 *
 * 写好后在 App 里点「插件市场 → 我也做一个」，会打开 GitHub 新建文件页：
 * 贴进去、路径写成 plugins/<id>/<版本>.js，提交就是一个 PR。
 * 我看过之后合进去（同时补 index.json 那一行，写上 by / at）就上架了。
 */
plugin({
  name: '空教室',
  tag: '自习',

  /* ① 生活页那张卡：只画个摘要，点进去看全部。 */
  html: () => '' +
    '<div class="ec-top">' +
      '<b>' + (Number(plugin.data('n')) || 0) + '</b><span>间空着</span>' +
    '</div>' +
    '<p class="ec-tip">点开看是哪几间</p>',
  css: [
    '.ec-top{display:flex;align-items:baseline;gap:6px;padding:2px 0 6px}',
    '.ec-top b{font-size:34px;font-weight:700;color:var(--text);line-height:1}',
    '.ec-top span{font-size:13px;color:var(--sub)}',
    '.ec-tip{margin:0;font-size:12px;color:var(--sub)}'
  ].join('\n'),

  /* ② 点卡片进来的一整页。想怎么排就怎么排。 */
  page: {
    title: '空教室',
    html: () => {
      const list = plugin.data('list') || ['A101', 'B203', 'C305'];
      return '' +
        '<p class="ep-h">今天空着的（示例数据）</p>' +
        '<div class="ep-list">' +
          list.map((r, i) =>
            '<button class="ep-row" data-act="pick" data-i="' + i + '">' +
              '<b>' + r + '</b><span>点一下记住它 ›</span>' +
            '</button>').join('') +
        '</div>' +
        '<button class="btn primary block" data-act="back">回生活页</button>';
    },
    css: [
      '.ep-h{margin:2px 0 10px;font-size:13px;color:var(--sub)}',
      '.ep-list{display:flex;flex-direction:column;gap:10px;margin-bottom:16px}',
      '.ep-row{display:flex;justify-content:space-between;align-items:center;',
      '  padding:16px;border:0;border-radius:16px;background:var(--card);',
      '  box-shadow:var(--shadow-card);font-size:16px;color:var(--text)}',
      '.ep-row:active{opacity:.7}',
      '.ep-row span{font-size:12px;color:var(--sub)}'
    ].join('\n'),
    on: {
      pick(el) {
        plugin.data('picked', el.querySelector('b').textContent);
        plugin.toast('记住了：' + plugin.data('picked'));
      },
      back() { plugin.back(); }
    }
  }

  /* ③ 只改外观的插件长这样（想试就取消注释、删掉上面那些）：
   *
   * style: [
   *   ':root{--accent:#123456;--accent-soft:#e8eefb}',
   *   '.lifew{border-radius:22px}'
   * ].join('\n')
   */
});
