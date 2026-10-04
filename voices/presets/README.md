# Preset voice references

Each preset voice speaks from the reference recording in this folder (`<id>.wav` plus its exact
transcript in `<id>.txt`). Shipping them means every user hears the **same** preset voices.

They are **not recordings of real people**. Each one was *designed* by the VoxCPM2 model from the
text description in `voxcpm_server/voices.py` (`PRESETS[...]["design"]`).

To redesign a preset (for example after changing its description), delete its `.wav`/`.txt` here.
The server then designs a new one on first use and saves it in `data/voices/`. Copy that file back
here if you want to ship it.

Your own designed and cloned voices are stored in `data/voices/`, never here.
