import sys, time
sys.stdout.reconfigure(line_buffering=True)
import bpy
print("background", bpy.app.background, bpy.app.version_string)
try:
    import gpu
    print("gpu.init doc:", getattr(gpu, "init", None).__doc__ if hasattr(gpu,"init") else None)
    t0=time.time(); gpu.init(); print("gpu.init ok", time.time()-t0, gpu.platform.backend_type_get() if hasattr(gpu.platform,"backend_type_get") else "", gpu.platform.renderer_get())
    from gpu_extras.batch import batch_for_shader
    t=time.time()
    off = gpu.types.GPUOffScreen(256, 256, format='RGBA8')
    print("offscreen ok", off, time.time()-t)
    import numpy as np
    shader = gpu.shader.from_builtin('FLAT_COLOR')
    pos=[(-0.5,-0.5,0),(0.5,-0.5,0),(0,0.5,0)]
    col=[(10/255,20/255,30/255,1)]*3
    batch = batch_for_shader(shader,'TRIS',{"pos":pos,"color":col})
    from mathutils import Matrix
    with off.bind():
        fb = gpu.state.active_framebuffer_get()
        fb.clear(color=(0,0,0,0), depth=1.0)
        gpu.state.depth_test_set('LESS'); gpu.state.depth_mask_set(True)
        gpu.matrix.load_matrix(Matrix.Identity(4)); gpu.matrix.load_projection_matrix(Matrix.Identity(4))
        batch.draw(shader)
        buf = fb.read_color(0,0,256,256,4,0,'UBYTE')
    a = np.array(buf.to_list(), dtype=np.uint8).reshape(256,256,4) if hasattr(buf,'to_list') else None
    print("center px", a[128,128].tolist() if a is not None else None, "unique", np.unique(a.reshape(-1,4),axis=0)[:5].tolist())
    off.free()
    print("GPU_OFFSCREEN_OK")
except Exception as e:
    import traceback; traceback.print_exc()
    print("GPU_OFFSCREEN_FAIL", repr(e))
