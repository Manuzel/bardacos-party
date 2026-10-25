"""
Ports: contratos que a aplicação exige do mundo externo.
As implementações concretas ficam em `infrastructure/` e `providers/` e são
injetadas pelo composition root (`main.py`). Tipagem estrutural (Protocol):
um adapter não precisa herdar daqui, só cumprir a assinatura.
"""
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Callable, Protocol

from domain.models import LibraryData, Tag, TagId, Track, TrackId


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
