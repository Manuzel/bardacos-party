"""Lateral de filtros: busca de tags e um grupo recolhível por categoria."""
import customtkinter as ctk

from presentation import theme
from presentation.chips import ChipFlow
from presentation.library_vm import FacetGroup, LibraryViewModel


class _GroupSection(ctk.CTkFrame):
    def __init__(self, master, fonts: theme.Fonts, vm: LibraryViewModel, title: str):
        super().__init__(master, fg_color="transparent")
        self._vm = vm
        self.collapsed = False
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkFrame(self, height=1, fg_color=theme.DIVIDER).grid(row=0, column=0, columnspan=3, sticky="ew")
        self.title_label = ctk.CTkLabel(self, text=title, font=fonts.ctk(fonts.section),
                                        text_color=theme.TEXT, anchor="w")
        self.title_label.grid(row=1, column=0, sticky="w", pady=(6, 4))
        self.badge = ctk.CTkLabel(self, text="", width=20, height=18, corner_radius=4,
                                  fg_color=theme.INPUT, text_color=theme.TEXT,
                                  font=fonts.ctk(fonts.small_bold))
        self.toggle = ctk.CTkButton(self, text="▾", width=20, height=20, fg_color="transparent",
                                    hover_color=theme.CHIP, text_color=theme.TEXT_MUTED,
                                    command=self._toggle_collapsed)
        self.toggle.grid(row=1, column=2, sticky="e")
        self.chips = ChipFlow(self, fonts, on_click=lambda key, right: vm.toggle(key, exclude=right))
        self.chips.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 8))

    def show(self, group: FacetGroup) -> None:
        if group.active:
            self.badge.configure(text=str(group.active))
            self.badge.grid(row=1, column=1, padx=4)
        else:
            self.badge.grid_remove()
        self.chips.set_chips(list(group.chips))

    def _toggle_collapsed(self) -> None:
        self.collapsed = not self.collapsed
        self.toggle.configure(text="▸" if self.collapsed else "▾")
        if self.collapsed:
            self.chips.grid_remove()
        else:
            self.chips.grid()


class FilterSidebar(ctk.CTkFrame):
    def __init__(self, master, fonts: theme.Fonts, vm: LibraryViewModel, width: int = 270):
        super().__init__(master, width=width, fg_color=theme.PANEL, corner_radius=0)
        self._fonts = fonts
        self._vm = vm
        self._sections: dict[str, _GroupSection] = {}
        self.grid_propagate(False)
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.search = ctk.CTkEntry(
            self, placeholder_text="Filtrar tags...", height=34, corner_radius=6,
            fg_color=theme.INPUT, border_color=theme.INPUT_BORDER, text_color=theme.TEXT,
            placeholder_text_color=theme.TEXT_MUTED, font=fonts.ctk(fonts.body),
        )
        self.search.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        self.search.bind("<KeyRelease>", lambda e: vm.set_tag_filter_text(self.search.get()))

        ctk.CTkLabel(self, text="Clique: incluir  ·  Botão direito: excluir", anchor="w",
                     text_color=theme.TEXT_MUTED, font=fonts.ctk(fonts.small)
                     ).grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))

        self.body = ctk.CTkScrollableFrame(self, fg_color=theme.PANEL, corner_radius=0,
                                           scrollbar_button_color=theme.CHIP,
                                           scrollbar_button_hover_color=theme.CHIP_HOVER)
        self.body.grid(row=2, column=0, sticky="nsew", padx=(6, 0))
        self.body.grid_columnconfigure(0, weight=1)

    def refresh(self) -> None:
        groups = self._vm.groups()
        visible = {g.title for g in groups}
        for title, section in self._sections.items():
            if title not in visible:
                section.grid_remove()
        for row, group in enumerate(groups):
            section = self._sections.get(group.title)
            if section is None:
                section = _GroupSection(self.body, self._fonts, self._vm, group.title)
                self._sections[group.title] = section
            section.grid(row=row, column=0, sticky="ew", padx=(6, 10))
            section.show(group)
