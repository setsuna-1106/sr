# sr 多智能体科研工作流

这是 `sr` 科研资产库的最小多智能体架构。它把一个研究问题拆成五个有明确输入和输出的角色：

1. 协调代理：建立任务图、分配角色、汇总状态；
2. 论文证据代理：读取已通过 PaperIR 校验的文献资产，提取可追溯证据；
3. 仿真代理：在项目目录中运行用户明确指定的仿真命令，保存日志和返回码；
4. 基线审查代理：比较指标、经典基线和资源开销；
5. 红队代理：检查证据缺口、数据泄漏、夸大量子优势和不可复现条件。

## 设计边界

- 默认不调用外部模型、不联网、不执行未由用户明确提供的命令。
- 角色之间通过 `manifest.json`、结构化 `result.json` 和共享 `artifacts/` 目录传递信息；不把聊天记录当作事实源。
- `supported` 必须能回到 PaperIR 的原文块；`inferred` 和 `unresolved` 不会被自动升级。
- 仿真结果只有在命令成功、输出日志保存且基线审查完成后，才可标记为可用。
- 红队结果只报告问题和理由，不替研究者放行结论。

## 用法

```sh
python3 scripts/sr.py agents init demo
python3 scripts/sr.py agents plan demo \
  --question '比较某光子线路与经典基线在分类任务上的资源和误差'
python3 scripts/sr.py agents validate demo --run RUN_ID
python3 scripts/sr.py agents run demo --run RUN_ID \
  --paper-ir path/to/paper-ir.json \
  --simulation-command 'python3 experiments/run.py' \
  --metrics path/to/metrics.json
```

也可以按需调用单个角色：

```sh
python3 scripts/sr.py agents call demo --run RUN_ID \
  --role evidence --paper-ir path/to/paper-ir.json
python3 scripts/sr.py agents call demo --run RUN_ID \
  --role simulation --simulation-command 'python3 experiments/run.py'
python3 scripts/sr.py agents call demo --run RUN_ID \
  --role baseline --metrics path/to/metrics.json
python3 scripts/sr.py agents call demo --run RUN_ID --role red_team
python3 scripts/sr.py agents call demo --run RUN_ID --role coordinator
```

角色依赖按顺序检查：`baseline` 依赖 `simulation`，`red_team` 依赖前三个角色，`coordinator` 依赖全部角色。依赖未完成时会返回 `blocked` 和下一步命令。

`run` 是一个本地、可审计的确定性执行器：缺少论文资产、仿真命令或指标时会标记为 `blocked`，不会猜测结果。未来接入 LLM 时，模型适配器应只负责生成角色结果，并继续通过同一套 schema 和红队检查。

## 运行目录

每次运行位于 `projects/<project>/agents/runs/<run-id>/`：

```text
manifest.json       # 问题、角色、输入和状态
tasks/              # 给每个角色的任务合同
results/            # 五个角色的结构化结果
artifacts/          # 仿真 stdout/stderr 和其他派生物
report.md           # 协调代理生成的汇总
```
