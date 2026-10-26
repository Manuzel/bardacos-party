"""
Casos de uso do catálogo de faixas.
Write-through: cada operação grava no repositório e, só se a transação confirmar,
atualiza o estado em memória. O disco é a fonte da verdade; a memória é o índice.
"""
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from domain.models import (
    SourceKind, Track, TrackId, infer_kind, new_track_id, normalize_title, title_from_filename
)
from domain.ports import LibraryRepository, MediaProbe, SoundFolder


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
        self._tracks: dict[TrackId, Track] = {t.id: t for t in repository.load().tracks}

    def tracks(self, include_missing: bool = False) -> list[Track]:
        """Faixas em ordem alfabética (sem acentos e sem caixa)."""
        visible = (t for t in self._tracks.values() if include_missing or not t.missing)
        return sorted(visible, key=lambda t: (normalize_title(t.title), t.id))

    def get(self, track_id: TrackId) -> Track | None:
        return self._tracks.get(track_id)

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
        )
        self._save(track)
        return track

    def remove_track(self, track_id: TrackId) -> Track | None:
        track = self._tracks.get(track_id)
        if track is None:
            return None
        with self._repository.unit_of_work() as uow:
            uow.delete_track(track_id)
        del self._tracks[track_id]
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
