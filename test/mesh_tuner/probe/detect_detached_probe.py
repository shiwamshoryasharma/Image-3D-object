"""Under DETACHED_PROCESS vs CREATE_NO_WINDOW, does a console grandchild spawned by Blender's Python get a NEW console?"""
import subprocess, sys, time, json
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from jobobject_probe import KillOnCloseJob, popen_in_job, descendants, start_reader, wait_for_line
B = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
res = {}
for name, flags in (("CREATE_NO_WINDOW", 0x08000000), ("DETACHED_PROCESS", 0x00000008)):
    job = KillOnCloseJob()
    p = popen_in_job([B, "-b", "--factory-startup", "--python", str(HERE / "blender_side" / "spawn_child.py")], job,
                     creationflags=flags & ~0x08000000 if name == "DETACHED_PROCESS" else 0,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    sink = []; start_reader(p, sink); wait_for_line(sink, "SPAWNED"); time.sleep(1.0)
    res[name] = descendants(p.pid)
    job.terminate(1); p.wait(10); job.close()
print(json.dumps(res, indent=1))
