"""
Mensagens trocadas entre a UI e o orquestrador de áudio. Todas imutáveis.
- Comandos: intenções da UI, enfileiradas em `AudioOrchestrator.send`.
- Eventos: fatos discretos que a UI não pode perder (fila).
- Snapshot: o estado mais recente do mixer (último valor; versões velhas são descartadas).
"""
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from domain.models import BackendHint, PlayableSource, Track

VoiceId = str


# ---------------- Comandos ----------------

@dataclass(frozen=True, slots=True)
class TogglePlay:
    """Toca, pausa ou retoma — quem decide é o orquestrador, com o estado verdadeiro."""
    voice_id: VoiceId
    track: Track
    source: PlayableSource
    volume: float
    loop: bool


@dataclass(frozen=True, slots=True)
class Stop:
    voice_id: VoiceId


@dataclass(frozen=True, slots=True)
class StopAll:
    pass


@dataclass(frozen=True, slots=True)
class Seek:
    voice_id: VoiceId
    seconds: float


@dataclass(frozen=True, slots=True)
class SetVolume:
    voice_id: VoiceId
    volume: float


@dataclass(frozen=True, slots=True)
class SetLoop:
    voice_id: VoiceId
    loop: bool


AudioCommand = TogglePlay | Stop | StopAll | Seek | SetVolume | SetLoop


# ---------------- Eventos ----------------

@dataclass(frozen=True, slots=True)
class VoiceEnded:
    voice_id: VoiceId


@dataclass(frozen=True, slots=True)
class VoiceFailed:
    voice_id: VoiceId
    reason: str


AudioEvent = VoiceEnded | VoiceFailed


# ---------------- Snapshot ----------------

class VoiceState(StrEnum):
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class VoiceView:
    voice_id: VoiceId
    state: VoiceState
    position_s: float
    duration_s: float | None
    volume: float
    loop: bool
    backend: BackendHint
    supports_seek: bool


@dataclass(frozen=True, slots=True)
class MixerSnapshot:
    seq: int
    voices: Mapping[VoiceId, VoiceView]

    @classmethod
    def empty(cls) -> "MixerSnapshot":
        return cls(seq=0, voices=MappingProxyType({}))
