"""
Ports: contratos que a aplicação exige do mundo externo.
As implementações concretas ficam em `infrastructure/` e `providers/` e são
injetadas pelo composition root (`main.py`). Tipagem estrutural (Protocol):
um adapter não precisa herdar daqui, só cumprir a assinatura.
"""
from contextlib import AbstractContextManager
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Protocol

from domain.models import BackendHint, LibraryData, PlayableSource, Tag, TagId, Track, TrackId


# ---------------- Persistência ----------------

class UnitOfWork(Protocol):
    """Operações de escrita de uma transação. Tudo ou nada."""

    def save_track(self, track: Track) -> None:
        """Insere ou atualiza a faixa, substituindo as tags dela por `track.tag_ids`."""
        ...

    def delete_track(self, track_id: TrackId) -> None: ...

    def save_tag(self, tag: Tag) -> Tag:
        """Insere (id None) ou atualiza. Devolve a tag com id. Nome repetido -> ValueError."""
        ...

    def delete_tag(self, tag_id: TagId) -> None: ...


class LibraryRepository(Protocol):
    def load(self) -> LibraryData: ...

    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]:
        """Confirma ao sair do bloco; desfaz tudo se uma exceção escapar."""
        ...

    def close(self) -> None: ...


class SoundFolder(Protocol):
    """A pasta de áudios locais (assets/sounds)."""

    def list_audio_files(self) -> list[str]:
        """Caminhos relativos à pasta, em formato POSIX."""
        ...

    def absolute_path(self, relative_path: str) -> Path: ...


class MediaProbe(Protocol):
    def duration_of(self, path: Path) -> float | None:
        """Duração em segundos lendo só o cabeçalho; None se não der para saber."""
        ...


class CoverProvider(Protocol):
    """Capas das faixas, guardadas em disco e buscadas em segundo plano."""

    def cover_path(self, track_id: TrackId) -> Path | None:
        """Arquivo da capa, se já existir."""
        ...

    def request(self, track: Track, local_file: Path | None, on_ready: Callable[[TrackId], None]) -> None:
        """Busca a capa se faltar. `on_ready` é chamado de outra thread quando ela fica pronta."""
        ...

    def discard(self, track_id: TrackId) -> None: ...


# ---------------- Reprodução ----------------
# Só a thread do orquestrador chama VoiceHandle e PlaybackBackend.

class BackendEvent(StrEnum):
    PLAYING = "playing"  # A saída de áudio abriu (reaplicar o volume)
    ENDED = "ended"      # Chegou ao fim sem emendar sozinho
    ERROR = "error"      # Não foi possível tocar (arquivo sumido, URL expirada...)


class VoiceLimitReached(Exception):
    """O backend não tem mais vozes/canais livres."""


class VoiceHandle(Protocol):
    supports_seek: bool

    def start(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def restart(self) -> None:
        """Volta ao início e toca de novo (loop sem emenda nativa)."""
        ...
    def stop(self) -> None:
        """Para e libera os recursos; o handle não pode ser reusado."""
        ...
    def seek(self, seconds: float) -> None: ...
    def set_loop(self, loop: bool) -> None:
        """Backends que emendam sozinhos usam isto; os outros emitem ENDED e recebem restart()."""
        ...
    def apply_gain(self, gain: float, force: bool = False) -> None:
        """Ganho linear 0–1. O adapter quantiza e só chama a biblioteca se o valor mudou."""
        ...
    def position(self) -> float: ...
    def duration(self) -> float | None: ...
    def poll(self) -> None:
        """Chamado a cada tick; backends sem callback nativo detectam o fim aqui."""
        ...


class PlaybackBackend(Protocol):
    name: BackendHint

    def open(self) -> None:
        """Inicializa a biblioteca. Chamado pela thread do orquestrador."""
        ...

    def close(self) -> None: ...

    def create_voice(
        self,
        source: PlayableSource,
        loop: bool,
        on_event: Callable[[BackendEvent], None]
    ) -> VoiceHandle:
        """Prepara uma voz parada. `on_event` pode ser chamado de qualquer thread. Sem vaga -> VoiceLimitReached."""
        ...


# ---------------- Provedores de mídia ----------------
# Assíncronos por callback, como a UI os usa hoje. No módulo 2 viram chamadas
# bloqueantes executadas pelo pool de resolução do orquestrador.

MediaMetadata = dict[str, Any]
ProgressCallback = Callable[[float, str], None]
ErrorCallback = Callable[[Exception], None]


class StreamResolver(Protocol):
    def get_stream_url(
        self,
        query_or_url: str,
        on_success: Callable[[str, MediaMetadata], None],
        on_error: ErrorCallback | None = None
    ) -> None:
        """Descobre em background a URL direta do áudio, sem gravar nada em disco."""
        ...


class TrackDownloader(Protocol):
    def download(
        self,
        query_or_url: str,
        output_dir: str,
        on_progress: ProgressCallback | None = None,
        on_complete: Callable[[str, MediaMetadata], None] | None = None,
        on_error: ErrorCallback | None = None
    ) -> None:
        """Baixa em background para `output_dir`; `on_complete` recebe o caminho final."""
        ...
