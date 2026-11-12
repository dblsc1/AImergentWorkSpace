const test = require("node:test");
const assert = require("node:assert");
const { clearBackdrop, tileOf } = require("./hex-figure-pick.js");

// 5×5：浅底，中间一圈深色描边围着一个白点（白点连不到边，不能被抠）
function picture() {
  const w = 5, h = 5, px = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) px.set([250, 248, 252, 255], i * 4);
  for (const [x, y] of [[1, 1], [2, 1], [3, 1], [1, 2], [3, 2], [1, 3], [2, 3], [3, 3]]) px.set([20, 20, 40, 255], (y * w + x) * 4);
  px.set([255, 255, 255, 255], (2 * w + 2) * 4);
  return { px, w, h };
}

test("clearBackdrop 只抠连着四边的底色，描边里面的白不动，返回外框", () => {
  const { px, w, h } = picture();
  assert.deepEqual(clearBackdrop(px, w, h), { x: 1, y: 1, w: 3, h: 3 });
  assert.equal(px[3], 0);                       // 左上角：透明
  assert.equal(px[(2 * w + 2) * 4 + 3], 255);   // 中间白点：还在
  assert.equal(px[(1 * w + 1) * 4 + 3], 255);   // 描边：还在
});

test("clearBackdrop 整张都是底色 = null", () => {
  const px = new Uint8ClampedArray(4 * 4).fill(255);
  assert.equal(clearBackdrop(px, 2, 2), null);
});

test("tileOf：静态图 / 动画第一帧 / 占位", () => {
  assert.deepEqual(tileOf({ kind: "qwen", image: "q.png" }), { type: "image", file: "q.png" });
  assert.deepEqual(tileOf({ kind: "gpt", cover: "a.webm" }), { type: "video", file: "a.webm" });
  assert.deepEqual(tileOf({ kind: "x", cover: null }), { type: "none", file: null });
});
