// 设计稿交互：主题切换 + 每个样机的宽度档位。真实产品的主题逻辑见落地清单。
(function () {
  'use strict';

  // 主题：设计稿里只切 data-theme；真实产品 = 跟随系统 + 手动覆盖存
  // localStorage('cockpit-theme')，并监听 storage 事件同步所有已开窗口。
  var root = document.documentElement;
  document.querySelectorAll('.themer button').forEach(function (b) {
    b.addEventListener('click', function () {
      root.dataset.theme = b.dataset.theme;
      document.querySelectorAll('.themer button').forEach(function (x) {
        x.setAttribute('aria-pressed', String(x === b));
      });
    });
  });

  // 宽度档位：390（手机/三分窗）· 768（平板/半窗）· 全宽（桌面）
  document.querySelectorAll('.frame').forEach(function (frame) {
    var body = frame.querySelector('.frame-body');
    frame.querySelectorAll('.frame-bar .w button').forEach(function (b) {
      b.addEventListener('click', function () {
        body.style.maxWidth = b.dataset.w ? b.dataset.w + 'px' : '';
        frame.querySelectorAll('.frame-bar .w button').forEach(function (x) {
          x.setAttribute('aria-pressed', String(x === b));
        });
      });
    });
  });
})();
