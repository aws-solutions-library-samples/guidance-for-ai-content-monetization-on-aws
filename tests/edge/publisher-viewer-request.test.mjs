import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  constants,
  createViewerRequestHandler,
  resolveMarkdownRoute,
  routeViewerRequest,
} from "../../source/edge/publisher-viewer-request.js";

const fixtureUrl = new URL("./addressing-vectors-v1.json", import.meta.url);
const fixture = JSON.parse(await readFile(fixtureUrl, "utf8"));

function requestFor(vector, overrides = {}) {
  return {
    method: "GET",
    uri: vector.uri,
    querystring: { ignored: { value: "true" } },
    headers: {
      host: { value: vector.host },
      [constants.WAF_FORMAT_HEADER]: { value: constants.MARKDOWN_FORMAT },
      "x-cm-format": { value: "html" },
    },
    ...overrides,
  };
}

test("addressing fixture is versioned", () => {
  assert.equal(fixture.schema_version, "1.0");
  assert.ok(fixture.vectors.length >= 20);
});

for (const vector of fixture.vectors) {
  test(`addressing vector: ${vector.name}`, () => {
    const request = requestFor(vector);
    assert.equal(resolveMarkdownRoute(request), vector.expected_uri);
  });
}

test("GET and HEAD select the configured central origin exactly once", () => {
  for (const method of ["GET", "HEAD"]) {
    const selectedOrigins = [];
    const request = requestFor(fixture.vectors[1], { method });
    const result = routeViewerRequest(
      request,
      (originId) => selectedOrigins.push(originId),
      "central-content-origin",
    );

    assert.strictEqual(result, request);
    assert.equal(result.uri, "/content/news.example.com/world/story.md");
    assert.deepEqual(selectedOrigins, ["central-content-origin"]);
    assert.deepEqual(result.querystring, { ignored: { value: "true" } });
    assert.equal(result.headers[constants.WAF_FORMAT_HEADER], undefined);
  }
});

test("only the trusted WAF format header enables Markdown routing", () => {
  const base = fixture.vectors[1];
  const variants = [
    {
      host: { value: base.host },
      "x-cm-format": { value: "markdown" },
    },
    {
      host: { value: base.host },
      "x-amzn-waf-cm-format": { value: "html" },
      "x-cm-format": { value: "markdown" },
    },
    {
      host: { value: base.host },
      "x-amzn-waf-cm-format": { value: "Markdown" },
    },
  ];

  for (const headers of variants) {
    const request = requestFor(base, { headers });
    const originalUri = request.uri;
    let selected = false;
    routeViewerRequest(request, () => {
      selected = true;
    }, "central-content-origin");

    assert.equal(request.uri, originalUri);
    assert.equal(selected, false);
  }
});

test("non-read methods pass through without origin selection", () => {
  for (const method of ["POST", "PUT", "DELETE", "OPTIONS"]) {
    const request = requestFor(fixture.vectors[1], { method });
    let selected = false;
    routeViewerRequest(request, () => {
      selected = true;
    }, "central-content-origin");

    assert.equal(request.uri, fixture.vectors[1].uri);
    assert.equal(selected, false);
  }
});

test("unsafe addressing vectors pass through without origin selection", () => {
  for (const vector of fixture.vectors.filter((item) => item.expected_uri === null)) {
    const request = requestFor(vector);
    const originalUri = request.uri;
    let selected = false;
    routeViewerRequest(request, () => {
      selected = true;
    }, "central-content-origin");

    assert.equal(request.uri, originalUri, vector.name);
    assert.equal(selected, false, vector.name);
  }
});

test("derived object keys cannot exceed the S3 key limit", () => {
  const longLabel = "a".repeat(63);
  const request = requestFor({
    host: `${longLabel}.${longLabel}.${longLabel}.${"b".repeat(61)}`,
    uri: `/${"c".repeat(760)}`,
  });

  assert.equal(resolveMarkdownRoute(request), null);
});

test("an unconfigured or failing central origin fails closed", () => {
  for (const originId of ["", "{{CENTRAL_ORIGIN_ID}}"]) {
    const request = requestFor(fixture.vectors[1]);
    routeViewerRequest(request, () => {
      assert.fail("origin selector must not be called");
    }, originId);
    assert.equal(request.uri, fixture.vectors[1].uri);
  }

  const request = requestFor(fixture.vectors[1]);
  routeViewerRequest(request, () => {
    throw new Error("origin missing");
  }, "central-content-origin");
  assert.equal(request.uri, fixture.vectors[1].uri);
});

test("the deploy entry exposes an injectable CloudFront seam", () => {
  const selectedOrigins = [];
  const handler = createViewerRequestHandler(
    {
      selectRequestOriginById(originId) {
        selectedOrigins.push(originId);
      },
    },
    "central-content-origin",
  );
  const request = requestFor(fixture.vectors[2]);

  assert.strictEqual(handler({ request }), request);
  assert.equal(request.uri, "/content/news.example.com/world/story/index.md");
  assert.deepEqual(selectedOrigins, ["central-content-origin"]);
});

test("viewer-controlled query strings do not affect object addressing", () => {
  const vector = fixture.vectors[1];
  const first = requestFor(vector, {
    querystring: { url: { value: "https://attacker.example/private" } },
  });
  const second = requestFor(vector, {
    querystring: { url: { value: "https://another.example/" } },
  });

  assert.equal(resolveMarkdownRoute(first), resolveMarkdownRoute(second));
});

test("already-central paths pass through without recursion", () => {
  for (const uri of ["/content", "/content/news.example.com/story.md"]) {
    const request = requestFor(fixture.vectors[1], { uri });
    let selected = false;
    routeViewerRequest(request, () => {
      selected = true;
    }, "central-content-origin");

    assert.equal(request.uri, uri);
    assert.equal(selected, false);
    assert.equal(request.headers[constants.WAF_FORMAT_HEADER], undefined);
  }
});
