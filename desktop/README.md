# 科研工作台 · macOS

科研工作台 v0.11.1 在本地管理研究项目、里程碑、任务、文献和记录。桌面端由 Tauri、React 与 Python worker 组成，数据保存在所选工作区。应用图标为[波函数 ψ](assets/icon.svg)。

## 从源码构建

当前构建面向 Apple 芯片和 macOS 26 或更新版本。准备 Xcode 命令行工具、Node.js 22.12 或更新版本、Rust/Cargo 和 Python 3。随包 worker 的构建环境已在 CPython 3.14.7 上验证。

从仓库根目录运行以下命令。

```sh
cd desktop
npm ci
npm run tauri:build
```

构建脚本先按 `requirements-build.txt` 安装锁定的 Python 依赖，再将 worker 打包到应用中。生成的应用路径为 `src-tauri/target/release/bundle/macos/科研工作台.app`，运行时自带 Python。源码构建使用临时签名。

如果 iCloud 同步目录中的文件元数据导致签名校验失败，可把 `CARGO_TARGET_DIR` 指向本机非同步目录后重新构建。已构建的默认路径可用以下命令复制到一个新目录并核验签名。

```sh
python3 scripts/verify_bundle.py --stage /private/tmp/sr-workbench-review
```

## 使用

首次打开应用时点「选择科研工作区」。应用会直接进入「任务看板」并显示全部任务，之后启动会自动恢复上次打开的工作区。可先选仓库根目录，移动 `DEMO-01`，然后查看 [projects/KANBAN.md](../projects/KANBAN.md) 的变更。正式使用时按[使用手册](../docs/usage.md)建立独立工作区。

- 「任务看板」是侧栏的独立入口，可筛选项目、拖动换列或批量选择。从总览或项目详情打开时会带入所选项目。
- 「科研总览」列出正在进行的任务和活跃项目的下一步。点击任务名称可编辑，点击状态可换列。
- 「研究项目」可以添加里程碑和任务。删除前会显示受影响的卡片和里程碑，可从「回收站」恢复。
- 「资料库」可浏览文件、检索文献、标记重点资料并编辑 Markdown。用 `⌘S` 写回原文件，遇到外部编辑冲突时可查看磁盘内容或另存草稿。
- 「资产 → 文献库」可在列表或详情修改阅读状态，也可以按状态筛选。保存会写回精读卡并同步文献索引。首次为未建卡的文献设置状态时会创建空白精读卡。
- 「资产 → 文献库 → 关系网络」展示已入库论文、共同署名作者和库内直接引用。可按标题、作者或 bibkey 搜索，点击节点聚焦。将指针放在图谱上，用 Mac 触控板双指张开或捏合即可围绕指针缩放。也可以拖动空白处平移，或用右下角按钮缩放。
- 用 `⌘K` 聚焦搜索，用 `⌘N` 新建当前页面的记录或任务。「工作区设置 → 外观」可切换跟随系统、浅色或深色。

署名关系由本地书目按作者全名匹配，同名作者可能被归并。点击「更新引文」才会将库内 DOI 逐项发给 Crossref，查得的参考文献 DOI 保存在工作区的 `workbench/citations/`，供离线查看。已缓存的查询结果在 30 天内复用。只有 DOI 精确对应的库内直接引用才会连线，覆盖范围取决于出版方提交的参考文献数据。[Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)

工作区备份应包含 `workbench/`，其中有任务关联、操作记录和回收站。

## 开发与验证

初次构建后，从 `desktop/` 运行以下命令。开发窗口使用 `http://127.0.0.1:1420` 和仓库中的 Python worker。可通过 `SR_DEV_PYTHON` 指定开发解释器的绝对路径。

```sh
npm run tauri:dev
```

```sh
npm run build
npm run test:drafts
cargo test --manifest-path src-tauri/Cargo.toml --lib
python3 tests/smoke_worker.py
python3 tests/smoke_worker.py --worker src-tauri/resources/worker/sr-worker
```

共享 Python 回归从仓库根目录运行 `python3 scripts/check.py`。
