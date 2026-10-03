# 架构与事实源

sr 采用仓库内 CLI 和开放文件格式。`scripts/sr.py` 从自身所在目录推导工作区根路径，读取当前副本的数据；没有后台数据库或内置模型服务。

| 路径 | 责任 |
|---|---|
| `AGENTS.md` | AI 行为规范与会话协议 |
| `PROFILE.md` | 使用者自行填写的已确认背景 |
| `library/references.bib` | 工具维护的中央书目 |
| `library/papers/` | 按 bibkey 归档的本地 PDF |
| `library/notes/` | 一文献一卡的证据与阅读状态 |
| `library/index.md` | 状态与资产入口的索引 |
| `ideas/` | 想法与独立评估 |
| `projects/*/state.md` | 当前阶段、未确认项与下一步 |
| `projects/*/journal.md` | 追加式工作记录 |
| `projects/*/cards/` | 低成本捕获层 |
| `outputs/` | 会话草稿与交付成果 |
| `sync/assets.json` | 本机完整性清单 |
| `sync/manifest.md` | 外部备份证据 |

## 代码

- `sr_library.py`：字面量 BibTeX 解析、索引、哈希、写入锁与回滚；不支持 BibTeX 宏拼接。
- `sr_zotero.py`：通过本机 API 读取文库，按 DOI 对应；登记使用 sr 的已有路径，不直接操作 Zotero SQLite。
- `sr_reading.py`：核验 PDF 后导出只读上下文，按完整 bibkey 查找会话草稿。
- `sr_agents.py`：五角色依赖与确定性执行，保存显式指定的命令及日志。
- `projects/paper-ir/paper_ir.py`：文本块、位置、哈希与精确引文校验。

## 研究判断

源文献、AI 辅助和本人理解须分开。精读卡是已有资料，不能冒充刚核对的原文。打开 PDF、通过测试或完成 AI 问答，都不自动升级阅读状态。红队报告只标问题；研究方向、解释、放行和发布由研究者判断。

文本可以通过私有 Git 仓库同步；PDF、大数据与渲染产物独立备份。哈希验证只证明文件与记录相符，不证明学术结论、抽取完整性或备份可恢复性。
