"""Heuristic registry: track compensating heuristics with expiry and metrics.

RFC-041 D5 — observability scaffolding.  Wraps existing compensating paths
with RFC origin, creation date, expiry date, graduation criteria, and a
Prometheus counter so that temporary fixes become visible and retire-able.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from prometheus_client import Counter, Gauge

logger = logging.getLogger(__name__)

_HEURISTIC_FIRE_COUNTER = Counter(
    "pageindex_heuristic_fire_total",
    "Times a registered compensating heuristic fired",
    ["heuristic"],
)

_HEURISTIC_EXPIRED_GAUGE = Gauge(
    "pageindex_heuristic_expired",
    "1 when a registered heuristic is past its expiry date, 0 otherwise",
    ["heuristic"],
)


@dataclass(frozen=True)
class HeuristicEntry:
    """Metadata for a registered compensating heuristic."""

    name: str
    rfc_origin: str
    created: date
    expiry: date
    graduation_criteria: str = ""


_DEFAULT_EXPIRY_DAYS = 90


class HeuristicRegistry:
    """Registry for compensating heuristics with expiry tracking and metrics."""

    def __init__(self) -> None:
        self._entries: dict[str, HeuristicEntry] = {}

    def register(
        self,
        name: str,
        rfc_origin: str,
        *,
        created: date | None = None,
        expiry: date | None = None,
        graduation_criteria: str = "",
    ) -> HeuristicEntry:
        created = created or date.today()
        expiry = expiry or (created + timedelta(days=_DEFAULT_EXPIRY_DAYS))
        entry = HeuristicEntry(
            name=name,
            rfc_origin=rfc_origin,
            created=created,
            expiry=expiry,
            graduation_criteria=graduation_criteria,
        )
        self._entries[name] = entry
        _HEURISTIC_EXPIRED_GAUGE.labels(heuristic=name).set(
            1.0 if self.is_expired(name) else 0.0
        )
        return entry

    def fire(self, name: str, *, ref_date: date | None = None) -> None:
        if name not in self._entries:
            logger.warning("heuristic_registry: fire called for unregistered heuristic %r", name)
            return
        _HEURISTIC_FIRE_COUNTER.labels(heuristic=name).inc()
        if self.is_expired(name, ref_date=ref_date):
            logger.warning(
                "heuristic_registry: expired heuristic %r fired (expiry=%s, rfc=%s)",
                name,
                self._entries[name].expiry,
                self._entries[name].rfc_origin,
            )
            _HEURISTIC_EXPIRED_GAUGE.labels(heuristic=name).set(1.0)

    def is_expired(self, name: str, *, ref_date: date | None = None) -> bool:
        entry = self._entries.get(name)
        if entry is None:
            return False
        ref = ref_date or date.today()
        return ref > entry.expiry

    def list_expired(self, *, ref_date: date | None = None) -> list[HeuristicEntry]:
        ref = ref_date or date.today()
        return [e for e in self._entries.values() if ref > e.expiry]

    def get(self, name: str) -> HeuristicEntry | None:
        return self._entries.get(name)

    def all_entries(self) -> list[HeuristicEntry]:
        return list(self._entries.values())


registry = HeuristicRegistry()


def _register_known_heuristics() -> None:
    """Register all known compensating heuristics at import time.

    RFC-041 D5: scaffolding only — each registration wraps an existing
    heuristic without changing its behavior.  Actual removal requires
    D6 golden-file baseline.
    """
    _created = date(2026, 9, 1)
    _expiry = date(2026, 12, 1)

    registry.register(
        "source_selection_bypass",
        rfc_origin="RFC-022",
        created=_created,
        expiry=_expiry,
        graduation_criteria="Close after D6 golden-file baseline quantifies verdict impact",
    )
    registry.register(
        "_ARABIC_FLAT_PREFER_MULTIPLIER",
        rfc_origin="RFC-027",
        created=_created,
        expiry=_expiry,
        graduation_criteria="Remove when Arabic flat-vs-tree scoring is validated",
    )
    registry.register(
        "force_verdict_override",
        rfc_origin="RFC-034",
        created=_created,
        expiry=_expiry,
        graduation_criteria="Remove when verdict authority is consolidated (D11)",
    )
    registry.register(
        "_try_image_enrichment",
        rfc_origin="RFC-022",
        created=_created,
        expiry=_expiry,
        graduation_criteria="Retain or graduate to permanent policy after D6 baseline",
    )
    registry.register(
        "_try_structural_pass",
        rfc_origin="RFC-033",
        created=_created,
        expiry=_expiry,
        graduation_criteria="Retain or graduate to permanent policy after D6 baseline",
    )
    registry.register(
        "_try_ocr_promotion",
        rfc_origin="RFC-033",
        created=_created,
        expiry=_expiry,
        graduation_criteria="Retain or graduate to permanent policy after D6 baseline",
    )
    registry.register(
        "_try_flat_promotion",
        rfc_origin="RFC-033",
        created=_created,
        expiry=_expiry,
        graduation_criteria="Retain or graduate to permanent policy after D6 baseline",
    )
    registry.register(
        "_try_content_class_promotion",
        rfc_origin="RFC-033",
        created=_created,
        expiry=_expiry,
        graduation_criteria="Retain or graduate to permanent policy after D6 baseline",
    )
    registry.register(
        "_try_small_doc_promotion",
        rfc_origin="RFC-033",
        created=_created,
        expiry=_expiry,
        graduation_criteria="Retain or graduate to permanent policy after D6 baseline",
    )


_register_known_heuristics()
