# PaperIR — 可追溯文本证据

将本地 PDF、Markdown 或 TXT 抽取为带位置与 SHA-256 的文本块，再添加有精确引文的信息条目。工具不调用模型，不执行输入中的代码。

- [使用文档](USER_GUIDE.md)
- [数据模式](schema/paper-ir-v0.1.schema.json)
- [虚构文本](examples/toy-paper.md)与[已标注 JSON](examples/toy-paper.json)

从仓库根目录验证现有示例：

```bash
python3 toolbox/paper-ir/paper_ir.py validate projects/paper-ir/examples/toy-paper.json
```

新建转换请使用一个尚不存在的输出路径：

```bash
python3 toolbox/paper-ir/paper_ir.py convert projects/paper-ir/examples/toy-paper.md \
  --paper-id synthetic-calibration --title 'Synthetic calibration' --output /tmp/sr-paper-example.json
```

`convert` 不覆盖已有文件。PDF 抽取需要 `pdftotext`，扫描件、双栏顺序、图表与公式需人工检查。`supported / inferred / unresolved` 区分证据状态，机械校验不能判定概括是否正确。
