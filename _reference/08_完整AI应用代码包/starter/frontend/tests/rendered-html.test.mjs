import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const handler = typeof worker === "function" ? worker : worker.fetch.bind(worker);
  return handler(new Request("http://localhost/", { headers: { accept: "text/html" } }), { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } }, { waitUntil() {}, passThroughOnException() {} });
}

test("server-renders only the starter product shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /图纸 AI 工程助手/);
  assert.match(html, /三个功能，彼此独立/);
  assert.match(html, /PDF 与 AI 功能尚未实现/);
  assert.doesNotMatch(html, /选择 PDF 图纸|开始 AI 审核|Mock|本地 OCR/);
});
