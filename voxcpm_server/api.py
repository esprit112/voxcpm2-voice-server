"""HTTP API (standard library only).

OpenAI-compatible:
  POST /v1/audio/speech        {"model","input","voice","response_format","speed"} + extras below
  GET  /v1/models
  GET  /v1/audio/voices        (also /v1/voices)

Server API (used by the web UI and the agent integrations):
  GET  /health                 status, device, model state
  GET  /api/voices             full voice list
  POST /api/voices/design      {"name","description","transcript"?,"gender"?,"language"?}
  POST /api/voices/clone       {"name","audio_base64","transcript"?,"description"?}
  PATCH  /api/voices/<id>      {"name"?,"description"?}
  DELETE /api/voices/<id>
  POST /api/voices/restore     bring back hidden presets
  GET  /api/voices/<id>/reference.wav
  POST /api/stream             same body as /v1/audio/speech; raw float32 mono PCM as it is produced
  POST /api/device             {"device": "auto|cuda|mps|cpu"}

Extra speech fields: emotion, style (free-form instruction), pitch (semitones), cfg_value,
inference_timesteps. Inline [emotion] markers in the text are honoured.
"""

from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.parse import unquote, urlparse

from . import __version__
from .audio_io import SUPPORTED_FORMATS, decode_upload, encode, prepare_reference, to_wav_bytes
from .config import WEB_DIR, Settings
from .engine import Engine
from .markers import strip_markers
from .voices import VoiceLibrary, is_safe_id

MAX_BODY = 40 * 1024 * 1024  # clone uploads are base64 audio
LOAD_WAIT_SECONDS = 900      # a speech request that arrives while the model loads waits for it
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


class ApiError(Exception):
    def __init__(self, status: int, message: str, kind: str = "invalid_request_error"):
        super().__init__(message)
        self.status, self.message, self.kind = status, message, kind


