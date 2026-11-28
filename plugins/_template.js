/* 酱菜通插件模板 —— 复制这个文件，改掉里面的字就是一个插件。
 *
 * 🔴 开头这三行注释是给市场卡片用的（改完记得一起改）：
 *    @name  卡片标题，最多 20 字
 *    @tag   右上角那个小标签，最多 10 字
 *    @desc  一句话简介，最多 90 字
 *    不写也能上架，只是名字会退回用目录名、标签写「插件」。
 *
 * 清单（plugins/index.json）**不用你写**：合并进 main 之后，仓库会自己算出字符数和
 * sha256 写进去，提交人是你的 GitHub 帐号。你只管交这一个 .js 文件。
 *
 * 插件能干的事（想用哪个用哪个，也可以一起用）：
 *
 *   ① 在生活页画一张卡      html / css        （写函数就每次重画都重新求值）
 *   ② 有自己的整页界面      page{ html, css, on, title, refresh }
 *   ③ 改 App 的外观         style             （全局 CSS，卸载自动还原）
 *   ④ 调宿主：路由、页面、底部导航、配置、原生   plugin.host
 *
 * 🔴 插件是放开的，不是只有 ①②③：`plugin.host` 里是整套宿主，随便调。
 *    插件代码本身就跑在宿主作用域里，switchTab('me')、store.get(...)、CFG 这些
 *    全局的东西直接写也能用（plugin.host 只是给你个整齐的入口）。
 *
 *    plugin.host.route.go('me')            切到某一页（'schedule' 'exams' 'campus' 'me' …）
 *    plugin.host.route.back()              从插件页返回
 *    plugin.host.route.cur()               现在是哪一页
 *    plugin.host.route.add({name,title,html,css,back})
 *                                          注册一整页：切过去就能看到，左上角自带返回
 *    plugin.host.route.remove(name)        撤掉这一页
 *    plugin.host.route.list()              App 现在有哪些页面
 *
 *    plugin.host.nav.add({tab,label,icon,before})
 *                                          底部加一格（tab 要和你注册的页面同名，点了才切过去）
 *    plugin.host.nav.remove(tab)           去掉一格
 *    plugin.host.nav.setLabel(tab,文字)     改某一格的字
 *    plugin.host.nav.setIcon(tab,svg)      换某一格的图标
 *    plugin.host.nav.list()                底部现在有哪几格
 *
 *    plugin.host.cfg.all()                 整份设置（对象，直接改）
 *    plugin.host.cfg.get(k) / set(k,v)     读 / 改一项
 *    plugin.host.cfg.save()                改完调这个才落盘
 *
 *    plugin.host.store.get / set / del     本机存储（可以存任意键）
 *    plugin.host.store.keys()              本机都存了些什么
 *
 *    plugin.host.native.*                  原生能力（下载、日历、蓝牙、通知……）
 *    plugin.host.android.*                 原生桥接本体
 *    plugin.host.win / doc / $ / $$        浏览器那一套
 *    plugin.host.toast(文字)               弹一句
 *
 *    还有原来那几个顺手的小接口：
 *    plugin.data(键, 值)   存 / 读自己的数据（只写一个参数就是读）
 *    plugin.week()         现在是第几周
 *    plugin.total()        这学期一共几周
 *    plugin.today()        今天 { m, d, w, text }
 *    plugin.back()         从自己那一页返回生活页
 *
 * 🔴 两条底线（写插件的人要心里有数）：
 *    1. 不能删、不能覆盖 App 本体的源码和安装包。这两样在手机上是只读的，
 *       你也没有任何写文件/删文件的入口 —— 想干也干不了，别在这上面花时间。
 *    2. 卸载插件时，它带来的所有改动都会被清干净：你经 host 写的每个存储键
 *       App 都记着安装前的原值，卸载时逐个还回去；页面、导航、塞进界面的东西
 *       都靠卸载后重载一次清掉。所以放心改，用户删掉就回原样。
 *
 * 交互怎么写：HTML 里给元素写 data-act="名字"，再在 on 里放同名函数。
 * 点完 App 会自动重画一次 —— 在 on 里改完 plugin.data，界面自己就更新了。
 *
 * 样式怎么写：
 *   · 卡片和 page 的 css 会自动加前缀，写 body{…} 只影响你自己；
 *     @media 里照样生效。想贴边就写 .plgv{padding:0} / .plgpg{padding:0}。
 *   · 想跟 App 配色一致就用这些变量（浅色深色自动切）：
 *       var(--text) 正文   var(--sub) 次要字   var(--line) 分隔线
 *       var(--card) 卡面   var(--accent) 主色  var(--accent-soft) 主色淡底
 *   · 也能直接用 App 现成的类：class="btn primary block"、class="card"、class="row"。
 *   · style（外观插件）**不做**作用域 —— 要换肤就得改整页，写好自己先看一眼。
 *
 * 写好后在 App 里点「插件市场 → 我也做一个」，会打开 GitHub 新建文件页：
 * 贴进去、路径写成 plugins/<id>/<版本>.js，提交就是一个 PR。
 * 我看过之后合进去（同时补 index.json 那一行）就上架了。
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

/* ④ 要自己的整页、要底部多一格 —— 放在 plugin({...}) 外面写（取消注释就能跑）：
 *
 * // 注册一整页：切过去就能看到，左上角自带返回，返回去哪由 back 定
 * plugin.host.route.add({
 *   name: 'ecroom',
 *   title: '空教室',
 *   html: '<p class="ec-h">今天 A101、B203 空着</p>',
 *   css: '.ec-h{font-size:15px;color:var(--text)}',
 *   back: 'campus'
 * });
 *
 * // 底部加一格，点了切到那一页（tab 必须和上面注册的 name 一样）
 * plugin.host.nav.add({ tab: 'ecroom', label: '空教室', before: 'me' });
 *
 * // 改一项设置，记得 save 才落盘
 * plugin.host.cfg.set('ecOn', true);
 * plugin.host.cfg.save();
 *
 * // 插件卸载时这些都会被清掉（存储键还原、页面和导航随重载消失），不用自己收尾。
 */
