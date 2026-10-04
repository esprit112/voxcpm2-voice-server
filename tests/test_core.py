"""Fast tests for the parts that do not need PyTorch or the model (run in CI on every OS).

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "integrations"))

from voxcpm_server import audio_io, dsp, markers  # noqa: E402
from voxcpm_server.config import load_dotenv  # noqa: E402
from voxcpm_server.voices import PRESETS, VoiceLibrary, is_safe_id  # noqa: E402


class Markers(unittest.TestCase):
    def test_plain_text_is_one_piece(self):
        plan = markers.plan_text("Hello there. How are you?")
        self.assertEqual(len(plan.pieces), 1)
        self.assertFalse(plan.has_markers)

    def test_style_marker_persists_until_next(self):
        plan = markers.plan_text("[sad] One. Two. [happy] Three.")
        self.assertEqual([p.emotion for p in plan.pieces], ["sad", "happy"])
        self.assertIn("Two", plan.pieces[0].text)

    def test_normal_returns_to_request_emotion(self):
        plan = markers.plan_text("[whisper] quiet [normal] loud", base_emotion="calm")
        self.assertEqual(plan.pieces[-1].emotion, "calm")

    def test_pause_markers(self):
        self.assertEqual(markers.parse_pause("pause:500ms"), 500)
        self.assertEqual(markers.parse_pause("pause:1.5s"), 1500)
        plan = markers.plan_text("First. [pause:800ms] Second.")
        self.assertEqual(plan.pieces[1].pause_before_ms, 800)

    def test_unknown_marker_is_free_style(self):
        plan = markers.plan_text("[like a pirate] Ahoy matey.")
        self.assertTrue(plan.pieces[0].style)

    def test_long_text_is_chunked(self):
        text = " ".join(f"This is sentence number {i}." for i in range(80))
        plan = markers.plan_text(text, max_chars=200)
        self.assertGreater(len(plan.pieces), 3)
        self.assertTrue(all(len(p.text) <= 260 for p in plan.pieces))

    def test_strip_markers(self):
        self.assertEqual(markers.strip_markers("[happy] Hi [pause] there").split(), ["Hi", "there"])


class Dsp(unittest.TestCase):
    sr = 48000

    def tone(self, seconds=1.0):
        t = np.arange(int(self.sr * seconds)) / self.sr
        return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    def test_speed_changes_length(self):
        x = self.tone()
        fast = dsp.apply_speed_and_pitch(x, self.sr, speed=1.5)
        slow = dsp.apply_speed_and_pitch(x, self.sr, speed=0.75)
        self.assertAlmostEqual(len(fast) / len(x), 1 / 1.5, delta=0.08)
        self.assertAlmostEqual(len(slow) / len(x), 1 / 0.75, delta=0.08)

    def test_silence_and_concat(self):
        self.assertEqual(len(dsp.silence(self.sr, 500)), self.sr // 2)
        joined = dsp.concat([self.tone(0.5), dsp.silence(self.sr, 250)])
        self.assertEqual(len(joined), int(self.sr * 0.75))

    def test_limit_peak(self):
        self.assertLessEqual(float(np.max(np.abs(dsp.limit_peak(self.tone() * 5)))), 0.981)


class AudioIO(unittest.TestCase):
    def test_wav_roundtrip(self):
        x = (0.1 * np.sin(np.linspace(0, 100, 4800))).astype(np.float32)
        data = audio_io.to_wav_bytes(x, 48000)
        with wave.open(io.BytesIO(data)) as w:
            self.assertEqual(w.getframerate(), 48000)
            self.assertEqual(w.getnframes(), 4800)

    def test_encode_formats(self):
        x = (0.1 * np.sin(np.linspace(0, 2000, 48000))).astype(np.float32)
        for fmt in ("wav", "flac", "pcm", "mp3", "opus"):
            with self.subTest(fmt=fmt):
                try:
                    data, ctype, _ = audio_io.encode(x, 48000, fmt)
                except RuntimeError as exc:  # old libsndfile without MP3/Opus and no ffmpeg
                    self.skipTest(str(exc))
                self.assertGreater(len(data), 100)
                self.assertTrue(ctype.startswith("audio/"))


class Voices(unittest.TestCase):
    def test_presets_are_complete(self):
        self.assertEqual(len(PRESETS), 8)
        for p in PRESETS:
            self.assertTrue(is_safe_id(p["id"]))
            self.assertTrue(p["design"])
            self.assertTrue((ROOT / "voices" / "presets" / f"{p['id']}.wav").exists(), p["id"])

    def test_unsafe_ids_rejected(self):
        for bad in ("../etc", "a/b", "", "x" * 200):
            self.assertFalse(is_safe_id(bad))

    def test_library_lookup_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            lib = VoiceLibrary(Path(d))
            self.assertEqual(lib.resolve("Aura Studio")["id"], "vox-aura-studio")
            self.assertEqual(lib.resolve("solas british")["id"], "vox-solas-british")
            self.assertEqual(lib.resolve(None)["id"], "vox-aura-studio")
            self.assertIn("has_reference", lib.get("vox-aura-studio") or {})


class DotEnv(unittest.TestCase):
    def test_env_file_does_not_override_real_env(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text("# c\nexport VOX_T_A=1\nVOX_T_B='two'\n", encoding="utf-8")
            os.environ["VOX_T_A"] = "real"
            load_dotenv(f)
            self.assertEqual(os.environ["VOX_T_A"], "real")
            self.assertEqual(os.environ["VOX_T_B"], "two")


class Integrations(unittest.TestCase):
    def test_jsonc_reader(self):
        from common import _strip_jsonc

        text = '{\n // comment\n "a": "http://x", /* b */ "c": [1,2,],\n}'
        self.assertEqual(json.loads(_strip_jsonc(text)), {"a": "http://x", "c": [1, 2]})

    def test_codex_block_replace(self):
        sys.path.insert(0, str(ROOT / "integrations" / "codex"))
        import importlib.util

        spec = importlib.util.spec_from_file_location("codex_inst", ROOT / "integrations" / "codex" / "install.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        toml = 'model = "o3"\n\n[mcp_servers.voxcpm2]\ncommand = "x"\n[mcp_servers.voxcpm2.env]\nA = "1"\n\n[mcp_servers.other]\ncommand = "y"\n'
        out = mod.remove_block(toml)
        self.assertNotIn("voxcpm2", out)
        self.assertIn("[mcp_servers.other]", out)
        self.assertIn('model = "o3"', out)

    def test_mcp_tools_list(self):
        import subprocess

        req = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n' \
              '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        proc = subprocess.run([sys.executable, str(ROOT / "integrations" / "mcp" / "voxcpm_mcp.py")], input=req,
                              capture_output=True, text=True, timeout=30)
        replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "voxcpm2-voice-server")
        self.assertIn("speak", [t["name"] for t in replies[1]["result"]["tools"]])

    def test_hook_markdown_cleanup(self):
        sys.path.insert(0, str(ROOT / "integrations" / "claude_code"))
        import speak_hook

        out = speak_hook.speakable("## Done\n- Fixed **bug** in `api.py`\n```py\nx=1\n```\nSee [docs](http://a.b).")
        self.assertNotIn("**", out)
        self.assertNotIn("```", out)
        self.assertIn("docs", out)
        self.assertNotIn("http", out)


if __name__ == "__main__":
    unittest.main()
