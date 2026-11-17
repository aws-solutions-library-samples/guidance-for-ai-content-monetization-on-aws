"""Content-domain port — ``ContentSourcePort`` (**PROPOSED — NOT ratified**).

This module intentionally defines **no** committed interface. Per Requirement 2.8 and
the design's ratification table, ``ContentSourcePort`` is recorded as a *Proposed_Port*:
its shape is described here in prose only, and it is ratified (a concrete
``Protocol``/``ABC`` with committed methods lands) once its first real adapter is built.

Why it is not ratified now
--------------------------
Although a WordPress connector exists in the UI today, the content-ingestion **integration
model is not yet settled**. The originally-envisioned shape is an **S3 export of a specific
JSON format that the GEO pipeline ingests on S3 landing** — i.e. a push/transform onto the
shared content bucket, *not necessarily* a synchronous ``fetch()``-style pull port. Current
WordPress capabilities have not been re-researched, so the transport is genuinely open:

- **S3-export-JSON** — the CMS (or a connector) writes a defined JSON payload to S3; the
  pipeline transforms it on landing. (The originally-envisioned shape.)
- **API-pull** — a port with a ``fetch()``/``list()`` operation that reads from the CMS API.
- **webhook** — the CMS pushes change events to an endpoint that triggers ingestion.

Choosing among these — and therefore the port's method surface — requires that
investigation, which is **deferred to the content vertical (#14)**. Committing an interface
now would be speculative generality (the failure mode Requirement 2.8 and the design
explicitly guard against).

Scope reserved here
-------------------
This spec (#35) reserves the *content* slot in the Connections model (Requirement 5) so the
existing WordPress connector can be re-homed as a content-domain Connection, but it does
**not** commit the ``ContentSourcePort`` interface. The real content-normalize adapter,
``url_hash`` handling, and the ratified port definition are owned by #14.

Proposed shape (illustrative sketch — deliberately commented out, NOT part of the contract)
------------------------------------------------------------------------------------------
The following is a *non-binding* sketch to convey intent only. It is commented out so that
no ratified ``ContentSourcePort`` is importable from this module (task 3.5 asserts this).
Do not uncomment or depend on it; #14 will decide the actual surface after the transport
investigation above.

    # class ContentSourcePort(Protocol):
    #     # Transport UNSETTLED — one of S3-export-JSON / API-pull / webhook (see above).
    #     # Method surface below is illustrative only and NOT ratified.
    #     def test(self) -> TestResult: ...       # safe, read-only connectivity/auth check
    #     def status(self) -> HealthStatus: ...   # ongoing health of an active Connection
    #     # e.g. an API-pull framing might add:  def fetch(self, ref: str) -> ContentMetadata: ...
    #     # e.g. an S3-export framing might instead be a push/transform on landing (no pull method)
"""

from __future__ import annotations
