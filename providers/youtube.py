import os
import re
import threading
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List

import yt_dlp

from providers.base import BaseAudioProvider


class YouTubeProvider(BaseAudioProvider):
    """
    Provedor de áudio do YouTube baseado no yt-dlp.
    Suporta:
    1. Extração direta de URL de stream (sem gravação em disco para o VLC).
    2. Download persistente ou temporário (cache) para MP3.
    3. Busca rápida de metadados.
    Todas as operações de rede rodam em threads assíncronas dedicadas.
    """

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """Pesquisa rápida de títulos e metadados no YouTube sem efetuar download."""
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'skip_download': True,
            'extract_flat': 'in_playlist',
        }
        search_query = f"ytsearch{max_results}:{query}"
        results = []

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                if 'entries' in info:
                    for entry in info['entries']:
                        results.append({
                            'id': entry.get('id'),
                            'title': entry.get('title'),
                            'url': entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}",
                            'duration': entry.get('duration', 0),
                            'uploader': entry.get('uploader')
                        })
        except Exception as exc:
            print(f"[YouTubeProvider Search Error] {exc}")

        return results

    def get_stream_url(
        self,
        query_or_url: str,
        on_success: Callable[[str, Dict[str, Any]], None],
        on_error: Optional[Callable[[Exception], None]] = None
    ) -> None:
        """
        Extrai em background a URL direta do fluxo de áudio para ser consumida pelo VLC,
        sem salvar nenhum byte em disco.
        """
        def _stream_worker():
            try:
                url = query_or_url.strip()
                if not url.startswith(("http://", "https://")):
                    url = f"ytsearch1:{url}"

                ydl_opts = {
                    'format': 'bestaudio/best',
                    'skip_download': True,
                    'quiet': True,
                    'no_warnings': True,
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if 'entries' in info and len(info['entries']) > 0:
                        info = info['entries'][0]

                    stream_url = info.get('url')
                    if not stream_url:
                        raise ValueError("Não foi possível extrair a URL de streaming deste vídeo.")

                    metadata = {
                        'id': info.get('id'),
                        'title': info.get('title', 'Áudio Desconhecido'),
                        'duration': float(info.get('duration') or 0.0),
                        'webpage_url': info.get('webpage_url') or query_or_url,
                        'uploader': info.get('uploader', 'YouTube')
                    }

                    on_success(stream_url, metadata)

            except Exception as exc:
                if on_error:
                    on_error(exc)
                else:
                    print(f"[YouTubeProvider Stream Error] {exc}")

        thread = threading.Thread(target=_stream_worker, daemon=True)
        thread.start()

    def download(
        self,
        query_or_url: str,
        output_dir: str,
        on_progress: Optional[Callable[[float, str], None]] = None,
        on_complete: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None
    ) -> None:
        """
        Baixa o áudio e converte para MP3 em um diretório alvo
        (pode ser a biblioteca permanente ou a pasta temporária de cache).
        """
        target_dir = Path(output_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        worker_thread = threading.Thread(
            target=self._download_worker,
            args=(query_or_url, str(target_dir), on_progress, on_complete, on_error),
            daemon=True
        )
        worker_thread.start()

    def download_or_stream(
        self,
        query_or_url: str,
        output_dir: str,
        on_progress: Optional[Callable[[float, str], None]] = None,
        on_complete: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None
    ) -> None:
        """Compatibilidade com a interface BaseAudioProvider."""
        self.download(query_or_url, output_dir, on_progress, on_complete, on_error)

    def _download_worker(
        self,
        query_or_url: str,
        output_dir: str,
        on_progress: Optional[Callable[[float, str], None]],
        on_complete: Optional[Callable[[str, Dict[str, Any]], None]],
        on_error: Optional[Callable[[Exception], None]]
    ) -> None:
        """Rotina interna síncrona dentro da thread de download."""
        last_downloaded_file: Optional[str] = None

        def progress_hook(d: Dict[str, Any]) -> None:
            nonlocal last_downloaded_file
            if d['status'] == 'downloading':
                downloaded_bytes = d.get('downloaded_bytes', 0)
                total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                percent = (downloaded_bytes / total_bytes) * 100

                speed = d.get('speed')
                speed_str = f"{speed / (1024 * 1024):.1f} MB/s" if speed else "calculando..."
                status_msg = f"Baixando: {percent:.1f}% ({speed_str})"

                if on_progress:
                    on_progress(min(percent, 99.0), status_msg)

            elif d['status'] == 'finished':
                last_downloaded_file = d.get('filename')
                if on_progress:
                    on_progress(99.0, "Convertendo para MP3...")

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': True,
        }

        try:
            url = query_or_url.strip()
            if not url.startswith(("http://", "https://")):
                url = f"ytsearch1:{url}"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if 'entries' in info and len(info['entries']) > 0:
                    info = info['entries'][0]

                title = info.get('title', 'audio')
                safe_title = re.sub(r'[/\\:*?"<>|]', '_', title)
                expected_mp3 = Path(output_dir) / f"{safe_title}.mp3"

                final_path = str(expected_mp3) if expected_mp3.exists() else last_downloaded_file or ""

                if not final_path or not os.path.exists(final_path):
                    files = list(Path(output_dir).glob("*.mp3")) + list(Path(output_dir).glob("*.ogg"))
                    if files:
                        final_path = str(max(files, key=os.path.getmtime))

            if on_progress:
                on_progress(100.0, "Concluído com sucesso!")

            if on_complete:
                on_complete(final_path, info)

        except Exception as exc:
            if on_error:
                on_error(exc)
            else:
                print(f"[YouTubeProvider Worker Error] {exc}")
