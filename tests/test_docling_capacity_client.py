# ALLOW-NEW-TEST-FILE: not tests/test_docling_capacity.py because it tests the service
# That file covers the docling-service HTTP surface (7.1-7.3); this covers the worker's
# remote client (7.4) -- a different module (client/remote.py) with its own
# fixture shape (a bare fake client, no FastAPI/TestClient).
"""RFC-052 P3, task 7.4: worker-side ``/capacity`` snapshot + build-skew check.

- The worker logs one structured ``/capacity`` snapshot per conversion job
  (design "P3 use (one active remote)"), best-effort: never raises, never
  meaningfully delays the conversion.
- Build-skew WARNING (R5 AC8) compares the worker's own ``BUILD_SHA`` against
  the backend's, on the shorter side's prefix (a 40-hex CI SHA vs a 12-hex
  ``install.sh --short=12`` SHA sharing that prefix must NOT warn), deduped
  per (backend, service_sha) per process.
"""

from __future__ import annotations

import itertools
import logging
from unittest.mock import patch

import pytest

from pageindex_mcp.client import remote as remote_module
from pageindex_mcp.client.remote import _build_sha_mismatch, _log_capacity_snapshot


class _FakeResponse:
    def __init__(self, data: dict, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeCapacityClient:
    """A bare stand-in for httpx.AsyncClient exposing only ``get``."""

    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []

    async def get(self, url, *, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        if self._exc is not None:
            raise self._exc
        return self._response


_SNAPSHOT = {
    "backend": "docling-1",
    "build_sha": "abc123",
    "effective_cpus": 16.0,
    "total_mem_bytes": 30_000_000_000,
    "free_mem_bytes": 12_000_000_000,
    "reserve_bytes": 4_294_967_296,
    "chunk_pages": 11,
    "per_proc_peak_bytes": 1_504_000_000,
    "safe_procs": 5,
    "busy_slots": 1,
    "max_slots": 5,
    "spp_ewma": 19.2,
    "spp_samples": 27,
}


@pytest.fixture(autouse=True)
def _reset_capacity_skew_state():
    """The dedup set is process-global by design; isolate tests from it."""
    remote_module._capacity_skew_warned.clear()
    yield
    remote_module._capacity_skew_warned.clear()


class TestBuildShaMismatchPrefixRule:
    @pytest.mark.parametrize(
        "local_sha, remote_sha, expected_mismatch",
        [
            # 40-hex CI SHA vs a 12-hex `install.sh --short=12` SHA sharing
            # that prefix: NOT a mismatch.
            ("abcdef123456" + "7890" * 7, "abcdef123456", False),
            # Genuinely different SHAs, same lengths.
            ("abcdef123456", "111111111111", True),
            # Either side "unknown" (or empty -- same falsy branch) is a
            # mismatch per the contract.
            ("unknown", "abcdef123456", True),
        ],
    )
    def test_prefix_comparison(self, local_sha, remote_sha, expected_mismatch):
        assert _build_sha_mismatch(local_sha, remote_sha) is expected_mismatch
        # F1 (repair cycle 1): a degenerate/too-short SHA must never suppress
        # the warning by accidentally sharing a trivial short prefix -- the
        # shorter side must be at least 7 chars (git's default abbreviation
        # length) to be compared at all. Asserted unconditionally here (not a
        # new parametrize case) to stay within the test-budget cap.
        assert _build_sha_mismatch("a", "abcdef1234567890") is True
        assert _build_sha_mismatch("abcdef1234567890", "a") is True


class TestLogCapacitySnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_logged_as_one_decision_record_with_no_document_content(self, caplog):
        """The whole snapshot is one INFO decision record (HR3: capacity
        numbers only, never document content), carrying exactly the fields
        the design calls out."""
        client = _FakeCapacityClient(_FakeResponse(_SNAPSHOT))
        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch.object(remote_module, "_CLIENT_BUILD_SHA", "abc123"),
            caplog.at_level(logging.INFO, logger="pageindex_mcp.obs"),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            await _log_capacity_snapshot(client, {"Authorization": "Bearer tok"})

        decisions = [
            r for r in caplog.records if getattr(r, "event", None) == "docling_capacity_snapshot"
        ]
        assert len(decisions) == 1
        attrs = decisions[0].attrs
        assert attrs == {
            "backend": "docling-1",
            "build_sha": "abc123",
            "effective_cpus": 16.0,
            "free_mem_bytes": 12_000_000_000,
            "safe_procs": 5,
            "busy_slots": 1,
            "max_slots": 5,
            "spp_ewma": 19.2,
            "spp_sample_count": 27,
        }
        # Same bearer token as /convert, GET (never POST-ing a document).
        assert client.calls[0]["url"] == "http://docling:8080/capacity"
        assert client.calls[0]["headers"] == {"Authorization": "Bearer tok"}

    @pytest.mark.asyncio
    async def test_build_skew_warns_once_per_backend_sha_pair_not_per_job(self, caplog):
        """Two jobs against the same skewed backend/sha warn only once."""
        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch.object(remote_module, "_CLIENT_BUILD_SHA", "local-sha"),
            caplog.at_level(logging.WARNING, logger="pageindex_mcp.client.remote"),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            client_1 = _FakeCapacityClient(_FakeResponse(_SNAPSHOT))  # build_sha "abc123"
            client_2 = _FakeCapacityClient(_FakeResponse(_SNAPSHOT))
            await _log_capacity_snapshot(client_1, {})
            await _log_capacity_snapshot(client_2, {})

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "docling-1" in warnings[0].message
        assert "abc123" in warnings[0].message
        assert "local-sha" in warnings[0].message

    @pytest.mark.asyncio
    async def test_never_raises_on_capacity_failure(self, caplog):
        """An older service (404), a timeout, or any network error is
        best-effort: swallowed, logged quietly, never raised, no WARNING."""
        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            caplog.at_level(logging.WARNING),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            client = _FakeCapacityClient(exc=RuntimeError("connection refused"))
            await _log_capacity_snapshot(client, {})  # must not raise

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)


# ── RFC-052 P5: split coordinator (client/split.py) ───────────────────────────


def _cap(safe=4, spp=20.0, busy=0, max_slots=1, sha="abcdef123456", chunk=10):
    from pageindex_mcp.client.split import Capacity

    return Capacity(sha, safe, busy, max_slots, spp, chunk)


@pytest.fixture
def split_env(monkeypatch):
    """Split on, two stub backends, fast poll, decisions captured.

    ``env.caps[name]`` is the /capacity answer (``None`` = refused, a list =
    one answer per probe, the last repeating); ``env.speed[name]`` is seconds
    per page; ``env.fail`` maps ``(name, page_start)`` to an exception raised
    once per listed entry.
    """
    import asyncio
    import dataclasses
    import types

    from pageindex_mcp.client import split as split_module

    settings = dataclasses.replace(
        remote_module.settings,
        docling_split_enabled=True,
        docling_split_backends="mac=http://mac:8090,node=http://node:8080",
        docling_split_pii_backends="node",
        docling_split_min_pages=20,
        docling_split_initial_frac=0.8,
        docling_expected_build_sha="abcdef1",
        docling_service_url="http://docling-active:8090",
        pii_corpus=False,
    )
    monkeypatch.setattr(split_module, "settings", settings)
    monkeypatch.setattr(remote_module, "settings", settings)
    monkeypatch.setattr(split_module, "_BUSY_POLL_S", 0.005)
    monkeypatch.setattr(remote_module, "_effective_read_timeout_s", lambda: 30.0)
    monkeypatch.setattr(remote_module, "_MIN_USEFUL_CALL_S", 0.0)
    split_module._absent_until.clear()

    env = types.SimpleNamespace(
        caps={"mac": _cap(safe=12, spp=20.0), "node": _cap(safe=4, spp=40.0)},
        speed={"mac": 0.0001, "node": 0.0001},
        fail={},
        calls=[],
        decisions=[],
        settings=settings,
    )
    probes: dict[int, int] = {}

    async def fake_capacity(client, backend):
        answer = env.caps.get(backend.name)
        if isinstance(answer, list):
            # Keyed by the list itself: assigning a new list restarts it.
            i = probes.get(id(answer), 0)
            probes[id(answer)] = i + 1
            answer = answer[min(i, len(answer) - 1)]
        return answer

    async def fake_convert(staging_key, **kw):
        name = {"http://mac:8090": "mac", "http://node:8080": "node"}.get(kw.get("base_url"))
        start, end = kw.get("page_start"), kw.get("page_end")
        env.calls.append({"backend": name, **kw})
        lo, hi = (0, kw["page_count"] - 1) if start is None else (start, end)
        await asyncio.sleep(env.speed.get(name, 0.0) * (hi - lo + 1))
        pending = env.fail.get((name, lo))
        if pending:
            raise pending.pop(0)
        return remote_module.RemoteConvertResult(
            markdown=f"# p{lo}-{hi}", pictures=[{"page": lo + 1}], page_start=lo
        )

    monkeypatch.setattr(split_module, "_fetch_capacity", fake_capacity)
    monkeypatch.setattr(remote_module, "_remote_pdf_convert", fake_convert)

    async def no_version_check(client):
        env.version_checks += 1

    env.version_checks = 0
    monkeypatch.setattr(remote_module, "_check_remote_docling_version", no_version_check)

    async def no_backend_state():
        return {"target": "mac"}

    monkeypatch.setattr("pageindex_mcp.cache.get_docling_backend_state", no_backend_state)
    monkeypatch.setattr(
        split_module,
        "decision",
        lambda **kw: env.decisions.append((kw["event"], kw["choice"], kw.get("attrs") or {})),
    )
    return env


def _run_split(env, page_count=300):
    import asyncio

    from pageindex_mcp.client.split import split_convert

    return asyncio.run(
        split_convert(
            "key",
            page_count=page_count,
            page_classes=None,
            convert_kwargs={"force_full_page_ocr": False},
        )
    )


def _outcome(env):
    return [(c, a) for e, c, a in env.decisions if e == "docling_split"][-1]


class TestSplitCoordinator:
    """RFC-052 P5 tasks 11.2-11.4 (R5 AC4, AC6, AC7, AC8, AC10; R6 AC2)."""

    def test_hr3_eligibility_table_and_build_match(self, split_env):
        """11.3 / R5 AC7: the Mac never sees a PII document; docling-1 may."""
        from pageindex_mcp.client.split import build_matches, configured_backends, hr3_eligible

        svc = "http://docling-service:8080"
        public = "https://docling.example.com"
        table = [
            # name, pii_corpus, DOCLING_SPLIT_PII_BACKENDS, url, /capacity identity, eligible
            ("mac", False, "node", public, "mac", True),
            ("node", False, "node", svc, "", True),
            ("mac", True, "node", svc, "", False),
            ("mac", True, "mac,node", svc, "", False),  # config cannot re-enable the Mac
            ("Mac", True, "Mac,node", svc, "", False),  # nor can a case variant
            ("gpu", True, "gpu", svc, "mac", False),  # the Mac under another alias
            ("node", True, "node", svc, "docling-1", True),
            ("node", True, "node", "http://docling-service.pageindex-mcp.svc:8080", "", True),
            ("node", True, "node", public, "", False),  # not a cluster Service
            ("node", True, "node", "http://100.106.50.6:8090", "", False),  # Tailscale IP
            ("node", True, "", svc, "", False),
            ("other", True, "node", svc, "", False),
        ]
        for name, pii, allowed, url, reported, want in table:
            got = hr3_eligible(
                name, pii_corpus=pii, url=url, reported=reported, pii_backends=allowed
            )
            assert got is want, (name, pii, url, reported)

        assert build_matches("16b3690d6672", "16b3690d6672aaaabbbbccccddddeeeeffff0000")
        assert not build_matches("16b3690d6672", "44d5c132f7fe")
        assert not build_matches("unknown", "unknown")
        assert not build_matches("abc", "abc")  # under 7 chars is not comparable
        assert [b.name for b in configured_backends("mac=http://m:1, bad ,node=http://n:2/")] == [
            "mac",
            "node",
        ]

        # End to end: PII -> the Mac is excluded and the whole document goes,
        # unsliced, to docling-1 with the per-backend gate already passed.
        import dataclasses

        from pageindex_mcp.client import split as split_module

        pii = dataclasses.replace(split_env.settings, pii_corpus=True)
        split_module.settings = pii
        remote_module.settings = pii
        res = _run_split(split_env)
        assert res.markdown == "# p0-299"
        (call,) = split_env.calls
        assert call["backend"] == "node" and call["hr3_checked"] is True
        assert call.get("page_start") is None
        choices = {
            a["backend"]: c for e, c, a in split_env.decisions if e == "docling_split_backend"
        }
        assert choices == {"mac": "hr3_blocked", "node": "eligible"}
        assert _outcome(split_env)[0] == "single_backend"

    def test_plan_partitions_the_document_on_chunk_boundaries(self):
        """Property P1 (coverage) plus the proportional initial split (R5 AC4)."""
        from hypothesis import given, settings
        from hypothesis import strategies as st

        from pageindex_mcp.client.split import join_heading_shifts, plan_split

        # Worked example: mac rate 0.6, node 0.1 -> 80% of 300 split 6:1.
        chunks = [(s, s + 9) for s in range(0, 300, 10)]
        caps = {"mac": _cap(safe=12, spp=20.0), "node": _cap(safe=4, spp=40.0)}
        initial, tail = plan_split(chunks, caps, initial_frac=0.8)
        assert (initial["mac"].start, initial["mac"].end) == (0, 209)
        assert (initial["node"].start, initial["node"].end) == (210, 239)
        assert [(t.start, t.end) for t in tail] == [(240, 279), (280, 299)]

        @settings(max_examples=150, deadline=None)
        @given(
            sizes=st.lists(st.integers(1, 60), min_size=1, max_size=40),
            procs=st.lists(st.integers(1, 16), min_size=1, max_size=3),
            spps=st.lists(st.floats(1.0, 100.0), min_size=3, max_size=3),
            frac=st.floats(0.0, 1.0),
        )
        def prop(sizes, procs, spps, frac):
            bounds, s = [], 0
            for n in sizes:
                bounds.append((s, s + n - 1))
                s += n
            caps = {f"b{i}": _cap(safe=p, spp=spps[i]) for i, p in enumerate(procs)}
            initial, tail = plan_split(bounds, caps, initial_frac=frac)
            shards = sorted([*initial.values(), *tail], key=lambda x: x.start)
            assert shards[0].start == 0 and shards[-1].end == s - 1
            for a, b in itertools.pairwise(shards):
                assert b.start == a.end + 1
            starts = {lo for lo, _ in bounds}
            ends = {hi for _, hi in bounds}
            assert all(x.start in starts and x.end in ends for x in shards)
            assert sorted(x.index for x in shards) == list(range(len(shards)))
            fastest = min(caps, key=lambda n: (-caps[n].rate, n))  # ties: by name
            assert initial[fastest].start == 0
            per_tail = min(procs)
            for t in tail:
                assert sum(1 for lo, _ in bounds if t.start <= lo <= t.end) <= per_tail

        prop()

        # R6 AC2: a join that opens two levels deeper is a counted shift.
        assert join_heading_shifts(["# A\n## B", "#### C", "", "## D"]) == 1
        assert join_heading_shifts(["# A", "## B", "# C"]) == 0

    def test_stub_backends_steal_reroute_and_share_the_deadline(self, split_env):
        """11.4: tail stealing, busy backends, retry/re-route, the second
        failure, and the shared deadline, against stub backends."""
        import httpx

        from pageindex_mcp.client.remote import DoclingUnavailable

        # 1. Stealing: docling-1 is 100x slower, so the Mac takes every tail
        #    shard after its own block; the merge is complete and in order.
        split_env.speed = {"mac": 0.0001, "node": 0.01}
        res = _run_split(split_env)
        assert res.markdown == "# p0-209\n\n# p210-239\n\n# p240-279\n\n# p280-299"
        assert [p["page"] for p in res.pictures] == [1, 211, 241, 281]
        assert [c["backend"] for c in split_env.calls if c["page_start"] >= 240] == ["mac", "mac"]
        assert {c["shard"] for c in split_env.calls} == {
            "1/4:0-209",
            "2/4:210-239",
            "3/4:240-279",
            "4/4:280-299",
        }
        choice, attrs = _outcome(split_env)
        assert choice == "split" and attrs["pages_by_backend"] == "mac:270,node:30"
        # Every shard's read timeout comes out of the one shared budget.
        assert all(0 < c["read_timeout_s"] <= 30.0 for c in split_env.calls)

        # 2. Busy: docling-1 reports a full slot on the per-shard check, so no
        #    tail shard is queued behind it (R5 AC6), even though it is fast.
        split_env.calls.clear()
        split_env.speed = {"mac": 0.002, "node": 0.0}
        split_env.caps["node"] = [_cap(safe=4, spp=40.0), _cap(safe=4, spp=40.0, busy=1)]
        _run_split(split_env)
        assert [c["backend"] for c in split_env.calls if c["page_start"] >= 240] == ["mac", "mac"]

        # 2b. A backend that rolled to another build between probes leaves the
        #     split: no tail shard of this document comes from the new build.
        split_env.calls.clear()
        split_env.caps["node"] = [_cap(safe=4, spp=40.0), _cap(safe=4, spp=40.0, sha="0000000aaaa")]
        _run_split(split_env)
        assert [c["backend"] for c in split_env.calls if c["page_start"] >= 240] == ["mac", "mac"]

        # 2c. A retry stays off the backend it failed on, even while the
        #     other backend is briefly busy: the Mac's first per-shard probe
        #     says busy, docling-1 is free, and the retry still waits for the Mac.
        split_env.calls.clear()
        split_env.speed = {"mac": 0.002, "node": 0.0}
        split_env.caps = {
            "mac": [_cap(safe=12, spp=20.0), _cap(safe=12, spp=20.0, busy=1), _cap(safe=12)],
            "node": _cap(safe=4, spp=40.0),
        }
        split_env.fail = {("node", 210): [httpx.ReadTimeout("slow")]}
        _run_split(split_env)
        assert [c["backend"] for c in split_env.calls if c["page_start"] == 210] == ["node", "mac"]
        assert split_env.version_checks > 0  # the pipeline-version gate still runs

        # 3. Re-route: docling-1's block times out once and is retried on the Mac.
        split_env.calls.clear()
        split_env.speed = {"mac": 0.0, "node": 0.0}
        split_env.caps["node"] = _cap(safe=4, spp=40.0)
        split_env.fail = {("node", 210): [httpx.ReadTimeout("slow")]}
        res = _run_split(split_env)
        assert "# p210-239" in res.markdown
        assert [c["backend"] for c in split_env.calls if c["page_start"] == 210] == ["node", "mac"]
        choice, attrs = _outcome(split_env)
        assert (choice, attrs["retries"], attrs["reroutes"]) == ("split", 1, 1)

        # 4. A second failure fails the conversion with the shard's own error.
        split_env.calls.clear()
        split_env.fail = {
            ("node", 210): [httpx.ReadTimeout("slow")],
            ("mac", 210): [httpx.ReadTimeout("slow")],
        }
        with pytest.raises(httpx.ReadTimeout):
            _run_split(split_env)
        choice, attrs = _outcome(split_env)
        assert (choice, attrs["error_class"]) == ("failed", "ReadTimeout")

        # 5. Shared deadline: the blocks use up the budget, so the next shard
        #    is refused with DoclingUnavailable instead of being dispatched.
        split_env.calls.clear()
        split_env.fail = {}
        # Both blocks end with < 0.25 s of the 0.3 s budget left (mac 0.21 s,
        # node 0.09 s), so neither may steal.
        split_env.speed = {"mac": 0.001, "node": 0.003}
        remote_module._effective_read_timeout_s = lambda: 0.3
        remote_module._MIN_USEFUL_CALL_S = 0.25
        with pytest.raises(DoclingUnavailable):
            _run_split(split_env)
        assert not any(c["page_start"] >= 240 for c in split_env.calls)

    def test_kill_switch_fallback_and_build_skew(self, split_env):
        """R5 AC10 / property P5, R5 AC8."""
        from pageindex_mcp.client import split as split_module

        # Below the size floor, or no eligible backend: today's path (None).
        assert _run_split(split_env, page_count=19) is None
        split_env.caps = {"mac": None, "node": _cap(safe=0)}
        assert _run_split(split_env) is None
        assert _outcome(split_env)[0] == "fallback_active"
        assert split_env.calls == []

        # Build skew: the Mac's build is not the expected one, so it is
        # excluded and docling-1 converts the whole document unsliced.
        split_env.caps = {"mac": _cap(sha="44d5c132f7fe"), "node": _cap()}
        res = _run_split(split_env)
        assert res.markdown == "# p0-299"
        assert [(c["backend"], c.get("page_start")) for c in split_env.calls] == [("node", None)]

        # Kill switch: DOCLING_SPLIT_ENABLED=0 never consults a backend.
        import dataclasses

        off = dataclasses.replace(split_env.settings, docling_split_enabled=False)
        split_module.settings = off
        split_env.calls.clear()
        assert _run_split(split_env) is None
        assert split_env.calls == []
