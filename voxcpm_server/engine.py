"""VoxCPM2 inference engine.

* One dedicated inference thread: model loading and every generation run there (torch.compile and
  CUDA graphs must stay on the thread that built them), so HTTP threads never touch the model.
* Device: auto -> CUDA (NVIDIA) -> MPS (Apple Silicon) -> CPU, or whatever the user forces.
* Missing weights are downloaded on first start (unless VOXCPM_AUTO_DOWNLOAD=0).
* Text is planned into pieces (emotion markers + sentence groups), each generated with the voice's
  reference recording, then joined with natural gaps and click-free fades.
"""

from __future__ import annotations

import concurrent.futures
import gc
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional, Tuple

import numpy as np

from . import dsp
from .audio_io import to_wav_bytes
from .config import Settings
from .markers import EMOTION_STYLES, Piece, plan_text
from .voices import CALIBRATION_TEXT, VoiceLibrary

HF_REPO = "openbmb/VoxCPM2"
REQUIRED_FILES = ("config.json", "model.safetensors", "audiovae.pth", "tokenizer.json")
DEFAULT_SR = 48000


def log(msg: str) -> None:
    print(f"[VoxCPM2] {msg}", flush=True)


def model_files_present(model_dir: Path) -> bool:
    return all((model_dir / f).is_file() for f in REQUIRED_FILES)


