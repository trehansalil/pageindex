# tests/test_converters_footprint.py
"""Memory-footprint contract for the Docling PDF pipeline (CONV-02).

The worker OOMKilled on a real PDF because Docling's CPU inference defaulted to 4
intra-op threads, multiplying per-thread scratch arenas at peak. Capping
accelerator threads is the one code-level RSS reducer that costs NO extraction
fidelity (Docling propagates num_threads to torch.set_num_threads / onnxruntime
internally), unlike disabling TableFormer or using TableFormerMode.FAST.

CONV-02-C1  the PDF pipeline is built single-threaded on CPU while preserving
            table-structure reconstruction at ACCURATE fidelity.
"""

import importlib

from docling.datamodel.accelerator_options import AcceleratorDevice
from docling.datamodel.pipeline_options import TableFormerMode

from pageindex_mcp.converters import _build_pdf_pipeline_options


def test_conv_02_c1_pipeline_is_single_threaded_cpu_without_losing_fidelity():
    """CONV-02-C1: the built PdfPipelineOptions cap intra-op threads to 1 on CPU
    (the no-fidelity-loss RSS reducer) while keeping do_table_structure=True at
    TableFormerMode.ACCURATE so table extraction quality is unchanged."""
    opts = _build_pdf_pipeline_options()

    # RSS reducer: one inference thread, CPU only.
    assert opts.accelerator_options.num_threads == 1
    assert opts.accelerator_options.device == AcceleratorDevice.CPU

    # Fidelity preserved: tables are still reconstructed at ACCURATE precision.
    assert opts.do_table_structure is True
    assert opts.table_structure_options.mode == TableFormerMode.ACCURATE
