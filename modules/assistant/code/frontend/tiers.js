/**
 * tiers.js —— 「三级分类」在「自动记录」页的界面（nexus-core v2.38；detector.rules.v1 v1.5；detector.settings.v1 v1.4）
 *
 * 先 GET activity/settings。tiers 关着（或读不到：老后端 / 失败）→ 什么都不动，旧的「分类规则」与 autoTrack 照旧。
 * tiers 打开 →
 *   - 旧的「分类规则」区块（#det-rules）换成两级规则栏（#tier-rules）：
 *     左栏第一级 = detector/rules 里 kind:"block" 的规则（程序名 / 标题正则 → 块名），增 / 改 / 删 / 排序，
 *       「保存规则」PUT 整套（旧式规则——含 migrated:true 的——原样带回，只替换第一级规则占的位置），If-Match，412 / 422 沿用 rules.js；
 *     右栏第二级 = activity/block-rules（块名 → 任务 / 项目），增 / 改目标（POST 同块名 = 改）/ 删，broken 的标出；
 *     两栏按目的地项目所在分区的颜色上色（第一级规则的块名没有第二级规则 → 灰）；
 *     activity/proposals 里 kind 为 windowRule / blockRule 的待批准提议以虚线框显示在各栏顶部：批准 / 更正（approve 带 edit）/ 忽略（reject）。
 *   - 隐藏 autoTrack（旧路径）、显示「键盘和鼠标」（inputActivity，settings.js 管读写与说明）。
 * 账号设置块（#tier-settings）：三级分类开关（打开前 confirm）+ agent 权限；PUT activity/settings 带 If-Match "<version>"，
 *   412 重读并提示，409 / 其余错误把 detail 原样显示。服务端的字一律只进 textContent。
 * 对外只挂 window.assistantTiers（纯函数，给单测用）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var CORE = BASE + "api/core/";

  // ── 纯函数 ─────────────────────────────────────────────────────────
  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  // 第一级规则的可比形状（空串 = null）：dirty 只看这几样
  function norm(r) {
    return { id: r.id || null, app: r.app || null, title: r.title || null, block: r.block || "", enabled: r.enabled !== false };
  }
  // 整套规则 + 编辑后的第一级规则 → 要 PUT 的整套。旧式规则逐字原样；第一级规则按原来的位置依次填回，多出来的追加在最后。
  // 同时返回每条第一级规则在整套里的下标（422 的 index 用）。
  function merge(all, blocks) {
    var out = [], at = [], k = 0;
    function wire(r) {
      var o = clone(r), n = norm(r);
      o.kind = "block"; o.block = n.block; o.app = n.app; o.title = n.title; o.enabled = n.enabled;
      if (!o.id) delete o.id;
      delete o._new;
      return o;
    }
    all.forEach(function (r) {
      if (r.kind !== "block") { out.push(clone(r)); return; }
      if (k < blocks.length) { at[k] = out.length; out.push(wire(blocks[k++])); }
    });
    while (k < blocks.length) { at[k] = out.length; out.push(wire(blocks[k++])); }
    return { rules: out, at: at };
  }
  // 提议列表：响应里凡是数组、元素 kind 为 windowRule / blockRule 且待批准的都算（不依赖后端把它们放在哪个键下）
  function pickProposals(body) {
    var out = [];
    Object.keys(body || {}).forEach(function (k) {
      if (!Array.isArray(body[k])) return;
      body[k].forEach(function (it) {
        if (it && (it.kind === "windowRule" || it.kind === "blockRule") && (it.status == null || it.status === "pending")) out.push(it);
      });
    });
    return out;
  }
  window.assistantTiers = { norm: norm, merge: merge, pickProposals: pickProposals };

  // ── DOM ─────────────────────────────────────────────────────────────
  if (typeof document === "undefined") return;
  var boxEl = document.getElementById("tier-settings");
  var rulesBox = document.getElementById("tier-rules");
  if (!boxEl || !rulesBox) return;
  var onEl = document.getElementById("tier-on");
  var directEl = document.getElementById("tier-direct");
  var windowsEl = document.getElementById("tier-windows");
  var recordsEl = document.getElementById("tier-records");
  var acctMsg = document.getElementById("tier-message");
  var msgEl = document.getElementById("tier-rules-message");
  var oldRules = document.getElementById("det-rules");
  var autoGroup = document.getElementById("det-autotrack-group");
  var inputGroup = document.getElementById("det-input-group");
  var l1El = document.getElementById("tier1-list"), l1Pend = document.getElementById("tier1-pending");
  var l2El = document.getElementById("tier2-list"), l2Pend = document.getElementById("tier2-pending");
  var dirtyEl = document.getElementById("tier1-dirty"), saveBtn = document.getElementById("tier1-save");
  var add1 = document.getElementById("tier1-add"), add2 = document.getElementById("tier2-add");

  var TIER_NOTICE = "打开三级分类后，检测程序会多上传每段 ≥ 1 秒的窗口记录（程序名与脱敏标题，脱敏同段）。" +
    "旧的自动记录会停止，旧规则会自动拆成两级。\n\n打开吗？";

  var acct = null;            // GET activity/settings 的整份响应
  var acctBusy = false, busy = false, ops = 0;   // ops：在飞的操作数，最后一个结束才解除 busy
  function begin() { ops++; busy = true; sync(); }
  function end() { ops--; busy = ops > 0; }
  var server = null;          // {version, rules}：第一级栏的底稿
  var l1 = [], l1Base = "";   // 编辑中的第一级规则 / 刚读到时的可比 JSON
  var errors = {};            // l1 下标 → 说明
  var blockRules = [];        // 第二级规则
  var proposals = [];
  var adding2 = false;        // 第二级栏正在填新的一条
  var newKey = "", newPick = "";   // 新增行里已经填的块名 / 去向：后台刷新重画时带回
  var correcting = null;      // 正在更正的提议 id
  var tasks = {}, projects = {}, taskProject = {}, projectColor = {};

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function say(node, text, isError) {
    node.textContent = text || "";
    node.hidden = !text;
    node.classList.toggle("is-error", Boolean(isError));
  }
  async function call(method, url, body, headers) {
    var init = { method: method, headers: Object.assign({}, headers || {}) };
    if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    var res;
    try { res = await fetch(CORE + url, init); } catch (err) {
      return { ok: false, status: 0, detail: "网络请求失败：" + ((err && err.message) || String(err)) };
    }
    var j = res.status === 204 ? null : await res.json().catch(function () { return null; });
    return { ok: res.ok, status: res.status, body: j,
      detail: (j && typeof j.detail === "string") ? j.detail : "请求失败（HTTP " + res.status + "）" };
  }

  // ── 账号设置：三级分类开关 + agent 权限 ──
  function renderAcct() {
    boxEl.hidden = !acct;
    if (!acct) return;
    var d = acct.agentDirect || {};
    onEl.checked = acct.tiers === true;
    directEl.checked = d.enabled === true;
    windowsEl.checked = d.windowsClassify === true;
    recordsEl.checked = d.honeycombRecords === true;
    lockAcct();
  }
  function lockAcct() {   // 请求在路上只锁、不重置勾选（结果回来再按服务端的画）
    onEl.disabled = directEl.disabled = acctBusy;
    windowsEl.disabled = recordsEl.disabled = acctBusy || !directEl.checked;   // 总开关关着，分开关不可改
  }
  async function putAcct(patch, okText) {
    if (acctBusy || !acct) return;
    acctBusy = true;
    lockAcct();
    say(acctMsg, "");
    var doc = clone(acct);
    delete doc.version;
    delete doc.tiersSince;   // 服务端字段，客户端给的忽略
    doc.agentDirect = Object.assign({}, doc.agentDirect, patch.agentDirect || {});
    if ("tiers" in patch) doc.tiers = patch.tiers;
    var r = await call("PUT", "activity/settings", doc, { "If-Match": '"' + acct.version + '"' });
    acctBusy = false;
    if (r.ok) {
      var was = acct.tiers;
      acct = r.body;
      // tiers 翻转：旧编辑器（rules.js）手里是切换前的规则快照，不重载的话之后可能存回过时规则——整页重载
      if (was !== acct.tiers) { window.location.reload(); return; }
      say(acctMsg, okText || "已保存。", false);
      renderAcct();
      return;
    }
    if (r.status === 412) {
      await loadAcct();
      say(acctMsg, "账号设置刚被别处改过，已刷新到最新；请再操作一次。", true);
      return;
    }
    renderAcct();   // 没成：开关勾回服务端的样子
    say(acctMsg, (r.status === 409 ? "没打开：" : "保存失败：") + r.detail, true);
  }
  onEl.addEventListener("change", function () {
    var want = onEl.checked;
    if (want && !window.confirm(TIER_NOTICE)) { onEl.checked = false; return; }
    putAcct({ tiers: want }, want ? "已打开三级分类。" : "已关闭三级分类。");
  });
  directEl.addEventListener("change", function () { putAcct({ agentDirect: { enabled: directEl.checked } }); });
  windowsEl.addEventListener("change", function () { putAcct({ agentDirect: { windowsClassify: windowsEl.checked } }); });
  recordsEl.addEventListener("change", function () { putAcct({ agentDirect: { honeycombRecords: recordsEl.checked } }); });

  async function loadAcct() {
    var r = await call("GET", "activity/settings");
    var wasOn = Boolean(acct && acct.tiers === true), had = Boolean(acct);
    acct = r.ok && r.body && typeof r.body.version === "number" ? r.body : null;
    var on = Boolean(acct && acct.tiers === true);
    if (had && wasOn !== on) { window.location.reload(); return; }   // 别处翻转了 tiers：同上，整页重载
    renderAcct();
    applyMode(on && !had);   // 只有第一次读到「打开」才载规则；其余账号刷新不丢未保存的第一级编辑
  }
  function applyMode(load) {
    var on = Boolean(acct && acct.tiers === true);
    oldRules.hidden = on;
    rulesBox.hidden = !on;
    autoGroup.hidden = on;
    inputGroup.hidden = !on;
    if (load) loadRules(true);
  }

  // ── 颜色与目标 ──
  function indexTree(tree) {
    var zoneColor = {};
    ((tree && tree.zones) || []).forEach(function (z) { zoneColor[z.id] = z.color; });
    tasks = window.assistantRules.paths(tree);
    projects = window.assistantRules.projectPaths(tree);
    taskProject = {}; projectColor = {};
    ((tree && tree.projects) || []).forEach(function (p) {
      projectColor[p.id] = zoneColor[p.zoneId] || null;
      (p.tasks || []).forEach(function (t) { taskProject[t.id] = p.id; });
    });
  }
  function colorOf(target) {   // target: {taskId?, projectId?} → 分区颜色或 null
    var pid = target && (target.projectId || taskProject[target.taskId]);
    return (pid && projectColor[pid]) || null;
  }
  function targetLabel(t) {
    if (t.projectId) return projects[t.projectId] ? projects[t.projectId] + " · 未分类" : "项目已删除（" + t.projectId + "）";
    return tasks[t.taskId] ? tasks[t.taskId].path : "任务已删除（" + t.taskId + "）";
  }
  function ruleFor(block) {
    return blockRules.filter(function (b) { return b.blockKey === block && !b.broken; })[0] || null;
  }
  function paint(node, color) {
    if (color) node.style.setProperty("--tier-c", color); else node.style.removeProperty("--tier-c");
    node.classList.toggle("is-grey", !color);
  }
  function targetSelect(cur, onPick) {   // cur: "p:…" | "t_…" | ""
    var s = el("select", "field");
    if (!cur) s.appendChild(new Option("选一个任务或项目", ""));
    Object.keys(tasks).forEach(function (id) {
      if (!tasks[id].done || id === cur) s.appendChild(new Option(tasks[id].path + (tasks[id].done ? "（已完成）" : ""), id));
    });
    Object.keys(projects).forEach(function (id) { s.appendChild(new Option(projects[id] + " · 未分类（只到项目）", "p:" + id)); });
    if (cur && !Array.prototype.some.call(s.options, function (o) { return o.value === cur; })) {
      s.appendChild(new Option("去向已不在树里（" + cur.replace(/^p:/, "") + "）", cur));
    }
    s.value = cur;
    s.addEventListener("change", function () { if (s.value) onPick(s.value); });
    return s;
  }
  function targetBody(v) { return v.indexOf("p:") === 0 ? { projectId: v.slice(2) } : { taskId: v }; }

  // ── 第一级栏 ──
  function dirty1() { return Boolean(server) && JSON.stringify(l1.map(norm)) !== l1Base; }
  function sync() {
    dirtyEl.hidden = !dirty1();
    saveBtn.disabled = busy || !dirty1();
    add1.disabled = add2.disabled = busy;
    rulesBox.inert = busy;
  }
  function setServer(body) {
    server = { version: body.version, rules: body.rules || [] };
    l1 = server.rules.filter(function (r) { return r.kind === "block"; }).map(clone);
    l1Base = JSON.stringify(l1.map(norm));
    errors = {};
  }
  function renderL1() {
    l1El.textContent = "";
    l1.forEach(function (r, i) {
      var li = el("li", "tier-rule" + (r.enabled === false ? " is-off" : ""));
      li.dataset.index = String(i);
      var rule = ruleFor(r.block);
      paint(li, rule ? colorOf(rule) : null);
      var grid = el("div", "tier-fields");
      [["app", "程序名（正则）"], ["title", "窗口标题（正则）"], ["block", "块名"]].forEach(function (f) {
        var n = el("input", "field");
        n.value = r[f[0]] == null ? "" : String(r[f[0]]);
        n.dataset.f = f[0];
        n.maxLength = f[0] === "block" ? 64 : 200;
        n.setAttribute("aria-label", f[1]);
        n.placeholder = f[1];
        n.addEventListener("input", function () {
          r[f[0]] = n.value;
          if (f[0] === "block") { var t = ruleFor(n.value); paint(li, t ? colorOf(t) : null); dest.textContent = t ? "→ " + targetLabel(t) : ""; }
          sync();
        });
        grid.appendChild(n);
      });
      li.appendChild(grid);
      var dest = el("span", "tier-dest", rule ? "→ " + targetLabel(rule) : "");
      li.appendChild(dest);
      if (typeof r.hitCount === "number") li.appendChild(el("span", "tier-n mono", r.hitCount + " 条"));
      [["↑", "上移", i - 1], ["↓", "下移", i + 1]].forEach(function (m) {
        var b = el("button", "btn btn-ghost rule-move", m[0]);
        b.type = "button";
        b.setAttribute("aria-label", m[1] + "第 " + (i + 1) + " 条");
        b.disabled = m[2] < 0 || m[2] >= l1.length;
        b.addEventListener("click", function () { l1.splice(m[2], 0, l1.splice(i, 1)[0]); errors = {}; renderL1(); });
        li.appendChild(b);
      });
      var del = el("button", "btn btn-ghost danger", "删除");
      del.type = "button";
      del.setAttribute("aria-label", "删除第 " + (i + 1) + " 条");
      del.addEventListener("click", function () { l1.splice(i, 1); errors = {}; renderL1(); });
      li.appendChild(del);
      if (errors[i]) li.appendChild(el("p", "field-error", errors[i]));
      l1El.appendChild(li);
    });
    sync();
  }

  // ── 第二级栏 ──
  function renderL2() {
    l2El.textContent = "";
    if (adding2) {
      var nli = el("li", "tier-rule is-grey");
      nli.id = "tier2-new";
      var key = el("input", "field");
      key.maxLength = 64; key.placeholder = "块名"; key.setAttribute("aria-label", "块名");
      key.value = newKey;
      key.addEventListener("input", function () { newKey = key.value; });
      var sel = targetSelect(newPick, function (v) { newPick = v; });
      sel.addEventListener("change", function () { newPick = sel.value; });   // 选回空白也要清掉，别沿用上一个去向
      var ok = el("button", "btn btn-fact", "添加");
      ok.type = "button";
      ok.addEventListener("click", function () {
        if (!key.value.trim() || !newPick) { say(msgEl, "要填块名并选一个去向。", true); return; }
        postRule(key.value.trim(), newPick);
      });
      var cancel = el("button", "btn btn-ghost", "取消");
      cancel.type = "button";
      cancel.addEventListener("click", function () { adding2 = false; newKey = newPick = ""; renderL2(); });
      [key, sel, ok, cancel].forEach(function (n) { nli.appendChild(n); });
      l2El.appendChild(nli);
    }
    blockRules.forEach(function (b) {
      var li = el("li", "tier-rule" + (b.broken ? " is-broken" : ""));
      li.dataset.id = b.id;
      paint(li, b.broken ? null : colorOf(b));
      li.appendChild(el("span", "tier-pat mono", "块「" + b.blockKey + "」"));
      var cur = b.projectId ? "p:" + b.projectId : b.taskId || "";
      li.appendChild(targetSelect(cur, function (v) { postRule(b.blockKey, v); }));
      if (b.broken) li.appendChild(el("span", "suggest-badge tier-broken", "失效：去向已删除"));
      if (b.via === "direct" || b.via === "ai-direct") li.appendChild(el("span", "suggest-badge", "AI 直接施加"));
      var del = el("button", "btn btn-ghost danger", "删除");
      del.type = "button";
      del.setAttribute("aria-label", "删除块「" + b.blockKey + "」的去向规则");
      del.addEventListener("click", async function () {
        if (busy) return;
        await act(function () { return call("DELETE", "activity/block-rules/" + encodeURIComponent(b.id)); }, "没删成：");
      });
      li.appendChild(del);
      l2El.appendChild(li);
    });
  }
  async function postRule(blockKey, v) {
    if (busy) return;
    var ok = await act(function () { return call("POST", "activity/block-rules", Object.assign({ blockKey: blockKey }, targetBody(v))); }, "没存成：");
    if (ok) { adding2 = false; newKey = newPick = ""; }
    renderL2();
  }

  // ── AI 待批准的规则（虚线框） ──
  var OPS = { add: "新增", edit: "修改", "delete": "删除" };
  function describeProposal(p) {
    var op = OPS[p.op] || "";
    if (p.kind === "windowRule") {
      var r = p.rule || {};
      if (!r.app && !r.title && r.id) r = l1.filter(function (x) { return x.id === r.id; })[0] || r;
      return op + " " + (r.app || "任意程序") + " · " + (r.title || "任意标题") + " → 块「" + ((p.rule && p.rule.block) || r.block || "") + "」";
    }
    return op + " 块「" + p.blockKey + "」" + (p.op === "delete" ? "" : " → " + targetLabel(p));
  }
  function renderPending() {
    l1Pend.textContent = ""; l2Pend.textContent = "";
    proposals.forEach(function (p) {
      var host = p.kind === "windowRule" ? l1Pend : l2Pend;
      var wrap = el("div", "tier-rule is-ai");
      wrap.dataset.id = p.id;
      paint(wrap, p.kind === "blockRule" ? colorOf(p) : null);
      wrap.appendChild(el("span", "tier-pat mono", describeProposal(p)));
      var bar = el("div", "tier-ai-actions");
      function btn(label, fn, cls) {
        var b = el("button", "btn " + (cls || "btn-ghost"), label);
        b.type = "button";
        b.addEventListener("click", fn);
        bar.appendChild(b);
        return b;
      }
      var run = function (path, body) {
        return act(function () { return call("POST", "activity/proposals/" + encodeURIComponent(p.id) + "/" + path, body); }, "没成：");
      };
      if (correcting === p.id) {
        var fix = null;
        if (p.kind === "windowRule") {
          var inp = el("input", "field");
          inp.maxLength = 64; inp.value = (p.rule && p.rule.block) || ""; inp.setAttribute("aria-label", "更正块名");
          inp.addEventListener("input", function () { fix = { rule: { block: inp.value.trim() } }; });
          fix = { rule: { block: inp.value.trim() } };
          wrap.appendChild(inp);
        } else {
          wrap.appendChild(targetSelect(p.projectId ? "p:" + p.projectId : p.taskId || "", function (v) { fix = targetBody(v); }));
          fix = p.projectId ? { projectId: p.projectId } : { taskId: p.taskId };
        }
        btn("确认更正并批准", function () { correcting = null; run("approve", { edit: fix }); }, "btn-fact");
        btn("取消", function () { correcting = null; renderPending(); });
      } else {
        btn("批准", function () { run("approve", {}); }, "btn-fact");
        if (p.op !== "delete") btn("更正", function () { correcting = p.id; renderPending(); });
        btn("忽略", function () { run("reject", {}); });
        if (p.reason) bar.appendChild(el("span", "tier-n", String(p.reason)));
      }
      wrap.appendChild(bar);
      host.appendChild(wrap);
    });
  }

  // ── 读与动作 ──
  // 动作 → 成功后重读（编辑中的第一级规则有没存的改动时不覆盖）；失败显示 detail。返回是否成功
  async function act(fn, failPrefix) {
    if (ops > 0) return false;   // 有别的操作在飞：不并发
    begin();
    say(msgEl, "");
    var r = await fn(), ok = r.ok;
    if (!ok) say(msgEl, failPrefix + r.detail, true); else await loadRules(false);
    end();
    sync();
    return ok;
  }
  async function loadRules(first) {
    var got = await Promise.all([call("GET", "detector/rules"), call("GET", "activity/block-rules"),
      call("GET", "views/tree?includeEphemeral=true"), call("GET", "activity/proposals?status=pending")]);
    if (got[2].ok) indexTree(got[2].body); else indexTree(null);
    blockRules = got[1].ok && got[1].body && Array.isArray(got[1].body.items) ? got[1].body.items : [];
    proposals = got[3].ok ? pickProposals(got[3].body) : [];
    if (!got[0].ok) { say(msgEl, "读取规则失败：" + got[0].detail, true); }
    else if (first || !dirty1()) setServer(got[0].body);
    renderL1(); renderL2(); renderPending();
  }

  add1.addEventListener("click", function () {
    l1.push({ app: "", title: "", block: "", enabled: true });
    renderL1();
    var ins = l1El.querySelectorAll('[data-f="app"]');
    ins[ins.length - 1].focus();
  });
  add2.addEventListener("click", function () { adding2 = true; newKey = newPick = ""; renderL2(); });

  saveBtn.addEventListener("click", async function () {
    if (ops > 0 || !server) return;
    begin();
    say(msgEl, "");
    var m = merge(server.rules, l1);
    var r = await call("PUT", "detector/rules", { rules: m.rules }, { "If-Match": '"' + server.version + '"' });
    if (r.status === 412) {   // 别处先改了：读最新的整套当底稿（旧式规则、别人的第一级规则都以它为准），保留我的第一级编辑
      var g = await call("GET", "detector/rules");
      if (g.ok) server = { version: g.body.version, rules: g.body.rules || [] };
      else server = Object.assign({}, server, { version: r.body && r.body.currentVersion });
    }
    end();
    if (r.ok) {
      setServer(r.body);
      renderL1(); renderL2();
      say(msgEl, "已保存。检测程序下一轮（最多 5 分钟）起按这套规则分块。", false);
      return;
    }
    if (r.status === 412) {   // 同 rules.js：不覆盖，换上最新版本号，人再点一次才用他的版本覆盖
      say(msgEl, "规则刚被别处改过（现在是第 " + server.version + " 版）。下面仍是你的第一级改动：再点「保存规则」会把它们并进最新的规则；想放弃就刷新页面。", true);
      sync();
      return;
    }
    errors = {};
    if (r.status === 422 && r.body && Array.isArray(r.body.errors)) {
      r.body.errors.forEach(function (e) {
        var k = m.at.indexOf(e.index);
        if (k >= 0) errors[k] = (errors[k] ? errors[k] + "；" : "") + (e.field ? e.field + "：" : "") + e.message;
      });
    }
    renderL1();
    say(msgEl, "保存失败：" + r.detail, true);
  });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && acct && acct.tiers === true && !busy) loadRules(false);
  });

  loadAcct();
})();
