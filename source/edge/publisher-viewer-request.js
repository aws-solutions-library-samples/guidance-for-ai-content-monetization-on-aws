import cf from "cloudfront";

// Replaced with the preconfigured central origin ID during infrastructure bundling.
const CENTRAL_ORIGIN_ID = "{{CENTRAL_ORIGIN_ID}}";
const WAF_FORMAT_HEADER = "x-amzn-waf-cm-format";
const MARKDOWN_FORMAT = "markdown";
const MAX_HOST_LENGTH = 253;
const MAX_URI_LENGTH = 900;

function hasControlOrNonAscii(value) {
  return /[^\x20-\x7e]/.test(value);
}

export function normalizeHost(value) {
  let candidate = value;
  if (typeof candidate === "string" && candidate.toLowerCase().endsWith(":443")) {
    candidate = candidate.slice(0, -4);
  }
  if (typeof candidate === "string" && candidate.endsWith(".")) {
    candidate = candidate.slice(0, -1);
  }
  if (
    typeof candidate !== "string" ||
    candidate.length === 0 ||
    candidate.length > MAX_HOST_LENGTH ||
    candidate !== candidate.trim() ||
    hasControlOrNonAscii(candidate) ||
    /[:\\/@%]/.test(candidate)
  ) {
    return null;
  }

  const host = candidate.toLowerCase();
  const labels = host.split(".");
  if (labels.length < 2 || /^\d+\.\d+\.\d+\.\d+$/.test(host)) {
    return null;
  }

  for (let labelIndex = 0; labelIndex < labels.length; labelIndex += 1) {
    const label = labels[labelIndex];
    if (
      label.length === 0 ||
      label.length > 63 ||
      !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label)
    ) {
      return null;
    }
  }

  return host;
}

function normalizePercentEscapes(segment) {
  let decodedSegment;
  try {
    decodedSegment = decodeURIComponent(segment);
  } catch (_error) {
    return null;
  }
  if (/[\x00-\x1f\x7f]/.test(decodedSegment)) {
    return null;
  }

  let normalized = "";

  for (let index = 0; index < segment.length; index += 1) {
    const character = segment[index];
    if (character !== "%") {
      normalized += character;
      continue;
    }

    const escape = segment.slice(index + 1, index + 3);
    if (!/^[0-9a-fA-F]{2}$/.test(escape)) {
      return null;
    }

    const decoded = String.fromCharCode(Number.parseInt(escape, 16));
    const lowerEscape = escape.toLowerCase();

    // Encoded separators, re-encoding, query, and fragment delimiters are ambiguous.
    if (["2f", "5c", "25", "3f", "23"].includes(lowerEscape)) {
      return null;
    }

    if (/^[A-Za-z0-9\-._~]$/.test(decoded)) {
      normalized += decoded;
    } else {
      normalized += `%${escape.toUpperCase()}`;
    }
    index += 2;
  }

  return normalized;
}

export function normalizePath(value) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > MAX_URI_LENGTH ||
    !value.startsWith("/") ||
    hasControlOrNonAscii(value) ||
    value.includes("\\") ||
    value.includes("?") ||
    value.includes("#")
  ) {
    return null;
  }

  if (value === "/") {
    return "index.md";
  }

  const directoryPath = value.endsWith("/");
  const rawSegments = value.slice(1, directoryPath ? -1 : undefined).split("/");
  const normalizedSegments = [];

  for (let segmentIndex = 0; segmentIndex < rawSegments.length; segmentIndex += 1) {
    const rawSegment = rawSegments[segmentIndex];
    if (rawSegment.length === 0 || rawSegment === ".") {
      continue;
    }
    if (rawSegment === "..") {
      if (normalizedSegments.length === 0) {
        return null;
      }
      normalizedSegments.pop();
      continue;
    }

    const segment = normalizePercentEscapes(rawSegment);
    if (
      segment === null ||
      segment === "." ||
      segment === ".." ||
      !/^(?:[A-Za-z0-9\-._~!$&'()*+,;=:@]|%[0-9A-F]{2})+$/.test(segment)
    ) {
      return null;
    }
    normalizedSegments.push(segment);
  }

  if (directoryPath) {
    normalizedSegments.push("index.md");
  } else {
    normalizedSegments[normalizedSegments.length - 1] += ".md";
  }

  return normalizedSegments.join("/");
}

function headerValue(headers, name) {
  const header = headers && headers[name];
  if (
    !header ||
    typeof header.value !== "string" ||
    (header.multiValue && header.multiValue.length > 1)
  ) {
    return null;
  }
  return header.value;
}

export function resolveMarkdownRoute(request) {
  if (!request || (request.method !== "GET" && request.method !== "HEAD")) {
    return null;
  }

  if (headerValue(request.headers, WAF_FORMAT_HEADER) !== MARKDOWN_FORMAT) {
    return null;
  }
  if (request.uri === "/content" || request.uri.startsWith("/content/")) {
    return null;
  }

  const host = normalizeHost(headerValue(request.headers, "host"));
  const path = normalizePath(request.uri);
  if (host === null || path === null) {
    return null;
  }

  const targetUri = `/content/${host}/${path}`;
  // S3 object keys exclude the leading slash and are limited to 1,024 bytes.
  return targetUri.length <= 1025 ? targetUri : null;
}

export function routeViewerRequest(request, selectOrigin, centralOriginId) {
  const targetUri = resolveMarkdownRoute(request);
  const trustedSignal =
    request &&
    headerValue(request.headers, WAF_FORMAT_HEADER) === MARKDOWN_FORMAT;
  if (trustedSignal) {
    delete request.headers[WAF_FORMAT_HEADER];
  }
  if (
    targetUri === null ||
    typeof selectOrigin !== "function" ||
    typeof centralOriginId !== "string" ||
    centralOriginId.length === 0 ||
    centralOriginId.length > 128 ||
    centralOriginId !== centralOriginId.trim() ||
    centralOriginId.includes("{{") ||
    centralOriginId.includes("}}") ||
    hasControlOrNonAscii(centralOriginId)
  ) {
    return request;
  }

  try {
    selectOrigin(centralOriginId);
    request.uri = targetUri;
  } catch (_error) {
    // A missing/misconfigured origin must preserve the publisher HTML path.
  }

  return request;
}

export function createViewerRequestHandler(cloudfrontApi, centralOriginId) {
  return function viewerRequestHandler(event) {
    const request = event.request;
    function selectOrigin(originId) {
      return cloudfrontApi.selectRequestOriginById(originId);
    }
    return routeViewerRequest(
      request,
      selectOrigin,
      centralOriginId,
    );
  };
}

const deployedHandler = createViewerRequestHandler(cf, CENTRAL_ORIGIN_ID);

export function handler(event) {
  return deployedHandler(event);
}

export const constants = Object.freeze({
  WAF_FORMAT_HEADER,
  MARKDOWN_FORMAT,
});
