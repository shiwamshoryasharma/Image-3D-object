#!/usr/bin/env bash
# Sequentially analyze selected real TRELLIS.2 jobs with Blender 5.2 (probe; outputs under test/outputs/mesh_tuner/analysis).
B="/c/Program Files/Blender Foundation/Blender 5.2/blender.exe"
ROOT="E:/14-OCT-2026/Img3D"
for J in "$@"; do
  OUT="$ROOT/test/outputs/mesh_tuner/analysis/$J"
  mkdir -p "$OUT"
  EXTRA=""
  [ "$J" = "job_04535ae38930" ] && EXTRA="--timing-merge-import"
  s=$(date +%s)
  "$B" --background --factory-startup --python-exit-code 1 --python "$ROOT/test/mesh_tuner/probe/analyze_glb.py" -- \
     --input "$ROOT/backend/runtime/jobs/$J/model.glb" --out "$OUT" --export-welded $EXTRA > "$OUT/blender_stdout.log" 2>&1
  echo "$J exit=$? wall=$(( $(date +%s)-s ))s"
done
