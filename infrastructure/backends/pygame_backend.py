"""
Adapter do pygame.mixer para o port `PlaybackBackend`.
Para efeitos e faixas curtas: o som fica pré-decodificado em RAM e começa em ~10 ms.
"""
import os
import time
from collections import OrderedDict
from typing import Callable

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from domain.models import BackendHint, PlayableSource
from domain.ports import BackendEvent, VoiceLimitReached

# O SDL_mixer guarda o volume do canal como inteiro de 0 a 128
_MIX_MAX_VOLUME = 128


class PygameBackend:
    name = BackendHint.PYGAME

    def __init__(
        self,
        num_channels: int = 32,
        cache_budget_bytes: int = 128 * 1024 * 1024,
        frequency: int = 44100,
        buffer: int = 512
    ):
        self._num_channels = num_channels
        self._cache_budget = cache_budget_bytes
        self._frequency = frequency
        self._buffer = buffer
        self._free: list[pygame.mixer.Channel] = []
        self._active: set["PygameVoice"] = set()
        self._cache: SoundCache | None = None

    def open(self) -> None:
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=self._frequency, size=-16, channels=2, buffer=self._buffer)
        pygame.mixer.set_num_channels(self._num_channels)
        # Pool explícito: um canal ocupado nunca é tomado de outra voz.
        self._free = [pygame.mixer.Channel(i) for i in reversed(range(self._num_channels))]
        self._cache = SoundCache(self._cache_budget)

    def close(self) -> None:
        for voice in list(self._active):
            voice.stop()
        if pygame.mixer.get_init():
            pygame.mixer.quit()
        self._free = []
        self._cache = None

    def create_voice(
        self,
        source: PlayableSource,
        loop: bool,
        on_event: Callable[[BackendEvent], None]
    ) -> "PygameVoice":
        if self._cache is None:
            raise RuntimeError("PygameBackend usado antes de open()")
        if not self._free:
            raise VoiceLimitReached(f"os {self._num_channels} canais do Pygame estão ocupados")

        sound = self._cache.acquire(source.uri)  # pygame.error se o arquivo não abrir
        channel = self._free.pop()

        def release(voice: "PygameVoice") -> None:
            channel.stop()
            self._free.append(channel)
            self._active.discard(voice)
            self._cache.release(source.uri)

        voice = PygameVoice(channel, sound, loop, on_event, on_release=release)
        self._active.add(voice)
        return voice


class PygameVoice:
    supports_seek = False  # Um Sound do Pygame não tem posição ajustável

    def __init__(self, channel, sound, loop: bool, on_event: Callable[[BackendEvent], None], on_release):
        self._channel = channel
        self._sound = sound
        self._loop = loop
        self._on_event = on_event
        self._on_release = on_release
        self._level: int | None = None
        self._started_at = 0.0
        self._paused_at: float | None = None
        self._ended = False
        self._released = False

    def start(self) -> None:
        self._channel.play(self._sound)
        if self._loop:
            # Com a próxima repetição na fila, a emenda é feita pelo SDL, sem lacuna.
            self._channel.queue(self._sound)
        self._started_at = time.monotonic()
        self._paused_at = None
        self._ended = False

    def pause(self) -> None:
        if self._paused_at is None:
            self._channel.pause()
            self._paused_at = time.monotonic()

    def resume(self) -> None:
        if self._paused_at is not None:
            self._channel.unpause()
            self._started_at += time.monotonic() - self._paused_at
            self._paused_at = None

    def restart(self) -> None:
        self._channel.stop()
        self.start()

    def stop(self) -> None:
        if self._released:
            return
        self._released = True
        self._on_release(self)

    def seek(self, seconds: float) -> None:
        pass

    def set_loop(self, loop: bool) -> None:
        # Desligar o loop deixa tocar no máximo a repetição que já estava na fila.
        self._loop = loop
        if loop and self._channel.get_busy() and self._channel.get_queue() is None:
            self._channel.queue(self._sound)

    def apply_gain(self, gain: float, force: bool = False) -> None:
        level = round(gain * _MIX_MAX_VOLUME)
        if force or level != self._level:
            self._channel.set_volume(level / _MIX_MAX_VOLUME)
            self._level = level

    def position(self) -> float:
        now = self._paused_at if self._paused_at is not None else time.monotonic()
        elapsed = max(0.0, now - self._started_at)
        length = self._sound.get_length()
        if length <= 0:
            return 0.0
        return elapsed % length if self._loop else min(elapsed, length)

    def duration(self) -> float | None:
        return self._sound.get_length() or None

    def poll(self) -> None:
        if self._released or self._paused_at is not None:
            return
        if self._channel.get_busy():
            if self._loop and self._channel.get_queue() is None:
                self._channel.queue(self._sound)  # A repetição anterior começou: põe a próxima
        elif not self._ended:
            self._ended = True
            self._on_event(BackendEvent.ENDED)


class SoundCache:
    """
    Sons decodificados, reaproveitados entre vozes (Flyweight) e despejados por LRU
    quando o total passa do orçamento. Sons em uso nunca são despejados.
    """

    def __init__(self, budget_bytes: int):
        self._budget = budget_bytes
        self._entries: OrderedDict[str, list] = OrderedDict()  # uri -> [sound, bytes, refs]
        self._total = 0

    @property
    def total_bytes(self) -> int:
        return self._total

    def acquire(self, uri: str):
        entry = self._entries.get(uri)
        if entry is None:
            sound = pygame.mixer.Sound(uri)
            entry = [sound, _decoded_size(sound), 0]
            self._entries[uri] = entry
            self._total += entry[1]
        self._entries.move_to_end(uri)
        entry[2] += 1
        self._evict()
        return entry[0]

    def release(self, uri: str) -> None:
        entry = self._entries.get(uri)
        if entry is not None:
            entry[2] = max(0, entry[2] - 1)
            self._evict()

    def _evict(self) -> None:
        for uri in list(self._entries):
            if self._total <= self._budget:
                return
            entry = self._entries[uri]
            if entry[2] == 0:
                del self._entries[uri]
                self._total -= entry[1]


def _decoded_size(sound) -> int:
    frequency, size, channels = pygame.mixer.get_init()
    return int(sound.get_length() * frequency) * channels * (abs(size) // 8)
