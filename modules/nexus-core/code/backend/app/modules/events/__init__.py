# events —— 事实唯一写入口（校验 → 盖 recordedAt → 防重 → 落库 → 触发 projector）。
# 将来的模块边界：外部只准 import 本包的 service.py 公开函数。
