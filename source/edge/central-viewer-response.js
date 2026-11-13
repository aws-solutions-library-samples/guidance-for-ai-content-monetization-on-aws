const LICENSE_METADATA_HEADER = "x-amz-meta-license-url";
const S3_METADATA_PREFIX = "x-amz-meta-";
const MAX_LICENSE_URL_LENGTH = 2048;

function normalizeHttpsUrl(value) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > MAX_LICENSE_URL_LENGTH ||
    value !== value.trim() ||
    /[\x00-\x20\x7f<>"\\]/.test(value) ||
    !value.startsWith("https://") ||
    value.includes("#")
  ) {
    return null;
  }

  const match = value.match(
    /^https:\/\/([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::([0-9]{1,5}))?(\/[^\s<>"]*)?$/,
  );
  if (!match) {
    return null;
  }

  const host = match[1].toLowerCase();
  const labels = host.split(".");
  if (labels.length < 2 || host.length > 253 || /^\d+\.\d+\.\d+\.\d+$/.test(host)) {
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

  if (match[2]) {
    const port = Number.parseInt(match[2], 10);
    if (port < 1 || port > 65535) {
      return null;
    }
  }

  return `https://${host}${match[2] ? `:${match[2]}` : ""}${match[3] || ""}`;
}

function normalizeLicenseUrl(value, request) {
  const absolute = normalizeHttpsUrl(value);
  if (absolute !== null) {
    return absolute;
  }
  if (
    typeof value !== "string" ||
    !value.startsWith("/licenses/") ||
    value.includes("//") ||
    value.includes("\\") ||
    value.includes("#") ||
    /[\x00-\x20\x7f<>"\\]/.test(value)
  ) {
    return null;
  }
  const hostHeader = request && request.headers && request.headers.host;
  const host =
    hostHeader && typeof hostHeader.value === "string"
      ? hostHeader.value.toLowerCase()
      : "";
  return normalizeHttpsUrl(`https://${host}${value}`);
}

function appendLicenseLink(headers, licenseUrl) {
  const linkValue = `<${licenseUrl}>; rel="license"`;
  const existing = headers.link;

  if (existing && typeof existing.value === "string" && existing.value.length > 0) {
    const existingLinks = existing.value.split(",");
    let duplicate = false;
    for (let index = 0; index < existingLinks.length; index += 1) {
      if (existingLinks[index].trim() === linkValue) {
        duplicate = true;
        break;
      }
    }
    if (!duplicate) {
      existing.value = `${existing.value}, ${linkValue}`;
    }
    return;
  }

  headers.link = { value: linkValue };
}

export function handleViewerResponse(event) {
  const response = event.response;
  const headers = response.headers || {};
  response.headers = headers;

  const licenseMetadata = headers[LICENSE_METADATA_HEADER];
  const licenseUrl = normalizeLicenseUrl(
    licenseMetadata && typeof licenseMetadata.value === "string"
      ? licenseMetadata.value
      : null,
    event.request,
  );

  if (licenseUrl !== null) {
    appendLicenseLink(headers, licenseUrl);
  }

  const headerNames = Object.keys(headers);
  for (let index = 0; index < headerNames.length; index += 1) {
    const name = headerNames[index];
    if (name.startsWith(S3_METADATA_PREFIX)) {
      delete headers[name];
    }
  }

  return response;
}

export function handler(event) {
  return handleViewerResponse(event);
}
