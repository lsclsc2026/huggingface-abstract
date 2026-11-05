# 公共内容提示词 v5

- 单篇分析：`beginner-shared-v5`；规则分析：`heuristic-shared-v5`。
- 今日导读：`digest-shared-v5`；规则导读：`digest-heuristic-shared-v5`。
- 保留 v4 的术语解释、类比、机制、对照、误解和证据边界要求。
- 不传入用户兴趣、身份、备注或语言偏好；统一简体中文科普风格。
- 不要求模型生成 relevance_score 或 highlight，归一化时忽略这些字段。
- 导读输入不含个人 relevance；选文、输入顺序和默认阅读路径按公共热度及 ID 排序。
- 个人评分由 `app/relevance.py` 计算，单独存入 `papers.relevance`；个人设置更新不改写 analysis 或 digest。
- 保留旧历史内容，不自动重建，不因为用户改兴趣而重复调用模型。旧模型自评分字段在读取时隐藏。
- 当前仍为单用户数据库；后续认证实施时，把个人相关度、兴趣和待读迁到按 user_id 隔离的数据模型，公共内容继续复用。
