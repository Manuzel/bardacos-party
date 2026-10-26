"""
Janela principal: compõe as views e liga as ações aos serviços.
- Lateral de filtros e grade de cards desenham o `LibraryViewModel`.
- O Deck e os botões dos cards desenham os snapshots do orquestrador de áudio.
- Respostas de outras threads (yt-dlp, capas) chegam pelo `UiPump`.
"""
import os
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from application.audio.messages import AudioEvent, MixerSnapshot, Stop, TogglePlay, VoiceFailed, VoiceState
from application.audio.orchestrator import AudioOrchestrator
from application.library import LibraryService
from domain.models import PlayableSource, SourceKind, Track, TrackKind
from domain.ports import CoverProvider, StreamResolver, TrackDownloader
from presentation import theme
from presentation.chips import ChipFlow
from presentation.cover_images import CoverImages
from presentation.deck import Deck
from presentation.grid import ERROR, PAUSED, PLAYING, RESOLVING, LibraryGrid
from presentation.library_vm import KIND_LABELS, LibraryViewModel
from presentation.pump import UiPump
from presentation.sidebar import FilterSidebar

_MODES = ("Streaming Direto", "Tocar em Cache", "Salvar na Biblioteca")


class MainWindow(ctk.CTk):
    def __init__(
        self,
        orchestrator: AudioOrchestrator,
        library: LibraryService,
        stream_resolver: StreamResolver,
        downloader: TrackDownloader,
        covers: CoverProvider | None,
        sounds_dir: str,
    ):
        super().__init__()
        self.orchestrator = orchestrator
        self.library = library
        self.stream_resolver = stream_resolver
        self.downloader = downloader
        self.covers = covers
        self.sounds_dir = Path(sounds_dir).resolve()
        # Pasta temporária para o modo 'Tocar em Cache'
        self.temp_cache_dir = tempfile.TemporaryDirectory(prefix="bardaco_cache_")

        self._sources: dict[str, str] = {}   # Fonte já resolvida nesta sessão (URL direta ou arquivo)
        self._flags: dict[str, str] = {}     # Estados locais do card: conectando / erro
        self._snapshot = MixerSnapshot.empty()
        self._search_job: str | None = None
        self._menu_vars: list[tk.Variable] = []

        self.title("Bardaco's Party — RPG Soundpad")
        self.geometry("1280x820")
        self.minsize(1000, 660)
        ctk.set_appearance_mode("Dark")
        self.configure(fg_color=theme.BG)

        self.fonts = theme.Fonts(self)
        self.vm = LibraryViewModel(library)
        self._pump = UiPump(self, orchestrator, on_snapshot=self._on_mixer_snapshot, on_event=self._on_audio_event)

        self._build_ui()
        self.vm.subscribe(self._on_vm_changed)
        self.reload_library()
        self._pump.start()

    # ================= Montagem =================

    def _build_ui(self) -> None:
        f = self.fonts
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ---------- Cabeçalho: marca + importação ----------
        header = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=0, height=58)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(header, text="⚔ Bardaco's Party", font=f.ctk(f.brand), text_color=theme.TEXT
                     ).grid(row=0, column=0, padx=(16, 18), pady=12)
        self.mode_selector = ctk.CTkOptionMenu(
            header, values=list(_MODES), width=170, height=34, corner_radius=6,
            fg_color=theme.INPUT, button_color=theme.CHIP, button_hover_color=theme.CHIP_HOVER,
            dropdown_fg_color=theme.SURFACE, font=f.ctk(f.body_bold), dropdown_font=f.ctk(f.body),
        )
        self.mode_selector.set(_MODES[0])
        self.mode_selector.grid(row=0, column=1, padx=(0, 6))
        self.query_entry = ctk.CTkEntry(
            header, placeholder_text="Cole o link do YouTube ou busque (ex: taverna medieval)...",
            height=34, corner_radius=6, fg_color=theme.INPUT, border_color=theme.INPUT_BORDER,
            text_color=theme.TEXT, placeholder_text_color=theme.TEXT_MUTED, font=f.ctk(f.body),
        )
        self.query_entry.grid(row=0, column=2, sticky="ew", padx=6)
        self.query_entry.bind("<Return>", lambda e: self._on_execute_action())
        self.exec_button = ctk.CTkButton(
            header, text="▶ Processar", width=120, height=34, corner_radius=6,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, font=f.ctk(f.button),
            command=self._on_execute_action,
        )
        self.exec_button.grid(row=0, column=3, padx=6)
        ctk.CTkButton(header, text="⟳ Recarregar", width=110, height=34, corner_radius=6,
                      fg_color=theme.CHIP, hover_color=theme.CHIP_HOVER, font=f.ctk(f.button),
                      command=self.reload_library).grid(row=0, column=4, padx=(6, 16))

        # ---------- Linha de status / progresso (só aparece quando há algo) ----------
        self.status_bar = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=0, height=26)
        self.status_bar.grid(row=1, column=0, sticky="ew")
        self.status_bar.grid_columnconfigure(1, weight=1)
        self.status_label = ctk.CTkLabel(self.status_bar, text="", anchor="w", font=f.ctk(f.small),
                                         text_color=theme.TEXT_MUTED)
        self.status_label.grid(row=0, column=0, padx=16, sticky="w")
        self.progress_bar = ctk.CTkProgressBar(self.status_bar, height=4, progress_color=theme.PLAYING)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=0, column=1, padx=16, sticky="ew")
        self.progress_bar.grid_remove()
        self.status_bar.grid_remove()

        # ---------- Corpo: lateral | principal ----------
        body = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=0)
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=8)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        self.sidebar = FilterSidebar(body, f, self.vm)
        self.sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 8))

        main = ctk.CTkFrame(body, fg_color=theme.PANEL, corner_radius=8)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(2, weight=1)
        main.grid_columnconfigure(0, weight=1)

        search_row = ctk.CTkFrame(main, fg_color="transparent")
        search_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        search_row.grid_columnconfigure(0, weight=1)
        self.search = ctk.CTkEntry(
            search_row, placeholder_text="Buscar nas faixas filtradas...", height=34, corner_radius=6,
            fg_color=theme.INPUT, border_color=theme.INPUT_BORDER, text_color=theme.TEXT,
            placeholder_text_color=theme.TEXT_MUTED, font=f.ctk(f.body),
        )
        self.search.grid(row=0, column=0, sticky="ew")
        self.search.bind("<KeyRelease>", self._on_search_typed)
        self.count_label = ctk.CTkLabel(search_row, text="", width=40, anchor="e",
                                        text_color=theme.TEXT_MUTED, font=f.ctk(f.body_bold))
        self.count_label.grid(row=0, column=1, padx=(10, 0))

        filters_row = ctk.CTkFrame(main, fg_color="transparent")
        filters_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        filters_row.grid_columnconfigure(0, weight=1)
        self.active_chips = ChipFlow(filters_row, f, on_click=lambda key, right: self.vm.remove(key),
                                     removable=True)
        self.active_chips.grid(row=0, column=0, sticky="ew")
        self.clear_link = ctk.CTkLabel(filters_row, text="limpar", cursor="hand2", text_color=theme.TEXT_MUTED,
                                       font=ctk.CTkFont(family=f.family, size=10, underline=True))
        self.clear_link.bind("<Button-1>", lambda e: self.vm.clear())
        self.sort_button = ctk.CTkButton(filters_row, text="", width=110, height=26, corner_radius=6,
                                         fg_color="transparent", border_width=1, border_color=theme.INPUT_BORDER,
                                         hover_color=theme.CHIP, text_color=theme.TEXT, font=f.ctk(f.small_bold),
                                         command=self.vm.cycle_sort)
        self.sort_button.grid(row=0, column=2, padx=(8, 0), sticky="ne")

        self.images = CoverImages(self.covers, background=theme.CARD)
        self.grid_view = LibraryGrid(main, f, self.images, card_tags=self.vm.card_tags,
                                     on_play=self._play, on_menu=self._open_card_menu,
                                     on_save=self._save_card_to_permanent_library)
        self.grid_view.grid(row=2, column=0, sticky="nsew", padx=(8, 4), pady=(0, 6))

        self.deck = Deck(main, f, send=self.orchestrator.send, track_of=self.library.get, on_toggle=self._play)
        self.deck.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))

    # ================= Biblioteca e filtros =================

    def reload_library(self) -> None:
        """Reconcilia a pasta de sons, redesenha e busca as capas que faltam."""
        self.library.refresh_local_files()
        self.vm.refresh()
        for track in self.library.tracks():
            self._request_cover(track)

    def _on_vm_changed(self) -> None:
        self.sidebar.refresh()
        active = self.vm.active_chips()
        self.active_chips.set_chips(active)
        if active:
            self.clear_link.grid(row=0, column=1, padx=(8, 0), sticky="n")
        else:
            self.clear_link.grid_remove()
        self.sort_button.configure(text=f"⇅ {self.vm.sort_label}")
        self.count_label.configure(text=str(len(self.vm.results)))
        if self.library.tracks():
            empty = "Nenhuma faixa com esses filtros."
        else:
            empty = "A biblioteca está vazia.\nCole um link do YouTube acima ou coloque áudios em assets/sounds."
        self.grid_view.set_tracks(self.vm.results, empty_text=empty, reset_scroll=True)

    def _on_search_typed(self, event=None) -> None:
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(150, lambda: self.vm.set_text(self.search.get()))

    def _request_cover(self, track: Track) -> None:
        if self.covers is None:
            return
        local = self.library.local_file_of(track) if track.local_path else None
        self.covers.request(track, local, on_ready=lambda tid: self._pump.post(self.grid_view.cover_ready, tid))

    # ================= Reprodução =================

    def _play(self, track: Track) -> None:
        """Tocar/pausar/retomar. Faixas remotas sem fonte nesta sessão são resolvidas antes."""
        if self._flags.get(track.id) == RESOLVING:
            return
        voice = self._snapshot.voices.get(track.id)
        if voice is None and track.source_kind != SourceKind.LOCAL and track.id not in self._sources:
            self._resolve_and_play(track)
            return
        source = self._sources.get(track.id) or str(self.library.local_file_of(track))
        self.orchestrator.send(TogglePlay(
            voice_id=track.id, track=track, source=PlayableSource.from_uri(source),
            volume=track.default_volume, loop=track.default_loop,
        ))

    def _resolve_and_play(self, track: Track) -> None:
        self._set_flag(track.id, RESOLVING)

        def ready(source: str) -> None:
            self._sources[track.id] = source
            self._set_flag(track.id, None)
            self._play(track)

        def failed(exc: Exception) -> None:
            self._show_error_flag(track.id)
            self._show_status(f"Erro de rede: {exc}", is_error=True)

        target = track.origin_url
        if track.source_kind == SourceKind.STREAM:
            self.stream_resolver.get_stream_url(
                query_or_url=target,
                on_success=lambda url, meta: self._pump.post(ready, url),
                on_error=lambda exc: self._pump.post(failed, exc),
            )
        else:
            self.downloader.download(
                query_or_url=target, output_dir=self.temp_cache_dir.name,
                on_complete=lambda path, meta: self._pump.post(ready, path),
                on_error=lambda exc: self._pump.post(failed, exc),
            )

    def _set_flag(self, track_id: str, flag: str | None) -> None:
        if flag is None:
            self._flags.pop(track_id, None)
        else:
            self._flags[track_id] = flag
        self._draw_card_states()

    def _show_error_flag(self, track_id: str) -> None:
        self._set_flag(track_id, ERROR)
        self.after(2500, lambda: self._flags.get(track_id) == ERROR and self._set_flag(track_id, None))

    def _on_mixer_snapshot(self, snapshot: MixerSnapshot) -> None:
        self._snapshot = snapshot
        self.deck.show(snapshot)
        self._draw_card_states()

    def _draw_card_states(self) -> None:
        states = {vid: (PLAYING if v.state == VoiceState.PLAYING else PAUSED)
                  for vid, v in self._snapshot.voices.items()}
        states.update(self._flags)
        self.grid_view.set_states(states)

    def _on_audio_event(self, event: AudioEvent) -> None:
        if isinstance(event, VoiceFailed):
            self._sources.pop(event.voice_id, None)  # Um stream que falhou é resolvido de novo
            self._show_error_flag(event.voice_id)
            self._show_status(event.reason, is_error=True)

    # ================= Menu do card =================

    def _open_card_menu(self, track: Track, x: int, y: int) -> None:
        menu_style = dict(tearoff=0, bg=theme.SURFACE, fg=theme.TEXT, activebackground=theme.ACCENT,
                          activeforeground="#ffffff", selectcolor=theme.TEXT, bd=0,
                          font=self.fonts.body, relief="flat")
        menu = tk.Menu(self, **menu_style)
        self._menu_vars = []

        tags_menu = tk.Menu(menu, **menu_style)
        for category, tags in self.vm.tags_by_category():
            sub = tk.Menu(tags_menu, **menu_style)
            for tag_id, name in tags:
                var = tk.BooleanVar(value=tag_id in track.tag_ids)
                self._menu_vars.append(var)
                sub.add_checkbutton(label=name, variable=var,
                                    command=lambda t=tag_id: self._toggle_tag(track.id, t))
            sub.add_separator()
            sub.add_command(label=f"Nova tag em {category}…", command=lambda c=category: self._new_tag(track.id, c))
            tags_menu.add_cascade(label=category, menu=sub)
        menu.add_cascade(label="Tags", menu=tags_menu)

        kind_menu = tk.Menu(menu, **menu_style)
        kind_var = tk.StringVar(value=track.kind.value)
        self._menu_vars.append(kind_var)
        for kind in TrackKind:
            kind_menu.add_radiobutton(label=KIND_LABELS[kind], value=kind.value, variable=kind_var,
                                      command=lambda k=kind: self._set_kind(track.id, k))
        menu.add_cascade(label="Formato", menu=kind_menu)

        menu.add_separator()
        if track.source_kind != SourceKind.LOCAL:
            menu.add_command(label="Salvar na biblioteca", command=lambda: self._save_card_to_permanent_library(track))
        menu.add_command(label="Excluir faixa…", command=lambda: self._delete_track(track))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _toggle_tag(self, track_id: str, tag_id: int) -> None:
        self.library.toggle_track_tag(track_id, tag_id)
        self.vm.refresh()

    def _new_tag(self, track_id: str, category: str) -> None:
        name = ctk.CTkInputDialog(title="Nova tag", text=f"Nome da nova tag em {category}:").get_input()
        if not name or not name.strip():
            return
        try:
            tag = self.library.create_tag(name, category)
        except ValueError as exc:
            self._show_status(str(exc), is_error=True)
            return
        self._toggle_tag(track_id, tag.id)

    def _set_kind(self, track_id: str, kind: TrackKind) -> None:
        self.library.set_track_kind(track_id, kind)
        self.vm.refresh()

    def _delete_track(self, track: Track) -> None:
        if track.source_kind == SourceKind.LOCAL:
            question = f"Excluir '{track.title}'?\n\nO arquivo será apagado de assets/sounds."
        else:
            question = f"Remover '{track.title}' da biblioteca?"
        if not messagebox.askyesno("Excluir faixa", question, parent=self):
            return

        self.orchestrator.send(Stop(track.id))
        if track.source_kind == SourceKind.LOCAL:
            try:
                self.library.local_file_of(track).unlink(missing_ok=True)
            except OSError as exc:
                self._show_status(f"Erro ao excluir o arquivo: {exc}", is_error=True)
                return
        elif track.source_kind == SourceKind.CACHE:
            cached = self._sources.get(track.id)
            if cached and os.path.exists(cached):
                try:
                    os.remove(cached)
                except OSError:
                    pass

        self.library.remove_track(track.id)
        if self.covers is not None:
            self.covers.discard(track.id)
        self._sources.pop(track.id, None)
        self.vm.refresh()
        self._show_status(f"Faixa '{track.title}' removida.")

    # ================= Importação (3 modos) =================

    def _on_execute_action(self) -> None:
        query = self.query_entry.get().strip()
        if not query:
            self._show_status("Digite uma URL ou termo de busca.", is_error=True)
            return

        mode = self.mode_selector.get()
        self.exec_button.configure(state="disabled", text="Processando...")
        self.progress_bar.grid()
        self.progress_bar.set(0.1)

        if mode == "Streaming Direto":
            self._show_status("Extraindo fluxo direto de áudio (Nuvem)...")
            self.stream_resolver.get_stream_url(
                query_or_url=query,
                on_success=lambda url, meta: self._pump.post(self._on_remote_ready, SourceKind.STREAM, url, meta, query),
                on_error=lambda exc: self._pump.post(self._on_operation_error, exc),
            )
        elif mode == "Tocar em Cache":
            self._show_status("Baixando faixa para cache temporário...")
            self.downloader.download(
                query_or_url=query, output_dir=self.temp_cache_dir.name,
                on_progress=lambda pct, msg: self._pump.post(self._on_progress, pct, msg),
                on_complete=lambda path, meta: self._pump.post(self._on_remote_ready, SourceKind.CACHE, path, meta, query),
                on_error=lambda exc: self._pump.post(self._on_operation_error, exc),
            )
        else:
            self._show_status("Baixando para a biblioteca permanente...")
            self.downloader.download(
                query_or_url=query, output_dir=str(self.sounds_dir),
                on_progress=lambda pct, msg: self._pump.post(self._on_progress, pct, msg),
                on_complete=lambda path, meta: self._pump.post(self._on_library_download_ready, path, meta),
                on_error=lambda exc: self._pump.post(self._on_operation_error, exc),
            )

    def _on_progress(self, percent: float, msg: str) -> None:
        self.progress_bar.set(percent / 100.0)
        self._show_status(msg)

    def _on_remote_ready(self, kind: SourceKind, source: str, meta: dict, original_query: str) -> None:
        """Registra a faixa de streaming ou cache, mostra e já começa a tocar."""
        title = meta.get("title") or ("Streaming YouTube" if kind == SourceKind.STREAM else Path(source).stem)
        track = self.library.add_remote_track(
            title=title, origin_url=meta.get("webpage_url") or original_query,
            source_kind=kind, duration_s=float(meta.get("duration") or 0.0),
        )
        self._sources[track.id] = source
        self._show_status(f"Faixa registrada: '{title}'")
        self._finish_action()
        self.vm.refresh()
        self._request_cover(track)
        self._play(track)

    def _on_library_download_ready(self, file_path: str, meta: dict) -> None:
        title = meta.get("title", Path(file_path).stem)
        self._show_status(f"Salvo na biblioteca: '{title}'")
        self._finish_action()
        self._after_library_download(file_path, meta)

    def _save_card_to_permanent_library(self, track: Track) -> None:
        self._show_status(f"Baixando '{track.title}' para assets/sounds em segundo plano...")
        self.downloader.download(
            query_or_url=track.origin_url, output_dir=str(self.sounds_dir),
            on_complete=lambda path, meta: self._pump.post(self._after_library_download, path, meta),
            on_error=lambda exc: self._pump.post(self._on_operation_error, exc),
        )

    def _after_library_download(self, file_path: str, meta: dict) -> None:
        self.library.refresh_local_files()
        url = meta.get("webpage_url") or meta.get("original_url")
        if file_path and url:
            self.library.link_origin(Path(file_path), url)
        self._show_status(f"'{meta.get('title', 'Áudio')}' salvo na biblioteca permanente!")
        self.reload_library()

    def _on_operation_error(self, exc: Exception) -> None:
        self.progress_bar.grid_remove()
        self._show_status(f"Erro: {exc}", is_error=True)
        self.exec_button.configure(state="normal", text="▶ Processar")

    def _finish_action(self) -> None:
        self.progress_bar.set(1.0)
        self.query_entry.delete(0, "end")
        self.exec_button.configure(state="normal", text="▶ Processar")
        self.after(3500, self.progress_bar.grid_remove)

    def _show_status(self, text: str, is_error: bool = False) -> None:
        self.status_bar.grid()
        self.status_label.configure(text=text, text_color=theme.DANGER if is_error else theme.PLAYING)
        if getattr(self, "_status_job", None):
            self.after_cancel(self._status_job)
        self._status_job = self.after(6000, self.status_bar.grid_remove)

    # ================= Encerramento =================

    def cleanup_resources(self) -> None:
        """Para o áudio e descarta os arquivos da pasta temporária."""
        try:
            self._pump.stop()
            self.orchestrator.shutdown()
            self.temp_cache_dir.cleanup()
        except Exception as e:
            print(f"[Cleanup Error] {e}")
