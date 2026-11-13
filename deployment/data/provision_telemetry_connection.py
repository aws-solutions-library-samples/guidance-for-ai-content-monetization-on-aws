#!/usr/bin/env python3
"""Provision the canonical telemetry Connection so a fresh deployment bills partner reads.

GitLab #58. ``emit_usage_event`` writes only through the **active** ``telemetry`` Connection in
``cm-connections``; with no such record the emitter logs ``telemetry_not_wired`` and returns, so
every billable read is silently dropped. Nothing in the deploy path created that record -- the
only writer was the Connections UI -- so a fresh deployment never metered anything.

This script closes that gap as part of deployment. It deliberately does NOT change the "no active
Connection means not wired" contract in ``.kiro/specs/integration-adapters/design.md`` (Req 6.6):
the Registry still refuses to invent a sink. Instead the deployment now *provisions* the
Connection that the contract expects to exist, which keeps the fail-closed semantics intact while
making the canonical raw sink reachable.

Idempotent and non-destructive:

* Creates the record only when absent.
* If a ``telemetry`` Connection already exists it is LEFT ALONE -- including an operator-authored
  one pointing at a different adapter, or one deliberately deactivated. Re-running a deployment
  must not resurrect a sink an operator switched off, and must not retarget their choice.
* Writes no secret. ``dynamo_usage_events`` authenticates with the execution role's IAM, so
  ``secret_ref`` stays empty (which the Registry now handles -- see ``integrations/registry.py``).

Run:
    AWS_PROFILE=<your-profile> AWS_DEFAULT_REGION=us-west-2 \
        python deployment/data/provision_telemetry_connection.py [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

CONNECTIONS_TABLE = os.environ.get("CONNECTIONS_TABLE_NAME", "cm-connections")
USAGE_EVENTS_TABLE = os.environ.get("USAGE_EVENTS_TABLE_NAME", "cm-usage-events")

DOMAIN = "telemetry"
ADAPTER = "dynamo_usage_events"
#: Brand-agnostic (single-brand) Connection: the Registry resolves ``brand=None`` for callers that
#: pass no brand, which is what ``emit_usage_event`` does today.
CONNECTION_ID = f"{DOMAIN}#{ADAPTER}#default"

#: The only secret prefix the emitting roles can read (see the grants on ``partner_api_role`` /
#: ``ui_backend_role`` in ``infrastructure/content_monetization_stack.py``). A ``secret_ref``
#: outside it resolves -- it is non-empty, so the Registry tries to read it -- and then fails
#: AccessDenied, which the Registry treats as NOT wired. The event is dropped silently.
#:
#: The ``allowlist secret`` pragma suppresses a detect-secrets false positive: the value is a
#: Secrets Manager NAME PREFIX used to classify where a secret lives, not a credential. The
#: scanner keys on the identifier containing "SECRET"; the string itself carries no entropy and
#: grants nothing. Suppressed deliberately rather than renamed, because the name states what the
#: constant is for.
READABLE_SECRET_PREFIX = "cm-connections/"  # pragma: allowlist secret


def _secret_ref_is_unreadable(secret_ref: str) -> bool:
    """Whether a non-empty ``secret_ref`` sits outside the readable prefix.

    An empty ``secret_ref`` is fine -- the Registry skips resolution entirely for a
    credential-less adapter. The dangerous case is a ref that IS set but points somewhere the
    emitting roles have no grant for: pre-#58 deployments were configured by hand with secrets
    named ``iab-llm-*``, matching the old (wrong) grant scope.
    """
    if not secret_ref:
        return False
    name = secret_ref.split(":secret:")[-1] if ":secret:" in secret_ref else secret_ref
    return not name.startswith(READABLE_SECRET_PREFIX)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _existing_telemetry_connections(table) -> list[dict]:
    """Every record in the telemetry domain, active or not."""
    return [
        item
        for item in table.scan().get("Items", [])
        if item.get("domain") == DOMAIN
    ]


def provision(dry_run: bool = False) -> int:
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(CONNECTIONS_TABLE)

    try:
        existing = _existing_telemetry_connections(table)
    except ClientError as exc:
        print(f"ERROR: could not read {CONNECTIONS_TABLE}: {exc}", file=sys.stderr)
        return 1

    if existing:
        # An operator's configuration wins. Report what is there so a deployment log explains why
        # nothing was written, rather than looking like a silent no-op.
        unreadable = False
        for item in existing:
            state = "active" if item.get("active") else "INACTIVE"
            print(
                f"telemetry Connection already present: {item.get('connection_id')} "
                f"-> {item.get('adapter')} ({state}); leaving it unchanged"
            )
            if not item.get("active"):
                print(
                    "  NOTE: it is inactive, so usage events are NOT being written. That is a "
                    "deliberate operator state and this script will not override it."
                )
            elif item.get("adapter") != ADAPTER:
                print(
                    f"  NOTE: it targets '{item.get('adapter')}', not the canonical raw sink "
                    f"'{ADAPTER}'. Raw billable truth may not be landing in {USAGE_EVENTS_TABLE}."
                )
            if _secret_ref_is_unreadable(item.get("secret_ref", "")):
                unreadable = True
                print(
                    f"  WARNING: its secret_ref is {item.get('secret_ref')!r}, which is OUTSIDE "
                    f"'{READABLE_SECRET_PREFIX}' -- the only prefix the emitting roles can read."
                )
                print(
                    "  This Connection will FAIL CLOSED and no usage event will be written. It "
                    "predates GitLab #58 and was almost certainly created by hand as a workaround."
                )
                print(
                    f"  {ADAPTER} needs NO credential, so the fix is to clear the field:\n"
                    f"    aws dynamodb update-item --table-name {CONNECTIONS_TABLE} \\\n"
                    f"      --key '{{\"connection_id\":{{\"S\":\"{item.get('connection_id')}\"}}}}' \\\n"
                    "      --update-expression 'SET secret_ref = :empty' \\\n"
                    "      --expression-attribute-values '{\":empty\":{\"S\":\"\"}}'"
                )
        if unreadable:
            # Non-zero so a deploy surfaces it rather than scrolling past. The condition is
            # actionable and leaves metering silently broken if ignored.
            return 2
        return 0

    record = {
        "connection_id": CONNECTION_ID,
        "domain": DOMAIN,
        "adapter": ADAPTER,
        # brand is omitted, not set to None: the Connection model drops None on write, and the
        # Registry matches `record.brand == brand` with brand=None for the single-brand case.
        "config": {"table_name": USAGE_EVENTS_TABLE},
        "secret_ref": "",  # IAM-authenticated sink; no credential to store
        "active": True,
        "created_at": _now(),
        "updated_at": _now(),
    }

    if dry_run:
        print(f"[dry-run] would create {CONNECTION_ID} -> {ADAPTER} (table {USAGE_EVENTS_TABLE})")
        return 0

    try:
        # Conditional write: if a concurrent deploy created it first, that is success, not failure.
        table.put_item(
            Item=record,
            ConditionExpression="attribute_not_exists(connection_id)",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            print(f"telemetry Connection {CONNECTION_ID} already created concurrently; no change")
            return 0
        print(f"ERROR: could not write the telemetry Connection: {exc}", file=sys.stderr)
        return 1

    print(f"Created telemetry Connection {CONNECTION_ID} -> {ADAPTER} ({USAGE_EVENTS_TABLE})")
    print("Partner reads will now record billable usage events.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing",
    )
    args = parser.parse_args()
    return provision(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
