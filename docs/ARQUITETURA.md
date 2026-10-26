# Bardaco's Party — Documento de Design Arquitetural (HLD)

> Status: **aprovado** · Implementados: módulo 1 (fundação) e módulo 2 (orquestrador) · Base avaliada: commit `6ccb7b6` · Python 3.14 (GIL padrão)

Escopo: (1) Tags e indexação, (2) Cenas e orquestração, (3) Fade In/Out assíncrono.
Este documento não contém código final; os trechos são contratos e pseudocódigo.

---

## 0. Diagnóstico do estado atual

A base funciona e já usa Strategy (provider) e Facade/Factory (engine), mas várias premissas
do roadmap não se sustentam no código de hoje:

| # | Onde | Problema | Impacto no roadmap |
|---|------|----------|--------------------|
| 1 | `audio/engine.py:310` | `if is_url or (self.has_vlc and ...)` manda **tudo** para o VLC quando ele existe. O Pygame (32 canais em RAM) só roda como fallback. | A "engine híbrida" na prática não existe; SFX curtos pagam a latência do VLC. |
| 2 | `audio/engine.py:89` | Um `vlc.Instance` **novo por faixa** (cada um carrega todos os plugins do libVLC). | Dezenas de vozes = dezenas de instâncias pesadas. |
| 3 | `audio/engine.py:201` | `find_channel(force=True)` rouba silenciosamente um canal em uso quando os 32 acabam. | Cenas com muitas camadas cortam sons sem aviso. |
| 4 | `audio/engine.py:186` | `pygame.mixer.Sound` decodifica o arquivo inteiro para PCM em RAM. Uma faixa de 1 h a 44,1 kHz/16 bit/estéreo ≈ **635 MB**. | Música longa nunca pode ir para o Pygame. |
| 5 | `audio/engine.py:157` | O loop do VLC é reiniciado dentro de `is_playing()` — uma consulta com efeito colateral, que só funciona porque a UI fica perguntando. | Se a UI travar, o loop não reinicia. |
| 6 | `library_manager.py:34` | JSON corrompido → `_tracks = {}` + `save()`: **sobrescreve a biblioteca do usuário com vazio**. A gravação (linha 47) não é atômica. | Perda de dados; inaceitável quando houver tags e cenas. |
| 7 | `ui/main_window.py:384-402` | Arquivos locais não existem no catálogo; o id é `local_{nome_do_arquivo}`. | Impossível marcar tags em arquivos locais; renomear perde identidade. |
| 8 | `ui/main_window.py:28` | A janela depende do `YouTubeProvider` concreto e usa `get_stream_url`/`download`, que não estão em `BaseAudioProvider`. | Violação de DIP/ISP; o "Strategy" não é substituível. |
| 9 | `ui/main_window.py:208` | Threads do yt-dlp chamam `self.after(...)`. O Tkinter não garante segurança entre threads. | Crashes intermitentes difíceis de reproduzir. |
| 10 | `ui/components.py:354` | Cada `SoundCard` tem seu próprio `after(300)`, mesmo parado. | Centenas de faixas = centenas de timers no event loop. |
| 11 | `ui/main_window.py:377` | "Recarregar" destrói e recria todos os cards (o comentário diz que preserva os que tocam). | Recriar widgets CTk é o gargalo real de filtragem (ver §2.4). |
| 12 | `audio/engine.py:198+` | A posição do Pygame é medida com `time.time()` (relógio de parede, sujeito a ajustes do NTP). | Deriva na trackline; usar `time.monotonic()`. |

Conclusão: antes de tags/cenas/fades, é preciso uma fundação — **um dono único do estado de
áudio** e **um catálogo persistente de verdade**. Os três pontos do roadmap dependem disso.

---

## 1. Padrões e camadas (resposta à pergunta 1)

### 1.1 Camadas (Clean Architecture)

Os pacotes ficam na raiz do repositório (sem um pacote `bardacos/` envolvendo), seguindo o
estilo de import que o projeto já usa. O antigo `audio/` virou `infrastructure/backends/` no
módulo 2, e `ui/` vira `presentation/` no módulo 4.

