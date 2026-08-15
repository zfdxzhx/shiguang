# 图纸 AI 审核助手

本地单功能课堂应用：上传 PDF 图纸 → AI 审核（每条问题带页码与证据）→ 下载《图纸 AI 审核报告》。
只做一个功能，不含工艺路线、智能报价、历史等扩展。

## 功能（4 步课堂流程）

1. **打开页面** —— 首页只有「AI 图纸审核」一个入口
2. **上传 PDF** —— 本地上传、解析页数、首页预览；刷新后仍能看到已完成结果
3. **AI 审核** —— K3 high / Gemini 3.7 Flash 读图提取 → 规则引擎 + DeepSeek V4 Pro 文字复核，每条问题带页码与证据，结论统一「待工程确认」
4. **完成演示** —— 下载《图纸 AI 审核报告》

## 快速开始

需要 Python 3.12。

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
copy .env.example .env.local   # 填入密钥后保存
.venv/Scripts/python server.py --open
```

浏览器打开 http://127.0.0.1:8766/ （仅绑定本机）。

`.env.local` 需要的密钥（已被 .gitignore 排除，不进 Git / 数据库 / 前端 / 日志）：

- 视觉组合任选其一：`GEMINI_API_KEY`（Gemini 3.7 Flash）或 `KIMI_API_KEY`（K3 high）
- 可选文字复核：`DEEPSEEK_API_KEY`（DeepSeek V4 Pro）。配置后必须成功，失败则整体审核失败，不会被静默吞掉

## 安全边界

- 原 PDF 与分页图只存本机 `runtime/private/`，只发给用户主动点击审核所选的那个视觉模型
- DeepSeek 只接收每页文字与标题栏，不接收图片 / PDF / 密钥 / 本机路径
- 报告标注「课堂审核草稿，不构成正式工程批准」，结论统一「待工程确认」

## 检查

```bash
.venv/Scripts/python scripts/test_review.py   # 离线审核测试（含报告生成）
.venv/Scripts/python server.py --check        # 服务器自检
# 服务运行后：
.venv/Scripts/python scripts/test_upload.py   # 真实上传 / 预览 / 设置检查
```

## 目录结构

- `backend/` —— 上传、审核编排、严格契约校验、报告生成
- `static/` —— 首页（单入口）与前端状态
- `scripts/` —— 课堂样张生成与测试
- `samples/` —— 课堂脱敏样张与示例报告
- `server.py` —— 本地启动入口（仅 127.0.0.1）
