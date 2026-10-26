# ⚔️ Bardaco's Party — RPG Soundpad

Um Soundpad multi-track para mestres de RPG de mesa, com suporte a reprodução simultânea de múltiplos áudios, streaming direto do YouTube e interface dark otimizada para sessões noturnas.

## ✨ Features

- **Multi-Track Simultâneo:** Toque chuva + música de taverna + efeito de espada ao mesmo tempo, cada um com volume independente.
- **Motor Híbrido de Verdade:** efeitos curtos tocam pelo Pygame (pré-carregados em RAM, latência de ~10 ms); músicas longas e streams, pelo VLC. Uma única thread de áudio controla todos os players, então a interface nunca trava o som.
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
| Motor de Áudio | `python-vlc` (streams e faixas longas) + `pygame` (efeitos curtos) |
| Downloads | `yt-dlp` + `ffmpeg` |
| Concorrência | Actor: thread única de áudio com fila de comandos |
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
│   ├── models.py           # Entidades puras: Track, Tag, PlayableSource, enums
│   └── ports.py            # Contratos: repositório, pasta de sons, backends de áudio, mídia
├── application/
│   ├── library.py          # LibraryService: casos de uso do catálogo (write-through)
│   └── audio/
│       ├── messages.py     # Comandos, eventos e snapshot trocados com a UI
│       ├── selector.py     # Regra de qual player toca cada faixa
│       ├── mixer.py        # Núcleo sem threads: dono único das vozes
│       └── orchestrator.py # Thread "audio-ctl" com fila em volta do mixer
├── infrastructure/
│   ├── sound_folder.py     # Pasta assets/sounds
│   ├── backends/
│   │   ├── vlc_backend.py  # Adapter libVLC (uma instância) + sonda de duração
│   │   └── pygame_backend.py # Adapter pygame.mixer (pool de canais, cache LRU)
│   └── persistence/
│       ├── sqlite.py       # Repositório SQLite com migrações versionadas
│       └── legacy_json.py  # Importação do library.json antigo
├── providers/
│   └── youtube.py          # yt-dlp com threads (implementa os ports de mídia)
├── tests/
├── ui/
│   ├── main_window.py      # Janela principal com seletor de modos
│   ├── components.py       # SoundCard: envia comandos e desenha o snapshot
│   └── pump.py             # Único laço de atualização da UI (after 33 ms)
└── assets/
    └── sounds/             # Biblioteca local de áudios
```

## 📜 Licença

MIT
