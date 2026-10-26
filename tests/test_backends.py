"""
Integração com as bibliotecas reais, em saídas de áudio "dummy" (sem som, sem placa).
São puladas se o pygame ou a libVLC não estiverem instalados.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

from audio_fakes import write_wav
from domain.models import PlayableSource
from domain.ports import BackendEvent, VoiceLimitReached

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

try:
    from infrastructure.backends.pygame_backend import PygameBackend
except ImportError:
    PygameBackend = None

try:
    from infrastructure.backends.vlc_backend import VlcBackend, VlcMediaProbe
except (ImportError, OSError):
    VlcBackend = VlcMediaProbe = None


def wait_until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class _WavFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.short = PlayableSource(str(write_wav(root / "curto.wav", 0.3)), is_remote=False)
        cls.long = PlayableSource(str(write_wav(root / "longo.wav", 1.5)), is_remote=False)
        cls.missing = PlayableSource(str(root / "nao_existe.wav"), is_remote=False)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


@unittest.skipIf(PygameBackend is None, "pygame não instalado")
class PygameBackendTest(_WavFixture):
    def setUp(self):
        self.backend = PygameBackend(num_channels=2, cache_budget_bytes=10 * 1024 * 1024)
        self.backend.open()
        self.events = []

    def tearDown(self):
        self.backend.close()

    def poll_until_event(self, voice, timeout=3.0):
        return wait_until(lambda: voice.poll() or self.events, timeout)

    def test_toca_ate_o_fim_e_avisa(self):
        voice = self.backend.create_voice(self.short, loop=False, on_event=self.events.append)
        voice.apply_gain(0.5, force=True)
        voice.start()
        self.assertAlmostEqual(voice.duration(), 0.3, places=2)
        self.assertFalse(voice.supports_seek)

        self.assertTrue(self.poll_until_event(voice))
        self.assertEqual(self.events, [BackendEvent.ENDED])
        voice.stop()

    def test_loop_emenda_sem_avisar_fim_e_para_quando_desligado(self):
        voice = self.backend.create_voice(self.short, loop=True, on_event=self.events.append)
        voice.start()
        self.assertFalse(self.poll_until_event(voice, timeout=1.0))  # ~3 repetições, nenhum fim

        voice.set_loop(False)
        self.assertTrue(self.poll_until_event(voice, timeout=2.0))
        voice.stop()

    def test_pausa_congela_a_posicao(self):
        voice = self.backend.create_voice(self.long, loop=False, on_event=self.events.append)
        voice.start()
        time.sleep(0.15)
        voice.pause()
        frozen = voice.position()
        time.sleep(0.15)
        self.assertEqual(voice.position(), frozen)
        voice.resume()
        self.assertTrue(wait_until(lambda: voice.position() > frozen + 0.05))
        voice.stop()

    def test_canais_sao_um_pool_explicito(self):
        a = self.backend.create_voice(self.long, loop=False, on_event=self.events.append)
        b = self.backend.create_voice(self.long, loop=False, on_event=self.events.append)
        a.start()
        b.start()
        with self.assertRaises(VoiceLimitReached):
            self.backend.create_voice(self.long, loop=False, on_event=self.events.append)
        self.assertTrue(a._channel.get_busy())  # Ninguém tomou o canal de A

        a.stop()
        c = self.backend.create_voice(self.short, loop=False, on_event=self.events.append)
        c.stop()
        b.stop()

    def test_mesmo_arquivo_compartilha_o_som_decodificado(self):
        a = self.backend.create_voice(self.short, loop=False, on_event=self.events.append)
        b = self.backend.create_voice(self.short, loop=False, on_event=self.events.append)
        self.assertIs(a._sound, b._sound)
        a.stop()
        b.stop()

    def test_volume_quantizado(self):
        voice = self.backend.create_voice(self.long, loop=False, on_event=self.events.append)
        voice.start()
        voice.apply_gain(0.5, force=True)
        self.assertAlmostEqual(voice._channel.get_volume(), 0.5, places=2)
        voice.stop()

    def test_arquivo_inexistente_falha_sem_gastar_canal(self):
        with self.assertRaises(Exception):
            self.backend.create_voice(self.missing, loop=False, on_event=self.events.append)
        self.assertEqual(len(self.backend._free), 2)


@unittest.skipIf(PygameBackend is None, "pygame não instalado")
class SoundCacheTest(_WavFixture):
    def test_despeja_por_lru_mas_nunca_um_som_em_uso(self):
        from infrastructure.backends.pygame_backend import SoundCache
        backend = PygameBackend(num_channels=2)
        backend.open()
        try:
            cache = SoundCache(budget_bytes=1)  # Qualquer som estoura o orçamento
            cache.acquire(self.short.uri)
            self.assertGreater(cache.total_bytes, 0)  # Em uso: fica
            cache.release(self.short.uri)
            self.assertEqual(cache.total_bytes, 0)    # Livre: sai
        finally:
            backend.close()


@unittest.skipIf(VlcBackend is None, "libVLC não instalada")
class VlcBackendTest(_WavFixture):
    def setUp(self):
        self.backend = VlcBackend(max_voices=1, extra_args=("--aout=dummy",))
        self.backend.open()
        self.events = []

    def tearDown(self):
        self.backend.close()

    def test_eventos_de_inicio_e_fim(self):
        voice = self.backend.create_voice(self.short, loop=False, on_event=self.events.append)
        voice.apply_gain(0.7, force=True)
        voice.start()
        self.assertTrue(wait_until(lambda: BackendEvent.ENDED in self.events))
        self.assertEqual(self.events[0], BackendEvent.PLAYING)
        voice.stop()

    def test_restart_toca_de_novo(self):
        voice = self.backend.create_voice(self.short, loop=True, on_event=self.events.append)
        voice.start()
        self.assertTrue(wait_until(lambda: BackendEvent.ENDED in self.events))
        self.events.clear()
        voice.restart()
        self.assertTrue(wait_until(lambda: BackendEvent.ENDED in self.events))
        voice.stop()

    def test_limite_de_vozes_e_liberacao(self):
        voice = self.backend.create_voice(self.long, loop=False, on_event=self.events.append)
        with self.assertRaises(VoiceLimitReached):
            self.backend.create_voice(self.long, loop=False, on_event=self.events.append)
        voice.stop()
        voice.stop()  # Idempotente
        self.backend.create_voice(self.long, loop=False, on_event=self.events.append).stop()

    def test_seek_e_duracao(self):
        voice = self.backend.create_voice(self.long, loop=False, on_event=self.events.append)
        voice.start()
        self.assertTrue(wait_until(lambda: voice.duration() is not None))
        self.assertAlmostEqual(voice.duration(), 1.5, places=1)
        voice.pause()
        voice.seek(1.0)
        self.assertTrue(wait_until(lambda: voice.position() >= 0.9))
        voice.stop()

    def test_arquivo_inexistente_vira_evento_de_erro(self):
        voice = self.backend.create_voice(self.missing, loop=False, on_event=self.events.append)
        voice.start()
        self.assertTrue(wait_until(lambda: BackendEvent.ERROR in self.events))
        voice.stop()


@unittest.skipIf(VlcMediaProbe is None, "libVLC não instalada")
class VlcMediaProbeTest(_WavFixture):
    def test_le_a_duracao_pelo_cabecalho(self):
        probe = VlcMediaProbe()
        try:
            self.assertAlmostEqual(probe.duration_of(Path(self.long.uri)), 1.5, places=1)
            self.assertIsNone(probe.duration_of(Path(self.missing.uri)))
        finally:
            probe.close()


if __name__ == "__main__":
    unittest.main()
