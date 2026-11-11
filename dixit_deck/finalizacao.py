from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from .core import (
    ARQUETIPOS_PREVISTOS,
    Conceito,
    DixitError,
    caminho_upscale,
    dimensoes_png,
    diretorios,
    escrever_json,
    ler_metadados_png,
)
from .pipeline import aprovadas


def _pillow() -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin
    except ImportError as erro:
        raise DixitError(
            "A pós-produção precisa do Pillow. Execute: python -m pip install -r requirements.txt"
        ) from erro
    return {
        "Image": Image,
        "ImageDraw": ImageDraw,
        "ImageFont": ImageFont,
        "ImageOps": ImageOps,
        "PngImagePlugin": PngImagePlugin,
    }


def exportar_conceitos(
    config: dict[str, Any], conceitos: list[Conceito], destino: Path
) -> None:
    aprovadas(config, conceitos)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(destino.suffix + ".tmp")
    with temporario.open("w", encoding="utf-8", newline="") as stream:
        gravador = csv.writer(stream, delimiter=";", lineterminator="\n")
        for conceito in conceitos:
            caminho = caminho_upscale(config, conceito.id)
            metadados = ler_metadados_png(caminho)
            gravador.writerow([
                conceito.id,
                conceito.arquetipo,
                conceito.sujeito,
                metadados["prompt"],
                metadados["seed"],
                metadados["lora"],
                metadados["lora_peso"],
                metadados["variacao"],
                str(caminho),
            ])
    os.replace(temporario, destino)


def _folha_contato(config: dict[str, Any], conceitos: list[Conceito], pillow: dict[str, Any]) -> Path:
    Image = pillow["Image"]
    ImageDraw = pillow["ImageDraw"]
    ImageFont = pillow["ImageFont"]
    ImageOps = pillow["ImageOps"]
    colunas = 7
    largura_celula, altura_celula = 240, 272
    margem = 24
    linhas = (len(conceitos) + colunas - 1) // colunas
    folha = Image.new(
        "RGB",
        (margem * 2 + colunas * largura_celula, margem * 2 + linhas * altura_celula + 42),
        "#18201e",
    )
    desenho = ImageDraw.Draw(folha)
    fonte = ImageFont.load_default()
    desenho.text((margem, margem), f"DECK DIXIT — {len(conceitos)} CARTAS", fill="#e4c28a", font=fonte)
    for indice, conceito in enumerate(conceitos):
        coluna, linha = indice % colunas, indice // colunas
        x = margem + coluna * largura_celula
        y = margem + 42 + linha * altura_celula
        with Image.open(caminho_upscale(config, conceito.id)) as imagem:
            miniatura = ImageOps.contain(imagem.convert("RGB"), (220, 220), Image.Resampling.LANCZOS)
        folha.paste(miniatura, (x + 10, y))
        desenho.text((x + 10, y + 228), f"Carta {conceito.id}", fill="#e8ecea", font=fonte)
        desenho.text((x + 10, y + 245), conceito.arquetipo.replace("_", " ")[:31], fill="#9eb1aa", font=fonte)
    destino = diretorios(config)["raiz"] / "folha_contato.png"
    destino.parent.mkdir(parents=True, exist_ok=True)
    folha.save(destino, "PNG")
    return destino


