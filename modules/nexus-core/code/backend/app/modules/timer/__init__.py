# timer —— 计时活状态；stop 时组装 session.completed 投进事件入口。
# 铁则：**不许绕过 events 入口的校验与防重直写 events 集合**。
