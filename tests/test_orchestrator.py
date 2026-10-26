import threading
import time
import unittest

from application.audio.messages import Stop, TogglePlay, VoiceEnded, VoiceFailed
from application.audio.orchestrator import AudioOrchestrator
from application.audio.selector import BackendSelector
from audio_fakes import FakeBackend, make_track
from domain.models import BackendHint, PlayableSource
from domain.ports import BackendEvent

LOCAL = PlayableSource("/sons/x.ogg", is_remote=False)
STREAM = PlayableSource("https://rr1.googlevideo.com/x", is_remote=True)


def wait_until(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return False


class AudioOrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.vlc = FakeBackend(BackendHint.VLC)
        self.pygame = FakeBackend(BackendHint.PYGAME)
        self.selector = BackendSelector(self.vlc, self.pygame)
        self.orchestrator = AudioOrchestrator(self.selector, tick_s=0.02)

    def tearDown(self):
        self.orchestrator.shutdown()

    def play(self, voice_id="t1", source=LOCAL):
        self.orchestrator.send(TogglePlay(voice_id, make_track(voice_id, duration_s=600), source, 0.8, False))

    def test_comando_vira_snapshot_e_shutdown_libera_tudo(self):
        self.orchestrator.start()
        self.play()
        self.assertTrue(wait_until(lambda: "t1" in self.orchestrator.snapshot().voices))
        self.assertTrue(self.vlc.opened and self.pygame.opened)

        self.orchestrator.shutdown()

        self.assertTrue(self.vlc.created[0].stopped)
        self.assertTrue(self.vlc.closed and self.pygame.closed)
        self.assertEqual(dict(self.orchestrator.snapshot().voices), {})

    def test_backends_abrem_e_tocam_so_na_thread_de_audio(self):
        threads = []
        original = self.vlc.create_voice

        def spy(*args):
            threads.append(threading.current_thread().name)
            return original(*args)

        self.vlc.create_voice = spy
        self.orchestrator.start()
        self.play()
        self.assertTrue(wait_until(lambda: threads))
        self.assertEqual(threads, ["audio-ctl"])

    def test_evento_vindo_de_outra_thread_chega_como_evento_da_ui(self):
        self.orchestrator.start()
        self.play()
        self.assertTrue(wait_until(lambda: self.vlc.created))

        # Callbacks do libVLC chegam de threads nativas
        threading.Thread(target=self.vlc.created[0].emit, args=(BackendEvent.ENDED,)).start()

        self.assertTrue(wait_until(lambda: "t1" not in self.orchestrator.snapshot().voices))
        self.assertEqual(self.orchestrator.poll_events(), [VoiceEnded("t1")])

    def test_tick_acontece_enquanto_ha_vozes(self):
        self.orchestrator.start()
        self.play()
        self.assertTrue(wait_until(lambda: self.vlc.created and self.vlc.created[0].calls.count("poll") >= 3))
        seq = self.orchestrator.snapshot().seq
        self.assertTrue(wait_until(lambda: self.orchestrator.snapshot().seq > seq))

    def test_parado_nao_publica_snapshots(self):
        self.orchestrator.start()
        self.play()
        self.assertTrue(wait_until(lambda: self.orchestrator.snapshot().voices))
        self.orchestrator.send(Stop("t1"))
        self.assertTrue(wait_until(lambda: not self.orchestrator.snapshot().voices))
        seq = self.orchestrator.snapshot().seq
        time.sleep(0.1)  # Cinco ticks, se a thread estivesse acordando à toa
        self.assertEqual(self.orchestrator.snapshot().seq, seq)

    def test_backend_que_nao_abre_e_desativado(self):
        self.vlc.open_error = OSError("sem libVLC")
        with self.assertLogs("application.audio.orchestrator", "WARNING"):
            self.orchestrator.start()
            self.play(source=STREAM)
            self.assertTrue(wait_until(lambda: self.selector.vlc is None))
        events = []
        self.assertTrue(wait_until(lambda: events.extend(self.orchestrator.poll_events()) or events))
        self.assertIn("VLC", events[0].reason)

    def test_falha_de_voz_aparece_em_poll_events(self):
        self.vlc.create_error = OSError("arquivo corrompido")
        events = []
        with self.assertLogs("application.audio.mixer", "ERROR"):
            self.orchestrator.start()
            self.play()
            self.assertTrue(wait_until(lambda: events.extend(self.orchestrator.poll_events()) or events))
        self.assertIsInstance(events[0], VoiceFailed)

    def test_shutdown_sem_start_nao_trava(self):
        AudioOrchestrator(self.selector).shutdown()


if __name__ == "__main__":
    unittest.main()
