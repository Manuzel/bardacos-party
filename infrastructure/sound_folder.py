from pathlib import Path

AUDIO_EXTENSIONS = frozenset({".mp3", ".ogg", ".wav"})


class LocalSoundFolder:
    """Implementa o port `SoundFolder` sobre uma pasta do disco (assets/sounds)."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list_audio_files(self) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
        )

    def absolute_path(self, relative_path: str) -> Path:
        return self.root / relative_path
