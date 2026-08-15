# Checkpoint 1｜PDF 与 API 基础

PDF 校验、SHA256、本地分页、第一页预览和统一 API 设置已经实现；AI 业务路由尚未注册。

## 本阶段真实存在

- `POST /api/v1/documents`
- 私有分页图与本地预览
- Gemini＋DeepSeek / K3＋DeepSeek 两种设置

## 本阶段真实缺失

- 没有审核、工艺或报价运行路由
- 工艺、报价与报告模块仍是明确 TODO

## 课堂任务

阶段 B：完成最小工具链、PDF 接入、API 设置和可复现提交。

## 启动与验证

需要 Python 3.11+、Node.js 22+ 和 Poppler（`pdfinfo` / `pdftoppm`）。

```bash
python3 -m pip install -r requirements.txt
cd frontend && npm install && npm run build:static && cd ..
python3 server.py --open
```

固定门禁：

```bash
python3 -m unittest -v tests.test_milestone
python3 server.py --check
cd frontend && npm run lint && npm test
```

## 不变边界

- 产品最终只有 AI 审核、工艺路线、报价三个平级功能。
- 产品 UI 只呈现三个独立功能、统一 API 设置、结果、历史和 PDF 下载。
- 默认 Gemini＋DeepSeek，K3＋DeepSeek 是国产备选。
- Key 不进入浏览器存储、SQLite、报告、日志或 Git。
- 真实图纸只有在本次明确授权后才能发给视觉模型。

## 下一步

Checkpoint 2：实现独立 AI 审核和直接 PDF 报告。
