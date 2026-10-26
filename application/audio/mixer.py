"""
Núcleo do orquestrador de áudio, sem threads (doc: §3.2).
Recebe mensagens e ticks em ordem, de uma única thread, e é o único dono das vozes.
Por isso não há locks: nada mais toca nos players.
"""
import itertools
import logging
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Iterable

from application.audio.messages import (
    AudioEvent, MixerSnapshot, Seek, SetLoop, SetVolume, Stop, StopAll, TogglePlay,
    VoiceEnded, VoiceFailed, VoiceId, VoiceState, VoiceView,
)
from application.audio.selector import BackendSelector, NoBackendAvailable
from domain.models import Track
from domain.ports import BackendEvent, PlaybackBackend, VoiceHandle, VoiceLimitReached

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BackendSignal:
    """Evento de um backend, trazido para a thread do mixer pela fila."""
    voice_id: VoiceId
    token: int  # Identifica o handle que emitiu; sinais de handles já descartados são ignorados
    event: BackendEvent


@dataclass(slots=True)
class _Voice:
    token: int
    track: Track
    backend: PlaybackBackend
    handle: VoiceHandle
    volume: float
    loop: bool
    state: VoiceState


class Mixer:
    def __init__(
        self,
        selector: BackendSelector,
        emit: Callable[[AudioEvent], None],
        post: Callable[[BackendSignal], None]
    ):
        self._selector = selector
        self._emit = emit    # Eventos para a UI
        self._post = post    # Volta para a fila do próprio mixer (callbacks de backend)
        self._voices: dict[VoiceId, _Voice] = {}
        self._tokens = itertools.count(1)
        self._seq = 0

    @property
    def has_voices(self) -> bool:
        return bool(self._voices)

    # ---------------- Entrada ----------------

    def handle_batch(self, messages: Iterable[object]) -> None:
        """Aplica um lote em ordem. Um SetVolume superado por outro da mesma voz no lote é pulado."""
        batch = list(messages)
        last_volume = {m.voice_id: i for i, m in enumerate(batch) if isinstance(m, SetVolume)}
        for index, message in enumerate(batch):
            if isinstance(message, SetVolume) and last_volume[message.voice_id] != index:
                continue
            self.handle(message)

    def handle(self, message: object) -> None:
        try:
            self._dispatch(message)
        except Exception as exc:
            # Uma falha de biblioteca derruba só a voz envolvida, nunca a thread de áudio.
            log.exception("Falha ao processar %r", message)
            voice_id = getattr(message, "voice_id", None)
            if voice_id is not None:
                self._discard(voice_id)
                self._emit(VoiceFailed(voice_id, f"Erro no motor de áudio: {exc}"))

    def tick(self) -> None:
        for voice in list(self._voices.values()):
            voice.handle.poll()

    def stop_all(self) -> None:
        for voice_id in list(self._voices):
            self._discard(voice_id)

    # ---------------- Saída ----------------

    def snapshot(self) -> MixerSnapshot:
        self._seq += 1
        views = {
            voice_id: VoiceView(
                voice_id=voice_id,
                state=voice.state,
                position_s=voice.handle.position(),
                duration_s=voice.handle.duration() or voice.track.duration_s,
                volume=voice.volume,
                loop=voice.loop,
                backend=voice.backend.name,
                supports_seek=voice.handle.supports_seek,
            )
            for voice_id, voice in self._voices.items()
        }
        return MixerSnapshot(seq=self._seq, voices=MappingProxyType(views))

    # ---------------- Comandos ----------------

    def _dispatch(self, message: object) -> None:
        match message:
            case TogglePlay():
                self._toggle(message)
            case Stop(voice_id=voice_id):
                self._discard(voice_id)
            case StopAll():
                self.stop_all()
            case Seek(voice_id=voice_id, seconds=seconds):
                voice = self._voices.get(voice_id)
                if voice and voice.handle.supports_seek:
                    voice.handle.seek(max(0.0, seconds))
            case SetVolume(voice_id=voice_id, volume=volume):
                voice = self._voices.get(voice_id)
                if voice:
                    voice.volume = _clamp(volume)
                    voice.handle.apply_gain(voice.volume)
            case SetLoop(voice_id=voice_id, loop=loop):
                voice = self._voices.get(voice_id)
                if voice:
                    voice.loop = loop
                    voice.handle.set_loop(loop)
            case BackendSignal():
                self._on_backend_signal(message)
            case _:
                log.warning("Mensagem desconhecida ignorada: %r", message)

    def _toggle(self, command: TogglePlay) -> None:
        voice = self._voices.get(command.voice_id)
        if voice is None:
            self._start(command)
        elif voice.state == VoiceState.PLAYING:
            voice.handle.pause()
            voice.state = VoiceState.PAUSED
        else:
            voice.handle.resume()
            voice.state = VoiceState.PLAYING

    def _start(self, command: TogglePlay) -> None:
        voice_id = command.voice_id
        try:
            candidates = self._selector.candidates(command.track, command.source)
        except NoBackendAvailable as exc:
            self._emit(VoiceFailed(voice_id, str(exc)))
            return

        token = next(self._tokens)

        def on_event(event: BackendEvent) -> None:
            self._post(BackendSignal(voice_id, token, event))

        for backend in candidates:
            try:
                handle = backend.create_voice(command.source, command.loop, on_event)
            except VoiceLimitReached:
                log.info("%s sem vagas para %s; tentando o próximo backend", backend.name, voice_id)
                continue
            break
        else:
            self._emit(VoiceFailed(voice_id, "Todos os canais de áudio estão ocupados."))
            return

        volume = _clamp(command.volume)
        self._voices[voice_id] = _Voice(
            token=token, track=command.track, backend=backend, handle=handle,
            volume=volume, loop=command.loop, state=VoiceState.PLAYING,
        )
        handle.apply_gain(volume, force=True)
        handle.start()

    def _on_backend_signal(self, signal: BackendSignal) -> None:
        voice = self._voices.get(signal.voice_id)
        if voice is None or voice.token != signal.token:
            return  # Sinal de um handle que já foi parado ou substituído

        if signal.event == BackendEvent.PLAYING:
            # Algumas saídas de áudio ignoram o volume definido antes de abrir.
            voice.handle.apply_gain(voice.volume, force=True)
        elif signal.event == BackendEvent.ENDED:
            if voice.loop:
                voice.handle.restart()
            else:
                self._discard(signal.voice_id)
                self._emit(VoiceEnded(signal.voice_id))
        elif signal.event == BackendEvent.ERROR:
            self._discard(signal.voice_id)
            self._emit(VoiceFailed(signal.voice_id, f"Não foi possível tocar '{voice.track.title}'."))

    def _discard(self, voice_id: VoiceId) -> None:
        voice = self._voices.pop(voice_id, None)
        if voice is None:
            return
        try:
            voice.handle.stop()
        except Exception:
            log.exception("Falha ao liberar a voz %s", voice_id)


def _clamp(volume: float) -> float:
    return max(0.0, min(1.0, float(volume)))
