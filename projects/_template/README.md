# Project

项目目录内保存可复现实验、推导、图表和状态；大文件放外部同步位置，在 `sync/manifest.md` 登记。

## 闪卡

灵感、研究细节和待验证问题记录在 `cards/`：

```bash
sr card add project-name --title "标题" --front "我现在想记住的问题或事实" \
  --kind 灵感 --tags 标签1,标签2
sr card list project-name
sr tui project-name
```
