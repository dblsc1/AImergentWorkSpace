/**
 * rules.js —— 「分类规则」（contracts/detector.rules.v1，nexus-core v2.6）
 *
 * 规则把检测到的窗口归到任务：按顺序第一条命中生效。规则多由 AI 助理起草（MCP propose_detector_rules），
 * 草稿不生效；这里显示「AI 草稿：新增 N / 修改 M / 删除 K」，人点一下「应用」整套生效。人也能手改：
 * 增、删、改、上下挪，「保存规则」PUT 整套，带 If-Match（读到的 version）。
 *
 * - 412（别处先改了，比如刚应用了草稿）：不覆盖，更新到最新 version 并提示；人再点一次保存才用他的版本覆盖。
 * - 422 的 errors[{index, field, message}] 挂到对应那一行的对应格下。
 * - 规则文本（正则、备注、AI 写的说明）一律 textContent / value，不进 innerHTML。
 * - 端点 404（后端早于 v2.6）→ 只留一句说明，不出编辑器。
 * 对外只挂 window.assistantRules（纯函数，给单测用）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var API = BASE + "api/core/detector/rules";

  // ── 纯函数 ─────────────────────────────────────────────────────────
  // 编辑中的一行 → 发给服务端的规则（空串 = null；新行不带 id，服务端分配）
  function toWire(r) {
    var out = { app: r.app || null, title: r.title || null, taskId: r.taskId, confidence: Number(r.confidence),
      note: r.note || null, enabled: r.enabled !== false };
    if (r.id) out.id = r.id;
    return out;
  }
  function counts(diff) {
    return "新增 " + diff.added.length + " / 修改 " + diff.changed.length + " / 删除 " + diff.removed.length +
      (diff.reordered ? "（顺序有变）" : "");
  }
  function paths(tree) {
    var zones = {}, out = {};
    ((tree && tree.zones) || []).forEach(function (z) { zones[z.id] = z.name; });
    ((tree && tree.projects) || []).forEach(function (p) {
      (p.tasks || []).forEach(function (t) {
        out[t.id] = { path: (zones[p.zoneId] || "?") + " / " + p.name + " / " + t.name, done: t.done === true };
      });
    });
    return out;
  }
  function describe(r, tasks) {
    var what = [r.app ? "程序 /" + r.app + "/" : "", r.title ? "标题 /" + r.title + "/" : ""].filter(Boolean).join(" 且 ");
    var t = tasks[r.taskId];
    return what + " → " + (t ? t.path : "任务已删除（" + r.taskId + "）") + " · " + Math.round(r.confidence * 100) + "%" +
      (r.enabled === false ? " · 停用" : "") + (r.note ? " · " + r.note : "");
  }

  window.assistantRules = { toWire: toWire, counts: counts, paths: paths, describe: describe };

  // ── DOM ─────────────────────────────────────────────────────────────
  var boxEl = document.getElementById("det-rules");
  if (!boxEl) return;
  var editorEl = document.getElementById("rules-editor");
  var noteEl = document.getElementById("rules-note");
  var listEl = document.getElementById("rules-list");
  var emptyEl = document.getElementById("rules-empty");
  var dirtyEl = document.getElementById("rules-dirty");
  var addBtn = document.getElementById("rules-add");
  var saveBtn = document.getElementById("rules-save");
  var msgEl = document.getElementById("rules-message");
  var draftEl = document.getElementById("rules-draft");
  var draftHeadEl = document.getElementById("rules-draft-head");
  var draftSumEl = document.getElementById("rules-draft-summary");
  var draftMetaEl = document.getElementById("rules-draft-meta");
  var diffEl = document.getElementById("rules-diff-list");
  var applyBtn = document.getElementById("rules-apply");
  var discardBtn = document.getElementById("rules-discard");

  var tasks = {};          // taskId → {path, done}
  var server = null;       // 编辑器的底稿 {version, rules}：保存时 If-Match 带它的 version
  var current = null;      // 最近一次读到的生效规则（草稿的「旧值」按它显示；有没保存的改动时可能比底稿新）
  var rules = [];          // 编辑中的副本
  var draft = null;
  var busy = false;
  var errors = {};         // 行下标 → {字段|"": 说明}

  function showMessage(text, isError) {
    msgEl.textContent = text;
    msgEl.hidden = !text;
    msgEl.classList.toggle("is-error", Boolean(isError));
  }
  function dirty() {
    return Boolean(server) && JSON.stringify(rules.map(toWire)) !== JSON.stringify(server.rules.map(toWire));
  }
  function sync() {
    var d = dirty();
    dirtyEl.hidden = !d;
    saveBtn.disabled = busy || !d;
    addBtn.disabled = busy;
    applyBtn.disabled = discardBtn.disabled = busy;
    editorEl.inert = busy;   // 请求在路上时编辑器不可改：回来会用服务端的规则重填，期间的改动会被冲掉
    emptyEl.hidden = rules.length > 0;
  }

  async function request(method, path, opts) {
    var res;
    try { res = await fetch(API + path, opts ? { method: method, headers: opts.h, body: opts.body } : { method: method }); }
    catch (err) { return { ok: false, status: 0, detail: "网络请求失败：" + ((err && err.message) || String(err)) }; }
    var j = res.status === 204 ? null : await res.json().catch(function () { return null; });
    return { ok: res.ok, status: res.status, body: j,
      detail: (j && typeof j.detail === "string") ? j.detail : "请求失败（HTTP " + res.status + "）" };
  }
  function ifMatch(version, body) {
    var h = { "If-Match": '"' + version + '"' };
    if (body !== undefined) h["Content-Type"] = "application/json";
    return { h: h, body: body === undefined ? undefined : JSON.stringify(body) };
  }
  function explain(r) {
    return r.status === 403 ? "服务器不让改（403）：" + r.detail + "。规则只能在登录后的网页上改。" : r.detail;
  }

  // ── 规则列表 ──
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function field(label, input, err) {
    var wrap = el("label", "set-field rule-field");
    wrap.appendChild(el("span", "set-label", label));
    wrap.appendChild(input);
    if (err) {
      input.setAttribute("aria-invalid", "true");
      wrap.appendChild(el("span", "field-error", err));
    }
    return wrap;
  }
  function input(r, key, attrs) {
    var n = el("input", "field");
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    n.value = r[key] == null ? "" : String(r[key]);
    n.dataset.f = key;
    n.addEventListener("input", function () { r[key] = n.value; sync(); });
    return n;
  }
  function taskSelect(r) {
    var s = el("select", "field");
    s.dataset.f = "taskId";
    var ids = Object.keys(tasks).filter(function (id) { return !tasks[id].done || id === r.taskId; });
    if (!r.taskId) s.appendChild(new Option("选一个任务", ""));
    if (r.taskId && !tasks[r.taskId]) s.appendChild(new Option("任务已删除（" + r.taskId + "）", r.taskId));
    ids.forEach(function (id) { s.appendChild(new Option(tasks[id].path + (tasks[id].done ? "（已完成）" : ""), id)); });
    s.value = r.taskId || "";
    s.addEventListener("change", function () { r.taskId = s.value; sync(); });
    return s;
  }
  function move(i, to) {
    var r = rules.splice(i, 1)[0];
    rules.splice(to, 0, r);
    errors = {};
    render();
  }
  function render() {
    listEl.textContent = "";
    rules.forEach(function (r, i) {
      var err = errors[i] || {};
      var li = el("li", "rule-item" + (r.enabled === false ? " is-off" : ""));
      li.dataset.index = String(i);
      var head = el("div", "rule-head");
      head.appendChild(el("span", "rule-no mono", "#" + (i + 1)));
      var on = el("label", "set-opt rule-on");
      var cb = el("input");
      cb.type = "checkbox";
      cb.checked = r.enabled !== false;
      cb.dataset.f = "enabled";
      cb.addEventListener("change", function () { r.enabled = cb.checked; li.classList.toggle("is-off", !cb.checked); sync(); });
      on.appendChild(cb);
      on.appendChild(document.createTextNode(" 启用"));
      head.appendChild(on);
      [["↑", "上移", i - 1], ["↓", "下移", i + 1]].forEach(function (m) {
        var b = el("button", "btn btn-ghost rule-move", m[0]);
        b.type = "button";
        b.setAttribute("aria-label", m[1] + "第 " + (i + 1) + " 条");
        b.disabled = m[2] < 0 || m[2] >= rules.length;
        b.addEventListener("click", function () { move(i, m[2]); sync(); });
        head.appendChild(b);
      });
      var del = el("button", "btn btn-ghost danger", "删除");
      del.type = "button";
      del.setAttribute("aria-label", "删除第 " + (i + 1) + " 条");
      del.addEventListener("click", function () { rules.splice(i, 1); errors = {}; render(); sync(); });
      head.appendChild(del);
      li.appendChild(head);
      var grid = el("div", "rule-grid");
      grid.appendChild(field("程序名（正则）", input(r, "app", { maxlength: "200", placeholder: "比如 code|goland" }), err.app));
      grid.appendChild(field("窗口标题（正则）", input(r, "title", { maxlength: "200", placeholder: "比如 garden" }), err.title));
      grid.appendChild(field("归到任务", taskSelect(r), err.taskId));
      grid.appendChild(field("把握（0–1）", input(r, "confidence", { type: "number", min: "0.01", max: "1", step: "0.05", inputmode: "decimal" }), err.confidence));
      grid.appendChild(field("备注", input(r, "note", { maxlength: "120" }), err.note));
      li.appendChild(grid);
      var rest = Object.keys(err).filter(function (k) { return ["app", "title", "taskId", "confidence", "note"].indexOf(k) < 0; });
      if (rest.length) li.appendChild(el("p", "field-error", rest.map(function (k) { return err[k]; }).join("；")));
      listEl.appendChild(li);
    });
    sync();
  }
  function setServer(body) {
    server = current = { version: body.version, rules: body.rules || [] };
    rules = JSON.parse(JSON.stringify(server.rules));
    errors = {};
    render();
  }

  // ── AI 草稿 ──
  function renderDraft() {
    draftEl.hidden = !draft;
    if (!draft) return;
    var d = draft.diff;
    draftHeadEl.textContent = (draft.author === "human" ? "草稿：" : "AI 草稿：") + counts(d);
    draftSumEl.textContent = draft.summary;
    var exp = new Date(draft.expiresAt);
    draftMetaEl.textContent = "共 " + draft.rules.length + " 条" + (d.unchanged ? "，其中 " + d.unchanged + " 条不变" : "") +
      " · " + (exp.getMonth() + 1) + "-" + exp.getDate() + " 前不应用就作废";
    diffEl.textContent = "";
    var cur = {};
    ((current && current.rules) || []).forEach(function (r) { cur[r.id] = r; });
    draft.rules.forEach(function (r) {
      var kind = d.added.indexOf(r.id) >= 0 ? "add" : d.changed.indexOf(r.id) >= 0 ? "change" : null;
      if (!kind) return;
      var li = el("li", "diff-" + kind);
      li.appendChild(el("span", "diff-tag", kind === "add" ? "新增" : "修改"));
      if (kind === "change" && cur[r.id]) li.appendChild(el("span", "diff-old", describe(cur[r.id], tasks)));
      li.appendChild(el("span", "diff-new", describe(r, tasks)));
      diffEl.appendChild(li);
    });
    if (d.reordered) {   // 顺序决定哪条先命中：内容没变、只是挪了位置的也列出来
      var was = {};
      ((current && current.rules) || []).forEach(function (r, i) { was[r.id] = i; });
      draft.rules.forEach(function (r, i) {
        if (d.added.indexOf(r.id) >= 0 || d.changed.indexOf(r.id) >= 0 || was[r.id] === i) return;
        var li = el("li", "diff-move");
        li.appendChild(el("span", "diff-tag", "挪动 #" + (was[r.id] + 1) + " → #" + (i + 1)));
        li.appendChild(el("span", "diff-new", describe(r, tasks)));
        diffEl.appendChild(li);
      });
    }
    d.removed.forEach(function (id) {
      var li = el("li", "diff-remove");
      li.appendChild(el("span", "diff-tag", "删除"));
      li.appendChild(el("span", "diff-old", cur[id] ? describe(cur[id], tasks) : id));
      diffEl.appendChild(li);
    });
  }
  async function loadDraft() {
    var r = await request("GET", "/drafts/current");
    var next = r.ok && r.body ? r.body.draft : null;   // 拿齐（草稿 + 对应的生效规则）才换上，期间「应用」仍指旧草稿
    if (next && current && next.currentVersion !== current.version) {
      // 别处改过规则：旧值按最新的生效规则显示。没手改就连编辑器一起刷新；有手改就留着（保存时照样 412）
      var g = await request("GET", "");
      if (!g.ok) {   // 拿不到草稿对应的生效规则：不显示草稿（否则「旧值」是过时的，人看不到真正会被换掉的）
        draft = null;
        renderDraft();
        showMessage("读取最新规则失败，AI 草稿先不显示：" + explain(g), true);
        return;
      }
      if (dirty()) current = { version: g.body.version, rules: g.body.rules || [] }; else setServer(g.body);
    }
    draft = next;
    renderDraft();
  }

  async function load() {
    var r = await request("GET", "");
    if (r.status === 404) {   // 后端早于 v2.6
      noteEl.textContent = "这个版本的后端还不支持在网页上管分类规则（需要 nexus-core v2.6）。";
      noteEl.hidden = false;
      return;
    }
    if (!r.ok) { showMessage("读取规则失败：" + explain(r), true); return; }
    try {
      var t = await fetch(BASE + "api/core/views/tree");
      if (t.ok) tasks = paths(await t.json());
    } catch (err) { /* 没有任务树：下拉只剩规则里已有的 taskId */ }
    editorEl.hidden = false;
    setServer(r.body);
    await loadDraft();
  }

  addBtn.addEventListener("click", function () {
    rules.push({ app: "", title: "", taskId: "", confidence: 0.9, note: "", enabled: true });
    render();
    var inputs = listEl.querySelectorAll('[data-f="app"]');
    inputs[inputs.length - 1].focus();
  });

  saveBtn.addEventListener("click", async function () {
    if (busy || !server) return;
    busy = true;
    sync();
    showMessage("", false);
    var r = await request("PUT", "", ifMatch(server.version, { rules: rules.map(toWire) }));
    busy = false;
    if (r.ok) {
      setServer(r.body);
      showMessage("已保存。检测程序下一轮（最多 5 分钟）起按这套规则给建议。", false);
      loadDraft();   // 草稿的「改动」按新的生效规则重算
      return;
    }
    if (r.status === 412) {
      // 下面仍是你的版本；再点保存 = 用你的覆盖。新对象：current（草稿旧值的底）不跟着变，下次读草稿会重拉
      server = { version: r.body.currentVersion, rules: server.rules };
      showMessage("规则刚被别处改过（现在是第 " + server.version + " 版，可能是刚应用了草稿）。下面仍是你的改动：" +
        "再点「保存规则」就用它覆盖；想放弃就刷新页面。", true);
      sync();
      return;
    }
    errors = {};
    if (r.status === 422 && r.body && Array.isArray(r.body.errors)) {
      r.body.errors.forEach(function (e) {
        if (e.index == null) return;
        (errors[e.index] = errors[e.index] || {})[e.field || ""] = e.message;
      });
    }
    render();
    showMessage("保存失败：" + explain(r), true);
  });

  applyBtn.addEventListener("click", async function () {
    if (busy || !draft) return;
    if (dirty() && !window.confirm("你有没保存的规则改动，应用草稿会把它们换掉。继续？")) return;
    busy = true;
    sync();
    showMessage("", false);
    var r = await request("POST", "/drafts/" + encodeURIComponent(draft.id) + "/apply", ifMatch(draft.currentVersion));
    busy = false;
    if (r.ok) {
      draft = null;
      renderDraft();
      setServer(r.body);
      showMessage("已应用。检测程序下一轮（最多 5 分钟）起按新规则给建议。", false);
      return;
    }
    if (r.status === 412 || r.status === 404) {
      showMessage(r.status === 412 ? "规则在你看草稿之后被改过，已重新比对，请再看一眼改动。" : "这份草稿已经不在了（过期、被丢弃或被新草稿顶掉）。", true);
      await loadDraft();
      sync();
      return;
    }
    showMessage("应用失败：" + explain(r), true);
    sync();
  });

  discardBtn.addEventListener("click", async function () {
    if (busy || !draft) return;
    busy = true;
    sync();
    var r = await request("POST", "/drafts/" + encodeURIComponent(draft.id) + "/discard");
    busy = false;
    if (!r.ok) { showMessage("丢弃失败：" + explain(r), true); sync(); return; }
    draft = null;
    renderDraft();
    showMessage("草稿已丢弃。", false);
    sync();
  });

  // AI 刚答完一轮（可能写了草稿）、或标签页重新可见：再看一次有没有草稿。不动编辑中的规则。
  document.addEventListener("assistant:turn-done", function () { if (server) loadDraft(); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && server) loadDraft();
  });

  load();
})();
