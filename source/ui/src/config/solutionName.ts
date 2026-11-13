/**
 * Single source for the solution's display name shown in the UI chrome.
 *
 * The canonical full name lives in the repository's `solution-manifest.yaml`
 * (`name:`). At build time `configure-ui-env.py` derives the UI-facing short
 * form by stripping the `Guidance for ` prefix and ` on AWS` suffix and writes
 * it into `VITE_SOLUTION_NAME`. The UI reads that var here, with a hardcoded
 * fallback so local dev (no generated .env) still renders the correct name.
 *
 * Full manifest name:  "Guidance for Publisher Content Monetization for AI Agents on AWS"
 * UI short form:       "Publisher Content Monetization for AI Agents"
 */

const FALLBACK_SOLUTION_NAME = 'Publisher Content Monetization for AI Agents';

/** The UI-facing solution name (short form), from the manifest-derived env var. */
export const SOLUTION_NAME: string =
  import.meta.env.VITE_SOLUTION_NAME?.trim() || FALLBACK_SOLUTION_NAME;
