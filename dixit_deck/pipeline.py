from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import (
    NEGATIVO,
    ComfyClient,
    Conceito,
    DixitError,
    adicionar_metadados_png,
    caminho_upscale,
    caminho_variacao,
    dimensoes_png,
    diretorios,
    ler_json,
    ler_metadados_png,
    seed_para,
)


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _salvar_png(caminho: Path, conteudo: bytes, metadados: dict[str, Any]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    temporario.write_bytes(adicionar_metadados_png(conteudo, metadados))
    temporario.replace(caminho)


def _registrar(config: dict[str, Any], evento: dict[str, Any]) -> None:
    caminho = diretorios(config)["raiz"] / "manifesto.jsonl"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(evento, ensure_ascii=False) + "\n")


def workflow_geracao(
    config: dict[str, Any], conceito: Conceito, seed: int
) -> dict[str, Any]:
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": config["checkpoint"]},
        },
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0],
                "clip": ["1", 1],
                "lora_name": config["lora"],
                "strength_model": config["lora_peso"],
                "strength_clip": config["lora_peso"],
            },
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": conceito.prompt, "clip": ["2", 1]},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": NEGATIVO, "clip": ["2", 1]},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": config["largura"],
                "height": config["altura"],
                "batch_size": 1,
            },
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["2", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["5", 0],
                "seed": seed,
                "steps": config["steps"],
                "cfg": config["cfg"],
                "sampler_name": config["sampler"],
                "scheduler": config["scheduler"],
                "denoise": 1.0,
            },
        },
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {
            "class_type": "SaveImage",
            "inputs": {"images": ["7", 0], "filename_prefix": f"dixit/carta_{conceito.id}"},
        },
    }


def workflow_upscale(config: dict[str, Any], imagem: str, identificador: str) -> dict[str, Any]:
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": imagem}},
        "2": {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": config["upscaler"]},
        },
        "3": {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]},
        },
        "4": {
            "class_type": "SaveImage",
            "inputs": {"images": ["3", 0], "filename_prefix": f"dixit_upscale/carta_{identificador}"},
        },
    }


def metadados_geracao(
    config: dict[str, Any], conceito: Conceito, variacao: int, seed: int
) -> dict[str, Any]:
    return {
        "versao": 1,
        "id": conceito.id,
        "arquetipo": conceito.arquetipo,
        "sujeito_cena": conceito.sujeito,
        "prompt": conceito.prompt,
        "negative_prompt": NEGATIVO,
        "variacao": variacao,
        "seed": seed,
        "checkpoint": config["checkpoint"],
        "lora": config["lora"],
        "lora_peso": config["lora_peso"],
        "sampler": config["sampler"],
        "scheduler": config["scheduler"],
        "steps": config["steps"],
        "cfg": config["cfg"],
        "largura": config["largura"],
        "altura": config["altura"],
        "criado_em": _agora(),
    }


def gerar(
    config: dict[str, Any],
    cliente: ComfyClient,
    conceitos: list[Conceito],
    substituir: bool = False,
) -> None:
    if not conceitos:
        raise DixitError("Nenhum conceito selecionado para geração.")
    cliente.validar_modelo("checkpoints", config["checkpoint"])
    cliente.validar_modelo("loras", config["lora"])
    total = len(conceitos) * int(config["variacoes"])
    indice = 0
    for conceito in conceitos:
        for variacao in range(1, int(config["variacoes"]) + 1):
            indice += 1
            destino = caminho_variacao(config, conceito.id, variacao)
            if destino.exists() and not substituir:
                print(f"[{indice}/{total}] mantida {destino}")
                continue
            seed = seed_para(config, conceito.id, variacao)
            print(f"[{indice}/{total}] gerando carta {conceito.id}, variação {variacao}, seed {seed}")
            workflow = workflow_geracao(config, conceito, seed)
            inicio = time.monotonic()
            conteudo = cliente.executar_imagem(workflow)
            duracao = time.monotonic() - inicio
            if dimensoes_png(conteudo) != (config["largura"], config["altura"]):
                raise DixitError(f"Dimensão inesperada ao gerar carta {conceito.id}: {dimensoes_png(conteudo)}")
            metadados = metadados_geracao(config, conceito, variacao, seed)
            metadados["duracao_segundos"] = round(duracao, 3)
            _salvar_png(destino, conteudo, metadados)
            _registrar(config, {"operacao": "geracao", "arquivo": str(destino), **metadados})


