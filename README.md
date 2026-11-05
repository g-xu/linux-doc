# Linux 内核笔记：Zensical 试验版

这是 `../Linux-html` 的独立副本。原 Docsify 站点及 Linux 源码没有修改。

## 本地浏览

双击 `serve.cmd`，在浏览器打开终端中显示的地址，默认是 `http://127.0.0.1:8001/`。
保存 Markdown 后，Zensical 自动重建并刷新预览。关闭预览窗口或按 Ctrl+C 停止服务。
本次试验留下的后台预览可以双击 `stop.cmd` 停止，再用 `serve.cmd` 重新启动。

也可以在本目录运行：

```powershell
.\site.ps1 serve
```

## 新增文档

正文放在 `docs/` 内，例如 `docs/core/新主题.md` 或 `docs/driver/new-topic/00-overview.md`。
使用普通 Markdown，以 `# 标题` 开头即可，不强制添加 YAML 元数据。
导航、全文搜索和首页的分类列表由构建流程自动更新，不需要生成 `_sidebar.md`。

`.nav.yml` 只定义初始分类名称及顺序；`*` 会自动收录后续新增内容。
编号文章在迁移时添加了简短的 `title` 元数据，用于侧栏显示，正文标题保持不变。

图片建议放在 `docs/assets/` 或文章附近，使用相对路径。
站内链接使用 `[标题](相对路径.md)`，不要使用 Docsify 的 `#/` 路由。

## 构建和发布

双击 `build.cmd`，或运行 `.\site.ps1 build`。
构建结果位于 `site/`，把这个目录的内容部署到静态 Web 服务器即可。
服务器不需要安装 Python 或 Zensical。

当前版本使用目录式 URL，应通过 HTTP 浏览，不以双击 `site/index.html` 作为浏览方式。
样式、搜索代码、图片和 Mermaid 图表库均保存在本地，阅读时不依赖 CDN。

## 检查结果

已经验证新增目录自动进入首页和侧栏，新增文章正文自动进入全文搜索。
同时测试了中文关键词、内核函数名、桌面和手机宽度下的排版，以及已有图片和图表。

原文仍有五类缺失资源，保留原文引用，不补造缺失内容：

- ARM64 读书笔记中的一张粘贴图片未保存在原目录。
- 调度文档引用了已不存在的旧 `.qoder` 方法论文件。
- 调度入门导读链接到尚未收录的 `sub/core`、`sub/topology`、`sub/cpu-prio-heaps` 三篇文章。

详情在 `known-source-issues.json`。构建警告会继续提示缺失文章，便于后续补齐。
运行 `.\site.ps1 check` 可以检查构建结果，区分原文缺失项和新增的坏链接。

## 环境和迁移

依赖安装在本项目的 `.venv/`，版本固定在 `requirements.txt`。
换一台电脑后安装 Python 3.11 或以上版本，再运行 `setup.cmd`。

迁移复制了正式分类下的正文与图片，没有复制 `.git`、`output` 草稿、工具脚本、
旧站点生成的首页、侧栏、搜索索引和分类 README。
调度文档引用的 `.evidence/` 已复制到 `docs/core/sched/evidence/`，对应链接已调整。
为避免 Windows 与 Linux 的路径大小写差异，`books/奔跑吧Linux内核.md` 已作为
`docs/books/奔跑吧linux内核/index.md`，与章节笔记放在同一目录，内容保持不变。
`migration-report.json` 保存每个复制文件的源文件校验值，便于核对。
`tools/migrate.py` 是一次性迁移工具，遇到已有正文会拒绝覆盖，不用于日常更新。
这两项以及 `tools/test_migration.py` 仅保留在迁移时的本机目录，不纳入 Git。

## Git 仓库

提交网站源文件：`docs/`（含 `.nav.yml`、图片与本地 Mermaid 库）、`overrides/`、
`zensical.toml`、`requirements.txt`、`.gitignore`、`README.md`、Windows 操作入口，
以及 `tools/check_site.py` 和 `known-source-issues.json`。

`.venv/`、`.cache/`、`site/`、`logs/`、`test-results/` 和 Python 缓存由 `.gitignore`
排除，不提交。旧 Docsify 文档与 Linux 源码也不属于本仓库。

克隆后运行 `setup.cmd` 安装环境，再运行 `serve.cmd` 预览或 `build.cmd` 构建。
`stop.cmd` 仅用于迁移时留下的后台预览；正常前台预览用 Ctrl+C 停止。