Ajustes feitos no módulo 2, em relação ao plano:
- O orquestrador se divide em `Mixer` (lógica pura, testada sem threads) e `AudioOrchestrator`
  (só a thread e a fila em volta).
- A resolução de streams continua disparada pela UI (pelo `UiPump`, sem `after()` vindo de
  outras threads). Ela vai para dentro do orquestrador, com as épocas, no módulo 5 (cenas).
- Sem vagas em nenhum backend, a voz nova é recusada com aviso; nada é cortado em silêncio.
  O roubo de voz por prioridade fica para as cenas, que é onde a prioridade passa a existir.
- Arquivos locais têm a duração lida pelo cabeçalho (via libVLC, poucos ms por arquivo) ao
  entrar no catálogo; é ela que manda efeitos curtos para o Pygame.

```
(raiz)
├── domain/          # Entidades e value objects puros (sem pygame, vlc, tk, sqlite)
│   ├── models.py    #   Track, Tag, Scene, SceneLayer, TrackKind, Transition
│   ├── curves.py    #   Curvas de fade (funções puras)
│   └── ports.py     #   Protocols: Repository, PlaybackBackend, VoiceHandle, SourceResolver, Clock
├── application/     # Casos de uso; depende só de domain
│   ├── library.py   #   LibraryService (CRUD + tags) e LibraryIndex (busca em memória)
│   ├── scenes.py    #   SceneService (CRUD) e o cálculo de diff entre cenas
│   └── audio/       #   AudioOrchestrator (actor), Voice, Envelope, comandos e eventos
├── infrastructure/  # Adapters concretos dos ports
│   ├── backends/    #   VlcBackend, PygameBackend, BackendSelector
│   ├── resolvers/   #   YtDlpResolver (pool de threads)
│   └── persistence/ #   SqliteRepository, JsonRepository (import/export/testes)
├── presentation/    # customtkinter: Views + ViewModels + UiPump
└── main.py          # Composition Root: monta e injeta tudo
```

Regra de dependência: setas só apontam para dentro. `presentation` e `infrastructure`
conhecem `application`/`domain`; o contrário nunca.

### 1.2 Padrões, cada um com um motivo

| Padrão | Onde | Por quê |
|--------|------|---------|
| **Actor** (Command + Mediator) | `AudioOrchestrator` | Uma única thread é dona de todos os players. Sem compartilhamento, sem locks, sem race condition por construção. É o núcleo do design (§3). |
| **Command** | `PlayScene`, `SetGain`, `Seek`, `StopAll(fade_ms)`… | A UI não chama o engine: ela enfileira intenções imutáveis. Permite coalescer, descartar comandos obsoletos e testar sem áudio. |
| **MVVM leve** | `presentation/` | A View (CTk) só desenha; o ViewModel guarda o estado de tela e traduz cliques em Commands. Os ViewModels são testáveis sem Tk. |
| **Observer com marshaling** | Orquestrador → UI | Eventos discretos (`VoiceStarted`, `VoiceEnded`, `ResolveFailed`) num `queue.SimpleQueue` + **snapshot de último valor** (posição/ganho). A UI consome tudo na própria thread. |
| **Strategy + Adapter** | `PlaybackBackend` (VLC, Pygame) | Normaliza APIs diferentes (volume int 0–100 no VLC, float→int 0–128 no Pygame/SDL) atrás de um contrato `VoiceHandle`. |
| **Chain of Responsibility** | `BackendSelector` | A política "qual player?" vira regras ordenadas e configuráveis em vez de um `if` dentro do engine. |
| **Flyweight / Object Pool** | Um `vlc.Instance` por processo; pool explícito dos 32 `pygame.mixer.Channel`; cache LRU de `pygame.mixer.Sound` por bytes | Corrige os itens 2, 3 e 4 do diagnóstico. |
| **State** | Ciclo de vida da `Voice` | `IDLE → RESOLVING → READY → PLAYING ⇄ PAUSED → STOPPING → RELEASED`. Um comando inválido para o estado é ignorado de forma explícita. |
| **Repository + Unit of Work** | `persistence/` | A aplicação não sabe se o disco é SQLite ou JSON. Uma operação (ex.: salvar cena com 6 camadas) é uma transação. |
| **Dependency Injection** (manual) | `main.py` | Composition root simples, sem framework. `Clock` e backends injetáveis → testes determinísticos. |

