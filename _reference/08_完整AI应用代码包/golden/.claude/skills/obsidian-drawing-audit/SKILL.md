---
name: obsidian-drawing-audit
description: 在 Obsidian 知识库中把图纸审核 SOP 生成可追溯审核笔记时使用
allowed-tools: Read, Grep, Glob, Write
---

# Obsidian 图纸审核 Skill

这是模块十任务 20 的项目 Skill，不是 Obsidian 插件。它把同一套图纸证据合同写成可触发、可复用的 Markdown 审核笔记。

## 输入

- 用户明确指定的图纸文件名或课堂样例；
- `document_id`、来源哈希、页码与区域；
- `ReviewDraftV2` 草稿和确定性规则结果；
- 人工确认状态。

## 操作

1. 先读取 `drawing-evidence` Skill，保持来源、结构、证据、规则和人工门顺序。
2. 只在用户指定的 Obsidian Vault / 目标 Markdown 路径写入；目标不明确时停止询问。
3. 创建一份审核笔记，包含 `source`、`page`、`region`、`confidence`、`rule_state`、`human_decision`。
4. 缺页码、区域、原文或授权时标记 `needs_review`，不得补造。
5. 保留图纸真实名称，不使用 REAL-01 一类内部代号。
6. 返回写入文件、未确认项和复验命令。

## 禁止事项

不要读取 `runtime/private`、`.env.local` 或未授权真实 PDF；不要调用网络模型；不要把模型草稿写成最终工程批准。
