# Checkpoint 3｜三功能完整版本

审核、工艺和报价均可从新 PDF 独立运行，统一保存历史并生成各自 PDF。

## 本阶段真实存在

- 三个独立产品路由
- 带来源公开参考数据包
- 确定性报价公式
- 三种 PDF 与统一历史

## 本阶段真实缺失

- 不包含讲师最后一轮界面、报告和显式数量修复

## 课堂任务

阶段 D：完成工艺、报价、前端、历史、报告和 CP3 验收。

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

Golden：用于终态演示、对照和严重故障恢复。
