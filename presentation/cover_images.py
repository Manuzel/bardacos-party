"""
Imagens das capas prontas para o canvas: no tamanho do card, com os cantos de cima
arredondados sobre a cor do card. Sem capa, uma capa gerada (degradê pela faixa).
As PhotoImage só podem ser criadas na thread da UI.
"""
import colorsys
import hashlib
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageTk

from domain.models import Track, TrackId
from domain.ports import CoverProvider


class CoverImages:
    def __init__(self, covers: CoverProvider | None, background: str, radius: int = 8, max_items: int = 300):
        self._covers = covers
        self._background = background
        self._radius = radius
        self._max_items = max_items
        self._cache: OrderedDict[tuple, ImageTk.PhotoImage] = OrderedDict()

    def get(self, track: Track, width: int, height: int) -> tuple[ImageTk.PhotoImage, bool]:
        """Devolve (imagem, é_capa_real)."""
        path = self._covers.cover_path(track.id) if self._covers else None
        key = (track.id, width, height, path is not None)
        image = self._cache.get(key)
        if image is None:
            source = self._load(path, width, height) if path else None
            image = ImageTk.PhotoImage(self._round_top(source or _placeholder(track.id, width, height)))
            self._cache[key] = image
            while len(self._cache) > self._max_items:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        return image, path is not None

    def invalidate(self, track_id: TrackId) -> None:
        for key in [k for k in self._cache if k[0] == track_id]:
            del self._cache[key]

    def _load(self, path, width: int, height: int) -> Image.Image | None:
        try:
            with Image.open(path) as image:
                return _cover_crop(image.convert("RGB"), width, height)
        except OSError:
            return None

    def _round_top(self, image: Image.Image) -> Image.Image:
        width, height = image.size
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height + self._radius), self._radius, fill=255)
        base = Image.new("RGB", image.size, self._background)
        base.paste(image, (0, 0), mask)
        return base


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    """Preenche o retângulo sem distorcer (corta o excesso no centro)."""
    scale = max(width / image.width, height / image.height)
    resized = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                           Image.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _placeholder(track_id: TrackId, width: int, height: int) -> Image.Image:
    """Degradê escuro com tom estável por faixa (a mesma faixa sempre ganha a mesma cor)."""
    hue = int(hashlib.md5(track_id.encode()).hexdigest()[:4], 16) / 0xFFFF
    top = tuple(round(c * 255) for c in colorsys.hls_to_rgb(hue, 0.30, 0.45))
    bottom = tuple(round(c * 255) for c in colorsys.hls_to_rgb((hue + 0.08) % 1, 0.12, 0.35))
    gradient = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(1, height - 1)
        gradient.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return gradient.resize((width, height))
