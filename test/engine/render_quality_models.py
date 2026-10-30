"""Test-only Blender renders of the live high-resolution quality batch.

Run under gpu_lock.py with Blender --background --factory-startup --python-exit-code86
--python test/engine/render_quality_models.py -- --label <batch-label>.
"""
from pathlib import Path
import argparse
import sys
import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'test/mesh_tuner/refine/blender_helpers'))
import render_util as ru
parser = argparse.ArgumentParser()
parser.add_argument('--label', required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
if not args.label.replace('_', '').replace('-', '').isalnum():
    parser.error('invalid test batch label')
output = ROOT / 'test/outputs/parts_quality_regression/final' / args.label
output.mkdir(parents=True, exist_ok=True)
cases = {
    'ALICE_full_front_view': {
        'anime_full': ([0, 0, 0], 1.12, [0, -1, 0], 'texture'),
        'anime_back': ([0, 0, 0], 1.12, [0, 1, 0], 'texture'),
        'anime_face': ([0, -.08, .395], .16, [0, -1, 0], 'texture'),
        'anime_face_clay': ([0, -.08, .395], .16, [0, -1, 0], 'matcap'),
        'anime_face_side': ([0, -.08, .395], .16, [.7, -1, 0], 'texture'),
    },
    'cat': {'cat': ([0, 0, 0], 1.12, [.6, -1, .4], 'texture'),
            'cat_clay': ([0, 0, 0], 1.12, [.6, -1, .4], 'matcap')},
    'house': {'house': ([0, 0, 0], 1.25, [.7, -1, .5], 'texture'),
              'house_clay': ([0, 0, 0], 1.25, [.7, -1, .5], 'matcap')},
}
for stem, views in cases.items():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    model = ROOT / 'test/outputs/generation_quality' / stem / args.label / 'model.glb'
    bpy.ops.import_scene.gltf(filepath=str(model))
    objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    ru.setup_scene(896)
    for name, (target, scale, direction, mode) in views.items():
        ru.render(str(output / (name + '.png')), target, scale, direction, objects, mode=mode)
        print('rendered', name, flush=True)