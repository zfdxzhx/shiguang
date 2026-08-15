import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

test("static starter bundle contains no future workflow", async () => {
  const root = new URL("../dist-static/", import.meta.url);
  const html = await readFile(new URL("index.html", root), "utf8");
  const assets = await readdir(new URL("assets/", root));
  const scripts = await Promise.all(assets.filter((name) => name.endsWith(".js")).map((name) => readFile(new URL(`assets/${name}`, root), "utf8")));
  const bundle = scripts.join("\n");
  assert.match(html, /图纸 AI 工程助手/);
  assert.match(bundle, /三个功能，彼此独立/);
  assert.match(bundle, /PDF 与 AI 功能尚未实现/);
  assert.doesNotMatch(bundle, /\/api\/v1\/documents|\/api\/v1\/features|Mock|本地 OCR/);
});
