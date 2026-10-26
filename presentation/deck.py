"""
Deck: o que está tocando agora, com os controles finos (tempo, volume, loop).
Os cards da biblioteca ficam limpos; tudo o que muda 10x por segundo vive aqui.
Poucas linhas (só as vozes ativas), então widgets comuns do customtkinter bastam.
"""
import tkinter as tk
from typing import Callable

import customtkinter as ctk

from application.audio.messages import (
    AudioCommand, MixerSnapshot, Seek, SetLoop, SetVolume, Stop, StopAll, VoiceState, VoiceView,
)
from domain.models import Track
from presentation import theme
from presentation.grid import format_duration

ROW_HEIGHT = 40
MAX_VISIBLE_ROWS = 4


class _DeckRow(ctk.CTkFrame):
    def __init__(self, master, fonts: theme.Fonts, track: Track, send: Callable[[AudioCommand], None],
                 on_toggle: Callable[[Track], None]):
        super().__init__(master, fg_color=theme.CARD, corner_radius=6, height=ROW_HEIGHT - 4)
        self._track = track
        self._send = send
        self._view: VoiceView | None = None
        self._seeking = False
        self.grid_columnconfigure(2, weight=1)

        self.play = ctk.CTkButton(self, text="❚❚", width=34, height=26, corner_radius=6,
                                  font=fonts.ctk(fonts.button), command=lambda: on_toggle(track))
        self.play.grid(row=0, column=0, padx=(6, 6), pady=5)
        self.title = ctk.CTkLabel(self, text=_short(track.title, 34), anchor="w", width=190,
                                  text_color=theme.TEXT, font=fonts.ctk(fonts.body_bold))
        self.title.grid(row=0, column=1, sticky="w")

        self.seek = ctk.CTkSlider(self, from_=0, to=1, height=14, progress_color=theme.ACCENT,
                                  button_color="#93c5fd", button_hover_color="#bfdbfe",
                                  command=self._on_seek_drag)
        self.seek.grid(row=0, column=2, sticky="ew", padx=8)
        self.seek.bind("<ButtonPress-1>", lambda e: setattr(self, "_seeking", True))
        self.seek.bind("<ButtonRelease-1>", self._on_seek_release)
        self.time = ctk.CTkLabel(self, text="0:00", width=90, anchor="e", text_color=theme.TEXT_MUTED,
                                 font=fonts.ctk(fonts.small))
        self.time.grid(row=0, column=3, padx=(0, 8))

        ctk.CTkLabel(self, text="🔊", width=20, text_color=theme.TEXT_MUTED,
                     font=(fonts.family, 14)).grid(row=0, column=4)
        self.volume = ctk.CTkSlider(self, from_=0, to=1, width=80, height=12, progress_color=theme.ACCENT,
                                    button_color="#93c5fd", command=self._on_volume)
        self.volume.grid(row=0, column=5, padx=(2, 4))
        self.volume_label = ctk.CTkLabel(self, text="", width=34, text_color=theme.TEXT_MUTED,
                                         font=fonts.ctk(fonts.small))
        self.volume_label.grid(row=0, column=6)

        self.loop = ctk.CTkSwitch(self, text="Loop", width=60, switch_width=30, switch_height=14,
                                  progress_color=theme.PLAYING, text_color=theme.TEXT_MUTED,
                                  font=fonts.ctk(fonts.small), command=self._on_loop)
        self.loop.grid(row=0, column=7, padx=6)
        ctk.CTkButton(self, text="■", width=30, height=26, corner_radius=6, fg_color=theme.CHIP,
                      hover_color=theme.DANGER, font=fonts.ctk(fonts.button),
                      command=lambda: send(Stop(track.id))).grid(row=0, column=8, padx=(0, 6))

    def show(self, view: VoiceView) -> None:
        previous, self._view = self._view, view
        if previous is None or previous.state != view.state:
            playing = view.state == VoiceState.PLAYING
            self.play.configure(text="❚❚" if playing else "▶",
                                fg_color=theme.PLAYING if playing else theme.PAUSED,
                                hover_color=theme.PLAYING if playing else theme.PAUSED)
        if previous is None or previous.supports_seek != view.supports_seek or previous.duration_s != view.duration_s:
            self.seek.configure(to=max(1.0, view.duration_s or 1.0),
                                state="normal" if view.supports_seek else "disabled")
        if previous is None:
            self.volume.set(view.volume)
            self.volume_label.configure(text=f"{round(view.volume * 100)}%")
            if view.loop:
                self.loop.select()
        if not self._seeking:
            self.seek.set(view.position_s)
            self._show_time(view.position_s)

    def _show_time(self, position: float) -> None:
        total = format_duration(self._view.duration_s) if self._view else ""
        self.time.configure(text=f"{format_duration(position) or '0:00'} / {total or '?'}")

    def _on_seek_drag(self, value: float) -> None:
        self._show_time(float(value))

    def _on_seek_release(self, event) -> None:
        self._seeking = False
        if self._view and self._view.supports_seek:
            self._send(Seek(self._track.id, float(self.seek.get())))

    def _on_volume(self, value: float) -> None:
        self.volume_label.configure(text=f"{round(float(value) * 100)}%")
        self._send(SetVolume(self._track.id, float(value)))

    def _on_loop(self) -> None:
        self._send(SetLoop(self._track.id, bool(self.loop.get())))


