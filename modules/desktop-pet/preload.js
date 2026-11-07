const { contextBridge, ipcRenderer } = require('electron');

const on = ch => cb => ipcRenderer.on(ch, (_e, v) => cb(v));
contextBridge.exposeInMainWorld('pet', {
  onCursor: on('cursor'), onBadge: on('badge'), onPanel: on('panel'), onCard: on('card'),
  onPlay: on('play'), onBubble: on('bubble'), onSize: on('size'),
  setHit: hit => ipcRenderer.send('hit', !!hit),
  hover: onOff => ipcRenderer.send('hover', !!onOff),
  pageDone: () => ipcRenderer.send('page-done'),
  clipEnded: () => ipcRenderer.send('clip-ended'),
});
