/* 一次跑完 tests/ 下所有 *.test.js（跨平台：不依赖 shell 的通配符展开）。
 *
 * 为什么要它：`npm test` 原来只跑 smoke + update 两条，CI 里 PR 检查看起来
 * 全绿，其实课表、AI 工具、生活组件这些真行为的用例一条都没跑。
 * 这里按文件名排序依次跑，谁挂了就停，退出码直接给 CI。
 */
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const dir = __dirname;
const files = fs.readdirSync(dir)
  .filter((f) => f.endsWith('.test.js'))
  .sort();

if (!files.length) {
  console.error('tests/ 下没有 *.test.js');
  process.exit(1);
}

let bad = [];
for (const f of files) {
  process.stdout.write('\n=== ' + f + '\n');
  const r = spawnSync(process.execPath, [path.join(dir, f)], { stdio: 'inherit' });
  if (r.status !== 0) bad.push(f);
}

console.log('\n──────── 汇总 ────────');
console.log('共 ' + files.length + ' 个用例文件，失败 ' + bad.length + ' 个');
if (bad.length) {
  bad.forEach((f) => console.log('  ✗ ' + f));
  process.exit(1);
}
console.log('全部通过');
