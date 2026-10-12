/**
 * settings.js —— 「活动检测设置」面板（contracts/detector.settings.v1，nexus-core v2.5）
 *
 * 选一台设备 → GET settings?deviceId= → 填表；「保存」PUT 整份文档，「恢复默认」DELETE（这台设备改回用
 * 它本机的配置文件）。检测程序每轮（≤ 5 分钟）拉一次，所以改了下一轮才生效。
 *
 * - 表单项都在 index.html 里，用 data-key="节.键" 对上文档：复选框 = 布尔、数字框 = 整数、
 *   radio 的 name = 枚举、textarea[data-list] = 每行一项的字符串数组、data-null 复选框勾上 = null（用本机名单）。
 * - 发出去的是「读进来的文档 + 表单改动」：服务端将来追加的键（比如 presence）原样带回去，不会被这页清掉。
 *   presence 只在文档里真有这个布尔时才出现一个勾选项；没有就不出现、也不发。
 * - 分段两项（v1.2 的 segmentByTitle / segmentByTitleApps）总是显示、总是发：文档里没有（v1.1 存下的）就按缺省填。
 * - autoTrack（v1.3「允许 AI 管理进行中的任务」，缺省关）同样总是显示、总是发。勾上保存时，文档里还没有布尔的
 *   presence（没设过在场心跳）就一并设成 true——实时的「自动 · 项目 / 任务」靠心跳，页面上的说明写着这一条。
 * - 强制脱敏（密码、密钥……）不在文档里，页面上是勾着的灰框，关不掉。
 * - 422 的 detail 形如「privacy.pathWhitelist: …」：按前缀挂到对应那一项下面，对不上就显示在表单底部。
 * - 设备行下面一条状态（v2.27）：检测程序在线 / 离线（health.at 超过 3 分钟没更新算离线）+ ActivityWatch 怎么样；
 *   「检测」重拉一次，「重启 ActivityWatch」POST restart 后每 3 秒拉一次设备，最多 75 秒，等检测程序回报结果。
 *   服务端的字符串一律只进 textContent。
 * 对外只挂 window.assistantSettings（纯函数，给单测用）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var API = BASE + "api/core/detector/";

  var DEFAULTS = {
    schemaVersion: 1,
    privacy: {
      paths: "full", pathWhitelist: [], titles: "keep", appOnly: true, appOnlyApps: null,
      browser: "domain", queryStrings: true, emails: true, phones: true, addresses: true,
      ips: true, usernames: true, longNumbers: true,
    },
    idle: {
      afkThresholdMinutes: 0, audibleAsPresent: false, focusAppsEnabled: false, focusApps: null,
      focusMaxMinutes: 60, idleSuggestions: false,
    },
    // v1.2：终端按标签页分段。v1.1 存下的文档没有这两个键——fill() 按这里的缺省补上再填表。
    segmentByTitle: true, segmentByTitleApps: null,
    autoTrack: false,   // v1.3
  };
  var LIMITS = {   // 契约「校验」：列表条数、每条长度；整数范围
    "privacy.pathWhitelist": [20, 200], "privacy.appOnlyApps": [200, 64], "idle.focusApps": [200, 64],
    "segmentByTitleApps": [200, 64],
    "idle.afkThresholdMinutes": [0, 240], "idle.focusMaxMinutes": [1, 480],
  };

  // ── 纯函数 ─────────────────────────────────────────────────────────
  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function getPath(obj, key) {
    return key.split(".").reduce(function (o, k) { return o == null ? undefined : o[k]; }, obj);
  }
  function setPath(obj, key, value) {
    var ks = key.split("."), o = obj;
    ks.slice(0, -1).forEach(function (k) { if (!o[k] || typeof o[k] !== "object") o[k] = {}; o = o[k]; });
    o[ks[ks.length - 1]] = value;
  }
  // 程序名去掉首尾空白；正则**逐字保留**（首尾空格也是正则的一部分），只丢空行
  // 按码点数（同服务端 Python 的 len）：emoji 之类不算两个
  function chars(s) { return Array.from(s).length; }
  function lines(text, verbatim) {
    return String(text || "").split("\n").map(function (l) { return verbatim ? l.replace(/\r$/, "") : l.trim(); })
      .filter(function (l) { return l !== ""; });
  }

  // 白名单正则的本地检查：只拦肯定不行的（长度、RE2 不支持的构造）。NOT_RE2 与服务端
  // （nexus-core detector/service.py 的 _NOT_RE2）逐字同一条，所以不会拦下服务端会收的写法。
  // 语法本身交给服务端（422 挂回这一项）——JS 的 RegExp 与 RE2 不同，拿它判会误拦合法写法（如 \Q…\E）。
  var NOT_RE2 = /\(\?<?[=!]|\\[1-9]|\(\?P=|\(\?\(|\(\?>|(?<!\\)[*+?}]\+/;
  function checkPattern(p) {
    if (chars(p) > 200) return "太长（最多 200 个字符）";
    var probe = p.replace(/\\[pP](\{[^}]*\}|[A-Za-z])/g, "x");   // 同服务端：\p{Han}+ 的「}+」不是占有量词
    if (NOT_RE2.test(probe)) return "用了 RE2 不支持的写法（前后查找 / 反向引用 / 条件组 / 原子组 / 占有量词）";
    return "";
  }

  // 整份文档的本地校验 → {字段: 说明}；空对象 = 没问题
  function validate(doc) {
    var errs = {};
    ["privacy.pathWhitelist", "privacy.appOnlyApps", "idle.focusApps", "segmentByTitleApps"].forEach(function (key) {
      var v = getPath(doc, key), lim = LIMITS[key];
      if (v === null) return;
      if (v.length > lim[0]) { errs[key] = "最多 " + lim[0] + " 条（现在 " + v.length + " 条）"; return; }
      for (var i = 0; i < v.length; i++) {
        var msg = key === "privacy.pathWhitelist" ? checkPattern(v[i])
          : (chars(v[i]) > lim[1] ? "每项最多 " + lim[1] + " 个字符" : "");
        if (msg) { errs[key] = "第 " + (i + 1) + " 条" + msg; return; }
      }
    });
    ["idle.afkThresholdMinutes", "idle.focusMaxMinutes"].forEach(function (key) {
      var v = getPath(doc, key), lim = LIMITS[key];
      if (!Number.isInteger(v) || v < lim[0] || v > lim[1]) errs[key] = "要 " + lim[0] + "–" + lim[1] + " 的整数";
    });
    return errs;
  }

  // 422 detail「privacy.pathWhitelist: Value error, …」→「privacy.pathWhitelist」；对不上 → null
  function fieldOf(detail) {
    var m = /^((?:privacy|idle)\.[A-Za-z]+|presence|segmentByTitleApps|segmentByTitle|autoTrack)\b/.exec(String(detail || ""));
    return m ? m[1] : null;
  }

  // presence 由别的 PR 追加（位置未定）：文档里哪儿有布尔 presence 就用哪儿；没有 → null
  function findPresence(doc) {
    var spots = ["presence", "privacy.presence", "idle.presence"];
    for (var i = 0; i < spots.length; i++) if (typeof getPath(doc, spots[i]) === "boolean") return spots[i];
    return null;
  }

  function pad2(n) { return String(n).padStart(2, "0"); }
  function when(iso) {
    if (!iso) return "没有";
    var d = new Date(iso);
    return pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }
  function lastSeen(dev) {
    return [dev.lastUploadAt, dev.lastFetchAt].filter(Boolean).sort().pop() || null;
  }

  // 检测程序状态（nexus-core v2.27）：device.health = {awState, at} | null。返回 {text, online}；online=null 表示没有状态可看
  var STALE_MS = 3 * 60 * 1000, POLL_MS = 3000, POLL_MAX_MS = 75000;
  var AW_TEXT = { ok: "正常", unreachable: "连不上 ActivityWatch", no_window: "ActivityWatch 最近一分钟没有窗口记录",
    paused: "已暂停", disabled: "未开启" };
  var RESTART_TEXT = { started: "已启动，ActivityWatch 的接口已经起来了", already_running: "ActivityWatch 本来就在运行",
    failed: "启动失败", unsupported: "这台电脑上没配置启动命令" };
  var AUTO_TEXT = { started: "已自动拉起", gave_up: "自动拉起 3 次都没成功，已停止重试",
    unsupported: "这台电脑上没配置启动命令", off: "自动拉起已关闭" };
  var REASON_TEXT = { not_found: "找不到 ActivityWatch 程序", spawn_error: "启动命令执行出错", exited_early: "启动后马上退出了",
    not_up: "进程起来了，但 ActivityWatch 接口 20 秒内没起来", no_display: "检测程序不在桌面会话里运行，没法打开图形程序",
    state_unwritable: "检测程序目录写不进去，没有尝试启动" };
  var LOG_HINT = "详细原因和日志在那台电脑上：运行 ai-detector status，或查看检测程序目录下的 aw-start.log。";
  // 服务端给的值只当键用：自有属性才认（"constructor"、"__proto__" 之类走 Object.prototype 的不算）
  function own(tbl, k, fallback) { return typeof k === "string" && Object.prototype.hasOwnProperty.call(tbl, k) ? tbl[k] : fallback; }
  function reasonText(r) { return own(REASON_TEXT, r, "原因未知"); }
  function healthLine(dev, nowMs) {
    var h = dev && dev.health, at = h ? Date.parse(h.at) : NaN;
    if (!h || isNaN(at)) return { text: "检测程序状态：这个版本的检测程序不报告状态", online: null };
    var age = nowMs - at;
    if (!(age < STALE_MS)) {
      return { text: "检测程序：离线（最近一次报告 " + Math.max(1, Math.floor(age / 60000)) + " 分钟前）", online: false };
    }
    var a = h.autoStart, capped = a === "gave_up" && h.autoReason === "daily_cap";
    var auto = (a == null || a === "idle") ? "" : own(AUTO_TEXT, a, "未知状态");   // 没有 / idle 什么都不说；不认识的值说「未知状态」
    if (capped) auto = "24 小时内自动拉起已达 20 次上限，已停止自动重试；可以点按钮手动重启";
    else if (a === "gave_up" && h.autoReason) auto += "（" + reasonText(h.autoReason) + "）";
    return { text: "检测程序：在线 · ActivityWatch：" + own(AW_TEXT, h.awState, "未知状态") + (auto ? " · " + auto : ""), online: true, gaveUp: a === "gave_up" && !capped };
  }

  window.assistantSettings = {
    DEFAULTS: DEFAULTS, checkPattern: checkPattern, validate: validate, fieldOf: fieldOf, findPresence: findPresence,
    healthLine: healthLine,
  };

  // ── DOM ─────────────────────────────────────────────────────────────
  var panelEl = document.getElementById("settings-panel");
  var formEl = document.getElementById("det-form");
  if (!panelEl || !formEl) return;
  var deviceEl = document.getElementById("det-device");
  var deviceRowEl = document.getElementById("det-device-row");
  var metaEl = document.getElementById("det-device-meta");
  var noteEl = document.getElementById("det-note");
  var msgEl = document.getElementById("det-message");
  var dirtyEl = document.getElementById("det-dirty");
  var saveBtn = document.getElementById("det-save");
  var resetBtn = document.getElementById("det-reset");
  var presenceRow = document.getElementById("det-presence-row");
  var presenceEl = document.getElementById("det-presence");
  var healthEl = document.getElementById("det-health");
  var checkBtn = document.getElementById("det-check");
  var restartBtn = document.getElementById("det-restart");
  var offlineHintEl = document.getElementById("det-offline-hint");
  var logHintEl = document.getElementById("det-log-hint");
  var restartMsgEl = document.getElementById("det-restart-msg");

  var devices = [];
  var skew = 0;             // 服务端时钟 − 浏览器时钟（GET devices 的 now）；老服务端没有 now = 0
  function noteNow(body) {
    var t = Date.parse(body && body.now);
    skew = isNaN(t) ? 0 : t - Date.now();
  }
  var device = null;        // 当前设备 id
  var loaded = null;        // GET 回来的整份响应 {deviceId, settings, updatedAt}
  var base = null;          // 表单的底稿：settings 或 DEFAULTS（未知键从这里原样带回去）
  var baseline = "";        // 底稿刚填进表单时 read() 的 JSON：不同 = 有没保存的改动
  var busy = false;
  var pending = null;       // 等回报的重启 {device, nonce}；换设备 / 出结果 / 超时都置回 null

  function showMessage(text, isError) {
    msgEl.textContent = text;
    msgEl.hidden = !text;
    msgEl.classList.toggle("is-error", Boolean(isError));
  }
  function showNote(text) { noteEl.textContent = text; noteEl.hidden = !text; }

  function fieldBox(key) { return formEl.querySelector('[data-field="' + key + '"]'); }
  function clearErrors() {
    formEl.querySelectorAll(".field-error").forEach(function (p) { p.hidden = true; p.textContent = ""; });
    formEl.querySelectorAll("[aria-invalid]").forEach(function (n) {
      n.removeAttribute("aria-invalid");
      n.removeAttribute("aria-describedby");
    });
  }
  function showErrors(errs) {
    clearErrors();
    var first = null;
    Object.keys(errs).forEach(function (key) {
      var box = fieldBox(key);
      if (!box) return;
      var p = box.querySelector(".field-error");
      p.textContent = errs[key];
      p.hidden = false;
      var input = box.querySelector("input:not([type=radio]):not([data-null]), textarea, input[type=radio]");
      p.id = "err-" + key.replace(/\./g, "-");
      if (input) {   // 读屏跟着焦点念出具体原因，不只是底部那句「见标红的」
        input.setAttribute("aria-invalid", "true");
        input.setAttribute("aria-describedby", p.id);
        first = first || input;
      }
    });
    return first;
  }

  // 表单 → 文档（从底稿复制，只改表单管得到的键）
  function read() {
    var doc = clone(base || DEFAULTS);
    formEl.querySelectorAll("input[data-key]").forEach(function (n) {
      if (n.type === "checkbox") setPath(doc, n.dataset.key, n.checked);
      else if (n.type === "number") setPath(doc, n.dataset.key, n.value.trim() === "" ? null : Number(n.value));
    });
    formEl.querySelectorAll("input[type=radio]:checked").forEach(function (n) { setPath(doc, n.name, n.value); });
    formEl.querySelectorAll("textarea[data-list]").forEach(function (t) {
      var key = t.dataset.list, nul = formEl.querySelector('input[data-null="' + key + '"]');
      setPath(doc, key, nul && nul.checked ? null : lines(t.value, key === "privacy.pathWhitelist"));
    });
    if (doc.autoTrack === true && !findPresence(doc)) doc.presence = true;   // 自动跟踪要心跳：没设过就一并打开
    return doc;
  }

  // 文档 → 表单
  function write(doc) {
    formEl.querySelectorAll("input[data-key]").forEach(function (n) {
      var v = getPath(doc, n.dataset.key);
      if (n.type === "checkbox") n.checked = v === true;
      else n.value = v == null ? "" : String(v);
    });
    formEl.querySelectorAll("input[type=radio]").forEach(function (n) { n.checked = getPath(doc, n.name) === n.value; });
    formEl.querySelectorAll("textarea[data-list]").forEach(function (t) {
      var v = getPath(doc, t.dataset.list), nul = formEl.querySelector('input[data-null="' + t.dataset.list + '"]');
      if (nul) nul.checked = v === null;
      t.value = (v || []).join("\n");
    });
    syncLists();
  }

  // 「用本机名单」勾上时名单框不可编辑（null 就是不在这儿列）
  function syncLists() {
    formEl.querySelectorAll("input[data-null]").forEach(function (nul) {
      var t = formEl.querySelector('textarea[data-list="' + nul.dataset.null + '"]');
      t.disabled = nul.checked;
    });
  }

  function sync() {
    var dirty = Boolean(base) && JSON.stringify(read()) !== baseline;
    dirtyEl.hidden = !dirty;
    var has = Boolean(loaded && loaded.settings);
    saveBtn.disabled = busy || !base || !(dirty || !has);
    resetBtn.disabled = busy || !has;
    deviceEl.disabled = busy;
    // 请求在路上时整张表不可改：PUT 回来会用服务端的文档重填，期间的改动会被冲掉
    formEl.querySelectorAll(".set-group").forEach(function (g) { g.disabled = busy; });
  }

  function fill(body) {
    loaded = body;
    base = body.settings ? clone(body.settings) : clone(DEFAULTS);
    ["segmentByTitle", "segmentByTitleApps", "autoTrack"].forEach(function (k) { if (base[k] === undefined) base[k] = DEFAULTS[k]; });
    var pkey = findPresence(base);
    presenceRow.hidden = !pkey;
    if (pkey) presenceEl.dataset.key = pkey; else delete presenceEl.dataset.key;
    write(base);
    baseline = JSON.stringify(read());
    clearErrors();
    formEl.hidden = false;
    showNote(body.settings ? "" :
      "这台设备还没在网页上设过，用的是它本机的配置文件。下面是缺省值；保存之后以这里为准。");
    renderMeta();
    sync();
  }

  function renderMeta() {
    var dev = devices.filter(function (d) { return d.deviceId === device; })[0] || {};
    var has = loaded && loaded.settings;
    metaEl.textContent = "最近上传建议：" + when(dev.lastUploadAt) + " · 最近拉设置：" + when(dev.lastFetchAt) +
      " · 网页设置：" + (has ? "有（" + when(loaded.updatedAt) + " 更新）" : "没有，用本机配置文件");
    var hl = healthLine(dev, Date.now() + skew);
    healthEl.textContent = hl.text;
    restartBtn.disabled = hl.online !== true || Boolean(pending);
    offlineHintEl.hidden = hl.online !== false;
    logHintEl.textContent = hl.gaveUp ? LOG_HINT : "";
    logHintEl.hidden = !hl.gaveUp;
  }

  function showRestart(text) { restartMsgEl.textContent = text; restartMsgEl.hidden = !text; }

  checkBtn.addEventListener("click", async function () {
    checkBtn.disabled = true;
    await refreshDeviceRow();
    checkBtn.disabled = false;
  });

  restartBtn.addEventListener("click", async function () {
    if (pending || !device) return;
    var id = device, mine = { device: id, nonce: null };
    pending = mine;   // 之后一律比对象本身：换设备（A→B→A）会把 pending 清掉，旧的请求回来不能认成现在这个
    restartBtn.disabled = true;
    showRestart("正在请求重启……");
    var r = await request("POST", "restart?deviceId=" + encodeURIComponent(id));
    if (pending !== mine) return;
    if (!r.ok || !r.body || !r.body.nonce) { pending = null; showRestart("请求失败：" + explain(r)); renderMeta(); return; }
    mine.nonce = r.body.nonce;
    showRestart("已请求，等检测程序回报（最多 75 秒）……");
    var deadline = Date.now() + POLL_MAX_MS;
    while (pending === mine) {
      await new Promise(function (ok) { setTimeout(ok, POLL_MS); });
      if (pending !== mine) return;
      await refreshDeviceRow();
      var dev = devices.filter(function (d) { return d.deviceId === id; })[0];
      var q = dev && dev.restart;
      if (pending !== mine) return;
      if (q && q.nonce === mine.nonce && q.result) {
        pending = null;
        showRestart(q.result === "failed" ? "启动失败：" + reasonText(q.reason) + "。" + LOG_HINT
          : own(RESTART_TEXT, q.result, "检测程序回报了未知结果"));
        break;
      }
      if (Date.now() >= deadline) { pending = null; showRestart("检测程序没有响应（它可能没在运行）"); break; }
    }
    renderMeta();
  });

  async function request(method, path, body) {
    var init = { method: method };
    if (body !== undefined) { init.headers = { "Content-Type": "application/json" }; init.body = JSON.stringify(body); }
    var res;
    try { res = await fetch(API + path, init); } catch (err) {
      return { ok: false, status: 0, detail: "网络请求失败：" + ((err && err.message) || String(err)) };
    }
    var j = res.status === 204 ? null : await res.json().catch(function () { return null; });
    return { ok: res.ok, status: res.status, body: j,
      detail: (j && typeof j.detail === "string") ? j.detail : "请求失败（HTTP " + res.status + "）" };
  }

  function explain(r) {
    if (r.status === 403) return "服务器不让改（403）：" + r.detail + "。设置只能在登录后的网页上改，设备令牌只能读。";
    return r.detail;
  }

  async function loadDevice(id) {
    device = id;
    pending = null;
    showRestart("");
    busy = true;
    sync();
    showMessage("", false);
    var r = await request("GET", "settings?deviceId=" + encodeURIComponent(id));
    busy = false;
    if (id !== device) return;   // 人又换了设备：旧的回来晚了，不用
    if (!r.ok) { formEl.hidden = true; base = null; showMessage("读取设置失败：" + explain(r), true); sync(); return; }
    fill(r.body);
  }

  async function loadDevices(selectId) {
    var r = await request("GET", "devices");
    panelEl.hidden = false;
    if (r.status === 404) {   // 后端早于 v2.5
      deviceRowEl.hidden = true;
      showNote("这个版本的后端还不支持在网页上改检测设置（需要 nexus-core v2.5）。");
      return;
    }
    if (!r.ok) { showMessage("设备列表加载失败：" + explain(r), true); return; }
    noteNow(r.body);
    devices = (r.body && r.body.devices) || [];
    deviceEl.textContent = "";
    devices.forEach(function (d) {
      var seen = lastSeen(d);
      deviceEl.appendChild(new Option(d.deviceId + " · " + (seen ? "最近活动 " + when(seen) : "还没见过活动"), d.deviceId));
    });
    deviceRowEl.hidden = !devices.length;
    if (!devices.length) {
      showNote("还没有设备。在电脑上装好 ai-detector、打开同步之后，它会出现在这里。");
      return;
    }
    var want = selectId && devices.some(function (d) { return d.deviceId === selectId; }) ? selectId : devices[0].deviceId;
    deviceEl.value = want;
    await loadDevice(want);
  }

  formEl.addEventListener("input", function () { syncLists(); sync(); });
  formEl.addEventListener("change", function () { syncLists(); sync(); });
  deviceEl.addEventListener("change", function () {
    var dirty = !dirtyEl.hidden;
    if (dirty && !window.confirm("这台设备的改动还没保存，换设备就丢了。继续？")) { deviceEl.value = device; return; }
    loadDevice(deviceEl.value);
  });

  formEl.addEventListener("submit", async function (e) {
    e.preventDefault();
    if (busy || !base) return;
    showMessage("", false);
    var doc = read();
    var first = showErrors(validate(doc));
    if (first) { first.focus(); showMessage("有几项要先改一下（见标红的说明）。", true); return; }
    busy = true;
    sync();
    var id = device;
    var r = await request("PUT", "settings?deviceId=" + encodeURIComponent(id), doc);
    busy = false;
    if (id !== device) return;
    if (r.ok) {
      fill(r.body);
      showMessage("已保存。检测程序下一轮（最多 5 分钟）起按这份设置处理，已经上传的不会改。", false);
      refreshDeviceRow();
      return;
    }
    var key = r.status === 422 ? fieldOf(r.detail) : null;
    if (key && fieldBox(key)) {
      var errs = {};
      errs[key] = r.detail;
      var input = showErrors(errs);
      sync();                       // 先解锁表单（busy 时整组 disabled，聚焦不上）
      if (input) input.focus();
      showMessage("服务器没收：见标红的那一项。", true);
    } else {
      showMessage("保存失败：" + explain(r), true);
    }
    sync();
  });

  resetBtn.addEventListener("click", async function () {
    if (busy || !window.confirm("删掉网页上给这台设备的设置？它会改回用本机的配置文件。")) return;
    busy = true;
    sync();
    showMessage("", false);
    var id = device;
    var r = await request("DELETE", "settings?deviceId=" + encodeURIComponent(id));
    busy = false;
    if (id !== device) return;
    if (!r.ok) { showMessage("恢复默认失败：" + explain(r), true); sync(); return; }
    fill({ deviceId: id, settings: null, updatedAt: null });
    showMessage("已删掉网页上的设置，这台设备下一轮起用它本机的配置文件。", false);
    refreshDeviceRow();
  });

  // 保存 / 删除之后设备行的「网页设置」跟着变：重拉一次列表，不换当前设备、不重填表单
  async function refreshDeviceRow() {
    var r = await request("GET", "devices");
    if (r.ok && r.body && r.body.devices) { noteNow(r.body); devices = r.body.devices; renderMeta(); }
  }

  loadDevices();
})();
