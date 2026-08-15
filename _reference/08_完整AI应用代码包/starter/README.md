# Starter｜产品骨架

只有真实产品外壳和三个独立功能的目标画面；PDF 接入和 AI 路由尚未实现。

## 本阶段真实存在

- 本地 FastAPI/React 骨架
- 三个平级功能卡
- 安全边界与测试入口

## 本阶段真实缺失

- `backend/intake.py` 明确保留 CP1 TODO
- 没有 `/api/v1/documents`
- 没有任何 `/api/v1/features/*` 产品路由

## 课堂任务

阶段 A：读懂代码结构、产品合同和停止条件。不要复制后续检查点。

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

Checkpoint 1：实现私有 PDF 接入和统一 API 设置。
