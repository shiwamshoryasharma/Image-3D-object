"""Orchestration of the 8 fixed steps (DESIGN section 3): protocol events, progress weights, timings, memory,
rollback of optional steps, report.json, ``result`` event and exit codes.

Failure policy
* ``load`` / ``geometry_repair`` / ``export`` (required): ``error`` event, partial report, exit 3 (input errors)
  or 4 (processing/export errors).
* ``face_refinement`` / ``topology_tuning`` / ``rig_optimization``: the primary mesh is snapshotted before the step
  and restored on failure; ``ctx.store``/metric overrides are restored too; the step is marked ``failed``, a
  warning is added and the run continues.
* ``analyze`` / ``validation`` (read-only): marked ``failed`` + warning, the run continues.
"""

from __future__ import annotations

import copy
import importlib
import logging
import os
import time
import traceback
from collections.abc import Callable
from typing import Any

import bpy

from . import analysis, io_mesh, meshdata, protocol, report
from .config import TunerConfig
from .context import STEP_SPECS, StepError, StepOutcome, StepSpec, TunerContext

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_BAD_CONFIG = 2
EXIT_INPUT = 3
EXIT_PROCESSING = 4


class Pipeline:
    """Runs one tuning job described by a ``TunerConfig``. ``run()`` returns the process exit code."""

    def __init__(self, config: TunerConfig, *, specs: tuple[StepSpec, ...] = STEP_SPECS) -> None:
        self.config = config
        self.specs = specs
        self.ctx = TunerContext(config)
        self.steps: list[dict[str, Any]] = []
        self.started = time.perf_counter()
        self._runners: dict[str, Callable[[TunerContext], StepOutcome]] = {
            "load": io_mesh.load_input,
            "analyze": _run_analyze,
            "export": _run_export,
        }

    # ------------------------------------------------------------------ public

    def run(self) -> int:
        ctx = self.ctx
        ctx.memory()
        try:
            os.makedirs(self.config.output_dir, exist_ok=True)
            if self.config.options.render_previews:
                os.makedirs(self.config.previews_path, exist_ok=True)
        except OSError as exc:
            protocol.error("output_dir_unwritable", f"Cannot create the output folder: {exc}")
            return EXIT_BAD_CONFIG
        protocol.plan([{"id": s.id, "label": s.label} for s in self.specs])
        for message in self.config.warnings:
            ctx.warn(message)
        try:
            base = 0.0
            for spec in self.specs:
                code = self._run_step(spec, base)
                if code is not None:
                    self._finish_failed(spec, code)
                    return code
                base += spec.weight
            return self._finish_ok()
        finally:
            ctx.cleanup()

    # ------------------------------------------------------------------ steps

    def _runner(self, spec: StepSpec) -> Callable[[TunerContext], StepOutcome]:
        if spec.id in self._runners:
            return self._runners[spec.id]
        module = importlib.import_module(f"{__package__}.{spec.module}")
        runner = getattr(module, "run", None)
        if not callable(runner):
            raise StepError("step_unavailable", f"Step module '{spec.module}' has no run(ctx) function")
        return runner

    def _run_step(self, spec: StepSpec, base: float) -> int | None:
        """Run one step; returns an exit code when the run must stop, else ``None``."""
        ctx = self.ctx
        ctx.begin_step(spec, base)
        protocol.step(spec.id, "running", message=spec.label, progress=base)
        snapshot = None
        store_backup: dict[str, Any] | None = None
        overrides_backup: dict[str, dict[str, Any]] | None = None
        modifiers_before: set[str] | None = None
        warnings_before = len(ctx.warnings)
        t0 = time.perf_counter()
        failure: StepError | None = None
        tb: str | None = None
        outcome: StepOutcome | None = None
        try:
            if spec.rollback and ctx.primary is not None:
                snapshot = meshdata.snapshot_mesh(ctx.primary, f"AMT_rollback_{spec.id}")
                store_backup = dict(ctx.store)
                overrides_backup = copy.deepcopy(ctx.metric_overrides)
                modifiers_before = {m.name for m in ctx.primary.modifiers}
            outcome = _coerce_outcome(self._runner(spec)(ctx))
        except StepError as exc:  # expected failure: code + message are enough
            failure = exc
        except MemoryError as exc:
            failure = StepError("out_of_memory", f"Out of memory: {exc}")
            tb = traceback.format_exc()
        except Exception as exc:  # a bug or an unexpected Blender error inside the step
            failure = StepError("internal_error", f"{type(exc).__name__}: {exc}")
            tb = traceback.format_exc()
        duration = time.perf_counter() - t0
        _ensure_object_mode()
        ctx.timings[f"step_{spec.id}"] = round(duration, 3)
        memory = ctx.memory()
        ctx.end_step()
        record = {"id": spec.id, "label": spec.label, "status": "", "message": "", "duration_s": round(duration, 3),
                  "details": {}, "memory_mb": memory}

        if failure is None and outcome is not None:
            meshdata.discard_mesh(snapshot)
            if ctx.primary is not None:
                ctx.update_bounds()
            if outcome.status == "warning" and outcome.message and len(ctx.warnings) == warnings_before:
                ctx.warn(f"{spec.label}: {outcome.message}")
            record.update(status=outcome.status, message=outcome.message, details=protocol.to_jsonable(outcome.details))
            self.steps.append(record)
            protocol.step(spec.id, outcome.status, message=outcome.message, duration_s=duration,
                          progress=base + spec.weight, details=record["details"])
            log.info("%s: %s (%.2fs)", spec.label, outcome.message or outcome.status, duration)
            return None

        assert failure is not None
        if tb:
            log.debug("%s failed:\n%s", spec.id, tb)
        record.update(status="failed", message=failure.message,
                      details={"code": failure.code, **protocol.to_jsonable(failure.details)})
        self.steps.append(record)
        if spec.required:
            protocol.step(spec.id, "failed", message=failure.message, duration_s=duration, progress=base,
                          details=record["details"])
            protocol.error(failure.code, failure.message, step=spec.id, traceback=tb)
            self._error = {"code": failure.code, "message": failure.message, "step": spec.id}
            return getattr(failure, "exit_code", EXIT_PROCESSING)
        # optional step: roll back and continue
        rolled_back = False
        if snapshot is not None and ctx.primary is not None:
            try:
                meshdata.restore_mesh(ctx.primary, snapshot)
                rolled_back = True
                snapshot = None
            except (RuntimeError, ReferenceError) as exc:
                log.error("Rollback of %s failed: %s", spec.id, exc)
        meshdata.discard_mesh(snapshot)
        if modifiers_before is not None and ctx.primary is not None:
            for mod in list(ctx.primary.modifiers):
                if mod.name not in modifiers_before:
                    ctx.primary.modifiers.remove(mod)
        if store_backup is not None:
            ctx.store.clear()
            ctx.store.update(store_backup)
        if overrides_backup is not None:
            ctx.metric_overrides = overrides_backup
        if ctx.primary is not None:
            ctx.update_bounds()
        record["details"]["rolled_back"] = rolled_back
        ctx.warn(f"{spec.label} failed and was skipped"
                 + (" (changes rolled back)" if rolled_back else "") + f": {failure.message}")
        protocol.step(spec.id, "failed", message=failure.message, duration_s=duration, progress=base + spec.weight,
                      details=record["details"])
        if spec.id == "validation" and ctx.validation is None:
            ctx.validation = {"passed": False, "checks": [
                {"id": "validation", "label": "Validation", "status": "fail", "value": None, "threshold": None,
                 "message": f"Validation could not run: {failure.message}"}]}
        return None

    # ------------------------------------------------------------------ finish

    _error: dict[str, Any] | None = None

    def _outputs(self) -> dict[str, Any]:
        out = self.ctx.store.get("outputs") or {}
        return {"model": out.get("model"), "blend": out.get("blend"), "previews": out.get("previews", [])}

    def _metrics(self) -> list[dict[str, Any]]:
        ctx = self.ctx
        return report.build_metrics(ctx.metrics_before, ctx.metrics_after, ctx.metric_overrides)

    def _finish_ok(self) -> int:
        ctx = self.ctx
        validation = ctx.validation or {"passed": False, "checks": []}
        warn_checks = any(c.get("status") in ("warn", "fail") for c in validation.get("checks", []))
        status = "completed_with_warnings" if ctx.warnings or warn_checks else "completed"
        metrics = self._metrics()
        outputs = self._outputs()
        doc = report.build_report(ctx, steps=self.steps, status=status, duration_s=time.perf_counter() - self.started,
                                  metrics=metrics, outputs=outputs)
        try:
            report.write_json_atomic(self.config.report_path, doc)
        except (OSError, ValueError, TypeError) as exc:
            protocol.error("report_write_failed", f"Could not write {self.config.outputs.report}: {exc}",
                           step="export", traceback=traceback.format_exc())
            return EXIT_PROCESSING
        protocol.result({"status": status, "model": outputs["model"], "report": self.config.outputs.report,
                         "blend": outputs["blend"], "previews": outputs["previews"], "metrics": metrics,
                         "warnings": list(ctx.warnings), "validation": validation,
                         "duration_s": round(time.perf_counter() - self.started, 3),
                         "peak_memory_mb": ctx.peak_memory})
        return EXIT_OK

    def _finish_failed(self, spec: StepSpec, code: int) -> None:
        """Best-effort partial report.json for a failed run (never raises)."""
        ctx = self.ctx
        try:
            doc = report.build_report(ctx, steps=self.steps, status="failed",
                                      duration_s=time.perf_counter() - self.started, metrics=self._metrics(),
                                      outputs=self._outputs(), error=self._error)
            report.write_json_atomic(self.config.report_path, doc)
        except Exception as exc:  # the error event was already emitted; the report is a bonus
            log.warning("Could not write the partial report: %s", exc)


