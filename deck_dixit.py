from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from dixit_deck.core import (
    ARQUETIPOS_PREVISTOS,
    ComfyClient,
    DixitError,
    carregar_config,
    ler_conceitos,
    mapa_conceitos,
)
from dixit_deck.curadoria import curar
from dixit_deck.finalizacao import exportar_conceitos, finalizar, qa
from dixit_deck.pipeline import corrigir_reprovadas, gerar, upscale


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deck_dixit.py",
        description="Pipeline local para produzir decks Dixit com ComfyUI.",
        epilog=(
            "Fluxo: validar → gerar → curar → regerar (se necessário) → upscale → finalizar → qa. "
            "Antes de gerar, ajuste checkpoint, lora e upscaler em config.json para os nomes instalados."
        ),
    )
    parser.add_argument("--config", default="config.json", help="Arquivo JSON de configuração.")
    parser.add_argument("--conceitos", default="conceitos.txt", help="Arquivo de conceitos separado por ponto e vírgula.")
    parser.add_argument("--comfy-url", help="Substitui comfy_url da configuração.")
    subparsers = parser.add_subparsers(dest="comando", required=True)

    subparsers.add_parser("validar", help="Validar conceitos, conexão e modelos instalados no ComfyUI.")

    geracao = subparsers.add_parser("gerar", help="Gerar quatro candidatas por conceito, uma por vez para economizar VRAM.")
    geracao.add_argument("--ids", help="IDs separados por vírgula; por padrão gera todo o deck.")
    geracao.add_argument("--substituir", action="store_true", help="Substituir candidatas já existentes.")

    curadoria = subparsers.add_parser("curar", help="Abrir a curadoria visual local e salvar aprovadas/reprovadas.")
    curadoria.add_argument("--porta", type=int, default=8765)
    curadoria.add_argument("--sem-navegador", action="store_true", help="Não abrir o navegador automaticamente.")

    regeracao = subparsers.add_parser("regerar", help="Regerar todas as reprovadas usando prompts efetivamente corrigidos.")
    regeracao.add_argument(
        "--correcoes",
        required=True,
        help="Arquivo id;arquetipo;novo_sujeito com uma correção para cada carta reprovada.",
    )

    ampliacao = subparsers.add_parser("upscale", help="Aplicar 4x-UltraSharp somente às cartas aprovadas.")
    ampliacao.add_argument("--substituir", action="store_true", help="Substituir upscales já existentes.")

    final = subparsers.add_parser(
        "finalizar", help="Exportar 744×1039 a 300 dpi, folha de contato, conceitos finais e QA técnico."
    )
    final.add_argument(
        "--conceitos-saida",
        help="Destino dos conceitos enriquecidos; por padrão atualiza o arquivo informado em --conceitos.",
    )

    exportacao = subparsers.add_parser("exportar", help="Exportar conceitos com prompt, seed, LoRA e variação aprovada.")
    exportacao.add_argument("--saida", default="conceitos_final.txt")

    subparsers.add_parser("qa", help="Revalidar dimensões, contagens, duplicatas exatas e similaridades visuais.")
    painel = subparsers.add_parser("painel", help="Abrir o painel web para controlar decks, conceitos, curadoria e processamento.")
    painel.add_argument("--porta", type=int, default=8765)
    painel.add_argument("--sem-navegador", action="store_true", help="Não abrir o navegador automaticamente.")
    return parser


def _selecionar_ids(conceitos: list[Any], ids: str | None) -> list[Any]:
    if not ids:
        return conceitos
    escolhidos = [item.strip().zfill(3) for item in ids.split(",") if item.strip()]
    mapa = mapa_conceitos(conceitos)
    desconhecidos = set(escolhidos) - set(mapa)
    if desconhecidos:
        raise DixitError("IDs desconhecidos: " + ", ".join(sorted(desconhecidos)))
    return [mapa[identificador] for identificador in escolhidos]


def _validar(config: dict[str, Any], conceitos: list[Any]) -> None:
    if not conceitos:
        raise DixitError("O arquivo de conceitos está vazio.")
    contagens = Counter(conceito.arquetipo for conceito in conceitos)
    escala = len(conceitos) / sum(ARQUETIPOS_PREVISTOS.values())
    for arquetipo, quantidade_base in ARQUETIPOS_PREVISTOS.items():
        previsto = quantidade_base * escala
        if not previsto * 0.8 <= contagens[arquetipo] <= previsto * 1.2:
            raise DixitError(
                f"{arquetipo}: esperado aproximadamente {previsto:.1f}, encontrado {contagens[arquetipo]}."
            )
    print(f"Conceitos: {len(conceitos)}; distribuição de arquétipos dentro de ±20%.")
    cliente = ComfyClient(config["comfy_url"], int(config["timeout"]))
    stats = cliente.json("/system_stats")
    sistema = stats.get("system", {})
    print(f"ComfyUI: {sistema.get('comfyui_version', 'versão não informada')}")
    print(f"Python remoto: {sistema.get('python_version', 'não informado')}")
    print(f"PyTorch remoto: {sistema.get('pytorch_version', 'não informado')}")
    for dispositivo in stats.get("devices", []):
        print("GPU: " + json.dumps(dispositivo, ensure_ascii=False))
    cliente.validar_modelo("checkpoints", config["checkpoint"])
    cliente.validar_modelo("loras", config["lora"])
    cliente.validar_modelo("upscale_models", config["upscaler"])
    print("Checkpoint, LoRA e upscaler encontrados. Ambiente pronto para gerar.")
    print("Na RTX 5060/sm_120, confirme que o PyTorch acima usa CUDA 12.8 ou superior.")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = criar_parser()
    args = parser.parse_args(argv)
    try:
        config = carregar_config(args.config)
        if args.comfy_url:
            config["comfy_url"] = args.comfy_url
        conceitos = ler_conceitos(args.conceitos)
        if args.comando == "painel":
            from dixit_deck.painel_server import executar_painel
            executar_painel(Path.cwd(), args.porta, not args.sem_navegador)
        elif args.comando == "validar":
            _validar(config, conceitos)
        elif args.comando == "gerar":
            selecionados = _selecionar_ids(conceitos, args.ids)
            cliente = ComfyClient(config["comfy_url"], int(config["timeout"]))
            gerar(config, cliente, selecionados, args.substituir)
        elif args.comando == "curar":
            curar(config, conceitos, args.porta, not args.sem_navegador)
        elif args.comando == "regerar":
            correcoes = ler_conceitos(args.correcoes)
            corrigidos = corrigir_reprovadas(config, conceitos, correcoes)
            cliente = ComfyClient(config["comfy_url"], int(config["timeout"]))
            gerar(config, cliente, corrigidos, substituir=True)
            print("Candidatas substituídas. Abra a curadoria e revise as cartas corrigidas.")
        elif args.comando == "upscale":
            cliente = ComfyClient(config["comfy_url"], int(config["timeout"]))
            upscale(config, cliente, conceitos, args.substituir)
        elif args.comando == "finalizar":
            destino = Path(args.conceitos_saida) if args.conceitos_saida else Path(args.conceitos)
            finalizar(config, conceitos, destino)
        elif args.comando == "exportar":
            exportar_conceitos(config, conceitos, Path(args.saida))
            print(f"Conceitos exportados para {args.saida}")
        elif args.comando == "qa":
            relatorio = qa(config, conceitos)
            print(json.dumps(relatorio, ensure_ascii=False, indent=2))
        return 0
    except DixitError as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nOperação interrompida.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
