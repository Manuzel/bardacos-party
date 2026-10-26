"""
Grade de cards virtual (doc: §2.4): um único tk.Canvas que desenha só as linhas visíveis.
Rolar, filtrar ou redimensionar apaga e redesenha algumas dezenas de itens; nenhum widget
é criado ou destruído, então a grade aguenta centenas de faixas.
"""
import math
import tkinter as tk
from typing import Callable

import customtkinter as ctk

from domain.models import SourceKind, Track, TrackKind
from presentation import theme
from presentation.canvas_draw import layout_chips, round_rect, wrap_lines
from presentation.cover_images import CoverImages

# Estados de card desenhados no botão principal
PLAYING, PAUSED, RESOLVING, ERROR = "playing", "paused", "resolving", "error"

MIN_CARD_WIDTH = 200
GAP = 10
PAD = 4
INNER = 9
TITLE_LINE = 17
CHIP_H = 19
CHIP_GAP = 4
CHIP_ROWS = 3
ACTION_H = 26

_PLAY_BUTTON = {
    None: ("▶ Tocar", theme.ACCENT),
    PLAYING: ("❚❚ Pausar", theme.PLAYING),
    PAUSED: ("▶ Retomar", theme.PAUSED),
    RESOLVING: ("⏳ Conectando", theme.RESOLVING),
    ERROR: ("⚠ Erro", theme.DANGER),
}
_BORDER = {PLAYING: (theme.PLAYING, 2), PAUSED: (theme.PAUSED, 2)}
_SOURCE_BADGE = {SourceKind.STREAM: "NUVEM", SourceKind.CACHE: "CACHE"}
_KIND_GLYPH = {TrackKind.SFX: "✦", TrackKind.AMBIENCE: "≋", TrackKind.MUSIC: "♫"}


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


