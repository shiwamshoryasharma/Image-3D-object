# Blender-side probe: dump interpreter facts as one JSON line.
import sys, json, os
try:
    import bpy
    bv = bpy.app.version_string
    extra = {
        "bpy.app.version": list(bpy.app.version),
        "bpy.app.version_cycle": bpy.app.version_cycle,
        "bpy.app.version_file": list(bpy.app.version_file),
        "bpy.app.build_hash": bpy.app.build_hash.decode(),
        "bpy.app.build_branch": bpy.app.build_branch.decode(),
        "bpy.app.background": bpy.app.background,
        "bpy.app.binary_path": bpy.app.binary_path,
        "bpy.app.factory_startup": getattr(bpy.app, "factory_startup", None),
        "bpy.utils.resource_path(USER)": bpy.utils.resource_path('USER'),
        "bpy.utils.resource_path(LOCAL)": bpy.utils.resource_path('LOCAL'),
        "bpy.utils.resource_path(SYSTEM)": bpy.utils.resource_path('SYSTEM'),
        "bpy.app.online_access": getattr(bpy.app, "online_access", None),
        "bpy.app.autoexec_fail": getattr(bpy.app, "autoexec_fail", None),
    }
except Exception as e:  # noqa
    bv, extra = None, {"err": repr(e)}
args_after = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
print("ARGV_JSON " + json.dumps({
    "sys.argv": sys.argv, "args_after_dashdash": args_after,
    "version_string": bv, "sys.version": sys.version,
    "sys.executable": sys.executable,
    "stdout.encoding": sys.stdout.encoding, "stdout.errors": sys.stdout.errors,
    "stdout.line_buffering": getattr(sys.stdout, "line_buffering", None),
    "stdout.isatty": sys.stdout.isatty(), "stderr.encoding": sys.stderr.encoding,
    "stdin": repr(sys.stdin),
    "flags.isolated": sys.flags.isolated, "flags.ignore_environment": sys.flags.ignore_environment,
    "flags.no_user_site": sys.flags.no_user_site, "flags.utf8_mode": sys.flags.utf8_mode,
    "cwd": os.getcwd(), "pid": os.getpid(), "ppid": os.getppid(),
    **extra}, ensure_ascii=True), flush=True)
