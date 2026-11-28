/* 学期进度 —— 示例插件（U118 上架的第一个）
 * 只读：本周第几周、还剩几周、今天几号。不联网，不改任何设置。 */
plugin({
  name: '学期进度',
  tag: '这周第几周',
  stats: () => {
    const w = plugin.week();
    const t = plugin.total();
    return [
      { k: '本周', v: w > 0 ? '第 ' + w + ' 周' : '—' },
      { k: '还剩', v: (t && w) ? Math.max(0, t - w) + ' 周' : '—' }
    ];
  },
  state: () => '● ' + plugin.today().text,
  action: {
    label: '看课表',
    run() { plugin.toast('现在是第 ' + plugin.week() + ' 周，这学期共 ' + plugin.total() + ' 周'); }
  }
});
