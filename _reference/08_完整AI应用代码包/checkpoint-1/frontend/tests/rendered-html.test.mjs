import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const handler = typeof worker === "function" ? worker : worker.fetch.bind(worker);
  return handler(new Request("http://localhost/", { headers: { accept: "text/html" } }), { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } }, { waitUntil() {}, passThroughOnException() {} });
}

test("server-renders the CP1 intake and API foundation", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /先安全接入图纸，再配置统一 API/);
  assert.match(html, /选择 PDF 图纸/);
  assert.match(html, /Gemini \+ DeepSeek/);
  assert.match(html, /本检查点没有 AI 运行接口/);
  assert.doesNotMatch(html, /开始 AI 审核|Mock|本地 OCR/);
});
