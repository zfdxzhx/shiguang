# DEVELOPMENT_PLAN｜图纸 AI 审核助手

> 本文档是项目的唯一开发总纲：目标、产品契约、技术栈、架构、安全边界、里程碑、验收门禁与开发流程。
> 更新时机：每个里程碑验收后同步修订一次；需求变更先行更新本文，再动代码。

## 1. 项目概览

| 项 | 内容 |
|---|---|
| 项目名 | 图纸 AI 审核助手（Drawing AI Review Assistant） |
| 一句话目标 | 把 PDF 图纸应用做成三个独立的最短路径：**AI 审核报告、工艺路线卡、参考报价单** |
| 产品形态 | 本地单机 Web 应用（localhost-only），浏览器访问 |
| 参考原型 | `_reference/08_完整AI应用代码包/`（课堂代码包：starter / checkpoint-1..3 / golden），按递进切片实现 |
| 技术基调 | Python 标准库优先；FastAPI 后端 + Next.js 静态前端；严格结构化契约；本地优先 |

### 1.1 产品契约（不变）

产品只有三个**平级**功能，每个功能都能从 PDF 上传独立开始，**不得**以另一个功能的完成、确认或历史结果作为前置条件：

1. **AI 审核**：AI 提取图纸事实 → 依据审核规则 → 直接生成可下载 PDF 报告。
2. **工艺路线**：AI 提取图纸事实 + 公开参考资料包补齐课堂条件 → 直接生成路线卡。
3. **报价**：AI 与公开参考资料包补齐课堂成本假设 → 金额只由**代码确定性公式**计算 → 生成参考报价单。

共享部分仅限：PDF 接入、AI 设置、结构化提取、公开资料包、历史、PDF 导出。
产品 UI 只呈现：三个独立功能入口、统一 API 设置、结果、历史、PDF 下载。

## 2. 技术栈与运行环境

| 层 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.11+ | 后端 |
| Web 框架 | FastAPI + uvicorn | 仅绑定 `127.0.0.1` / `localhost` |
| 数据校验 | pydantic v2 | 严格 Schema，模型输出视为不可信输入 |
| 前端 | Next.js + React + TypeScript | `vite.static.config.ts` 静态构建到 `dist-static`，产品不依赖 Node 运行时 |
| PDF 解析 | Poppler（`pdfinfo` / `pdftoppm`） | 本地方页渲染；不依赖在线 OCR |
| 数据库 | SQLite（`backend/database.py`） | 仅存历史元数据与结果摘要，**不存 Key** |
| PDF 生成 | reportlab + Pillow | 报告 / 路线卡 / 报价单三类导出 |
| 凭据 | keyring（系统钥匙串） | Key 不落盘到代码、数据库或 Git |
| AI Provider | 默认 **Gemini＋DeepSeek**；国产备选 **K3＋DeepSeek** | Gemini/K3 接收分页图做视觉提取；DeepSeek 只收最小化文本做复核 |
| MCP | stdio 本地工具（`.mcp.json`） | 只读项目工具，不访问网络 |

### 2.1 前置环境

- Python 3.11+、Node.js 22+、Poppler（`pdfinfo` / `pdftoppm` 在 PATH）。
- AI Provider Key 存放于系统钥匙串（不写入 `.env.local` 之外任何文件）。

## 3. 目标目录结构

```
图纸AI审核助手/
├── DEVELOPMENT_PLAN.md        # 本文件（开发总纲）
├── README.md                  # 启动与使用说明
├── AGENTS.md                  # 共享开发规则（契约/原则/门禁）
├── CLAUDE.md                  # Claude Code 项目说明
├── CODEX_REVIEW.md            # 独立检查清单
├── server.py                  # 本地启动与自检入口
├── requirements.txt
├── .env.example               # 仅示例，不包含真实 Key
├── .mcp.json                  # MCP stdio 本地工具
├── .gitignore
├── tools/
│   ├── classroom_mcp.py       # MCP 只读项目工具
│   └── cli_connection_demo.py
├── backend/
│   ├── app.py                 # FastAPI 路由（/api/v1/features/*）
│   ├── intake.py              # PDF 校验、SHA256、本地方页
│   ├── providers.py           # Gemini/K3 视觉提取 + DeepSeek 复核
│   ├── models.py              # 严格结构化契约（ReviewDraftV2 等）
│   ├── rules.py               # 图纸审核规则
│   ├── engineering_review.py  # AI 审核服务
│   ├── reference_profiles.py  # 带来源的课堂制造/成本参考数据
│   ├── workflows.py           # 工艺模板 + 确定性报价公式
│   ├── service.py             # 三功能运行、历史、报告
│   ├── pdf_report.py          # AI 审核 PDF 报告
│   ├── process_plan_pdf.py    # 工艺路线卡 PDF
│   ├── quote_report.py        # 报价单 PDF
│   ├── database.py            # SQLite 历史
│   ├── credential_store.py    # 钥匙串凭据
│   └── course_stage.py        # 里程碑门禁状态
├── frontend/
│   ├── app/
│   │   ├── page.tsx           # 首页三入口
│   │   └── drawing-review-app.tsx  # 单页核心组件
│   └── ...
├── tests/
│   └── test_milestone.py      # 累计验收测试
├── runtime/                   # 运行时私有目录（不入 Git）
└── _reference/                # 课堂参考代码包（只读参考，非实现副本）
```

## 4. 安全边界（任何实现不得突破）

