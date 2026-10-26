"""
Actor de áudio (doc: §1.3 e §3.2): uma thread "audio-ctl" é a única que toca nos players.
- `send()` enfileira comandos de qualquer thread.
- `snapshot()` devolve o estado mais recente (atribuição de referência, sem lock).
- `poll_events()` drena os eventos discretos.
A thread fica bloqueada na fila quando nada toca (sem uso de CPU ocioso) e acorda a cada
`tick_s` enquanto houver vozes, para detectar fins de faixa e atualizar posições.
"""
import logging
import queue
import threading
import time

from application.audio.messages import AudioCommand, AudioEvent, MixerSnapshot
from application.audio.mixer import Mixer
from application.audio.selector import BackendSelector

log = logging.getLogger(__name__)

_SHUTDOWN = object()


class AudioOrchestrator:
    def __init__(self, selector: BackendSelector, tick_s: float = 0.1):
        self._selector = selector
        self._tick_s = tick_s
        self._inbox: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._events: queue.SimpleQueue[AudioEvent] = queue.SimpleQueue()
        self._snapshot = MixerSnapshot.empty()
        self._mixer = Mixer(selector, emit=self._events.put, post=self._inbox.put)
        self._thread = threading.Thread(target=self._run, name="audio-ctl", daemon=True)

    # ---------------- API pública (qualquer thread) ----------------

    def start(self) -> None:
        self._thread.start()

    def send(self, command: AudioCommand) -> None:
        self._inbox.put(command)

    def snapshot(self) -> MixerSnapshot:
        return self._snapshot

    def poll_events(self, max_items: int = 200) -> list[AudioEvent]:
        events = []
        while len(events) < max_items:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def shutdown(self, timeout: float = 3.0) -> None:
        """Para todas as vozes, fecha os backends e espera a thread terminar."""
        if not self._thread.is_alive():
            return
        self._inbox.put(_SHUTDOWN)
        self._thread.join(timeout)
        if self._thread.is_alive():
            log.warning("A thread de áudio não terminou em %.1f s", timeout)

    # ---------------- Thread audio-ctl ----------------

    def _run(self) -> None:
        self._open_backends()
        next_tick = time.monotonic()
        running = True

        while running:
            timeout = max(0.0, next_tick - time.monotonic()) if self._mixer.has_voices else None
            batch = self._collect(timeout)

            if _SHUTDOWN in batch:
                running = False
                batch = batch[:batch.index(_SHUTDOWN)]
            self._mixer.handle_batch(batch)

            now = time.monotonic()
            if now >= next_tick:
                self._mixer.tick()
                next_tick = now + self._tick_s
            self._snapshot = self._mixer.snapshot()

        self._mixer.stop_all()
        self._snapshot = self._mixer.snapshot()
        self._close_backends()

    def _collect(self, timeout: float | None) -> list[object]:
        """Espera a primeira mensagem (ou o próximo tick) e drena o que mais tiver chegado."""
        try:
            batch = [self._inbox.get(timeout=timeout)]
        except queue.Empty:
            return []
        while True:
            try:
                batch.append(self._inbox.get_nowait())
            except queue.Empty:
                return batch

    def _open_backends(self) -> None:
        for backend in self._selector.backends:
            try:
                backend.open()
            except Exception as exc:
                log.warning("Motor de áudio %s indisponível: %s", backend.name, exc)
                self._selector.disable(backend)

    def _close_backends(self) -> None:
        for backend in self._selector.backends:
            try:
                backend.close()
            except Exception:
                log.exception("Falha ao fechar o motor de áudio %s", backend.name)
