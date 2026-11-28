# 使用手册

## 建立自己的工作副本

公开仓库适合试用和贡献。保存真实科研记录前，创建独立目录，跳过公开仓库的 Git 历史；在 `sr` 根目录执行：

```bash
mkdir ../my-research &&
  git archive HEAD | tar -x -C ../my-research

cd ../my-research
python3 scripts/sr.py doctor --strict
```

向新目录导出当前提交中的文件。若目标目录已存在，请换一个目录名再运行。按需要在工作副本初始化 Git，并选择适合资料的可见性。填写自己的 PROFILE 和项目记录，并通过 `sr add-file` 登记文献。

## 登记 PDF

将一份已核对元数据的 PDF 放入 `library/inbox/`，再运行：

```bash
python3 scripts/sr.py add-file library/inbox/paper.pdf \
  --as YOUR_BIBKEY --doi VERIFIED_DOI --title '已核对标题' \
  --author '作者一 and 作者二' --journal '期刊' --year 2026
python3 scripts/sr.py note YOUR_BIBKEY
python3 scripts/sr.py status YOUR_BIBKEY 粗读
```

参数是占位符，必须换成核对后的真实信息。`add-file` 成功后会将来源归档到 `library/papers/<bibkey>.pdf` 并移除 inbox 中的源文件；需要额外原件时先另行备份。书目只通过工具维护。`add-arxiv` 与 `add-doi` 尚未实现。

阅读状态为 `未读 → 粗读 → 精读中 → 已讲通`，由研究者决定何时变更。建卡不会自动填入“我的话”或“我的推导”。索引是状态的检索视图，主题与文献角色仍由人维护。

桌面版可在「资产 → 文献库」按阅读状态筛选，并在列表或文献详情修改状态。首次为未建卡的文献设置状态时会创建空白精读卡，保存时同步文献索引。

切换到「关系网络」可查看库内论文的共同署名和直接引用。共同署名读取本地书目，引用数据在点击「更新引文」后按 DOI 查询。将指针移到图谱上，使用 Mac 触控板双指张开或捏合即可缩放。数据来源与缓存说明见[科研工作台](../desktop/README.md#使用)。

```bash
python3 scripts/sr.py list
python3 scripts/sr.py find 关键词
python3 scripts/sr.py bib YOUR_BIBKEY
python3 scripts/sr.py index --check
```

## 项目与闪卡

```bash
python3 scripts/sr.py new demo --desc '待验证的研究问题'
python3 scripts/sr.py card add demo --title '一个卡点' --front '原始问题' --kind 问题
python3 scripts/sr.py card list demo
python3 scripts/sr.py tui demo
```

`new` 复制 `projects/_template/`，不会替研究者填写研究判断。新项目的 `state.md` 包含占位日期与行动，填写后再运行严格检查。会话结束记录做了什么、卡在哪，并把下一步写成一个可直接执行的动作。

## 任务看板与科研工作台

运行 `python3 scripts/sr.py board` 打开本机浏览器看板。用示例卡片试用换列和编辑，修改会写回 [projects/KANBAN.md](../projects/KANBAN.md)。在终端按 `Ctrl+C` 停止；用 `--port` 可指定端口。

macOS 桌面版支持管理项目、任务、里程碑、重点资料和记录。首次使用时选择包含 `projects/` 与 `library/` 的工作区目录，应用会进入独立的「任务看板」。以后启动时会自动恢复上次打开的工作区，并在看板显示全部任务。见[安装和构建](../desktop/README.md)。

## 日常检查

```bash
python3 scripts/sr.py overview
python3 scripts/sr.py doctor --verify --strict
python3 scripts/sr.py index --check
```

`inventory` 可补记已有 PDF 的校验基线；旧校验不一致时会停止，不能用它掩盖原件漂移。`sync/assets.json` 记录本机文件大小与哈希；`sync/manifest.md` 只记录真实备份证据。

写入采用本机锁与失败回滚，不能保证断电时多文件整体原子性，也不协调跨设备写入。跨设备修改前先同步，保留冲突副本并人工核对。

## 环境

核心测试与文献管理无需第三方 Python 包。PDF 文本抽取需要 Poppler 的 `pdftotext`；macOS 可用 `brew install poppler`，Linux 使用发行版的 Poppler 工具包。扫描 PDF、图、表与公式需人工处理或另接 OCR；文本抽取不代表语义完整。

Zotero 是可选集成，详见 [Zotero 说明](../projects/research-system/ZOTERO_GUIDE.md)。
