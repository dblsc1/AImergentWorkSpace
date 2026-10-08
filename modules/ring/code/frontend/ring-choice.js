/**
 * ring-choice.js —— 「你在 X，记到哪？」（nexus-core v2.14 自动跟踪：规则认不出的窗口请人选）
 *
 * 「允许 AI 管理进行中的任务」开着、没有手动计时、某个窗口规则认不出又停留够久时，views/lanes 的
 * human.needsChoice 给出 {key, app, title, since}。本文件只管做那张卡：选项目 →（可选）选任务，缺省记到项目的
 * 「未分类」；勾着「以后这个窗口都这样记」就由服务端往分类规则最前面加一条。卡放在哪、什么时候出现由
 * ring-lanes.js 定（泳道最上面，经共享的 lanes.js 的 opts.lead，随换位动效浮上来）。
 *
 * - 确定 = POST api/core/activity/choice {key, taskId | projectId, remember}；这次不选 = POST …/choice/dismiss {key}。
 * - 服务端说标题是代号（pseudonymized）/ 规则没写成：留一句说明，人点「知道了」才收卡。窗口已不在记录里（404）同样。
 * - 人一直不答：什么都不发生——那段活动照常进「待确认」，在 AI助理页和别的碎片一起归类。
 * - app / title / 项目名 / 任务名都是数据：一律 textContent / Option 文本，不进 innerHTML。
 * - reject(key)（nexus-core v2.15）：人对「自动 · …（AI 认的）」说「不对」= POST …/choice/reject {key}；之后由
 *   ring-lanes.js 把这张卡摆出来让人自己选。
 * 对外只挂 window.RingChoice = { card, windowLabel, reject }。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var CORE = BASE + "api/core/";

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // 窗口的叫法：程序 · 标题（app-only 的程序没有标题）
  function windowLabel(need) {
    return [need.app, need.title].filter(function (s) { return s; }).join(" · ");
  }

  async function post(path, body) {
    var res, data = null;
    try {
      res = await fetch(CORE + path, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
      });
      try { data = await res.json(); } catch (err) { data = null; }
    } catch (err) { return { ok: false, status: 0, detail: "连不上服务" }; }
    var detail = data && data.detail;
    return { ok: res.ok, status: res.status, body: data,
      detail: typeof detail === "string" ? detail : "请求失败（" + res.status + "）" };
  }

  // card(need, onClose) → 一张卡（DOM 节点）。onClose()：卡该收了（人答了 / 说这次不选 / 看完了说明）。
  function card(need, onClose) {
    var box = el("section", "choice-card");
    box.setAttribute("aria-label", "这个窗口记到哪");
    box.dataset.key = need.key;

    var title = el("p", "choice-title");
    title.appendChild(document.createTextNode("你在 "));
    title.appendChild(el("b", "choice-window", windowLabel(need)));
    title.appendChild(document.createTextNode("，记到哪？"));
    box.appendChild(title);

    var form = el("div", "choice-form");
    var project = el("select", "field choice-project");
    project.setAttribute("aria-label", "项目");
    project.appendChild(new Option("选项目…", ""));
    var task = el("select", "field choice-task");
    task.setAttribute("aria-label", "任务（可选）");
    task.appendChild(new Option("未分类", ""));
    task.disabled = true;
    var keep = el("label", "choice-remember");
    var remember = el("input");
    remember.type = "checkbox";
    remember.checked = true;
    keep.appendChild(remember);
    keep.appendChild(document.createTextNode(" 以后这个窗口都这样记"));
    var ok = el("button", "btn btn-fact choice-ok", "确定");
    ok.type = "button";
    ok.disabled = true;
    var skip = el("button", "btn btn-ghost choice-skip", "这次不选");
    skip.type = "button";
    [project, task, keep, ok, skip].forEach(function (n) { form.appendChild(n); });
    box.appendChild(form);
    var message = el("p", "choice-message");
    message.setAttribute("role", "status");
    message.hidden = true;
    box.appendChild(message);

    var tree = null, busy = false;
    function sync() {
      project.disabled = remember.disabled = skip.disabled = busy;
      task.disabled = busy || !project.value;
      ok.disabled = busy || !project.value;
    }
    function say(text, isError) {
      message.textContent = text || "";
      message.hidden = !text;
      message.classList.toggle("is-error", Boolean(isError));
    }
    // 说完再收：表单换成一句话 + 「知道了」
    function finish(text) {
      form.textContent = "";
      var done = el("button", "btn btn-ghost choice-done", "知道了");
      done.type = "button";
      done.addEventListener("click", onClose);
      form.appendChild(done);
      say(text, false);
      done.focus();
    }

    project.addEventListener("change", function () {
      task.textContent = "";
      task.appendChild(new Option("未分类", ""));
      var p = ((tree && tree.projects) || []).filter(function (x) { return x.id === project.value; })[0];
      ((p && p.tasks) || []).forEach(function (t) {
        if (t.done !== true) task.appendChild(new Option(t.name, t.id));
      });
      sync();
    });

    ok.addEventListener("click", async function () {
      if (busy || !project.value) return;
      busy = true;
      sync();
      say("");
      var body = { key: need.key, remember: remember.checked };
      if (task.value) body.taskId = task.value; else body.projectId = project.value;
      var r = await post("activity/choice", body);
      busy = false;
      if (r.ok) {
        if (r.body && r.body.pseudonymized) {
          finish("这次按你选的算了。但这台电脑上传的是标题代号，规则认的是换代号之前的标题，没法记住这个窗口。");
        } else if (remember.checked && !(r.body && r.body.remembered)) {
          finish("这次按你选的算了。规则没写成（标题太长，或规则已经满了），以后还会再问。");
        } else {
          onClose();
        }
        return;
      }
      // 404 两种：窗口已不在在场记录里（收卡）；选的任务 / 项目刚被删了（留着让人重选）
      if (r.status === 404 && /窗口/.test(r.detail)) { finish("这个窗口已经不在最近的记录里了，不用选了。"); return; }
      say("没记上：" + r.detail, true);
      sync();
    });

    skip.addEventListener("click", async function () {
      if (busy) return;
      busy = true;
      sync();
      var r = await post("activity/choice/dismiss", { key: need.key });
      busy = false;
      if (r.ok || r.status === 404) { onClose(); return; }
      say("没记上：" + r.detail, true);
      sync();
    });

    fetch(CORE + "views/tree").then(function (res) { return res.ok ? res.json() : null; }).then(function (t) {
      tree = t;
      var zones = {};
      ((t && t.zones) || []).forEach(function (z) { zones[z.id] = z.name; });
      ((t && t.projects) || []).forEach(function (p) {
        if (p.status === "done") return;
        project.appendChild(new Option([zones[p.zoneId], p.name].filter(Boolean).join(" / "), p.id));
      });
      if (!t) say("项目列表读不到，稍后再试。", true);
    }).catch(function () { say("项目列表读不到，稍后再试。", true); });

    return box;
  }

  function reject(key) { return post("activity/choice/reject", { key: key }); }

  window.RingChoice = { card: card, windowLabel: windowLabel, reject: reject };
})();
