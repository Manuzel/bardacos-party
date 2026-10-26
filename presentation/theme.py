"""
Tokens visuais da interface (cores e fontes) num lugar só.
Paleta escura inspirada em catálogos de música para RPG: painéis quase pretos,
chips cinza, verde para "incluir", vermelho riscado para "excluir", cards com borda azul.
"""
import tkinter.font as tkfont

# ---------------- Cores ----------------
BG = "#0d0e12"             # Fundo da janela
PANEL = "#15161b"          # Lateral e área principal
SURFACE = "#1c1d23"        # Barras (cabeçalho, deck)
INPUT = "#2a2a31"
INPUT_BORDER = "#34353d"
DIVIDER = "#2c2d34"

TEXT = "#ececf1"
TEXT_MUTED = "#9b9ca5"
TEXT_DISABLED = "#4d4f58"

CHIP = "#2b2c33"
CHIP_HOVER = "#373842"
CHIP_DISABLED = "#1d1e23"
INCLUDE = "#2e8b3e"
INCLUDE_HOVER = "#37a14a"
EXCLUDE = "#c62f2f"
EXCLUDE_HOVER = "#dc3f3f"

CARD = "#12141d"
CARD_BORDER = "#35507a"
CARD_BORDER_HOVER = "#6189c4"
BADGE = "#0b0b0e"

ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
PLAYING = "#16a34a"
PAUSED = "#d97706"
RESOLVING = "#a16207"
DANGER = "#dc2626"
DANGER_HOVER = "#b91c1c"

# ---------------- Fontes ----------------
# Condensada em negrito, como no catálogo de referência; com fallbacks comuns no Linux.
_FAMILIES = ("Noto Sans Condensed", "Noto Sans", "Inter", "DejaVu Sans")


class Fonts:
    """Fontes do Tk (só podem ser criadas depois da janela raiz)."""

    def __init__(self, root):
        available = set(tkfont.families(root))
        self.family = next((f for f in _FAMILIES if f in available), "TkDefaultFont")

        def font(size: int, weight: str = "normal", **extra) -> tkfont.Font:
            return tkfont.Font(root=root, family=self.family, size=size, weight=weight, **extra)

        self.brand = font(16, "bold")
        self.section = font(11, "bold")
        self.body = font(11)
        self.body_bold = font(11, "bold")
        self.small = font(9)
        self.small_bold = font(9, "bold")
        self.card_title = font(11, "bold")
        self.chip = font(9, "bold")
        self.chip_count = font(8)
        self.chip_strike = font(9, "bold", overstrike=1)
        self.badge = font(8, "bold")
        self.button = font(10, "bold")
        self.glyph = font(28, "bold")

    def ctk(self, font: tkfont.Font):
        """Tupla aceita pelos widgets do customtkinter."""
        actual = font.actual()
        return (actual["family"], actual["size"], actual["weight"])
