// 嵌在浮窗里的 /hive/mini 的预加载脚本（沙箱，只做消息转发）：
// 页面 window.postMessage({type:'hive-mini-done'}) -> 通知外壳「完成」；外壳的卡状态 -> 页面 postMessage。
const { ipcRenderer } = require('electron');

window.addEventListener('message', e => {
  if (e.source === window && e.data && e.data.type === 'hive-mini-done') ipcRenderer.sendToHost('hive-mini-done');
});
ipcRenderer.on('card', (_e, state) => window.postMessage({ type: 'hive-mini-card', state }, '*'));
