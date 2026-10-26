import unittest

from application.audio.selector import BackendSelector, NoBackendAvailable
from audio_fakes import FakeBackend, make_track
from domain.models import BackendHint, PlayableSource, TrackKind

LOCAL = PlayableSource("/sons/chuva.ogg", is_remote=False)
STREAM = PlayableSource("https://rr1.googlevideo.com/x", is_remote=True)


class BackendSelectorTest(unittest.TestCase):
    def setUp(self):
        self.vlc = FakeBackend(BackendHint.VLC)
        self.pygame = FakeBackend(BackendHint.PYGAME)
        self.selector = BackendSelector(vlc=self.vlc, pygame=self.pygame)

    def test_stream_so_no_vlc(self):
        self.assertEqual(self.selector.candidates(make_track(duration_s=3), STREAM), [self.vlc])

    def test_local_curto_vai_para_o_pygame_com_vlc_de_reserva(self):
        self.assertEqual(self.selector.candidates(make_track(duration_s=12), LOCAL), [self.pygame, self.vlc])

    def test_efeito_sem_duracao_conhecida_tambem_e_curto(self):
        track = make_track(duration_s=None, kind=TrackKind.SFX)
        self.assertEqual(self.selector.candidates(track, LOCAL)[0], self.pygame)

    def test_local_longo_ou_desconhecido_vai_para_o_vlc(self):
        self.assertEqual(self.selector.candidates(make_track(duration_s=600), LOCAL), [self.vlc])
        self.assertEqual(self.selector.candidates(make_track(duration_s=None), LOCAL), [self.vlc])

    def test_duracao_conhecida_vence_o_tipo(self):
        # Um "efeito" de 5 minutos não deve ser decodificado inteiro em RAM
        track = make_track(duration_s=300, kind=TrackKind.SFX)
        self.assertEqual(self.selector.candidates(track, LOCAL), [self.vlc])

    def test_escolha_forcada_na_faixa(self):
        track = make_track(duration_s=600, backend_hint=BackendHint.PYGAME)
        self.assertEqual(self.selector.candidates(track, LOCAL), [self.pygame, self.vlc])

    def test_escolha_forcada_nao_vale_para_stream(self):
        track = make_track(duration_s=3, backend_hint=BackendHint.PYGAME)
        self.assertEqual(self.selector.candidates(track, STREAM), [self.vlc])

    def test_sem_vlc(self):
        selector = BackendSelector(vlc=None, pygame=self.pygame)
        with self.assertRaises(NoBackendAvailable):
            selector.candidates(make_track(), STREAM)
        with self.assertRaises(NoBackendAvailable):
            selector.candidates(make_track(duration_s=600), LOCAL)
        # Duração desconhecida: tenta o Pygame, como o app antigo fazia
        self.assertEqual(selector.candidates(make_track(duration_s=None), LOCAL), [self.pygame])

    def test_sem_pygame_tudo_vai_para_o_vlc(self):
        selector = BackendSelector(vlc=self.vlc, pygame=None)
        self.assertEqual(selector.candidates(make_track(duration_s=2), LOCAL), [self.vlc])

    def test_nenhum_backend(self):
        with self.assertRaises(NoBackendAvailable):
            BackendSelector(vlc=None, pygame=None).candidates(make_track(duration_s=2), LOCAL)

    def test_disable(self):
        self.selector.disable(self.vlc)
        self.assertEqual(self.selector.backends, [self.pygame])


if __name__ == "__main__":
    unittest.main()
