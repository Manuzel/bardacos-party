import os
import tempfile
from pathlib import Path
from typing import Dict
import customtkinter as ctk

from application.library import LibraryService
from audio.engine import AudioEngine
from domain.models import SourceKind
from domain.ports import StreamResolver, TrackDownloader
from ui.components import SoundCard


class MainWindow(ctk.CTk):
    """
    Janela Principal do Bardaco's Party com:
    - Catálogo único (LibraryService) para faixas locais, de streaming e de cache
    - Resolução On-Demand de streams salvos
    - Exclusão dinâmica com remoção física de arquivo ou descarte de registro
    """

    def __init__(
        self,
        audio_engine: AudioEngine,
        library: LibraryService,
        stream_resolver: StreamResolver,
        downloader: TrackDownloader,
        sounds_dir: str
    ):
        super().__init__()

        self.audio_engine = audio_engine
        self.library = library
        self.stream_resolver = stream_resolver
        self.downloader = downloader
        self.sounds_dir = Path(sounds_dir).resolve()

        # Pasta temporária para o modo 'Tocar em Cache'
        self.temp_cache_dir = tempfile.TemporaryDirectory(prefix="bardaco_cache_")

        # Configurações da Janela
        self.title("Bardaco's Party — RPG Soundpad")
        self.geometry("1150x780")
        self.minsize(900, 620)

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color="#0b0d12")

        self.cards_dict: Dict[str, SoundCard] = {}

        self._build_ui()
        self.load_sound_cards()

    def _build_ui(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ---------------- 1. HEADER SUPERIOR ----------------
        header_frame = ctk.CTkFrame(self, fg_color="#13161f", corner_radius=0, height=65)
        header_frame.grid(row=0, column=0, sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.grid(row=0, column=0, padx=20, pady=12, sticky="w")

        title_lbl = ctk.CTkLabel(
            title_box,
            text="⚔️ Bardaco's Party",
            font=ctk.CTkFont(family="Helvetica", size=22, weight="bold"),
            text_color="#f9fafb"
        )
        title_lbl.pack(side="left")

        badge_lbl = ctk.CTkLabel(
            title_box,
            text="MULTI-TRACK SOUNDPAD",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#38bdf8",
            fg_color="#0c4a6e",
            corner_radius=6,
            padx=8,
            pady=2
        )
        badge_lbl.pack(side="left", padx=12)

        # Ações Globais
        actions_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        actions_box.grid(row=0, column=1, padx=20, pady=12, sticky="e")

        self.btn_refresh = ctk.CTkButton(
            actions_box,
            text="🔄 Recarregar",
            width=100,
            height=34,
            corner_radius=8,
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self.load_sound_cards
        )
        self.btn_refresh.pack(side="left", padx=4)

        self.btn_stop_all = ctk.CTkButton(
            actions_box,
            text="🛑 Parar Todos",
            width=120,
            height=34,
            corner_radius=8,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            font=ctk.CTkFont(weight="bold"),
            command=self._on_stop_all
        )
        self.btn_stop_all.pack(side="left", padx=4)

        # ---------------- 2. BARRA DE AÇÃO COM SELETOR DE MODOS ----------------
        action_card = ctk.CTkFrame(self, fg_color="#161922", corner_radius=12, border_width=1, border_color="#262c3d")
        action_card.grid(row=1, column=0, padx=20, pady=(14, 8), sticky="ew")
        action_card.grid_columnconfigure(1, weight=1)

        self.mode_selector = ctk.CTkOptionMenu(
            action_card,
            values=["Streaming Direto", "Tocar em Cache", "Salvar na Biblioteca"],
            width=180,
            height=38,
            corner_radius=8,
            fg_color="#1f2937",
            button_color="#374151",
            button_hover_color="#4b5563",
            dropdown_fg_color="#1f2937",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.mode_selector.set("Streaming Direto")
        self.mode_selector.grid(row=0, column=0, padx=(14, 6), pady=12)

        self.query_entry = ctk.CTkEntry(
            action_card,
            placeholder_text="Cole o link do YouTube ou busque um efeito sonoro (ex: Taverna, Batalha Épica)...",
            height=38,
            corner_radius=8,
            fg_color="#0b0d13",
            border_color="#374151"
        )
        self.query_entry.grid(row=0, column=1, padx=6, pady=12, sticky="ew")
        self.query_entry.bind("<Return>", lambda e: self._on_execute_action())

        self.exec_button = ctk.CTkButton(
            action_card,
            text="▶ Processar",
            height=38,
            width=130,
            corner_radius=8,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            font=ctk.CTkFont(weight="bold"),
            command=self._on_execute_action
        )
        self.exec_button.grid(row=0, column=2, padx=(6, 14), pady=12)

        self.progress_bar = ctk.CTkProgressBar(action_card, height=4, progress_color="#10b981")
        self.progress_bar.set(0)
        self.progress_bar.grid(row=1, column=0, columnspan=3, padx=14, pady=(0, 2), sticky="ew")
        self.progress_bar.grid_remove()

        self.status_label = ctk.CTkLabel(
            action_card,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#9ca3af"
        )
        self.status_label.grid(row=2, column=0, columnspan=3, padx=14, pady=(0, 6), sticky="w")
        self.status_label.grid_remove()

        # ---------------- 3. GRID MULTI-TRACK COM SCROLL ----------------
        self.scroll_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            label_text="PAINEL DE FAIXAS ATIVAS E BIBLIOTECA",
            label_font=ctk.CTkFont(family="Helvetica", size=12, weight="bold"),
            label_text_color="#94a3b8"
        )
        self.scroll_frame.grid(row=2, column=0, padx=16, pady=(6, 16), sticky="nsew")

        for c in range(3):
            self.scroll_frame.grid_columnconfigure(c, weight=1, uniform="soundpad_grid")

    # --- PROCESSAMENTO E PERSISTÊNCIA DOS 3 MODOS ---

    def _on_execute_action(self) -> None:
        query = self.query_entry.get().strip()
        if not query:
            self._show_status("Digite uma URL ou termo de busca.", is_error=True)
            return

        mode = self.mode_selector.get()
        self.exec_button.configure(state="disabled", text="Processando...")
        self.progress_bar.grid()
        self.progress_bar.set(0.1)
        self.status_label.grid()

        if mode == "Streaming Direto":
            self._show_status("Extraindo fluxo direto de áudio (Nuvem)...", is_error=False)
            self.stream_resolver.get_stream_url(
                query_or_url=query,
                on_success=lambda url, meta: self.after(0, self._on_stream_ready, url, meta, query),
                on_error=lambda exc: self.after(0, self._on_operation_error, exc)
            )

        elif mode == "Tocar em Cache":
            self._show_status("Baixando faixa para cache temporário...", is_error=False)
            self.downloader.download(
                query_or_url=query,
                output_dir=self.temp_cache_dir.name,
                on_progress=lambda pct, msg: self.after(0, self._on_progress, pct, msg),
                on_complete=lambda path, meta: self.after(0, self._on_cache_ready, path, meta, query),
                on_error=lambda exc: self.after(0, self._on_operation_error, exc)
            )

        elif mode == "Salvar na Biblioteca":
            self._show_status("Baixando para a biblioteca permanente...", is_error=False)
            self.downloader.download(
                query_or_url=query,
                output_dir=str(self.sounds_dir),
                on_progress=lambda pct, msg: self.after(0, self._on_progress, pct, msg),
                on_complete=lambda path, meta: self.after(0, self._on_library_download_ready, path, meta),
                on_error=lambda exc: self.after(0, self._on_operation_error, exc)
            )

    def _on_progress(self, percent: float, msg: str) -> None:
        self.progress_bar.set(percent / 100.0)
        self._show_status(msg, is_error=False)

    def _on_stream_ready(self, stream_url: str, meta: dict, original_query: str) -> None:
        """Registra a faixa de streaming no catálogo e adiciona ao Grid."""
        title = meta.get("title", "Streaming YouTube")
        duration = float(meta.get("duration", 0.0))
        target_url = meta.get("webpage_url") or original_query
        sound_id = self.library.add_remote_track(
            title=title,
            origin_url=target_url,
            source_kind=SourceKind.STREAM,
            duration_s=duration
        ).id

        self._show_status(f"Streaming registrado: '{title}'", is_error=False)
        self._finish_action()

        card = SoundCard(
            master=self.scroll_frame,
            sound_id=sound_id,
            title=title,
            source=stream_url,
            audio_engine=self.audio_engine,
            mode_type="stream",
            duration=duration,
            original_url=target_url,
            on_save_to_library=self._save_card_to_permanent_library,
            on_delete=self._delete_sound_card,
            on_resolve_source=self._resolve_virtual_track
        )
        self._add_card_to_grid(sound_id, card, auto_play=True)

    def _on_cache_ready(self, file_path: str, meta: dict, original_query: str) -> None:
        """Registra a faixa de cache no catálogo e adiciona ao Grid."""
        title = meta.get("title", Path(file_path).stem)
        duration = float(meta.get("duration", 0.0))
        target_url = meta.get("webpage_url") or original_query
        sound_id = self.library.add_remote_track(
            title=title,
            origin_url=target_url,
            source_kind=SourceKind.CACHE,
            duration_s=duration
        ).id

        self._show_status(f"Faixa em cache registrada: '{title}'", is_error=False)
        self._finish_action()

        card = SoundCard(
            master=self.scroll_frame,
            sound_id=sound_id,
            title=title,
            source=file_path,
            audio_engine=self.audio_engine,
            mode_type="cache",
            duration=duration,
            original_url=target_url,
            on_save_to_library=self._save_card_to_permanent_library,
            on_delete=self._delete_sound_card,
            on_resolve_source=self._resolve_virtual_track
        )
        self._add_card_to_grid(sound_id, card, auto_play=True)

    def _on_library_download_ready(self, file_path: str, meta: dict) -> None:
        title = meta.get("title", Path(file_path).stem)
        self._show_status(f"Salvo na biblioteca com sucesso: '{title}'", is_error=False)
        self._finish_action()
        self.load_sound_cards()

    def _save_card_to_permanent_library(self, sound_id: str, original_url: str) -> None:
        self._show_status("Baixando cópia permanente para assets/sounds em segundo plano...", is_error=False)
        self.downloader.download(
            query_or_url=original_url,
            output_dir=str(self.sounds_dir),
            on_complete=lambda path, meta: self.after(0, self._on_background_save_complete, meta),
            on_error=lambda exc: self.after(0, self._on_operation_error, exc)
        )

    def _on_background_save_complete(self, meta: dict) -> None:
        title = meta.get("title", "Áudio")
        self._show_status(f"Faixa '{title}' salva na biblioteca permanente!", is_error=False)
        self.load_sound_cards()

    def _on_operation_error(self, exc: Exception) -> None:
        self.progress_bar.grid_remove()
        self._show_status(f"Erro: {exc}", is_error=True)
        self.exec_button.configure(state="normal", text="▶ Processar")

    def _finish_action(self) -> None:
        self.progress_bar.set(1.0)
        self.query_entry.delete(0, "end")
        self.exec_button.configure(state="normal", text="▶ Processar")
        self.after(3500, lambda: self.progress_bar.grid_remove())
        self.after(5000, lambda: self.status_label.grid_remove())

    def _show_status(self, text: str, is_error: bool = False) -> None:
        self.status_label.grid()
        color = "#ef4444" if is_error else "#10b981"
        self.status_label.configure(text=text, text_color=color)

    # --- RESOLUÇÃO ON-DEMAND PARA FAIXAS VIRTUAIS ---

    def _resolve_virtual_track(
        self,
        sound_id: str,
        target_url: str,
        mode_type: str,
        on_ready,
        on_error
    ) -> None:
        """Resolve a URL de streaming expirada ou baixa novamente para a pasta de cache."""
        if mode_type == "stream":
            self.stream_resolver.get_stream_url(
                query_or_url=target_url,
                on_success=lambda url, meta: self.after(0, on_ready, url),
                on_error=lambda exc: self.after(0, on_error, exc)
            )
        elif mode_type == "cache":
            self.downloader.download(
                query_or_url=target_url,
                output_dir=self.temp_cache_dir.name,
                on_complete=lambda path, meta: self.after(0, on_ready, path),
                on_error=lambda exc: self.after(0, on_error, exc)
            )

    # --- MESCLAGEM (MERGE) E EXCLUSÃO DA BIBLIOTECA ---

    def load_sound_cards(self) -> None:
        """
        Reconcilia a pasta assets/sounds com o catálogo e recria os cards de todas
        as faixas disponíveis (locais, streaming e cache), em ordem alfabética.
        """
        for card in list(self.cards_dict.values()):
            card.destroy()
        self.cards_dict.clear()

        self.library.refresh_local_files()

        for track in self.library.tracks():
            if track.source_kind == SourceKind.LOCAL:
                card = SoundCard(
                    master=self.scroll_frame,
                    sound_id=track.id,
                    title=track.title,
                    source=str(self.library.local_file_of(track)),
                    audio_engine=self.audio_engine,
                    mode_type=track.source_kind.value,
                    duration=track.duration_s or 0.0,
                    on_delete=self._delete_sound_card
                )
            else:
                card = SoundCard(
                    master=self.scroll_frame,
                    sound_id=track.id,
                    title=track.title,
                    source="",  # Será resolvido On-Demand no primeiro Play
                    audio_engine=self.audio_engine,
                    mode_type=track.source_kind.value,
                    duration=track.duration_s or 0.0,
                    original_url=track.origin_url,
                    on_save_to_library=self._save_card_to_permanent_library,
                    on_delete=self._delete_sound_card,
                    on_resolve_source=self._resolve_virtual_track
                )
            self.cards_dict[track.id] = card

        self._reposition_cards()

    def _delete_sound_card(self, sound_id: str) -> None:
        """
        Exclui uma faixa:
        - Se for arquivo físico local: remove o arquivo do disco.
        - Se for faixa virtual: descarta o arquivo temporário, se houver.
        - Em todos os casos, remove a faixa do catálogo e o card da interface.
        """
        card = self.cards_dict.get(sound_id)
        if not card:
            return

        # Para a reprodução imediatamente
        self.audio_engine.stop(sound_id)

        # 1. Exclusão física para arquivos locais
        if card.mode_type == "local":
            try:
                file_path = Path(card.source)
                if file_path.exists():
                    file_path.unlink()
            except OSError as e:
                self._show_status(f"Erro ao excluir arquivo físico: {e}", is_error=True)
                return

        # 2. Remove arquivo temporário de faixas em cache, se houver
        elif card.mode_type == "cache" and card.source and os.path.exists(card.source):
            try:
                os.remove(card.source)
            except OSError:
                pass

        self.library.remove_track(sound_id)

        # 3. Remoção do componente da interface
        card.destroy()
        del self.cards_dict[sound_id]
        self._reposition_cards()

        self._show_status(f"Faixa '{card.title}' removida com sucesso.", is_error=False)

    def _add_card_to_grid(self, sound_id: str, card: SoundCard, auto_play: bool = False) -> None:
        self.cards_dict[sound_id] = card
        self._reposition_cards()
        if auto_play:
            card._toggle_playback()

    def _reposition_cards(self) -> None:
        columns = 3
        for index, (s_id, card) in enumerate(self.cards_dict.items()):
            row = index // columns
            col = index % columns
            card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

    def _on_stop_all(self) -> None:
        self.audio_engine.stop_all()
        for card in self.cards_dict.values():
            card.stop()

    def cleanup_resources(self) -> None:
        """Limpa recursos e descarta os arquivos da pasta temporária."""
        try:
            self.audio_engine.stop_all()
            self.temp_cache_dir.cleanup()
        except Exception as e:
            print(f"[Cleanup Error] {e}")
