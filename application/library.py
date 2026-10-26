"""
Casos de uso do catálogo de faixas.
Write-through: cada operação grava no repositório e, só se a transação confirmar,
atualiza o estado em memória. O disco é a fonte da verdade; a memória é o índice.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from application.library_index import FacetCounts, LibraryIndex, LibraryQuery
from domain.models import (
    SourceKind, Tag, TagId, Track, TrackId, TrackKind, infer_kind, new_track_id, normalize_title,
    title_from_filename,
)
from domain.ports import LibraryRepository, MediaProbe, SoundFolder

# Grupo das tags sem categoria na lateral
UNCATEGORIZED = "Outras"


@dataclass(frozen=True, slots=True)
class SyncReport:
    added: int
    went_missing: int
    restored: int
    probed: int = 0  # Faixas antigas que ganharam duração nesta passada


class LibraryService:
    def __init__(
        self,
        repository: LibraryRepository,
        sound_folder: SoundFolder,
        probe: MediaProbe | None = None
    ):
        self._repository = repository
        self._sound_folder = sound_folder
        self._probe = probe
        data = repository.load()
        self._tracks: dict[TrackId, Track] = {t.id: t for t in data.tracks}
        self._tags: dict[TagId, Tag] = {t.id: t for t in data.tags}
        self._index = LibraryIndex(data.tags)
        for track in data.tracks:
            if not track.missing:
                self._index.upsert(track)

    # ---------------- Consultas ----------------

    def tracks(self, include_missing: bool = False) -> list[Track]:
        """Faixas em ordem alfabética (sem acentos e sem caixa)."""
        visible = (t for t in self._tracks.values() if include_missing or not t.missing)
        return sorted(visible, key=lambda t: (normalize_title(t.title), t.id))

    def get(self, track_id: TrackId) -> Track | None:
        return self._tracks.get(track_id)

    def query(self, query: LibraryQuery) -> list[Track]:
        """Faixas visíveis (não ausentes) que atendem ao filtro, já ordenadas."""
        return self._index.query(query)

    def facet_counts(self, tracks: list[Track]) -> FacetCounts:
        return self._index.facet_counts(tracks)

    def tags(self) -> list[Tag]:
        return sorted(self._tags.values(), key=lambda t: (t.position, normalize_title(t.name)))

    def tag(self, tag_id: TagId) -> Tag | None:
        return self._tags.get(tag_id)

    def tag_categories(self) -> list[str]:
        """Categorias na ordem da lateral: pela menor posição das suas tags; "Outras" no fim."""
        first_position: dict[str, int] = {}
        for tag in self.tags():
            first_position.setdefault(tag.category or UNCATEGORIZED, tag.position)
        return sorted(first_position, key=lambda c: (c == UNCATEGORIZED, first_position[c]))

    def local_file_of(self, track: Track) -> Path:
        if track.local_path is None:
            raise ValueError(f"A faixa {track.id} não é local.")
        return self._sound_folder.absolute_path(track.local_path)

    def add_remote_track(
        self,
        title: str,
        origin_url: str,
        source_kind: SourceKind,
        duration_s: float | None = None
    ) -> Track:
        """Registra uma faixa de streaming ou cache. Duração 0 (desconhecida) vira None."""
        duration = duration_s if duration_s and duration_s > 0 else None
        track = Track(
            id=new_track_id(),
            title=title.strip() or origin_url,
            kind=infer_kind(duration),
            source_kind=source_kind,
            origin_url=origin_url,
            duration_s=duration,
            added_at=_now(),
        )
        self._save(track)
        return track

    def link_origin(self, local_file: Path, origin_url: str) -> Track | None:
        """Liga um arquivo baixado à página de origem (assim ele ganha capa e pode ser baixado de novo)."""
        target = local_file.resolve()
        for track in self._tracks.values():
            if track.local_path and self.local_file_of(track).resolve() == target:
                if track.origin_url == origin_url:
                    return track
                updated = replace(track, origin_url=origin_url)
                self._save(updated)
                return updated
        return None

    # ---------------- Tags e formato ----------------

    def create_tag(self, name: str, category: str | None) -> Tag:
        """Cria a tag no fim da categoria. Nome repetido (ignorando caixa) -> ValueError."""
        category = None if category in (None, UNCATEGORIZED) else category
        last = max((t.position for t in self._tags.values()), default=-1)
        with self._repository.unit_of_work() as uow:
            tag = uow.save_tag(Tag(id=None, name=name.strip(), category=category, position=last + 1))
        self._tags[tag.id] = tag
        self._index.set_tags(self._tags.values())
        return tag

    def set_track_tags(self, track_id: TrackId, tag_ids: frozenset[TagId]) -> Track:
        track = self._require(track_id)
        unknown = tag_ids - self._tags.keys()
        if unknown:
            raise KeyError(f"Tags inexistentes: {sorted(unknown)}")
        updated = replace(track, tag_ids=frozenset(tag_ids))
        self._save(updated)
        return updated

    def toggle_track_tag(self, track_id: TrackId, tag_id: TagId) -> Track:
        track = self._require(track_id)
        return self.set_track_tags(track_id, track.tag_ids ^ {tag_id})

    def set_track_kind(self, track_id: TrackId, kind: TrackKind) -> Track:
        updated = replace(self._require(track_id), kind=kind)
        self._save(updated)
        return updated

    def remove_track(self, track_id: TrackId) -> Track | None:
        track = self._tracks.get(track_id)
        if track is None:
            return None
        with self._repository.unit_of_work() as uow:
            uow.delete_track(track_id)
        del self._tracks[track_id]
        self._index.remove(track_id)
        return track

    def refresh_local_files(self) -> SyncReport:
        """
        Reconcilia a pasta de áudios com o catálogo:
        - arquivo novo vira faixa nova;
        - faixa cujo arquivo sumiu é marcada como `missing` (nunca apagada: mantém tags e cenas);
        - faixa `missing` cujo arquivo voltou é restaurada;
        - faixa presente sem duração é sondada; a duração define o tipo inicial (efeito ou música).
        """
        on_disk = set(self._sound_folder.list_audio_files())
        known = {t.local_path: t for t in self._tracks.values() if t.source_kind == SourceKind.LOCAL}

        added = []
        for path in sorted(on_disk - known.keys()):
            duration = self._probe_duration(path)
            added.append(Track(
                id=new_track_id(),
                title=title_from_filename(PurePosixPath(path).stem),
                kind=infer_kind(duration),
                source_kind=SourceKind.LOCAL,
                local_path=path,
                duration_s=duration,
                added_at=_now(),
            ))

        changed: list[Track] = []
        went_missing = restored = probed = 0
        for path, track in known.items():
            present = path in on_disk
            updated = replace(track, missing=not present)
            if present and track.duration_s is None and (duration := self._probe_duration(path)) is not None:
                updated = replace(updated, duration_s=duration, kind=infer_kind(duration))
                probed += 1
            if updated == track:
                continue
            changed.append(updated)
            if updated.missing:
                went_missing += 1
            elif track.missing:
                restored += 1

        self._save(*added, *changed)
        return SyncReport(added=len(added), went_missing=went_missing, restored=restored, probed=probed)

    def _probe_duration(self, relative_path: str) -> float | None:
        if self._probe is None:
            return None
        return self._probe.duration_of(self._sound_folder.absolute_path(relative_path))

    def _save(self, *tracks: Track) -> None:
        if not tracks:
            return
        with self._repository.unit_of_work() as uow:
            for track in tracks:
                uow.save_track(track)
        for track in tracks:
            self._tracks[track.id] = track
            if track.missing:
                self._index.remove(track.id)
            else:
                self._index.upsert(track)

    def _require(self, track_id: TrackId) -> Track:
        track = self._tracks.get(track_id)
        if track is None:
            raise KeyError(f"Faixa {track_id} não existe.")
        return track


def _now() -> datetime:
    return datetime.now(timezone.utc)
