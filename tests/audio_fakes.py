"""Dublês de backend e utilitários compartilhados pelos testes de áudio."""
import math
import struct
import wave
from pathlib import Path

from domain.models import BackendHint, SourceKind, Track, TrackKind
from domain.ports import BackendEvent, VoiceLimitReached


class FakeVoice:
    def __init__(self, backend: "FakeBackend", source, loop, on_event):
        self.backend = backend
        self.source = source
        self.loop = loop
        self.on_event = on_event
        self.supports_seek = backend.supports_seek
        self.calls: list[str] = []
        self.gains: list[tuple[float, bool]] = []
        self.seeks: list[float] = []
        self.stopped = False
        self.fail_on: str | None = None

    def _record(self, name: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"falha simulada em {name}")
        self.calls.append(name)

    def start(self): self._record("start")
    def pause(self): self._record("pause")
    def resume(self): self._record("resume")
    def restart(self): self._record("restart")
    def poll(self): self._record("poll")

    def stop(self):
        self._record("stop")
        self.stopped = True
        self.backend.active.remove(self)

    def seek(self, seconds):
        self.seeks.append(seconds)

    def set_loop(self, loop):
        self.loop = loop

    def apply_gain(self, gain, force=False):
        self.gains.append((gain, force))

    def position(self):
        return 1.5

    def duration(self):
        return None

    def emit(self, event: BackendEvent):
        self.on_event(event)


class FakeBackend:
    def __init__(self, name: BackendHint, capacity: int = 8, supports_seek: bool = True):
        self.name = name
        self.capacity = capacity
        self.supports_seek = supports_seek
        self.active: list[FakeVoice] = []
        self.created: list[FakeVoice] = []
        self.opened = self.closed = False
        self.open_error: Exception | None = None
        self.create_error: Exception | None = None

    def open(self):
        if self.open_error:
            raise self.open_error
        self.opened = True

    def close(self):
        self.closed = True

    def create_voice(self, source, loop, on_event):
        if self.create_error:
            raise self.create_error
        if len(self.active) >= self.capacity:
            raise VoiceLimitReached(self.name)
        voice = FakeVoice(self, source, loop, on_event)
        self.active.append(voice)
        self.created.append(voice)
        return voice


def make_track(track_id="t1", duration_s=None, kind=TrackKind.MUSIC, **overrides) -> Track:
    fields = dict(id=track_id, title=f"Faixa {track_id}", kind=kind, source_kind=SourceKind.LOCAL,
                  local_path=f"{track_id}.ogg", duration_s=duration_s)
    fields.update(overrides)
    return Track(**fields)


def write_wav(path: Path, seconds: float, rate: int = 22050) -> Path:
    """Gera um seno de 440 Hz mono 16 bits."""
    frames = b"".join(
        struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(int(rate * seconds))
    )
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames)
    return path
