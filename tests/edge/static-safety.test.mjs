import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const edgeFiles = [
  "publisher-viewer-request.js",
  "central-viewer-response.js",
];

test("edge functions contain no lookup, network, or secret dependencies", async () => {
  for (const file of edgeFiles) {
    const source = await readFile(
      new URL(`../../source/edge/${file}`, import.meta.url),
      "utf8",
    );

    assert.ok(Buffer.byteLength(source, "utf8") <= 10 * 1024, `${file} exceeds 10 KB`);
    assert.doesNotMatch(source, /\b(?:kvs|fetch|XMLHttpRequest|DynamoDB|SecretsManager)\b/i, file);
    assert.doesNotMatch(source, /(?:api[_-]?key|authorization|cookie|secret)/i, file);
    assert.doesNotMatch(source, /for\s*\([^)]*\bof\b/, file);
    assert.doesNotMatch(source, /=>/, file);
  }
});

test("the viewer-request wrapper uses only preconfigured origin selection", async () => {
  const source = await readFile(
    new URL("../../source/edge/publisher-viewer-request.js", import.meta.url),
    "utf8",
  );

  assert.match(source, /selectRequestOriginById/);
  assert.doesNotMatch(source, /createRequestOrigin|updateRequestOrigin/);
});
