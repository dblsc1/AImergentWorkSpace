# 排障

错误是 `isError: true`，`structuredContent.error {status, detail, example?, hint}`，同时带 `permissions`。参数错（400）带 `example`，照着改。

## 常见错误

| 现象 | 含义 | 做法 |
|---|---|---|
| 403，「这个令牌是只读范围（read），不能调写工具」 | 令牌是 read | 告诉人换 write 令牌；你先只做读 |
| 403，「没有直接施加：当前调用方没有 write 范围」（needWriteScope） | 带了 `direct_apply: true` 但令牌不是 write | 去掉 `direct_apply` 改提议，或请人换令牌 |
| 403，「总开关」/「分开关「窗口分类」」/「分开关「蜂巢记录」」没打开 | 账号开关没开 | 整次失败，没有退回成提议。去掉 `direct_apply` 重发，并告诉人去「自动记录」页设置里打开 |
| 400，「找不到对应的分区 / 项目 / 任务。相近的：…」 | address 对不上唯一一个 | 从候选里选，或 `Get_View` 取真实 address / id。例：错写 `kf-hpkf-mcpx`，候选里会列 `kf-hpkf-mcpj`。以后存 id，因为改名会变 address |
| 400，「是项目的「未分类」系统桶，只能当记时间的目标」 | 未分类桶被当成 `target` 以外的用 | 只用作 `target` |
| 400，带 `example` | 参数不合法 | 对照 `example`，或 `help: true` |
| 400，`create` 带了 `direct_apply` | `create` 不收 | 去掉 |
| 409，通用窗口 | 对 generic 块写第二级规则 | 改用 `CRUD_Windowsblock` |
| 502，「数据服务暂时不可用」 | 下游暂时连不上 | 稍后重试一次，别连发 |

## 时区

时刻都带偏移，偏移见 `Get_Started` 的 `tz`（如 `+08:00`）。日期按服务端时区归日。写 `from` / `to` 用同一个偏移，别用 UTC 手算。

## 检测程序掉线还是没活动

- `Get_Today.gaps[].detectorOffline: true`：与掉线区间有交集，检测程序没在报。`Get_Device_Health`（可带 `deviceId`）看 `awState`、`incidents`、`hints`。只读，你不能重启；把现象和 `hints` 转告人。不要拿这种空档问人。
- `detectorOffline: false`：没有活动，多半人不在电脑前。
- 记录整体丢了：`Get_Device_Health`，再看 `Get_Started.tiersOn`。

## 没存的计时造成的空档

`gaps` 依据原始记录和已入账的段，正在跑的计时暂不计入。人在计时但还没停时，当前时段可能显示为空档。先 `Get_Now {"who": "human"}` 看 `human.timer`；有计时在跑就不问人，也不补记。

## 给人的话

说清：哪一步、什么错、你做了什么（已提议 / 没做）、需要人做什么（开开关、换令牌、看设备）。几句话，不贴整段返回。
