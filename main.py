import sys
import os
from pathlib import Path

# Adiciona o diretório raiz ao sys.path para importações absolutas dos módulos
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from audio.engine import AudioEngine
from providers.youtube import YouTubeProvider
from ui.main_window import MainWindow


def main():
    """Ponto de entrada do Soundpad Desktop 'Bardaco's Party'."""
    # Define e garante a existência do diretório de armazenamento de áudio
    sounds_directory = ROOT_DIR / "assets" / "sounds"
    sounds_directory.mkdir(parents=True, exist_ok=True)

    # 1. Inicializa o motor de áudio híbrido (VLC + Pygame)
    audio_engine = AudioEngine()

    # 2. Inicializa o provedor de áudio (Strategy Pattern)
    # Futuramente, pode ser substituído ou combinado com SpotifyProvider sem alterar a UI
    youtube_provider = YouTubeProvider()

    # 3. Inicializa a Interface Gráfica
    app = MainWindow(
        audio_engine=audio_engine,
        audio_provider=youtube_provider,
        sounds_dir=str(sounds_directory)
    )

    # Garante o encerramento seguro dos canais de áudio e limpeza do cache ao fechar a janela
    def on_closing():
        app.cleanup_resources()
        app.destroy()

    app.protocol("WM_DELETE_WINDOW", on_closing)

    # 4. Inicia o loop de eventos
    app.mainloop()


if __name__ == "__main__":
    main()
