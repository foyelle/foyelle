"""Speech to text on this computer: a small HTTP server in front of sherpa-onnx.

The canvas posts a recording to ``/inference`` as multipart form data (field ``file``, a WAV),
the request whisper.cpp's server takes, and gets ``{"text": ...}`` back. The model (Parakeet
Ultra, int8) works out the language by itself. The server listens on loopback only, and the
recording is decoded in memory and never written anywhere.
"""

from __future__ import annotations

import argparse
import array
import io
import json
import sys
import threading
import wave
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol

MAX_BYTES = 8 * 1024 * 1024  # two minutes of 16 kHz 16-bit mono is under 4 MB
MAX_SECONDS = 120
LOOPBACK = ("127.0.0.1", "localhost")


class Recognizer(Protocol):
    def transcribe(self, sample_rate: int, samples: list[float]) -> str: ...


class AudioError(ValueError):
    """The request held no audio this server can read."""


def wav_samples(data: bytes) -> tuple[int, list[float]]:
    """16-bit PCM WAV -> (sample rate, mono samples in [-1, 1])."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            rate, channels, width, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            if width != 2:
                raise AudioError("only 16-bit PCM WAV is accepted")
            if frames > rate * MAX_SECONDS:
                raise AudioError(f"recordings are limited to {MAX_SECONDS} seconds")
            pcm = array.array("h", w.readframes(frames))
    except (wave.Error, EOFError) as exc:
        raise AudioError(f"not a WAV file ({exc})") from exc
    if sys.byteorder == "big":
        pcm.byteswap()
    if channels > 1:
        return rate, [sum(pcm[i : i + channels]) / channels / 32768.0 for i in range(0, len(pcm), channels)]
    return rate, [s / 32768.0 for s in pcm]


def audio_from_request(body: bytes, content_type: str) -> bytes:
    """The WAV bytes from a multipart form (field ``file``) or a raw ``audio/wav`` body."""
    if content_type.split(";")[0].strip().lower() in ("audio/wav", "audio/x-wav", "audio/wave"):
        return body
    if not content_type.lower().startswith("multipart/form-data"):
        raise AudioError("send multipart/form-data with a 'file' field, or audio/wav")
    message = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") == "file":
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes) and payload:
                return payload
    raise AudioError("the form has no 'file' field")


class SherpaRecognizer:
    """Parakeet (NeMo transducer, int8 ONNX) through sherpa-onnx."""

    def __init__(self, model_dir: Path, threads: int = 4) -> None:
        import sherpa_onnx  # only in the image's speech service, not in the connectors' own deps

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model_dir / "encoder.int8.onnx"),
            decoder=str(model_dir / "decoder.int8.onnx"),
            joiner=str(model_dir / "joiner.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            model_type="nemo_transducer",
            num_threads=threads,
        )

    def transcribe(self, sample_rate: int, samples: list[float]) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()


def make_handler(recognizer: Recognizer, max_bytes: int = MAX_BYTES) -> type[BaseHTTPRequestHandler]:
    lock = threading.Lock()  # one recording at a time: the model uses every thread it has

    class Handler(BaseHTTPRequestHandler):
        server_version = "foyelle-speech"

        def log_message(self, fmt: str, *args: object) -> None:  # no request logs: they would say when you spoke
            pass

        def reply(self, code: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self.reply(200, {"ok": True})
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path.split("?")[0] != "/inference":
                self.reply(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length <= 0:
                self.reply(411, {"error": "a Content-Length is required"})
                return
            if length > max_bytes:
                self.reply(413, {"error": f"recordings are limited to {max_bytes} bytes"})
                return
            body = self.rfile.read(length)
            try:
                rate, samples = wav_samples(audio_from_request(body, self.headers.get("Content-Type", "")))
            except AudioError as exc:
                self.reply(400, {"error": str(exc)})
                return
            if not samples:
                self.reply(200, {"text": ""})
                return
            with lock:
                text = recognizer.transcribe(rate, samples)
            self.reply(200, {"text": text})

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def serve(recognizer: Recognizer, host: str, port: int) -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise SystemExit(f"refusing to listen on {host}: the speech service is for this computer only")
    return _Server((host, port), make_handler(recognizer))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7310)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    server = serve(SherpaRecognizer(args.model_dir, args.threads), args.host, args.port)
    print(f"foyelle-speech: listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
