# DSH 社区插件推荐清单 — YOLO工作台（长期项目）

> 依据：yolo_lab 项目交接文档 + 社区目录（kejixiaoliang/awesome-dsh-plugins 306+ 插件 / awesome-dsh-plugin / npm registry 实测）。
> 状态：**已装 8 个 starter（2026-08-29 起，重启后已验证全部加载）**；"可选/后续" 分组按需再装，安装统一用 `dsh plugin --profile web add <pkg>`（GitHub 源用 `github:owner/repo`）。

## 已安装（starter，均验证）

| 插件 | 说明 | 本项目用途 |
|---|---|---|
| dsh-mnemon | 本地三层记忆（Runtime Memory/文档/Space） | 长期项目知识跨会话保真：8位hex崩溃、novram、中文路径、同步义务等"坑"自动记住 |
| dsh-sentinel | file/command/http/process/webhook 条件监视→唤醒 agent | 800 批量/训练/ComfyUI 长任务：监视 `runs/h3/800_进度.md`、端口 8188、python 进程 |
| dsh-reminder（peon-ping） | 完成/审批跨窗口弹窗提醒 | 27h 批量、训练完自动提醒你回来看 |
| @dsh-external/dsh-vision-toolkit | 视觉工具箱：OCR/长截图/UI 还原/定位/像素对比/Artifacts | 验证 archify 四图 visual-check PNG、UI 截图、摔倒帧质量 |
| dsh-scout | 只读环境探测（runtime/资源/端口/服务/GPU） | 多服务环境：ComfyUI 8188/Ollama 11434/图片服务 8085/分享 8642/GPU |
| dsh-port-guard | 端口冲突处理（复用/切换/精杀） | 上述端口被占时的快速处置 |
| dsh-context | 上下文构成/演化洞察面板 | 超长 spec 会话（101K 推理链）与费用可视化 |
| dsh-plugin-hub | 插件管理面板+多源市场+技能管理 | 后续自己发现/启停/更新社区插件 |

## 备选 / 后续（按痛点优先级）

### A. 长期迭代安全（强烈建议尽快）
- **dsh-turn-rewind**（105★，github:Anionex/dsh-turn-rewind）— 会话+工作区状态回滚。项目无 git，长时间开发的安全网。
- **dsh-tool-git**（npm dsh-tool-git 0.1.3）— 结构化 git 工具。**建议给 yolo_lab 源码区（排除 data/runs/tools 大件）初始化 git**，长期开发可追溯；本机 GitHub/VPN 可用后配合。
- **dsh-review-skills**（github:ben7am1n）— code-review/simplify/plan-then-execute/test-first 纪律包，对接"同步义务"流程。
- **dsh-safe-delete**（npm 0.2.1）— 删除走回收站；112GB 级项目防误删（数据集清理/归档操作多）。

### B. 上网查询增强（用户重点）
- 已内置 `web_search`（deepseek 路线）；如需多源/更稳：**dsh-search-mcp**（Tavily/Brave/Exa/Perplexity/DDG，需各平台 key）或 **dsh-plugin-web-search-tavily**（npm 0.1.2，单一 key）。
- **dsh-webfetch**（github:withlovehub）— robots 合规 + SSRF 保护的抓取，用于拉官方文档/README。

### C. 双 Python / 测试
- **@huanlin/dsh-plugin-interpreters**（npm 0.2.3）— run_python 可配解释器：应用 3.13 + ComfyUI venv 3.10，两条链都能跑。
- **dsh-test-runner**（github:suimi8）— pytest 自动识别；项目已有 pytest.ini/tests。

### D. 文档/表格/历史会话
- **dsh-cowork**（npm 0.0.1，很早期，谨慎）— xlsx/pdf/docx/pptx 读写：桌面复盘点.xlsx、考核表.docx。
- **dsh-chat-import**（npm 0.8.1 / 120★）— 把旧 Codex/Claude 会话导入 DSH 延续开发上下文（交接文档提到 zstd 会话记录）。
- **dsh-session-search**（github:Tieboyh）— 跨 dsh/Codex/Claude/pi 会话全文搜索。

### E. 量化/视觉替代
- **@liustack/modlens**（3734★）— 结构化视觉证据（OCR/布局/语义），纯文本模型看图首选。
- **dsh-screenshot-diff**（npm 0.1.1）— 同一 prompt 的 UI 截图像素 diff（主题切换/自检回归）。

### F. 安全与打包
- **dsh-security-audit**（github:omdsh-dev，13★）/ **dsh-security-scan**（github:ben7am1n）— 打包部署包前扫 .env/密钥/危险模式。

## 不建议/慎装
- dsh-usage（同作者工作台配套，与已装功能重叠，可选）；多套记忆插件（mnemon 已够，避免打架）；TUI/皮肤类与本项目无直接关系；dsh-daily-progress 等偏个人工作流。

## 安装注意（本机实测）
- GitHub 直连（github.com/releases）不可达时：`dsh plugin add` 会因旧 URL 依赖整体失败。本 profile 已把 worktable/super-injector 改为 `link:vendor/extract/*` 本地目录（tgz 经 api.github.com 下载后解压），与网络解耦；升级这两个包需手动替换 vendor/extract 内容。
- 新装插件走 npm registry 即可；`autoInstallPeers: false`（官方预发布版本号不兼容 pnpm 自动拉 peers）；装完重启 dsh web 生效（dev_plugin_status 可见 [active]）。