### 1.3 Visão de threads e fluxo

```
┌────────────────────── Thread UI (Tk mainloop) ──────────────────────┐
│  Views (CTk) ◄── ViewModels ◄── UiPump (after 33 ms: drena eventos, │
│       │                          lê snapshot, atualiza só o que mudou)│
└───────┼──────────────────────────────────────────────▲──────────────┘
        │ Command (inbox.put)                          │ eventos (fila) + snapshot (último valor)
┌───────▼──────────────────────────────────────────────┴──────────────┐
│  AudioOrchestrator — thread "audio-ctl" (ÚNICA que toca nos players)  │
│  inbox → reducer → Voices{estado, estágios de ganho, envelopes} → tick │
└───┬──────────────┬───────────────────────────────────▲──────────────┘
    │ VoiceHandle  │ submit(track, voice_id, epoch)     │ SourceResolved(voice_id, epoch, src)
┌───▼─────┐ ┌──────▼──┐            ┌────────────────────┴──────┐
│ Pygame  │ │  VLC    │            │ Resolver pool (yt-dlp, 3) │
│ (SDL)   │ │(libVLC) │──eventos──►│ inbox.put — nunca chama   │
└─────────┘ └─────────┘  EndReached│ libVLC dentro do callback │
                                   └───────────────────────────┘
   SQLite: uma conexão só, na thread da UI (gravações de catálogo levam < 1 ms).
   Leituras vêm da memória. Se surgir escrita pesada, ela passa para uma thread
   "db-writer" atrás do mesmo port, sem mudar quem usa.
```

Por que isso é seguro com o GIL: a decodificação e a mixagem rodam em threads nativas
(SDL e libVLC) que soltam o GIL. A thread `audio-ctl` passa quase todo o tempo bloqueada na
fila e só faz chamadas baratas (`set_volume`, `get_time`). Um travamento de 300 ms na UI
(ex.: montar widgets) não afeta o áudio nem os fades.

### 1.4 Contratos principais (esboço)

```python
class VoiceHandle(Protocol):
    supports_seek: bool
    def start(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...
    def release(self) -> None: ...
    def seek(self, seconds: float) -> None: ...
    def apply_gain(self, gain: float) -> None: ...   # 0.0–1.0 linear; o adapter quantiza
    def position(self) -> float: ...
    def duration(self) -> float | None: ...

class PlaybackBackend(Protocol):
    name: str
    def accepts(self, track: Track, source: PlayableSource) -> bool: ...
    def create_voice(self, source: PlayableSource, loop: bool) -> VoiceHandle: ...

class SourceResolver(Protocol):            # bloqueante; sempre roda no pool
    def resolve(self, track: Track) -> PlayableSource: ...

class LibraryRepository(Protocol):
    def load_all(self) -> LibrarySnapshot: ...   # tracks, tags, ligações, cenas
    def unit_of_work(self) -> ContextManager[UnitOfWork]: ...

class Clock(Protocol):
    def now(self) -> float: ...                   # monotonic
```

O `BaseAudioProvider` atual se divide em dois ports segregados: `SourceResolver` (URL → fonte
tocável, com TTL) e `Downloader` (salvar em disco). O `YouTubeProvider` implementa os dois.

### 1.5 Política de backend (`BackendSelector`)

Regras avaliadas em ordem; a primeira que aceitar vence:

