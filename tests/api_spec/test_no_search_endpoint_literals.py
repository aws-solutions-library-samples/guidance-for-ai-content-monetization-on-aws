"""/search was deleted; nothing in the shipped source should name it as an endpoint.

Discovery is POST /query. Demo data and path heuristics that still say /search mislead readers
and dashboards even when they do not route anywhere. Tests may still use /search as arbitrary input.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / "source" / "lambda", ROOT / "source" / "ui" / "src"]
SKIP_PARTS = {"python", "node_modules", "build", "dist", "__pycache__"}
SEARCH_ENDPOINT = re.compile(r"""["'`](?:/api)?(?:/content)?/search(?:[/?"'`])""")


def test_no_search_endpoint_literals_in_source():
    hits = []
    for base in SOURCES:
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or SKIP_PARTS & set(path.parts):
                continue
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if SEARCH_ENDPOINT.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:100]}")
    assert not hits, "\n".join(hits)
