---
name: drawing-evidence
description: 在开发或审查图纸接入、多模态结构化草稿、证据规则、人工门和导出时使用
allowed-tools: Read, Grep, Glob
---

# 图纸证据合同

1. 先确认来源：document_id、source hash、page_count 必须绑定同一次接入。
2. 再确认结构：模型输出必须通过 ReviewDraftV2；禁止用自由文本补造字段。
3. 再确认证据：每个 finding 至少包含 code、page、region、description、confidence。
4. 再跑规则：blocked 优先于 needs_review，needs_review 优先于 pass；缺证据必须 fail-closed。
5. 最后人工处理：required_decision_ids 未清零时不得 finalized。

只读取代码、Schema 和受控测试数据。不要读取真实 PDF、`runtime/private` 或 `.env.local`，不要调用外部模型。

完成后返回：发现的问题、文件/函数、复现命令、风险边界；不要直接改测试或降低标准。
