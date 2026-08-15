# Golden｜讲师恢复成品

以 2026-08-12 当前正式应用冻结，包含最终界面、优化报告、工程流程表工序复用、显式数量处理和完整离线测试。

## 本阶段真实存在

- 三个独立产品功能
- 最终三种报告格式
- Gemini＋DeepSeek / K3＋DeepSeek
- 可选 macOS 钥匙串
- 完整测试与自检

## 本阶段真实缺失

- 不包含真实 PDF、API Key、运行数据库或历史结果

## 课堂任务

阶段 E：用于终态演示、交付对照或课堂严重阻塞恢复，不能冒充学员开发成果。

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

完成一次离线三路径验收；只有明确授权后才进行实时 Provider 彩排。