class Deck(ctk.CTkFrame):
    def __init__(self, master, fonts: theme.Fonts, send: Callable[[AudioCommand], None],
                 track_of: Callable[[str], Track | None], on_toggle: Callable[[Track], None]):
        super().__init__(master, fg_color=theme.SURFACE, corner_radius=8)
        self._fonts = fonts
        self._send = send
        self._track_of = track_of
        self._on_toggle = on_toggle
        self._rows: dict[str, _DeckRow] = {}
        self.grid_columnconfigure(0, weight=1)

        self.header = ctk.CTkLabel(self, text="TOCANDO AGORA", anchor="w", text_color=theme.TEXT_MUTED,
                                   font=fonts.ctk(fonts.small_bold))
        self.header.grid(row=0, column=0, sticky="w", padx=12, pady=(6, 2))
        ctk.CTkButton(self, text="■ Parar tudo", width=100, height=24, corner_radius=6,
                      fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
                      font=fonts.ctk(fonts.small_bold), command=lambda: send(StopAll())
                      ).grid(row=0, column=1, padx=10, pady=(6, 2))

        self.empty = ctk.CTkLabel(self, text="Nada tocando. Clique em ▶ Tocar num card.",
                                  text_color=theme.TEXT_DISABLED, font=fonts.ctk(fonts.body))
        self.empty.grid(row=1, column=0, columnspan=2, pady=(0, 8))
        # Rolagem própria: o CTkScrollableFrame não respeita alturas pequenas
        self.viewport = tk.Canvas(self, bg=theme.SURFACE, highlightthickness=0, bd=0, height=ROW_HEIGHT)
        self.scrollbar = ctk.CTkScrollbar(self, command=self.viewport.yview, button_color=theme.CHIP,
                                          button_hover_color=theme.CHIP_HOVER)
        self.viewport.configure(yscrollcommand=self.scrollbar.set)
        self.rows = ctk.CTkFrame(self.viewport, fg_color="transparent")
        self.rows.grid_columnconfigure(0, weight=1)
        self._window = self.viewport.create_window(0, 0, window=self.rows, anchor="nw")
        self.viewport.bind("<Configure>", lambda e: self.viewport.itemconfigure(self._window, width=e.width))
        self.rows.bind("<Configure>", lambda e: self.viewport.configure(scrollregion=self.viewport.bbox("all")))
        for sequence, units in (("<Button-4>", -1), ("<Button-5>", 1)):
            self.viewport.bind(sequence, lambda e, u=units: self.viewport.yview_scroll(u, "units"))

    def show(self, snapshot: MixerSnapshot) -> None:
        voices = snapshot.voices
        for voice_id in [v for v in self._rows if v not in voices]:
            self._rows.pop(voice_id).destroy()
        for voice_id, view in voices.items():
            row = self._rows.get(voice_id)
            if row is None:
                track = self._track_of(voice_id)
                if track is None:
                    continue
                row = _DeckRow(self.rows, self._fonts, track, self._send, self._on_toggle)
                self._rows[voice_id] = row
            row.show(view)
        for index, row in enumerate(self._rows.values()):
            row.grid(row=index, column=0, sticky="ew", pady=2)

        count = len(self._rows)
        self.header.configure(text=f"TOCANDO AGORA · {count}" if count else "TOCANDO AGORA")
        if count:
            self.empty.grid_remove()
            self.viewport.configure(height=min(count, MAX_VISIBLE_ROWS) * ROW_HEIGHT)
            self.viewport.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(6, 0), pady=(0, 6))
            if count > MAX_VISIBLE_ROWS:
                self.scrollbar.grid(row=1, column=2, sticky="ns", pady=(0, 6))
            else:
                self.scrollbar.grid_remove()
                self.viewport.yview_moveto(0)
        else:
            self.viewport.grid_remove()
            self.scrollbar.grid_remove()
            self.empty.grid()


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