1. Fonte remota (stream) → **VLC**.
2. Local e (`kind == SFX` ou duração ≤ 30 s e PCM estimado ≤ 16 MB) → **Pygame** (pré-decodificado, latência ~10 ms, várias vozes podem compartilhar o mesmo `Sound`).
3. Local e longo (música/ambiente) → **VLC** (lê do disco, tem seek).
4. Sem VLC: só a regra 2 vale; faixa longa local mostra erro claro em vez de carregar 600 MB.

`Track.backend_hint` permite forçar por faixa. Limites configuráveis: 32 canais Pygame e um
teto de vozes VLC (sugestão: 24), com **roubo de voz por prioridade** (SFX one-shot <
ambiente < música) em vez do `force=True` cego.

---

## 2. Tags e indexação (resposta à pergunta 2, parte A)

### 2.1 Uma observação de escala

"Centenas de faixas" é pouco para a busca: até um laço linear sobre 1.000 itens leva menos de
1 ms em Python. **O gargalo real de "filtrar instantaneamente" é a UI**: destruir e recriar
centenas de cards CTk (≈12 widgets desenhados em canvas cada) custa centenas de milissegundos.
Por isso o design ataca os dois lados: índice em memória (barato) e UI virtualizada (§2.4).

### 2.2 Persistência: SQLite já, JSON como formato de troca

Recomendo **migrar para SQLite agora**, em vez de esperar. Ele vem com o Python (sem
dependência nova), grava de forma atômica e transacional (resolve o item 6), suporta
relações N:N (tags, camadas de cena) e migrações por `PRAGMA user_version`. O `JsonRepository`
continua existindo atrás do mesmo port, para import/export e testes. Na primeira execução,
o `library.json` v1 é importado e guardado como `library.json.bak`.

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE tracks (
    id             TEXT PRIMARY KEY,                 -- uuid4 estável (não deriva do nome do arquivo)
    title          TEXT NOT NULL,
    title_norm     TEXT NOT NULL,                    -- casefold + sem acento, para busca
    kind           TEXT NOT NULL CHECK (kind IN ('sfx','ambience','music')),
    source_kind    TEXT NOT NULL CHECK (source_kind IN ('local','stream','cache')),
    local_path     TEXT UNIQUE,                      -- relativo a assets/sounds; NULL p/ remotos
    origin_url     TEXT,                             -- URL do YouTube; NULL p/ locais
    duration_s     REAL,
    default_volume REAL NOT NULL DEFAULT 0.8 CHECK (default_volume BETWEEN 0 AND 1),
    default_loop   INTEGER NOT NULL DEFAULT 0,
    backend_hint   TEXT CHECK (backend_hint IN ('vlc','pygame')),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    CHECK (local_path IS NOT NULL OR origin_url IS NOT NULL)
);

CREATE TABLE tags (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE,    -- "Taverna", "Batalha", "Tensão"
    color    TEXT,
    position INTEGER NOT NULL DEFAULT 0              -- ordem dos chips na UI
);

CREATE TABLE track_tags (
    track_id TEXT    NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (track_id, tag_id)
) WITHOUT ROWID;
CREATE INDEX ix_track_tags_tag ON track_tags(tag_id, track_id);
```

Arquivos locais passam a ser registrados por um `LibraryScanner`, que reconcilia
`assets/sounds` com a tabela: arquivo novo vira linha nova; arquivo sumido vira faixa
"órfã", sinalizada em vez de apagada. Assim as tags valem para tudo.

### 2.3 Índice em memória (`LibraryIndex`)

Carregado uma vez na inicialização (centenas de linhas levam milissegundos) e mantido por
**write-through**: o `LibraryService` grava no repositório e, se a transação confirmar,
atualiza o índice na mesma operação. O disco é a fonte da verdade; a memória é o índice.

```python
class LibraryIndex:
    tracks:     dict[TrackId, TrackView]          # O(1)
    by_tag:     dict[TagId, set[TrackId]]         # índice invertido
    by_kind:    dict[TrackKind, set[TrackId]]
    order:      list[TrackId]                     # pré-ordenado por title_norm

    def query(self, all_tags=(), any_tags=(), kind=None, text="") -> list[TrackId]:
        # 1. AND: interseção começando pelo menor conjunto; sai cedo se esvaziar
        # 2. OR: união dos conjuntos de any_tags, intersectada com o resultado
        # 3. texto: substring em title_norm só sobre os candidatos restantes
        # 4. percorre `order` filtrando pelo conjunto → mantém a ordem sem re-sort
