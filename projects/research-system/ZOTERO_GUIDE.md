# 可选 Zotero 集成

Zotero 本地个人文库需运行并允许本机 API 访问；先用 `sr zotero status` 检查连接。当前使用本机 `127.0.0.1:23119`，不需要云端 API 密钥。

```bash
python3 scripts/sr.py zotero status
python3 scripts/sr.py zotero list 关键词
python3 scripts/sr.py zotero import ITEMKEY --as YOUR_BIBKEY
# 核对预览后才执行：
python3 scripts/sr.py zotero import ITEMKEY --as YOUR_BIBKEY --apply
python3 scripts/sr.py zotero sync
python3 scripts/sr.py zotero open YOUR_BIBKEY --page 1 --print-url
```

使用 Zotero 返回的真实 item key 替换 ITEMKEY。多个 PDF 附件时用 `--attachment KEY` 指定；元数据缺失或 DOI 冲突会停止。导入保留 Zotero 原 PDF，不移动其存储文件；已有 DOI 沿用登记的 bibkey，不覆盖个人理解。

`export --output PATH.bib` 可导出含本机 PDF 路径的副本，中央书目不变；在 Zotero 导入时选择链接原始文件。此类导出含本机路径，应保留在个人工作区。`sync/zotero.json` 保存本机映射，不进 Git。

macOS 可移除 `--print-url` 直接打开 PDF；其他环境使用输出的 Zotero URL。`--page` 使用 PDF 实际页码，从 1 开始。

## 伴读

sr 不附带 Zotero 插件或模型账号。可自行配置合适的插件与模型，具体兼容性需在自己的环境验证；系统提示词可参考本目录的模板，并将工作区改为自己的绝对路径。

```bash
python3 scripts/sr.py read context YOUR_BIBKEY --project YOUR_PROJECT
python3 scripts/sr.py read records YOUR_BIBKEY
```

已知实际 PDF 页码时可加 `--page N` 导出该页文本（需要 Poppler）。上下文先核对 PDF 与登记哈希，不写精读卡或项目状态。

将插件记录目录设置为自己的 `outputs/reading/zotero/sessions/`，并授予所需文件访问权限，才可能由插件保存会话草稿。sr 自身不生成 AI 回答或自动保存对话；提示词只是行为规范，不是硬性权限隔离。
