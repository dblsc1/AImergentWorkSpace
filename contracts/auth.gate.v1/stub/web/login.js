"use strict";

/**
 * auth.gate.v1 登录页的提交逻辑。原生 fetch，无框架，配套 index.html。
 *
 * 状态码分支严格照抄 ../contract.md 「POST /api/auth/login」一节和
 * ../auth_stub.py:180-199（_login）的真实行为，不臆造契约里没出现过的分支：
 *   204  口令正确 —— 无 body，Set-Cookie 已由浏览器处理，前端只管跳转
 *   401  口令不正确（auth_stub.py:196-198）
 *   400  Content-Length 不是合法整数（auth_stub.py:182-185），
 *        或请求体不是合法 JSON 对象（auth_stub.py:190-194）——两种情况
 *        都落在同一个 400，前端发的本来就是合法 JSON，走到这里通常是
 *        传输环节出了问题，不必也无法区分子原因
 *   413  请求体超过 4096 字节 / Content-Length 为负（auth_stub.py:47, 186-187）
 *   其它非 2xx，或 fetch 本身抛异常（断网等）—— 契约里没有这些分支，
 *        一律给兜底文案，不猜测原因、不把原始 Error 对象展示给用户
 *
 * 关于 cookie：本文件从头到尾不读、不写、不检查 cookie。会话 cookie 由服务端
 * 经 Set-Cookie 下发，并且带 HttpOnly 属性（contract.md「换实现要满足什么」、
 * auth_stub.py:139-149 的 _set_cookie）。HttpOnly 意味着浏览器从一开始就不把
 * 这个 cookie 交给任何页面脚本（document.cookie 读不到它）——这是特意的安全
 * 设计：即便页面被注入一段恶意脚本（XSS），那段脚本也偷不到会话凭证。所以
 * "前端不碰 cookie" 不是本文件漏写了什么，而是这条本来就不该、也没有能力去做。
 *
 * 关于口令：口令只出现在下面这一次 fetch 调用的请求体里。不写 localStorage、
 * 不写 sessionStorage、不拼进 URL、不 console.log —— 这几个地方要么会被
 * 持久化到磁盘，要么可能被浏览器历史/同源脚本/日志采集器读到，把明文口令
 * 留在任何一处都是白白多开一个泄漏口子。
 */
(function () {
  var form = document.getElementById("login-form");
  var passwordInput = document.getElementById("password");
  var submitButton = document.getElementById("submit");
  var errorBox = document.getElementById("error");

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.hidden = true;
  }

  /**
   * 开放重定向防护。
   *
   * 为什么必须校验：如果直接把 URL 里的 ?next= 原样拿去跳转，任何人都能
   * 构造出 /login/?next=https://坏站点 这样的链接发给受害者。受害者点开的
   * 域名、TLS 证书、地址栏显示的都是这个站点本身——看起来百分之百可信，
   * 只有登录成功那一刻会被悄悄带去 next 指向的任意外部地址。这比一条
   * 来源不明的钓鱼链接更有欺骗性，因为它是从"自己人"（本站域名）发出的。
   *
   * 防护策略：只接受"明显是本站内部路径"的值——必须以单个 "/" 开头，
   * 且不能以 "//" 或 "/\" 开头。后两种前缀是浏览器 URL 解析的怪癖：
   * "//evil.com/x" 和 "/\evil.com/x" 都会被当成协议相对 URL，实际跳到
   * evil.com，是绕过"看起来像相对路径"这条检查的经典手法。任何不满足
   * 条件的输入，一律退回站内根路径 "/"，不尝试"修复"或"猜测"用户的意图。
   */
  function safeNext(raw) {
    if (typeof raw !== "string" || raw === "") return "/";
    if (raw.charAt(0) !== "/") return "/";
    if (raw.indexOf("//") === 0 || raw.indexOf("/\\") === 0) return "/";
    return raw;
  }

  function targetAfterLogin() {
    var params = new URLSearchParams(window.location.search);
    return safeNext(params.get("next"));
  }

  function submitLogin(password) {
    return fetch("/api/auth/login", {
      method: "POST",
      // same-origin：把本站已有的 cookie 带上、并允许服务端这次的
      // Set-Cookie 被浏览器写入——不是前端去处理 cookie，是让浏览器
      // 按标准 HTTP 语义自己处理。
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password }),
    }).then(
      function (response) {
        if (response.status === 204) {
          window.location.href = targetAfterLogin();
          return;
        }
        if (response.status === 401) {
          showError("口令不对，请重新输入。");
          return;
        }
        if (response.status === 400) {
          showError("请求格式有误，请刷新页面后重试。");
          return;
        }
        if (response.status === 413) {
          showError("输入内容过长，请检查后重试。");
          return;
        }
        // 契约四个状态码之外的任何响应：不猜测原因，只给兜底文案。
        showError("登录失败，请稍后重试。");
      },
      function () {
        // fetch 本身失败（断网、服务未起、CORS 之类）：不把原始 Error
        // 对象展示给用户——那对普通用户没有意义，还可能带出内部实现细节。
        showError("网络异常，请检查连接后重试。");
      }
    );
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    clearError();

    var password = passwordInput.value;

    // 防连点：请求在途时禁用按钮，避免同一次提交被并发发出好几次请求
    // （对一个只认"对/不对"的共享口令门来说，连点没有任何好处，只会
    // 白白多打几次 401/204）。
    submitButton.disabled = true;
    submitLogin(password).finally(function () {
      submitButton.disabled = false;
    });
  });
})();