```

Custos: lookup de tag O(1); AND de k tags O(k · min|Sᵢ|); texto O(|candidatos| · L);
projeção ordenada O(n). Para milhares de faixas, tudo fica abaixo de 1 ms. Se um dia passar
de dezenas de milhares, troca-se `set` por bitsets (`int` do Python, AND = `&`) sem mexer na API.

Em SQL, a mesma consulta (para relatórios ou um futuro modo sem índice) é
`... WHERE tag_id IN (?,?) GROUP BY track_id HAVING COUNT(*) = 2`, servida pelo
`ix_track_tags_tag` em O(log n).

### 2.4 UI da biblioteca

- **Separar biblioteca de deck**: a biblioteca vira uma lista/grade de itens leves (título,
  tags, botão ▶). O card completo, com trackline e volume, só existe no **Deck** para as vozes
  ativas. Hoje cada faixa tem um card completo, mesmo parada.
- **Virtualização**: um pool fixo de widgets de linha (visíveis + margem) é reaproveitado com
  `bind(track_view)` ao rolar ou filtrar. Nunca se destrói ou cria widgets durante um filtro.
- **Debounce** de 150 ms no campo de texto (`after_cancel`/`after`); chips de tag aplicam na hora.

---

## 3. Cenas e orquestração (pergunta 2, parte B, e base da pergunta 3)

### 3.1 Schema das cenas

```sql
CREATE TABLE scenes (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT,
    fade_in_ms   INTEGER NOT NULL DEFAULT 2000,
    fade_out_ms  INTEGER NOT NULL DEFAULT 2000,
    curve        TEXT NOT NULL DEFAULT 'equal_power',
    position     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE scene_layers (
    scene_id       TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    track_id       TEXT NOT NULL REFERENCES tracks(id) ON DELETE RESTRICT,
    volume         REAL NOT NULL CHECK (volume BETWEEN 0 AND 1),
    loop           INTEGER NOT NULL DEFAULT 1,
    start_at_s     REAL NOT NULL DEFAULT 0,      -- offset inicial (se o backend tiver seek)
    delay_ms       INTEGER NOT NULL DEFAULT 0,   -- entrada escalonada ("gritos" 4 s depois)
    fade_in_ms     INTEGER,                      -- NULL = herda da cena
    position       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scene_id, track_id)
) WITHOUT ROWID;

CREATE TABLE scene_tags (                        -- cenas também filtráveis por tag
    scene_id TEXT    NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (scene_id, tag_id)
) WITHOUT ROWID;
```

`ON DELETE RESTRICT` em `track_id`: apagar uma faixa usada em cena exige confirmação na UI
(que lista as cenas afetadas), em vez de quebrar a cena em silêncio.

### 3.2 O orquestrador como Actor

Uma thread, uma fila de entrada, um dono. Nada fora da `audio-ctl` chama VLC ou Pygame.

```python
def run(self):
    while self._running:
        timeout = self._time_to_next_tick()           # None (bloqueia) se nada toca
        try:
            self._handle(self._inbox.get(timeout=timeout))
            while True:                               # drena o que acumulou e coalesce
                self._handle(self._inbox.get_nowait())
        except queue.Empty:
            pass
        now = self._clock.now()
        if self._tick_due(now):
            self._tick(now)                           # avança envelopes, aplica ganhos, publica snapshot
