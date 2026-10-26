import io
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

from audio_fakes import make_track
from domain.models import SourceKind
from infrastructure.covers import COVER_SIZE, CoverStore, to_cover, youtube_id


def jpeg(width: int, height: int, color=(200, 40, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "JPEG")
    return buffer.getvalue()


def remote(track_id="s1", url="https://www.youtube.com/watch?v=oVZ8KGtZtwY"):
    return make_track(track_id, source_kind=SourceKind.STREAM, local_path=None, origin_url=url)


class YoutubeIdTest(unittest.TestCase):
    def test_formatos_de_link(self):
        for url in ("https://www.youtube.com/watch?v=oVZ8KGtZtwY",
                    "https://youtu.be/oVZ8KGtZtwY?t=30",
                    "https://www.youtube.com/watch?list=PL1&v=oVZ8KGtZtwY",
                    "https://youtube.com/shorts/oVZ8KGtZtwY"):
            self.assertEqual(youtube_id(url), "oVZ8KGtZtwY", url)
        self.assertIsNone(youtube_id("https://soundcloud.com/x"))
        self.assertIsNone(youtube_id(None))


class ToCoverTest(unittest.TestCase):
    def test_corta_para_16_9_no_centro(self):
        # hqdefault: 480x360 com faixas pretas em cima e embaixo
        image = Image.new("RGB", (480, 360), (0, 0, 0))
        image.paste((255, 0, 0), (0, 45, 480, 315))
        cover = to_cover(image)
        self.assertEqual(cover.size, COVER_SIZE)
        self.assertEqual(cover.getpixel((240, 2)), (255, 0, 0))  # Sem faixa preta


class CoverStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "covers"
        self.requested: list[str] = []
        self.ready = threading.Event()

    def tearDown(self):
        self._tmp.cleanup()

    def store(self, fetch, artwork_of=None, workers=2):
        def recording_fetch(url):
            self.requested.append(url)
            return fetch(url)
        return CoverStore(self.folder, fetch_bytes=recording_fetch, artwork_of=artwork_of, max_workers=workers)

    def wait_idle(self, store):
        """Com um worker só, a fila é FIFO: quando isto roda, o que veio antes terminou."""
        done = threading.Event()
        store._pool.submit(done.set)
        self.assertTrue(done.wait(5))

    def on_ready(self, track_id):
        self.ready_id = track_id
        self.ready.set()

    def test_youtube_baixa_corta_e_avisa(self):
        store = self.store(lambda url: jpeg(1280, 720))
        store.request(remote(), None, self.on_ready)
        self.assertTrue(self.ready.wait(5))
        with Image.open(store.cover_path("s1")) as image:
            self.assertEqual(image.size, COVER_SIZE)
        self.assertIn("maxresdefault", self.requested[0])
        store.close()

    def test_sem_maxres_usa_hqdefault(self):
        def fetch(url):
            if "maxres" in url:
                raise OSError("404")
            return jpeg(480, 360)
        store = self.store(fetch)
        store.request(remote(), None, self.on_ready)
        self.assertTrue(self.ready.wait(5))
        self.assertIn("hqdefault", self.requested[-1])
        store.close()

    def test_local_usa_a_capa_embutida(self):
        art = Path(self._tmp.name) / "art.png"
        Image.new("RGB", (300, 300), (10, 200, 10)).save(art)
        store = self.store(lambda url: b"", artwork_of=lambda path: art)
        store.request(make_track("l1"), Path("/sons/l1.mp3"), self.on_ready)
        self.assertTrue(self.ready.wait(5))
        with Image.open(store.cover_path("l1")) as image:
            self.assertGreater(image.getpixel((5, 5))[1], 150)  # Verde da capa embutida
        store.close()

    def test_falha_nao_repete_na_sessao_e_capa_existente_nao_busca(self):
        def offline(url):
            raise OSError("offline")
        store = self.store(offline, workers=1)
        store.request(remote(), None, self.on_ready)
        self.wait_idle(store)
        calls = len(self.requested)
        self.assertEqual(calls, 2)  # maxres e hqdefault
        store.request(remote(), None, self.on_ready)
        self.wait_idle(store)
        self.assertEqual(len(self.requested), calls)
        self.assertFalse(self.ready.is_set())

        self.folder.joinpath("s2.jpg").write_bytes(jpeg(16, 9))
        store.request(remote("s2"), None, self.on_ready)
        self.assertEqual(len(self.requested), calls)
        store.discard("s2")
        self.assertIsNone(store.cover_path("s2"))
        store.close()


if __name__ == "__main__":
    unittest.main()