class Engine:
    def __init__(self, settings: Settings, library: VoiceLibrary):
        self.s = settings
        self.voices = library
        self.model = None
        self.status = "starting"            # starting | downloading | loading | ready | no_model | error
        self.error: Optional[str] = None
        self.device = "cpu"
        self.device_label = "CPU"
        self.preferred_device = settings.device or "auto"
        self.optimize = settings.optimize
        self.load_seconds: Optional[float] = None
        self.started_at = time.time()
        self.stats = {"requests": 0, "audio_seconds": 0.0, "compute_seconds": 0.0}
        self._ready = threading.Event()
        self._load_started = threading.Event()
        self._gen_lock = threading.RLock()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="voxcpm-infer")
        self._infer_ident = self._executor.submit(threading.get_ident).result()

    # ---- threading -----------------------------------------------------------------------------
    def run_on_infer(self, fn: Callable):
        if threading.get_ident() == self._infer_ident:
            return fn()
        return self._executor.submit(fn).result()

    def stream_on_infer(self, factory: Callable[[], Iterator], stop: threading.Event) -> Iterator:
        q: "queue.Queue" = queue.Queue(maxsize=64)
        done = object()

        def put(item) -> bool:
            while not stop.is_set():
                try:
                    q.put(item, timeout=0.5)
                    return True
                except queue.Full:
                    continue
            return False

        def producer():
            gen = None
            try:
                gen = factory()
                for item in gen:
                    if not put(item):
                        break
            except Exception as exc:  # noqa: BLE001 - re-raised on the reader side
                put(exc)
            finally:
                if gen is not None and hasattr(gen, "close"):
                    try:
                        gen.close()
                    except Exception:  # noqa: BLE001
                        pass
                put(done)

        self._executor.submit(producer)
        while True:
            try:
                item = q.get(timeout=1.0)
            except queue.Empty:
                if stop.is_set():
                    return
                continue
            if item is done:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    # ---- loading -------------------------------------------------------------------------------
    def start_loading(self) -> None:
        if not self._load_started.is_set():
            self._load_started.set()
            self._executor.submit(self._load)

    def wait_ready(self, timeout: float) -> bool:
        self.start_loading()
        return self._ready.wait(timeout)

    def _pick_device(self) -> str:
        import torch

        want = (self.preferred_device or "auto").lower()
        has_mps = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        if want.startswith("cuda"):
            if not torch.cuda.is_available():
                log("CUDA was requested but is not available; falling back to automatic selection.")
                want = "auto"
            else:
                return want
        if want == "mps":
            if has_mps:
                return "mps"
            log("MPS was requested but is not available; falling back to automatic selection.")
            want = "auto"
        if want == "cpu":
            return "cpu"
        if torch.cuda.is_available():
            return "cuda"
        if has_mps:
            return "mps"
        return "cpu"

    def _describe_device(self, device: str) -> str:
        import torch

        if device.startswith("cuda"):
            idx = torch.cuda.current_device() if device == "cuda" else int(device.split(":")[1])
            props = torch.cuda.get_device_properties(idx)
            return f"{props.name} ({round(props.total_memory / 1024 ** 3, 1)} GB, CUDA {torch.version.cuda})"
        if device == "mps":
            return "Apple Silicon GPU (Metal / MPS)"
        return f"CPU ({os.cpu_count()} threads)"

    def _ensure_weights(self) -> bool:
        if model_files_present(self.s.model_dir):
            return True
        if os.environ.get("VOXCPM_AUTO_DOWNLOAD", "1").strip().lower() in ("0", "false", "no", "off"):
            self.status = "no_model"
            self.error = (f"VoxCPM2 weights not found in {self.s.model_dir}. Run the installer again, or "
                          f"'python scripts/download_model.py'.")
            log(self.error)
            return False
        self.status = "downloading"
        log(f"Weights not found in {self.s.model_dir}; downloading {HF_REPO} (~5 GB, one time only)...")
        try:
            from huggingface_hub import snapshot_download

            snapshot_download(repo_id=HF_REPO, local_dir=str(self.s.model_dir))
        except Exception as exc:  # noqa: BLE001
            self.status = "no_model"
            self.error = f"Downloading the model failed: {exc}. Check your connection, then restart."
            log(self.error)
            return False
        return model_files_present(self.s.model_dir)

    def _load(self) -> None:
        t0 = time.time()
        self._ready.clear()
        self.model = None
        self.error = None
        try:
            import torch  # noqa: F401
        except ImportError:
            self.status = "error"
            self.error = "PyTorch is not installed in this environment. Re-run the installer."
            log(self.error)
            return
        if not self._ensure_weights():
            return
        self.status = "loading"
        try:
            from voxcpm import VoxCPM
        except ImportError as exc:
            self.status = "error"
            self.error = f"The 'voxcpm' package could not be imported ({exc}). Re-run the installer."
            log(self.error)
            return
        try:
            device = self._pick_device()
            self.device = device
            self.device_label = self._describe_device(device)
            want_compile = bool(self.optimize and device.startswith("cuda"))
            log(f"Loading VoxCPM2 from {self.s.model_dir} on {self.device_label}"
                f"{' with torch.compile' if want_compile else ''}...")
            try:
                self.model = VoxCPM.from_pretrained(str(self.s.model_dir), load_denoiser=False,
                                                    optimize=want_compile, device=device)
            except Exception as exc:  # noqa: BLE001
                if not want_compile:
                    raise
                log(f"torch.compile failed ({exc}); loading without it.")
                self.optimize = False
                self.model = VoxCPM.from_pretrained(str(self.s.model_dir), load_denoiser=False,
                                                    optimize=False, device=device)
            if device.startswith("cuda"):
                self._keep_vae_float32()
            self.status = "ready"
            self.load_seconds = round(time.time() - t0, 1)
            self._ready.set()
            log(f"Ready on {self.device_label} in {self.load_seconds}s (sample rate {self.sample_rate} Hz).")
        except Exception as exc:  # noqa: BLE001
            self.model = None
            self.status = "error"
            msg = str(exc)
            if "out of memory" in msg.lower():
                msg += " - not enough GPU memory. Close other GPU apps or start with --device cpu."
            self.error = f"Model load failed: {msg}"
            log(self.error)

    def _keep_vae_float32(self) -> None:
        """Keep the AudioVAE in float32 under bf16 weights (avoids 'unsupported ScalarType BFloat16')."""
        try:
            import torch
        except ImportError:
            return
        roots = [self.model, getattr(self.model, "tts_model", None)]
        for root in roots:
            if root is None:
                continue
            for name in ("audio_vae", "audiovae", "vae"):
                vae = getattr(root, name, None)
                if not isinstance(vae, torch.nn.Module) or getattr(vae, "_vox_fp32", False):
                    continue
                vae.to(dtype=torch.float32)
                for method in ("decode", "encode"):
                    orig = getattr(vae, method, None)
                    if orig is None:
                        continue

                    def wrapped(x, *a, _orig=orig, **kw):
                        with torch.autocast(device_type="cuda", enabled=False):
                            if isinstance(x, torch.Tensor) and x.is_floating_point() and x.dtype != torch.float32:
                                x = x.float()
                            return _orig(x, *a, **kw)

                    setattr(vae, method, wrapped)
                vae._vox_fp32 = True

    def switch_device(self, device: str) -> Tuple[int, Dict]:
        device = (device or "").lower().strip()
        if device not in ("auto", "cpu", "cuda", "mps") and not device.startswith("cuda:"):
            return 400, {"error": "device must be auto, cuda, cuda:N, mps or cpu"}
        self.preferred_device = device

        def reload():
            with self._gen_lock:
                self.model = None
                self._ready.clear()
                self.status = "loading"
                gc.collect()
                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001
                    pass
            self._load()

        self._load_started.set()
        self._executor.submit(reload)
        return 202, {"message": f"Reloading the model on {device.upper()}..."}

    # ---- info ----------------------------------------------------------------------------------
    @property
    def sample_rate(self) -> int:
        for obj in (self.model, getattr(self.model, "tts_model", None)):
            sr = getattr(obj, "sample_rate", None) if obj is not None else None
            if isinstance(sr, (int, float)) and sr > 0:
                return int(sr)
        return DEFAULT_SR

    def info(self) -> Dict:
        gpu: Dict = {}
        try:
            import torch

            gpu["torch"] = torch.__version__
            gpu["cuda_available"] = torch.cuda.is_available()
            gpu["mps_available"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
            if self.device.startswith("cuda") and torch.cuda.is_available():
                idx = torch.cuda.current_device()
                total = torch.cuda.get_device_properties(idx).total_memory
                gpu["vram_total_mb"] = round(total / 1024 ** 2)
                gpu["vram_used_mb"] = round(torch.cuda.memory_reserved(idx) / 1024 ** 2)
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": self.status,
            "ready": self.model is not None,
            "error": self.error,
            "device": self.device,
            "device_label": self.device_label,
            "preferred_device": self.preferred_device,
            "optimized": bool(self.optimize and self.device.startswith("cuda")),
            "sample_rate": self.sample_rate,
            "model_dir": str(self.s.model_dir),
            "model_present": model_files_present(self.s.model_dir),
            "load_seconds": self.load_seconds,
            "uptime_seconds": round(time.time() - self.started_at),
            "stats": dict(self.stats, audio_seconds=round(self.stats["audio_seconds"], 1),
                          compute_seconds=round(self.stats["compute_seconds"], 1)),
            "gpu": gpu,
        }

    # ---- references ----------------------------------------------------------------------------
    def ensure_reference(self, voice: Dict) -> Tuple[Optional[str], Optional[str]]:
        """(wav path, transcript) to clone from; designs the reference once if the voice has none."""
        path = self.voices.reference_path(voice["id"])
        if path:
            return str(path), self.voices.reference_transcript(voice["id"])
        design = (voice.get("design") or "").strip()
        if not design or self.model is None:
            return None, None
        log(f"Designing the reference recording for '{voice['name']}' (one time only)...")
        self.design_reference(voice["id"], design)
        path = self.voices.reference_path(voice["id"])
        return (str(path), self.voices.reference_transcript(voice["id"])) if path else (None, None)

    def design_reference(self, voice_id: str, design: str, transcript: Optional[str] = None,
                         cfg_value: Optional[float] = None, steps: Optional[int] = None) -> Dict:
        """Create a voice from a description: generate a calibration clip and store it as the reference."""
        text = " ".join(str(transcript or "").split()) or CALIBRATION_TEXT

        def work():
            with self._gen_lock:
                audio = self._generate_one(f"({design.strip()[:300]}){text}",
                                           cfg_value or self.s.cfg_value, steps or self.s.inference_timesteps)
            return audio

        audio = self.run_on_infer(work)
        if audio is None or len(audio) == 0:
            raise RuntimeError("The model could not generate the reference audio.")
        sr = self.sample_rate
        audio = dsp.limit_peak(audio)
        self.voices.save_reference(voice_id, to_wav_bytes(audio, sr), text)
        return {"transcript": text, "duration": round(len(audio) / sr, 2), "sample_rate": sr}

    # ---- generation ----------------------------------------------------------------------------
    def _generate_one(self, text: str, cfg: float, steps: int, **refs) -> Optional[np.ndarray]:
        """One model call; must run on the inference thread."""
        import torch

        kwargs = {"text": text, "cfg_value": float(cfg), "inference_timesteps": int(steps)}
        kwargs.update({k: v for k, v in refs.items() if v is not None})
        for attempt in (1, 2):
            try:
                with torch.inference_mode():
                    result = self.model.generate(**kwargs)
                return np.asarray(result, dtype=np.float32).reshape(-1)
            except Exception as exc:  # noqa: BLE001
                log(f"Generation error on {self.device}: {exc}")
                if attempt == 1 and self._recover_from_compile_failure(exc):
                    continue
                if "out of memory" in str(exc).lower():
                    try:
                        torch.cuda.empty_cache()
                    except Exception:  # noqa: BLE001
                        pass
                return None
            finally:
                if self.device == "mps":
                    try:
                        torch.mps.empty_cache()
                    except Exception:  # noqa: BLE001
                        pass
        return None

    def _recover_from_compile_failure(self, exc: Exception) -> bool:
        kind = f"{type(exc).__module__}.{type(exc).__name__} {exc}".lower()
        if not self.optimize or not any(k in kind for k in ("dynamo", "inductor", "triton", "cudagraph", "compile")):
            return False
        log("Optimised model failed at run time; reloading without torch.compile...")
        self.optimize = False
        self._load()
        return self.model is not None

    def _piece_request(self, piece: Piece, voice: Dict, cfg: float, steps: int, style_prompt: str = ""):
        """Text with its style prefix, the reference kwargs and the cfg to use for one piece."""
        ref_path, ref_text = self.ensure_reference(voice)
        style_parts = []
        if not ref_path and voice.get("design"):
            style_parts.append(voice["design"])  # no reference could be made: describe the voice
        if piece.emotion and piece.emotion != "neutral":
            style_parts.append(EMOTION_STYLES.get(piece.emotion, f"{piece.emotion} tone"))
        if piece.style:
            style_parts.append(piece.style)
        if style_prompt.strip():
            style_parts.append(style_prompt.strip())
        text = piece.text
        if style_parts and not text.startswith("("):
            text = f"({', '.join(style_parts)}){text}"
        refs = {}
        if ref_path:
            refs["reference_wav_path"] = ref_path
            if ref_text and not style_parts:
                # exact transcript and no style override: continue from the prompt for best fidelity
                refs["prompt_wav_path"] = ref_path
                refs["prompt_text"] = ref_text
        emotional = piece.marked and (bool(piece.style) or piece.emotion not in ("neutral", "whisper"))
        return text, refs, (max(cfg, 3.0) if emotional else cfg)

    def synthesize(self, text: str, voice_id: Optional[str] = None, emotion: str = "neutral",
                   style_prompt: str = "", speed: float = 1.0, pitch: float = 0.0,
                   cfg_value: Optional[float] = None, steps: Optional[int] = None) -> Tuple[np.ndarray, int, Dict]:
        """Full synthesis of text (markers allowed). Returns (audio float32, sample_rate, meta)."""
        if self.model is None:
            raise RuntimeError(self.error or f"The model is not ready ({self.status}).")
        voice = self.voices.resolve(voice_id)
        plan = plan_text(text, emotion, self.s.max_chars_per_piece)
        if not plan.pieces:
            raise ValueError("There is no speakable text.")
        cfg = float(cfg_value if cfg_value is not None else self.s.cfg_value)
        n_steps = int(steps if steps is not None else self.s.inference_timesteps)

        def work():
            t0 = time.time()
            sr = self.sample_rate
            parts, prev = [], ""
            for piece in plan.pieces:
                with self._gen_lock:
                    gen_text, refs, piece_cfg = self._piece_request(piece, voice, cfg, n_steps, style_prompt)
                    audio = self._generate_one(gen_text, piece_cfg, n_steps, **refs)
                    if audio is None or len(audio) == 0:
                        audio = self._generate_one(gen_text, piece_cfg, n_steps, **refs)  # one retry
                if audio is None or len(audio) == 0:
                    if not parts:
                        raise RuntimeError("The model could not generate this text.")
                    log(f"Skipped a piece the model could not generate: '{piece.text[:40]}...'")
                    continue
                gap = piece.pause_before_ms or (0 if not parts else (140 if prev.rstrip()[-1:] in ".!?\u2026" else 40))
                parts.append(dsp.silence(sr, gap))
                parts.append(dsp.fade_edges(audio, sr))
                prev = piece.text
            parts.append(dsp.silence(sr, plan.trailing_pause_ms))
            out = dsp.apply_speed_and_pitch(dsp.concat(parts), sr, speed, pitch)
            out = dsp.limit_peak(out)
            elapsed = time.time() - t0
            return out, sr, elapsed

        audio, sr, elapsed = self.run_on_infer(work)
        duration = len(audio) / sr
        self.stats["requests"] += 1
        self.stats["audio_seconds"] += duration
        self.stats["compute_seconds"] += elapsed
        log(f"Spoke {duration:.1f}s of audio in {elapsed:.1f}s (RTF {elapsed / max(duration, 0.01):.2f}) "
            f"| voice '{voice['name']}' | {len(plan.pieces)} piece(s)")
        return audio, sr, {"voice": voice, "pieces": len(plan.pieces), "duration": round(duration, 2),
                           "elapsed": round(elapsed, 2)}

    def stream(self, text: str, voice_id: Optional[str] = None, emotion: str = "neutral",
               style_prompt: str = "", cfg_value: Optional[float] = None, steps: Optional[int] = None,
               stop: Optional[threading.Event] = None) -> Iterator[np.ndarray]:
        """float32 chunks as the model produces them (speed/pitch are not applied while streaming)."""
        if self.model is None:
            raise RuntimeError(self.error or f"The model is not ready ({self.status}).")
        voice = self.voices.resolve(voice_id)
        plan = plan_text(text, emotion, self.s.max_chars_per_piece)
        if not plan.pieces:
            raise ValueError("There is no speakable text.")
        cfg = float(cfg_value if cfg_value is not None else self.s.cfg_value)
        n_steps = int(steps if steps is not None else self.s.inference_timesteps)
        stop = stop or threading.Event()

        def factory():
            import torch

            sr = self.sample_rate
            prev = ""
            for i, piece in enumerate(plan.pieces):
                gap = piece.pause_before_ms or (0 if i == 0 else (140 if prev.rstrip()[-1:] in ".!?\u2026" else 40))
                if gap:
                    yield dsp.silence(sr, gap)
                with self._gen_lock:
                    gen_text, refs, piece_cfg = self._piece_request(piece, voice, cfg, n_steps, style_prompt)
                    kwargs = {"text": gen_text, "cfg_value": piece_cfg, "inference_timesteps": n_steps,
                              "retry_badcase": False, **refs}
                    with torch.inference_mode():
                        gen = self.model.generate_streaming(**kwargs)
                        try:
                            for chunk in gen:
                                if stop.is_set():
                                    return
                                yield np.clip(np.asarray(chunk, dtype=np.float32).reshape(-1), -1.0, 1.0)
                        finally:
                            gen.close()
                prev = piece.text
            if plan.trailing_pause_ms:
                yield dsp.silence(sr, plan.trailing_pause_ms)

        self.stats["requests"] += 1
        return self.stream_on_infer(factory, stop)


def preflight_summary() -> str:
    """One line about Python/torch for the start-up banner."""
    try:
        import torch

        accel = "CUDA" if torch.cuda.is_available() else (
            "MPS" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "CPU only")
        return f"Python {sys.version.split()[0]} | PyTorch {torch.__version__} | {accel}"
    except ImportError:
        return f"Python {sys.version.split()[0]} | PyTorch NOT installed"