class LibraryGrid(ctk.CTkFrame):
    def __init__(
        self,
        master,
        fonts: theme.Fonts,
        images: CoverImages,
        card_tags: Callable[[Track], list[tuple[str, bool]]],
        on_play: Callable[[Track], None],
        on_menu: Callable[[Track, int, int], None],
        on_save: Callable[[Track], None],
    ):
        super().__init__(master, fg_color=theme.PANEL, corner_radius=0)
        self._fonts = fonts
        self._images = images
        self._card_tags = card_tags
        self._on_play = on_play
        self._on_menu = on_menu
        self._on_save = on_save

        self._tracks: list[Track] = []
        self._by_id: dict[str, Track] = {}
        self._states: dict[str, str] = {}
        self._empty_text = ""
        self._cols = 1
        self._card_w = MIN_CARD_WIDTH
        self._card_h = 300
        self._image_h = 112
        self._drawn_rows: tuple[int, int] | None = None
        self._redraw_pending = False
        self._hover: str | None = None
        self._photos: list = []  # Referências vivas das imagens desenhadas

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=theme.PANEL, highlightthickness=0, bd=0, yscrollincrement=24)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ctk.CTkScrollbar(self, command=self.canvas.yview, button_color=theme.CHIP,
                                          button_hover_color=theme.CHIP_HOVER)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self._on_scrolled)

        self.canvas.bind("<Configure>", lambda e: self._relayout())
        self.canvas.bind("<Button-1>", self._on_left_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        for sequence, units in (("<Button-4>", -3), ("<Button-5>", 3)):
            self.canvas.bind(sequence, lambda e, u=units: self._scroll(u))
        self.canvas.bind("<MouseWheel>", lambda e: self._scroll(-3 if e.delta > 0 else 3))

    # ---------------- API ----------------

    def set_tracks(self, tracks: list[Track], empty_text: str = "", reset_scroll: bool = False) -> None:
        self._tracks = list(tracks)
        self._by_id = {t.id: t for t in tracks}
        self._empty_text = empty_text
        if reset_scroll:
            self.canvas.yview_moveto(0)
        self._relayout()

    def set_states(self, states: dict[str, str]) -> None:
        if states != self._states:
            self._states = dict(states)
            self._request_redraw(force=True)

    def cover_ready(self, track_id: str) -> None:
        self._images.invalidate(track_id)
        if track_id in self._by_id:
            self._request_redraw(force=True)

    def refresh(self) -> None:
        self._request_redraw(force=True)

    # ---------------- Layout ----------------

    def _relayout(self) -> None:
        width = max(1, self.canvas.winfo_width())
        usable = width - 2 * PAD
        self._cols = max(1, (usable + GAP) // (MIN_CARD_WIDTH + GAP))
        # Antes de a janela abrir a largura é 1; um mínimo evita imagens de tamanho negativo
        self._card_w = max(120, (usable - GAP * (self._cols - 1)) // self._cols)
        self._image_h = round(self._card_w * 9 / 16)
        chips_h = CHIP_ROWS * CHIP_H + (CHIP_ROWS - 1) * CHIP_GAP
        self._card_h = self._image_h + INNER + 2 * TITLE_LINE + 6 + chips_h + 12 + ACTION_H + INNER
        rows = math.ceil(len(self._tracks) / self._cols)
        total = PAD + rows * (self._card_h + GAP)
        self.canvas.configure(scrollregion=(0, 0, width, max(total, self.canvas.winfo_height())))
        self._request_redraw(force=True)

    def _visible_rows(self) -> tuple[int, int]:
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        row_h = self._card_h + GAP
        first = max(0, int((top - PAD) // row_h) - 1)
        last = int((bottom - PAD) // row_h) + 1
        return first, last

    def _request_redraw(self, force: bool = False) -> None:
        if force:
            self._drawn_rows = None
        if not self._redraw_pending:
            self._redraw_pending = True
            self.after_idle(self._redraw)

    def _redraw(self) -> None:
        self._redraw_pending = False
        rows = self._visible_rows()
        if rows == self._drawn_rows:
            return
        self._drawn_rows = rows
        self.canvas.delete("all")
        self._photos.clear()

        if not self._tracks:
            self.canvas.create_text(self.canvas.winfo_width() / 2, 80, text=self._empty_text,
                                    fill=theme.TEXT_MUTED, font=self._fonts.body, justify="center")
            return

        first, last = rows
        for index in range(first * self._cols, min(len(self._tracks), (last + 1) * self._cols)):
            row, col = divmod(index, self._cols)
            x = PAD + col * (self._card_w + GAP)
            y = PAD + row * (self._card_h + GAP)
            self._draw_card(self._tracks[index], x, y)

    def _draw_card(self, track: Track, x: int, y: int) -> None:
        c, f = self.canvas, self._fonts
        w, h = self._card_w, self._card_h
        state = self._states.get(track.id)
        card_tag = f"card:{track.id}"
        tags = ("card", card_tag)

        border, border_w = _BORDER.get(state, (theme.CARD_BORDER_HOVER if self._hover == track.id
                                              else theme.CARD_BORDER, 1))
        round_rect(c, x, y, x + w, y + h, 8, fill=theme.CARD, outline=border, width=border_w,
                   tags=(*tags, f"border:{track.id}"))

        # Capa
        photo, real = self._images.get(track, w - 2, self._image_h)
        self._photos.append(photo)
        c.create_image(x + 1, y + 1, image=photo, anchor="nw", tags=tags)
        if not real:
            c.create_text(x + w / 2, y + 1 + self._image_h / 2, text=_KIND_GLYPH[track.kind],
                          fill="#c7cbe0", font=f.glyph, tags=tags)
        badge = _SOURCE_BADGE.get(track.source_kind)
        if badge:
            self._badge(x + 7, y + 7, badge, tags, anchor="nw")
        duration = format_duration(track.duration_s)
        if duration:
            self._badge(x + w - 7, y + self._image_h - 6, duration, tags, anchor="se")

        # Título (até duas linhas)
        text_x = x + INNER
        cursor_y = y + 1 + self._image_h + INNER
        for line in wrap_lines(track.title, f.card_title.measure, w - 2 * INNER, 2):
            c.create_text(text_x, cursor_y, text=line, anchor="nw", fill=theme.TEXT, font=f.card_title, tags=tags)
            cursor_y += TITLE_LINE
        cursor_y = y + 1 + self._image_h + INNER + 2 * TITLE_LINE + 6

        # Tags (até três linhas, o resto vira "+N")
        card_tags = self._card_tags(track)
        if card_tags:
            labels = [name for name, _ in card_tags]
            layout = layout_chips(labels, f.chip.measure, w - 2 * INNER, CHIP_H, pad_x=6,
                                  gap=CHIP_GAP, max_rows=CHIP_ROWS)
            for box in layout.boxes:
                name, highlighted = card_tags[box.index]
                cx, cy = text_x + box.x, cursor_y + box.y
                round_rect(c, cx, cy, cx + box.width, cy + CHIP_H, 5,
                           fill=theme.INCLUDE if highlighted else theme.CHIP, outline="", tags=tags)
                c.create_text(cx + 6, cy + CHIP_H / 2, text=name, anchor="w",
                              fill="#ffffff" if highlighted else theme.TEXT, font=f.chip, tags=tags)
            if layout.hidden:
                c.create_text(text_x + layout.more_x, cursor_y + layout.more_y + CHIP_H / 2,
                              text=f"+{layout.hidden}", anchor="w", fill=theme.TEXT_MUTED,
                              font=f.chip, tags=tags)
        else:
            c.create_text(text_x, cursor_y + 2, text="sem tags — use ⋯ para marcar", anchor="nw",
                          fill=theme.TEXT_DISABLED, font=f.small, tags=tags)

        # Ações
        bottom = y + h - INNER
        label, color = _PLAY_BUTTON[state]
        play_w = f.button.measure("▶ Retomar") + 22
        round_rect(c, text_x, bottom - ACTION_H, text_x + play_w, bottom, 6, fill=color, outline="",
                   tags=(*tags, f"act:play:{track.id}"))
        c.create_text(text_x + play_w / 2, bottom - ACTION_H / 2, text=label, fill="#ffffff",
                      font=f.button, tags=(*tags, f"act:play:{track.id}"))

        right = x + w - INNER
        self._icon_button(right - ACTION_H, bottom, "⋯", f"act:menu:{track.id}", tags)
        if track.source_kind != SourceKind.LOCAL:
            self._icon_button(right - 2 * ACTION_H - 6, bottom, "⤓", f"act:save:{track.id}", tags)

    def _badge(self, x: float, y: float, text: str, tags, anchor: str) -> None:
        font = self._fonts.badge
        width, height = font.measure(text) + 10, 16
        x1 = x if anchor.endswith("w") else x - width
        y1 = y if anchor.startswith("n") else y - height
        round_rect(self.canvas, x1, y1, x1 + width, y1 + height, 4, fill=theme.BADGE, outline="", tags=tags)
        self.canvas.create_text(x1 + width / 2, y1 + height / 2, text=text, fill=theme.TEXT,
                                font=font, tags=tags)

    def _icon_button(self, x: float, bottom: float, glyph: str, action: str, tags) -> None:
        item_tags = (*tags, action)
        round_rect(self.canvas, x, bottom - ACTION_H, x + ACTION_H, bottom, 6, fill=theme.CHIP,
                   outline="", tags=item_tags)
        self.canvas.create_text(x + ACTION_H / 2, bottom - ACTION_H / 2, text=glyph, fill=theme.TEXT,
                                font=self._fonts.button, tags=item_tags)

    # ---------------- Eventos ----------------

    def _on_scrolled(self, first: str, last: str) -> None:
        self.scrollbar.set(first, last)
        self._request_redraw()

    def _scroll(self, units: int) -> None:
        self.canvas.yview_scroll(units, "units")

    def _hit(self, event) -> tuple[Track | None, str | None]:
        """(faixa sob o mouse, ação sob o mouse)."""
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        track, action = None, None
        for item in reversed(self.canvas.find_overlapping(x, y, x, y)):
            for tag in self.canvas.gettags(item):
                if tag.startswith("act:") and action is None:
                    _, action, track_id = tag.split(":", 2)
                    track = self._by_id.get(track_id)
                elif tag.startswith("card:") and track is None:
                    track = self._by_id.get(tag[5:])
        return track, action

    def _on_left_click(self, event) -> None:
        track, action = self._hit(event)
        if track is None:
            return
        if action == "play":
            self._on_play(track)
        elif action == "menu":
            self._on_menu(track, event.x_root, event.y_root)
        elif action == "save":
            self._on_save(track)

    def _on_right_click(self, event) -> None:
        track, _ = self._hit(event)
        if track is not None:
            self._on_menu(track, event.x_root, event.y_root)

    def _on_motion(self, event) -> None:
        track, action = self._hit(event)
        self.canvas.configure(cursor="hand2" if action else "")
        self._set_hover(track.id if track else None)

    def _set_hover(self, track_id: str | None) -> None:
        if track_id == self._hover:
            return
        previous, self._hover = self._hover, track_id
        for tid in (previous, track_id):
            if tid and self._states.get(tid) not in _BORDER:
                color = theme.CARD_BORDER_HOVER if tid == track_id else theme.CARD_BORDER
                self.canvas.itemconfigure(f"border:{tid}", outline=color)