def _ensure_object_mode() -> None:
    """Leave Edit/Sculpt/Weight-paint mode a step may have left behind (export and rollback need Object mode)."""
    obj = bpy.context.view_layer.objects.active
    if obj is not None and obj.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError as exc:
            log.warning("Could not return to Object mode: %s", exc)


def _coerce_outcome(value: Any) -> StepOutcome:
    """Accept ``StepOutcome``, ``None`` (== done) or a ``{"status", "message", "details"}`` dict."""
    if isinstance(value, StepOutcome):
        return value
    if value is None:
        return StepOutcome("done", "")
    if isinstance(value, dict):
        return StepOutcome(value.get("status", "done"), value.get("message", ""), value.get("details") or {})
    raise StepError("bad_step_result", f"Step returned {type(value).__name__} instead of StepOutcome")


# ================================================================================================================
# framework-internal steps
# ================================================================================================================


def _run_analyze(ctx: TunerContext) -> StepOutcome:
    obj = ctx.require_primary()
    metrics = analysis.analyze(ctx, obj, label="before")
    load = ctx.store.get("load") or {}
    if load.get("merged_vertices"):
        # duplicates were welded on load; report how many the INPUT had
        metrics["duplicate_vertices"] = int(load["merged_vertices"]) + int(metrics.get("duplicate_vertices") or 0)
    ctx.metrics_before = metrics
    parts = [f"{metrics['faces']:,} faces", f"{metrics.get('holes', 0)} holes",
             f"{metrics.get('non_manifold_edges', 0):,} non-manifold edges",
             f"{metrics.get('small_components', 0)} small parts"]
    details = dict(metrics)
    details["timings_s"] = ctx.store["analysis"]["before"]["details"].get("timings_s")
    return StepOutcome("done", ", ".join(parts), details)


