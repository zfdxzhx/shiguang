import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

test("static CP2 bundle contains review but no process or quote run URL", async () => {
  const root = new URL("../dist-static/", import.meta.url);
  const assets = await readdir(new URL("assets/", root));
  const scripts = await Promise.all(assets.filter((name) => name.endsWith(".js")).map((name) => readFile(new URL(`assets/${name}`, root), "utf8")));
  const bundle = scripts.join("\n");
  assert.match(bundle, /\/api\/v1\/features\/review\/runs/);
  assert.match(bundle, /上传后直接生成图纸 AI 审核报告/);
  assert.match(bundle, /工艺路线和报价尚未注册产品路由/);
  assert.doesNotMatch(bundle, /\/api\/v1\/features\/process\/runs|\/api\/v1\/features\/quote\/runs|Mock|本地 OCR|授权本次 AI 处理|我确认该图纸可以发送给当前模型|result-top/);
});
