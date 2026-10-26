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


class FakeProbe:
    def __init__(self, durations: dict[str, float | None]):
        self.durations = durations
        self.calls: list[Path] = []

    def duration_of(self, path: Path) -> float | None:
        self.calls.append(path)
        return self.durations.get(path.name)


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

    def test_sonda_define_duracao_e_tipo_dos_arquivos_novos(self):
        probe = FakeProbe({"espada.wav": 1.2, "taverna.mp3": 240.0})
        library = LibraryService(self.repo, FakeSoundFolder("espada.wav", "taverna.mp3", "estranho.ogg"), probe)

        library.refresh_local_files()

        by_title = {t.title: t for t in library.tracks()}
        self.assertEqual((by_title["Espada"].duration_s, by_title["Espada"].kind), (1.2, TrackKind.SFX))
        self.assertEqual(by_title["Taverna"].kind, TrackKind.MUSIC)
        self.assertIsNone(by_title["Estranho"].duration_s)
        self.assertEqual(probe.calls[0], Path("/sons/espada.wav"))

    def test_faixas_antigas_sem_duracao_sao_sondadas_uma_vez(self):
        self.folder.files = ["grito.wav"]
        self.library.refresh_local_files()  # Sem sonda (como no módulo 1)
        (old,) = self.library.tracks()

        probe = FakeProbe({"grito.wav": 2.0})
        library = LibraryService(self.repo, self.folder, probe)
        report = library.refresh_local_files()

        self.assertEqual(report.probed, 1)
        (track,) = library.tracks()
        self.assertEqual((track.id, track.duration_s, track.kind), (old.id, 2.0, TrackKind.SFX))
        library.refresh_local_files()
        self.assertEqual(len(probe.calls), 1)  # Já tem duração: não sonda de novo

    def test_arquivo_que_volta_e_sondado_e_restaurado_numa_gravacao_so(self):
        self.folder.files = ["grito.wav"]
        self.library.refresh_local_files()
        self.folder.files = []
        self.library.refresh_local_files()

        self.folder.files = ["grito.wav"]
        library = LibraryService(self.repo, self.folder, FakeProbe({"grito.wav": 2.0}))
        report = library.refresh_local_files()

        self.assertEqual((report.restored, report.probed), (1, 1))
        (track,) = library.tracks()
        self.assertFalse(track.missing)
        self.assertEqual(track.duration_s, 2.0)

    def test_tags_padrao_e_categorias_em_ordem(self):
        self.assertEqual(self.library.tag_categories(), ["Cenário", "Uso", "Timbre", "Lugar", "Clima"])
        self.library.create_tag("Bardo", None)
        self.assertEqual(self.library.tag_categories()[-1], "Outras")

    def test_criar_tag_no_fim_da_categoria_e_recusar_repetida(self):
        tag = self.library.create_tag("  Tambores ", "Timbre")
        self.assertEqual((tag.name, tag.category), ("Tambores", "Timbre"))
        timbre = [t.name for t in self.library.tags() if t.category == "Timbre"]
        self.assertEqual(timbre[-1], "Tambores")
        with self.assertRaises(ValueError):
            self.library.create_tag("CORAL", "Timbre")

    def test_marcar_tags_e_formato_grava_e_reindexa(self):
        from application.library_index import LibraryQuery
        self.folder.files = ["grito.wav"]
        self.library.refresh_local_files()
        (track,) = self.library.tracks()
        masmorra = next(t.id for t in self.library.tags() if t.name == "Masmorra")

        self.library.toggle_track_tag(track.id, masmorra)
        self.library.set_track_kind(track.id, TrackKind.SFX)

        reloaded = LibraryService(self.repo, self.folder)
        self.assertEqual(reloaded.get(track.id).tag_ids, frozenset({masmorra}))
        self.assertEqual(reloaded.get(track.id).kind, TrackKind.SFX)
        self.assertEqual(len(self.library.query(LibraryQuery(include_tags=frozenset({masmorra})))), 1)
        self.library.toggle_track_tag(track.id, masmorra)
        self.assertEqual(self.library.query(LibraryQuery(include_tags=frozenset({masmorra}))), [])
        with self.assertRaises(KeyError):
            self.library.set_track_tags(track.id, frozenset({99999}))

    def test_faixa_ausente_sai_da_busca_e_volta(self):
        from application.library_index import LibraryQuery
        self.folder.files = ["grito.wav"]
        self.library.refresh_local_files()
        self.folder.files = []
        self.library.refresh_local_files()
        self.assertEqual(self.library.query(LibraryQuery()), [])
        self.folder.files = ["grito.wav"]
        self.library.refresh_local_files()
        self.assertEqual(len(self.library.query(LibraryQuery())), 1)

    def test_link_origin_liga_o_arquivo_baixado_ao_youtube(self):
        self.folder.files = ["Batalha Épica.mp3"]
        self.library.refresh_local_files()
        url = "https://www.youtube.com/watch?v=oVZ8KGtZtwY"

        track = self.library.link_origin(Path("/sons/Batalha Épica.mp3"), url)

        self.assertEqual(track.origin_url, url)
        self.assertEqual(track.source_kind, SourceKind.LOCAL)
        self.assertIsNone(self.library.link_origin(Path("/sons/outro.mp3"), url))

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
