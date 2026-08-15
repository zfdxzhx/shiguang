# Checkpoint 2｜独立 AI 审核

AI 审核可从一份新 PDF 独立运行并直接生成审核报告；工艺和报价的代码与产品路由尚未实现。

## 本阶段真实存在

- `POST /api/v1/features/review/runs`
- 严格 ReviewDraftV2
- 审核结果页与 PDF 报告

## 本阶段真实缺失

- 没有 process / quote 运行路由
- `workflows.py`、参考资料和两种下游报告仍是 CP3 TODO

## 课堂任务

阶段 C：完成角色分工、Provider、严格契约和独立审核。

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

Checkpoint 3：补齐独立工艺、独立报价、统一历史和三种报告。
