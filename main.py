import logging
import sys
from pathlib import Path

# Adiciona o diretório raiz ao sys.path para importações absolutas dos módulos
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from application.library import LibraryService
from audio.engine import AudioEngine
from infrastructure.persistence.legacy_json import migrate_legacy_library
from infrastructure.persistence.sqlite import SqliteLibraryRepository
from infrastructure.sound_folder import LocalSoundFolder
from providers.youtube import YouTubeProvider
from ui.main_window import MainWindow


def main():
    """
    Ponto de entrada do Soundpad Desktop 'Bardaco's Party'.
    Composition Root: o único lugar que conhece as implementações concretas
    e as injeta nas camadas de cima.
    """
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")

    assets_dir = ROOT_DIR / "assets"
    sound_folder = LocalSoundFolder(assets_dir / "sounds")

    # 1. Catálogo persistente (SQLite), importando o library.json antigo na primeira vez
    repository = SqliteLibraryRepository(assets_dir / "library.db")
    migrate_legacy_library(assets_dir / "library.json", repository)
    library = LibraryService(repository, sound_folder)

    # 2. Motor de áudio híbrido (VLC + Pygame)
    audio_engine = AudioEngine()

    # 3. Provedor de mídia: o YouTube cumpre os dois ports (resolver e downloader)
    youtube_provider = YouTubeProvider()

    # 4. Interface Gráfica
    app = MainWindow(
        audio_engine=audio_engine,
        library=library,
        stream_resolver=youtube_provider,
        downloader=youtube_provider,
        sounds_dir=str(sound_folder.root)
    )

    # Garante o encerramento seguro dos canais de áudio, do cache e do banco ao fechar a janela
    def on_closing():
        app.cleanup_resources()
        app.destroy()
        repository.close()

    app.protocol("WM_DELETE_WINDOW", on_closing)

    # 5. Inicia o loop de eventos
    app.mainloop()


if __name__ == "__main__":
    main()
