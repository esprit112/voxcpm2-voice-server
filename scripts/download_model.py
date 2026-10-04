#!/usr/bin/env python3
"""Download (or resume / repair) the VoxCPM2 weights into models/voxcpm2.

    python scripts/download_model.py                 # default location
    python scripts/download_model.py --dir D:/models/voxcpm2
    HF_ENDPOINT=https://hf-mirror.com python scripts/download_model.py   # use a mirror

Interrupted downloads resume where they stopped. Safe to run again at any time.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPO = "openbmb/VoxCPM2"
REQUIRED = {"config.json": 1_000, "model.safetensors": 4_000_000_000, "audiovae.pth": 300_000_000,
            "tokenizer.json": 1_000_000, "tokenizer_config.json": 500}


def default_dir() -> Path:
    try:
        from voxcpm_server.config import load_settings

        return load_settings([]).model_dir
    except Exception:  # noqa: BLE001
        return ROOT / "models" / "voxcpm2"


def verify(folder: Path) -> list:
    problems = []
    for name, min_size in REQUIRED.items():
        p = folder / name
        if not p.is_file():
            problems.append(f"missing {name}")
        elif p.stat().st_size < min_size:
            problems.append(f"{name} looks incomplete ({p.stat().st_size:,} bytes)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="target folder (default: VOXCPM_MODEL_DIR or ./models/voxcpm2)")
    ap.add_argument("--check", action="store_true", help="only verify the files, do not download")
    a = ap.parse_args()
    target = Path(a.dir).expanduser().resolve() if a.dir else default_dir()

    problems = verify(target)
    if not problems:
        print(f"VoxCPM2 weights are complete in {target}")
        return 0
    if a.check:
        print(f"VoxCPM2 weights in {target} are not usable: {', '.join(problems)}")
        return 1

    target.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(target).free / 1024 ** 3
    if free_gb < 6:
        print(f"Only {free_gb:.1f} GB free at {target}; the model needs about 5 GB. Free some space first.")
        return 1

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub is not installed. Run the installer first (or: pip install huggingface_hub).")
        return 1

    mirror = os.environ.get("HF_ENDPOINT")
    print(f"Downloading {REPO} (~5 GB) to {target}" + (f" via {mirror}" if mirror else ""))
    print("This happens once. If it is interrupted, run this script again to resume.\n")
    for attempt in range(1, 4):
        try:
            snapshot_download(repo_id=REPO, local_dir=str(target))
            break
        except KeyboardInterrupt:
            print("\nStopped. Run the script again to resume.")
            return 130
        except Exception as exc:  # noqa: BLE001
            print(f"\nDownload attempt {attempt} failed: {exc}")
            if attempt == 3:
                print("Giving up. Check your internet connection (or set HF_ENDPOINT to a mirror) and run again.")
                return 1
            time.sleep(5 * attempt)

    problems = verify(target)
    if problems:
        print(f"Download finished but the files are not complete: {', '.join(problems)}. Run again to repair.")
        return 1
    print(f"\nDone. VoxCPM2 weights are ready in {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
