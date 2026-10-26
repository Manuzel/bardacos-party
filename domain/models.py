"""
Entidades do catálogo de áudio.
Puras: não conhecem disco, banco, áudio nem interface. Todas são imutáveis;
alterações geram uma cópia nova com `dataclasses.replace`.
"""
import unicodedata
import uuid
from dataclasses import dataclass
from enum import StrEnum

TrackId = str
TagId = int

# Até esta duração uma faixa é tratada como efeito sonoro (cabe pré-decodificada em RAM).
SFX_MAX_DURATION_S = 30.0


class TrackKind(StrEnum):
    SFX = "sfx"
    AMBIENCE = "ambience"
    MUSIC = "music"


class SourceKind(StrEnum):
    LOCAL = "local"    # Arquivo permanente em assets/sounds
    STREAM = "stream"  # Tocado direto da URL pelo VLC, sem gravar em disco
    CACHE = "cache"    # Baixado para a pasta temporária a cada sessão


class BackendHint(StrEnum):
    VLC = "vlc"
    PYGAME = "pygame"


@dataclass(frozen=True, slots=True)
class Track:
    id: TrackId
    title: str
    kind: TrackKind
    source_kind: SourceKind
    local_path: str | None = None    # Relativo a assets/sounds, em formato POSIX
    origin_url: str | None = None    # Página original (YouTube); também vale para locais baixados
    duration_s: float | None = None  # None = desconhecida
    default_volume: float = 0.8
    default_loop: bool = False
    backend_hint: BackendHint | None = None
    missing: bool = False            # Arquivo local sumiu do disco; a faixa fica guardada, não apagada
    tag_ids: frozenset[TagId] = frozenset()

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("A faixa precisa de um título.")
        if not 0.0 <= self.default_volume <= 1.0:
            raise ValueError(f"Volume fora de 0–1: {self.default_volume}")
        if self.duration_s is not None and self.duration_s <= 0:
            raise ValueError(f"Duração inválida: {self.duration_s}")
        if self.source_kind == SourceKind.LOCAL and not self.local_path:
            raise ValueError("Faixa local precisa de local_path.")
        if self.source_kind != SourceKind.LOCAL and not self.origin_url:
            raise ValueError("Faixa remota precisa de origin_url.")


@dataclass(frozen=True, slots=True)
class Tag:
    id: TagId | None  # None até ser gravada pela primeira vez
    name: str
    color: str | None = None
    position: int = 0  # Ordem dos chips na UI

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("A tag precisa de um nome.")


@dataclass(frozen=True, slots=True)
class PlayableSource:
    """O que um backend de áudio consegue abrir: um arquivo local ou uma URL direta."""
    uri: str
    is_remote: bool

    def __post_init__(self) -> None:
        if not self.uri:
            raise ValueError("Fonte de áudio vazia.")

    @classmethod
    def from_uri(cls, uri: str) -> "PlayableSource":
        return cls(uri=uri, is_remote=uri.startswith(("http://", "https://", "rtsp://", "mms://")))


@dataclass(frozen=True, slots=True)
class LibraryData:
    """Tudo o que o repositório guarda, carregado de uma vez na inicialização."""
    tracks: tuple[Track, ...]
    tags: tuple[Tag, ...]


def new_track_id() -> TrackId:
    return uuid.uuid4().hex


def normalize_title(title: str) -> str:
    """Forma usada para ordenar e buscar: sem acentos, sem caixa, espaços únicos."""
    decomposed = unicodedata.normalize("NFKD", title.casefold())
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_marks.split())


def title_from_filename(stem: str) -> str:
    """'taverna_do-anao' -> 'Taverna Do Anao'."""
    return " ".join(stem.replace("_", " ").replace("-", " ").split()).title()


def infer_kind(duration_s: float | None) -> TrackKind:
    """Palpite inicial; o usuário pode reclassificar. Sem duração conhecida, assume música."""
    if duration_s is not None and duration_s <= SFX_MAX_DURATION_S:
        return TrackKind.SFX
    return TrackKind.MUSIC
