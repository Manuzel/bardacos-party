import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional


class LibraryManager:
    """
    Camada de persistência leve para gerenciamento da biblioteca virtual em JSON.
    Armazena e gerencia metadados de faixas não-locais (Streaming e Cache) para que
    não se percam ao reiniciar a aplicação.
    """

    def __init__(self, json_path: Path | str):
        self.json_path = Path(json_path).resolve()
        # Garante a existência do diretório pai (ex: /assets)
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        self._tracks: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> Dict[str, Dict[str, Any]]:
        """Carrega as faixas do arquivo JSON. Cria um arquivo vazio se não existir."""
        if not self.json_path.exists():
            self._tracks = {}
            self.save()
            return self._tracks

        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._tracks = data.get("tracks", {})
        except (json.JSONDecodeError, OSError) as e:
            print(f"[LibraryManager Warning] Falha ao ler {self.json_path}: {e}. Reiniciando base.")
            self._tracks = {}
            self.save()

        return self._tracks

    def save(self) -> None:
        """Salva o estado atual de faixas no arquivo JSON de forma segura."""
        try:
            payload = {
                "version": "1.0",
                "tracks": self._tracks
            }
            # Gravação direta com indentação limpa
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"[LibraryManager Error] Falha ao salvar biblioteca JSON: {e}")

    def add_track(
        self,
        sound_id: str,
        title: str,
        url: str,
        mode: str,
        duration: float = 0.0
    ) -> Dict[str, Any]:
        """
        Adiciona ou atualiza uma faixa virtual na biblioteca.
        
        :param sound_id: Identificador único da faixa.
        :param title: Nome legível do som.
        :param url: URL original da fonte (YouTube).
        :param mode: Modo de operação ('stream' ou 'cache').
        :param duration: Duração em segundos.
        """
        track_data = {
            "sound_id": sound_id,
            "title": title,
            "url": url,
            "mode": mode,
            "duration": float(duration)
        }
        self._tracks[sound_id] = track_data
        self.save()
        return track_data

    def remove_track(self, sound_id: str) -> bool:
        """Remove uma faixa virtual pelo seu sound_id."""
        if sound_id in self._tracks:
            del self._tracks[sound_id]
            self.save()
            return True
        return False

    def get_track(self, sound_id: str) -> Optional[Dict[str, Any]]:
        """Retorna os metadados de uma faixa específica."""
        return self._tracks.get(sound_id)

    def get_all_tracks(self) -> List[Dict[str, Any]]:
        """Retorna todas as faixas virtuais cadastradas."""
        return list(self._tracks.values())
