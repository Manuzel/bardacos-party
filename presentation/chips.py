"""
Chips de filtro desenhados num canvas que quebra linhas sozinho (o Tk não tem flow layout).
Usado na lateral (com contagem) e na barra de filtros ativos (com ✕).
"""
import tkinter as tk
from typing import Callable

from presentation import theme
from presentation.canvas_draw import layout_chips, round_rect
from presentation.library_vm import ChipModel, ChipState, FacetKey

CHIP_HEIGHT = 22


class ChipFlow(tk.Canvas):
    """
    `on_click(key, right_button)`: na lateral, esquerdo inclui e direito exclui;
    com `removable=True` (filtros ativos) qualquer clique remove.
    """

    def __init__(
        self,
        master,
        fonts: theme.Fonts,
        on_click: Callable[[FacetKey, bool], None],
        removable: bool = False,
        bg: str = theme.PANEL,
    ):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, height=1)
        self._fonts = fonts
        self._on_click = on_click
        self._removable = removable
        self._chips: list[ChipModel] = []
        self._width = 0
        self._hover: int | None = None
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", lambda e: self._click(e, right=False))
        self.bind("<Button-3>", lambda e: self._click(e, right=True))
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))

    def set_chips(self, chips: list[ChipModel]) -> None:
        if chips != self._chips:
            self._chips = list(chips)
            self._hover = None
            self._draw()

    # ---------------- Desenho ----------------

    def _label(self, chip: ChipModel) -> str:
        return f"{chip.label}  ✕" if self._removable else chip.label

    def _measure(self, chip: ChipModel) -> int:
        width = self._fonts.chip.measure(self._label(chip))
        if chip.count is not None and not self._removable:
            width += 4 + self._fonts.chip_count.measure(str(chip.count))
        return width

    def _draw(self) -> None:
        self.delete("all")
        if self._width <= 1 or not self._chips:
            self.configure(height=1 if not self._chips else CHIP_HEIGHT)
            return
        widths = [self._measure(c) for c in self._chips]
        layout = layout_chips([str(i) for i in range(len(self._chips))], lambda s: widths[int(s)],
                              max_width=self._width - 2, chip_height=CHIP_HEIGHT)
        for box in layout.boxes:
            self._draw_chip(box.index, self._chips[box.index], box.x + 1, box.y + 1, box.width)
        self.configure(height=int(layout.height) + 2)

    def _draw_chip(self, index: int, chip: ChipModel, x: float, y: float, width: float) -> None:
        hover = index == self._hover and not chip.disabled
        if chip.state == ChipState.INCLUDE:
            fill, fg = (theme.INCLUDE_HOVER if hover else theme.INCLUDE), "#ffffff"
        elif chip.state == ChipState.EXCLUDE:
            fill, fg = (theme.EXCLUDE_HOVER if hover else theme.EXCLUDE), "#ffffff"
        elif chip.disabled:
            fill, fg = theme.CHIP_DISABLED, theme.TEXT_DISABLED
        else:
            fill, fg = (theme.CHIP_HOVER if hover else theme.CHIP), theme.TEXT

        tag = f"chip:{index}"
        round_rect(self, x, y, x + width, y + CHIP_HEIGHT, 6, fill=fill, outline="", tags=(tag,))
        font = self._fonts.chip_strike if chip.state == ChipState.EXCLUDE else self._fonts.chip
        text_x = x + 7
        self.create_text(text_x, y + CHIP_HEIGHT / 2, text=self._label(chip), anchor="w",
                         fill=fg, font=font, tags=(tag,))
        if chip.count is not None and not self._removable:
            count_fg = "#d1fae5" if chip.state else (theme.TEXT_DISABLED if chip.disabled else theme.TEXT_MUTED)
            self.create_text(text_x + font.measure(chip.label) + 4, y + CHIP_HEIGHT / 2 + 1,
                             text=str(chip.count), anchor="w", fill=count_fg,
                             font=self._fonts.chip_count, tags=(tag,))

    # ---------------- Eventos ----------------

    def _on_configure(self, event) -> None:
        if event.width != self._width:
            self._width = event.width
            self._draw()

    def _chip_at(self, event) -> int | None:
        for item in self.find_overlapping(event.x, event.y, event.x, event.y):
            for tag in self.gettags(item):
                if tag.startswith("chip:"):
                    return int(tag[5:])
        return None

    def _click(self, event, right: bool) -> None:
        index = self._chip_at(event)
        if index is None:
            return
        chip = self._chips[index]
        if chip.disabled and chip.state is None:
            return
        self._on_click(chip.key, right)

    def _on_motion(self, event) -> None:
        self._set_hover(self._chip_at(event))

    def _set_hover(self, index: int | None) -> None:
        if index != self._hover:
            self._hover = index
            clickable = index is not None and not self._chips[index].disabled
            self.configure(cursor="hand2" if clickable else "")
            self._draw()
