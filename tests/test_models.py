import unittest

from domain.models import (
    SourceKind, Tag, Track, TrackKind, infer_kind, normalize_title, title_from_filename
)


class NormalizeTitleTest(unittest.TestCase):
    def test_remove_acentos_caixa_e_espacos_repetidos(self):
        self.assertEqual(normalize_title("  Tensão   ÉPICA na Taverna "), "tensao epica na taverna")

    def test_title_from_filename(self):
        self.assertEqual(title_from_filename("taverna_do-anao"), "Taverna Do Anao")


class InferKindTest(unittest.TestCase):
    def test_curto_e_efeito_longo_ou_desconhecido_e_musica(self):
        self.assertEqual(infer_kind(4.0), TrackKind.SFX)
        self.assertEqual(infer_kind(30.0), TrackKind.SFX)
        self.assertEqual(infer_kind(30.5), TrackKind.MUSIC)
        self.assertEqual(infer_kind(None), TrackKind.MUSIC)


class TrackValidationTest(unittest.TestCase):
    def _track(self, **overrides):
        fields = dict(id="t1", title="Chuva", kind=TrackKind.AMBIENCE,
                      source_kind=SourceKind.LOCAL, local_path="chuva.ogg")
        fields.update(overrides)
        return Track(**fields)

    def test_faixa_valida(self):
        self.assertEqual(self._track().local_path, "chuva.ogg")

    def test_local_sem_caminho(self):
        with self.assertRaises(ValueError):
            self._track(local_path=None)

    def test_remota_sem_url(self):
        with self.assertRaises(ValueError):
            self._track(source_kind=SourceKind.STREAM, local_path=None)

    def test_volume_fora_do_intervalo(self):
        with self.assertRaises(ValueError):
            self._track(default_volume=1.2)

    def test_duracao_zero(self):
        with self.assertRaises(ValueError):
            self._track(duration_s=0.0)

    def test_titulo_vazio(self):
        with self.assertRaises(ValueError):
            self._track(title="   ")

    def test_tag_sem_nome(self):
        with self.assertRaises(ValueError):
            Tag(id=None, name=" ")


if __name__ == "__main__":
    unittest.main()
