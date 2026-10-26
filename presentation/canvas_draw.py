"""
Desenho e layout em tk.Canvas.
Cards e chips são desenhados como itens de canvas em vez de dezenas de widgets:
é o que permite rolar e filtrar centenas de faixas sem travar.
As funções de layout são puras (recebem `measure`), para serem testadas sem Tk.
"""
from dataclasses import dataclass
from typing import Callable, Sequence

Measure = Callable[[str], int]


def round_rect(canvas, x1: float, y1: float, x2: float, y2: float, radius: float, **options) -> int:
    """Retângulo de cantos arredondados (polígono suavizado)."""
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = (
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    )
    return canvas.create_polygon(points, smooth=True, splinesteps=12, **options)


def wrap_lines(text: str, measure: Measure, width: int, max_lines: int) -> list[str]:
    """Quebra por palavras em até `max_lines` linhas; a última ganha reticências se sobrar texto."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for index, word in enumerate(words):
        candidate = f"{current} {word}".strip()
        if measure(candidate) <= width or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines:
            return _ellipsize(lines, " ".join(words[index:]), measure, width)
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        return _ellipsize(lines[:max_lines], " ".join(lines[max_lines:]), measure, width)
    return [_fit(line, measure, width) for line in lines]


def _ellipsize(lines: list[str], rest: str, measure: Measure, width: int) -> list[str]:
    last = f"{lines[-1]} {rest}".strip()
    lines[-1] = _fit(last, measure, width, force_ellipsis=True)
    return lines


def _fit(text: str, measure: Measure, width: int, force_ellipsis: bool = False) -> str:
    if measure(text) <= width and not force_ellipsis:
        return text
    while text and measure(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


@dataclass(frozen=True, slots=True)
class ChipBox:
    index: int     # Posição na lista de entrada
    x: float
    y: float
    width: float
    row: int


@dataclass(frozen=True, slots=True)
class ChipLayout:
    boxes: tuple[ChipBox, ...]
    hidden: int                  # Quantos não couberam (mostrados como "+N")
    more_x: float | None         # Onde desenhar o "+N"
    more_y: float | None
    height: float


def layout_chips(
    labels: Sequence[str],
    measure: Measure,
    max_width: float,
    chip_height: float,
    pad_x: float = 7,
    gap: float = 4,
    max_rows: int | None = None,
    more_label: Callable[[int], str] = lambda n: f"+{n}",
) -> ChipLayout:
    """Distribui chips em linhas (flow). Com `max_rows`, o que sobra vira um "+N" no fim."""
    boxes: list[ChipBox] = []
    x = y = 0.0
    row = 0
    for index, label in enumerate(labels):
        width = min(measure(label) + 2 * pad_x, max_width)
        if x > 0 and x + width > max_width:
            row += 1
            if max_rows is not None and row >= max_rows:
                break
            x, y = 0.0, y + chip_height + gap
        boxes.append(ChipBox(index, x, y, width, row))
        x += width + gap

    hidden = len(labels) - len(boxes)
    if not hidden:
        rows = (boxes[-1].row + 1) if boxes else 0
        return ChipLayout(tuple(boxes), 0, None, None, rows * chip_height + max(0, rows - 1) * gap)

    # Abre espaço para o "+N" na última linha, tirando chips do fim se preciso
    while True:
        more_width = measure(more_label(hidden)) + 2
        last_row = boxes[-1].row
        end = boxes[-1].x + boxes[-1].width + gap
        if end + more_width <= max_width or len([b for b in boxes if b.row == last_row]) == 1:
            break
        boxes.pop()
        hidden += 1
    last = boxes[-1]
    rows = last.row + 1
    height = rows * chip_height + (rows - 1) * gap
    return ChipLayout(tuple(boxes), hidden, last.x + last.width + gap, last.y, height)
