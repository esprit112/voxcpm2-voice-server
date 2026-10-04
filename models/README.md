# Model folder

The installers download the VoxCPM2 weights (about 5 GB) into `models/voxcpm2/`.
This folder is ignored by git.

Download or repair them yourself with:

```bash
# Windows
.venv\Scripts\python scripts\download_model.py
# Linux / macOS
.venv/bin/python scripts/download_model.py
```

Already have the weights somewhere else? Set `VOXCPM_MODEL_DIR=/path/to/voxcpm2` in `.env`
instead of downloading them again.

Model: [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2), Apache-2.0.
