import unittest
from datetime import datetime, timedelta, timezone

from application.library_index import LibraryIndex, LibraryQuery, SortOrder
from audio_fakes import make_track
from domain.models import SourceKind, Tag, TrackKind

FANTASIA, SUSPENSE, MASMORRA, CORAL = 1, 2, 3, 4
TAGS = [Tag(FANTASIA, "Fantasia"), Tag(SUSPENSE, "Suspense"), Tag(MASMORRA, "Masmorra"), Tag(CORAL, "Coral")]
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def track(track_id, title, tags=(), **overrides):
    return make_track(track_id, title=title, tag_ids=frozenset(tags), **overrides)


class LibraryIndexTest(unittest.TestCase):
    def setUp(self):
        self.index = LibraryIndex(TAGS)
        self.tracks = [
            track("capela", "Capela Sombria", {FANTASIA, SUSPENSE, CORAL}, duration_s=176, added_at=T0),
            track("templo", "Templo do Sol Pavoroso", {FANTASIA, SUSPENSE, MASMORRA}, duration_s=186,
                  added_at=T0 + timedelta(days=2)),
            track("abismo", "Ecos do Abismo", {FANTASIA, MASMORRA, CORAL}, duration_s=196,
                  added_at=T0 + timedelta(days=1)),
            track("grito", "Grito", set(), kind=TrackKind.SFX, duration_s=2,
                  source_kind=SourceKind.STREAM, local_path=None, origin_url="https://youtu.be/abcdefghijk"),
        ]
        for t in self.tracks:
            self.index.upsert(t)

    def ids(self, **query):
        return [t.id for t in self.index.query(LibraryQuery(**query))]

    def test_sem_filtro_tudo_em_ordem_alfabetica(self):
        self.assertEqual(self.ids(), ["capela", "abismo", "grito", "templo"])

    def test_incluir_tags_e_e(self):
        self.assertEqual(self.ids(include_tags=frozenset({FANTASIA, SUSPENSE})), ["capela", "templo"])
        self.assertEqual(self.ids(include_tags=frozenset({SUSPENSE, MASMORRA, CORAL})), [])

    def test_excluir_tags(self):
        self.assertEqual(self.ids(include_tags=frozenset({FANTASIA}), exclude_tags=frozenset({CORAL})), ["templo"])

    def test_formato_e_origem_sao_ou_dentro_do_grupo(self):
        self.assertEqual(self.ids(kinds=frozenset({TrackKind.SFX})), ["grito"])
        self.assertEqual(len(self.ids(sources=frozenset({SourceKind.LOCAL, SourceKind.STREAM}))), 4)
        self.assertEqual(self.ids(exclude_sources=frozenset({SourceKind.LOCAL})), ["grito"])
        self.assertEqual(len(self.ids(exclude_kinds=frozenset({TrackKind.SFX}))), 3)

    def test_texto_busca_titulo_e_tags_sem_acento(self):
        self.assertEqual(self.ids(text="templo pavoroso"), ["templo"])
        self.assertEqual(self.ids(text="  ECOS  "), ["abismo"])
        self.assertEqual(self.ids(text="coral sombria"), ["capela"])  # tag + título

    def test_ordenacoes(self):
        self.assertEqual(self.ids(sort=SortOrder.RECENT)[:3], ["templo", "abismo", "capela"])
        self.assertEqual(self.ids(sort=SortOrder.DURATION), ["grito", "capela", "templo", "abismo"])

    def test_contagens_do_resultado(self):
        result = self.index.query(LibraryQuery(include_tags=frozenset({FANTASIA})))
        counts = self.index.facet_counts(result)
        self.assertEqual(counts.tags[FANTASIA], 3)
        self.assertEqual(counts.tags[SUSPENSE], 2)
        self.assertEqual(counts.kinds[TrackKind.MUSIC], 3)
        self.assertEqual(counts.sources[SourceKind.STREAM], 0)

    def test_upsert_atualiza_tags_e_remove_tira_do_indice(self):
        from dataclasses import replace
        self.index.upsert(replace(self.tracks[0], tag_ids=frozenset({MASMORRA})))
        self.assertEqual(self.ids(include_tags=frozenset({SUSPENSE})), ["templo"])
        self.assertIn("capela", self.ids(include_tags=frozenset({MASMORRA})))

        self.index.remove("templo")
        self.index.remove("nao-existe")
        self.assertEqual(self.ids(include_tags=frozenset({SUSPENSE})), [])
        self.assertEqual(len(self.index), 3)

    def test_nome_novo_de_tag_entra_na_busca(self):
        self.index.set_tags(TAGS + [Tag(9, "Tambores")])
        self.index.upsert(track("tambor", "Ritmo", {9}))
        self.assertEqual(self.ids(text="tambores"), ["tambor"])


if __name__ == "__main__":
    unittest.main()
