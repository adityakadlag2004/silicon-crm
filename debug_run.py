import sys, traceback, runpy, os
log = open('debug_run.log', 'w', encoding='utf-8')
def w(*args, **kwargs):
    print(*args, **kwargs, file=log)
    log.flush()
w("PYTHON:", sys.version)
w("CWD:", os.getcwd())
w("EXE:", sys.executable)
try:
    import importlib
    w("=== import config.settings ===")
    importlib.import_module('config.settings')
    w("config.settings: OK")
except Exception:
    w("config.settings import FAILED")
    traceback.print_exc(file=log)
    log.flush()
w("\n=== Running manage.py as a script (capture exceptions) ===")
try:
    runpy.run_path('manage.py', run_name='__main__')
    w("manage.py returned normally")
except SystemExit as se:
    w("manage.py raised SystemExit:", repr(se))
    traceback.print_exc(file=log)
except Exception:
    w("manage.py raised Exception:")
    traceback.print_exc(file=log)
w("\n=== End ===")
log.close()
