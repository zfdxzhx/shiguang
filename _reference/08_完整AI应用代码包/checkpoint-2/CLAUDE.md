@AGENTS.md

# Claude Code 项目说明

## 一句话目标

把一个 PDF 图纸应用做成三个独立的最短路径：审核报告、工艺路线卡、参考报价单。

## 架构入口

- `server.py`：本地启动和自检。
- `backend/app.py`：FastAPI 路由，产品主路由统一在 `/api/v1/features/*`。
- `backend/intake.py`：PDF 校验、SHA256 和本地分页。
- `backend/providers.py`：Gemini/K3 视觉提取与 DeepSeek 最小化复核。
- `backend/models.py`：严格结构化契约。
- `backend/reference_profiles.py`：带来源的课堂制造和成本参考数据。
- `backend/workflows.py`：工艺模板和确定性报价公式。
- `backend/service.py`：三功能运行、历史和报告。
- `frontend/app/drawing-review-app.tsx`：首页三入口、结果、历史和统一 API 设置。

## 开发顺序

1. 先跑累计测试，解释首个失败。
2. 先完成 PDF 接入和本地安全边界。
3. 再接 AI Provider 和 `ReviewDraftV2`。
4. 将三个功能分别实现，每个都从 `document_id` 开始。
5. 最后接前端、历史和 PDF 报告，用同一组 E2E 复验。

实现时保持三个功能独立，并只保留统一 API 设置、结果、历史和 PDF 下载。

## 需要停下请求用户的事

未授权的真实图纸外发、付费 API 调用、GitHub push、生产写入和新权限。
