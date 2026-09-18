"""The converter child's timeout budget -- one callable seam (RFC-046 D11).

The budget used to be derived inline inside ``_run_converter_subprocess``,
which left task 3.10's property tests nothing to call: they re-implemented the
formula in the test body and stayed green when the 3.11 fix was reverted. This
module exists so production and its tests read the same code.

``constants.py`` cannot host this -- it declares zero internal imports to stay
free of circular dependencies, and the per-chunk budget lives in
``converters.docling_conv``.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..converters.docling_conv import (
    _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S,
    chunked_docling_timeout_s,
)
from .constants import CHILD_TIMEOUT, MAX_EFFECTIVE_TIMEOUT


@dataclass(frozen=True, slots=True)
class ChildTimeout:
    """The budget granted to one converter child.

    ``requested`` is what the formula produced; ``effective`` is what the child
    actually gets after MAX_EFFECTIVE_TIMEOUT. Both are kept so the caller can
    log the difference -- a silently capped timeout is the failure mode that
    makes a document look like it "just timed out".
    """

    requested: int
    effective: int

    @property
    def capped(self) -> bool:
        return self.requested > self.effective


def effective_child_timeout(
    *,
    chunk_count: int = 1,
    is_docling_route: bool = False,
    ocr_multiplier: float = 1.0,
) -> ChildTimeout:
    """The child's timeout budget, from the startup handshake's own numbers.

    ``CHILD_TIMEOUT`` is the allowance for everything that is not Docling
    conversion: model load, OCR, tree build, LLM calls. A chunked conversion
    *adds* its per-chunk budget on top of that floor rather than competing with
    it -- RFC-046 task 3.11. The prior ``max(CHILD_TIMEOUT, dynamic_timeout)``
    let the chunk budget swallow the floor from chunk_count=3 upward, leaving
    300s for every non-conversion step.

    chunk_count=1 keeps the historical single-pass value exactly.
    """
    requested = CHILD_TIMEOUT
    if is_docling_route:
        chunks = max(1, chunk_count)
        if chunks > 1:
            requested = CHILD_TIMEOUT + chunks * _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S
        else:
            requested = max(CHILD_TIMEOUT, chunked_docling_timeout_s(chunks))

    requested = int(requested * ocr_multiplier)
    return ChildTimeout(requested=requested, effective=min(requested, MAX_EFFECTIVE_TIMEOUT))
