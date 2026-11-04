# 参与维护

本仓库维护 SPECTRA 的静态前端与 Windows 设备连接入口。Jetson 任务 API、可视化网关和协议分析工具不在本仓库中。

## 开发与检查

使用 Node.js 22 或更高版本：

```sh
npm ci
npx playwright install chromium
npm start
```

打开终端显示的本地地址。直接编辑 `dist/` 中的 HTML、CSS 和 JavaScript，无需打包。

提交前运行 `npm run check` 和 `npm test`。`npm run screenshots` 会运行相同的浏览器检查，并更新 `docs/screenshots/` 的演示截图。Windows 已安装 Edge 时检查会直接使用 Edge；其他系统使用 Playwright Chromium。

## 分支和提交

`main` 用于可展示版本；新功能使用 `feat/描述`，修复使用 `fix/描述`。建议通过 Pull Request 合并，附上交互变化、截图和检查结果。此流程是维护约定，不代表仓库已强制启用分支保护。

同一工程在多台电脑上工作时，先 `git pull --ff-only`，再修改、提交和推送。不要覆盖他人提交。

## 数据和演示

保持演示模式的示例标记；真实结果中的 unknown、partial、failed 状态需要如实保留。不要把设备地址、访问令牌、SSH 私钥、原始 IQ 或本地任务报告加入提交。`qa/`、`outputs/` 和 `.spectra-launcher.json` 为本地工作文件。

## 展示部署

`Deploy demo` 工作流只发布 `dist/` 到 GitHub Pages。修改前端并推送到 `main` 后会更新演示；文档变更不触发部署。部署目标和权限按 [GitHub Pages 官方工作流说明](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) 配置。
