# activity.classifier.v1 —— 活动段分类服务接口

> **状态**：v1 规范性。本文件是「把一段电脑活动归到哪个任务」这件事的**唯一事实**。
> 只增不改不删；不兼容的改动发 `activity.classifier.v2`，新旧并行。
>
> 调用方：`modules/ai-detector`（桌面同步程序）。
> 默认实现：ai-detector 内置的**规则分类**（本机规则文件，见该模块 README）。
> 外部实现：**本仓不提供**。任何人按本文件实现一个 HTTP 服务、把地址配进 ai-detector，
> 就能替换/补充规则分类——不绑定任何模型或厂商。ai-detector 的测试里有一个假实现。

```yaml
provides:
  - id: activity.classifier.v1
    summary: >
      活动段 → 任务建议的分类接口。一次 POST，批量段 + 候选任务进，逐段建议出。
      只产出「建议」，从不写事实。
```

## 一、请求

`POST <分类服务地址>`，`Content-Type: application/json`。

- **地址必须是 `https://`**，唯一例外是本机回环（`localhost` / `127.0.0.0/8` / `::1`）可用 `http://`。
  调用方发现地址不合规，整轮不上传并报错，而不是明文发出去。
- **凭据**：配了 `classifierToken` 才带 `Authorization: Bearer <classifierToken>`，否则不带。
  **HoneyComb 的设备令牌绝不发给分类服务**——分类服务是用户填的任意地址，
  把设备令牌给它等于把账号的接口权限交给第三方。
- **不跟随重定向**：服务回 3xx 按失败处理（防止请求连同凭据被转到别处）。

```jsonc
{
  "segments": [
    { "id": "seg_0",                          // 本次请求内唯一，只用于对上号，不持久
      "app": "code",                          // 程序名，ActivityWatch 报什么就是什么
      "title": "plot.gd — garden — Visual Studio Code",  // **已在本机脱敏**
      "startAt": "2026-09-26T11:05:00+08:00", // ISO 8601，带偏移
      "durationSeconds": 3600 }               // 整数，已扣离开时间
  ],
  "tasks": [
    { "id": "t_a1", "path": "学习 / garden / 写提示词" }   // 分区 / 项目 / 任务
  ]
}
```

- 服务**只会**看到脱敏后的段：没有原始窗口切换记录、没有完整网址（浏览器只有域名 +
  页面标题）、聊天 / 邮件 / 密码管理器类程序的 `title` 是空串。
- 只有规则没认出来的段才发给服务。

## 二、响应

`2xx`，`Content-Type: application/json`：

```jsonc
{
  "suggestions": [
    { "segmentId": "seg_0",
      "taskId": "t_a1",        // 或 null：认不出
      "confidence": 0.82,      // 0..1
      "reason": "标题含 garden" }
  ]
}
```

- 每个 `segmentId` 至多一条；没给的段按「认不出」处理。
- 调用方**不信任**服务：`taskId` 不在请求的 `tasks` 里 → 当作 `null`（把握记 0）；
  `confidence` 夹到 `[0, 1]`；`reason` 去掉控制字符、截到 200 字节以内；多余字段忽略。

## 三、失败语义（规范性）

- 超时、非 2xx、重定向、响应不是上面的形状 → 这一批段全部按「无建议」处理
  （`taskId: null`），**照常上传，绝不阻塞同步**。
- 这时上传的 `reason` 是**固定文案**（「分类服务不可用」「分类服务响应格式不对」
  「分类服务未调用：取不到任务树」），错误细节只写本机日志——错误信息里常带完整地址和查询串。
- 服务从不决定事实：建议要人在界面上确认后才会写成 `session.completed`。
