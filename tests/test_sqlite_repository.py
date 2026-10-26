import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from domain.models import SourceKind, Tag, Track, TrackKind
from infrastructure.persistence.sqlite import (
    DEFAULT_TAGS, MIGRATIONS, SCHEMA_VERSION, SqliteLibraryRepository
)


def custom_tag_names(data) -> list[str]:
    """Tags criadas pelo teste (as padrão da migração 2 têm categoria)."""
    return [t.name for t in data.tags if t.category is None]


def make_track(track_id="t1", title="Chuva Forte", **overrides) -> Track:
    fields = dict(id=track_id, title=title, kind=TrackKind.AMBIENCE,
                  source_kind=SourceKind.LOCAL, local_path=f"{track_id}.ogg")
    fields.update(overrides)
    return Track(**fields)


class SqliteRepositoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "library.db"
        self.repo = SqliteLibraryRepository(self.db_path)

    def tearDown(self):
        self.repo.close()
        self._tmp.cleanup()

    def reopen(self) -> SqliteLibraryRepository:
        self.repo.close()
        self.repo = SqliteLibraryRepository(self.db_path)
        return self.repo

    def test_banco_novo_fica_na_versao_atual_e_reabrir_nao_migra_de_novo(self):
        self.assertEqual(self.repo.schema_version, SCHEMA_VERSION)
        self.assertEqual(self.reopen().schema_version, SCHEMA_VERSION)

    def test_ida_e_volta_com_tags_persiste_no_disco(self):
        with self.repo.unit_of_work() as uow:
            taverna = uow.save_tag(Tag(id=None, name="Bardo Cantor"))
            tensao = uow.save_tag(Tag(id=None, name="Tensão Máxima", color="#b91c1c", position=1))
            uow.save_track(make_track(tag_ids=frozenset({taverna.id, tensao.id}), duration_s=95.5))
            uow.save_track(make_track(
                "t2", "Batalha Épica", kind=TrackKind.MUSIC, source_kind=SourceKind.STREAM,
                local_path=None, origin_url="https://youtu.be/x", default_loop=True
            ))

        data = self.reopen().load()
        self.assertEqual(custom_tag_names(data), ["Bardo Cantor", "Tensão Máxima"])
        by_id = {t.id: t for t in data.tracks}
        self.assertEqual(by_id["t1"].tag_ids, frozenset({taverna.id, tensao.id}))
        self.assertEqual(by_id["t1"].duration_s, 95.5)
        self.assertEqual(by_id["t2"].source_kind, SourceKind.STREAM)
        self.assertTrue(by_id["t2"].default_loop)
        # Ordem alfabética ignorando acentos: "Batalha" antes de "Chuva"
        self.assertEqual([t.id for t in data.tracks], ["t2", "t1"])

    def test_salvar_de_novo_atualiza_e_substitui_as_tags(self):
        with self.repo.unit_of_work() as uow:
            a = uow.save_tag(Tag(id=None, name="A"))
            b = uow.save_tag(Tag(id=None, name="B"))
            uow.save_track(make_track(tag_ids=frozenset({a.id})))
        with self.repo.unit_of_work() as uow:
            uow.save_track(make_track(title="Chuva Fraca", tag_ids=frozenset({b.id})))

        (track,) = self.repo.load().tracks
        self.assertEqual(track.title, "Chuva Fraca")
        self.assertEqual(track.tag_ids, frozenset({b.id}))

    def test_excecao_no_meio_desfaz_a_transacao_inteira(self):
        with self.assertRaises(RuntimeError):
            with self.repo.unit_of_work() as uow:
                uow.save_track(make_track("t1"))
                uow.save_tag(Tag(id=None, name="Bardo"))
                raise RuntimeError("falha simulada")

        data = self.repo.load()
        self.assertEqual(data.tracks, ())
        self.assertEqual(custom_tag_names(data), [])
        # A conexão continua utilizável depois do rollback
        with self.repo.unit_of_work() as uow:
            uow.save_track(make_track("t1"))
        self.assertEqual(len(self.repo.load().tracks), 1)

    def test_apagar_tag_ou_faixa_remove_as_ligacoes(self):
        with self.repo.unit_of_work() as uow:
            tag = uow.save_tag(Tag(id=None, name="Batalha"))
            uow.save_track(make_track("t1", tag_ids=frozenset({tag.id})))
            uow.save_track(make_track("t2", tag_ids=frozenset({tag.id})))
        with self.repo.unit_of_work() as uow:
            uow.delete_tag(tag.id)
            uow.delete_track("t2")

        (track,) = self.repo.load().tracks
        self.assertEqual(track.tag_ids, frozenset())

    def test_nome_de_tag_repetido_ignora_caixa(self):
        with self.repo.unit_of_work() as uow:
            uow.save_tag(Tag(id=None, name="Bardo"))
        for name in ("BARDO", "taverna"):  # A segunda é uma das tags padrão
            with self.assertRaises(ValueError):
                with self.repo.unit_of_work() as uow:
                    uow.save_tag(Tag(id=None, name=name))

    def test_renomear_tag(self):
        with self.repo.unit_of_work() as uow:
            tag = uow.save_tag(Tag(id=None, name="Bardo", category="Clima"))
        with self.repo.unit_of_work() as uow:
            uow.save_tag(replace(tag, name="  Bardo do Anão "))
        (renamed,) = [t for t in self.repo.load().tags if t.id == tag.id]
        self.assertEqual((renamed.name, renamed.category), ("Bardo do Anão", "Clima"))

    def test_tag_inexistente_nao_pode_ser_ligada(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repo.unit_of_work() as uow:
                uow.save_track(make_track(tag_ids=frozenset({999})))

    def test_dois_registros_para_o_mesmo_arquivo_sao_recusados(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repo.unit_of_work() as uow:
                uow.save_track(make_track("t1", local_path="chuva.ogg"))
                uow.save_track(make_track("t2", local_path="chuva.ogg"))

    def test_banco_novo_vem_com_as_tags_padrao_em_categorias(self):
        tags = self.repo.load().tags
        expected = sum(len(names) for _, names in DEFAULT_TAGS)
        self.assertEqual(len(tags), expected)
        by_name = {t.name: t for t in tags}
        self.assertEqual(by_name["Masmorra"].category, "Lugar")
        self.assertEqual(by_name["Fantasia"].category, "Cenário")
        # A ordem das posições segue a ordem das categorias
        self.assertLess(by_name["Fantasia"].position, by_name["Combate"].position)

    def test_data_de_inclusao_e_gravada_e_preservada(self):
        with self.repo.unit_of_work() as uow:
            uow.save_track(make_track())
        (first,) = self.repo.load().tracks
        self.assertIsNotNone(first.added_at)
        with self.repo.unit_of_work() as uow:
            uow.save_track(replace(first, title="Outro título"))
        (again,) = self.repo.load().tracks
        self.assertEqual(again.added_at, first.added_at)

    def test_banco_da_versao_1_migra_sem_perder_faixas(self):
        self.repo.close()
        self.db_path.unlink()
        conn = sqlite3.connect(self.db_path)
        conn.executescript(MIGRATIONS[0] + "\nPRAGMA user_version = 1;")
        conn.execute(
            "INSERT INTO tracks (id, title, title_norm, kind, source_kind, local_path, created_at, updated_at)"
            " VALUES ('v1', 'Antiga', 'antiga', 'music', 'local', 'antiga.ogg', '2026-10-01T10:00:00+00:00', 'x')"
        )
        conn.execute("INSERT INTO tags (name, position) VALUES ('Minha Tag', 0)")
        conn.commit()
        conn.close()

        data = self.reopen().load()

        self.assertEqual(self.repo.schema_version, SCHEMA_VERSION)
        self.assertEqual([t.id for t in data.tracks], ["v1"])
        self.assertEqual(custom_tag_names(data), ["Minha Tag"])
        self.assertIn("Masmorra", {t.name for t in data.tags})

    def test_banco_de_versao_mais_nova_nao_abre(self):
        self.repo.close()
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.close()
        with self.assertRaises(RuntimeError):
            self.repo = SqliteLibraryRepository(self.db_path)
        self.repo = SqliteLibraryRepository(":memory:")  # para o tearDown


if __name__ == "__main__":
    unittest.main()