```

Ritmo de tick adaptativo: **50 Hz** com algum fade ativo, **10 Hz** com vozes tocando (só
para atualizar posição no snapshot) e **zero** (bloqueio total) com tudo parado. Sem uso de CPU ocioso.

Coalescência: arrastar o slider de volume gera dezenas de `SetGain` por segundo; ao drenar,
só o último por voz é aplicado.

### 3.3 Races reais e como cada uma é eliminada

| Cenário | Solução |
|---------|---------|
| Clico na Cena A; o stream do YouTube leva 4 s para resolver; nesse meio tempo clico na Cena B. Quando A resolve, ela **não** pode começar a tocar por cima de B. | **Época (epoch)**: cada `PlayScene` incrementa `scene_epoch`; cada voz guarda a época em que nasceu. `SourceResolved(voice_id, epoch, src)` é descartado (e o recurso liberado) se a voz não existe mais, se a época mudou ou se o estado não é `RESOLVING`. Cancelamento sem lock. |
| Callback de fim de mídia do VLC chega enquanto a voz está sendo parada. | O callback do libVLC só faz `inbox.put(VoiceEnded(...))` (chamar libVLC de dentro do callback causa deadlock). O orquestrador processa em ordem e ignora se o estado já é `STOPPING`/`RELEASED`. |
| Duplo clique na mesma cena. | `PlayScene` é idempotente: se a cena alvo já está ativa e sem transição pendente, é no-op. |
| Usuário mexe no volume no meio de um fade. | Estágios de ganho separados (§4.1): o slider mexe no estágio `layer`, o fade mexe no estágio `fade`. Nunca brigam pelo mesmo número. |
| Stop de uma voz cujo fade-in ainda roda. | O novo envelope parte do ganho **atual** calculado (§4.3); o anterior é descartado. Sem salto, sem estado duplicado. |

### 3.4 Troca de cena por diff

Trocar de A para B não é "parar tudo e tocar tudo". O orquestrador calcula três conjuntos
pela chave `track_id`:

- **Só em A** → fade-out (`fade_out_ms`, curva `equal_power`) e depois `stop + release`.
- **Só em B** → cria a voz, resolve a fonte, fade-in a partir de 0 após `delay_ms`.
- **Em A e B** → **continua tocando**, sem reiniciar; só faz um glide do estágio `layer`
  de `volume_A` para `volume_B`. A chuva que existe nas duas cenas não "pisca".

Custo O(|A| + |B|), operações de conjunto.

### 3.5 Sincronização de início — o que é possível prometer

VLC e SDL usam streams de saída e relógios diferentes; **sincronia em nível de amostra entre
backends não existe**, e a latência de partida varia muito (Pygame ~10 ms, VLC local ~100–300 ms,
stream 1–5 s entre resolver e bufferizar). Para RPG isso não é problema, desde que a política
seja explícita:

1. **Prepare**: ao ativar a cena, todas as vozes novas nascem com ganho efetivo 0; locais
   ficam `READY` na hora, streams entram em `RESOLVING`.
2. **Commit**: no instante T₀, todas as `READY` começam a tocar e seus envelopes de fade-in
   partem juntos (mesmo `t0`).
3. **Retardatárias**: uma voz que fica pronta depois de T₀ entra com seu próprio fade-in na
   hora em que fica pronta (um stream que chega 3 s depois aparece suavemente, sem "estalo").

**Pré-resolução** para esconder a latência do YouTube: URLs resolvidas ficam em cache com TTL
(as URLs do googlevideo expiram em ~6 h; usar o parâmetro `expire` quando presente, senão 4 h).
Cenas fixadas são pré-resolvidas na inicialização, e passar o mouse sobre uma cena dispara a
pré-resolução das camadas dela. O pool do yt-dlp é um `ThreadPoolExecutor(max_workers=3)`.

---

## 4. Fade In/Out concorrente (resposta à pergunta 3)

### 4.1 Estágios de ganho (gain staging)

O ganho efetivo de cada voz é um produto de estágios independentes, todos em [0, 1]:

```
G = master × bus(cena) × layer × fade × (0 se mute)
```

- `master`: volume geral do app.
- `bus`: volume da cena (permite um "abaixar a cena inteira" em um slider).
- `layer`: volume da camada / slider do card.
- `fade`: o envelope de transição (fade in/out, crossfade, pause/resume suave).

Cada estágio é um `AutomatableParam(value, envelope | None)`. Qualquer estágio pode ser
automatizado (o glide de §3.4 automatiza `layer`; fades automatizam `fade`). Mexer
manualmente num estágio cancela só a automação dele.

### 4.2 Envelope como função do tempo (sem passos acumulados)

Um fade **não** é "a cada tick, some 0,02". Isso acumula erro e depende do tick chegar na
hora. O envelope é uma função pura do relógio monotônico:

```
u(t) = clamp((t − t₀) / T, 0, 1)
x(t) = x₀ + (x₁ − x₀) · u(t)          # posição no domínio da curva
g(t) = curva.f(x(t))                    # ganho linear aplicado
```

Se um tick atrasar 80 ms, o próximo calcula o valor certo para *aquele* instante: o fade termina
exatamente em `t₀ + T`, sem deriva. Isso é o que torna o resultado independente de jitter.

### 4.3 Curvas e interrupção contínua

Cada curva é um par monotônico e inversível em [0, 1]:

| Curva | f(x) | f⁻¹(g) | Uso |
|-------|------|--------|-----|
| `cubic` | x³ | g^(1/3) | fade in/out de uma voz, pause/resume. Aproxima a percepção em dB (x = 0,1 → −60 dB; x = 0,5 → −18 dB). |
| `equal_power` | sin(πx/2) | (2/π)·asin(g) | crossfade entre cenas. Com x subindo numa voz e descendo na outra, os ganhos são sin e cos e **sin² + cos² = 1**: potência constante, sem o "buraco" de −6 dB no meio de um crossfade linear. |
| `linear` | x | g | glides de volume de camada. |

**Interrupção sem clique**: ao iniciar um envelope novo sobre uma voz que já está num fade, o
ponto de partida é `x₀ = curva_nova.f⁻¹(g_atual)`. O ganho continua exatamente de onde estava
(continuidade C⁰), mesmo trocando de curva no meio. Um envelope carrega uma ação de conclusão:
`NONE | PAUSE | STOP | RELEASE`.

```python
@dataclass(slots=True)
class Envelope:
    curve: Curve
    x0: float; x1: float
    t0: float; duration: float
    on_done: DoneAction

    def sample(self, now: float) -> tuple[float, bool]:
        u = 1.0 if self.duration <= 0 else min(1.0, max(0.0, (now - self.t0) / self.duration))
        return self.curve.f(self.x0 + (self.x1 - self.x0) * u), u >= 1.0
