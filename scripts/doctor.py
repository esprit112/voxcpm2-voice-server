#!/usr/bin/env python3
"""Diagnose a VoxCPM2 Voice Server install and print plain-English fixes.

    python scripts/doctor.py          # full check (imports PyTorch, a few seconds)
    python scripts/doctor.py --quick  # skip the GPU test

Run it with the server's own Python (.venv) - the start scripts and installers do this for you.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.request
import warnings
from pathlib import Path

# Library deprecation notices (e.g. torch.jit) are noise for an end-user health check
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from download_model import verify  # noqa: E402  (sibling script)

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"
results = []


def report(level, title, detail="", fix=""):
    results.append(level)
    print(f"[{level}] {title}" + (f": {detail}" if detail else ""))
    if fix and level != OK:
        print(f"       -> {fix}")


def os_label() -> str:
    if platform.system() == "Windows":
        build = int((platform.version().split(".") + ["0", "0", "0"])[2] or 0)
        return f"Windows {'11' if build >= 22000 else platform.release()} (build {build})"
    if platform.system() == "Darwin":
        return f"macOS {platform.mac_ver()[0]}"
    return f"{platform.system()} {platform.release()}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--json", action="store_true", help="also print a machine-readable summary")
    a = ap.parse_args()
    venv = ".venv\\Scripts\\python" if os.name == "nt" else ".venv/bin/python"

    print(f"VoxCPM2 Voice Server doctor - {os_label()} ({platform.machine()})\n")

    # Python
    v = sys.version_info
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    report(OK if v >= (3, 10) else FAIL, "Python", f"{platform.python_version()} at {sys.executable}",
           "VoxCPM2 needs Python 3.10 or newer. Re-run the installer (it installs its own Python).")
    if v >= (3, 14) and os.name == "nt":
        report(WARN, "Python version", "3.14+ on Windows: the 'kaldifst' dependency has no 64-bit package and must be "
               "compiled (needs CMake + Visual C++)",
               "Use the installer's Python 3.11 environment (.venv) instead of your system Python.")
    if not in_venv:
        report(WARN, "Virtual environment", "not running inside .venv",
               f"Run the doctor with the server's Python: {venv} scripts/doctor.py")

    # Platform specifics
    if platform.system() == "Darwin" and platform.machine() != "arm64":
        report(FAIL, "Mac CPU", "Intel Mac detected",
               "Current PyTorch releases only support Apple Silicon (M1 or newer) Macs.")

    # Settings
    try:
        from voxcpm_server.config import load_settings

        s = load_settings([])
        report(OK, "Settings", f"port {s.port}, device {s.device}, model {s.model_dir}")
    except Exception as exc:  # noqa: BLE001
        report(FAIL, "Settings", str(exc), "Check the syntax of your .env file.")
        return 1

    # Packages
    for name, fix in (("numpy", "re-run the installer"), ("soundfile", "re-run the installer"),
                      ("huggingface_hub", "re-run the installer")):
        try:
            mod = importlib.import_module(name)
            report(OK, f"package {name}", getattr(mod, "__version__", "installed"))
        except Exception as exc:  # noqa: BLE001
            report(FAIL, f"package {name}", str(exc), fix)

    try:
        import soundfile as sf

        fmts = sf.available_formats()
        missing = [f for f in ("MP3", "OGG", "FLAC") if f not in fmts]
        report(OK if not missing else WARN, "audio encoders", f"libsndfile {sf.__libsndfile_version__}",
               f"No native {', '.join(missing)} support; install ffmpeg for those formats (WAV always works).")
    except Exception:  # noqa: BLE001
        pass

    torch = None
    try:
        import torch  # noqa: F811

        report(OK, "PyTorch", torch.__version__)
    except Exception as exc:  # noqa: BLE001
        report(FAIL, "PyTorch", str(exc), "Re-run the installer to install PyTorch for your hardware.")

    try:
        import voxcpm  # noqa: F401
        from importlib.metadata import version

        report(OK, "voxcpm", version("voxcpm"))
    except Exception as exc:  # noqa: BLE001
        report(FAIL, "voxcpm", str(exc), "Re-run the installer (pip package 'voxcpm').")

    # Accelerator
    nvsmi = shutil.which("nvidia-smi")
    gpu_line = ""
    if nvsmi:
        try:
            gpu_line = subprocess.run([nvsmi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                                      capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
        except Exception:  # noqa: BLE001
            gpu_line = ""
    if torch is not None:
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            vram = p.total_memory / 1024 ** 3
            report(OK if vram >= 8 else WARN, "NVIDIA GPU", f"{p.name}, {vram:.1f} GB, CUDA {torch.version.cuda}",
                   "Under 8 GB of VRAM the model may not fit; use VOXCPM_DEVICE=cpu if loading fails.")
            if not a.quick:
                try:
                    x = torch.randn(1024, 1024, device="cuda")
                    _ = (x @ x).sum().item()
                    report(OK, "GPU compute test", "passed")
                except Exception as exc:  # noqa: BLE001
                    report(FAIL, "GPU compute test", str(exc), "Update your NVIDIA driver, then re-run the installer.")
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            report(OK, "Apple GPU (MPS)", "available")
        else:
            if gpu_line:
                report(WARN, "GPU", f"nvidia-smi sees '{gpu_line}' but PyTorch cannot use it",
                       "PyTorch is a CPU-only build or the driver is too old. Update the NVIDIA driver and run the "
                       "installer again (it picks a CUDA build for your driver).")
            else:
                report(WARN, "GPU", "none usable - running on CPU",
                       "CPU works but is slow (several seconds per second of speech).")

    # RAM
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("m", ctypes.c_ulong), ("t", ctypes.c_ulonglong),
                            ("a", ctypes.c_ulonglong), ("x", ctypes.c_ulonglong * 5)]
            ms = MS()
            ms.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            ram = ms.t / 1024 ** 3
        elif platform.system() == "Darwin":
            ram = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 1024 ** 3
        else:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
        report(OK if ram >= 15 else WARN, "System memory", f"{ram:.0f} GB",
               "16 GB or more is recommended (the model alone is ~5 GB; CPU/MPS mode keeps it in RAM).")
    except Exception:  # noqa: BLE001
        pass

    # Model files
    problems = verify(s.model_dir)
    report(OK if not problems else WARN, "Model weights", str(s.model_dir) if not problems else "; ".join(problems),
           f"Run: {venv} scripts/download_model.py   (or let the server download them on first start)")

    free = shutil.disk_usage(ROOT).free / 1024 ** 3
    report(OK if free >= 3 else WARN, "Free disk space", f"{free:.0f} GB", "Keep a few GB free for voices and audio.")

    # Port / running server
    url = s.public_url
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
            h = json.loads(r.read())
        report(OK, "Server", f"running at {url} - {h.get('status')} on {h.get('device_label')}")
    except Exception:  # noqa: BLE001
        with socket.socket() as sock:
            busy = sock.connect_ex(("127.0.0.1", s.port)) == 0
        if busy:
            report(FAIL, f"Port {s.port}", "used by another program",
                   "Set VOXCPM_PORT=8809 (or any free port) in .env.")
        else:
            report(OK, "Server", f"not running (port {s.port} is free)")

    if shutil.which("ffmpeg"):
        report(OK, "ffmpeg", "found (optional)")

    fails, warns = results.count(FAIL), results.count(WARN)
    print(f"\n{fails} problem(s), {warns} warning(s).", "All good!" if not fails and not warns else "")
    if a.json:
        print(json.dumps({"fail": fails, "warn": warns}))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