def carregar_selecoes(config: dict[str, Any]) -> dict[str, Any]:
    dados = ler_json(diretorios(config)["raiz"] / "curadoria.json", {})
    if not isinstance(dados, dict):
        raise DixitError("curadoria.json deve conter um objeto JSON.")
    return dados


def corrigir_reprovadas(
    config: dict[str, Any],
    originais: list[Conceito],
    correcoes: list[Conceito],
) -> list[Conceito]:
    selecionadas = carregar_selecoes(config)
    reprovadas = {
        identificador for identificador, item in selecionadas.items()
        if isinstance(item, dict) and item.get("status") == "reprovada"
    }
    if not reprovadas:
        raise DixitError("Não há conceitos marcados como reprovados na curadoria.")
    mapa_original = {conceito.id: conceito for conceito in originais}
    resultado = []
    for correcao in correcoes:
        if correcao.id not in reprovadas:
            raise DixitError(f"Carta {correcao.id} não está marcada como reprovada.")
        original = mapa_original.get(correcao.id)
        if original is None:
            raise DixitError(f"Carta {correcao.id} não existe no arquivo original.")
        if correcao.arquetipo != original.arquetipo:
            raise DixitError(f"A correção da carta {correcao.id} deve manter o arquétipo.")
        normalizar = lambda texto: " ".join(texto.casefold().split())
        if normalizar(correcao.sujeito) == normalizar(original.sujeito):
            raise DixitError(f"A carta {correcao.id} precisa de um prompt corrigido, não apenas de outra seed.")
        resultado.append(correcao)
    ausentes = reprovadas - {conceito.id for conceito in resultado}
    if ausentes:
        raise DixitError("Faltam correções para as cartas: " + ", ".join(sorted(ausentes)))
    return resultado


def aprovadas(config: dict[str, Any], conceitos: list[Conceito]) -> list[tuple[Conceito, int]]:
    selecoes = carregar_selecoes(config)
    pendentes = [
        conceito.id for conceito in conceitos
        if selecoes.get(conceito.id, {}).get("status") != "aprovada"
    ]
    if pendentes:
        raise DixitError(
            f"Faltam {len(pendentes)} aprovações na curadoria: " + ", ".join(pendentes)
        )
    resultado = []
    for conceito in conceitos:
        variacao = selecoes[conceito.id].get("variacao")
        if not isinstance(variacao, int) or not 1 <= variacao <= int(config["variacoes"]):
            raise DixitError(f"Variação aprovada inválida para carta {conceito.id}.")
        origem = caminho_variacao(config, conceito.id, variacao)
        if not origem.exists():
            raise DixitError(f"Candidata aprovada não encontrada: {origem}")
        resultado.append((conceito, variacao))
    return resultado


def upscale(
    config: dict[str, Any],
    cliente: ComfyClient,
    conceitos: list[Conceito],
    substituir: bool = False,
) -> None:
    cliente.validar_modelo("upscale_models", config["upscaler"])
    selecionadas = aprovadas(config, conceitos)
    for indice, (conceito, variacao) in enumerate(selecionadas, 1):
        origem = caminho_variacao(config, conceito.id, variacao)
        destino = caminho_upscale(config, conceito.id)
        if destino.exists() and not substituir:
            print(f"[{indice}/{len(selecionadas)}] mantida {destino}")
            continue
        print(f"[{indice}/{len(selecionadas)}] upscale da carta {conceito.id}")
        imagem_remota = cliente.upload_imagem(origem)
        conteudo = cliente.executar_imagem(workflow_upscale(config, imagem_remota, conceito.id))
        if dimensoes_png(conteudo) != (4096, 4096):
            raise DixitError(
                f"Upscale da carta {conceito.id} gerou {dimensoes_png(conteudo)}, esperado 4096×4096."
            )
        metadados = ler_metadados_png(origem)
        metadados.update({
            "upscaler": config["upscaler"],
            "upscale_fator": 4,
            "aprovada_em": carregar_selecoes(config)[conceito.id].get("atualizado_em"),
            "upscale_em": _agora(),
        })
        _salvar_png(destino, conteudo, metadados)
        _registrar(config, {
            "operacao": "upscale",
            "arquivo": str(destino),
            "origem": str(origem),
            **metadados,
        })
