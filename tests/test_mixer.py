import unittest

from application.audio.messages import (
    Seek, SetLoop, SetVolume, Stop, StopAll, TogglePlay, VoiceEnded, VoiceFailed, VoiceState
)
from application.audio.mixer import BackendSignal, Mixer
from application.audio.selector import BackendSelector
from audio_fakes import FakeBackend, make_track
from domain.models import BackendHint, PlayableSource
from domain.ports import BackendEvent

LOCAL = PlayableSource("/sons/x.ogg", is_remote=False)
STREAM = PlayableSource("https://rr1.googlevideo.com/x", is_remote=True)


def toggle(voice_id="t1", duration_s=600, source=LOCAL, volume=0.8, loop=False) -> TogglePlay:
    return TogglePlay(voice_id, make_track(voice_id, duration_s=duration_s), source, volume, loop)


class MixerTest(unittest.TestCase):
    def setUp(self):
        self.vlc = FakeBackend(BackendHint.VLC)
        self.pygame = FakeBackend(BackendHint.PYGAME, supports_seek=False)
        self.events = []
        self.inbox = []
        self.mixer = Mixer(BackendSelector(self.vlc, self.pygame), emit=self.events.append, post=self.inbox.append)

    def deliver_backend_signals(self):
        """Simula a thread entregando à fila o que os backends postaram."""
        pending, self.inbox[:] = list(self.inbox), []
        self.mixer.handle_batch(pending)

    def views(self):
        return self.mixer.snapshot().voices

    # ---------------- Ciclo de vida ----------------

    def test_toggle_inicia_pausa_e_retoma(self):
        self.mixer.handle(toggle(volume=0.6))
        (voice,) = self.vlc.created
        self.assertEqual(voice.calls, ["start"])
        self.assertEqual(voice.gains, [(0.6, True)])  # Volume aplicado antes de tocar
        view = self.views()["t1"]
        self.assertEqual((view.state, view.backend, view.volume), (VoiceState.PLAYING, BackendHint.VLC, 0.6))

        self.mixer.handle(toggle())
        self.assertEqual(self.views()["t1"].state, VoiceState.PAUSED)
        self.mixer.handle(toggle())
        self.assertEqual(self.views()["t1"].state, VoiceState.PLAYING)
        self.assertEqual(voice.calls, ["start", "pause", "resume"])

    def test_stop_libera_a_voz_e_um_novo_toggle_cria_outra(self):
        self.mixer.handle(toggle())
        self.mixer.handle(Stop("t1"))
        self.assertTrue(self.vlc.created[0].stopped)
        self.assertEqual(dict(self.views()), {})

        self.mixer.handle(toggle())
        self.assertEqual(len(self.vlc.created), 2)

    def test_stop_all(self):
        self.mixer.handle(toggle("a"))
        self.mixer.handle(toggle("b", duration_s=3))
        self.mixer.handle(StopAll())
        self.assertFalse(self.mixer.has_voices)
        self.assertEqual(self.vlc.active + self.pygame.active, [])

    def test_comandos_para_voz_inexistente_sao_ignorados(self):
        for command in (Stop("x"), Seek("x", 3), SetVolume("x", 0.2), SetLoop("x", True)):
            self.mixer.handle(command)
        self.assertEqual(self.events, [])

    def test_duracao_do_snapshot_vem_da_faixa_se_o_backend_nao_souber(self):
        self.mixer.handle(toggle(duration_s=600))
        self.assertEqual(self.views()["t1"].duration_s, 600)

    # ---------------- Escolha do backend ----------------

    def test_curto_vai_para_o_pygame(self):
        self.mixer.handle(toggle(duration_s=4))
        self.assertEqual(self.views()["t1"].backend, BackendHint.PYGAME)
        self.assertFalse(self.views()["t1"].supports_seek)

    def test_canais_do_pygame_acabaram_usa_o_vlc(self):
        self.pygame.capacity = 1
        self.mixer.handle(toggle("a", duration_s=4))
        self.mixer.handle(toggle("b", duration_s=4))
        self.assertEqual(self.views()["a"].backend, BackendHint.PYGAME)
        self.assertEqual(self.views()["b"].backend, BackendHint.VLC)
        # Ninguém perdeu o canal
        self.assertFalse(self.pygame.created[0].stopped)

    def test_sem_vagas_em_lugar_nenhum_avisa_em_vez_de_cortar_outro_som(self):
        self.pygame.capacity = self.vlc.capacity = 0
        self.mixer.handle(toggle(duration_s=4))
        self.assertEqual(self.events, [VoiceFailed("t1", "Todos os canais de áudio estão ocupados.")])
        self.assertFalse(self.mixer.has_voices)

    def test_stream_sem_vlc_avisa(self):
        mixer = Mixer(BackendSelector(None, self.pygame), emit=self.events.append, post=self.inbox.append)
        mixer.handle(toggle(source=STREAM))
        self.assertIsInstance(self.events[0], VoiceFailed)
        self.assertIn("VLC", self.events[0].reason)

    def test_erro_ao_abrir_o_arquivo_avisa(self):
        self.vlc.create_error = OSError("arquivo corrompido")
        with self.assertLogs("application.audio.mixer", "ERROR"):
            self.mixer.handle(toggle())
        self.assertEqual(len(self.events), 1)
        self.assertIn("arquivo corrompido", self.events[0].reason)

    # ---------------- Sinais dos backends ----------------

    def test_fim_sem_loop_remove_e_avisa(self):
        self.mixer.handle(toggle())
        self.vlc.created[0].emit(BackendEvent.ENDED)
        self.deliver_backend_signals()
        self.assertEqual(self.events, [VoiceEnded("t1")])
        self.assertFalse(self.mixer.has_voices)

    def test_fim_com_loop_reinicia(self):
        self.mixer.handle(toggle(loop=True))
        voice = self.vlc.created[0]
        voice.emit(BackendEvent.ENDED)
        self.deliver_backend_signals()
        self.assertEqual(voice.calls, ["start", "restart"])
        self.assertTrue(self.mixer.has_voices)

    def test_loop_ligado_depois_tambem_reinicia(self):
        self.mixer.handle(toggle(loop=False))
        self.mixer.handle(SetLoop("t1", True))
        voice = self.vlc.created[0]
        self.assertTrue(voice.loop)
        voice.emit(BackendEvent.ENDED)
        self.deliver_backend_signals()
        self.assertIn("restart", voice.calls)

    def test_playing_reaplica_o_volume(self):
        self.mixer.handle(toggle(volume=0.3))
        voice = self.vlc.created[0]
        voice.emit(BackendEvent.PLAYING)
        self.deliver_backend_signals()
        self.assertEqual(voice.gains, [(0.3, True), (0.3, True)])

    def test_erro_do_backend_remove_e_avisa(self):
        self.mixer.handle(toggle())
        self.vlc.created[0].emit(BackendEvent.ERROR)
        self.deliver_backend_signals()
        self.assertIsInstance(self.events[0], VoiceFailed)
        self.assertFalse(self.mixer.has_voices)

    def test_sinal_de_um_handle_antigo_e_ignorado(self):
        # Para e toca de novo: o fim atrasado da voz antiga não pode derrubar a nova.
        self.mixer.handle(toggle())
        old = self.vlc.created[0]
        self.mixer.handle(Stop("t1"))
        self.mixer.handle(toggle())
        old.emit(BackendEvent.ENDED)
        self.deliver_backend_signals()
        self.assertEqual(self.events, [])
        self.assertTrue(self.mixer.has_voices)

    def test_tick_consulta_as_vozes(self):
        self.mixer.handle(toggle())
        self.mixer.tick()
        self.assertEqual(self.vlc.created[0].calls, ["start", "poll"])

    # ---------------- Volume, seek e lotes ----------------

    def test_volume_e_limitado_a_0_1(self):
        self.mixer.handle(toggle())
        self.mixer.handle(SetVolume("t1", 1.7))
        self.assertEqual(self.vlc.created[0].gains[-1], (1.0, False))
        self.assertEqual(self.views()["t1"].volume, 1.0)

    def test_lote_aplica_so_o_ultimo_volume_de_cada_voz(self):
        self.mixer.handle(toggle("a"))
        self.mixer.handle(toggle("b"))
        self.mixer.handle_batch([
            SetVolume("a", 0.1), SetVolume("b", 0.5), SetVolume("a", 0.2), SetVolume("a", 0.3)
        ])
        a, b = self.vlc.created
        self.assertEqual(a.gains[1:], [(0.3, False)])
        self.assertEqual(b.gains[1:], [(0.5, False)])

    def test_seek_so_onde_o_backend_permite(self):
        self.mixer.handle(toggle("longa", duration_s=600))
        self.mixer.handle(toggle("curta", duration_s=3))
        self.mixer.handle(Seek("longa", -5))
        self.mixer.handle(Seek("curta", 1))
        self.assertEqual(self.vlc.created[0].seeks, [0.0])
        self.assertEqual(self.pygame.created[0].seeks, [])

    # ---------------- Robustez ----------------

    def test_falha_numa_voz_nao_derruba_o_mixer(self):
        self.mixer.handle(toggle("a"))
        self.mixer.handle(toggle("b"))
        self.vlc.created[0].fail_on = "pause"

        with self.assertLogs("application.audio.mixer", "ERROR"):
            self.mixer.handle(toggle("a"))  # pause explode

        self.assertIsInstance(self.events[0], VoiceFailed)
        self.assertEqual(set(self.views()), {"b"})
        self.mixer.handle(toggle("b"))  # o resto continua funcionando
        self.assertEqual(self.views()["b"].state, VoiceState.PAUSED)

    def test_snapshot_tem_sequencia_crescente(self):
        first = self.mixer.snapshot().seq
        self.assertGreater(self.mixer.snapshot().seq, first)

    def test_mensagem_desconhecida_e_ignorada(self):
        with self.assertLogs("application.audio.mixer", "WARNING"):
            self.mixer.handle(object())
        self.mixer.handle(BackendSignal("fantasma", 99, BackendEvent.ENDED))
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
