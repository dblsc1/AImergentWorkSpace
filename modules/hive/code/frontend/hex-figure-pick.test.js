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

test("clearBackdrop 只留最大的一块：零碎的装饰丢掉", () => {
  const w = 7, h = 5, px = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) px.set([250, 248, 252, 255], i * 4);
  for (const [x, y] of [[1, 1], [2, 1], [1, 2], [2, 2], [1, 3], [2, 3]]) px.set([20, 20, 40, 255], (y * w + x) * 4);   // 人物：6 格
  px.set([200, 40, 40, 255], (2 * w + 5) * 4);                                                                         // 装饰：1 格，不挨着
  assert.deepEqual(clearBackdrop(px, w, h), { x: 1, y: 1, w: 2, h: 3 });
  assert.equal(px[(2 * w + 5) * 4 + 3], 0);
});

test("clearBackdrop gradient：顺着渐变抠，深色描边挡得住", () => {
  const w = 9, h = 3, px = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) px.set([250 - x * 8, 240 - x * 8, 245 - x * 8, 255], (y * w + x) * 4);   // 左右渐变，和左上角差到 64
  px.set([30, 30, 30, 255], (1 * w + 4) * 4);
  assert.ok(clearBackdrop(px.slice(), w, h).w > 1);
  assert.deepEqual(clearBackdrop(px, w, h, "gradient"), { x: 4, y: 1, w: 1, h: 1 });
});
