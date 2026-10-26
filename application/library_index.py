"""
Índice do catálogo em memória (doc: §2.3).
Índice invertido tag -> faixas; a filtragem é interseção de conjuntos começando pelo
menor, então o custo cresce com o resultado, não com a biblioteca.
"""
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable

from domain.models import SourceKind, Tag, TagId, Track, TrackId, TrackKind, normalize_title


class SortOrder(StrEnum):
    TITLE = "title"
    RECENT = "recent"
    DURATION = "duration"


@dataclass(frozen=True, slots=True)
class LibraryQuery:
    include_tags: frozenset[TagId] = frozenset()     # Todas precisam estar na faixa (E)
    exclude_tags: frozenset[TagId] = frozenset()     # Nenhuma pode estar
    kinds: frozenset[TrackKind] = frozenset()        # Vazio = qualquer formato (OU entre os marcados)
    exclude_kinds: frozenset[TrackKind] = frozenset()
    sources: frozenset[SourceKind] = frozenset()     # Vazio = qualquer origem (OU entre as marcadas)
    exclude_sources: frozenset[SourceKind] = frozenset()
    text: str = ""                                   # Palavras no título ou nos nomes das tags
    sort: SortOrder = SortOrder.TITLE


@dataclass(frozen=True, slots=True)
class FacetCounts:
    """Quantas faixas de um resultado têm cada tag, formato e origem."""
    tags: Counter = field(default_factory=Counter)
    kinds: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)


class LibraryIndex:
    def __init__(self, tags: Iterable[Tag] = ()):
        self._tags: dict[TagId, Tag] = {}
        self._tracks: dict[TrackId, Track] = {}
        self._by_tag: dict[TagId, set[TrackId]] = {}
        self._search_text: dict[TrackId, str] = {}
        self._title_norm: dict[TrackId, str] = {}
        self.set_tags(tags)

    def __len__(self) -> int:
        return len(self._tracks)

    def set_tags(self, tags: Iterable[Tag]) -> None:
        """Troca o dicionário de tags e refaz o texto de busca (os nomes das tags entram nele)."""
        self._tags = {tag.id: tag for tag in tags if tag.id is not None}
        for track in self._tracks.values():
            self._search_text[track.id] = self._make_search_text(track)

    def upsert(self, track: Track) -> None:
        self.remove(track.id)
        self._tracks[track.id] = track
        for tag_id in track.tag_ids:
            self._by_tag.setdefault(tag_id, set()).add(track.id)
        self._title_norm[track.id] = normalize_title(track.title)
        self._search_text[track.id] = self._make_search_text(track)

    def remove(self, track_id: TrackId) -> None:
        track = self._tracks.pop(track_id, None)
        if track is None:
            return
        for tag_id in track.tag_ids:
            members = self._by_tag.get(tag_id)
            if members is not None:
                members.discard(track_id)
                if not members:
                    del self._by_tag[tag_id]
        self._title_norm.pop(track_id, None)
        self._search_text.pop(track_id, None)

    def query(self, query: LibraryQuery) -> list[Track]:
        candidates = self._tag_candidates(query.include_tags)

        if query.exclude_tags:
            excluded = set().union(*(self._by_tag.get(t, set()) for t in query.exclude_tags))
            candidates = candidates - excluded

        words = normalize_title(query.text).split()
        result = []
        for track_id in candidates:
            track = self._tracks[track_id]
            if query.kinds and track.kind not in query.kinds:
                continue
            if track.kind in query.exclude_kinds:
                continue
            if query.sources and track.source_kind not in query.sources:
                continue
            if track.source_kind in query.exclude_sources:
                continue
            if words and not all(word in self._search_text[track_id] for word in words):
                continue
            result.append(track)

        result.sort(key=self._sort_key(query.sort))
        return result

    def facet_counts(self, tracks: Iterable[Track]) -> FacetCounts:
        counts = FacetCounts()
        for track in tracks:
            counts.tags.update(track.tag_ids)
            counts.kinds[track.kind] += 1
            counts.sources[track.source_kind] += 1
        return counts

    # ---------------- Internos ----------------

    def _tag_candidates(self, include: frozenset[TagId]) -> set[TrackId]:
        if not include:
            return set(self._tracks)
        # Interseção começando pelo menor conjunto; vazio encerra cedo.
        groups = sorted((self._by_tag.get(tag_id, set()) for tag_id in include), key=len)
        result = set(groups[0])
        for group in groups[1:]:
            if not result:
                break
            result &= group
        return result

    def _make_search_text(self, track: Track) -> str:
        names = (self._tags[t].name for t in track.tag_ids if t in self._tags)
        return normalize_title(" ".join([track.title, *names]))

    def _sort_key(self, order: SortOrder):
        if order == SortOrder.RECENT:
            # Mais recentes primeiro; sem data (nunca gravadas) vão para o fim
            return lambda t: (t.added_at is None, -(t.added_at.timestamp() if t.added_at else 0),
                              self._title_norm[t.id])
        if order == SortOrder.DURATION:
            return lambda t: (t.duration_s is None, t.duration_s or 0.0, self._title_norm[t.id])
        return lambda t: (self._title_norm[t.id], t.id)
