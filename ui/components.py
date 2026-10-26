from typing import Callable, Optional
import customtkinter as ctk

from application.audio.messages import (
    AudioCommand, Seek, SetLoop, SetVolume, Stop, TogglePlay, VoiceState, VoiceView
)
from domain.models import PlayableSource, SourceKind, Track

# O botão está mostrando outra coisa (conectando, erro): o próximo estado sempre redesenha.
_DIRTY = object()


def format_time(seconds: float) -> str:
    """Formata segundos em MM:SS legível."""
    if seconds <= 0:
        return "00:00"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


class SoundCard(ctk.CTkFrame):
    """
    Card de áudio unificado com suporte a:
    - Indicador de Modo (Nuvem/Streaming, Cache ou Local)
    - Linha do tempo interativa (Trackline/Seekbar) com timestamp (ex: 01:20 / 05:00)
    - Controles de reprodução (Play, Pause, Stop, Loop)
    - Resolução On-Demand (Lazy Loading) de URLs de streaming expiradas
    - Controle de volume independente
    - Botão de download rápido (💾) para salvar áudios na biblioteca permanente
    - Botão de remoção (🗑️) com exclusão física ou descarte do registro

    O card não fala com os players: envia comandos (`send`) e desenha o estado que
    chega do orquestrador em `apply_view`.
    """

    def __init__(
        self,
        master,
        track: Track,
        source: str,
        send: Callable[[AudioCommand], None],
        on_save_to_library: Optional[Callable[[str, str], None]] = None,
        on_delete: Optional[Callable[[str], None]] = None,
        on_resolve_source: Optional[Callable[[str, str, str, Callable[[str], None], Callable[[Exception], None]], None]] = None,
        **kwargs
    ):
        super().__init__(master, corner_radius=12, fg_color="#1a1d26", border_width=1, border_color="#2b3040", **kwargs)

        self.track = track
        self.sound_id = track.id
        self.title = track.title
        self.source = source
        self.send = send
        self.mode_type = track.source_kind.value  # 'stream', 'cache', 'local'
        self.duration = track.duration_s or 0.0
        self.original_url = track.origin_url
        self.on_save_to_library = on_save_to_library
        self.on_delete = on_delete
        self.on_resolve_source = on_resolve_source

        self.current_volume: float = track.default_volume
        self.is_looping: bool = track.default_loop
        self._is_user_seeking: bool = False
        self._is_resolving: bool = False
        self._showing_error: bool = False
        self._view: VoiceView | None = None
        self._rendered_state: object = None  # VoiceState, None (parado) ou _DIRTY

        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        # ---------------- 1. CABEÇALHO (TÍTULO E BADGE DE MODO) ----------------
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.grid(row=0, column=0, padx=14, pady=(12, 2), sticky="ew")
        top_frame.grid_columnconfigure(0, weight=1)

        # Trunca o título para até 28 caracteres no card
        display_title = self.title if len(self.title) <= 28 else f"{self.title[:26]}..."
        self.title_label = ctk.CTkLabel(
            top_frame,
            text=display_title,
            font=ctk.CTkFont(family="Helvetica", size=13, weight="bold"),
            text_color="#f3f4f6",
            anchor="w"
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        # Badge de Modo
        badge_config = {
            "stream": {"text": "☁ Nuvem", "fg": "#0369a1", "text_c": "#e0f2fe"},
            "cache": {"text": "⚡ Cache", "fg": "#b45309", "text_c": "#fef3c7"},
            "local": {"text": "💾 Local", "fg": "#374151", "text_c": "#f3f4f6"}
        }.get(self.mode_type, {"text": "Áudio", "fg": "#374151", "text_c": "#f3f4f6"})

        badge = ctk.CTkLabel(
            top_frame,
            text=badge_config["text"],
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=badge_config["text_c"],
            fg_color=badge_config["fg"],
            corner_radius=6,
            padx=6,
            pady=2
        )
        badge.grid(row=0, column=1, padx=(6, 0), sticky="e")

        # ---------------- 2. LINHA DO TEMPO (TRACKLINE / SEEKBAR) ----------------
        trackline_frame = ctk.CTkFrame(self, fg_color="transparent")
        trackline_frame.grid(row=1, column=0, padx=14, pady=(6, 4), sticky="ew")
        trackline_frame.grid_columnconfigure(0, weight=1)

        self.timeline_slider = ctk.CTkSlider(
            trackline_frame,
            from_=0.0,
            to=max(1.0, self.duration),
            height=14,
            progress_color="#3b82f6",
            button_color="#60a5fa",
            button_hover_color="#93c5fd",
            command=self._on_seek_drag
        )
        self.timeline_slider.set(0.0)
        self.timeline_slider.grid(row=0, column=0, sticky="ew")

        # Eventos para evitar conflito entre o arrasto do usuário e o relógio de atualização
        self.timeline_slider.bind("<Button-1>", self._on_seek_press)
        self.timeline_slider.bind("<ButtonRelease-1>", self._on_seek_release)

        self.time_label = ctk.CTkLabel(
            trackline_frame,
            text=f"00:00 / {format_time(self.duration)}",
            font=ctk.CTkFont(family="Courier", size=10),
            text_color="#9ca3af"
        )
        self.time_label.grid(row=1, column=0, sticky="e", pady=(2, 0))

        # ---------------- 3. BOTÕES DE CONTROLE (PLAY, STOP, LOOP) ----------------
        ctrls_frame = ctk.CTkFrame(self, fg_color="transparent")
        ctrls_frame.grid(row=2, column=0, padx=12, pady=4, sticky="ew")
        ctrls_frame.grid_columnconfigure(0, weight=3)
        ctrls_frame.grid_columnconfigure(1, weight=1)
        ctrls_frame.grid_columnconfigure(2, weight=1)

        self.play_btn = ctk.CTkButton(
            ctrls_frame,
            text="▶ Tocar",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            height=32,
            corner_radius=8,
            command=self._toggle_playback
        )
        self.play_btn.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.stop_btn = ctk.CTkButton(
            ctrls_frame,
            text="⏹",
            width=36,
            height=32,
            corner_radius=8,
            fg_color="#374151",
            hover_color="#4b5563",
            command=self.stop
        )
        self.stop_btn.grid(row=0, column=1, padx=2, sticky="ew")

        self.loop_switch = ctk.CTkSwitch(
            ctrls_frame,
            text="",
            width=38,
            progress_color="#10b981",
            command=self._on_toggle_loop
        )
        if self.is_looping:
            self.loop_switch.select()
        self.loop_switch.grid(row=0, column=2, padx=(4, 0), sticky="e")

        # ---------------- 4. VOLUME, DOWNLOAD E BOTÃO DE LIXEIRA ----------------
        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.grid(row=3, column=0, padx=12, pady=(4, 12), sticky="ew")
        bottom_frame.grid_columnconfigure(1, weight=1)

        vol_icon = ctk.CTkLabel(bottom_frame, text="🔊", font=ctk.CTkFont(size=11))
        vol_icon.grid(row=0, column=0, padx=(0, 4))

        self.vol_slider = ctk.CTkSlider(
            bottom_frame,
            from_=0.0,
            to=1.0,
            height=12,
            button_color="#60a5fa",
            progress_color="#3b82f6",
            command=self._on_volume_change
        )
        self.vol_slider.set(self.current_volume)
        self.vol_slider.grid(row=0, column=1, sticky="ew")

        self.vol_label = ctk.CTkLabel(
            bottom_frame,
            text=f"{int(self.current_volume * 100)}%",
            font=ctk.CTkFont(size=10),
            text_color="#9ca3af",
            width=28
        )
        self.vol_label.grid(row=0, column=2, padx=2)

        # Botão independente de Download (apenas para streaming ou cache)
        if self.mode_type in ("stream", "cache") and self.on_save_to_library:
            self.download_btn = ctk.CTkButton(
                bottom_frame,
                text="💾",
                width=28,
                height=26,
                corner_radius=6,
                fg_color="#059669",
                hover_color="#047857",
                command=self._on_save_button_clicked
            )
            self.download_btn.grid(row=0, column=3, padx=2)

        # Botão de Exclusão (Lixeira 🗑️)
        if self.on_delete:
            self.delete_btn = ctk.CTkButton(
                bottom_frame,
                text="🗑️",
                width=28,
                height=26,
                corner_radius=6,
                fg_color="#dc2626",
                hover_color="#b91c1c",
                command=self._on_delete_clicked
            )
            self.delete_btn.grid(row=0, column=4, padx=(2, 0))

    # --- LÓGICA DE REPRODUÇÃO E RESOLUÇÃO LAZY ---

    def _toggle_playback(self) -> None:
        if self._is_resolving:
            return

        # Caso a fonte ainda não tenha sido resolvida (ex: sessão reiniciada)
        if self._view is None and not self.source and self.on_resolve_source:
            self._resolve_and_play()
            return

        self._send_toggle()

    def _send_toggle(self) -> None:
        # Tocar, pausar ou retomar: o orquestrador decide pelo estado verdadeiro da voz.
        self.send(TogglePlay(
            voice_id=self.sound_id,
            track=self.track,
            source=PlayableSource.from_uri(self.source),
            volume=self.current_volume,
            loop=self.is_looping
        ))

    def _resolve_and_play(self) -> None:
        """Resolve o link do streaming ou baixa para o cache antes de tocar."""
        self._is_resolving = True
        self._rendered_state = _DIRTY
        self.play_btn.configure(text="⏳ Conectando...", fg_color="#d97706")

        def on_resolved(resolved_source: str):
            self._is_resolving = False
            self.source = resolved_source
            self._send_toggle()

        def on_failed(exc: Exception):
            self._is_resolving = False
            self.show_error("Erro de Rede")

        target_url = self.original_url or self.source
        self.on_resolve_source(self.sound_id, target_url, self.mode_type, on_resolved, on_failed)

    def stop(self) -> None:
        """Interrompe a faixa; o card volta ao estado parado quando o snapshot confirmar."""
        self.send(Stop(self.sound_id))

    def _on_toggle_loop(self) -> None:
        self.is_looping = bool(self.loop_switch.get())
        self.send(SetLoop(self.sound_id, self.is_looping))

    def _on_volume_change(self, value: float) -> None:
        self.current_volume = float(value)
        self.send(SetVolume(self.sound_id, self.current_volume))
        self.vol_label.configure(text=f"{int(self.current_volume * 100)}%")

    # --- LÓGICA DA LINHA DO TEMPO (SEEKBAR) ---

    def _on_seek_press(self, event) -> None:
        self._is_user_seeking = True

    def _on_seek_drag(self, value: float) -> None:
        cur_str = format_time(float(value))
        tot_str = format_time(self.duration)
        self.time_label.configure(text=f"{cur_str} / {tot_str}")

    def _on_seek_release(self, event) -> None:
        target_seconds = float(self.timeline_slider.get())
        self.send(Seek(self.sound_id, target_seconds))
        self._is_user_seeking = False

    def _on_save_button_clicked(self) -> None:
        if self.on_save_to_library:
            target_url = self.original_url or self.source
            if hasattr(self, 'download_btn'):
                self.download_btn.configure(state="disabled", fg_color="#374151")
            self.on_save_to_library(self.sound_id, target_url)

    def _on_delete_clicked(self) -> None:
        """Para o áudio e dispara o callback de deleção física ou lógica."""
        self.stop()
        if self.on_delete:
            self.on_delete(self.sound_id)

    # --- SINCRONIZAÇÃO COM O ORQUESTRADOR ---

    def apply_view(self, view: Optional[VoiceView]) -> None:
        """Desenha o estado da voz vindo do snapshot (None = parada)."""
        self._view = view

        if view is None:
            if self._rendered_state is not None:
                self.timeline_slider.set(0.0)
                self.time_label.configure(text=f"00:00 / {format_time(self.duration)}")
            self._render_state(None)
            return

        if view.duration_s and abs(view.duration_s - self.duration) > 0.5:
            self.duration = view.duration_s
            self.timeline_slider.configure(to=max(1.0, self.duration))

        self._render_state(view.state)
        if not self._is_user_seeking:
            self.timeline_slider.set(view.position_s)
            self.time_label.configure(text=f"{format_time(view.position_s)} / {format_time(self.duration)}")

    def show_error(self, text: str = "⚠ Erro") -> None:
        """Mostra a falha no botão por alguns segundos. Um stream que falhou é resolvido de novo no próximo play."""
        if self.mode_type == SourceKind.STREAM.value:
            self.source = ""
        self._showing_error = True
        self._rendered_state = _DIRTY
        self.play_btn.configure(text=text, fg_color="#dc2626", hover_color="#b91c1c")

        def clear() -> None:
            self._showing_error = False
            self.apply_view(self._view)

        self.after(2500, clear)

    def _render_state(self, state: Optional[VoiceState]) -> None:
        # Só reconfigura os widgets quando o estado muda: o snapshot chega 10x por segundo.
        if self._showing_error or self._is_resolving or state is self._rendered_state:
            return
        self._rendered_state = state

        if state == VoiceState.PLAYING:
            self._set_playing_ui()
        elif state == VoiceState.PAUSED:
            self._set_paused_ui()
        else:
            self._set_stopped_ui()

        seek_state = "normal" if self._view is None or self._view.supports_seek else "disabled"
        self.timeline_slider.configure(state=seek_state)

    def _set_playing_ui(self) -> None:
        self.play_btn.configure(text="⏸ Pausar", fg_color="#059669", hover_color="#047857")
        self.configure(border_color="#10b981", border_width=2)

    def _set_paused_ui(self) -> None:
        self.play_btn.configure(text="▶ Retomar", fg_color="#d97706", hover_color="#b45309")
        self.configure(border_color="#f59e0b", border_width=1)

    def _set_stopped_ui(self) -> None:
        self.play_btn.configure(text="▶ Tocar", fg_color="#2563eb", hover_color="#1d4ed8")
        self.configure(border_color="#2b3040", border_width=1)
