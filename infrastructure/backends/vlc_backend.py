"""
Adapter do libVLC (python-vlc) para os ports `PlaybackBackend` e `MediaProbe`
(e a leitura da capa embutida usada pelas capas).
Importar este módulo falha (ImportError/OSError) se a libVLC não estiver instalada;
o composition root trata isso e segue sem o VLC.
"""
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Callable

import vlc

from domain.models import BackendHint, PlayableSource
from domain.ports import BackendEvent, VoiceLimitReached

_BASE_ARGS = ("--no-xlib", "--quiet", "--no-video")

_FINISHED_PARSE = {
    vlc.MediaParsedStatus.done,
    vlc.MediaParsedStatus.failed,
    vlc.MediaParsedStatus.skipped,
    vlc.MediaParsedStatus.timeout,
}


class VlcBackend:
    name = BackendHint.VLC

    def __init__(self, max_voices: int = 24, network_caching_ms: int = 1500, extra_args: tuple[str, ...] = ()):
        self._max_voices = max_voices
        self._network_caching_ms = network_caching_ms
        self._extra_args = extra_args
        self._instance: vlc.Instance | None = None
        self._active: set["VlcVoice"] = set()

    def open(self) -> None:
        # Uma única instância por processo: cada uma carrega todos os plugins do libVLC.
        self._instance = vlc.Instance(*_BASE_ARGS, *self._extra_args)
        if self._instance is None:
            raise RuntimeError("a libVLC não inicializou")

    def close(self) -> None:
        for voice in list(self._active):
            voice.stop()
        if self._instance is not None:
            self._instance.release()
            self._instance = None

    def create_voice(
        self,
        source: PlayableSource,
        loop: bool,
        on_event: Callable[[BackendEvent], None]
    ) -> "VlcVoice":
        if self._instance is None:
            raise RuntimeError("VlcBackend usado antes de open()")
        if len(self._active) >= self._max_voices:
            raise VoiceLimitReached(f"limite de {self._max_voices} vozes VLC")

        media = self._instance.media_new(source.uri)
        if source.is_remote:
            media.add_option(f":network-caching={self._network_caching_ms}")
        player = self._instance.media_player_new()
        player.set_media(media)

        voice = VlcVoice(player, media, on_event, on_release=self._active.discard)
        self._active.add(voice)
        return voice


class VlcVoice:
    """Uma faixa num `MediaPlayer`. O loop é feito pelo mixer: ENDED -> restart()."""

    supports_seek = True

    def __init__(self, player, media, on_event: Callable[[BackendEvent], None], on_release):
        self._player = player
        self._media = media
        self._on_release = on_release
        self._volume: int | None = None
        self._released = False

        # Os callbacks rodam em threads do libVLC: só repassam o evento para a fila.
        # Chamar o libVLC daqui dentro causa deadlock.
        manager = player.event_manager()
        for vlc_event, event in (
            (vlc.EventType.MediaPlayerPlaying, BackendEvent.PLAYING),
            (vlc.EventType.MediaPlayerEndReached, BackendEvent.ENDED),
            (vlc.EventType.MediaPlayerEncounteredError, BackendEvent.ERROR),
        ):
            manager.event_attach(vlc_event, lambda _e, ev=event: on_event(ev))

    def start(self) -> None:
        if self._player.play() == -1:
            raise RuntimeError("o VLC recusou a mídia")

    def pause(self) -> None:
        self._player.set_pause(1)

    def resume(self) -> None:
        self._player.set_pause(0)

    def restart(self) -> None:
        self._player.stop()
        self.start()

    def stop(self) -> None:
        if self._released:
            return
        self._released = True
        self._player.stop()
        self._player.release()
        self._media.release()
        self._on_release(self)

    def seek(self, seconds: float) -> None:
        self._player.set_time(int(seconds * 1000))

    def set_loop(self, loop: bool) -> None:
        pass  # O mixer reinicia ao receber ENDED

    def apply_gain(self, gain: float, force: bool = False) -> None:
        volume = round(gain * 100)  # O libVLC aceita inteiros de 0 a 100
        if force or volume != self._volume:
            self._player.audio_set_volume(volume)
            self._volume = volume

    def position(self) -> float:
        return max(0, self._player.get_time()) / 1000.0

    def duration(self) -> float | None:
        length = self._player.get_length()
        return length / 1000.0 if length > 0 else None

    def poll(self) -> None:
        pass  # O fim chega pelo evento MediaPlayerEndReached


class VlcMediaProbe:
    """
    Lê duração e capa embutida pelo cabeçalho do arquivo (poucos ms por arquivo).
    Pode ser usada por mais de uma thread (catálogo e capas).
    """

    def __init__(self, timeout_ms: int = 2000):
        self._timeout_ms = timeout_ms
        self._instance: vlc.Instance | None = None
        self._lock = threading.Lock()

    def duration_of(self, path: Path) -> float | None:
        duration_ms = self._parse(path, vlc.MediaParseFlag.local, lambda media: media.get_duration())
        return duration_ms / 1000.0 if duration_ms and duration_ms > 0 else None

    def artwork_of(self, path: Path) -> Path | None:
        """Imagem embutida (ex.: capa no ID3 de um MP3), que o libVLC extrai para o cache dele."""
        url = self._parse(
            path, vlc.MediaParseFlag(vlc.MediaParseFlag.local.value | vlc.MediaParseFlag.fetch_local.value),
            lambda media: media.get_meta(vlc.Meta.ArtworkURL),
        )
        if not url or not url.startswith("file://"):
            return None
        artwork = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path))
        return artwork if artwork.is_file() else None

    def close(self) -> None:
        with self._lock:
            if self._instance is not None:
                self._instance.release()
                self._instance = None

    def _parse(self, path: Path, flags, read):
        with self._lock:
            if self._instance is None:
                self._instance = vlc.Instance(*_BASE_ARGS, "--aout=dummy")
            instance = self._instance
        media = instance.media_new(str(path))
        try:
            media.parse_with_options(flags, self._timeout_ms)
            deadline = time.monotonic() + self._timeout_ms / 1000
            while media.get_parsed_status() not in _FINISHED_PARSE and time.monotonic() < deadline:
                time.sleep(0.002)
            return read(media)
        finally:
            media.release()
