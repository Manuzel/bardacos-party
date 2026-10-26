"""
Capas das faixas (implementa o port `CoverProvider`).
- YouTube: a miniatura sai do id do vídeo (i.ytimg.com), sem chamar o yt-dlp.
- Arquivo local: a capa embutida, se o leitor de mídia a encontrar.
Tudo em segundo plano; as capas ficam em assets/covers/<id da faixa>.jpg, já em 16:9.
"""
import io
import logging
import re
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from PIL import Image

from domain.models import Track, TrackId

log = logging.getLogger(__name__)

COVER_SIZE = (480, 270)
_YOUTUBE_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")
# maxresdefault não existe para todo vídeo; hqdefault sempre existe (4:3 com faixas pretas)
_THUMBNAIL_URLS = ("https://i.ytimg.com/vi/{id}/maxresdefault.jpg", "https://i.ytimg.com/vi/{id}/hqdefault.jpg")


def youtube_id(url: str | None) -> str | None:
    match = _YOUTUBE_ID.search(url or "")
    return match.group(1) if match else None


def to_cover(image: Image.Image) -> Image.Image:
    """Corta no centro para 16:9 (some com as faixas pretas do hqdefault) e reduz."""
    image = image.convert("RGB")
    width, height = image.size
    target = 16 / 9
    if width / height > target:
        new_width = round(height * target)
        left = (width - new_width) // 2
        image = image.crop((left, 0, left + new_width, height))
    else:
        new_height = round(width / target)
        top = (height - new_height) // 2
        image = image.crop((0, top, width, top + new_height))
    return image.resize(COVER_SIZE, Image.LANCZOS)


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "BardacosParty/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


class CoverStore:
    def __init__(
        self,
        folder: Path,
        fetch_bytes: Callable[[str], bytes] = _http_get,
        artwork_of: Callable[[Path], Path | None] | None = None,
        max_workers: int = 2
    ):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self._fetch_bytes = fetch_bytes
        self._artwork_of = artwork_of
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="covers")
        self._lock = threading.Lock()
        self._pending: set[TrackId] = set()
        self._failed: set[TrackId] = set()  # Não insiste na mesma sessão

    def cover_path(self, track_id: TrackId) -> Path | None:
        path = self._file(track_id)
        return path if path.exists() else None

    def request(self, track: Track, local_file: Path | None, on_ready: Callable[[TrackId], None]) -> None:
        if self.cover_path(track.id) is not None:
            return
        with self._lock:
            if track.id in self._pending or track.id in self._failed:
                return
            self._pending.add(track.id)
        self._pool.submit(self._work, track, local_file, on_ready)

    def discard(self, track_id: TrackId) -> None:
        self._file(track_id).unlink(missing_ok=True)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _work(self, track: Track, local_file: Path | None, on_ready: Callable[[TrackId], None]) -> None:
        try:
            image = self._fetch(track, local_file)
            if image is None:
                with self._lock:
                    self._failed.add(track.id)
                return
            tmp = self._file(track.id).with_suffix(".tmp")
            to_cover(image).save(tmp, "JPEG", quality=85)
            tmp.replace(self._file(track.id))  # Nunca deixa uma capa pela metade
            on_ready(track.id)
        except Exception as exc:
            log.info("Sem capa para '%s': %s", track.title, exc)
            with self._lock:
                self._failed.add(track.id)
        finally:
            with self._lock:
                self._pending.discard(track.id)

    def _fetch(self, track: Track, local_file: Path | None) -> Image.Image | None:
        video_id = youtube_id(track.origin_url)
        if video_id:
            for template in _THUMBNAIL_URLS:
                try:
                    return Image.open(io.BytesIO(self._fetch_bytes(template.format(id=video_id))))
                except Exception:
                    continue
        if local_file is not None and self._artwork_of is not None:
            artwork = self._artwork_of(local_file)
            if artwork is not None:
                return Image.open(artwork)
        return None

    def _file(self, track_id: TrackId) -> Path:
        return self.folder / f"{track_id}.jpg"
