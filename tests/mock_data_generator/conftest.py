"""Deterministic seeding for the mock_data_generator suite (GitLab #11).

Several tests here assert on *statistical* properties of RANDOMLY generated data (e.g. "at least 50%
of access-log response codes are 2xx", timestamp spreads, category counts). The generator uses the
stdlib ``random`` module with no fixed seed, so those assertions are flaky — they pass in isolation
but occasionally fail in a full run when the unseeded distribution drifts past a threshold (this is
what intermittently broke CI). Seeding ``random`` before each test makes the generated data — and
therefore the assertions — deterministic and reliable for gating, without weakening any assertion.
"""

import random

import pytest


@pytest.fixture(autouse=True)
def _seed_random():
    """Seed the stdlib RNG before every test so random-data assertions are deterministic."""
    random.seed(1337)
    yield
