import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from domain.models import SourceKind, TrackKind
from infrastructure.persistence.legacy_json import migrate_legacy_library
from infrastructure.persistence.sqlite import SqliteLibraryRepository

LOGGER = "infrastructure.persistence.legacy_json"

# Mesmo formato gravado pelo LibraryManager (v1)
V1_PAYLOAD = {
    "version": "1.0",
    "tracks": {
        "stream_3370081b": {
            "sound_id": "stream_3370081b",
            "title": "Dangerous Encounter | D&D/TTRPG Music | 1 Hour",
            "url": "https://www.youtube.com/watch?v=oVZ8KGtZtwY",
            "mode": "stream",
            "duration": 4213.0,
        },
        "cache_a1b2c3d4": {
            "sound_id": "cache_a1b2c3d4",
            "title": "Espada desembainhando",
            "url": "https://www.youtube.com/watch?v=abc",
            "mode": "cache",
            "duration": 3.0,
        },
        "stream_semurl": {"sound_id": "stream_semurl", "title": "Quebrada", "mode": "stream"},
        "stream_semduracao": {
            "sound_id": "stream_semduracao", "title": "Sem duração",
            "url": "https://www.youtube.com/watch?v=def", "mode": "stream", "duration": 0.0,
        },
    },
}


class MigrateLegacyLibraryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.json_path = self.dir / "library.json"
        self.repo = SqliteLibraryRepository(self.dir / "library.db")

    def tearDown(self):
        self.repo.close()
        self._tmp.cleanup()

    def write_json(self, payload) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.json_path.write_text(text, encoding="utf-8")

    def test_importa_preserva_ids_e_arquiva_o_json(self):
        self.write_json(V1_PAYLOAD)

        with self.assertLogs(LOGGER, "WARNING") as logs:
            report = migrate_legacy_library(self.json_path, self.repo)

        self.assertIn("stream_semurl", logs.output[0])
        self.assertEqual((report.imported, report.skipped), (3, 1))
        self.assertFalse(self.json_path.exists())
        self.assertEqual(json.loads(report.backup_path.read_text(encoding="utf-8")), V1_PAYLOAD)

        by_id = {t.id: t for t in self.repo.load().tracks}
        self.assertEqual(set(by_id), {"stream_3370081b", "cache_a1b2c3d4", "stream_semduracao"})
        encounter = by_id["stream_3370081b"]
        self.assertEqual(encounter.source_kind, SourceKind.STREAM)
        self.assertEqual(encounter.origin_url, "https://www.youtube.com/watch?v=oVZ8KGtZtwY")
        self.assertEqual(encounter.kind, TrackKind.MUSIC)
        self.assertEqual(by_id["cache_a1b2c3d4"].kind, TrackKind.SFX)
        self.assertIsNone(by_id["stream_semduracao"].duration_s)

    def test_json_corrompido_fica_intacto_e_nada_e_importado(self):
        self.write_json('{"version": "1.0", "tracks": {')

        with self.assertLogs(LOGGER, "WARNING"):
            self.assertIsNone(migrate_legacy_library(self.json_path, self.repo))

        self.assertEqual(self.json_path.read_text(encoding="utf-8"), '{"version": "1.0", "tracks": {')
        self.assertEqual(self.repo.load().tracks, ())

    def test_formato_inesperado_tambem_fica_intacto(self):
        self.write_json({"version": "1.0", "tracks": ["lista", "em vez de objeto"]})
        with self.assertLogs(LOGGER, "WARNING"):
            self.assertIsNone(migrate_legacy_library(self.json_path, self.repo))
        self.assertTrue(self.json_path.exists())

    def test_sem_arquivo_nao_faz_nada(self):
        self.assertIsNone(migrate_legacy_library(self.json_path, self.repo))

    def test_reimportar_nao_sobrescreve_faixas_existentes(self):
        self.write_json(V1_PAYLOAD)
        with self.assertLogs(LOGGER, "WARNING"):
            migrate_legacy_library(self.json_path, self.repo)

        # Usuário renomeia a faixa no app; depois o JSON antigo reaparece
        (encounter,) = [t for t in self.repo.load().tracks if t.id == "stream_3370081b"]
        with self.repo.unit_of_work() as uow:
            uow.save_track(replace(encounter, title="Encontro Perigoso"))
        self.write_json(V1_PAYLOAD)

        with self.assertLogs(LOGGER, "WARNING"):
            report = migrate_legacy_library(self.json_path, self.repo)

        self.assertEqual((report.imported, report.skipped), (0, 4))
        titles = {t.id: t.title for t in self.repo.load().tracks}
        self.assertEqual(titles["stream_3370081b"], "Encontro Perigoso")


if __name__ == "__main__":
    unittest.main()
