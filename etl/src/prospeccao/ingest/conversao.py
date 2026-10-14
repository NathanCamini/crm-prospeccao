"""Conversão dos CSVs da Receita para UTF-8.

A Receita declara ISO-8859-1 (Latin-1), mas os arquivos reais trazem bytes de 0x80 a 0x9F
(ex.: aspas curvas “ ” e travessão –), que só existem no Windows-1252. O leitor de CSV do
DuckDB recusa esses bytes ("File is not latin-1 encoded"). Por isso a extração converte
para UTF-8 em streaming, decodificando como Windows-1252; os 5 bytes que o Windows-1252
não define (0x81, 0x8D, 0x8F, 0x90, 0x9D) caem para Latin-1, então nenhum byte é perdido.
"""

from __future__ import annotations

import codecs
import io
from typing import BinaryIO

ENCODING_ORIGEM = "cp1252"
_HANDLER = "receita_latin1"
_BLOCO = 4 * 1024 * 1024


def _latin1_no_erro(erro: UnicodeError) -> tuple[str, int]:
    if not isinstance(erro, UnicodeDecodeError):
        raise erro
    trecho = erro.object[erro.start : erro.end]
    return bytes(trecho).decode("latin-1"), erro.end


codecs.register_error(_HANDLER, _latin1_no_erro)


def decodificar(dados: bytes) -> str:
    """Bytes da Receita -> texto (Windows-1252 com reserva em Latin-1)."""
    return dados.decode(ENCODING_ORIGEM, errors=_HANDLER)


def converter_para_utf8(origem: BinaryIO, destino: BinaryIO) -> int:
    """Copia `origem` (bytes da Receita) para `destino` em UTF-8, em blocos. Devolve bytes lidos."""
    leitor = io.TextIOWrapper(origem, encoding=ENCODING_ORIGEM, errors=_HANDLER, newline="")
    escritor = io.TextIOWrapper(destino, encoding="utf-8", newline="", write_through=True)
    total = 0
    try:
        while bloco := leitor.read(_BLOCO):
            escritor.write(bloco)
            total += len(bloco)
        escritor.flush()
    finally:
        escritor.detach()
        leitor.detach()
    return total
