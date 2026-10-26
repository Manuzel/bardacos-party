import unittest

from presentation.canvas_draw import layout_chips, wrap_lines
from presentation.grid import format_duration


def measure(text: str) -> int:
    """Fonte monoespaçada de mentira: 10 px por caractere."""
    return 10 * len(text)


class WrapLinesTest(unittest.TestCase):
    def test_cabe_numa_linha(self):
        self.assertEqual(wrap_lines("Capela Sombria", measure, 200, 2), ["Capela Sombria"])

    def test_quebra_por_palavras(self):
        self.assertEqual(wrap_lines("Templo do Sol Pavoroso", measure, 130, 2), ["Templo do Sol", "Pavoroso"])

    def test_sobra_vira_reticencias_na_ultima_linha(self):
        lines = wrap_lines("Dangerous Encounter D&D TTRPG Music One Hour", measure, 130, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("…"))
        self.assertLessEqual(measure(lines[1]), 130)

    def test_palavra_maior_que_a_largura_e_cortada(self):
        (line,) = wrap_lines("Supercalifragilístico", measure, 80, 2)
        self.assertTrue(line.endswith("…"))
        self.assertLessEqual(measure(line), 80)

    def test_vazio(self):
        self.assertEqual(wrap_lines("", measure, 100, 2), [])


class LayoutChipsTest(unittest.TestCase):
    def test_flow_quebra_linhas(self):
        layout = layout_chips(["aaaa", "bbbb", "cccc"], measure, max_width=120, chip_height=20, pad_x=5, gap=4)
        self.assertEqual([(b.x, b.row) for b in layout.boxes], [(0, 0), (54, 0), (0, 1)])
        self.assertEqual(layout.hidden, 0)
        self.assertEqual(layout.height, 44)

    def test_excesso_vira_mais_n_na_ultima_linha(self):
        labels = ["fantasia", "suspense", "coral", "orquestral", "masmorra", "templo"]
        layout = layout_chips(labels, measure, max_width=200, chip_height=20, pad_x=5, gap=4, max_rows=2)
        self.assertGreater(layout.hidden, 0)
        self.assertEqual(len(layout.boxes) + layout.hidden, len(labels))
        self.assertTrue(all(b.row < 2 for b in layout.boxes))
        # O "+N" cabe na linha
        self.assertLessEqual(layout.more_x + measure(f"+{layout.hidden}"), 200)

    def test_chip_maior_que_a_linha_e_limitado(self):
        layout = layout_chips(["x" * 50], measure, max_width=100, chip_height=20)
        self.assertEqual(layout.boxes[0].width, 100)

    def test_sem_chips(self):
        layout = layout_chips([], measure, max_width=100, chip_height=20, max_rows=3)
        self.assertEqual((layout.boxes, layout.hidden, layout.height), ((), 0, 0))


class FormatDurationTest(unittest.TestCase):
    def test_formatos(self):
        self.assertEqual(format_duration(176), "2:56")
        self.assertEqual(format_duration(4213), "1:10:13")
        self.assertEqual(format_duration(None), "")


if __name__ == "__main__":
    unittest.main()
