# Blender-side probe: sleep for N seconds (argv after --), printing heartbeat.
import sys, time, os
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
n = float(args[0]) if args else 60
print(f"SLEEP_START pid={os.getpid()} ppid={os.getppid()}", flush=True)
end = time.time() + n
while time.time() < end:
    time.sleep(0.25)
print("SLEEP_DONE", flush=True)
