from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psutil

from .core import (
    ARQUETIPOS_PREVISTOS,
    ComfyClient,
    Conceito,
    DixitError,
    caminho_upscale,
    caminho_variacao,
    carregar_config,
    diretorios,
    ler_conceitos,
    ler_metadados_png,
    escrever_json,
)
from .pipeline import carregar_selecoes

ACOES = {"gerar", "upscale", "finalizar", "qa", "validar"}


@dataclass(frozen=True)
class Deck:
    id: str
    nome: str
    config_path: Path
    conceitos_path: Path
    config: dict[str, Any]
    conceitos: list[Conceito]


class GerenciadorJobs:
    def __init__(self, raiz: Path):
        self.raiz = raiz
        self.jobs: dict[str, dict[str, Any]] = {}
        self.bloqueio = threading.Lock()

    def ativo_para_deck(self, deck_id: str) -> dict[str, Any] | None:
        with self.bloqueio:
            return next(
                (self._publico(job) for job in self.jobs.values() if job["deck_id"] == deck_id and job["status"] == "executando"),
                None,
            )

    def processos_externos(self, deck: Deck) -> list[dict[str, Any]]:
        proprios = {job["processo"].pid for job in self.jobs.values() if job["status"] == "executando"}
        encontrados = []
        for processo in psutil.process_iter(["pid", "cmdline"]):
            try:
                pid = processo.info["pid"]
                if pid in proprios or pid == os.getpid():
                    continue
                comando = processo.info["cmdline"] or []
                if not any(Path(parte).name == "deck_dixit.py" for parte in comando):
                    continue
                acao = next((parte for parte in comando if parte in ACOES or parte == "regerar"), None)
                if not acao:
                    continue
                if "--config" in comando:
                    indice = comando.index("--config")
                    config = Path(comando[indice + 1])
                else:
                    config = Path("config.json")
                if not config.is_absolute():
                    config = self.raiz / config
                if config.resolve() == deck.config_path.resolve():
                    encontrados.append({"pid": pid, "acao": acao, "externo": True})
            except (psutil.NoSuchProcess, psutil.AccessDenied, IndexError, OSError):
                continue
        return encontrados

    def iniciar(self, deck: Deck, acao: str, ids: str | None = None, substituir: bool = False) -> dict[str, Any]:
        if acao not in ACOES:
            raise DixitError("Ação inválida.")
        if self.ativo_para_deck(deck.id):
            raise DixitError("Já existe uma ação em execução para este deck.")
        if self.processos_externos(deck):
            raise DixitError("Este deck já possui um processo externo em execução; aguarde seu término.")
        job_id = uuid.uuid4().hex[:12]
        comando = [
            sys.executable,
            "-B",
            "-u",
            str(self.raiz / "deck_dixit.py"),
            "--config",
            str(deck.config_path),
            "--conceitos",
            str(deck.conceitos_path),
            acao,
        ]
        if ids and acao == "gerar":
            comando.extend(["--ids", ids])
        if substituir and acao in ("gerar", "upscale"):
            comando.append("--substituir")
        processo = subprocess.Popen(
            comando,
            cwd=self.raiz,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with self.bloqueio:
            job = {
                "id": job_id,
                "deck_id": deck.id,
                "deck_nome": deck.nome,
                "acao": acao,
                "status": "executando",
                "inicio": time.time(),
                "fim": None,
                "codigo_saida": None,
                "linhas": [],
                "processo": processo,
            }
            self.jobs[job_id] = job
        threading.Thread(target=self._ler_saida, args=(job_id,), daemon=True).start()
        return self._publico(job)

    def _ler_saida(self, job_id: str) -> None:
        with self.bloqueio:
            job = self.jobs[job_id]
            processo = job["processo"]
        assert processo.stdout is not None
        for linha in processo.stdout:
            with self.bloqueio:
                job["linhas"].append({"em": time.time(), "texto": linha.rstrip()})
                del job["linhas"][:-1500]
        codigo = processo.wait()
        with self.bloqueio:
            job["codigo_saida"] = codigo
            job["status"] = "concluido" if codigo == 0 else "erro" if codigo != 0 and job["status"] != "parado" else "parado"
            job["fim"] = time.time()

    def parar(self, job_id: str) -> dict[str, Any]:
        with self.bloqueio:
            job = self.jobs.get(job_id)
            if not job:
                raise DixitError("Job não encontrado.")
            if job["status"] != "executando":
                return self._publico(job)
            processo = job["processo"]
            job["status"] = "parado"
        processo.terminate()
        return self._publico(job)

    def listar(self) -> list[dict[str, Any]]:
        with self.bloqueio:
            jobs = sorted(self.jobs.values(), key=lambda item: item["inicio"], reverse=True)
            return [self._publico(job, incluir_logs=False) for job in jobs]

    def obter(self, job_id: str) -> dict[str, Any]:
        with self.bloqueio:
            if job_id not in self.jobs:
                raise DixitError("Job não encontrado.")
            return self._publico(self.jobs[job_id], incluir_logs=True)

    def _publico(self, job: dict[str, Any], incluir_logs: bool = False) -> dict[str, Any]:
        fim = job["fim"] or time.time()
        dados = {
            "id": job["id"],
            "deck_id": job["deck_id"],
            "deck_nome": job["deck_nome"],
            "acao": job["acao"],
            "status": job["status"],
            "inicio": job["inicio"],
            "fim": job["fim"],
            "duracao": max(0, fim - job["inicio"]),
            "codigo_saida": job["codigo_saida"],
        }
        if incluir_logs:
            dados["logs"] = list(job["linhas"])
        return dados


class ServicoDecks:
    def __init__(self, raiz: Path):
        self.raiz = raiz
        self.jobs = GerenciadorJobs(raiz)
        self.bloqueio = threading.Lock()

    def listar(self) -> list[Deck]:
        decks = []
        for config_path in sorted(self.raiz.glob("config*.json")):
            try:
                config = carregar_config(config_path)
                identificador = config_path.stem.removeprefix("config_")
                if identificador == "config":
                    identificador = "principal"
                conceitos_path = Path(config.get("conceitos", "conceitos.txt"))
                if not conceitos_path.is_absolute():
                    conceitos_path = self.raiz / conceitos_path
                decks.append(Deck(
                    id=identificador,
                    nome=config.get("nome", identificador),
                    config_path=config_path,
                    conceitos_path=conceitos_path,
                    config=config,
                    conceitos=ler_conceitos(conceitos_path),
                ))
            except DixitError:
                continue
        return decks

    def obter(self, identificador: str) -> Deck:
        for deck in self.listar():
            if deck.id == identificador:
                return deck
        raise DixitError("Deck não encontrado.")

    def _duracoes(self, deck: Deck) -> dict[tuple[str, int], dict[str, Any]]:
        pasta = diretorios(deck.config)["geradas"]
        arquivos = []
        if pasta.exists():
            for caminho in pasta.glob("carta_*.png"):
                match = re.fullmatch(r"carta_(\d+)_(\d+)\.png", caminho.name)
                if match:
                    arquivos.append((caminho.stat().st_mtime, match[1], int(match[2])))
        arquivos.sort()
        resultado = {}
        anterior = None
        for mtime, identificador, variacao in arquivos:
            duracao = None if anterior is None else mtime - anterior
            if duracao is not None and not 0 < duracao <= 300:
                duracao = None
            resultado[(identificador, variacao)] = {
                "segundos": round(duracao, 1) if duracao is not None else None,
                "estimado": True,
                "mtime": mtime,
            }
            anterior = mtime
        return resultado

    def resumo(self, deck: Deck) -> dict[str, Any]:
        diretorio = diretorios(deck.config)
        total = len(deck.conceitos)
        variacoes = int(deck.config["variacoes"])
        candidatas = list(diretorio["geradas"].glob("carta_*.png")) if diretorio["geradas"].exists() else []
        upscales = list(diretorio["upscaled"].glob("carta_*.png")) if diretorio["upscaled"].exists() else []
        impressoes = list(diretorio["impressao"].glob("carta_*.png")) if diretorio["impressao"].exists() else []
        selecoes = carregar_selecoes(deck.config)
        ids = {conceito.id for conceito in deck.conceitos}
        aprovadas = sum(item.get("status") == "aprovada" for chave, item in selecoes.items() if chave in ids)
        reprovadas = sum(item.get("status") == "reprovada" for chave, item in selecoes.items() if chave in ids)
        mtimes = [caminho.stat().st_mtime for caminho in candidatas]
        ativo = self.jobs.ativo_para_deck(deck.id)
        externos = self.jobs.processos_externos(deck)
        if len(impressoes) == total and total:
            etapa = "concluido"
        elif len(upscales) == total and total:
            etapa = "exportacao"
        elif len(candidatas) >= total * variacoes:
            etapa = "curadoria"
        else:
            etapa = "geracao"
        return {
            "id": deck.id,
            "nome": deck.nome,
            "total_cartas": total,
            "variacoes_por_carta": variacoes,
            "candidatas": len(candidatas),
            "candidatas_esperadas": total * variacoes,
            "aprovadas": aprovadas,
            "reprovadas": reprovadas,
            "pendentes": total - aprovadas - reprovadas,
            "upscales": len(upscales),
            "impressoes": len(impressoes),
            "etapa": etapa,
            "job_ativo": ativo,
            "processos_externos": externos,
            "ocupado": bool(ativo or externos),
            "ultima_atualizacao": max(mtimes) if mtimes else None,
            "tempo_decorrido_segundos": max(mtimes) - min(mtimes) if len(mtimes) > 1 else None,
            "folha_contato": (diretorio["raiz"] / "folha_contato.png").is_file(),
        }

    def detalhe(self, deck: Deck) -> dict[str, Any]:
        selecoes = carregar_selecoes(deck.config)
        duracoes = self._duracoes(deck)
        cartas = []
        for conceito in deck.conceitos:
            opcoes = []
            for variacao in range(1, int(deck.config["variacoes"]) + 1):
                caminho = caminho_variacao(deck.config, conceito.id, variacao)
                info = duracoes.get((conceito.id, variacao), {})
                opcoes.append({
                    "variacao": variacao,
                    "existe": caminho.is_file(),
                    "duracao": info.get("segundos"),
                    "duracao_estimada": info.get("estimado", True),
                    "versao": caminho.stat().st_mtime_ns if caminho.is_file() else 0,
                })
            selecao = selecoes.get(conceito.id, {})
            tempos = [item["duracao"] for item in opcoes if item["duracao"] is not None]
            cartas.append({
                "id": conceito.id,
                "arquetipo": conceito.arquetipo,
                "sujeito": conceito.sujeito,
                "status": selecao.get("status", "pendente"),
                "variacao_aprovada": selecao.get("variacao"),
                "geradas": sum(item["existe"] for item in opcoes),
                "candidatas": opcoes,
                "tempo_total": round(sum(tempos), 1) if tempos else None,
                "upscale": caminho_upscale(deck.config, conceito.id).is_file(),
            })
        return {"deck": self.resumo(deck), "cartas": cartas}

    def carta(self, deck: Deck, identificador: str) -> dict[str, Any]:
        conceito = next((item for item in deck.conceitos if item.id == identificador), None)
        if not conceito:
            raise DixitError("Carta não encontrada.")
        duracoes = self._duracoes(deck)
        candidatas = []
        for variacao in range(1, int(deck.config["variacoes"]) + 1):
            caminho = caminho_variacao(deck.config, identificador, variacao)
            item = {"variacao": variacao, "existe": caminho.is_file(), **duracoes.get((identificador, variacao), {})}
            if caminho.is_file():
                metadados = ler_metadados_png(caminho)
                item.update({
                    "seed": metadados.get("seed"),
                    "prompt": metadados.get("prompt"),
                    "criado_em": metadados.get("criado_em"),
                    "tamanho_bytes": caminho.stat().st_size,
                })
                if metadados.get("duracao_segundos") is not None:
                    item["segundos"] = metadados["duracao_segundos"]
                    item["estimado"] = False
            candidatas.append(item)
        return {"conceito": {"id": conceito.id, "arquetipo": conceito.arquetipo, "sujeito": conceito.sujeito}, "candidatas": candidatas}

    def curadoria(self, deck: Deck, identificador: str, status: str, variacao: Any) -> dict[str, Any]:
        if identificador not in {item.id for item in deck.conceitos} or status not in ("aprovada", "reprovada"):
            raise DixitError("Carta ou status inválido.")
        with self.bloqueio:
            selecoes = carregar_selecoes(deck.config)
            selecao = {"status": status, "atualizado_em": datetime.now(timezone.utc).isoformat()}
            if status == "aprovada":
                if not isinstance(variacao, int) or not 1 <= variacao <= int(deck.config["variacoes"]):
                    raise DixitError("Variação inválida.")
                if not caminho_variacao(deck.config, identificador, variacao).is_file():
                    raise DixitError("A candidata selecionada não existe.")
                selecao["variacao"] = variacao
            selecoes[identificador] = selecao
            escrever_json(diretorios(deck.config)["raiz"] / "curadoria.json", selecoes)
        return selecao

    def editar_conceito(self, deck: Deck, identificador: str, sujeito: str, arquetipo: str) -> None:
        if not sujeito.strip() or arquetipo not in ARQUETIPOS_PREVISTOS:
            raise DixitError("Sujeito e arquétipo válidos são obrigatórios.")
        with self.bloqueio:
            with deck.conceitos_path.open(encoding="utf-8", newline="") as stream:
                linhas = list(csv.reader(stream, delimiter=";"))
            encontrado = False
            for linha in linhas:
                if linha and linha[0].strip().zfill(3) == identificador:
                    linha[1] = arquetipo
                    linha[2] = sujeito.strip()
                    encontrado = True
                    break
            if not encontrado:
                raise DixitError("Conceito não encontrado.")
            temporario = deck.conceitos_path.with_suffix(".tmp")
            with temporario.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream, delimiter=";", lineterminator="\n").writerows(linhas)
            os.replace(temporario, deck.conceitos_path)


