# PaperIR toolbox entry

这是 PaperIR v0.1 的跨项目入口。它不复制实现，而是调用仓库内的规范实现：

```sh
python3 toolbox/paper-ir/paper_ir.py convert INPUT.md \
  --paper-id my-paper --title 'My paper' --output OUTPUT.json

python3 toolbox/paper-ir/paper_ir.py validate OUTPUT.json
```

PDF 输入需要系统中的 `pdftotext`。完整参数、字段解释、证据状态和限制见：

- [`projects/paper-ir/USER_GUIDE.md`](../../projects/paper-ir/USER_GUIDE.md)
- [`projects/paper-ir/DESIGN.md`](../../projects/paper-ir/DESIGN.md)
- [`projects/paper-ir/schema/paper-ir-v0.1.schema.json`](../../projects/paper-ir/schema/paper-ir-v0.1.schema.json)

## 版本约定

- 工具箱入口与实现共享项目版本；当前为 `0.1.0`。
- 不要在工具箱入口中加入论文专用的提示词、密钥或领域规则。
- 修改 PaperIR 行为后，先更新项目实现和测试，再从入口运行回归测试。