```

### 4.4 O tick: N vozes mistas, uma thread

```python
def _tick(self, now):
    for voice in self._voices.values():
        g = voice.effective_gain(now)        # produto dos estágios, cada um amostrado em `now`
        voice.handle.apply_gain(g)           # adapter quantiza e só chama o backend se mudou
        for done in voice.finished_envelopes():
            self._apply_done_action(voice, done)
    self._publish_snapshot(now)              # no máximo 10 Hz
```

Não há concorrência a resolver: com 30 vozes a 50 Hz são ~1.500 chamadas baratas por segundo,
todas na mesma thread, em ordem. "Concorrente" aqui quer dizer *simultâneo no tempo*, não
*paralelo em threads*. Essa é a decisão central: **fades paralelos em threads paralelas seriam
a fonte das race conditions**, não a solução.

Por que não usar `after()` do Tkinter para os fades: os timers do Tk atrasam junto com a UI.
Um filtro que monte widgets por 200 ms congelaria todos os fades nesse intervalo (degrau
audível). Por que não `asyncio`: as chamadas ao libVLC são bloqueantes e curtas; uma thread com
fila é mais simples, determinística e fácil de testar. Por que não uma thread por fade: duas
threads podem mexer na mesma voz, e o número de threads cresce sem controle.

### 4.5 Detalhes de cada backend

- **Quantização**: o VLC aceita volume inteiro 0–100 (`audio_set_volume`); o Pygame converte o
  float para inteiro 0–128 do SDL_mixer. O adapter quantiza (`round(100·g)` / `round(128·g)`) e
  faz **dirty-check**: só chama o backend se o inteiro mudou. Isso corta a maioria das chamadas
  num fade lento. Abaixo do degrau 1 vira 0 (silêncio de fato).
- **VLC ignora volume antes de abrir a saída de áudio** (comportamento conhecido com
  PulseAudio/PipeWire): toda voz VLC nasce em 0 e o orquestrador **reaplica** o ganho ao receber
  `MediaPlayerPlaying` (via fila). Sem isso, o primeiro instante do stream pode sair no volume
  cheio antes do fade-in.
- **Loop no VLC**: opção de mídia `:input-repeat=65535` para arquivos locais; para streams, o
  `MediaPlayerEndReached` vai para a fila e o orquestrador reinicia (reaplicando a mídia).
  Remove o efeito colateral de `is_playing()` (item 5).
- **Canal no Pygame**: só `Channel.set_volume` é usado (nunca `Sound.set_volume`, que
  multiplicaria) e o `Sound` pode ser compartilhado entre vozes.
- **Rede no VLC**: `:network-caching=1500` por mídia de stream (configurável).

### 4.6 Retorno para a UI

- **Snapshot de último valor**: `MixerSnapshot(seq, active_scene, voices: tuple[VoiceView, ...])`,
  imutável (`frozen=True, slots=True`), publicado por atribuição de referência. A UI lê o mais
  recente; nunca há fila crescendo com posições velhas.
- **Eventos discretos** numa `SimpleQueue` (`VoiceStarted`, `VoiceEnded`, `ResolveFailed`,
  `SceneActivated`): não podem se perder, então vão em fila.
- **Um único `UiPump`** com `after(33)` substitui os N timers dos cards: drena até 200 eventos
  por quadro, compara `seq` do snapshot e atualiza só os widgets cujos valores mudaram.
- Progresso do yt-dlp e resultados de download também chegam por essa fila (corrige o item 9).

---

## 5. Testabilidade

- `Clock` falso + `FakeBackend` (grava cada `apply_gain`) permitem testar fades, crossfades,
  diffs de cena e a regra de época **sem placa de som e sem Tk**, de forma determinística.
- Propriedades a verificar: o ganho é contínuo ao interromper fades; `equal_power` mantém
  sin²+cos² = 1; `SourceResolved` com época velha nunca gera `start()`; o índice e o SQLite
  concordam depois de cada operação.
- Os ViewModels são testados sem janela; a View fica fina.

---

## 6. Ordem de implementação sugerida

A ordem do roadmap (tags → cenas → fades) inverte uma dependência: cenas precisam do
orquestrador e dos fades. Proposta:

1. **Fundação**: `domain/` + ports, composition root, `SqliteRepository` com migração do
   `library.json`, `LibraryScanner`. Corrige os itens 6, 7 e 8.
2. **Orquestrador + backends**: actor, `Voice`/estados, `BackendSelector`, um `vlc.Instance`,
   pool de canais, `UiPump`. Os cards atuais passam a mandar Commands. Corrige 1–5, 9, 10, 12.
3. **Fader**: estágios de ganho, envelopes, curvas, play/pause/stop com fade.
4. **Tags**: `LibraryIndex`, chips de filtro, biblioteca virtualizada + Deck. Independente do
   passo 3; pode andar em paralelo.
5. **Cenas**: CRUD, diff de transição, épocas, pré-resolução com TTL.

---

## 7. Decisões para aprovar

1. **SQLite agora** (JSON vira import/export), em vez de manter JSON com interface "para depois".
2. **Uma única thread dona do áudio** (actor) em vez de locks em torno dos players.
3. **Nova política de backend**: Pygame para locais curtos/SFX, VLC para streams e locais longos.
   Isso muda o comportamento atual, em que o VLC toca tudo.
4. **Separar Biblioteca (itens leves, virtualizada) de Deck (cards completos das vozes ativas)**.
5. **Ordem**: fundação → orquestrador → fader → tags → cenas.
