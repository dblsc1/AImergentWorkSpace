/**
 * ring-chat.js —— 「问问助手」聊天面板（contracts/agent.chat.v1）
 *
 * 只跟 <站点前缀>api/agent/ 说话：列会话、建会话、删会话、读历史、发消息（POST 回的是 SSE 流，
 * 用 fetch + ReadableStream 自己解析——EventSource 只会 GET）、停止。前端不认识任何代理运行时的形状。
 *
 * - health 不是 200 / 不是 JSON（没装聊天后端：网关 404/502，或没登录被 302 到登录页）→ 整块不出现。
 * - configured:false → 面板在，但说明「去 .env 填 AGENT_API_KEY」，输入框禁用。
 * - 模型输出、会话标题、错误 detail 一律 textContent，不进 innerHTML。
 * - 不认识的 SSE 事件一律忽略（契约 v1 之内可以追加事件）。
 * 对外只挂 window.ringChat（纯函数，给单测用）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var API = BASE + "api/agent/";

  // ── 纯函数 ─────────────────────────────────────────────────────────
  // 把累积的文本切成完整的 SSE 事件；没收完的尾巴原样还回去等下一块。
  // 注释行（": ping"）丢掉；一条事件可以有多行 data:，按规范用 \n 拼。
  function parseSSE(buf) {
    var events = [];
    var blocks = buf.replace(/\r\n/g, "\n").split("\n\n");
    var rest = blocks.pop();
    blocks.forEach(function (block) {
      var type = "message", data = [];
      block.split("\n").forEach(function (line) {
        if (line.indexOf("event:") === 0) type = line.slice(6).trim();
        else if (line.indexOf("data:") === 0) data.push(line.slice(5).replace(/^ /, ""));
      });
      if (!data.length) return;
      try { events.push({ type: type, data: JSON.parse(data.join("\n")) }); } catch (e) { /* 坏的一条跳过 */ }
    });
    return { events: events, rest: rest };
  }

  function pad2(n) { return String(n).padStart(2, "0"); }
  function sessionLabel(s) {
    if (s.title) return s.title;
    var d = new Date(s.createdAt);
    return pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + " 的对话";
  }

  window.ringChat = { parseSSE: parseSSE, sessionLabel: sessionLabel };

  // ── DOM ─────────────────────────────────────────────────────────────
  var panelEl = document.getElementById("chat-panel");
  var selectEl = document.getElementById("chat-session");
  var newBtn = document.getElementById("chat-new");
  var delBtn = document.getElementById("chat-delete");
  var logEl = document.getElementById("chat-log");
  var toolEl = document.getElementById("chat-tool");
  var msgEl = document.getElementById("chat-message");
  var formEl = document.getElementById("chat-form");
  var inputEl = document.getElementById("chat-input");
  var sendBtn = document.getElementById("chat-send");
  var stopBtn = document.getElementById("chat-stop");
  if (!panelEl || !formEl) return;

  var configured = false;
  var current = null;      // 当前会话 id
  var generating = false;

  function showMessage(text, isError) {
    msgEl.textContent = text;
    msgEl.hidden = !text;
    msgEl.classList.toggle("is-error", Boolean(isError));
  }

  function sync() {
    inputEl.disabled = !configured || generating;
    sendBtn.hidden = generating;
    stopBtn.hidden = !generating;
    sendBtn.disabled = !configured;
    selectEl.disabled = newBtn.disabled = generating;
    delBtn.disabled = generating || !current;
  }

  async function call(method, path, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    var res = await fetch(API + path, opts);
    if (!res.ok) {
      var j = await res.json().catch(function () { return null; });
      throw new Error((j && typeof j.detail === "string") ? j.detail : "聊天服务出错（HTTP " + res.status + "）");
    }
    return res.status === 204 ? null : res.json();
  }

  function bubble(role, text) {
    var p = document.createElement("p");
    p.className = "chat-msg chat-" + role;
    p.textContent = text;
    logEl.appendChild(p);
    logEl.scrollTop = logEl.scrollHeight;
    return p;
  }

  async function loadSessions(selectId) {
    var body = await call("GET", "sessions");
    var items = body.items || [];
    selectEl.textContent = "";
    items.forEach(function (s) { selectEl.appendChild(new Option(sessionLabel(s), s.id)); });
    if (!items.length) selectEl.appendChild(new Option("还没有对话", ""));
    current = selectId && items.some(function (s) { return s.id === selectId; }) ? selectId
      : (items[0] ? items[0].id : null);
    selectEl.value = current || "";
    await loadHistory();
  }

  async function loadHistory() {
    logEl.textContent = "";
    toolEl.hidden = true;
    sync();
    if (!current) return;
    var body = await call("GET", "sessions/" + encodeURIComponent(current));
    body.messages.forEach(function (m) { bubble(m.role, m.text); });
  }

  async function send(text) {
    showMessage("", false);
    if (!current) {
      var s = await call("POST", "sessions", { title: text.slice(0, 30) });
      await loadSessions(s.id);
    }
    generating = true;
    sync();
    var sid = current, answer = null, ended = false;
    try {
      var res = await fetch(API + "sessions/" + encodeURIComponent(sid) + "/messages", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: text }),
      });
      if (!res.ok) {
        var j = await res.json().catch(function () { return null; });
        throw new Error((j && typeof j.detail === "string") ? j.detail : "发送失败（HTTP " + res.status + "）");
      }
      inputEl.value = "";
      bubble("user", text);
      var reader = res.body.getReader(), dec = new TextDecoder(), buf = "";
      for (;;) {
        var chunk = await reader.read();
        if (chunk.done) break;
        var parsed = parseSSE(buf + dec.decode(chunk.value, { stream: true }));
        buf = parsed.rest;
        parsed.events.forEach(function (ev) {
          var d = ev.data || {};
          if (ev.type === "delta") {
            if (!answer) answer = bubble("assistant", "");
            answer.textContent += d.text || "";
            logEl.scrollTop = logEl.scrollHeight;
          } else if (ev.type === "tool") {
            toolEl.textContent = d.status === "running" ? "正在查：" + d.name + "…" : "";
            toolEl.hidden = d.status !== "running";
          } else if (ev.type === "done") {
            ended = true;
            if (d.reason === "cancelled") bubble("note", "（已停止）");
          } else if (ev.type === "error") {
            ended = true;
            showMessage((d.detail || "出错了") + (d.correlationId ? "（" + d.correlationId + "）" : ""), true);
          }
        });
      }
      if (!ended) showMessage("连接断了，已收到的部分已保存。", true);
    } catch (err) {
      showMessage(err.message, true);
    } finally {
      generating = false;
      toolEl.hidden = true;
      sync();
    }
  }

  formEl.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = inputEl.value.trim();
    if (text && configured && !generating) send(text);
  });
  inputEl.addEventListener("keydown", function (e) {   // Enter 发送，Shift+Enter 换行
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); formEl.requestSubmit(); }
  });
  stopBtn.addEventListener("click", function () {
    if (current) call("POST", "sessions/" + encodeURIComponent(current) + "/cancel", {}).catch(function (err) {
      showMessage(err.message, true);
    });
  });
  selectEl.addEventListener("change", function () {
    current = selectEl.value || null;
    showMessage("", false);
    loadHistory().catch(function (err) { showMessage(err.message, true); });
  });
  // 建 / 删期间两个按钮都禁用：连点不会建出两段、删两次
  function once(work) {
    newBtn.disabled = delBtn.disabled = true;
    showMessage("", false);
    work().catch(function (err) { showMessage(err.message, true); }).then(sync);
  }
  newBtn.addEventListener("click", function () {
    once(function () { return call("POST", "sessions", {}).then(function (s) { return loadSessions(s.id); }); });
  });
  delBtn.addEventListener("click", function () {
    if (!current || !window.confirm("删掉这段对话？删了就找不回来。")) return;
    once(function () {
      return call("DELETE", "sessions/" + encodeURIComponent(current)).then(function () { return loadSessions(); });
    });
  });

  (async function init() {
    var health;
    try {
      var res = await fetch(API + "health");
      health = res.ok ? await res.json() : null;
    } catch (err) {
      health = null;
    }
    if (!health || health.status !== "ok") return;   // 没装聊天后端：整块不出现
    configured = health.configured === true;
    panelEl.hidden = false;
    if (!configured) showMessage("还没配模型：在 .env 里填 AGENT_API_KEY（换模型再填 AGENT_MODEL），重启 HoneyComb。", false);
    sync();
    try { await loadSessions(); } catch (err) { showMessage(err.message, true); }
  })();
})();
