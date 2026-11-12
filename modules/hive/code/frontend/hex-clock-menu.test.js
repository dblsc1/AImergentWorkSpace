// hex-clock-menu.js 纯逻辑测试
const assert = require("assert");
const C = require("./hex-clock-menu.js");

// 按压分类
assert.strictEqual(C.pressKind(100, 2, 1), "tap");
assert.strictEqual(C.pressKind(C.HOLD_MS, 0, 0), "hold");
assert.strictEqual(C.pressKind(C.HOLD_MS + 300, 4, 4), "hold");
assert.strictEqual(C.pressKind(900, 30, 0), "move");   // 按得久但拖远了 = 拖
assert.strictEqual(C.pressKind(50, 0, 40), "move");

// 倒计时剩余 / 格式 / 自定义分钟 / 结束文案
assert.strictEqual(C.remainingSeconds(10000, 0), 10);
assert.strictEqual(C.remainingSeconds(10000, 9001), 1);
assert.strictEqual(C.remainingSeconds(10000, 20000), 0);
assert.strictEqual(C.formatRemaining(25 * 60), "25:00");
assert.strictEqual(C.formatRemaining(65), "01:05");
assert.strictEqual(C.formatRemaining(3600 + 90), "1:01:30");
assert.strictEqual(C.formatRemaining(-5), "00:00");
assert.deepStrictEqual(["25", " 7 ", "1440", "0", "1441", "", "x", "1.5", "-3"].map(C.parseMinutes), [25, 7, 1440, null, null, null, null, null, null]);
assert.strictEqual(C.endedText(0, 25 * 60000), "倒计时结束 · 25 分钟已记下");
assert.deepStrictEqual(C.PRESETS, [25, 45, 60]);

console.log("hex-clock-menu ok");
