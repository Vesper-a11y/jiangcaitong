/* 检查更新专测（2026-11-25）
 * 覆盖：Release 直链拿到 json → 解析（GitHub latest 主 / Gitee raw 备）；回登录页 HTML → 不当清单；
 *       主地址失败 → 备地址兜底；两套字段名都认；启动自动检查不改变界面。
 * 跑法：node tests/update.test.js（项目根目录，jsdom 从 node_modules 解析）
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.join(__dirname, '..');
const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };

// 用假 Android 起一份只含 native.js 的 DOM；按 URL 决定回什么。
function makeNative(answer) {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const w = dom.window;
  w.Android = {
    req(id) {
      const body = answer.apply(null, arguments);
      w.__nativeDone(id, JSON.stringify(body));
    },
    req2(id, method, url) {
      const body = answer(url);
      w.__nativeDone(id, JSON.stringify(body));
    },
  };
  w.eval(fs.readFileSync(path.join(ROOT, 'web/native.js'), 'utf8'));
  return w;
}

const GITEE_JSON = JSON.stringify({
  versionName: '1.2.0', versionCode: 3,
  downloadUrl: 'https://gitee.com/fangqiang/jiangcaitong/releases/download/v1.2.0/app.apk',
  changelog: ['新增自动检查更新', '修好几个问题'],
});
const COS_JSON = JSON.stringify({
  version: '1.1.0', code: 2,
  url: 'https://cos.example.com/app.apk', notes: '切到腾讯云的第一版',
});

async function main() {
  // 1) 主地址（Gitee）正常
  {
    const w = makeNative((url) => url.indexOf('gitee.com') >= 0
      ? { status: 200, body: GITEE_JSON, url }
      : { status: 404, body: '', url });
    const r = await w.NativeApi.handle('/api/update/check', {
      urls: ['https://gitee.com/a/b/releases/download/v1.0.0/version.json'],
    });
    ok(r.ok && r.info.version === '1.2.0' && r.info.code === 3,
      'Gitee 格式 json（versionName/code/url）没解析对：' + JSON.stringify(r));
    ok(r.info.notes.length === 2 && /自动检查/.test(r.info.notes[0]),
      '更新日志没解析成数组');
    w.close();
  }

  // 2) 私有仓库回登录页 HTML（status 200）→ 必须当失败，转备地址
  {
    const w = makeNative((url) => url.indexOf('gitee.com') >= 0
      ? { status: 200, body: '<html>请先登录 Gitee</html>', url }
      : { status: 200, body: COS_JSON, url });
    const r = await w.NativeApi.handle('/api/update/check', {
      urls: [
        'https://gitee.com/a/b/releases/download/v1.0.0/version.json',
        'https://cos.example.com/version.json',
      ],
    });
    ok(r.ok && /cos\.example\.com/.test(r.info.source),
      '登录页 HTML 被当成清单 / 备地址没兜底：' + JSON.stringify(r));
    ok(r.info.version === '1.1.0' && r.info.notes.length === 1,
      'COS 老格式 json（version/code/url/notes）没解析对');
    ok(r.tried && r.tried.length === 2 && r.tried[0].ok === false,
      '尝试记录没写清第一跳失败');
    w.close();
  }

  // 3) 两个地址都失败 → 明确回 ok:false，界面照用户话术提示
  {
    const w = makeNative((url) => ({ status: 404, body: 'not found', url }));
    const r = await w.NativeApi.handle('/api/update/check', {
      urls: ['https://gitee.com/a/b/r/version.json', 'https://cos.example.com/version.json'],
    });
    ok(!r.ok && /检查不了/.test(r.error), '双失败没回人话错误：' + JSON.stringify(r));
    w.close();
  }

  // 4) 版本号比较：code 优先，其次按点分段
  {
    const dom = new JSDOM('<!doctype html><html><body></body></html>', {
      url: 'http://localhost/', runScripts: 'outside-only',
    });
    const w = dom.window;
    w.NativeApi = {};
    w.eval('var APP_CODE = 1; var APP_VERSION = "1.0.0";');
    w.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8').match(
      /function updNewer[\s\S]*?\n}/)[0]);
    ok(w.updNewer({ code: 2, version: '1.0.0' }, '1.0.0') === true,
      'code 更大就该提示新版本');
    ok(w.updNewer({ code: 0, version: '1.10.0' }, '1.9.0') === true,
      '按点分段比大小错了（1.10.0 应大于 1.9.0）');
    ok(w.updNewer({ code: 0, version: '1.0.0' }, '1.0.0') === false,
      '同版本不该提示新版本');
    w.close();
  }

  console.log(fails.length ? ('FAIL: ' + fails.join(' | ')) : 'PASS: 检查更新（GitHub latest 主/Gitee raw 备 + 双格式 + HTML 拦截 + 版本比较）');
  process.exitCode = fails.length ? 1 : 0;
}
main();
