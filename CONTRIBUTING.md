# 贡献

请先提交一个可复现的问题或范围明确的改动。核心代码保持标准库优先，不引入与任务无关的服务。

提交前运行 `python3 scripts/check.py`、`python3 scripts/sr.py doctor --verify --strict`、`python3 scripts/sr.py index --check` 与 `git diff --check`。测试使用临时目录和虚构材料，不访问真实文库或个人记录。

保持失败时源文件保留、精确 bibkey 匹配、阅读状态由人决定和证据可回溯的行为。新的数据格式应说明兼容性与迁移方式。不要用语法或哈希校验代替语义审查。

Issue/PR 中只附脱敏或虚构的最小复现材料。
