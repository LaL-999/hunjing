"""数据库表的 Python 表示(纯 dataclass,不带行为)。

设计原则(ADR §2.1):
- 不引入 ORM,不带方法
- from_row 类方法负责 sqlite3.Row → dataclass 的转换
- JSON 字段(如 consent.checks)在 from_row 时反序列化
"""
