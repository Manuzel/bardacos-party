import os
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional, Any

# Verificação e importação condicional do VLC
try:
    import vlc
    VLC_AVAILABLE = True
except (ImportError, OSError):
    VLC_AVAILABLE = False

# Importação do Pygame
try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False


class BaseTrackPlayer(ABC):
    """Contrato abstrato para reprodutores de faixas individuais."""

    @abstractmethod
    def play(self, loop: bool = False) -> bool:
        pass

    @abstractmethod
    def pause(self) -> None:
        pass

    @abstractmethod
    def resume(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def seek(self, seconds: float) -> bool:
        pass

    @abstractmethod
    def set_volume(self, volume: float) -> None:
        pass

    @abstractmethod
    def get_position(self) -> float:
        """Retorna o tempo atual em segundos."""
        pass

    @abstractmethod
    def get_duration(self) -> float:
        """Retorna a duração total em segundos."""
        pass

    @abstractmethod
    def is_playing(self) -> bool:
        pass

    @abstractmethod
    def is_paused(self) -> bool:
        pass


class VLCTrackPlayer(BaseTrackPlayer):
    """
    Sub-motor baseado em libVLC (python-vlc).
    Oferece suporte nativo a:
    - Streaming direto de URLs (sem salvar no disco)
    - Linha do tempo (Seek) precisa em milissegundos
    - Leitura de arquivos locais e temporários
    - Multi-track através de múltiplas instâncias de MediaPlayer
    """

    def __init__(self, source: str, volume: float = 0.8, loop: bool = False, duration: float = 0.0):
        if not VLC_AVAILABLE:
            raise RuntimeError("VLC não está disponível no sistema operacional ou python-vlc não foi instalado.")

        self.source = source
        self.volume = max(0.0, min(1.0, volume))
        self.loop = loop
        self.preset_duration = duration

        # Instancia libVLC sem interface gráfica e em modo silencioso
        self._vlc_instance = vlc.Instance("--no-xlib", "--quiet")
        self._player = self._vlc_instance.media_player_new()
        self._media = self._vlc_instance.media_new(source)
        self._player.set_media(self._media)

        # Configura volume (escala do VLC é 0-100)
        self._player.audio_set_volume(int(self.volume * 100))
        self._is_paused = False

    def play(self, loop: Optional[bool] = None) -> bool:
        if loop is not None:
            self.loop = loop

        if self._is_paused:
            self._player.play()
            self._is_paused = False
            return True

        state = self._player.get_state()
        if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
            # Recarrega a mídia caso tenha finalizado
            self._player.set_media(self._media)

        res = self._player.play()
        self._player.audio_set_volume(int(self.volume * 100))
        self._is_paused = False
        return res == 0

    def pause(self) -> None:
        if self._player.is_playing():
            self._player.pause()
            self._is_paused = True

    def resume(self) -> None:
        if self._is_paused:
            self._player.play()
            self._is_paused = False

    def stop(self) -> None:
        self._player.stop()
        self._is_paused = False

    def seek(self, seconds: float) -> bool:
        target_ms = int(max(0.0, seconds) * 1000)
        # O VLC aceita set_time em milissegundos
        res = self._player.set_time(target_ms)
        return res == 0

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))
        self._player.audio_set_volume(int(self.volume * 100))

    def get_position(self) -> float:
        current_ms = self._player.get_time()
        if current_ms < 0:
            return 0.0
        return current_ms / 1000.0

    def get_duration(self) -> float:
        # Se informada duração de metadados externa (ex: yt-dlp)
        if self.preset_duration > 0:
            return self.preset_duration

        total_ms = self._player.get_length()
        if total_ms > 0:
            return total_ms / 1000.0
        return 0.0

    def is_playing(self) -> bool:
        # Verifica se finalizou e deve reiniciar em loop
        if self.loop and self._player.get_state() == vlc.State.Ended:
            self._player.set_media(self._media)
            self._player.play()
            return True

        return bool(self._player.is_playing())

    def is_paused(self) -> bool:
        return self._is_paused


class PygameTrackPlayer(BaseTrackPlayer):
    """
    Sub-motor baseado em pygame.mixer para arquivos locais quando VLC não estiver presente.
    Nota: Pygame não possui seek nativo preciso para múltiplos canais de Sound.
    """

    def __init__(self, source: str, volume: float = 0.8, loop: bool = False, duration: float = 0.0):
        if not PYGAME_AVAILABLE:
            raise RuntimeError("Pygame não está disponível.")

        self.source = source
        self.volume = max(0.0, min(1.0, volume))
        self.loop = loop
        self.duration = duration
        self.is_paused_state = False

        self.sound = pygame.mixer.Sound(source)
        self.channel: Optional[pygame.mixer.Channel] = None
        self._start_time = 0.0
        self._accumulated_pos = 0.0

    def play(self, loop: Optional[bool] = None) -> bool:
        if loop is not None:
            self.loop = loop

        if self.is_paused_state and self.channel:
            self.channel.unpause()
            self.is_paused_state = False
            self._start_time = time.time()
            return True

        self.channel = pygame.mixer.find_channel(force=True)
        if not self.channel:
            return False

        self.channel.set_volume(self.volume)
        loops_arg = -1 if self.loop else 0
        self.channel.play(self.sound, loops=loops_arg)
        self.is_paused_state = False
        self._start_time = time.time()
        self._accumulated_pos = 0.0
        return True

    def pause(self) -> None:
        if self.channel and self.channel.get_busy() and not self.is_paused_state:
            self._accumulated_pos += time.time() - self._start_time
            self.channel.pause()
            self.is_paused_state = True

    def resume(self) -> None:
        if self.channel and self.is_paused_state:
            self.channel.unpause()
            self.is_paused_state = False
            self._start_time = time.time()

    def stop(self) -> None:
        if self.channel:
            self.channel.stop()
            self.channel = None
        self.is_paused_state = False
        self._accumulated_pos = 0.0

    def seek(self, seconds: float) -> bool:
        # Pygame Sound não possui set_pos nativo em canais individuais
        # Mantemos o registro aproximado
        self._accumulated_pos = seconds
        self._start_time = time.time()
        return False

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))
        if self.channel:
            self.channel.set_volume(self.volume)

    def get_position(self) -> float:
        if self.is_paused_state:
            return self._accumulated_pos
        if self.channel and self.channel.get_busy():
            return self._accumulated_pos + (time.time() - self._start_time)
        return 0.0

    def get_duration(self) -> float:
        if self.duration > 0:
            return self.duration
        return self.sound.get_length()

    def is_playing(self) -> bool:
        return bool(self.channel and self.channel.get_busy() and not self.is_paused_state)

    def is_paused(self) -> bool:
        return self.is_paused_state