def finalizar(config: dict[str, Any], conceitos: list[Conceito], conceitos_saida: Path) -> dict[str, Any]:
    pillow = _pillow()
    Image = pillow["Image"]
    ImageOps = pillow["ImageOps"]
    PngImagePlugin = pillow["PngImagePlugin"]
    selecionadas = aprovadas(config, conceitos)
    pasta_impressao = diretorios(config)["impressao"]
    pasta_impressao.mkdir(parents=True, exist_ok=True)
    deck = []
    for indice, (conceito, _variacao) in enumerate(selecionadas, 1):
        origem = caminho_upscale(config, conceito.id)
        if not origem.is_file():
            raise DixitError(f"Upscale não encontrado: {origem}")
        print(f"[{indice}/{len(selecionadas)}] exportando carta {conceito.id} para impressão")
        metadados = ler_metadados_png(origem)
        with Image.open(origem) as imagem:
            if imagem.size != (4096, 4096):
                raise DixitError(f"Carta {conceito.id} não está em 4096×4096: {imagem.size}")
            impressao = ImageOps.fit(
                imagem.convert("RGB"),
                (744, 1039),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
        info = PngImagePlugin.PngInfo()
        info.add_text("dixit", json.dumps(metadados, ensure_ascii=False))
        destino = pasta_impressao / f"carta_{conceito.id}.png"
        impressao.save(destino, "PNG", dpi=(300, 300), pnginfo=info)
        deck.append({
            "id": conceito.id,
            "arquetipo": conceito.arquetipo,
            "upscale": str(origem),
            "impressao": str(destino),
            "seed": metadados["seed"],
            "prompt": metadados["prompt"],
        })
    folha = _folha_contato(config, conceitos, pillow)
    exportar_conceitos(config, conceitos, conceitos_saida)
    escrever_json(diretorios(config)["raiz"] / "deck.json", deck)
    print(f"Folha de contato: {folha}")
    print(f"Conceitos reproduzíveis: {conceitos_saida}")
    return qa(config, conceitos)


def _hash_perceptual(caminho: Path, Image: Any) -> int:
    with Image.open(caminho) as imagem:
        cinza = imagem.convert("L").resize((16, 16), Image.Resampling.LANCZOS)
        pixels = list(cinza.getdata())
    media = sum(pixels) / len(pixels)
    resultado = 0
    for pixel in pixels:
        resultado = (resultado << 1) | int(pixel >= media)
    return resultado


def qa(config: dict[str, Any], conceitos: list[Conceito]) -> dict[str, Any]:
    pillow = _pillow()
    Image = pillow["Image"]
    erros: list[str] = []
    avisos: list[str] = []
    total = len(conceitos)
    if not total:
        erros.append("O arquivo de conceitos está vazio.")
    contagens = Counter(conceito.arquetipo for conceito in conceitos)
    escala = total / sum(ARQUETIPOS_PREVISTOS.values())
    distribuicao = {}
    for arquetipo, quantidade_base in ARQUETIPOS_PREVISTOS.items():
        previsto = quantidade_base * escala
        atual = contagens[arquetipo]
        dentro = previsto * 0.8 <= atual <= previsto * 1.2
        distribuicao[arquetipo] = {"previsto": round(previsto, 1), "atual": atual, "dentro_da_tolerancia": dentro}
        if not dentro:
            erros.append(f"Arquétipo {arquetipo}: {atual} cartas, fora da tolerância de ±20% de {previsto:.1f}.")
    desconhecidos = set(contagens) - set(ARQUETIPOS_PREVISTOS)
    if desconhecidos:
        erros.append("Arquétipos desconhecidos: " + ", ".join(sorted(desconhecidos)))

    hashes_exatos: dict[str, str] = {}
    hashes_perceptuais: list[tuple[str, int]] = []
    arquivos_verificados = 0
    pasta_upscale = diretorios(config)["upscaled"]
    pasta_impressao = diretorios(config)["impressao"]
    for pasta in (pasta_upscale, pasta_impressao):
        encontrados = list(pasta.glob("carta_*.png")) if pasta.exists() else []
        if len(encontrados) != total:
            erros.append(f"{pasta}: esperados {total} PNGs; encontrados {len(encontrados)}.")
    for conceito in conceitos:
        final = caminho_upscale(config, conceito.id)
        impressao = pasta_impressao / f"carta_{conceito.id}.png"
        try:
            conteudo = final.read_bytes()
            if dimensoes_png(conteudo) != (4096, 4096):
                erros.append(f"{final}: dimensão diferente de 4096×4096.")
            digest = hashlib.sha256(conteudo).hexdigest()
            if digest in hashes_exatos:
                erros.append(f"Duplicata exata: {conceito.id} e {hashes_exatos[digest]}.")
            hashes_exatos[digest] = conceito.id
            hashes_perceptuais.append((conceito.id, _hash_perceptual(final, Image)))
        except (OSError, DixitError) as erro:
            erros.append(str(erro))
        try:
            with Image.open(impressao) as imagem:
                if imagem.size != (744, 1039):
                    erros.append(f"{impressao}: dimensão diferente de 744×1039.")
                dpi = imagem.info.get("dpi", (0, 0))
                if not all(abs(valor - 300) <= 1 for valor in dpi):
                    erros.append(f"{impressao}: resolução DPI ausente ou diferente de 300.")
                imagem.load()
            arquivos_verificados += 1
        except (OSError, ValueError) as erro:
            erros.append(f"{impressao}: {erro}")
    limiar = 6
    for indice, (id_a, hash_a) in enumerate(hashes_perceptuais):
        for id_b, hash_b in hashes_perceptuais[indice + 1:]:
            distancia = (hash_a ^ hash_b).bit_count()
            if distancia <= limiar:
                avisos.append(
                    f"Possível similaridade visual entre {id_a} e {id_b} (distância {distancia}/{256}); revisar na folha de contato."
                )
    relatorio = {
        "ok_tecnico": not erros,
        "cartas": len(conceitos),
        "arquivos_impressao_verificados": arquivos_verificados,
        "dimensoes_finais": [4096, 4096],
        "dimensoes_impressao": [744, 1039],
        "dpi_impressao": 300,
        "distribuicao": distribuicao,
        "erros": erros,
        "avisos": avisos,
        "criterios_humanos_pendentes": [
            "Cada carta admite pelo menos duas interpretações.",
            "Não há texto, marcas-d'água ou defeitos anatômicos evidentes.",
            "A paleta e a pintura são consistentes em cartas aleatórias.",
            "As similaridades semânticas e o teste de mesa exigem avaliação humana.",
        ],
    }
    escrever_json(diretorios(config)["raiz"] / "qa.json", relatorio)
    if erros:
        raise DixitError(f"QA técnico encontrou {len(erros)} erro(s); consulte {diretorios(config)['raiz'] / 'qa.json'}")
    print(f"QA técnico aprovado; {len(avisos)} aviso(s) de similaridade para revisão humana.")
    return relatorio
