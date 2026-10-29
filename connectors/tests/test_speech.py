from __future__ import annotations

import http.client
import io
import json
import threading
import urllib.error
import urllib.request
import wave

import pytest

from foyelle_connectors import speech


def wav(samples: list[int], rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"".join(int(s).to_bytes(width, "little", signed=True) for s in samples))
    return buf.getvalue()


def multipart(fields: dict[str, tuple[str | None, bytes]], boundary: str = "----foyelle") -> tuple[bytes, str]:
    """What the canvas's FormData sends: a file field plus plain fields."""
    out = b""
    for name, (filename, data) in fields.items():
        disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        head = f"--{boundary}\r\nContent-Disposition: {disp}\r\n"
        head += "Content-Type: audio/wav\r\n" if filename else ""
        out += head.encode() + b"\r\n" + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


class FakeRecognizer:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def transcribe(self, sample_rate: int, samples: list[float]) -> str:
        self.calls.append((sample_rate, len(samples)))
        return "what is in my documents folder?"


@pytest.fixture
def server():
    fake = FakeRecognizer()
    srv = speech.serve(fake, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", fake
    srv.shutdown()


def post(url: str, body: bytes, content_type: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_wav_samples_reads_mono_and_mixes_stereo() -> None:
    rate, s = speech.wav_samples(wav([0, 16384, -32768]))
    assert rate == 16000 and s == [0.0, 0.5, -1.0]
    rate, s = speech.wav_samples(wav([16384, -16384, 8192, 8192], rate=44100, channels=2))
    assert rate == 44100 and s == [0.0, 0.25]
    with pytest.raises(speech.AudioError):
        speech.wav_samples(b"RIFF nonsense")
    with pytest.raises(speech.AudioError):
        speech.wav_samples(wav([1, 2], width=1))
    assert len(speech.wav_samples(wav([0] * (8 * speech.MAX_SECONDS), rate=8))[1]) == 8 * speech.MAX_SECONDS
    with pytest.raises(speech.AudioError):
        speech.wav_samples(wav([0] * (8 * speech.MAX_SECONDS + 1), rate=8))


def test_audio_from_a_form_or_a_raw_body() -> None:
    body, ct = multipart({"file": ("speech.wav", b"WAVDATA"), "response_format": (None, b"json")})
    assert speech.audio_from_request(body, ct) == b"WAVDATA"
    assert speech.audio_from_request(b"RAW", "audio/wav") == b"RAW"
    body, ct = multipart({"other": ("x.wav", b"X")})
    with pytest.raises(speech.AudioError):
        speech.audio_from_request(body, ct)
    with pytest.raises(speech.AudioError):
        speech.audio_from_request(b"{}", "application/json")


def test_inference_answers_like_whisper_cpp(server) -> None:
    url, fake = server
    body, ct = multipart({"file": ("speech.wav", wav([0] * 1600)), "response_format": (None, b"json"), "temperature": (None, b"0")})
    assert post(url + "/inference", body, ct) == (200, {"text": "what is in my documents folder?"})
    assert fake.calls == [(16000, 1600)]
    assert post(url + "/inference", wav([0] * 16), "audio/wav")[0] == 200


def test_bad_requests(server) -> None:
    url, fake = server
    assert post(url + "/inference", b"not audio", "audio/wav")[0] == 400
    assert post(url + "/elsewhere", b"x", "audio/wav")[0] == 404
    # Too large: refused from the headers, before the recording is read.
    conn = http.client.HTTPConnection(url.removeprefix("http://"), timeout=5)
    conn.putrequest("POST", "/inference")
    conn.putheader("Content-Type", "audio/wav")
    conn.putheader("Content-Length", str(speech.MAX_BYTES + 1))
    conn.endheaders()
    assert conn.getresponse().status == 413
    conn.close()
    body, ct = multipart({"file": ("speech.wav", wav([]))})
    assert post(url + "/inference", body, ct) == (200, {"text": ""})
    assert fake.calls == []
    with urllib.request.urlopen(url + "/health", timeout=5) as r:
        assert json.load(r) == {"ok": True}


def test_loopback_only() -> None:
    with pytest.raises(SystemExit):
        speech.serve(FakeRecognizer(), "0.0.0.0", 0)