def executar_painel(raiz: Path, porta: int, abrir_navegador: bool = True) -> None:
    servico = ServicoDecks(raiz)
    estaticos = raiz / "painel"

    class Handler(BaseHTTPRequestHandler):
        def _enviar(self, conteudo: bytes, tipo: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(conteudo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(conteudo)

        def _json(self, dados: Any, status: int = 200) -> None:
            self._enviar(json.dumps(dados, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

        def _corpo(self) -> dict[str, Any]:
            tamanho = int(self.headers.get("Content-Length", "0"))
            if not 0 < tamanho <= 100000:
                raise DixitError("Corpo da requisição inválido.")
            dados = json.loads(self.rfile.read(tamanho))
            if not isinstance(dados, dict):
                raise DixitError("JSON deve ser um objeto.")
            return dados

        def do_GET(self) -> None:
            try:
                url = urllib.parse.urlparse(self.path)
                if url.path == "/api/decks":
                    self._json([servico.resumo(deck) for deck in servico.listar()]); return
                match = re.fullmatch(r"/api/decks/([^/]+)", url.path)
                if match:
                    self._json(servico.detalhe(servico.obter(match.group(1)))); return
                match = re.fullmatch(r"/api/decks/([^/]+)/cartas/(\d+)", url.path)
                if match:
                    self._json(servico.carta(servico.obter(match.group(1)), match.group(2).zfill(3))); return
                if url.path == "/api/jobs":
                    self._json(servico.jobs.listar()); return
                match = re.fullmatch(r"/api/jobs/([a-f0-9]+)", url.path)
                if match:
                    self._json(servico.jobs.obter(match.group(1))); return
                if url.path == "/api/comfy":
                    deck = servico.listar()[0]
                    cliente = ComfyClient(deck.config["comfy_url"])
                    self._json({"system": cliente.json("/system_stats"), "queue": cliente.json("/queue")}); return
                if url.path == "/api/imagem":
                    self._imagem(urllib.parse.parse_qs(url.query)); return
                if url.path == "/api/folha-contato":
                    deck = servico.obter(urllib.parse.parse_qs(url.query).get("deck", [""])[0])
                    caminho = diretorios(deck.config)["raiz"] / "folha_contato.png"
                    if not caminho.is_file():
                        raise DixitError("Folha de contato ainda não existe.")
                    self._enviar(caminho.read_bytes(), "image/png"); return
                if url.path in ("/", "/index.html", "/app.js", "/estilo.css"):
                    nome = "index.html" if url.path == "/" else url.path.lstrip("/")
                    tipos = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "estilo.css": "text/css; charset=utf-8"}
                    caminho = estaticos / nome
                    if not caminho.is_file():
                        raise DixitError("Arquivos do painel não encontrados.")
                    self._enviar(caminho.read_bytes(), tipos[nome]); return
                self._json({"erro": "Não encontrado"}, 404)
            except (DixitError, KeyError, ValueError, IndexError) as erro:
                self._json({"erro": str(erro)}, 400)
            except Exception as erro:
                self._json({"erro": str(erro)}, 500)

        def _imagem(self, parametros: dict[str, list[str]]) -> None:
            deck = servico.obter(parametros.get("deck", [""])[0])
            identificador = parametros.get("id", [""])[0].zfill(3)
            if identificador not in {item.id for item in deck.conceitos}:
                raise DixitError("Carta inválida.")
            tipo = parametros.get("tipo", ["geradas"])[0]
            if tipo == "geradas":
                variacao = int(parametros.get("v", ["0"])[0])
                if not 1 <= variacao <= int(deck.config["variacoes"]):
                    raise DixitError("Variação inválida.")
                caminho = caminho_variacao(deck.config, identificador, variacao)
            elif tipo == "upscaled":
                caminho = caminho_upscale(deck.config, identificador)
            elif tipo == "impressao":
                caminho = diretorios(deck.config)["impressao"] / f"carta_{identificador}.png"
            else:
                raise DixitError("Tipo de imagem inválido.")
            if not caminho.is_file():
                raise DixitError("Imagem não encontrada.")
            self._enviar(caminho.read_bytes(), "image/png")

        def do_POST(self) -> None:
            try:
                url = urllib.parse.urlparse(self.path)
                dados = self._corpo()
                match = re.fullmatch(r"/api/decks/([^/]+)/acoes", url.path)
                if match:
                    deck = servico.obter(match.group(1))
                    self._json(servico.jobs.iniciar(deck, dados.get("acao", ""), dados.get("ids"), bool(dados.get("substituir"))), 202); return
                match = re.fullmatch(r"/api/decks/([^/]+)/cartas/(\d+)/curadoria", url.path)
                if match:
                    deck = servico.obter(match.group(1))
                    self._json(servico.curadoria(deck, match.group(2).zfill(3), dados.get("status", ""), dados.get("variacao"))); return
                match = re.fullmatch(r"/api/decks/([^/]+)/cartas/(\d+)/conceito", url.path)
                if match:
                    deck = servico.obter(match.group(1))
                    servico.editar_conceito(deck, match.group(2).zfill(3), dados.get("sujeito", ""), dados.get("arquetipo", ""))
                    self._json({"ok": True, "requer_regeracao": True}); return
                match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/parar", url.path)
                if match:
                    self._json(servico.jobs.parar(match.group(1))); return
                self._json({"erro": "Não encontrado"}, 404)
            except (DixitError, KeyError, ValueError, json.JSONDecodeError) as erro:
                self._json({"erro": str(erro)}, 400)
            except Exception as erro:
                self._json({"erro": str(erro)}, 500)

        def log_message(self, formato: str, *args: Any) -> None:
            return

    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
    endereco = f"http://127.0.0.1:{porta}"
    print(f"Painel Dixit disponível em {endereco}. Pressione Ctrl+C para encerrar.")
    if abrir_navegador:
        webbrowser.open(endereco)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nPainel encerrado.")
    finally:
        servidor.server_close()
