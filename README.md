# Linux 内核笔记

使用 Zensical 将 Markdown 文档构建为静态网站，支持自动导航、全文搜索和本地实时预览。

在线阅读：<https://g-xu.github.io/linux-doc/>

以下命令均在仓库根目录执行。环境需要 Python 3.11 或以上版本，建议使用与线上构建一致的 Python 3.12。

## 添加和修改文档

将 `.md` 文件放到 `docs/` 内对应分类，也可以创建新的主题目录：

```text
docs/
├── driver/                 # 设备驱动子系统
│   └── new-topic/
│       ├── 00-overview.md
│       └── 01-api.md
├── core/                   # 内核核心机制
│   └── 新主题.md
├── lib/                    # 内核库与数据结构
├── summary/                # 专题总结
├── books/                  # 读书笔记
├── Linux_APIs/             # API 参考
└── tools/                  # 调试与工具相关文档
```

普通文章使用一级标题开头即可：

```markdown
# 新主题

## 基本概念

正文内容。
```

需要简短的侧栏名称时，可以选用头部元数据；它不是必填项：

```markdown
---
title: 扩展机制
---

# GPIO 子系统的扩展框架
```

新增、删除或重命名文档后，预览或构建会自动更新导航和搜索，不需要维护 `_sidebar.md`。
`.nav.yml` 用于设置分类名称和顺序，根目录导航中的 `*` 会收录新增分类。
现有首页和分类页的 `index.md` 使用 `directory_index` 元数据显示自动目录，修改标题时保留该字段。

图片放在 `docs/assets/` 或文章附近；图片和站内文档链接使用相对路径。例如，在 `docs/core/新主题.md` 中：

```markdown
![示意图](../assets/topic.png)
[GPIO 总览](../driver/gpio/00-overview.md)
```

文件名和链接中的大小写应一致，不使用 Docsify 的 `#/` 路由。
`docs/` 下的内容用于公开网站，不要把临时草稿、私密资料或仅供本地使用的脚本放在这里。

## 本地预览

### Windows

进入 `tools/`，首次使用双击 `setup.cmd` 安装依赖，再双击 `serve.cmd` 启动预览。
也可以在 PowerShell 中运行：

```powershell
.\tools\site.ps1 setup
.\tools\site.ps1 serve
```

脚本会自动定位仓库根目录，不依赖调用时所在的目录。
在 `tools/` 内运行时也可以直接使用 `.\site.ps1`。

默认访问 <http://127.0.0.1:8001/linux-doc/>。端口已占用时，脚本会选择后续可用端口，按终端显示的端口访问。
也可以指定起始端口：

```powershell
.\tools\site.ps1 serve -Port 8002
```

保存 Markdown、样式或配置后，预览会自动重建并刷新。按 `Ctrl+C` 停止前台预览。
`tools/stop.cmd` 仅用于停止本机已记录的后台预览。

### WSL / Linux

先确认 `python3 --version` 为 3.11 或以上版本，并已安装 `venv` 支持。
首次使用创建独立环境并安装依赖：

```bash
python3 -m venv ~/.venvs/linux-doc
source ~/.venvs/linux-doc/bin/activate
python -m pip install -r requirements.txt
zensical serve --dev-addr 127.0.0.1:8001
```

访问 <http://127.0.0.1:8001/linux-doc/>，按 `Ctrl+C` 停止。
之后每次使用只需激活环境并启动预览：

```bash
source ~/.venvs/linux-doc/bin/activate
zensical serve --dev-addr 127.0.0.1:8001
```

端口被占用时，手动改为 `8002` 等其他端口。
Windows 与 WSL / Linux 需要分别安装依赖；这里将 Linux 环境放在主目录，避免共享仓库时发生冲突。

## 构建和检查

Windows 双击 `tools/build.cmd`，或运行：

```powershell
.\tools\site.ps1 build
.\tools\site.ps1 check
```

WSL / Linux 激活环境后运行：

```bash
zensical build --clean
python tools/check_site.py
```

构建结果位于 `site/`，包含 HTML、图片、样式、脚本和搜索资源。
链接检查会区分已记录的缺失资源与新问题。
如果预览正在重建，等待完成后再检查；构建出现警告时，应查看具体原因，不代表构建一定失败。

当前网站使用目录式 URL，应通过 HTTP 服务浏览，不直接双击 `site/index.html`。
部署到自己的静态服务器时，上传 `site/` 的内容，并与 `zensical.toml` 中的 `site_url` 路径保持一致。
更换域名或部署路径时，先修改 `site_url`，再重新构建。静态服务器不需要安装 Python 或 Zensical。

## 发布到 GitHub Pages

先检查本次修改，只暂存需要发布的文件。例如：

```bash
git status
git diff
git add -- docs/core/新主题.md docs/assets/topic.png
git commit -m "Add new kernel notes"
git push
```

示例文件路径应替换为实际修改的路径。文档、图片、样式、配置和工作流属于源文件；不提交生成的 `site/`。

推送到 `main` 后，`.github/workflows/pages.yml` 会自动执行：

1. 获取提交的源文件，准备 Python 并安装依赖。
2. 运行 `zensical build --clean`，生成 `site/`。
3. 上传构建产物并部署到 GitHub Pages。

在 GitHub 仓库的 **Actions → Deploy Linux Docs** 中查看结果，`build` 和 `deploy` 均成功后访问在线网站。
也可以通过 **Run workflow** 手动运行。发布只使用已推送的内容，不会读取本机未提交的修改。

仓库的 **Settings → Pages → Source** 应保持为 **GitHub Actions**，无需指定 `/docs` 目录或创建 `gh-pages` 分支。
线上工作流不运行 `tools/check_site.py`，需要时在本地检查后再推送。

## 目录和文件说明

站点辅助文件集中在根目录的 `tools/`；`docs/tools/` 仍是公开的工具文档分类，两者用途不同。
根目录保留 `zensical.toml` 和 `requirements.txt`，方便直接构建及 GitHub Actions 使用。
以下只列出 Git 仓库中的目录和文件。

| 路径 | 作用 |
| --- | --- |
| `docs/` | Markdown 正文、图片、首页和分类页；其中 `tools/` 也是网站内容 |
| `docs/**/.nav.yml` | 自动导航的分类名称、顺序等配置 |
| `docs/stylesheets/extra.css` | 自定义配色、宽屏布局和阅读排版 |
| `docs/assets/vendor/` | 本地 Mermaid 图表库及版权说明 |
| `tools/overrides/main.html` | 首页和分类目录模板、本地图表库加载 |
| `zensical.toml` | 网站地址、主题、输入输出目录、搜索和 Markdown 扩展配置 |
| `requirements.txt` | 固定 Zensical 版本，供本地和线上安装依赖 |
| `.github/workflows/pages.yml` | GitHub Actions 自动构建和部署流程 |
| `tools/site.ps1` | Windows 的安装、预览、构建、检查和停止入口 |
| `tools/setup.cmd` / `tools/serve.cmd` / `tools/build.cmd` / `tools/stop.cmd` | 对应操作的 Windows 双击入口 |
| `tools/check_site.py` / `tools/test_check_site.py` | 构建网站的链接检查脚本及其测试；不属于正文 |
| `tools/preview_port.py` / `tools/test_preview_port.py` | 本机预览端口探测和测试 |
| `known-source-issues.json` | 链接检查中已知缺失资源的基线；修复对应问题后可更新 |
| `.gitignore` / `.gitattributes` | 排除生成文件，并统一 Windows / Linux 换行规则 |
