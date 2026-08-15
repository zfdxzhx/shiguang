import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const handler = typeof worker === "function" ? worker : worker.fetch.bind(worker);
  return handler(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the independent three-feature product", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<html lang="zh-CN">/i);
  assert.match(html, /<title>图纸 AI 工程助手<\/title>/i);
  assert.match(html, /三个功能，彼此独立/);
  assert.match(html, /AI 审核/);
  assert.match(html, /工艺路线/);
  assert.match(html, /报价/);
  assert.match(html, /开始新任务/);
  assert.match(html, /选择工作类型/);
  assert.doesNotMatch(html, /开发指引|AI 运行模式|Mock \/ 回放|确认工艺路线，进入预报价|完成本次人工复核/);
  assert.doesNotMatch(html, /Your site is taking shape|SkeletonPreview/);
});
