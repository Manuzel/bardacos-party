# ⚔️ Bardaco's Party — RPG Soundpad

Um Soundpad multi-track para mestres de RPG de mesa, com suporte a reprodução simultânea de múltiplos áudios, streaming direto do YouTube e interface dark otimizada para sessões noturnas.

## ✨ Features

- **Multi-Track Simultâneo:** Toque chuva + música de taverna + efeito de espada ao mesmo tempo, cada um com volume independente.
- **3 Modos de Reprodução:**
  - ☁️ **Streaming Direto** — Reproduz áudio do YouTube via VLC sem baixar nada.
  - ⚡ **Tocar em Cache** — Baixa para pasta temporária, descartada ao fechar o app.
  - 💾 **Salvar na Biblioteca** — Download permanente em `assets/sounds/`.
- **Linha do Tempo Interativa (Seekbar)** com seek preciso via libVLC.
- **Persistência JSON** — Links de streaming e cache são salvos em `library.json` e restaurados ao reabrir.
- **Download do YouTube** via `yt-dlp` em threads separadas (nunca congela a UI).
- **Interface Dark Absoluta** com `customtkinter`.

## 🛠️ Stack

| Componente | Tecnologia |
|---|---|
| Interface | `customtkinter` |
| Motor de Áudio | `python-vlc` (primário) + `pygame` (fallback) |
| Downloads | `yt-dlp` + `ffmpeg` |
| Concorrência | `threading` |
| Persistência | JSON (`library_manager.py`) |

## 📦 Instalação

```bash
# Dependência de sistema (Linux)
sudo apt install -y vlc ffmpeg

# Instalar dependências Python
pip install -r requirements.txt

# Executar
python main.py
```

## 📁 Estrutura

```
bardacos_party/
├── main.py                 # Entrypoint
├── requirements.txt
├── library_manager.py      # Persistência JSON
├── audio/
│   └── engine.py           # Motor híbrido VLC + Pygame (Facade/Factory)
├── providers/
│   ├── base.py             # Interface abstrata (Strategy Pattern)
│   └── youtube.py          # Implementação yt-dlp com threads
├── ui/
│   ├── main_window.py      # Janela principal com seletor de modos
│   └── components.py       # SoundCard com seekbar, volume e controles
└── assets/
    └── sounds/             # Biblioteca local de áudios
```

## 📜 Licença

MIT
