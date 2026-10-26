"""
Política de escolha do backend (doc: §1.5). Devolve candidatos em ordem de preferência;
o mixer tenta o próximo se um deles estiver sem vagas.
"""
from domain.models import BackendHint, PlayableSource, SFX_MAX_DURATION_S, Track, TrackKind
from domain.ports import PlaybackBackend


class NoBackendAvailable(Exception):
    pass


class BackendSelector:
    def __init__(self, vlc: PlaybackBackend | None, pygame: PlaybackBackend | None):
        self.vlc = vlc
        self.pygame = pygame

    @property
    def backends(self) -> list[PlaybackBackend]:
        return [b for b in (self.vlc, self.pygame) if b is not None]

    def disable(self, backend: PlaybackBackend) -> None:
        """Tira de uso um backend que não conseguiu abrir (ex.: sem placa de som)."""
        if backend is self.vlc:
            self.vlc = None
        elif backend is self.pygame:
            self.pygame = None

    def candidates(self, track: Track, source: PlayableSource) -> list[PlaybackBackend]:
        # 1. Stream: só o VLC toca URL
        if source.is_remote:
            if self.vlc is None:
                raise NoBackendAvailable(
                    "O streaming precisa do VLC instalado no sistema (ex.: sudo apt install vlc)."
                )
            return [self.vlc]

        if not self.backends:
            raise NoBackendAvailable("Nenhum motor de áudio disponível.")

        # 2. Escolha forçada na faixa, se o backend existir; os outros ficam de reserva
        forced = {BackendHint.VLC: self.vlc, BackendHint.PYGAME: self.pygame}.get(track.backend_hint)
        if forced is not None:
            return [forced] + [b for b in self.backends if b is not forced]

        # 3. Local curto ou efeito: Pygame (pré-decodificado em RAM, baixa latência),
        #    com o VLC de reserva se os canais acabarem
        if _is_short(track):
            return [b for b in (self.pygame, self.vlc) if b is not None]

        # 4. Local longo: VLC (lê do disco, tem seek)
        if self.vlc is not None:
            return [self.vlc]

        # 5. Sem VLC: Pygame só se a duração for desconhecida (comportamento antigo);
        #    uma música longa conhecida ocuparia centenas de MB de RAM.
        if track.duration_s is None:
            return [self.pygame]
        raise NoBackendAvailable(
            "Faixas longas precisam do VLC instalado no sistema (ex.: sudo apt install vlc)."
        )


def _is_short(track: Track) -> bool:
    if track.duration_s is not None:
        return track.duration_s <= SFX_MAX_DURATION_S
    return track.kind == TrackKind.SFX
