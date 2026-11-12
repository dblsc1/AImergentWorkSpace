// hive · hex-figure-pick.js —— 形象挑选（hive.stage.v1「十一」）：人自己的形象、每个 AI 的形象，都从同一张表里挑。
// 表 = /hive/pets/index.json 列出的宠物包（动画包 + 静态形象包）。以后支持上传：上传的形象也是一个包目录、index.json 多一行，这里不用改。
// 上半是纯函数（给 hex-figure-pick.test.js 用），下半是 DOM。
(function () {
  "use strict";

  var NEAR = 20, STEP = 12, LIGHT = 150;
  // 静态形象是带浅色底的整张图：从四边往里把连着的底抠成透明，再只留最大的那一块（零碎的装饰丢掉）。
  // mode 缺省：底 = 和左上角颜色差不多的（纯色底；衣服里的白被描边挡住，不会被抠）。
  // mode "gradient"：底 = 浅色、且和已抠掉的邻格只差一点的（渐变底、淡阴影；人物是白衣白发的图别用，会漏进去）。
  // px = RGBA 数组（原地改）；返回留下那一块的外框 {x, y, w, h}，什么都没留下 = null
  function clearBackdrop(px, w, h, mode) {
    var r0 = px[0], g0 = px[1], b0 = px[2], grad = mode === "gradient", n = w * h, seen = new Uint8Array(n), stack = [], i, x, y;
    function d(o, r, g, b) { return Math.max(Math.abs(px[o] - r), Math.abs(px[o + 1] - g), Math.abs(px[o + 2] - b)); }
    function bg(p, from) {
      var o = p * 4;
      if (!grad) return d(o, r0, g0, b0) <= NEAR;
      if (Math.min(px[o], px[o + 1], px[o + 2]) < LIGHT) return false;
      return from < 0 || d(o, px[from * 4], px[from * 4 + 1], px[from * 4 + 2]) <= STEP;
    }
    function push(p, from) { if (!seen[p] && bg(p, from)) { seen[p] = 1; stack.push(p); } }
    for (x = 0; x < w; x++) { push(x, -1); push((h - 1) * w + x, -1); }
    for (y = 0; y < h; y++) { push(y * w, -1); push(y * w + w - 1, -1); }
    while (stack.length) {
      i = stack.pop(); x = i % w; y = (i - x) / w;
      if (x > 0) push(i - 1, i);
      if (x < w - 1) push(i + 1, i);
      if (y > 0) push(i - w, i);
      if (y < h - 1) push(i + w, i);
    }
    // 剩下的按连通块标号，留最大的一块
    var comp = new Int32Array(n), sizes = [0], k = 0, best = 0, s, c;
    for (s = 0; s < n; s++) {
      if (seen[s] || comp[s]) continue;
      comp[s] = ++k; sizes[k] = 0; stack.push(s);
      while (stack.length) {
        i = stack.pop(); sizes[k]++; x = i % w; y = (i - x) / w;
        if (x > 0 && !seen[c = i - 1] && !comp[c]) { comp[c] = k; stack.push(c); }
        if (x < w - 1 && !seen[c = i + 1] && !comp[c]) { comp[c] = k; stack.push(c); }
        if (y > 0 && !seen[c = i - w] && !comp[c]) { comp[c] = k; stack.push(c); }
        if (y < h - 1 && !seen[c = i + w] && !comp[c]) { comp[c] = k; stack.push(c); }
      }
      if (sizes[k] > sizes[best]) best = k;
    }
    if (!best) { for (i = 0; i < n; i++) px[i * 4 + 3] = 0; return null; }
    var x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (i = 0; i < n; i++) {
      if (comp[i] !== best) { px[i * 4 + 3] = 0; continue; }
      x = i % w; y = (i - x) / w;
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    return { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
  }

  // 一个包在表里的那一格用什么画：静态图 / 动画第一帧 / 占位
  function tileOf(pack) {
    if (pack.image) return { type: "image", file: pack.image };
    if (pack.cover) return { type: "video", file: pack.cover };
    return { type: "none", file: null };
  }

  var H = { clearBackdrop: clearBackdrop, tileOf: tileOf };
  if (typeof module !== "undefined" && module.exports) { module.exports = H; return; }
  window.HexFigurePick = H;

  // ── DOM ──
  var CUT_H = 360, cuts = {};
  // 抠好底、裁掉留白的静态形象 → blob 地址（每个地址只做一次）
  H.cutout = function (url, mode) {
    if (cuts[url]) return cuts[url];
    return (cuts[url] = fetch(url).then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.blob(); })
      .then(function (b) { return createImageBitmap(b, { resizeHeight: CUT_H, resizeQuality: "high" }); })
      .then(function (bmp) {
        var c = document.createElement("canvas"); c.width = bmp.width; c.height = bmp.height;
        var g = c.getContext("2d"); g.drawImage(bmp, 0, 0);
        var img = g.getImageData(0, 0, c.width, c.height), box = clearBackdrop(img.data, c.width, c.height, mode);
        if (!box) throw new Error("空图");
        var out = document.createElement("canvas"); out.width = box.w; out.height = box.h;
        g.putImageData(img, 0, 0); out.getContext("2d").drawImage(c, box.x, box.y, box.w, box.h, 0, 0, box.w, box.h);
        return new Promise(function (ok) { out.toBlob(function (blob) { ok(URL.createObjectURL(blob)); }, "image/png"); });
      }).catch(function () { delete cuts[url]; return null; }));
  };

  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  var panel = null;
  function close() {
    if (!panel) return;
    panel.remove(); panel = null;
    document.removeEventListener("pointerdown", onOutside, true); document.removeEventListener("keydown", onKey, true);
  }
  function onOutside(ev) { if (panel && !panel.contains(ev.target)) close(); }
  function onKey(ev) { if (ev.key === "Escape") { ev.stopPropagation(); close(); } }

  // o: {title, base（/hive/pets/ 的地址）, current（现在用的包）, near（贴着哪个元素）, placeholder（占位形象的 html）, onPick(kind)}
  H.open = function (o) {
    close();
    panel = el("div", "fp-panel st-card"); panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", o.title);
    panel.appendChild(el("h3", "", o.title));
    var grid = el("div", "fp-grid"); panel.appendChild(grid);
    document.body.appendChild(panel); place();
    document.addEventListener("pointerdown", onOutside, true); document.addEventListener("keydown", onKey, true);
    var mine = panel;
    function place() {
      var r = o.near.getBoundingClientRect(), w = panel.offsetWidth, hgt = panel.offsetHeight;
      panel.style.left = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 8)) + "px";
      panel.style.top = (r.bottom + 10 + hgt <= window.innerHeight - 8 ? r.bottom + 10 : Math.max(8, r.top - 10 - hgt)) + "px";
    }
    fetch(o.base + "index.json").then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }).then(function (ix) {
      if (panel !== mine) return;
      var list = (ix && Array.isArray(ix.packs) ? ix.packs : []).filter(function (p) { return p && /^[\w-]+$/.test(p.kind || ""); });
      if (!list.length) { grid.appendChild(el("div", "fp-empty", "形象素材还没下好")); place(); return; }
      list.forEach(function (p) {
        var b = el("button", "fp-tile"), art = el("span", "fp-art"), t = tileOf(p);
        var url = t.file && o.base + encodeURIComponent(p.kind) + "/" + String(t.file).split("/").map(encodeURIComponent).join("/");
        b.type = "button"; b.dataset.kind = p.kind; b.setAttribute("aria-pressed", p.kind === o.current ? "true" : "false");
        if (t.type === "video") {
          var v = el("video"); v.muted = true; v.playsInline = true; v.preload = "metadata"; v.src = url + "#t=0.1";
          v.addEventListener("error", function () { art.innerHTML = o.placeholder; });
          art.appendChild(v);
        } else {
          art.innerHTML = o.placeholder;
          if (t.type === "image") H.cutout(url, p.backdrop).then(function (u) { if (u) { var im = el("img"); im.alt = ""; im.src = u; art.textContent = ""; art.appendChild(im); } });
        }
        b.appendChild(art); b.appendChild(el("span", "fp-name", p.name || p.kind));
        b.addEventListener("click", function () { close(); o.onPick(p.kind); });
        grid.appendChild(b);
      });
      place();
      var cur = grid.querySelector('[aria-pressed="true"]') || grid.querySelector("button"); if (cur) cur.focus();
    });
  };
  H.close = close;
})();