def _run_export(ctx: TunerContext) -> StepOutcome:
    from . import previews as previews_mod

    cfg = ctx.config
    ctx.progress(0.02, "Writing GLB")
    info = io_mesh.export_glb(ctx, cfg.model_path)
    outputs: dict[str, Any] = {"model": cfg.outputs.model, "blend": None, "previews": []}
    ctx.store["outputs"] = outputs
    details: dict[str, Any] = {"model": {k: v for k, v in info.items() if k != "path"}}
    ctx.progress(0.45, "GLB written")
    if cfg.options.render_previews:
        try:
            shots = previews_mod.render_previews(ctx, cfg.previews_path, progress=(0.45, 0.85))
            outputs["previews"] = [{"name": s["name"], "view": s["view"], "variant": s["variant"],
                                    "shading": s["shading"]} for s in shots]
            details["previews"] = {"count": len(shots), "render_s": round(sum(s["render_s"] for s in shots), 3)}
        except Exception as exc:  # previews are a convenience; never fail the run for them
            log.debug("preview rendering failed:\n%s", traceback.format_exc())
            ctx.warn(f"Preview rendering failed: {type(exc).__name__}: {exc}")
    blend_path = cfg.blend_path
    if blend_path:
        ctx.progress(0.88, "Saving .blend")
        io_mesh.remove_original_snapshot(ctx)
        try:
            blend = io_mesh.save_blend(blend_path)
            outputs["blend"] = os.path.basename(blend_path)
            details["blend"] = {k: v for k, v in blend.items() if k != "path"}
        except (StepError, RuntimeError) as exc:
            ctx.warn(f"Could not save the .blend file: {getattr(exc, 'message', exc)}")
    ctx.progress(1.0, "Results saved")
    size_mb = info["size_bytes"] / (1024 * 1024)
    return StepOutcome("done", f"Saved {cfg.outputs.model} ({size_mb:.1f} MB)"
                       + (f" + {len(outputs['previews'])} previews" if outputs["previews"] else ""), details)
