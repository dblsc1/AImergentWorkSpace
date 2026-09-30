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
 * - 强制脱敏（密码、密钥……）不在文档里，页面上是勾着的灰框，关不掉。
 * - 422 的 detail 形如「privacy.pathWhitelist: …」：按前缀挂到对应那一项下面，对不上就显示在表单底部。
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
  };
  var LIMITS = {   // 契约「校验」：列表条数、每条长度；整数范围
    "privacy.pathWhitelist": [20, 200], "privacy.appOnlyApps": [200, 64], "idle.focusApps": [200, 64],
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
  function lines(text, verbatim) {
    return String(text || "").split("\n").map(function (l) { return verbatim ? l.replace(/\r$/, "") : l.trim(); })
      .filter(function (l) { return l !== ""; });
  }

  // 白名单正则的本地检查：只拦肯定不行的（长度、RE2 不支持的构造，同服务端那条规则）。
  // 语法本身交给服务端（422 挂回这一项）——JS 的 RegExp 与 RE2 不同，拿它判会误拦合法写法（如 \Q…\E）。
  var NOT_RE2 = /\(\?<?[=!]|\\[1-9]|\(\?P=|\(\?\(|\(\?>|(?<!\\)[*+?}]\+/;
  function checkPattern(p) {
    if (p.length > 200) return "太长（最多 200 个字符）";
    var probe = p.replace(/\\[pP](\{[^}]*\}|[A-Za-z])/g, "x");   // 同服务端：\p{Han}+ 的「}+」不是占有量词
    if (NOT_RE2.test(probe)) return "用了 RE2 不支持的写法（前后查找 / 反向引用 / 条件组 / 原子组 / 占有量词）";
    return "";
  }

  // 整份文档的本地校验 → {字段: 说明}；空对象 = 没问题
  function validate(doc) {
    var errs = {};
    ["privacy.pathWhitelist", "privacy.appOnlyApps", "idle.focusApps"].forEach(function (key) {
      var v = getPath(doc, key), lim = LIMITS[key];
      if (v === null) return;
      if (v.length > lim[0]) { errs[key] = "最多 " + lim[0] + " 条（现在 " + v.length + " 条）"; return; }
      for (var i = 0; i < v.length; i++) {
        var msg = key === "privacy.pathWhitelist" ? checkPattern(v[i])
          : (v[i].length > lim[1] ? "每项最多 " + lim[1] + " 个字符" : "");
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
    var m = /^((?:privacy|idle)\.[A-Za-z]+|presence)\b/.exec(String(detail || ""));
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

  window.assistantSettings = {
    DEFAULTS: DEFAULTS, checkPattern: checkPattern, validate: validate, fieldOf: fieldOf, findPresence: findPresence,
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

  var devices = [];
  var device = null;        // 当前设备 id
  var loaded = null;        // GET 回来的整份响应 {deviceId, settings, updatedAt}
  var base = null;          // 表单的底稿：settings 或 DEFAULTS（未知键从这里原样带回去）
  var baseline = "";        // 底稿刚填进表单时 read() 的 JSON：不同 = 有没保存的改动
  var busy = false;

  function showMessage(text, isError) {
    msgEl.textContent = text;
    msgEl.hidden = !text;
    msgEl.classList.toggle("is-error", Boolean(isError));
  }
  function showNote(text) { noteEl.textContent = text; noteEl.hidden = !text; }

  function fieldBox(key) { return formEl.querySelector('[data-field="' + key + '"]'); }
  function clearErrors() {
    formEl.querySelectorAll(".field-error").forEach(function (p) { p.hidden = true; p.textContent = ""; });
    formEl.querySelectorAll("[aria-invalid]").forEach(function (n) { n.removeAttribute("aria-invalid"); });
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
      if (input) { input.setAttribute("aria-invalid", "true"); first = first || input; }
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
  }

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
    if (r.ok && r.body && r.body.devices) { devices = r.body.devices; renderMeta(); }
  }

  loadDevices();
})();
