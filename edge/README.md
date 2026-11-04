# SPECTRA 边缘端部署代码

本目录从现有 Jetson 的实际运行目录读取，包含 Python 任务 API、可视化网关、分析工具、DSH 插件、原生接收插件、构建/启停脚本及依赖锁文件。第三方源码通过固定提交和部署补丁恢复。

[部署指南](../docs/edge-deployment.md) · [源码快照与校验值](deploy/source-snapshot.json) · [第三方固定版本](deploy/vendor-lock.json) · [后端历史说明](docs/backend-history.md)

```text
src/spectrum_agent/       API、队列、采集、DSP、证据报告、可视化网关
plugins/                  DSH 工具适配与本地 C/C++ 协议工具
adapters/libcrafter/      Rust 接收适配器及 Cargo.lock
configs/                  模型工具策略、协议能力和先验；地点配置提供空白示例
scripts/                  实际启停、构建、补丁及历史验证脚本
tests/                    原部署中的 Python 回归测试源文件
runtime/dsh/              固定 DSH package.json / package-lock.json
vendor/usrp_client/       运行所需的传统分割源码副本
deploy/                   环境准备、固定版本恢复、源码校验、补丁及许可证
```

公开源码保持已验证的部署布局 `/home/sribd/spectrum_agent` 与 `usrp_dev` 环境依赖。它不是独立于平台的一键安装包；换用户名、Python ABI 或架构需要适配绝对路径和原生构建。新增准备脚本通过语法/离线检查，尚未在空白 Jetson 上完成从零安装验收。

`deploy/verify.py --source-only` 验证导出源码的 SHA-256 和 Python 语法，不连接设备。`scripts/` 也包含历史采集、模型回放和清理工具；部署启动命令见指南，不需要逐个运行这些历史脚本。

密钥使用后端既有的用户级凭据文件格式；本目录不携带原设备的虚拟环境、二进制依赖、任务库或 IQ。第三方许可证及来源保存在 `deploy/licenses/`、`deploy/vendor-lock.json`，不对这些依赖重新授权。
