# Blender-side probe: spawn a grandchild (python subprocess) then sleep, to test kill-tree.
import sys, subprocess, time, os
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
print(f"SPAWNED grandchild pid={child.pid} from blender pid={os.getpid()}", flush=True)
time.sleep(120)
