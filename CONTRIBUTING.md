# 参与维护

本仓库维护 SPECTRA 的静态前端、历史实采摘录、Windows 连接入口，以及 `edge/` 中从 Jetson 导出的后端与部署代码。部署范围和环境限制见 [边缘端部署指南](docs/edge-deployment.md)。

## 开发与检查

使用 Node.js 22 或更高版本：

```sh
npm ci
npx playwright install chromium
npm start
```

打开终端显示的本地地址。直接编辑 `dist/` 中的 HTML、CSS 和 JavaScript，无需打包。

提交前运行 `npm run check` 和 `npm test`。`npm run screenshots` 会运行相同的浏览器检查，并更新 `docs/screenshots/` 的演示与归档页面截图；不会重取原工作台的实采截图。Windows 已安装 Edge 时检查会直接使用 Edge；其他系统使用 Playwright Chromium。

## 分支和提交

`main` 用于可展示版本；新功能使用 `feat/描述`，修复使用 `fix/描述`。建议通过 Pull Request 合并，附上交互变化、截图和检查结果。此流程是维护约定，不代表仓库已强制启用分支保护。

同一工程在多台电脑上工作时，先 `git pull --ff-only`，再修改、提交和推送。不要覆盖他人提交。

## 数据和演示

保持演示模式的示例标记；真实结果中的 unknown、partial、failed 状态需要如实保留。不要把设备地址、访问令牌、SSH 私钥、原始 IQ 或本地任务报告加入提交。`qa/`、`outputs/` 和 `.spectra-launcher.json` 为本地工作文件。

`dist/measured/` 是历史实采数据的只读公开摘录，更新时遵循 [实采说明](docs/measured-results.md) 的字段白名单与来源检查。不要把 PHY 报头或特征候选计作校验帧；复用同一段 IQ 的任务不算独立采集。浏览器检查会同时验证归档来源、计数和零设备请求行为。

`edge/deploy/source-snapshot.json` 记录原 Jetson 导出文件的校验值。修改后端时应保留来源信息并记录后续改动；CI 的源码快照检查会提示与导出版本的差异。第三方源码由 `edge/deploy/vendor-lock.json` 及补丁恢复，避免提交环境、node_modules、编译产物或运行数据。独立的后端语法/来源检查为 `python3 edge/deploy/verify.py --source-only`；原始回归测试中部分案例依赖既有环境或历史 IQ，不能把整目录测试当作通用无设备检查。

## 展示部署

`Deploy demo` 工作流只发布 `dist/` 到 GitHub Pages。修改前端并推送到 `main` 后会更新演示；文档变更不触发部署。部署目标和权限按 [GitHub Pages 官方工作流说明](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) 配置。