class AudioEngine:
    """
    Facade e Factory para reprodução multi-track de áudio.
    Gerencia instâncias internas (VLC e Pygame) de forma transparente para a UI:
    - Se a fonte for URL remota (Streaming), utiliza VLCTrackPlayer.
    - Se for arquivo local/cache e o VLC estiver disponível, utiliza VLCTrackPlayer para permitir Seek interativo.
    - Como fallback local, utiliza PygameTrackPlayer.
    """

    def __init__(self, num_pygame_channels: int = 32, **kwargs):
        self.has_vlc = VLC_AVAILABLE
        self._tracks: Dict[str, BaseTrackPlayer] = {}

        channels = kwargs.get("num_channels", num_pygame_channels)

        if PYGAME_AVAILABLE and not pygame.mixer.get_init():
            try:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
                pygame.mixer.set_num_channels(channels)
            except Exception as e:
                print(f"[AudioEngine Warning] Falha ao inicializar pygame.mixer: {e}")

    def play(
        self,
        sound_id: str,
        source: str,
        volume: float = 0.8,
        loop: bool = False,
        duration: float = 0.0,
        force_engine: Optional[str] = None
    ) -> bool:
        """
        Inicia ou retoma a reprodução de uma faixa específica identificada por sound_id.
        Cria o sub-motor apropriado (Factory) caso ainda não exista.
        """
        # Se a faixa já foi instanciada
        if sound_id in self._tracks:
            track = self._tracks[sound_id]
            if track.is_paused():
                track.resume()
                return True
            else:
                track.stop()

        # Seleciona o sub-motor (Factory)
        is_url = source.startswith(("http://", "https://", "rtsp://", "mms://"))

        if is_url or (self.has_vlc and force_engine != "pygame"):
            if not self.has_vlc:
                raise RuntimeError(
                    "O 'Streaming Direto' requer a biblioteca VLC instalada no sistema (libvlc). "
                    "Por favor, instale o pacote 'vlc' em seu Linux (ex: sudo apt install vlc)."
                )
            player = VLCTrackPlayer(source=source, volume=volume, loop=loop, duration=duration)
        else:
            player = PygameTrackPlayer(source=source, volume=volume, loop=loop, duration=duration)

        success = player.play(loop=loop)
        if success:
            self._tracks[sound_id] = player
        return success

    def pause(self, sound_id: str) -> None:
        track = self._tracks.get(sound_id)
        if track:
            track.pause()

    def resume(self, sound_id: str) -> None:
        track = self._tracks.get(sound_id)
        if track:
            track.resume()

    def stop(self, sound_id: str) -> None:
        track = self._tracks.pop(sound_id, None)
        if track:
            track.stop()

    def stop_all(self) -> None:
        """Interrompe todas as faixas ativas simultaneamente."""
        for track in list(self._tracks.values()):
            track.stop()
        self._tracks.clear()

    def seek(self, sound_id: str, seconds: float) -> bool:
        """Move o cabeçote de leitura da faixa para o tempo desejado."""
        track = self._tracks.get(sound_id)
        if track:
            return track.seek(seconds)
        return False

    def get_position(self, sound_id: str) -> float:
        """Retorna a posição atual de reprodução em segundos."""
        track = self._tracks.get(sound_id)
        if track:
            return track.get_position()
        return 0.0

    def get_duration(self, sound_id: str) -> float:
        """Retorna a duração total da faixa em segundos."""
        track = self._tracks.get(sound_id)
        if track:
            return track.get_duration()
        return 0.0

    def set_volume(self, sound_id: str, volume: float) -> None:
        """Define o volume individual da faixa (0.0 a 1.0)."""
        track = self._tracks.get(sound_id)
        if track:
            track.set_volume(volume)

    def set_loop(self, sound_id: str, loop: bool) -> None:
        """Define se a faixa deve repetir continuamente."""
        track = self._tracks.get(sound_id)
        if track:
            if hasattr(track, 'loop'):
                track.loop = loop

    def is_playing(self, sound_id: str) -> bool:
        track = self._tracks.get(sound_id)
        return track.is_playing() if track else False

    def is_paused(self, sound_id: str) -> bool:
        track = self._tracks.get(sound_id)
        return track.is_paused() if track else False
