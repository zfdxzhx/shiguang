import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const handler = typeof worker === "function" ? worker : worker.fetch.bind(worker);
  return handler(new Request("http://localhost/", { headers: { accept: "text/html" } }), { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } }, { waitUntil() {}, passThroughOnException() {} });
}

test("server-renders the independent review checkpoint", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /独立 AI 审核/);
  assert.match(html, /从一份新 PDF 独立开始/);
  assert.match(html, /上传后直接生成图纸 AI 审核报告/);
  assert.match(html, /工艺路线和报价尚未注册产品路由/);
  assert.doesNotMatch(html, /Mock|本地 OCR/);
});
