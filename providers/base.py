from abc import ABC, abstractmethod
from typing import Callable, Optional, Dict, Any, List


class BaseAudioProvider(ABC):
    """
    Interface base (Strategy Pattern) para provedores de áudio.
    Permite desacoplar a interface gráfica da fonte dos arquivos,
    facilitando a adição futura de serviços como Spotify (Spotipy), SoundCloud, etc.
    """

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Pesquisa faixas de áudio ou metadados na fonte.

        :param query: Termo de busca ou título.
        :param max_results: Quantidade máxima de itens retornados.
        :return: Lista de dicionários contendo informações das faixas encontradas.
        """
        pass

    @abstractmethod
    def download_or_stream(
        self,
        query_or_url: str,
        output_dir: str,
        on_progress: Optional[Callable[[float, str], None]] = None,
        on_complete: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None
    ) -> None:
        """
        Baixa o arquivo de áudio ou prepara o fluxo para reprodução.
        Deve ser executado em uma thread separada para não travar a UI.

        :param query_or_url: URL ou termo a ser baixado.
        :param output_dir: Diretório onde o arquivo final será salvo.
        :param on_progress: Callback de progresso (percentual float de 0 a 100, texto de status).
        :param on_complete: Callback de sucesso (caminho absoluto do arquivo, metadados).
        :param on_error: Callback de erro (objeto Exception).
        """
        pass
