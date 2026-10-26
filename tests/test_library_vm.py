import unittest

from application.library import LibraryService
from application.library_index import SortOrder
from domain.models import SourceKind, TrackKind
from infrastructure.persistence.sqlite import SqliteLibraryRepository
from presentation.library_vm import ChipState, FacetType, LibraryViewModel
from test_library_service import FakeSoundFolder


class LibraryViewModelTest(unittest.TestCase):
    def setUp(self):
        self.repo = SqliteLibraryRepository(":memory:")
        self.library = LibraryService(self.repo, FakeSoundFolder("capela.ogg", "templo.ogg", "grito.wav"))
        self.library.refresh_local_files()
        self.tag = {t.name: t.id for t in self.library.tags()}
        by_title = {t.title: t for t in self.library.tracks()}
        self.capela, self.templo, self.grito = by_title["Capela"], by_title["Templo"], by_title["Grito"]
        self.library.set_track_tags(self.capela.id, frozenset({self.tag["Fantasia"], self.tag["Coral"]}))
        self.library.set_track_tags(self.templo.id, frozenset({self.tag["Fantasia"], self.tag["Masmorra"]}))
        self.library.set_track_kind(self.grito.id, TrackKind.SFX)
        self.vm = LibraryViewModel(self.library)
        self.notified = 0
        self.vm.subscribe(self._on_change)

    def _on_change(self):
        self.notified += 1

    def tearDown(self):
        self.repo.close()

    def titles(self):
        return [t.title for t in self.vm.results]

    def chip(self, group_title, label):
        group = next(g for g in self.vm.groups() if g.title == group_title)
        return next(c for c in group.chips if c.label == label)

    def test_incluir_excluir_e_desligar(self):
        fantasia = (FacetType.TAG, self.tag["Fantasia"])
        self.vm.toggle(fantasia)
        self.assertEqual(self.titles(), ["Capela", "Templo"])
        self.vm.toggle((FacetType.TAG, self.tag["Coral"]), exclude=True)
        self.assertEqual(self.titles(), ["Templo"])
        self.vm.toggle(fantasia)  # Segundo clique desliga
        self.assertEqual(self.vm.state_of(fantasia), None)
        self.assertEqual(self.titles(), ["Grito", "Templo"])
        self.assertGreater(self.notified, 0)

    def test_incluir_depois_excluir_troca_o_estado(self):
        key = (FacetType.TAG, self.tag["Coral"])
        self.vm.toggle(key)
        self.vm.toggle(key, exclude=True)
        self.assertEqual(self.vm.state_of(key), ChipState.EXCLUDE)

    def test_contagens_e_chips_apagados(self):
        self.vm.toggle((FacetType.TAG, self.tag["Fantasia"]))
        self.assertEqual(self.chip("Lugar", "Masmorra").count, 1)
        self.assertTrue(self.chip("Lugar", "Taverna").disabled)
        self.assertEqual(self.chip("Cenário", "Fantasia").state, ChipState.INCLUDE)
        cenario = next(g for g in self.vm.groups() if g.title == "Cenário")
        self.assertEqual(cenario.active, 1)

    def test_formato_conta_ignorando_o_proprio_grupo(self):
        self.vm.toggle((FacetType.KIND, TrackKind.SFX))
        self.assertEqual(self.titles(), ["Grito"])
        # Música continua clicável para somar ao filtro (OU)
        self.assertFalse(self.chip("Formato", "Música").disabled)
        self.vm.toggle((FacetType.KIND, TrackKind.MUSIC))
        self.assertEqual(len(self.vm.results), 3)

    def test_origem(self):
        self.vm.toggle((FacetType.SOURCE, SourceKind.STREAM))
        self.assertEqual(self.vm.results, [])
        self.assertEqual(self.chip("Origem", "Local").count, 3)

    def test_chips_ativos_em_ordem_de_clique_e_limpar(self):
        self.vm.toggle((FacetType.TAG, self.tag["Masmorra"]))
        self.vm.toggle((FacetType.TAG, self.tag["Fantasia"]))
        self.vm.toggle((FacetType.TAG, self.tag["Coral"]), exclude=True)
        self.assertEqual([(c.label, c.state) for c in self.vm.active_chips()],
                         [("Masmorra", ChipState.INCLUDE), ("Fantasia", ChipState.INCLUDE),
                          ("Coral", ChipState.EXCLUDE)])
        self.vm.clear()
        self.assertEqual(self.vm.active_chips(), [])
        self.assertEqual(len(self.vm.results), 3)

    def test_busca_por_texto_e_ordenacao(self):
        self.vm.set_text("mas")  # Tag "Masmorra"
        self.assertEqual(self.titles(), ["Templo"])
        self.vm.set_text("")
        self.vm.cycle_sort()
        self.assertEqual(self.vm.sort, SortOrder.RECENT)
        self.assertEqual(self.vm.sort_label, "Recentes")

    def test_filtro_de_tags_da_lateral(self):
        self.vm.toggle((FacetType.TAG, self.tag["Coral"]))
        self.vm.set_tag_filter_text("masm")
        lugar = next(g for g in self.vm.groups() if g.title == "Lugar")
        self.assertEqual([c.label for c in lugar.chips], ["Masmorra"])
        # Um chip ativo nunca some da lateral
        timbre = next(g for g in self.vm.groups() if g.title == "Timbre")
        self.assertEqual([c.label for c in timbre.chips], ["Coral"])
        # Grupos sem chips somem; Origem e Formato ficam
        self.assertNotIn("Clima", [g.title for g in self.vm.groups()])

    def test_tags_do_card_com_as_filtradas_primeiro(self):
        self.vm.toggle((FacetType.TAG, self.tag["Masmorra"]))
        self.assertEqual(self.vm.card_tags(self.library.get(self.templo.id)),
                         [("Masmorra", True), ("Fantasia", False)])

    def test_tag_nova_aparece_e_filtro_de_tag_apagada_some(self):
        tag = self.library.create_tag("Tambores", "Timbre")
        self.library.toggle_track_tag(self.grito.id, tag.id)
        self.vm.refresh()
        self.assertEqual(self.chip("Timbre", "Tambores").count, 1)
        self.assertEqual(self.vm.tags_by_category()[0][0], "Cenário")


if __name__ == "__main__":
    unittest.main()
