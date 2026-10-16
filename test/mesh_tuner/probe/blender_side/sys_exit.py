import sys
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
print("before exit", flush=True)
sys.exit(int(args[0]) if args else 0)
