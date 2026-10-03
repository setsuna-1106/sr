# 使用文档

## 环境

- Python 3.10 或更新版本；无需 Python 第三方包。
- 处理 PDF 时需要 Poppler 的 `pdftotext`。macOS 可通过 `brew install poppler` 安装；Markdown 和 TXT 不需要。
- 输入应是可提取文本的论文。扫描版 PDF 需先在外部 OCR；本工具不会猜测图、表、公式含义。

## 命令

```sh
python3 paper_ir.py convert INPUT.pdf --paper-id BIBKEY --title '论文标题' --output OUTPUT.json
python3 paper_ir.py add OUTPUT.json --kind method --status supported \
  --text '经过人工核对的简短表述' --block b0012 --quote '文本块中的精确子串'
python3 paper_ir.py add OUTPUT.json --kind limitation --status unresolved \
  --text '文中未给出足够信息来确定这个参数'
python3 paper_ir.py validate OUTPUT.json
```

`--paper-id` 建议填本仓库已登记的 bibkey。`convert` 不覆盖已有文件；更新源论文后，请重新转换并重新核对条目。`add` 原子写入，只有整份文件仍通过校验才保存。

## 标注流程

1. 运行 `convert`。输出 `blocks`，每个块有 `id`、原文、PDF 页码或文本行号、文本哈希。
2. 搜索 JSON 中要标注的原文，记录块 ID 和**精确子串**。PDF 的排版可能造成断词与跨栏顺序错误，先对照原 PDF。
3. 运行 `add` 添加 `items`。`kind` 可选 `problem`、`method`、`assumption`、`parameter`、`result`、`limitation`、`resource`。
4. `status=supported` 表示原文直接支持，必须附证据；`inferred` 表示解释或合理推断；`unresolved` 表示资料不足。后两种也可以附原文线索，但不能伪装成原文结论。
5. 运行 `validate`，并人工审查条目表述是否真正被引用句支持。机械校验只检查引用句确实存在。

可以直接编辑 JSON，为一个条目的 `evidence` 添加多个 `{ "block_id": "b0002", "quote": "..." }`，再运行 `validate`。`source.path` 相对 JSON 所在目录，移动文件时要一起更新路径或重新转换。

## 消费输出

下游程序读取 JSON 顶层的 `paper`、`source`、`blocks`、`items`。只将 `supported` 条目当成有原文依据的候选信息；使用 `inferred` 时向用户明示其推断状态；`unresolved` 应转为待查问题。引用某个条目时保留 `paper.id`、`source.sha256`、`block_id` 和 `quote`。

## 常见错误

- `No text blocks found`：扫描版 PDF 或空文件，先 OCR。
- `source SHA-256 mismatch`：源文件已变化，不能沿用旧证据。
- `quote not found in block`：引用的字句与抽取文本不一致，检查 PDF 断行、连字符和块 ID。
- `Output exists`：原型不静默覆盖既有标注；改输出路径或先备份旧文件。
- `supported item needs evidence`：添加 `--block` 与 `--quote`，或把状态改为 `inferred` / `unresolved`。