1. **Key 隔离**：API Key 只存在于系统钥匙串，绝不进入 Git、SQLite、浏览器、报告、日志。
2. **真实图纸不外发**：原 PDF、分页图只有在用户**主动点击运行某项功能**时才发送给当次任务的视觉模型；事前授权不可跨次复用。
3. **DeepSeek 最小化**：只按需接收结构化文本；不收图片、PDF、绝对路径、密钥。
4. **绝对路径与私有文件**：`runtime/private`、`.env.local`、本机绝对路径不得进入 Git、响应、日志或报告；服务只返回文档 id 与相对引用。
5. **模型输出不可信**：输出先过严格 Schema + 证据校验；弱坐标/缺失证据一律**拒绝**，不猜测、不补造。错误输出不能伪造成结论。
6. **结论边界**：AI 审核报告是"课堂审核草稿"非正式批准；工艺路线不冒充 NC 程序/投产参数；报价不冒充商务要约，费率替换前仅供参考。
7. **本地优先**：服务只绑定本机；MCP 工具只读、不联网。

## 5. 里程碑计划

按课堂递进切片组织；每个里程碑以**固定门禁全绿**为完成条件，不通过放宽契约、删除断言或拿夹具冒充真实 AI 制造绿灯。

### M1｜PDF 与 API 基础（对齐 checkpoint-1）

- [ ] 项目骨架：`server.py`、`backend/app.py`、统一 `/api/v1/features/*` 路由、自检
- [ ] PDF 接入：文件头校验、SHA256、本地私有分页（Poppler）、第一页预览
- [ ] 统一 API 设置页：默认 Gemini＋DeepSeek / 国产备选 K3＋DeepSeek，Key 走钥匙串，不回显
- [ ] 前端骨架：三入口静态页 + 设置入口
- [ ] `tools/classroom_mcp.py` 只读自检 + `.mcp.json`
- **验收**：`python3 -m unittest -v tests.test_milestone` 通过；`python3 server.py --check` 通过；`cd frontend && npm run lint && npm test` 通过；原 PDF 与 Key 不入 Git。

### M2｜独立 AI 审核（对齐 checkpoint-2）

- [ ] 严格契约 `ReviewDraftV2`：模型 JSON 校验，拒绝额外字段、空证据、非法坐标（fail-closed）
- [ ] `backend/rules.py` + `engineering_review.py`：图纸事实提取 → 规则命中 → 证据定位（行号/区域）
- [ ] `backend/providers.py`：Gemini/K3 视觉提取 + DeepSeek 最小化文本复核
- [ ] `backend/pdf_report.py`：AI 审核直接生成可下载 PDF 报告
- [ ] 前端接入审核入口与结果页
- **验收**：从**新 PDF** 独立运行审核全流程并生成报告；弱坐标被拒绝；产品内无"人工定稿页"；审计记录记录真实调用与离线测试。

### M3｜工艺路线 + 报价 + 收尾（对齐 checkpoint-3）

- [ ] `reference_profiles.py`：带来源与访问日期的课堂制造/成本参考数据
- [ ] `workflows.py` + `process_plan_pdf.py`：从新 PDF 生成工艺路线卡（标注课堂假设，不冒充 NC/投产参数）
- [ ] `quote_report.py`：确定性公式报价单，金额可手工复算（AI 不猜总价）
- [ ] `backend/database.py`：统一历史记录
- [ ] 前端三入口 + 结果 + 历史 + 三类 PDF 下载完整跑通
- [ ] 端到端验收：三条路径均从新 PDF 独立运行，四条固定门禁全绿
- **验收**：详见"固定门禁"一节全部通过；产品 UI 不出现 Mock/测试夹具标识。

## 6. 固定门禁（每条命令原样重跑）

```bash
python3 -m unittest -v tests.test_milestone
python3 server.py --check
cd frontend && npm run lint
cd frontend && npm test
```

实现、审查、业务判断三种责任分离：CI 只保证实现正确；`CODEX_REVIEW.md` 做独立审查；**业务结论由人负责**。

## 7. 开发流程约定

1. 先跑累计测试，解释首个失败，再动手。
2. 先 PDF 接入与本地安全边界，再接 AI Provider 与严格契约。
3. 三个功能分别实现，各自从 `document_id` 开始，保持独立。
4. 最后接前端、历史与 PDF 报告，用同一组 E2E 复验。
5. 每轮交接记录四件事：**改了什么 / 跑了什么 / 结果是什么 / 还没验证什么**。
6. 需要停下请求用户的事：未授权真实图纸外发、付费 API 调用、GitHub push、生产写入、新权限。

## 8. 风险与假设

| 风险 / 假设 | 应对 |
|---|---|
| 视觉模型坐标不可靠 | fail-closed：弱证据直接拒绝，不猜框 |
| 未配 Key / 网络不可用 | 自检区分"真实调用"与"离线测试"，产品不拿夹具冒充真实 AI |
| 课程费率非真实商务数据 | 报价页显著标注"课堂假设 + 来源 + 替换说明" |
| 国产备选差异（如 DeepSeek 不收图） | 抽象 Provider 接口，按能力路由 |
| 大 PDF 性能 | 本地私有分页 + 仅当次任务发送必要页 |

## 9. 完成定义（DoD）

- [ ] 三个功能均从新 PDF 独立跑通并产出 PDF。
- [ ] 四条固定门禁全绿；报告/日志/仓库中无 Key、无绝对路径、无真实图纸泄漏。
- [ ] 审计记录区分真实 AI 调用与离线测试。
- [ ] README 启动命令可复现；AGENTS.md / CODEX_REVIEW.md 同步更新。
- [ ] `_reference` 仅作参考，未混入实现。
