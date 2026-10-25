"""
Importação do `library.json` (formato v1 do LibraryManager) para o repositório.
Regras de segurança:
- JSON ilegível nunca é sobrescrito nem apagado: o arquivo fica intacto e o app segue.
- O arquivo só é renomeado para `.bak` depois que a transação de importação confirmou.
- Faixas cujo id já está no banco são puladas, então rodar de novo não estraga edições.
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain.models import SourceKind, Track, infer_kind
from domain.ports import LibraryRepository

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImportReport:
    imported: int
    skipped: int
    backup_path: Path


def migrate_legacy_library(json_path: Path, repository: LibraryRepository) -> ImportReport | None:
    """Importa e arquiva o JSON antigo. Devolve None se não havia nada a importar."""
    if not json_path.exists():
        return None

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        entries = payload["tracks"]
        if not isinstance(entries, dict):
            raise TypeError("'tracks' deveria ser um objeto")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning(
            "Não foi possível ler %s (%s). O arquivo foi mantido intacto; corrija-o e reabra o app "
            "para importar.", json_path, exc
        )
        return None

    existing_ids = {track.id for track in repository.load().tracks}
    imported = skipped = 0

    with repository.unit_of_work() as uow:
        for key, entry in entries.items():
            track = _entry_to_track(key, entry)
            if track is None or track.id in existing_ids:
                skipped += 1
                continue
            uow.save_track(track)
            imported += 1

    backup_path = json_path.with_name(json_path.name + ".bak")
    json_path.replace(backup_path)
    log.info("library.json importado: %d faixa(s), %d pulada(s). Cópia em %s",
             imported, skipped, backup_path)
    return ImportReport(imported=imported, skipped=skipped, backup_path=backup_path)


def _entry_to_track(key: str, entry: Any) -> Track | None:
    try:
        duration = float(entry.get("duration") or 0.0) or None
        return Track(
            id=str(entry.get("sound_id") or key),
            title=str(entry.get("title") or "Áudio sem título"),
            kind=infer_kind(duration),
            source_kind=SourceKind(entry.get("mode", "stream")),
            origin_url=entry.get("url"),
            duration_s=duration,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        log.warning("Entrada %r do library.json ignorada: %s", key, exc)
        return None
