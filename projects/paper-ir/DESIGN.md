# PaperIR 数据契约

输入为本地 PDF、Markdown 或 TXT。输出包含 schema_version、paper、source、blocks 与 items。

source 保存相对路径、源 SHA-256 与抽取器标识。blocks 保存文本、位置与哈希。items 保存信息类型、supported/inferred/unresolved 状态与 block_id/quote 证据。

convert 创建文本块；add 在验证旧文件和新条目后原子保存；validate 重新抽取源文件并检查哈希、块和引用的一致性。它不是通用 JSON Schema 引擎，完整字段约束参见 schema。

图表/公式语义、OCR、自动科研判断、代码生成与实验执行不属于本工具当前功能。
