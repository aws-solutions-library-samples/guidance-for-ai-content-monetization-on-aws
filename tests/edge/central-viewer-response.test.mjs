import assert from "node:assert/strict";
import test from "node:test";

import {
  handleViewerResponse,
  handler,
} from "../../source/edge/central-viewer-response.js";

function responseEvent(headers) {
  return {
    request: {
      headers: {
        host: { value: "content.example.com" },
      },
    },
    response: {
      statusCode: 200,
      statusDescription: "OK",
      headers,
    },
  };
}

test("converts valid license metadata to Link and preserves ETag", () => {
  const etag = { value: "\"content-etag\"" };
  const event = responseEvent({
    etag,
    "content-type": { value: "text/markdown; charset=utf-8" },
    "x-amz-meta-license-url": {
      value: "https://LICENSES.Example.com/licenses/standard?v=1",
    },
    "x-amz-meta-markdown-schema-version": { value: "1.0" },
  });

  const response = handleViewerResponse(event);

  assert.strictEqual(response, event.response);
  assert.strictEqual(response.headers.etag, etag);
  assert.equal(response.headers.etag.value, "\"content-etag\"");
  assert.equal(
    response.headers.link.value,
    "<https://licenses.example.com/licenses/standard?v=1>; rel=\"license\"",
  );
  assert.equal(response.headers["x-amz-meta-license-url"], undefined);
  assert.equal(response.headers["x-amz-meta-markdown-schema-version"], undefined);
  assert.equal(response.headers["content-type"].value, "text/markdown; charset=utf-8");
});

test("appends to an existing Link header without replacing it", () => {
  const event = responseEvent({
    link: { value: "<https://example.com/source>; rel=\"canonical\"" },
    "x-amz-meta-license-url": {
      value: "https://licenses.example.com/licenses/standard",
    },
  });

  const response = handler(event);

  assert.equal(
    response.headers.link.value,
    "<https://example.com/source>; rel=\"canonical\", " +
      "<https://licenses.example.com/licenses/standard>; rel=\"license\"",
  );
});

test("resolves a relative license path against the validated central host", () => {
  const event = responseEvent({
    "x-amz-meta-license-url": {
      value: "/licenses/lic_default_v1",
    },
  });

  assert.equal(
    handler(event).headers.link.value,
    "<https://content.example.com/licenses/lic_default_v1>; rel=\"license\"",
  );
});

test("does not duplicate an existing identical license Link", () => {
  const licenseLink =
    "<https://licenses.example.com/licenses/standard>; rel=\"license\"";
  const event = responseEvent({
    link: { value: licenseLink },
    "x-amz-meta-license-url": {
      value: "https://licenses.example.com/licenses/standard",
    },
  });

  assert.equal(handler(event).headers.link.value, licenseLink);
});

test("invalid or unsafe license metadata is removed without emitting Link", () => {
  const invalidUrls = [
    "http://licenses.example.com/license",
    "https://user@licenses.example.com/license",
    "https://licenses.example.com/license#fragment",
    "https://licenses.example.com/license\r\nx-injected: true",
    "https://localhost/license",
    "https://192.0.2.10/license",
    " https://licenses.example.com/license",
    "https://licenses.example.com:99999/license",
    "/licenses//ambiguous",
    "/other/not-a-license",
  ];

  for (const value of invalidUrls) {
    const event = responseEvent({
      etag: { value: "\"etag\"" },
      "x-amz-meta-license-url": { value },
      "x-amz-meta-private": { value: "do-not-expose" },
    });
    const response = handleViewerResponse(event);

    assert.equal(response.headers.link, undefined, value);
    assert.equal(response.headers["x-amz-meta-license-url"], undefined, value);
    assert.equal(response.headers["x-amz-meta-private"], undefined, value);
    assert.equal(response.headers.etag.value, "\"etag\"", value);
  }
});

test("strips all S3 custom metadata when no license metadata exists", () => {
  const response = handleViewerResponse(
    responseEvent({
      etag: { value: "\"etag\"" },
      "cache-control": { value: "public, max-age=60" },
      "x-amz-meta-internal-id": { value: "cnt_private" },
    }),
  );

  assert.deepEqual(response.headers, {
    etag: { value: "\"etag\"" },
    "cache-control": { value: "public, max-age=60" },
  });
});

test("handles a response without a headers object", () => {
  const event = { response: { statusCode: 304 } };
  assert.deepEqual(handleViewerResponse(event), {
    statusCode: 304,
    headers: {},
  });
});
