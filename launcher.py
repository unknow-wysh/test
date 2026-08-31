from __future__ import annotations

import os
import subprocess
import sys


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_312 = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"


def main() -> int:
    os.chdir(BASE_DIR)
    python_exe = PYTHON_312 if os.path.exists(PYTHON_312) else sys.executable
    env = dict(os.environ)
    env.setdefault("PYTHON_EXECUTABLE", python_exe)

    startup = os.path.join(BASE_DIR, "startup.py")
    if not os.path.exists(startup):
        print("startup.py not found")
        return 1

    proc = subprocess.Popen([python_exe, startup], cwd=BASE_DIR, env=env)
    return proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
