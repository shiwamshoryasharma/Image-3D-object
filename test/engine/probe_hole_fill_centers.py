"""Audit native-added fan centers in captured test-only legacy decoded meshes."""
from pathlib import Path
import sys,json,numpy as np,torch
ROOT=Path(__file__).resolve().parents[2]
report=[]
for capture in [ROOT/'test/outputs/parts_quality_regression/reproduction/ALICE_full_front_view/decoded.pt',ROOT/'test/outputs/generation_quality/cat/final_handoff/decoded.pt']:
 d=torch.load(capture,map_location='cpu',weights_only=True); v=d['vertices'].numpy();f=d['faces'].numpy();n=len(d['coords']);extra=len(v)-n
 fans=f[(f[:,2]>=n)&(f[:,0]<n)&(f[:,1]<n)]
 ids=fans[:,2]-n; counts=np.bincount(ids,minlength=extra)
 midpoint=(v[fans[:,0]].astype('float64')+v[fans[:,1]])*.5
 means=np.column_stack([np.bincount(ids,weights=midpoint[:,axis],minlength=extra) for axis in range(3)])/counts[:,None]
 error=np.abs(v[n:].astype('float64')-means)
 item={'capture':str(capture.relative_to(ROOT)),'source_vertices':n,'added_centers':extra,'fan_faces':len(fans),'max_abs_error_xyz':error.max(0).tolist(),'median_abs_error_xyz':np.median(error,axis=0).tolist(),'incorrect_center_count':int(np.any(error>1/d['resolution'],axis=1).sum())}
 report.append(item);print(json.dumps(item),flush=True)
out=ROOT/'test/outputs/parts_quality_regression/hole_center_corruption.json';out.write_text(json.dumps(report,indent=2))