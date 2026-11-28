/* =====================================================================
 * AI 助手（U95⑦，2026-11-28）—— 独立的对话 + 工具调用模块
 * =====================================================================
 * 为什么要单独一个文件：
 *   app.js 已经 1MB，Function Calling 的 schema / 对话状态机 / 工具白名单
 *   塞进去只会更难维护。这里只依赖三样东西：window.NativeApi（HTTP 通道）、
 *   DOM（对话弹层）、app.js 启动时注入的工具实现（registerTools）。

 * 🔴 隐私口径（用户 2026-11-28 拍板，README 已同步明示）：
 *   · 只有用户主动开启 AI、并填了自己的服务密钥之后才会发请求；
 *   · 对话内容 + 工具查询结果会发给用户自己填的服务商（DeepSeek）；
 *   · 每次只发工具需要的最小字段：课表只发「第几周周几第几节什么课」，
 *     电费只发金额数字，绝不把账号 / 登录凭据 / 一卡通号写进对话；
 *   · 密钥只存本机 localStorage，不随请求回传，也不显示在界面上。

 * 🔴 工具白名单是硬边界：模型说要调什么，都先过 ALLOWED 校验，
 *    清单外的一律拒答，绝不照模型的话执行。高危工具（开水 / 签到）必须先弹
 *    用户确认，确认了才执行 —— 这一层不可绕过。

 * 数据流：
 *   用户说话 → chat() → 发 messages + tools 给模型
 *     → 没有 tool_calls：直接显示回答
 *     → 有 tool_calls：逐个白名单校验 → 查询类直接跑 / 高危类弹确认
 *     → 把工具结果作为 tool 消息回灌 → 再问一次模型 → 显示最终回答
 * ===================================================================== */
