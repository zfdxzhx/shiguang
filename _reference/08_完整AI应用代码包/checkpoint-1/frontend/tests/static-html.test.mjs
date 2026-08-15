import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

test("static CP1 bundle exposes intake and exactly two provider plans", async () => {
  const root = new URL("../dist-static/", import.meta.url);
  const assets = await readdir(new URL("assets/", root));
  const scripts = await Promise.all(assets.filter((name) => name.endsWith(".js")).map((name) => readFile(new URL(`assets/${name}`, root), "utf8")));
  const bundle = scripts.join("\n");
  assert.match(bundle, /\/api\/v1\/documents/);
  assert.match(bundle, /\/api\/v1\/ai\/config/);
  assert.match(bundle, /Gemini \+ DeepSeek（推荐）/);
  assert.match(bundle, /K3 \+ DeepSeek（国产备选）/);
  assert.match(bundle, /本检查点没有 AI 运行接口/);
  assert.doesNotMatch(bundle, /\/api\/v1\/features|Mock|本地 OCR/);
});
