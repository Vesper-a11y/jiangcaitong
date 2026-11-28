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
        description: '查宿舍电费余额、网费到期日。用户说电费、电量、还剩多少度、网费、宽带、' +
          '网络费、网什么时候到期，都调这个，不要说自己查不了。只返回金额和日期，不含账号。',
        parameters: { type: 'object', properties: {} }
      }
    },
    {
      type: 'function',
      function: {
        name: 'open_water',
        description: '打开宿舍洗澡热水（住理生活的水表）。会真实出水并计费，必须先拿到用户确认再调用。',
        parameters: { type: 'object', properties: {} }
      }
    },
    {
      type: 'function',
      function: {
        name: 'open_dispenser',
        description: '打开直饮水机接水。会真实出水并按用量扣钱，必须先拿到用户确认再调用。',
        parameters: {
          type: 'object',
          properties: {
            which: { type: 'string', description: '可选。哪一台设备，用户说了就说设备名，没说就留空（默认第一台）。' }
          }
        }
      }
    },
    {
      type: 'function',
      function: {
        name: 'open_page',
        description: '打开 App 里的某一页：课表、学习、生活、我的、校历、空教室、账号管理、校园服务；也能跳到快递取件，比如「淘宝身份码」「菜鸟取件」「拼多多待取快递」「京东快递」—— 说了哪个 App 就直接跳过去。用户说「打开XX」「跳到XX」时调用，别说打不开。',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string', description: '页面名，如「课表」「生活」「校历」；快递就写用户说的那个，如「淘宝身份码」；必填。' }
          },
          required: ['name']
        }
      }
    },
    {
      type: 'function',
      function: {
        name: 'get_grades',
        description: '查成绩。返回课程名、分数、学分。',
        parameters: {
          type: 'object',
          properties: {
            term: { type: 'string', description: '可选。哪个学期，不填就是全部。' }
          }
        }
      }
    },
    {
      type: 'function',
      function: {
        name: 'get_exams',
        description: '查考试安排。返回日期、时间、科目、考场。',
        parameters: { type: 'object', properties: {} }
      }
    },
    {
      type: 'function',
      function: {
        name: 'stop_water',
        description: '关掉正在开的洗澡热水。只会关本 App 开的那一次。',
        parameters: { type: 'object', properties: {} }
      }
    },
    {
      type: 'function',
      function: {
        name: 'stop_dispenser',
        description: '停掉正在接水的饮水机（本 App 开的）。',
        parameters: { type: 'object', properties: {} }
      }
    },
    {
      type: 'function',
      function: {
        name: 'sync_school',
        description: '重新同步一次教务数据（课表、成绩、考试）。会比较频繁地访问学校系统，用户明确要同步时才调用。',
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
    },
    {
      type: 'function',
      function: {
        name: 'add_course',
        description: '在课表上加一节自己加的课。用户说「周三第 3-4 节加一节大学物理实验」时调用。只写在这台手机的课表上，不会改学校教务系统。',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string', description: '课程名。用户只说「数学课」这类泛称时，用课表已有课程里的全名（如「线性代数(工)」），不要照抄泛称。' },
            day: { type: 'string', description: '星期几，如「周一」「周三」「周日」；必填。' },
            start_period: { type: 'integer', description: '第几节开始，从 1 数起；必填。' },
            end_period: { type: 'integer', description: '第几节结束（含这一节），不填就只占一节。' },
            weeks: { type: 'string', description: '上哪些周，如「1-16」「单周」「全学期」；用户说「这周」「本周」时填当前周的数字；不填默认整个学期。' },
            room: { type: 'string', description: '教室，可不填。' },
            teacher: { type: 'string', description: '老师，可不填。' }
          },
          required: ['name', 'day', 'start_period']
        }
      }
    },
    {
      type: 'function',
      function: {
        name: 'update_course',
        description: '改一门课的时间和教室。自己加的课直接改；学校教务给的课也行，只改这台手机上怎么显示，不动学校数据。也能用来恢复原样。用户说的是某一天全部课（没说哪一门）时，改用 move_day。',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string', description: '要改的那门课的名字，或名字里的一部分；必填。' },
            new_name: { type: 'string', description: '改成什么名字，不改就不填。' },
            day: { type: 'string', description: '改到星期几，不改就不填。' },
            start_period: { type: 'integer', description: '改到第几节开始，不改就不填。' },
            end_period: { type: 'integer', description: '改到第几节结束，不改就不填。' },
            from_week: { type: 'string', description: '用户说「把第12周那节调到第13周」时，这里填原来那一周（12）。原周那节跳过不上，目标周再补一节。只在同一周里换时间就不填。' },
            weeks: { type: 'string', description: '改到哪一周（目标周），例如「13」；老师说「调到下周六」就填下周是第几周。不填就是用户正在看的那一周。' },
            room: { type: 'string', description: '改成哪个教室，不改就不填。' },
            teacher: { type: 'string', description: '改成哪个老师，不改就不填。' },
            apply_all: { type: 'boolean', description: '只有用户明确说「以后每周都改」「整门课换时间」时才填 true。默认不填：老师临时调课、只改这一次，别的周保持原样。' },
            reset: { type: 'boolean', description: '用户说「恢复原样」时填 true，把这门课改回学校原本的时间和显示。' }
          },
          required: ['name']
        }
      }
    },
    {
      type: 'function',
      function: {
        name: 'move_day',
        description: '把某一周某一天的全部课整体挪到另一天。用户说的是「周五的课」而不是某一门课时用它，一次挪完整天，别一门一门改。',
        parameters: {
          type: 'object',
          properties: {
            from_day: { type: 'string', description: '原来那天，如「周五」；必填。' },
            to_day: { type: 'string', description: '挪到星期几，如「周六」；必填。' },
            from_week: { type: 'string', description: '原来那一周，如「12」；用户说「这个星期」就填当前周，不填默认当前周。' },
            weeks: { type: 'string', description: '挪到哪一周，如「13」；用户说「下个星期六」就填下一周。不填就是同一周内换天。' }
          },
          required: ['from_day', 'to_day']
        }
      }
    },
    {
      type: 'function',
      function: {
        name: 'undo',
        description: '把刚才对课表的改动退回去。用户说「撤销」「撤回」「把你做的操作都撤销」「改回原来的样子」时用它；这不是在改某一门课。',
        parameters: {
          type: 'object',
          properties: {
            all: { type: 'boolean', description: '用户说「都撤销」「全部撤回」「所有操作」时填 true，退回他提这件事之前；只说「撤销」就不填，只退一步。' }
          }
        }
      }
    },
    {
      type: 'function',
      function: {
        name: 'delete_course',
        description: '把一门课从这台手机的课表上删掉。自己加的课直接删；学校教务给的课只是在这台手机上不显示，学校那边不受影响。',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string', description: '要删的课名，或名字里的一部分；必填。' }
          },
          required: ['name']
        }
      }
    }
  ];

  // 白名单：查询类直接执行，高危类需确认。**清单外一律拒绝**。
  // 改课表只动本机课表数据，不碰学校系统、不产生真实费用，归为普通工具；
  // 每次改动都会把结果原样回给模型，用户能在对话里看见改了什么。
  var ALLOWED = {
    get_schedule: { risk: false, label: '查课表' },
    get_electricity: { risk: false, label: '查电费' },
    add_course: { risk: false, label: '加课' },
    update_course: { risk: false, label: '改课' },
    move_day: { risk: false, label: '整天调课' },
    undo: { risk: false, label: '撤销改课' },
    delete_course: { risk: false, label: '删课' },
    open_water: { risk: true, label: '打开热水' },
    open_dispenser: { risk: true, label: '打开饮水机' },
    stop_water: { risk: false, label: '关热水' },
    stop_dispenser: { risk: false, label: '停饮水机' },
    open_page: { risk: false, label: '打开页面' },
    get_grades: { risk: false, label: '查成绩' },
    get_exams: { risk: false, label: '查考试' },
    sync_school: { risk: true, label: '同步教务' },
    submit_signin: { risk: true, label: '提交签到' }
  };

  var tools = {};          // app.js 注入的工具实现
  var history = [];        // 对话记录 [{role, content, ...}]
  var busy = false;        // 防止重复提交
  var ctxFn = null;        // app.js 注入的「此刻状态」：第几周、课表里有哪些课

  /* U106：把此刻的周次、日期、已有课程名交给模型。
   * 为什么必须有：模型原来对这台手机一无所知 ——
   *  · 用户说「只要今天这一周」，它只能反问「你这一周是第几周」；
   *  · 用户说「加一节数学课」，它照抄「数学课」三个字，课表上就多出一门
   *    和「线性代数(工)」并存的怪课。
   * 只发课程名 / 老师 / 教室这三样，账号、一卡通号、登录凭据一律不进。 */
  function setContext(f) { ctxFn = (typeof f === 'function') ? f : null; }

  /** 此刻的状态（第几周、有哪些课），拿不到返回 null。 */
  function ctxState() {
    var c = null;
    if (ctxFn) { try { c = ctxFn(); } catch (e) { c = null; } }
    return (c && typeof c === 'object') ? c : null;
  }

  function ctxText() {
    var c = null;
    if (ctxFn) { try { c = ctxFn(); } catch (e) { c = null; } }
    if (!c || typeof c !== 'object') return '';
    var lines = [];
    var w = Number(c.week) || 0;
    var head = '';
    if (c.today) head = '今天是 ' + c.today + (c.weekday ? '（周' + c.weekday + '）' : '');
    if (w > 0) {
      head += (head ? '，' : '') + '本学期第 ' + w + ' 周' +
        (c.total ? '（共 ' + c.total + ' 周）' : '');
    }
    if (head) lines.push(head + '。');
    if (c.courses && c.courses.length) {
      lines.push('课表里已有的课（加课、改课用这里面的全名；用户说「数学课」这种泛称时，' +
        '挑最接近的那一门）：');
      lines.push(c.courses.slice(0, 30).map(function (x) {
        var ex = [];
        if (x.teacher) ex.push(x.teacher);
        if (x.room) ex.push(x.room);
        return '- ' + x.name + (ex.length ? '（' + ex.join(' · ') + '）' : '');
      }).join('\n'));
    }
    return lines.length ? ('\n\n【此刻的情况】\n' + lines.join('\n')) : '';
  }

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
    if (name === 'open_water') return '现在打开洗澡热水？会真实出水并开始计费。';
    if (name === 'open_dispenser') return '现在打开饮水机接水？会真实出水并按用量扣钱。';
    if (name === 'sync_school') return '现在重新同步一次教务数据？';
    if (name === 'submit_signin') return '现在提交' + (args.course ? '「' + args.course + '」的' : '') + '签到？';
    if (name === 'get_schedule') return '查看' + (args.day ? args.day : '今天') + '的课表';
    if (name === 'get_electricity') return '查看宿舍电费';
    if (name === 'add_course') return '把「' + (args.name || '') + '」加到课表？';
    if (name === 'update_course') return '改一下「' + (args.name || '') + '」？';
    if (name === 'move_day') return '把' + (args.from_day || '那天') + '的课挪到' + (args.to_day || '另一天') + '？';
    if (name === 'delete_course') return '从课表删掉「' + (args.name || '') + '」？';
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
    // 🔴 U115：改课类工具把用户原话一起递进去。
    //    源周（原来那一周）模型常常不填，而「界面此刻停在第几周」并不等于
    //    「他说的是哪一周」—— 他可能已经翻到第 13 周，嘴里说的是「12 周周五的改到
    //    13 周周六」。让代码自己从原话里认，比信模型那个可选字段稳。
    if (name === 'update_course' || name === 'move_day' || name === 'undo') {
      try { args.said = String(lastUserText() || '').slice(0, 300); } catch (e) { args.said = ''; }
    }
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

  /**
   * U104：把本机对话记录拼成能发给模型的完整消息。
   * 🔴 以前两处请求各写各的：第一轮只取 role/content，把工具调用和工具结果的
   *    关键字段丢了。只要用户问过一次课表 / 电费，之后再发任何一句话，服务方
   *    都会因为「工具调用没有对应的结果」整段拒收，界面统一显示「没连上服务」，
   *    而这条记录一直留在本机，之后每句都失败、重启也清不掉。
   *    现在两处请求共用这一份拼法；老版本留下的不完整记录也会被自动降级，
   *    装上新版即可自愈，不用手动清数据。
   */
  function apiMessages() {
    var out = [];
    var pending = null;   // 最近一条带工具调用的助手消息，在等它的结果
    var answered = false; // 这个回合是否已经收到工具结果
    function closePending() {
      if (!pending) return;
      // 只有「发出了调用、却没收到结果」的孤儿回合才降级成纯文字；
      // 结果齐全的回合必须原样保留，否则又会变成不完整的对话。
      if (!answered) {
        delete pending.tool_calls;
        if (!pending.content) pending.content = '（已查询）';
      }
      pending = null;
      answered = false;
    }
    for (var i = 0; i < history.length; i++) {
      var m = history[i];
      if (m.role === 'tool') {
        // 缺编号、或前面没有工具调用：老数据里的孤儿记录，丢掉，别让整段被拒
        if (!pending || !m.tool_call_id) continue;
        out.push({ role: 'tool', tool_call_id: m.tool_call_id,
          content: String(m.content || '').slice(0, 2000) });
        answered = true;
        continue;
      }
      closePending();
      if (m.role === 'user' || m.role === 'system') {
        out.push({ role: m.role, content: String(m.content || '') });
        continue;
      }
      if (m.role === 'assistant') {
        var o = { role: 'assistant', content: String(m.content || '') };
        if (m.tool_calls && m.tool_calls.length) { o.tool_calls = m.tool_calls; pending = o; }
        out.push(o);
      }
    }
    return out;
  }

  /** 失败提示说人话：不再把所有情况都笼统写成「没连上服务」。 */
  function friendlyError(e) {
    var m = String((e && e.message) || '').trim();
    var simple = {
      '请求失败': '服务暂时没回应，稍后再试',
      '服务返回异常': '服务没答上来，稍后再试',
      '没有收到回复': '服务没答上来，稍后再试',
      '服务没连上，请稍后再试': '服务暂时没回应，稍后再试',
      '当前版本不支持': '这个版本用不了学习助手'
    };
    return simple[m] || m || '服务暂时没回应，稍后再试';
  }

  /* ------------------------------------------------------------ 动作兜底 */
  /*
   * U109：用户 2026-11-28「热水还是开不了」「他说改好了，实际上也没有改好」。
   * 查下来工具本身没问题，问题在模型：它常常只回一段很像回事的文字，
   * 根本没调工具 —— 于是水没开、课表没动，界面却在说「好了」。
   * 所以这里加两道硬约束，不靠它自觉：
   *   ① 明确要开 / 关热水、饮水机的：它没调，就由代码直接调；
   *   ② 要改课表却只回了「改好了」的：逼它再走一次工具，还不走就如实说没改。
   */
  var forcedSeq = 0;
  function lastUserText() {
    for (var i = history.length - 1; i >= 0; i--) {
      if (history[i] && history[i].role === 'user') return String(history[i].content || '');
    }
    return '';
  }
  /** 这句话是不是在明确要求开水 / 关水。返回工具名，认不出返回空串。 */
  function forcedTool(t) {
    var s = String(t || '');
    if (!s) return '';
    var stop = /关|停|断|别|不要|不用/.test(s);
    var open = /开|放|启动|来|接/.test(s);
    if (/饮水|直饮/.test(s)) return stop ? 'stop_dispenser' : (open ? 'open_dispenser' : '');
    if (/热水|洗澡|淋浴/.test(s)) return stop ? 'stop_water' : (open ? 'open_water' : '');
    return '';
  }
  function wantsCourseChange(t) {
    var s = String(t || '');
    // 「撤销 / 重新执行 / 恢复原样」这类回头话，本身不带「课」字，但意思就是改课表。
    if (/(撤销|撤回|重做|重新执行|再来一遍|恢复原样)/.test(s)) return true;
    // U114：用户纠正时说的是「把这个星期的星期五调到下个星期六」——
    //   一个「课」字都没有，以前这句不触发强制改课，模型就改成查课表了。
    return /(课|节|教室|周|星期|礼拜)/.test(s)
      && /(改|换|挪|调|移|加|删|去掉|取消|恢复|放到|设成|转到)/.test(s);
  }
  /** 明确说要打开 / 跳到某个页面（含快递身份码）时，由代码替它调一次（U114）。
   *  用户「跳到淘宝身份码」，模型却回「这个页面我打不开」—— 话都说到这份上了，
   *  工具本身完全能跳，就别让模型再决定一次。 */
  function forcedPage(t) {
    var s = String(t || '');
    if (!s) return '';
    if (!/(打开|跳|进入|切到|去看看)/.test(s)) return '';
    if (!/(身份码|取件|快递|驿站|包裹|课表|校历|成绩|考试|空教室|账号|生活|我的)/.test(s)) return '';
    return s;
  }
  /** 「把周五的课调到周六」「这个星期的星期五调到下个星期六」—— 整句都在说
   *  从哪一天到哪一天，没有第二义。模型要是又只回一句「改好了」或者去查课表，
   *  由代码直接调 move_day（U114）。识别不出返回 null，交给模型。 */
  function forcedMoveDay(t) {
    var s = String(t || '');
    if (!s) return null;
    if (!/(调|挪|换|移|放到|改到)/.test(s)) return null;
    if (/[?？]/.test(s) || /吗$/.test(s)) return null;        // 问句不动手
    var names = s.match(/(?:星期|周|礼拜)[一二三四五六日天]/g) || [];
    if (names.length < 2) return null;                        // 只说了去哪天，没说从哪天
    // 这几种是在「精细地改某一门课」，整天挪会把那天别的课一起搬走：
    //   · 提到了具体第几节（第 5 节）
    //   · 补了一句「13 周周五的不变」这种边界
    //   · 原话里点名了课表里的某一门课
    // 其余（「把周五的课调到周六」）才是整天。
    if (/第\s*\d+\s*节/.test(s)) return null;
    if (/不变|别动|保持|不动|其他|其它|其余/.test(s)) return null;
    var known = [];
    try { var st = ctxState(); known = (st && st.courses) ? st.courses : []; } catch (e) { known = []; }
    for (var ki = 0; ki < known.length; ki++) {
      var kn = String((known[ki] && known[ki].name) || '').trim();
      if (kn.length >= 2 && s.indexOf(kn) >= 0) return null;
    }
    var dg = function (x) {
      var c = String(x).slice(-1);
      return c === '天' ? 6 : '一二三四五六日'.indexOf(c);
    };
    var from = dg(names[0]);
    var to = dg(names[names.length - 1]);
    if (from < 0 || to < 0) return null;
    var all = s.match(/第\s*(\d{1,2})\s*周/g) || [];
    var num = function (x) { return String(parseInt(String(x).replace(/\D/g, ''), 10) || ''); };
    var curW = 0;
    try { var st = ctxState(); curW = st ? (Number(st.week) || 0) : 0; } catch (e) { curW = 0; }
    var srcW = all.length ? num(all[0]) : '';
    var dstW = all.length >= 2 ? num(all[1]) : '';
    // 「这个星期 / 本周」= 当前周；「下个星期 / 下周」= 当前周 + 1。
    // 拿不到当前周就留空（同一周内换天），绝不瞎猜成第 1 周。
    if (!srcW && curW > 0 && /这(个)?(星期|周)|本(星期|周)|当(星期|周)/.test(s)) srcW = String(curW);
    if (!dstW && curW > 0 && /下(个)?(星期|周)/.test(s)) dstW = String(curW + 1);
    return { from_day: '周' + '一二三四五六日'.charAt(from), to_day: '周' + '一二三四五六日'.charAt(to),
      from_week: srcW, weeks: dstW };
  }
  /** 这句话是不是让我把刚才的改动退回去。返回 'all'（全退）/ 'one'（退一步）/ ''。
   *  🔴 U116：用户说「把你做的所有操作都撤销」，模型会把它翻成 update_course ——
   *     参数跟上一句一样，于是回「改好了」但课表一个字没动。这句话的意思只有一个：
   *     退回去。由代码直接调 undo，不让模型再猜。 */
  function wantsUndo(t) {
    var s = String(t || '');
    if (!s) return '';
    var all = /(都|全部|所有|统统|这些操作|刚才那些)/.test(s);
    if (/(撤销|撤回|回滚|退回|撤掉|撤销掉|撤了)/.test(s)) return all ? 'all' : 'one';
    if (all && /(恢复|还原|复原)/.test(s)) return 'all';
    return '';
  }
  function claimsDone(t) {
    return /(改好|已改|已经改|加好|已加|已删|删掉了|开好|已开|搞定|已恢复|恢复好)/.test(String(t || ''));
  }
  function fakeCall(name, args) {
    forcedSeq += 1;
    return { id: 'f' + forcedSeq + '_' + Date.now().toString(36), type: 'function',
      function: { name: name, arguments: JSON.stringify(args || {}) } };
  }

  /** 执行一批工具调用，再把结果交给模型说人话。 */
  function runCalls(calls, content) {
    history.push({ role: 'assistant', content: content || '', tool_calls: calls });
    saveHistory(); render();
    return Promise.all(calls.map(runTool)).then(function (results) {
      results.forEach(function (r) {
        history.push({ role: 'tool', tool_call_id: r.id, content: String(r.content).slice(0, 2000) });
      });
      saveHistory();
      // 回灌后再问一次模型，拿最终回答
      busy = true; render();
      return request({ model: getModel(), messages:
        [{ role: 'system', content:
          '工具已经执行完了。只把结果照实说给用户：做到就说做到，没做到就说没做到，' +
          '不要加工具结果里没有的信息，不要用 ** 或 # 这类符号，最多两句话。用中文。' +
          ctxText() }]
          .concat(apiMessages()), tools: TOOLS
      }).then(function (j2) {
        var c2 = j2 && j2.choices && j2.choices[0] && j2.choices[0].message;
        var ans = c2 ? String(c2.content || '').trim() : '';
        // U110：工具结果本身就是人话（「热水开了…」「已从课表删掉…」）。
        //   以前模型偶尔回一句空的，界面就只剩一个空气泡；现在拿结果顶上。
        var all = results.map(function (r) { return String(r.content || ''); }).join('\n');
        if (!ans) ans = (all.split('\n')[0] || '').trim() || '好了。';
        // 工具明说没改上，回答里就不许再出现「改好了」。
        if (/没改上|没删掉|没开成|这次没改/.test(all) && claimsDone(ans)) {
          ans = all.split('\n')[0] || ans;
        }
        // 🔴 U116：这一轮是撤销，模型却说「改好了」——那就是那句熟悉的谎话。
        //    撤销本身没有「改好」这回事，直接用工具自己的结果顶上。
        var hasUndo = calls.some(function (c) {
          return c && c.function && c.function.name === 'undo';
        });
        if (hasUndo && claimsDone(ans)) ans = all.split('\n')[0] || ans;
        history.push({ role: 'assistant', content: ans.slice(0, 4000) });
        saveHistory(); busy = false; render();
        return ans;
      }).catch(function () {
        // 🔴 U112：工具已经执行完了（水开了、课改了），模型这一轮没答上来也不能
        //    让界面说「没答上来」—— 直接把它自己的结果说出来，本来就是人话。
        var all = results.map(function (r) { return String(r.content || ''); }).join('\n');
        var ans = (all.split('\n')[0] || '').trim() || '好了。';
        history.push({ role: 'assistant', content: ans.slice(0, 4000) });
        saveHistory(); busy = false; render();
        return ans;
      });
    });
  }

  function askModel(nudge) {
    busy = true;
    render();
    var messages = [{ role: 'system', content:
      '你是「酱菜通」里的学习助手，像同学一样说话，别像客服。\n' +
      '1. 回答最多两句话，能一句话说完就一句。不列 1.2.3.，不用 ** 或 # 排版。\n' +
      '2. 不问「要不要我帮你查」，需要数据就直接调工具；查完只报结果，不讲过程。\n' +
      '3. 不许让用户自己去别的页面看。工具真缺条件时，只说缺什么、去哪填，一句说完。\n' +
      '4. 不重复用户的话，不总结他的消息。\n' +
      '5. 拿不准就只问一句最关键的，别铺开分析。\n' +
      '6. 「这周 / 本周 / 今天这周」就是当前周，直接用下面给的周次，不要反问第几周。\n' +
      '7. 加课、改课用课表里已有课程的全名；用户说「数学课」这种，选最接近的那一门。\n' +
      '8. 开热水、开饮水机、签到、同步会真的出水或扣费：先说「现在开吗」，确认后再调工具。\n' +
      '9. 不写账号、学号等任何登录信息。\n' +
      '10. 说「打开XX」「跳到XX」就调 open_page；问成绩、考试就查；' +
      '快递取件、淘宝/菜鸟/拼多多身份码也算一页，说了哪个 App 就把那个填进 name，别说打不开；' +
      '问网费、宽带、网什么时候到期也调 get_electricity，别说查不了；说关水、停饮水机就直接关。\n' +
      '11. 老师通知这类消息，先说结论（例如「周五那节挪到周六」）。日期拿不准就一句话问清，别逐条分析。' +
      '老师临时调课只改他点名的那一周：原来那周填 from_week（那节跳过不上），目标周填 weeks，' +
      '别的周必须照原样；只有他说「以后每周都这样」才填 apply_all。' +
      '「把12周第5节调到13周星期六」就是 from_week=12、weeks=13、day=周六、start_period=5，' +
      '不要动 13 周原本的课。\n' +
      '12. 用户说的是某一天全部的课、又没点名哪一门（「把这个星期的星期五调到下个星期六」' +
      '「周五的课挪到周六」），用 move_day，一次把那天所有课都挪过去：' +
      'from_day=周五、to_day=周六、from_week=12、weeks=13。他有几门课就挪几门，不要只挑一门、' +
      '也不要为了挪去一门一门调 update_course。\n' +
      '13. 用户说「不对」「不是这个」「我说的是」就是在纠正你：照他这一句重新改一遍，' +
      '不要回头去查课表，也不要重复上一遍的做法。\n' +
      '14. 改课表（加课、改课、删课、整天调课）不用问「要不要改」，也不要等确认：直接调工具改，改完一句话报结果。\n' +
      '15. 没真的调用工具，就绝不能说「改好了 / 已开 / 已完成」。\n' +
      '16. 用户说「撤销」「撤回」「都撤销」是让我把刚才的改动退回去，调 undo；' +
      '不要用 update_course 再改一遍同一门课，也不要说「改好了」。\n' +
      '17. 「把 X 改回周五」这种退回去的话：update_course 填 name 加 day（周五）就够了，' +
      '周次填他说的那一周，别再补一条同名课。\n' +
      (nudge ? (nudge + '\n') : '') + ctxText()
    }].concat(apiMessages());
    return request({ model: getModel(), messages: messages, tools: TOOLS, tool_choice: 'auto' })
      .then(function (j) {
        var choice = j && j.choices && j.choices[0];
        if (!choice) throw new Error('没有收到回复');
        var msg = choice.message || {};
        var calls = msg.tool_calls || [];
        // 🔴 U116：「把你做的所有操作都撤销」是退回课表，不是再改一次某门课。
        //    模型常把它翻成 update_course（改的还是同一格）—— 界面回「改好了」，
        //    课表一动不动。这句话意图唯一，不管模型调了什么，都由代码调 undo。
        var und = nudge ? '' : wantsUndo(lastUserText());
        if (und && !calls.some(function (x) {
          return x && x.function && x.function.name === 'undo';
        })) {
          return runCalls([fakeCall('undo', { all: und === 'all' })], msg.content || '');
        }
        if (!calls.length) {
          var txt = String(msg.content || '').trim();
          // ① 明确要开水 / 关水：模型只回文字就直接执行，别让它糊过去。
          var ft = nudge ? '' : forcedTool(lastUserText());
          if (ft && typeof tools[ft] === 'function') {
            return runCalls([fakeCall(ft)], '');
          }
          // ①b 明确要跳页面 / 开快递身份码：模型只回「打不开」也照样跳。
          var fp = nudge ? '' : forcedPage(lastUserText());
          if (fp && typeof tools.open_page === 'function') {
            return runCalls([fakeCall('open_page', { name: fp })], '');
          }
          // ①c 「把周五的课调到周六」这种整天调课：模型不动手，代码替它动手。
          var fm = nudge ? null : forcedMoveDay(lastUserText());
          if (fm && typeof tools.move_day === 'function') {
            return runCalls([fakeCall('move_day', fm)], '');
          }
          // ② 要改课表（含「撤销 / 重新执行」）：没调工具就是没动过课表，逼它真调一次。
          //    U110：不再只在它嘴上说「改好了」时才拦 —— 它也可能说「已经处理过了」
          //    或者干脆回一句空的，用户看到的就是「没反应」。
          if (!nudge && wantsCourseChange(lastUserText())) {
            return askModel('用户要改课表，上一轮你没有调用工具，课表其实一点没动。' +
              '现在直接调用工具做完，不要问，也不要解释：' +
              '说的是某一天全部的课（像「把周五的课调到周六」）就用 move_day；' +
              '说得是某一门课就用 update_course；加课用 add_course；删课用 delete_course。' +
              '用户说「不对」「不是这个」就是上次改错了，按他这一句重新改一遍，不要去查课表。' +
              '确实做不了就说做不到，别声称做好了。');
          }
          // ③ 空回答：一次是网络/模型抽风，重问一次；还空就明说，别留空气泡。
          if (!txt) {
            if (!nudge) {
              return askModel('刚才你回了一句空的。直接用一两句话说结果；做不到就说做不到。');
            }
            txt = '这次没答上来，再说一次。';
          }
          history.push({ role: 'assistant', content: txt.slice(0, 4000) });
          saveHistory(); busy = false; render();
          return txt;
        }
        return runCalls(calls, msg.content || '');
      })
      .catch(function (e) {
        busy = false;
        history.push({ role: 'assistant', content: friendlyError(e) });
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
            '<button type="button" id="aiSetClear">清除密钥</button>' +
            '<button type="button" id="aiSetTest">测试</button>' +
            '<button type="button" class="on" id="aiSetSave">保存</button>' +
          '</div>' +
          '<button type="button" class="ai-set-clear-chat" id="aiSetClearChat">清空对话记录</button>' +
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
    // U104：留一个能自己恢复的入口，遇到异常记录时不用等新版本。
    document.getElementById('aiSetClearChat').addEventListener('click', function () {
      clear();
      note('对话已清空。');
    });
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
      if (m.role !== 'user' && m.role !== 'assistant') return false;
      // U107：工具调用那一轮只是「正在查」的过程，不是给用户看的话。
      // 最终回答出来之后它就该消失，不再变成历史里一条「（正在查询…）」。
      if (m.role === 'assistant' && m.tool_calls && !String(m.content || '').trim()) return false;
      // U110：老记录里可能有「空回答」留下的空消息，别再画成空气泡。
      if (m.role === 'assistant' && !String(m.content || '').trim()) return false;
      return true;
    }).map(function (m) {
      if (m.role === 'user') {
        return '<div class="ai-row me"><div class="ai-av me">我</div>' +
          '<div class="ai-bub ai-me">' + esc(m.content) + '</div></div>';
      }
      return '<div class="ai-row bot"><div class="ai-av bot">✦</div>' +
        '<div class="ai-bub ai-bot">' + esc(plain(m.content)) + '</div></div>';
    }).join('') + (busy ? '<div class="ai-row bot"><div class="ai-av bot">✦</div>' +
      '<div class="ai-bub ai-bot ai-typing">（正在查询…）</div></div>' : '');
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
      tools: TOOLS
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
      // U112：确认条在消息区下方，聊得久了可能落在视野外 —— 用户看不到就一直在等。
      try { if (bar.scrollIntoView) bar.scrollIntoView({ block: 'nearest' }); } catch (e) {}
      document.getElementById('aiConfNo').onclick = function () { finishConfirm(false); };
      document.getElementById('aiConfYes').onclick = function () { finishConfirm(true); };
    });
  }
  function finishConfirm(v) {
    var bar = document.getElementById('aiConfirm');
    if (bar) bar.hidden = true;
    if (pendingConfirm) { var r = pendingConfirm; pendingConfirm = null; r(v); }
  }

  /** 气泡里不认排版符号：模型偶尔仍会带 **加粗** / ### 标题 / 反引号，
   *  直接显示出来就是一堆星号，看着像坏掉了。这里先剥掉再转义。 */
  function plain(s) {
    return String(s == null ? '' : s)
      .replace(/\*\*([^*\n]+)\*\*/g, '$1')
      .replace(/^\s{0,3}#{1,6}\s+/gm, '')
      .replace(/`([^`\n]+)`/g, '$1')
      .replace(/^\s*[-*]\s+/gm, '· ');
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
    setContext: setContext,
    getKey: getKey,
    setKey: setKey
  };
})();
