# 图纸 AI 工程助手｜v3.4 真实递进代码包

这不是五份相同代码加一个解锁数字，而是同一产品的五个真实开发快照。学员从 Starter 开始；Checkpoint 只在课堂阻塞时用于恢复；Golden 仅供讲师演示、对照和严重故障恢复。

这里的“阶段 A～E”是图纸 AI 项目内部开发步骤，不占用正式课程表中的“实战 18～23”编号。

## 五个真实快照

| 目录 | 真实存在 | 真实缺失 | 课堂位置 |
| --- | --- | --- | --- |
| `starter` | 产品骨架、三个平级功能目标 | PDF 与所有 AI 产品路由 | 阶段 A：读懂产品 |
| `checkpoint-1` | PDF 校验、哈希、分页、预览、统一 API 设置 | 审核、工艺、报价运行路由 | 阶段 B：接入底座 |
| `checkpoint-2` | 独立 AI 审核、直接审核报告 | 工艺、报价及两种报告实现 | 阶段 C：完成审核 |
| `checkpoint-3` | 三个独立功能、统一历史、三种 PDF | 最后一轮界面和报告优化 | 阶段 D：补齐三功能 |
| `golden` | 当前正式产品终态和完整测试 | 真实资料、Key、运行数据库 | 阶段 E：交付收口与恢复 |

相邻版本之间不只修改 `course_stage.py`：路由、前端、关键业务模块、测试和说明都有实际差异。

## 产品合同

- AI 审核：上传 PDF → AI 识别与审核 → 直接生成《图纸 AI 审核报告》。
- 工艺路线：上传 PDF → AI 提取 → 公开参考资料补齐条件 → 《AI 工艺路线卡》。
- 报价：上传 PDF → AI 提取 → 参考成本补齐 → 程序公式计算 → 《AI 参考报价单》。
- 三个功能彼此独立，不能依赖另一个功能的历史或确认状态。
- 产品 UI 只呈现三个独立功能、统一 API 设置、结果、历史和 PDF 下载。
- 默认 Gemini＋DeepSeek，K3＋DeepSeek 作为国产备选。

## 统一验证

首次使用先进入一个快照安装依赖，例如：

```bash
cd starter
python3 -m pip install -r requirements.txt
cd frontend && npm install && npm run build:static && cd ..
```

然后回到代码包根目录执行整包验证：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/verify_package.py
```

单个快照：

```bash
python3 -m unittest -v tests.test_milestone
python3 server.py --check
cd frontend && npm run lint && npm test
```

离线测试夹具只存在于测试代码，不进入产品界面、设置、历史或报告。离线全绿不等于实时 Provider 已验证；实时成功也不等于正式工程批准、投产工艺或正式报价。

## 数据与密钥边界

- 课堂包不包含真实 PDF、API Key、运行数据库、历史记录或个人绝对路径。
- 学员使用自己的 Key，课堂默认只保存在后端进程内存；macOS 钥匙串是个人电脑可选项。
- 每次真实图纸外发都要重新确认本次授权。
- DeepSeek 只接收任务所需的最小化结构化文本，不接收 PDF、图片或本机路径。

逐段任务见 [课堂带练任务卡.md](./课堂带练任务卡.md)。
