# ⚔️ Bardaco's Party — RPG Soundpad

Um Soundpad multi-track para mestres de RPG de mesa, com suporte a reprodução simultânea de múltiplos áudios, streaming direto do YouTube e interface dark otimizada para sessões noturnas.

## ✨ Features

- **Multi-Track Simultâneo:** Toque chuva + música de taverna + efeito de espada ao mesmo tempo, cada um com volume independente.
- **3 Modos de Reprodução:**
  - ☁️ **Streaming Direto** — Reproduz áudio do YouTube via VLC sem baixar nada.
  - ⚡ **Tocar em Cache** — Baixa para pasta temporária, descartada ao fechar o app.
  - 💾 **Salvar na Biblioteca** — Download permanente em `assets/sounds/`.
- **Linha do Tempo Interativa (Seekbar)** com seek preciso via libVLC.
- **Catálogo em SQLite** — Faixas locais, de streaming e de cache ficam em `assets/library.db`, com gravação atômica. O `library.json` antigo é importado sozinho na primeira execução e guardado como `library.json.bak`.
- **Download do YouTube** via `yt-dlp` em threads separadas (nunca congela a UI).
- **Interface Dark Absoluta** com `customtkinter`.

## 🛠️ Stack

| Componente | Tecnologia |
|---|---|
| Interface | `customtkinter` |
| Motor de Áudio | `python-vlc` (primário) + `pygame` (fallback) |
| Downloads | `yt-dlp` + `ffmpeg` |
| Concorrência | `threading` |
| Persistência | SQLite (`sqlite3` da stdlib) |

## 📦 Instalação

```bash
# Dependência de sistema (Linux)
sudo apt install -y vlc ffmpeg

# Instalar dependências Python
pip install -r requirements.txt

# Executar
python main.py

# Testes (stdlib, sem dependência extra)
python -m unittest discover -s tests
```

## 📁 Estrutura

A arquitetura alvo está em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).

```
bardacos_party/
├── main.py                 # Entrypoint e Composition Root (injeta as dependências)
├── requirements.txt
├── domain/
│   ├── models.py           # Entidades puras: Track, Tag, enums
│   └── ports.py            # Contratos: repositório, pasta de sons, resolver, downloader
├── application/
│   └── library.py          # LibraryService: casos de uso do catálogo (write-through)
├── infrastructure/
│   ├── sound_folder.py     # Pasta assets/sounds
│   └── persistence/
│       ├── sqlite.py       # Repositório SQLite com migrações versionadas
│       └── legacy_json.py  # Importação do library.json antigo
├── audio/
│   └── engine.py           # Motor híbrido VLC + Pygame (Facade/Factory)
├── providers/
│   └── youtube.py          # yt-dlp com threads (implementa os ports de mídia)
├── tests/
├── ui/
│   ├── main_window.py      # Janela principal com seletor de modos
│   └── components.py       # SoundCard com seekbar, volume e controles
└── assets/
    └── sounds/             # Biblioteca local de áudios
```

## 📜 Licença

MIT
