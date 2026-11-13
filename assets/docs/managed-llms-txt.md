# Managed llms.txt: verification

The publisher manages `llms.txt` through the Cognito-authenticated
`/content/llms-txt*` operations. A generation run builds a draft from the
published catalog; a person reviews it, optionally edits it, and publishes it.
Nothing is generated or published automatically on deployment.

The managed Access section lists only `GET /info` and `POST /query` (renderer revision
`partner-api-access-v2`). A renderer-revision change produces a new draft even when the catalog
is unchanged. A published change reaches the public bytes within the 60-second stage cache.

## Automated checks

`tests/integration/test_llms_txt_lifecycle.py` runs against a deployed stack.

| Tier | Gate | Covers |
| --- | --- | --- |
| Read-only | `RUN_INTEGRATION=1` | Public `GET /llms.txt` returns identical Markdown on the internal and partner planes; all five management operations return `401` without a token |
| Lifecycle | also `IAB_LLMS_TXT_LIFECYCLE=1`, `COGNITO_USERNAME`, `COGNITO_PASSWORD` | Singleton lock, stale-lock reclaim, a new draft after a renderer-revision change on an unchanged catalog, `no_change` rerun, `If-Match` edits (`412` when stale), publish and idempotent republish, public bytes converging within the 60-second stage cache |

The lifecycle tier calls Bedrock and publishes the draft it generates (with its
content unchanged), so run it only when you intend to publish. Its two lock
tests write a synthetic run and the control record's active-run fields directly
in the jobs table, conditional on no real run being active, and remove what
they wrote.

```bash
RUN_INTEGRATION=1 IAB_LLMS_TXT_LIFECYCLE=1 AWS_REGION=us-west-2 \
COGNITO_USERNAME=<pool user> COGNITO_PASSWORD=<password> \
  .venv/bin/python -m pytest tests/integration/test_llms_txt_lifecycle.py -v -p no:cacheprovider
```

## Manual check: worker crash and DLQ reconciliation

A real worker crash cannot be forced safely from a test. Verify it by hand in a
development account when the worker or its failure handling changes.

1. Confirm no run is active: `GET /content/llms-txt` returns `"active_run": null`.
2. Make every worker invocation fail before it claims the run: remove the
   worker function's `CONTENT_TABLE_NAME` environment variable. The DLQ
   reconciler needs only `JOBS_TABLE_NAME`, so it keeps working.
3. Start a run with `POST /content/llms-txt/runs` and note the `run_id`.
4. Wait for the asynchronous retries to finish. The function retries twice;
   the failed event then lands on the `cm-llms-txt-worker-dlq` queue, whose
   messages the same worker consumes.
5. Check that `GET /content/llms-txt/runs/{run_id}` reports `status: failed`,
   and that `GET /content/llms-txt` reports no active run, so a new run can
   start immediately.
6. Restore `CONTENT_TABLE_NAME` and start a run to confirm generation works.
