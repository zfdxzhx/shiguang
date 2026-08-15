import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

test("static product bundle contains only the simple independent product flow", async () => {
  const root = new URL("../dist-static/", import.meta.url);
  const html = await readFile(new URL("index.html", root), "utf8");
  const assets = await readdir(new URL("assets/", root));
  const scripts = await Promise.all(
    assets.filter((name) => name.endsWith(".js")).map((name) => readFile(new URL(`assets/${name}`, root), "utf8")),
  );

  assert.match(html, /图纸 AI 工程助手/);
  assert.match(html, /type="module"/);
  assert.ok(assets.some((name) => name.endsWith(".js")));
  assert.ok(assets.some((name) => name.endsWith(".css")));
  assert.doesNotMatch(html, /https?:\/\//);

  const bundle = scripts.join("\n");
  for (const required of [
    "三个功能，彼此独立",
    "开始 AI 审核",
    "生成工艺路线",
    "生成参考报价",
    "上传后直接生成图纸 AI 审核报告",
    "查看 AI 与公开资料的估算依据",
    "确定性公式生成课堂参考报价",
    "Gemini + DeepSeek（推荐）",
    "K3 + DeepSeek（国产备选）",
    "首次运行时验证",
    "留空沿用现有 Key",
    "状态连接中断，正在重试",
    "点击选择 PDF 文件",
    "当前有任务运行",
    "关键提醒",
    "输入信息不足",
    "报告已生成，但图纸不能直接放行",
    "主模型已验证",
    "DeepSeek 未触发",
    "查看模型运行详情",
    "零件名称",
    "重新读取",
    "/api/v1/features/",
  ]) {
    assert.match(bundle, new RegExp(required.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }

  assert.doesNotMatch(bundle, /AI 运行模式|Mock \/ 回放|本地 OCR|授权本次 AI 处理|我确认该图纸可以发送给当前模型|选择授权后即可运行|确认工艺路线，进入预报价|完成本次人工复核|继续查看预报价|Google Gemini（可选扩展）|Kimi K3 high|可直接使用|或将文件拖到这里|完成 Checkpoint|result-top/);
});
