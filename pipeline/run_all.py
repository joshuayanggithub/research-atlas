"""Pipeline orchestrator.

Runs the stages in dependency order. Note s09 (edges) runs before s08 (neighbors) because
the fused-similarity kNN consumes the intra-corpus citation graph.

    python -m pipeline.run_all                 # run everything
    python -m pipeline.run_all --from s04       # resume from a stage
    python -m pipeline.run_all --only s06,s07   # run a subset
"""

from __future__ import annotations

import importlib

import typer

from pipeline.common import log
from pipeline.config import load_config

app = typer.Typer(add_completion=False)

# (key, module) in execution order. The hierarchy consumes the fused neighbor graph, so
# edges and neighbors must be built before s06.
STAGES: list[tuple[str, str]] = [
    ("s00", "pipeline.stages.s00_resolve_orgs"),
    ("s01", "pipeline.stages.s01_fetch_openalex"),
    ("s02", "pipeline.stages.s02_build_corpus"),
    ("s03", "pipeline.stages.s03_embed"),
    ("s04", "pipeline.stages.s04_project"),
    ("s12", "pipeline.stages.s12_tiles"),
    ("s05", "pipeline.stages.s05_cluster"),
    ("s09", "pipeline.stages.s09_edges"),
    ("s08", "pipeline.stages.s08_neighbors"),
    ("s06", "pipeline.stages.s06_hierarchy"),
    ("s07", "pipeline.stages.s07_label"),
    ("s10", "pipeline.stages.s10_indexes"),
    ("s13", "pipeline.stages.s13_figures"),
    ("s11", "pipeline.stages.s11_emit"),
]


@app.command()
def main(
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
    from_: str = typer.Option(None, "--from", help="Resume from this stage key (e.g. s04)"),
    only: str = typer.Option(None, help="Comma-separated stage keys to run (e.g. s06,s07)"),
):
    cfg = load_config(config)
    only_set = set(only.split(",")) if only else None
    started = from_ is None

    for key, module_path in STAGES:
        if only_set is not None and key not in only_set:
            continue
        if not started:
            if key == from_:
                started = True
            else:
                continue
        mod = importlib.import_module(module_path)
        mod.run(cfg)

    log.info("\n=== pipeline complete ===")


if __name__ == "__main__":
    app()
