// hex-source-health.js 的纯逻辑。node hex-source-health.test.js
"use strict";
const assert = require("assert");
const S = require("./hex-source-health.js");

const dev = (issues, at) => ({ deviceId: "d", sourceHealth: issues === null ? null : { issues, at } });

// 没有任何设备报过 / 全好：什么都不显示
assert.deepStrictEqual(S.problems([]), []);
assert.deepStrictEqual(S.problems(null), []);
assert.deepStrictEqual(S.problems([dev(null)]), []);
assert.deepStrictEqual(S.problems([dev([], "2026-11-08T10:00:00+00:00")]), []);

// 有问题：条数、每条一行修法；取最近一次体检的设备
const p = S.problems([dev(["aw_down"], "2026-11-08T09:00:00+00:00"), dev(["web_extension_missing", "window_wayland"], "2026-11-08T10:00:00+00:00")]);
assert.deepStrictEqual(p.map((x) => x.code), ["web_extension_missing", "window_wayland"]);
assert.ok(p.every((x) => x.text && x.fix && Array.isArray(x.links)));
assert.ok(p[0].links.some((l) => l.url === "https://addons.mozilla.org/en-US/firefox/addon/aw-watcher-web/"));
assert.ok(p[1].fix.includes("awatcher"));

// 新服务端的未知编码跳过，不炸
assert.deepStrictEqual(S.problems([dev(["from_the_future"], "2026-11-08T10:00:00+00:00")]), []);
console.log("hex-source-health ok");
