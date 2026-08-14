"""Stage-level tests for the optional offline figure bake."""

from __future__ import annotations

import builtins
import importlib
import sys


def test_stage_import_does_not_require_figure_extractor(monkeypatch):
    """A disabled optional stage must be importable without PyMuPDF installed."""
    sys.modules.pop("pipeline.stages.s13_figures", None)
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"pipeline.common.figure_extract", "pymupdf"}:
            raise AssertionError(f"optional figure dependency imported eagerly: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.import_module("pipeline.stages.s13_figures")
