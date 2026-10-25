import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from application.library import LibraryService
from domain.models import SourceKind, TrackKind
from infrastructure.persistence.sqlite import SqliteLibraryRepository
from infrastructure.sound_folder import LocalSoundFolder


class FakeSoundFolder:
    def __init__(self, *files: str):
        self.files = list(files)

    def list_audio_files(self) -> list[str]:
        return sorted(self.files)

    def absolute_path(self, relative_path: str) -> Path:
        return Path("/sons") / relative_path


class FailingCommitRepository(SqliteLibraryRepository):
    """Simula uma falha de disco: a transação é aberta, mas nunca confirma."""

    @contextmanager
    def unit_of_work(self):
        with super().unit_of_work() as uow:
            yield uow
            raise OSError("disco cheio")


class LibraryServiceTest(unittest.TestCase):
    def setUp(self):
        self.repo = SqliteLibraryRepository(":memory:")
        self.folder = FakeSoundFolder()
        self.library = LibraryService(self.repo, self.folder)

    def tearDown(self):
        self.repo.close()

    def test_arquivos_novos_viram_faixas_locais(self):
        self.folder.files = ["efeitos/espada_curta.wav", "taverna-alegre.mp3"]

        report = self.library.refresh_local_files()

        self.assertEqual((report.added, report.went_missing, report.restored), (2, 0, 0))
        espada, taverna = self.library.tracks()
        self.assertEqual(espada.title, "Espada Curta")
        self.assertEqual(espada.source_kind, SourceKind.LOCAL)
        self.assertEqual(self.library.local_file_of(espada), Path("/sons/efeitos/espada_curta.wav"))
        self.assertEqual(taverna.title, "Taverna Alegre")

    def test_refresh_repetido_nao_duplica(self):
        self.folder.files = ["chuva.ogg"]
        self.library.refresh_local_files()
        report = self.library.refresh_local_files()
        self.assertEqual((report.added, report.went_missing, report.restored), (0, 0, 0))
        self.assertEqual(len(self.library.tracks()), 1)

    def test_arquivo_sumido_e_marcado_e_restaurado_com_o_mesmo_id(self):
        self.folder.files = ["chuva.ogg"]
        self.library.refresh_local_files()
        (original,) = self.library.tracks()

        self.folder.files = []
        report = self.library.refresh_local_files()
        self.assertEqual(report.went_missing, 1)
        self.assertEqual(self.library.tracks(), [])
        self.assertTrue(self.library.tracks(include_missing=True)[0].missing)

        self.folder.files = ["chuva.ogg"]
        report = self.library.refresh_local_files()
        self.assertEqual(report.restored, 1)
        (restored,) = self.library.tracks()
        self.assertEqual(restored.id, original.id)

    def test_faixa_remota_com_duracao_zero_fica_sem_duracao(self):
        track = self.library.add_remote_track("Batalha", "https://youtu.be/x", SourceKind.STREAM, 0.0)
        self.assertIsNone(track.duration_s)
        self.assertEqual(track.kind, TrackKind.MUSIC)

        sfx = self.library.add_remote_track("Grito", "https://youtu.be/y", SourceKind.CACHE, 2.5)
        self.assertEqual(sfx.kind, TrackKind.SFX)

    def test_tudo_sobrevive_a_um_novo_servico_sobre_o_mesmo_banco(self):
        self.folder.files = ["chuva.ogg"]
        self.library.refresh_local_files()
        remote = self.library.add_remote_track("Batalha", "https://youtu.be/x", SourceKind.STREAM, 300)

        reloaded = LibraryService(self.repo, self.folder)

        self.assertEqual([t.title for t in reloaded.tracks()], ["Batalha", "Chuva"])
        self.assertEqual(reloaded.get(remote.id), remote)

    def test_remover_faixa(self):
        track = self.library.add_remote_track("Batalha", "https://youtu.be/x", SourceKind.STREAM)
        self.assertEqual(self.library.remove_track(track.id), track)
        self.assertIsNone(self.library.get(track.id))
        self.assertEqual(LibraryService(self.repo, self.folder).tracks(), [])
        self.assertIsNone(self.library.remove_track(track.id))

    def test_falha_ao_gravar_nao_altera_a_memoria(self):
        repo = FailingCommitRepository(":memory:")
        library = LibraryService(repo, FakeSoundFolder("chuva.ogg"))

        with self.assertRaises(OSError):
            library.refresh_local_files()

        self.assertEqual(library.tracks(), [])
        self.assertEqual(repo.load().tracks, ())
        repo.close()


class LocalSoundFolderTest(unittest.TestCase):
    def test_lista_audios_recursivamente_ignorando_outros_arquivos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "efeitos").mkdir()
            for name in ("efeitos/espada.WAV", "chuva.ogg", "notas.txt", ".gitkeep"):
                (root / name).touch()

            folder = LocalSoundFolder(root)

            self.assertEqual(folder.list_audio_files(), ["chuva.ogg", "efeitos/espada.WAV"])
            self.assertEqual(folder.absolute_path("chuva.ogg"), root.resolve() / "chuva.ogg")


if __name__ == "__main__":
    unittest.main()
