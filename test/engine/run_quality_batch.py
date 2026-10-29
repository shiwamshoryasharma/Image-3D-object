"""Real multi-image quality validation with one production engine load."""
from __future__ import annotations
import argparse
import importlib.util
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
# Keep validation state local even when this runner is invoked from another shell.
import os
_quality_runtime = ROOT / "test/.runtime/generation_quality"
_quality_cache = _quality_runtime / "cache"
for _key, _value in {
    "IMG3D_RUNTIME_DIR": _quality_runtime,
    "IMG3D_LOG_DIR": ROOT / "test/.logs/generation_quality",
    "IMG3D_HF_HOME": _quality_runtime / "hf_home",
    "HF_HOME": _quality_runtime / "hf_home",
    "TRITON_CACHE_DIR": _quality_cache / "triton",
    "TORCH_EXTENSIONS_DIR": _quality_cache / "torch_extensions",
    "CUDA_CACHE_PATH": _quality_cache / "cuda",
    "TEMP": _quality_runtime / "temp",
    "TMP": _quality_runtime / "temp",
}.items():
    _value.mkdir(parents=True, exist_ok=True)
    os.environ[_key] = str(_value)
os.environ["FLEX_GEMM_AUTOTUNE_CACHE_PATH"] = str(_quality_cache / "flex_gemm/autotune_cache.json")
(_quality_cache / "flex_gemm").mkdir(parents=True, exist_ok=True)
os.environ["IMG3D_ALLOW_MODEL_DOWNLOADS"] = "false"
sys.dont_write_bytecode = True

sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "test/engine")]
spec = importlib.util.spec_from_file_location("img3d_quality_smoke", ROOT / "test/engine/run_generation_smoke.py")
smoke = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", nargs="+", type=Path, default=[
        ROOT/"test/images/ALICE/ALICE_full_front_view.png",
        ROOT/"test/images/generation_quality/cat.jpg",
        ROOT/"test/images/generation_quality/house.webp"])
    parser.add_argument("--resolution", type=int, choices=(512,1024,1536),default=1024)
    parser.add_argument("--texture-size",type=int,default=1024)
    parser.add_argument("--polygons",type=int,default=500000)
    parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--label",default="fixed")
    args=parser.parse_args()
    if not args.label.replace("-","").replace("_","").isalnum():
        parser.error("label must be an alphanumeric test-run name")
    for image in args.images:
        if not image.resolve().is_relative_to(ROOT/"test/images") or not image.is_file():
            parser.error(f"input must be an existing file under test/images: {image}")
    from inference.base import CancellationToken,ConditioningImage,GenerationParams,GenerationRequest
    from inference.trellis.engine import TrellisEngine
    from inference.trellis import generation
    from models.enums import EngineState,ReferenceSlot
    from mesh_quality_probe import analyze_file
    import torch
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    output_root=ROOT/"test/outputs/generation_quality"
    smoke.OUTPUT_ROOT=output_root/args.label
    settings=smoke.build_settings(SimpleNamespace(no_low_vram=False,attention=None))
    engine=TrellisEngine(settings)
    report={"label":args.label,"images":[],"settings":vars(args)|{"images":[str(x) for x in args.images]}}
    original_export=generation.export_glb
    current={}
    def capture(mesh,pipeline,resolution,params,out_path,reporter,cancel):
        torch.save({"vertices":mesh.vertices.detach().cpu(),"faces":mesh.faces.detach().cpu(),
            "attrs":mesh.attrs.detach().cpu(),"coords":mesh.coords.detach().cpu(),
            "layout":{key:(value.start,value.stop,value.step) for key,value in pipeline.pbr_attr_layout.items()},
            "resolution":int(resolution),"texture_size":int(params.texture_size),
            "polygon_target":int(params.polygon_target),"webp_textures":bool(params.glb_webp_textures)},
            current["directory"]/"decoded.pt")
        return original_export(mesh,pipeline,resolution,params,out_path,reporter,cancel)
    generation.export_glb=capture
    failed=False
    try:
        started=time.perf_counter()
        engine.load(on_status=lambda status: print(f"[load] {status.state}: {status.message}",flush=True))
        report["load_seconds"]=round(time.perf_counter()-started,3)
        if engine.status().state is not EngineState.READY:
            raise RuntimeError(engine.status().error)
        for image in args.images:
            image=image.resolve()
            directory=output_root/image.stem/args.label
            if directory.exists() and (directory/"model.glb").exists():
                raise RuntimeError(f"refusing to overwrite existing validation run {directory}")
            directory.mkdir(parents=True,exist_ok=True)
            current["directory"]=directory
            copied=directory/("input"+image.suffix)
            shutil.copyfile(image,copied)
            item={"image":str(image.relative_to(ROOT)),"directory":str(directory)}
            report["images"].append(item)
            cancel=CancellationToken()
            reporter=smoke.PrintingReporter(cancel, None)
            sampler=smoke.DeviceSampler(engine)
            sampler.start()
            try:
                request=GenerationRequest(job_id="job_quality_"+image.stem,
                    images=[ConditioningImage(image_id="img_quality0001",slot=ReferenceSlot.FRONT,path=copied,is_primary=True)],
                    all_references=[ConditioningImage(image_id="img_quality0001",slot=ReferenceSlot.FRONT,path=copied,is_primary=True)],
                    params=GenerationParams(resolution=args.resolution,texture_size=args.texture_size,
                        polygon_target=args.polygons,seed=args.seed,remove_background=True),
                    output_dir=directory)
                result=engine.generate(request,reporter,cancel)
                item.update({"outcome":"completed","timings":result.timings,"mesh_stats":result.mesh_stats,
                    "metadata":result.metadata,"glb_check":smoke.validate_glb(result.glb_path),
                    "quality":analyze_file(result.glb_path)})
                if not item["glb_check"]["has_uv"] or not result.preview_path.is_file():
                    raise AssertionError("missing UV/preview output")
            except Exception as exc:
                failed=True
                item.update({"outcome":"failed","error":repr(exc)})
                logging.exception("Validation failed for %s",image.name)
            finally:
                sampler.stop()
                item["peak_vram_used_mb"]=max((sample.get("vram_used_mb",0) for sample in sampler.samples),default=0)
                (directory/"summary.json").write_text(json.dumps(item,indent=2,default=str)+"\n",encoding="utf-8")
                print("[quality-summary]",json.dumps({"image":image.name,"outcome":item["outcome"],"quality":{key:item.get("quality",{}).get(key) for key in ("faces","boundary_edges","nonmanifold_edges","duplicate_faces","connected_components")}}),flush=True)
                report_path=output_root/(args.label+"_batch.json")
                report_path.write_text(json.dumps(report,indent=2,default=str)+"\n",encoding="utf-8")
    finally:
        generation.export_glb=original_export
        engine.unload()
    return int(failed)

if __name__=="__main__":
    raise SystemExit(main())