(function () {
  'use strict';

  var KEY_SETTING = 'zt.aikey';        // 密钥（本机）
  var KEY_MODEL = 'zt.aimodel';        // 选中的模型（本机）
  var KEY_HISTORY = 'zt.aichat';       // 对话记录（本机，最多 40 条）
  var API_URL = 'https://api.deepseek.com/chat/completions';
  // 2026-11-28：用户指出 deepseek-chat 是老模型，要用 V4.1 Flash。
  // 官方 API 名就是 deepseek-flash（V4.1 Flash，2026-09 上线）；
  // deepseek-chat 留作旧版兼容选项，默认走新的。
  var MODELS = [
    { id: 'deepseek-flash', label: 'V4.1 Flash · deepseek-flash（推荐）' },
    { id: 'deepseek-chat', label: '旧版 Chat · deepseek-chat（兼容）' }
  ];
  var DEFAULT_MODEL = 'deepseek-flash';
  var MAX_HISTORY = 40;

  /* ---- 工具清单（模型可见的 schema）----------------------------------
   * name 必须与 ALLOWED 一致；description 用人话，避免模型误触发。
   * 高危工具在 description 里写明「需要用户确认」，模型也会倾向先问。 */
  var TOOLS = [
    {
      type: 'function',
      function: {
        name: 'get_schedule',
        description: '查询我的课表。可以问「今天有什么课」「周三下午上什么」。返回课程名、时间、教室。',
        parameters: {
          type: 'object',
          properties: {
            day: { type: 'string', description: '可选。星期几，如「周三」；不填默认今天。' }
          }
        }
      }
    },
    {
      type: 'function',
      function: {
        name: 'get_electricity',
        description: '查询宿舍电费余额和最近充值记录。只返回金额，不含账号。',
        parameters: { type: 'object', properties: {} }
      }
    },
    {
      type: 'function',
      function: {
        name: 'open_water',
        description: '打开住理生活的洗澡热水。这是高风险操作，会真实开水并计费，必须先请用户确认。',
        parameters: { type: 'object', properties: {} }
      }
    },
    {
      type: 'function',
      function: {
        name: 'submit_signin',
        description: '在学习通提交一次签到。这是高风险操作，会改变平台状态，必须先请用户确认。',
        parameters: {
          type: 'object',
          properties: {
            course: { type: 'string', description: '课程名，用于定位签到。' }
          }
        }
      }
    }
  ];

  // 白名单：查询类直接执行，高危类需确认。**清单外一律拒绝**。
  var ALLOWED = {
    get_schedule: { risk: false, label: '查课表' },
    get_electricity: { risk: false, label: '查电费' },
    open_water: { risk: true, label: '打开热水' },
    submit_signin: { risk: true, label: '提交签到' }
  };

  var tools = {};          // app.js 注入的工具实现
  var history = [];        // 对话记录 [{role, content, ...}]
  var busy = false;        // 防止重复提交

  /* ------------------------------------------------------------------ 存储 */
  function getKey() {
    try { return (window.localStorage.getItem(KEY_SETTING) || '').trim(); } catch (e) { return ''; }
  }
  function setKey(v) {
    try {
      v = String(v || '').trim();
      if (v) window.localStorage.setItem(KEY_SETTING, v);
      else window.localStorage.removeItem(KEY_SETTING);
    } catch (e) {}
  }
  function getModel() {
    try {
      var v = (window.localStorage.getItem(KEY_MODEL) || '').trim();
      return validModel(v) ? v : DEFAULT_MODEL;
    } catch (e) { return DEFAULT_MODEL; }
  }
  function setModel(v) {
    try {
      if (validModel(v)) window.localStorage.setItem(KEY_MODEL, v);
      else window.localStorage.removeItem(KEY_MODEL);
    } catch (e) {}
  }
  function validModel(v) {
    for (var i = 0; i < MODELS.length; i++) if (MODELS[i].id === v) return true;
    return false;
  }
  function loadHistory() {
    try {
      var a = JSON.parse(window.localStorage.getItem(KEY_HISTORY) || '[]');
      if (Array.isArray(a)) history = a.slice(-MAX_HISTORY);
    } catch (e) { history = []; }
  }
  function saveHistory() {
    try {
      history = history.slice(-MAX_HISTORY);
      window.localStorage.setItem(KEY_HISTORY, JSON.stringify(history));
    } catch (e) {}
  }

  /* ------------------------------------------------------------ 工具执行 */
  function registerTools(t) {
    tools = t || {};
  }

  /** 把工具调用翻译成「给用户看的一句话」，用于确认弹层。 */
  function toolDesc(name, args) {
    args = args || {};
    if (name === 'open_water') return '现在打开洗澡热水？会真实开水并开始计费。';
    if (name === 'submit_signin') return '现在提交' + (args.course ? '「' + args.course + '」的' : '') + '签到？';
    if (name === 'get_schedule') return '查看' + (args.day ? args.day : '今天') + '的课表';
    if (name === 'get_electricity') return '查看宿舍电费';
    return '执行 ' + name;
  }

  /**
   * 执行一个工具调用。高危的先弹确认；返回结果字符串（给模型回灌用）。
   * @param {object} call 模型给的 {id, function:{name, arguments}}
   * @returns {Promise<{id, content}>}
   */
  function runTool(call) {
    var fn = call && call.function;
    var name = fn && fn.name;
    var args = {};
    try { args = JSON.parse((fn && fn.arguments) || '{}') || {}; } catch (e) { args = {}; }
    var allow = ALLOWED[name];
    if (!allow) {
      return Promise.resolve({ id: call.id, content: '这个操作不在允许范围内，我没有执行。' });
    }
    var impl = tools[name];
    if (typeof impl !== 'function') {
      return Promise.resolve({ id: call.id, content: '这个功能暂时用不了。' });
    }

    var go = function () {
      try {
        return Promise.resolve(impl(args)).then(function (r) {
          if (r && typeof r === 'object') {
            if (r.error) return { id: call.id, content: r.error };
            return { id: call.id, content: r.text || JSON.stringify(r) };
          }
          return { id: call.id, content: String(r == null ? '' : r) };
        });
      } catch (e) {
        return Promise.resolve({ id: call.id, content: '没查到，请稍后再试。' });
      }
    };

    if (allow.risk) {
      return confirmRisk(toolDesc(name, args)).then(function (yes) {
        if (!yes) return { id: call.id, content: '用户取消了这次操作，没有执行。' };
        return go();
      });
    }
    return go();
  }

  /* ------------------------------------------------------------ 聊天主流程 */
  function chat(text) {
    var key = getKey();
    if (!key) return Promise.reject(new Error('请先填写服务密钥'));
    history.push({ role: 'user', content: String(text || '').slice(0, 2000) });
    saveHistory();
    render();
    return askModel();
  }

  function askModel() {
    busy = true;
    render();
    var messages = [{ role: 'system', content:
      '你是校园助手。只回答与校园生活相关的问题。' +
      '需要查课表或电费时调用对应工具；开水、签到这类操作必须先让用户确认。' +
      '不要在回答里写账号、学号或任何登录信息。回答简洁、用中文。'
    }].concat(history.map(function (m) {
      // 只把必要字段发给模型，工具结果也是最小字段（工具实现里保证）。
      return { role: m.role, content: m.content };
    }));
    return request({ model: getModel(), messages: messages, tools: TOOLS, tool_choice: 'auto' })
      .then(function (j) {
        var choice = j && j.choices && j.choices[0];
        if (!choice) throw new Error('没有收到回复');
        var msg = choice.message || {};
        var calls = msg.tool_calls || [];
        if (!calls.length) {
          history.push({ role: 'assistant', content: String(msg.content || '').slice(0, 4000) });
          saveHistory(); busy = false; render();
          return msg.content;
        }
        // 有工具调用：记录助手消息（含 tool_calls，回灌要原样带回）
        history.push({ role: 'assistant', content: msg.content || '', tool_calls: calls });
        saveHistory(); render();
        return Promise.all(calls.map(runTool)).then(function (results) {
          results.forEach(function (r) {
            history.push({ role: 'tool', tool_call_id: r.id, content: String(r.content).slice(0, 2000) });
          });
          saveHistory();
          // 回灌后再问一次模型，拿最终回答
          busy = true; render();
          return request({ model: getModel(), messages:
            [{ role: 'system', content: '你是校园助手，用中文简洁回答。不要写账号、学号或登录信息。' }]
              .concat(history.map(function (m) {
                var o = { role: m.role, content: m.content };
                if (m.tool_calls) o.tool_calls = m.tool_calls;
                if (m.tool_call_id) o.tool_call_id = m.tool_call_id;
                return o;
              })), tools: TOOLS
          }).then(function (j2) {
            var c2 = j2 && j2.choices && j2.choices[0] && j2.choices[0].message;
            var ans = c2 ? String(c2.content || '') : '没查到，请稍后再试。';
            history.push({ role: 'assistant', content: ans.slice(0, 4000) });
            saveHistory(); busy = false; render();
            return ans;
          });
        });
      })
      .catch(function (e) {
        busy = false;
        history.push({ role: 'assistant', content: '没连上服务：' + ((e && e.message) || '请稍后再试') });
        saveHistory(); render();
        throw e;
      });
  }

  /** 走原生 HTTP 通道（native.js 的 /api/ai/chat 白名单转发）发一条请求。 */
  function request(payload) {
    if (!window.NativeApi || typeof window.NativeApi.handle !== 'function') {
      return Promise.reject(new Error('当前版本不支持'));
    }
    var key = getKey();
    return window.NativeApi.handle('/api/ai/chat', {
      url: API_URL, key: key, payload: payload
    }).then(function (r) {
      if (!r || !r.ok) {
        var m = (r && r.error) || '请求失败';
        throw new Error(m);
      }
      var j = r.data;
      if (!j || typeof j !== 'object') throw new Error('服务返回异常');
      if (j.error) throw new Error(j.error.message || '请求失败');
      return j;
    });
  }

  /* ------------------------------------------------------------ 界面 */
  function open() {
    loadHistory();
    ensureDom();
    var root = document.getElementById('aiChatRoot');
    if (root) root.hidden = false;
    // 上次如果停在设置面板没关，重新进来先收起，别残留。
    closeSettings();
    render();
  }
  function close() {
    var root = document.getElementById('aiChatRoot');
    if (root) root.hidden = true;
    // 关弹层时若有高危操作在等确认，按「取消」结掉，别让 Promise 悬着。
    if (pendingConfirm) finishConfirm(false);
  }
  function clear() {
    history = []; saveHistory(); render();
  }

  function ensureDom() {
    if (document.getElementById('aiChatRoot')) return;
    var box = document.createElement('div');
    box.id = 'aiChatRoot';
    box.className = 'ai-root';
    box.hidden = true;
    box.innerHTML =
      '<div class="ai-mask" id="aiMask"></div>' +
      '<div class="ai-sheet">' +
        '<div class="ai-head">' +
          '<button type="button" class="ai-x" id="aiX" aria-label="关闭">✕</button>' +
          '<div class="ai-title">学习助手</div>' +
          // 🔴 2026-11-28：这里原来叫 class="ai-set"，跟下面的设置面板同名 ——
          // 面板那条 .ai-set{position:absolute;inset:0;z-index:3} 把按钮也撑成一张
          // 盖住全屏的透明层，于是「设置」显示在弹层正中、点输入框就误触到它。
          // 按钮单独用 .ai-setbtn（右上角），面板用 .ai-setpanel，互不干扰。
          '<button type="button" class="ai-setbtn" id="aiSetBtn">设置</button>' +
        '</div>' +
        '<div class="ai-privacy">对话和查询结果会发给你在设置里填的 AI 服务，请只问校园学习相关问题。</div>' +
        '<div class="ai-msgs" id="aiMsgs"></div>' +
        '<div class="ai-input">' +
          '<input type="text" id="aiInput" placeholder="问点什么…" autocomplete="off">' +
          '<button type="button" class="ai-send" id="aiSend">发送</button>' +
        '</div>' +
      '</div>';
    (document.body || document.documentElement).appendChild(box);

    // 2026-11-28：用户「设置弹在对话框上面太难看，要单独一个界面」。
    // 设置页做成跟对话弹层**平级的独立全屏层**（不是叠在对话上的卡片），
    // 从右上角「设置」进入，点返回 / 遮罩回到对话。id 仍叫 aiSet 兼容测试。
    var setRoot = document.createElement('div');
    setRoot.id = 'aiSettingsRoot';
    setRoot.className = 'ai-root ai-settings-root';
    setRoot.hidden = true;
    var modelBtns = MODELS.map(function (m) {
      return '<button type="button" class="ai-model-opt" data-model="' + m.id + '">' + m.label + '</button>';
    }).join('');
    setRoot.innerHTML =
      '<div class="ai-mask" id="aiSetMask"></div>' +
      '<div class="ai-sheet ai-set-sheet">' +
        '<div class="ai-head">' +
          '<button type="button" class="ai-x" id="aiSetBack" aria-label="返回">‹</button>' +
          '<div class="ai-title">AI 服务设置</div>' +
          '<span class="ai-set-head-sp"></span>' +
        '</div>' +
        '<div class="ai-set-body" id="aiSet">' +
          '<p class="ai-set-note">填好密钥就能用。密钥只保存在这台手机，对话内容会发给下面这家服务商。<button type="button" class="ai-set-inline" id="aiSetLink">怎么获取密钥</button></p>' +
          '<div class="ai-set-fld">' +
            '<div class="ai-set-lb">服务商</div>' +
            '<div class="ai-set-ro">DeepSeek</div>' +
          '</div>' +
          '<div class="ai-set-fld">' +
            '<div class="ai-set-lb">服务地址</div>' +
            '<button type="button" class="ai-set-ro ai-set-url" id="aiSetUrl">' + API_URL.replace('/chat/completions', '') + '</button>' +
          '</div>' +
          '<div class="ai-set-fld">' +
            '<div class="ai-set-lb">密钥 <span class="ai-set-hint">只发给上面这家</span></div>' +
            '<input type="password" class="ai-set-input" id="aiSetInput" placeholder="粘贴申请到的密钥" autocomplete="off">' +
          '</div>' +
          '<div class="ai-set-fld">' +
            '<div class="ai-set-lb">模型</div>' +
            '<div class="ai-model-pick" id="aiModelPick">' + modelBtns + '</div>' +
          '</div>' +
          '<div class="ai-set-btns">' +
            '<button type="button" id="aiSetClear">清除</button>' +
            '<button type="button" id="aiSetTest">测试</button>' +
            '<button type="button" class="on" id="aiSetSave">保存</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    (document.body || document.documentElement).appendChild(setRoot);

    document.getElementById('aiX').addEventListener('click', close);
    document.getElementById('aiMask').addEventListener('click', close);
    document.getElementById('aiSetBtn').addEventListener('click', openSettings);
    document.getElementById('aiSetBack').addEventListener('click', closeSettings);
    document.getElementById('aiSetMask').addEventListener('click', closeSettings);
    document.getElementById('aiSetClear').addEventListener('click', clearSettings);
    document.getElementById('aiSetSave').addEventListener('click', saveSettings);
    document.getElementById('aiSetTest').addEventListener('click', testSettings);
    // 模型按钮：点一下选中并存本机
    var opts = setRoot.querySelectorAll('.ai-model-opt');
    for (var i = 0; i < opts.length; i++) {
      opts[i].addEventListener('click', function () {
        setModel(this.getAttribute('data-model'));
        paintModelPick();
      });
    }
    document.getElementById('aiSetUrl').addEventListener('click', function () {
      copyText(API_URL.replace('/chat/completions', ''), '服务地址已复制');
    });
    document.getElementById('aiSetLink').addEventListener('click', function () {
      var url = 'https://platform.deepseek.com/api_keys';
      var ok = (window.NativeApi && typeof window.NativeApi.openUrl === 'function')
        ? window.NativeApi.openUrl(url) : false;
      if (!ok) copyText(url, '网址已复制，请用浏览器打开');
    });
    document.getElementById('aiSend').addEventListener('click', send);
    document.getElementById('aiInput').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') send();
    });
    paintModelPick();  // 建好 DOM 就画一次默认选中态，别等到打开设置页才亮
  }

  function send() {
    if (busy) return;
    var input = document.getElementById('aiInput');
    var text = (input && input.value || '').trim();
    if (!text) return;
    if (input) input.value = '';
    if (!getKey()) { openSettings(); return; }
    chat(text).catch(function () {});
  }

  function render() {
    var msgs = document.getElementById('aiMsgs');
    if (!msgs) return;
    if (!history.length) {
      msgs.innerHTML = '<div class="ai-empty">你对它说「今天有什么课」试试。<br>' +
        '涉及开水和签到的事，它会先问你一句再动手。</div>';
      return;
    }
    msgs.innerHTML = history.filter(function (m) {
      return m.role === 'user' || m.role === 'assistant';
    }).map(function (m) {
      if (m.role === 'user') return '<div class="ai-bub ai-me">' + esc(m.content) + '</div>';
      var txt = m.content || (m.tool_calls ? '（正在查询…）' : '');
      return '<div class="ai-bub ai-bot">' + esc(txt) + '</div>';
    }).join('') + (busy ? '<div class="ai-bub ai-bot ai-typing">…</div>' : '');
    msgs.scrollTop = msgs.scrollHeight;
  }

  /** 独立设置页（2026-11-28）：跟对话弹层平级的一整屏，不再叠在对话框上。 */
  function openSettings() {
    var root = document.getElementById('aiSettingsRoot');
    if (!root) return;
    var inp = document.getElementById('aiSetInput');
    if (inp) inp.value = getKey();
    paintModelPick();
    root.hidden = false;
    if (inp) setTimeout(function () { inp.focus(); }, 30);
  }
  function closeSettings() {
    var root = document.getElementById('aiSettingsRoot');
    if (root) root.hidden = true;
  }
  /** 模型选项高亮当前选中项。 */
  function paintModelPick() {
    var cur = getModel();
    var opts = (document.getElementById('aiSettingsRoot') || document).querySelectorAll('.ai-model-opt');
    for (var i = 0; i < opts.length; i++) {
      if (opts[i].getAttribute('data-model') === cur) opts[i].className = 'ai-model-opt on';
      else opts[i].className = 'ai-model-opt';
    }
  }
  function saveSettings() {
    var inp = document.getElementById('aiSetInput');
    var v = inp ? String(inp.value || '').trim() : '';
    setKey(v);
    closeSettings();
    note(v ? '已保存到这台手机。' : '已清空。');
  }
  function clearSettings() {
    var inp = document.getElementById('aiSetInput');
    if (inp) inp.value = '';
    setKey('');
    note('已清除本机密钥。');
  }
  /** 测试：发一条最短的问题验证密钥能不能用，结果就写在面板里。 */
  function testSettings() {
    var inp = document.getElementById('aiSetInput');
    var v = inp ? String(inp.value || '').trim() : '';
    if (!v) { note('先填密钥再测试。'); return; }
    setKey(v);
    note('正在连接…');
    request({
      model: getModel(),
      messages: [{ role: 'user', content: '回复一个字：好' }],
      max_tokens: 8,
      tools: []
    }).then(function (j) {
      var txt = (j && j.choices && j.choices[0] && j.choices[0].message &&
        j.choices[0].message.content) || '';
      note('连上了，可以用。（' + String(txt).replace(/\s+/g, ' ').trim().slice(0, 12) + '）');
    }).catch(function (e) {
      note('连不上：' + ((e && e.message) || '请检查密钥') );
    });
  }
  /** 面板内轻声提示，不弹系统框。 */
  function note(text) {
    var el = document.querySelector('.ai-set-note');
    if (!el) return;
    if (el.getAttribute('data-old') === null) el.setAttribute('data-old', el.textContent);
    el.textContent = text;
    if (note._t) clearTimeout(note._t);
    note._t = setTimeout(function () {
      var o = el.getAttribute('data-old');
      if (o !== null) el.textContent = o;
    }, 2600);
  }
  /** 复制文字。优先原生通道，退回浏览器接口。 */
  function copyText(text, tip) {
    var done = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text); done = true;
      }
    } catch (e) { /* 退回下面 */ }
    if (!done) {
      try {
        var ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        (document.body || document.documentElement).appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); done = true; } catch (e2) { /* 忽略 */ }
        ta.parentNode.removeChild(ta);
      } catch (e3) { /* 忽略 */ }
    }
    if (tip) note(tip);
  }

  /** 高危操作确认：在 AI 弹层**内部**显示一条确认条（ sheetMask 会被本弹层盖住，
   *  不能用 app.js 那套）。用户点「确认」才继续，点「取消」或关弹层都算取消。 */
  var pendingConfirm = null;
  function confirmRisk(text) {
    return new Promise(function (resolve) {
      pendingConfirm = resolve;
      var bar = document.getElementById('aiConfirm');
      if (!bar) {
        bar = document.createElement('div');
        bar.id = 'aiConfirm';
        bar.className = 'ai-confirm';
        var msgs = document.getElementById('aiMsgs');
        if (msgs && msgs.parentNode) msgs.parentNode.insertBefore(bar, msgs.nextSibling);
        else return resolve(false);
      }
      bar.innerHTML = '<div class="ai-confirm-t">' + esc(text) + '</div>' +
        '<div class="ai-confirm-b">' +
          '<button type="button" id="aiConfNo">取消</button>' +
          '<button type="button" id="aiConfYes">确认</button>' +
        '</div>';
      bar.hidden = false;
      document.getElementById('aiConfNo').onclick = function () { finishConfirm(false); };
      document.getElementById('aiConfYes').onclick = function () { finishConfirm(true); };
    });
  }
  function finishConfirm(v) {
    var bar = document.getElementById('aiConfirm');
    if (bar) bar.hidden = true;
    if (pendingConfirm) { var r = pendingConfirm; pendingConfirm = null; r(v); }
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  window.AI = {
    open: open,
    close: close,
    clear: clear,
    chat: chat,
    registerTools: registerTools,
    getKey: getKey,
    setKey: setKey
  };
})();