def make_handler(settings: Settings, engine: Engine, library: VoiceLibrary):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"VoxCPM2-Voice-Server/{__version__}"
        protocol_version = "HTTP/1.1"

        # ---- plumbing --------------------------------------------------------------------------
        def log_message(self, fmt, *args):  # quiet: only errors and non-poll requests
            line = str(args[0]) if args else ""
            code = str(args[1]) if len(args) > 1 else ""
            if code.startswith(("2", "3")) and any(p in line for p in ("/health", "GET / ", "/api/voices ", "/favicon")):
                return
            print(f"[HTTP] {self.address_string()} {line} -> {code}", flush=True)

        def _origin_allowed(self) -> Optional[str]:
            origin = self.headers.get("Origin")
            if not origin:
                return None
            if "*" in settings.cors_origins or origin in settings.cors_origins:
                return origin
            host = urlparse(origin).hostname or ""
            if host in LOCAL_HOSTS or host.endswith(".localhost"):
                return origin
            if urlparse(origin).netloc == self.headers.get("Host", ""):
                return origin
            return ""

        def _cors(self):
            allowed = self._origin_allowed()
            if allowed:
                self.send_header("Access-Control-Allow-Origin", allowed)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-API-Key")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
                self.send_header("Access-Control-Expose-Headers",
                                 "X-Sample-Rate, X-Audio-Format, X-Audio-Duration, X-Voice-Id, X-Voice-Name")

        def _send(self, status: int, body: bytes, content_type: str, headers: Optional[Dict] = None):
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, str(v))
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        def _json(self, status: int, payload, headers: Optional[Dict] = None):
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", headers)

        def _error(self, err: ApiError):
            self.close_connection = True  # the request body may be unread; never reuse this connection
            self._json(err.status, {"error": {"message": err.message, "type": err.kind}})

        def _body(self) -> Dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(413, "Request body too large (max 40 MB).")
            raw = self.rfile.read(length) if length > 0 else b""
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                raise ApiError(400, "Body must be JSON.")
            if not isinstance(data, dict):
                raise ApiError(400, "Body must be a JSON object.")
            return data

        def _authorized(self) -> bool:
            if not settings.api_key:
                return True
            auth = self.headers.get("Authorization", "")
            key = auth[7:].strip() if auth.lower().startswith("bearer ") else self.headers.get("X-API-Key", "")
            return hmac.compare_digest(key.encode(), settings.api_key.encode())

        def _route(self, method: str):
            path = urlparse(self.path).path.rstrip("/") or "/"
            try:
                if method == "OPTIONS":
                    self.send_response(204)
                    self._cors()
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if method in ("GET", "HEAD") and path in ("/", "/index.html", "/favicon.ico", "/favicon.svg"):
                    return self._static(path)
                if path == "/health":
                    return self._json(200, self._health())
                if (path.startswith("/v1/") or path.startswith("/api/")) and not self._authorized():
                    raise ApiError(401, "Missing or wrong API key (send 'Authorization: Bearer <key>').",
                                   "authentication_error")
                handler = ROUTES.get((method, path))
                if handler:
                    return handler(self)
                if path.startswith("/api/voices/"):
                    return self._voice_item(method, path[len("/api/voices/"):])
                raise ApiError(404, f"No route for {method} {path}")
            except ApiError as err:
                self._error(err)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._error(ApiError(500, f"Internal error: {exc}", "server_error"))

        def do_GET(self):
            self._route("GET")

        def do_HEAD(self):
            self._route("HEAD")

        def do_POST(self):
            self._route("POST")

        def do_PATCH(self):
            self._route("PATCH")

        def do_DELETE(self):
            self._route("DELETE")

        def do_OPTIONS(self):
            self._route("OPTIONS")

        # ---- pages -----------------------------------------------------------------------------
        def _static(self, path: str):
            if path in ("/favicon.ico", "/favicon.svg"):
                svg = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" '
                       b'rx="14" fill="#7c5cff"/><path d="M14 34h6l4-12 8 26 6-18 4 8h8" fill="none" '
                       b'stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>')
                return self._send(200, svg, "image/svg+xml", {"Cache-Control": "max-age=86400"})
            page = WEB_DIR / "index.html"
            if not page.is_file():
                raise ApiError(404, "web/index.html is missing from this install.")
            self._send(200, page.read_bytes(), mimetypes.types_map.get(".html", "text/html") + "; charset=utf-8",
                       {"Cache-Control": "no-cache"})

        def _health(self) -> Dict:
            return dict(engine.info(), version=__version__, auth_required=bool(settings.api_key),
                        default_voice=library.default_id(), formats=list(SUPPORTED_FORMATS))

        # ---- speech ----------------------------------------------------------------------------
        def _speech_args(self, body: Dict) -> Dict:
            text = body.get("input") if body.get("input") is not None else body.get("text")
            text = str(text or "")
            if not text.strip():
                raise ApiError(400, "Missing or empty 'input'.")
            if len(text) > settings.max_input_chars:
                raise ApiError(400, f"Input is longer than {settings.max_input_chars} characters.")

            def num(*names, default=None, lo=None, hi=None):
                for n in names:
                    if body.get(n) is not None:
                        try:
                            v = float(body[n])
                        except (TypeError, ValueError):
                            raise ApiError(400, f"'{n}' must be a number.")
                        if lo is not None:
                            v = max(lo, v)
                        if hi is not None:
                            v = min(hi, v)
                        return v
                return default

            steps = num("inference_timesteps", "inferenceTimesteps", "steps", lo=4, hi=50)
            return {
                "text": text,
                "voice_id": body.get("voice") or body.get("voice_id") or body.get("voiceId"),
                "emotion": str(body.get("emotion") or "neutral"),
                "style_prompt": str(body.get("style") or body.get("instructions") or body.get("style_prompt") or ""),
                "cfg_value": num("cfg_value", "cfgValue", "cfg", lo=1.0, hi=5.0),
                "steps": int(steps) if steps is not None else None,
            }

        def _wait_model(self):
            if engine.model is not None:
                return
            if engine.status in ("no_model", "error"):
                raise ApiError(503, engine.error or "The model is not available.", "service_unavailable")
            if not engine.wait_ready(LOAD_WAIT_SECONDS):
                raise ApiError(503, engine.error or f"The model is still {engine.status}; try again shortly.",
                               "service_unavailable")

        def speech(self):
            body = self._body()
            args = self._speech_args(body)
            speed = float(body.get("speed") or 1.0)
            pitch = float(body.get("pitch") or body.get("pitchSemitones") or 0.0)
            fmt = str(body.get("response_format") or "mp3").lower()
            self._wait_model()
            try:
                audio, sr, meta = engine.synthesize(speed=max(0.25, min(4.0, speed)), pitch=max(-12, min(12, pitch)),
                                                    **args)
            except ValueError as exc:
                raise ApiError(400, str(exc))
            except RuntimeError as exc:
                raise ApiError(502, str(exc), "server_error")
            data, ctype, used = encode(audio, sr, fmt)
            self._send(200, data, ctype, {
                "X-Sample-Rate": sr, "X-Audio-Format": used, "X-Audio-Duration": meta["duration"],
                "X-Voice-Id": meta["voice"]["id"], "X-Voice-Name": meta["voice"]["name"].encode("ascii", "replace").decode(),
            })

        def stream(self):
            body = self._body()
            args = self._speech_args(body)
            self._wait_model()
            stop = threading.Event()
            try:
                chunks = engine.stream(stop=stop, **args)
                first = next(chunks, None)  # surface errors before committing to a 200
            except ValueError as exc:
                stop.set()
                raise ApiError(400, str(exc))
            except RuntimeError as exc:
                stop.set()
                raise ApiError(502, str(exc), "server_error")
            voice = library.resolve(args["voice_id"])
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("X-Audio-Format", "f32le")
            self.send_header("X-Sample-Rate", str(engine.sample_rate))
            self.send_header("X-Voice-Id", voice["id"])
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def write_chunk(payload: bytes):
                self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()

            try:
                if first is not None and len(first):
                    write_chunk(first.tobytes())
                for chunk in chunks:
                    if len(chunk):
                        write_chunk(chunk.tobytes())
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # listener pressed stop
            finally:
                stop.set()
                self.close_connection = True

        # ---- catalog ---------------------------------------------------------------------------
        def models(self):
            created = 1735689600
            base = {"object": "model", "created": created, "owned_by": "openbmb"}
            self._json(200, {"object": "list", "data": [
                dict(base, id="voxcpm2"), dict(base, id="tts-1"), dict(base, id="tts-1-hd"),
                dict(base, id="gpt-4o-mini-tts"),
            ]})

        def openai_voices(self):
            voices = library.all()
            self._json(200, {"voices": [{
                "voice_id": v["id"], "id": v["id"], "name": v["name"], "label": v["name"],
                "language": v.get("language", ""), "gender": v.get("gender", ""),
                "description": v.get("description", ""), "default": v["default"],
                "preview_url": f"/api/voices/{v['id']}/reference.wav",
            } for v in voices]})

        def list_voices(self):
            self._json(200, {"voices": library.all(), "default": library.default_id()})

        def design_voice(self):
            body = self._body()
            description = " ".join(str(body.get("description") or "").split())
            if len(description) < 8:
                raise ApiError(400, "Describe the voice in a sentence (age, gender, accent, tone...).")
            self._wait_model()
            voice = library.add(name=body.get("name") or "Designed voice", kind="designed",
                                description=description, design=description[:300],
                                gender=str(body.get("gender") or ""), language=str(body.get("language") or ""))
            try:
                info = engine.design_reference(voice["id"], description, body.get("transcript"),
                                               body.get("cfg_value"), body.get("inference_timesteps"))
            except Exception as exc:  # noqa: BLE001
                library.delete(voice["id"])
                raise ApiError(502, f"The model could not design this voice: {exc}", "server_error")
            self._json(200, {"voice": library.get(voice["id"]), "reference": info})

        def clone_voice(self):
            body = self._body()
            b64 = str(body.get("audio_base64") or "")
            if "," in b64[:100] and b64.startswith("data:"):
                b64 = b64.split(",", 1)[1]
            try:
                raw = base64.b64decode(b64, validate=False)
            except ValueError:
                raise ApiError(400, "audio_base64 is not valid base64.")
            if len(raw) < 2000:
                raise ApiError(400, "Upload a recording of 5-30 seconds (WAV, FLAC, OGG or MP3).")
            try:
                audio, sr = decode_upload(raw)
            except Exception as exc:  # noqa: BLE001
                raise ApiError(400, f"Could not read the recording: {exc}")
            audio = prepare_reference(audio, sr)
            seconds = len(audio) / max(sr, 1)
            if seconds < 2.0:
                raise ApiError(400, f"The recording is only {seconds:.1f}s of sound; use 5-30 seconds of clear speech.")
            transcript = " ".join(str(body.get("transcript") or "").split()) or None
            voice = library.add(name=body.get("name") or "Cloned voice", kind="cloned",
                                description=str(body.get("description") or "Cloned from a recording"),
                                gender=str(body.get("gender") or ""), language=str(body.get("language") or ""))
            library.save_reference(voice["id"], to_wav_bytes(audio, sr), transcript)
            self._json(200, {"voice": library.get(voice["id"]), "reference_seconds": round(seconds, 1),
                             "transcript_used": bool(transcript)})

        def restore_presets(self):
            self._json(200, {"restored": library.restore_presets()})

        def _voice_item(self, method: str, rest: str):
            rest = unquote(rest)
            if rest.endswith("/reference.wav") and method in ("GET", "HEAD"):
                vid = rest[: -len("/reference.wav")]
                if not is_safe_id(vid):
                    raise ApiError(400, "Invalid voice id.")
                voice = library.get(vid)
                if not voice:
                    raise ApiError(404, "Voice not found.")
                path = library.reference_path(vid)
                if path is None:
                    self._wait_model()
                    engine.run_on_infer(lambda: engine.ensure_reference(voice))
                    path = library.reference_path(vid)
                if path is None:
                    raise ApiError(404, "This voice has no reference recording yet.")
                return self._send(200, path.read_bytes(), "audio/wav", {"Cache-Control": "no-cache"})
            if not is_safe_id(rest):
                raise ApiError(400, "Invalid voice id.")
            if method == "GET":
                voice = library.get(rest)
                if not voice:
                    raise ApiError(404, "Voice not found.")
                return self._json(200, voice)
            if method == "DELETE":
                ok, msg = library.delete(rest)
                if not ok:
                    raise ApiError(409 if "last" in msg else 404, msg)
                return self._json(200, {"deleted": rest, "result": msg, "default": library.default_id()})
            if method == "PATCH":
                body = self._body()
                voice = library.update(rest, name=body.get("name"), description=body.get("description"))
                if not voice:
                    raise ApiError(404, "Only your own (designed or cloned) voices can be edited.")
                return self._json(200, voice)
            raise ApiError(405, "Method not allowed.")

        def device(self):
            body = self._body()
            status, payload = engine.switch_device(str(body.get("device") or ""))
            self._json(status, payload)

        def plain_text(self):
            body = self._body()
            self._json(200, {"text": strip_markers(str(body.get("input") or body.get("text") or ""))})

    ROUTES = {
        ("POST", "/v1/audio/speech"): Handler.speech,
        ("POST", "/api/stream"): Handler.stream,
        ("POST", "/v1/audio/speech/stream"): Handler.stream,
        ("GET", "/v1/models"): Handler.models,
        ("GET", "/v1/audio/voices"): Handler.openai_voices,
        ("GET", "/v1/voices"): Handler.openai_voices,
        ("GET", "/api/voices"): Handler.list_voices,
        ("POST", "/api/voices/design"): Handler.design_voice,
        ("POST", "/api/voices/clone"): Handler.clone_voice,
        ("POST", "/api/voices/restore"): Handler.restore_presets,
        ("POST", "/api/device"): Handler.device,
        ("POST", "/api/plain-text"): Handler.plain_text,
    }
    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # POSIX: allow a quick restart while old sockets sit in TIME_WAIT. Windows: SO_REUSEADDR would let
    # a second copy share the port silently, so it stays off and a duplicate start fails loudly.
    allow_reuse_address = os.name != "nt"

    def handle_error(self, request, client_address):
        # A client closing its keep-alive connection (curl, browsers, agents) is normal; only print real errors.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def serve(settings: Settings, engine: Engine, library: VoiceLibrary) -> Server:
    return Server((settings.host, settings.port), make_handler(settings, engine, library))
