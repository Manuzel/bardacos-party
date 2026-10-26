"""
ViewModel da biblioteca (MVVM): o estado do filtro e tudo o que as views desenham.
Python puro, sem Tk, para ser testado sem janela.

Semântica dos filtros (como no catálogo de referência):
- Tags: incluir = E (cada tag a mais estreita o resultado); excluir = nenhuma delas.
- Origem e Formato: incluir = OU dentro do grupo (marcar Local e Nuvem mostra as duas).
  As contagens desses grupos ignoram o próprio grupo, para dar para marcar mais de um.
"""
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Callable, Hashable

from application.library import UNCATEGORIZED, LibraryService
from application.library_index import FacetCounts, LibraryQuery, SortOrder
from domain.models import SourceKind, TagId, Track, TrackKind, normalize_title


class FacetType(StrEnum):
    TAG = "tag"
    KIND = "kind"
    SOURCE = "source"


FacetKey = tuple[FacetType, Hashable]


class ChipState(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


@dataclass(frozen=True, slots=True)
class ChipModel:
    key: FacetKey
    label: str
    count: int | None            # None = não mostrar contagem (chips ativos)
    state: ChipState | None
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class FacetGroup:
    title: str
    chips: tuple[ChipModel, ...]
    active: int                  # Quantos filtros deste grupo estão ligados


KIND_LABELS = {TrackKind.SFX: "Efeito", TrackKind.AMBIENCE: "Ambiente", TrackKind.MUSIC: "Música"}
SOURCE_LABELS = {SourceKind.LOCAL: "Local", SourceKind.STREAM: "Nuvem", SourceKind.CACHE: "Cache"}
SORT_LABELS = {SortOrder.TITLE: "Título", SortOrder.RECENT: "Recentes", SortOrder.DURATION: "Duração"}


class LibraryViewModel:
    def __init__(self, library: LibraryService):
        self._library = library
        # Ordem de inserção = ordem dos chips ativos no topo
        self._filters: dict[FacetKey, ChipState] = {}
        self._listeners: list[Callable[[], None]] = []
        self.text = ""
        self.tag_filter_text = ""
        self.sort = SortOrder.TITLE
        self.results: list[Track] = []
        self._counts = FacetCounts()
        self.refresh()

    # ---------------- Observação ----------------

    def subscribe(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # ---------------- Ações ----------------

    def toggle(self, key: FacetKey, exclude: bool = False) -> None:
        """Clique liga/desliga o incluir; com exclude, liga/desliga o excluir."""
        target = ChipState.EXCLUDE if exclude else ChipState.INCLUDE
        current = self._filters.pop(key, None)
        if current != target:
            self._filters[key] = target
        self.refresh()

    def remove(self, key: FacetKey) -> None:
        if self._filters.pop(key, None) is not None:
            self.refresh()

    def clear(self) -> None:
        if self._filters:
            self._filters.clear()
            self.refresh()

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.text = text
            self.refresh()

    def set_tag_filter_text(self, text: str) -> None:
        if text != self.tag_filter_text:
            self.tag_filter_text = text
            self._notify()

    def cycle_sort(self) -> None:
        orders = list(SortOrder)
        self.sort = orders[(orders.index(self.sort) + 1) % len(orders)]
        self.refresh()

    def refresh(self) -> None:
        """Recalcula resultado e contagens. Chamar também depois de mudar o catálogo."""
        # Um filtro de tag apagada não pode ficar preso no topo
        for key in [k for k in self._filters if k[0] == FacetType.TAG and self._library.tag(k[1]) is None]:
            del self._filters[key]

        query = self._query()
        self.results = self._library.query(query)
        tag_counts = self._library.facet_counts(self.results)
        kind_counts = self._library.facet_counts(self._library.query(replace(query, kinds=frozenset())))
        source_counts = self._library.facet_counts(self._library.query(replace(query, sources=frozenset())))
        self._counts = FacetCounts(tags=tag_counts.tags, kinds=kind_counts.kinds, sources=source_counts.sources)
        self._notify()

    # ---------------- Leitura para as views ----------------

    @property
    def sort_label(self) -> str:
        return SORT_LABELS[self.sort]

    def state_of(self, key: FacetKey) -> ChipState | None:
        return self._filters.get(key)

    def label(self, key: FacetKey) -> str:
        kind, value = key
        if kind == FacetType.KIND:
            return KIND_LABELS[value]
        if kind == FacetType.SOURCE:
            return SOURCE_LABELS[value]
        tag = self._library.tag(value)
        return tag.name if tag else "?"

    def active_chips(self) -> list[ChipModel]:
        return [ChipModel(key, self.label(key), None, state) for key, state in self._filters.items()]

    def groups(self) -> list[FacetGroup]:
        groups = [
            self._group("Origem", [(FacetType.SOURCE, s) for s in SourceKind], self._counts.sources),
            self._group("Formato", [(FacetType.KIND, k) for k in TrackKind], self._counts.kinds),
        ]
        needle = normalize_title(self.tag_filter_text)
        by_category: dict[str, list[FacetKey]] = {c: [] for c in self._library.tag_categories()}
        for tag in self._library.tags():
            key = (FacetType.TAG, tag.id)
            if needle and needle not in normalize_title(tag.name) and key not in self._filters:
                continue
            by_category[tag.category or UNCATEGORIZED].append(key)
        for category, keys in by_category.items():
            if keys:
                groups.append(self._group(category, keys, self._counts.tags))
        return groups

    def card_tags(self, track: Track) -> list[tuple[str, bool]]:
        """Tags do card: (nome, destacada). As incluídas no filtro vêm primeiro, em verde."""
        tags = [self._library.tag(t) for t in track.tag_ids]
        tags = [t for t in tags if t is not None]
        included = {k[1] for k, s in self._filters.items() if k[0] == FacetType.TAG and s == ChipState.INCLUDE}
        tags.sort(key=lambda t: (t.id not in included, t.position))
        return [(t.name, t.id in included) for t in tags]

    def tags_by_category(self) -> list[tuple[str, list[tuple[TagId, str]]]]:
        """Para o menu de tags do card."""
        grouped: dict[str, list[tuple[TagId, str]]] = {c: [] for c in self._library.tag_categories()}
        for tag in self._library.tags():
            grouped[tag.category or UNCATEGORIZED].append((tag.id, tag.name))
        return list(grouped.items())

    # ---------------- Internos ----------------

    def _group(self, title: str, keys: list[FacetKey], counts) -> FacetGroup:
        chips = []
        for key in keys:
            state = self._filters.get(key)
            count = counts.get(key[1], 0)
            chips.append(ChipModel(key, self.label(key), count, state, disabled=count == 0 and state is None))
        return FacetGroup(title, tuple(chips), active=sum(1 for c in chips if c.state is not None))

    def _query(self) -> LibraryQuery:
        def values(kind: FacetType, state: ChipState) -> frozenset:
            return frozenset(k[1] for k, s in self._filters.items() if k[0] == kind and s == state)

        return LibraryQuery(
            include_tags=values(FacetType.TAG, ChipState.INCLUDE),
            exclude_tags=values(FacetType.TAG, ChipState.EXCLUDE),
            kinds=values(FacetType.KIND, ChipState.INCLUDE),
            exclude_kinds=values(FacetType.KIND, ChipState.EXCLUDE),
            sources=values(FacetType.SOURCE, ChipState.INCLUDE),
            exclude_sources=values(FacetType.SOURCE, ChipState.EXCLUDE),
            text=self.text,
            sort=self.sort,
        )
