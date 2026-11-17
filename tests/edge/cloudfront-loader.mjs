const stubUrl = new URL("./cloudfront-stub.mjs", import.meta.url).href;

export async function resolve(specifier, context, nextResolve) {
  if (specifier === "cloudfront") {
    return { url: stubUrl, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
