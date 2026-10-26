"""
Ponte entre as threads de trabalho e o Tkinter (doc: §4.6).
O Tkinter só pode ser tocado pela thread da UI. Em vez de cada thread chamar `after()`
(o que não é seguro) e de cada card ter seu próprio timer, um único laço `after(33)`:
1. executa os callbacks postados por outras threads (`post`);
2. drena os eventos do orquestrador de áudio;
3. entrega o snapshot mais recente, só se ele mudou.
"""
import logging
import queue
from typing import Any, Callable

from application.audio.messages import AudioEvent, MixerSnapshot
from application.audio.orchestrator import AudioOrchestrator

log = logging.getLogger(__name__)


class UiPump:
    def __init__(
        self,
        root,
        orchestrator: AudioOrchestrator,
        on_snapshot: Callable[[MixerSnapshot], None],
        on_event: Callable[[AudioEvent], None],
        interval_ms: int = 33,
        max_per_frame: int = 200
    ):
        self._root = root
        self._orchestrator = orchestrator
        self._on_snapshot = on_snapshot
        self._on_event = on_event
        self._interval_ms = interval_ms
        self._max_per_frame = max_per_frame
        self._posted: queue.SimpleQueue[tuple[Callable[..., Any], tuple]] = queue.SimpleQueue()
        self._last_seq = -1
        self._after_id: str | None = None

    def post(self, fn: Callable[..., Any], *args: Any) -> None:
        """Agenda `fn(*args)` na thread da UI. Pode ser chamado de qualquer thread."""
        self._posted.put((fn, args))

    def start(self) -> None:
        if self._after_id is None:
            self._after_id = self._root.after(self._interval_ms, self._frame)

    def stop(self) -> None:
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
            self._after_id = None

    def _frame(self) -> None:
        # Agenda o próximo quadro antes de tudo: uma exceção aqui não pode parar o laço.
        self._after_id = self._root.after(self._interval_ms, self._frame)

        for _ in range(self._max_per_frame):
            try:
                fn, args = self._posted.get_nowait()
            except queue.Empty:
                break
            self._safely(fn, *args)

        for event in self._orchestrator.poll_events(self._max_per_frame):
            self._safely(self._on_event, event)

        snapshot = self._orchestrator.snapshot()
        if snapshot.seq != self._last_seq:
            self._last_seq = snapshot.seq
            self._safely(self._on_snapshot, snapshot)

    @staticmethod
    def _safely(fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("Falha num callback da UI")
