/**
 * tokens.js —— 「Agent 令牌」面板（contracts/auth.gate.v1 v1.4「权限范围与匿名上报」）
 *
 * 登录了的人在这里给 AI 代理、同步程序发访问令牌：选权限（只上报 / 只读 / 读写）、写个备注、生成。
 * **令牌从不自动生成**——只有人点了「生成令牌」才发。列表里是自己名下的令牌，可以单独吊销。
 *
 * - GET <前缀>api/auth/tokens 回 200 才出现这一块；401 / 404（老的或换掉的认证服务）整块不出现；
 *   503（没设 AUTH_SECRET）出现一句说明、没有表单。
 * - **令牌本身只显示一次**：只在发令牌那次响应里有，这里只放进内存里的一个变量与页面上的一个节点。
 *   不写 localStorage / sessionStorage / cookie，不进地址栏，不进 console；点「我已保存好」或再发一个就清掉。
 * - 备注是用户自己写的字，一律 textContent。
 * 对外只挂 window.assistantTokens（纯函数，给单测用）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var AUTH = BASE + "api/auth/";
  var SCOPE_NAME = { report: "只上报", read: "只读", write: "读写" };

  // ── 纯函数 ─────────────────────────────────────────────────────────
  /** 可以直接粘贴的配置片段：[{title, text}]。site = 站点地址（含前缀，以 / 结尾）。 */
  function snippets(scope, token, site) {
    var hooks = {
      title: "代理钩子（tools/agent-hooks）的配置文件 agent-hooks.json",
      text: JSON.stringify({ url: site, token: token }, null, 2),
    };
    var env = {
      title: "或者用环境变量",
      text: "export COCKPIT_URL=" + site + "\nexport COCKPIT_TOKEN=" + token,
    };
    var mcp = {
      title: "MCP 客户端（Streamable HTTP）",
      text: JSON.stringify({ mcpServers: { honeycomb: {
        type: "http", url: site + "api/mcp/", headers: { Authorization: "Bearer " + token },
      } } }, null, 2),
    };
    var detector = {
      title: "桌面检测程序（ai-detector）的配置",
      text: JSON.stringify({ cockpitUrl: site, deviceToken: token }, null, 2),
    };
    if (scope === "report") return [hooks, env];
    if (scope === "read") return [mcp, hooks];
    return [detector, mcp];
  }
  function day(iso) { return String(iso || "").slice(0, 10); }
  function describe(t) {
    return SCOPE_NAME[t.scope] || t.scope;
  }
  var api = { snippets: snippets, day: day, describe: describe };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window === "undefined" || !window.document) return;
  window.assistantTokens = api;

  // ── 页面 ───────────────────────────────────────────────────────────
  var $ = function (id) { return document.getElementById(id); };
  var panel = $("tokens-panel");
  if (!panel) return;
  var form = $("tok-form"), note = $("tok-note"), message = $("tok-message"), list = $("tok-list");
  var fresh = $("tok-new"), secretEl = $("tok-secret"), snippetsEl = $("tok-snippets");
  var secret = "";   // 刚发出来的令牌：只活在这个变量与 #tok-secret 里

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function say(text, isError) {
    message.textContent = text || "";
    message.hidden = !text;
    message.classList.toggle("is-error", !!isError);
  }
  async function request(method, path, body) {
    var init = { method: method, cache: "no-store" };
    if (body !== undefined) { init.headers = { "Content-Type": "application/json" }; init.body = JSON.stringify(body); }
    var res;
    try { res = await fetch(AUTH + path, init); } catch (err) { return { status: 0, body: null }; }
    var j = res.status === 204 ? null : await res.json().catch(function () { return null; });
    return { status: res.status, body: j };
  }
  function why(r) {
    var code = r.body && r.body.error;
    if (r.status === 401) return "登录已过期，重新登录后再来。";
    if (r.status === 409 || code === "too_many_tokens") return "名下的令牌太多了（上限 100 个）：先吊销用不着的。";
    if (r.status === 503) return "这台服务器现在发不了令牌（没设 AUTH_SECRET，或令牌文件写不了）。";
    if (r.status === 404) return "没有这个令牌（可能已经到期）。";
    if (r.status === 0) return "网络请求失败。";
    return "没成功（HTTP " + r.status + (code ? "，" + code : "") + "）。";
  }

  async function copy(text, button) {
    var ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (err) { ok = false; }
    if (!ok) {   // 非安全来源（局域网 HTTP）没有 clipboard API：退回选中 + execCommand
      var ta = el("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.className = "tok-offscreen";
      document.body.appendChild(ta); ta.select();
      try { ok = document.execCommand("copy"); } catch (err2) { ok = false; }
      ta.remove();
    }
    var old = button.textContent;
    button.textContent = ok ? "已复制" : "复制不了，请手动选中";
    setTimeout(function () { button.textContent = old; }, 1500);
  }
  function copyButton(getText, label) {
    var b = el("button", "btn btn-ghost tok-copy", label || "复制");
    b.type = "button";
    b.addEventListener("click", function () { copy(getText(), b); });
    return b;
  }

  function forget() {
    secret = "";
    secretEl.textContent = "";
    snippetsEl.textContent = "";
    fresh.hidden = true;
  }
  function reveal(t) {
    secret = t.token;
    secretEl.textContent = secret;
    snippetsEl.textContent = "";
    var site = location.origin + BASE;
    snippets(t.scope, secret, site).forEach(function (s) {
      var box = el("div", "tok-snippet");
      var head = el("div", "tok-snippet-head");
      head.appendChild(el("span", "set-label", s.title));
      head.appendChild(copyButton(function () { return s.text; }));
      box.appendChild(head);
      box.appendChild(el("pre", "tok-pre mono", s.text));
      snippetsEl.appendChild(box);
    });
    $("tok-new-scope").textContent = "「" + describe(t) + "」令牌" + (t.name ? "（" + t.name + "）" : "");
    fresh.hidden = false;
    fresh.scrollIntoView({ block: "nearest" });
  }

  function render(tokens) {
    list.textContent = "";
    $("tok-empty").hidden = tokens.length > 0;
    tokens.slice().reverse().forEach(function (t) {
      var li = el("li", "tok-item" + (t.revoked ? " is-revoked" : ""));
      li.dataset.id = t.id;
      var main = el("div", "tok-item-main");
      main.appendChild(el("span", "tok-scope tok-scope-" + t.scope, describe(t)));
      main.appendChild(el("span", "tok-item-name", t.name || "（没写备注）"));
      li.appendChild(main);
      li.appendChild(el("span", "tok-item-meta mono",
        t.id + " · " + day(t.createdAt) + " 发 · " + day(t.expiresAt) + " 到期"));
      if (t.revoked) {
        li.appendChild(el("span", "tok-revoked", "已吊销"));
      } else {
        var b = el("button", "btn btn-ghost danger tok-revoke", "吊销");
        b.type = "button";
        b.setAttribute("aria-label", "吊销令牌 " + (t.name || t.id));
        b.addEventListener("click", function () { revoke(t, b); });
        li.appendChild(b);
      }
      list.appendChild(li);
    });
  }

  async function load() {
    var r = await request("GET", "tokens");
    if (r.status === 503) {   // 认证服务支持、但这台没配好：说清楚，不给表单
      panel.hidden = false; form.hidden = true; list.hidden = true; $("tok-empty").hidden = true;
      note.hidden = false;
      note.textContent = "这台服务器还发不了令牌：在 .env 里设 AUTH_SECRET（一串随机字符）后重启 auth 服务。";
      return false;
    }
    if (r.status !== 200 || !r.body || !Array.isArray(r.body.tokens)) return false;   // 老的 / 换掉的认证服务：整块不出现
    panel.hidden = false;
    render(r.body.tokens);
    return true;
  }

  async function revoke(t, button) {
    if (!window.confirm("吊销「" + (t.name || t.id) + "」这个" + describe(t) + "令牌？\n用着它的代理会立刻被拒绝，吊销不能撤回。")) return;
    button.disabled = true;
    var r = await request("POST", "tokens/revoke", { tokenId: t.id });
    if (r.status !== 204) { button.disabled = false; say(why(r), true); return; }
    say("已吊销。", false);
    await load();
  }

  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var scope = (form.querySelector('input[name="tok-scope"]:checked') || {}).value || "report";
    var name = $("tok-name").value.trim();
    var button = $("tok-mint");
    button.disabled = true;
    forget();
    say("", false);
    var r = await request("POST", "tokens", { scope: scope, name: name });
    button.disabled = false;
    if (r.status !== 201 || !r.body || typeof r.body.token !== "string") { say(why(r), true); return; }
    $("tok-name").value = "";
    reveal(r.body);
    r = null;
    await load();
  });
  $("tok-secret-copy").addEventListener("click", function () { copy(secret, $("tok-secret-copy")); });
  $("tok-done").addEventListener("click", forget);
  window.addEventListener("pagehide", forget);   // 离开这页就忘掉（含进后退缓存之前）

  load().then(function (ok) {
    if (!ok) return;
    // 单人模式下不带令牌也能上报：说一声，免得人以为钩子必须配令牌
    request("GET", "health").then(function (r) {
      $("tok-anon").hidden = !(r.status === 200 && r.body && r.body.anonymousReport === true);
    });
  });
})();
