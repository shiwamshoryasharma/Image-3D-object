"""Run real semantic parts on an isolated copy of a validated test reconstruction."""
from pathlib import Path
import sys,os,json,shutil,time,argparse
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'test/part_aware'),str(ROOT/'backend')]
import run_parts_smoke as harness
parser=argparse.ArgumentParser();parser.add_argument('--model',type=Path,required=True);parser.add_argument('--label',default='semantic');args=parser.parse_args()
if not args.label.replace('_','').replace('-','').isalnum(): parser.error('label must be an alphanumeric test run name')
model=args.model.resolve();assert model.is_relative_to(ROOT/'test')
runtime=ROOT/'test/.runtime'/('parts_quality_'+args.label);output=ROOT/'test/outputs/parts_quality_regression/semantic'/args.label
harness.RUNTIME=runtime;harness.LOG_DIR=runtime/'logs';output.mkdir(parents=True,exist_ok=True)
quality_runtime=ROOT/'test/.runtime/generation_quality'
for key,value in {'HF_HOME':quality_runtime/'hf_home','TRITON_CACHE_DIR':quality_runtime/'cache/triton','TORCH_EXTENSIONS_DIR':quality_runtime/'cache/torch_extensions','CUDA_CACHE_PATH':quality_runtime/'cache/cuda','TEMP':quality_runtime/'temp','TMP':quality_runtime/'temp'}.items():
 value.mkdir(parents=True,exist_ok=True);os.environ[key]=str(value)
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['FLEX_GEMM_AUTOTUNE_CACHE_PATH']=str(quality_runtime/'cache/flex_gemm/autotune_cache.json')
jobid='job_000000001536';imageid='img_000000001536';jobdir=runtime/'jobs'/jobid
jobdir.mkdir(parents=True,exist_ok=True);(jobdir/'inputs').mkdir(exist_ok=True)
info=json.loads((ROOT/'test/outputs/parts_quality_regression/reported/job.json').read_text())
info.update(id=jobid,reference_set_id='rs_000000001536',primary_image_id=imageid,references=[{'image_id':imageid,'slot':'front','is_primary':True}])
shutil.copyfile(model,jobdir/'model.glb')
import trimesh,numpy as np
fixture_mesh=trimesh.load_scene(model,process=False).to_mesh()
lo,hi=np.asarray(fixture_mesh.vertices).min(axis=0),np.asarray(fixture_mesh.vertices).max(axis=0)
info['result']['stats'].update(vertices=len(fixture_mesh.vertices),faces=len(fixture_mesh.faces),
    bbox_min=lo.tolist(),bbox_max=hi.tolist(),dimensions=(hi-lo).tolist(),file_size_bytes=model.stat().st_size)
shutil.copyfile(ROOT/'test/images/ALICE/ALICE_full_front_view.png',jobdir/'inputs'/f'{imageid}.png')
shutil.copyfile(ROOT/'test/outputs/parts_quality_regression/reproduction/ALICE_full_front_view/baseline/job_smoke221952/preview.png',jobdir/'preview.png')
(jobdir/'job.json').write_text(json.dumps(info,indent=2))
hashes={name:harness.sha256(jobdir/name) for name in harness.PROTECTED}
backend=harness.Backend()
try:
 backend.wait_ready();status,features,_=backend.get('/api/v1/features')
 (output/'features.json').write_text(json.dumps(features,indent=2))
 print('features',status,features.get('part_extraction',{}),flush=True)
 body={'strategy':'semantic_parts','segmentation':{'subject':'anime_character','profile':'anime'},'views':8,'export':{'per_part_glbs':True,'previews':True}}
 status,response,_=backend.request('POST',f'/api/v1/jobs/{jobid}/part-analyses',body)
 print('created',status,response.get('id',response),flush=True)
 if status!=202: raise RuntimeError('semantic part request refused')
 run=harness.poll(backend,f"/api/v1/part-analyses/{response['id']}",2400,'semantic')
 (output/'run.json').write_text(json.dumps(run,indent=2))
 unchanged=all(harness.sha256(jobdir/name)==value for name,value in hashes.items())
 print('result',run['status'],'unchanged',unchanged,json.dumps({k:run['result'][k] for k in ['part_count','physical_count','region_count','unlabelled_face_ratio','cut_partner_consistency']}),flush=True)
 if run['status']!='completed' or not unchanged: raise RuntimeError('semantic analysis failed')
 asset=jobdir/'parts'/run['id']/'asset'
 from parts.reader import AssetReader
 reader=AssetReader(asset,verify='full')
 print('reassembly',reader.manifest.reassembly.all_exact,flush=True)
 assert reader.manifest.reassembly.all_exact, 'part reassembly must retain the original mesh'
 assert run['result']['unlabelled_face_ratio'] <= harness.MAX_UNLABELLED
 assert run['result']['cut_partner_consistency'] >= harness.MIN_CUT_CONSISTENCY
 exported=[]
 for path in sorted(asset.rglob('*.glb')):
  mesh=trimesh.load_scene(path,process=False).to_mesh()
  assert len(mesh.faces) and np.isfinite(mesh.vertices).all(), path.name
  exported.append({'path':str(path.relative_to(asset)),'faces':len(mesh.faces)})
 (output/'exports.json').write_text(json.dumps(exported,indent=2))
 (output/'location.json').write_text(json.dumps({'job':jobid,'analysis':run['id'],'asset':str(asset),'unchanged':unchanged},indent=2))
finally:
 backend.stop()