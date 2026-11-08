const { contextBridge, ipcRenderer } = require('electron');

const on = ch => cb => ipcRenderer.on(ch, (_e, v) => cb(v));
contextBridge.exposeInMainWorld('pet', {
  onCursor: on('cursor'), onBadge: on('badge'), onPanel: on('panel'), onCard: on('card'),
  onPlay: on('play'), onBubble: on('bubble'), onDialog: on('dialog'), onSize: on('size'),
  setHit: hit => ipcRenderer.send('hit', !!hit),
  hover: onOff => ipcRenderer.send('hover', !!onOff),
  answerDialog: (seq, value) => ipcRenderer.send('dialog-answer', { seq, value }),
  petClick: () => ipcRenderer.send('pet-click'),
  pageDone: () => ipcRenderer.send('page-done'),
  clipEnded: () => ipcRenderer.send('clip-ended'),
});
