# Blender-side probe: print lines spaced by sleeps, WITHOUT explicit flush (mode via argv after --).
import sys, time
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
flush = "flush" in args
for i in range(5):
    print(f"LINE {i} t={time.time():.3f}", flush=flush)
    time.sleep(0.5)
print("UNICODE é ✓ 日本 Ω", flush=flush)
sys.stderr.write("STDERR line é\n")
