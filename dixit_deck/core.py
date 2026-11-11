from __future__ import annotations

import csv
import json
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ESTILO = (
    "surrealist illustration, dreamlike, whimsical storybook art, "
    "soft painterly brushwork, dominant golden yellow, cobalt blue and warm white palette, "
    "balanced yellow blue and white color blocking, symbolic composition, gentle volumetric light, subtle grain, no text"
)
NEGATIVO = (
    "text, watermark, logo, signature, photorealistic, 3d render, cgi, "
    "harsh lighting, oversaturated, blurry faces, extra limbs, low quality"
)
ARQUETIPOS_PREVISTOS = {
    "figura_humana_objeto_impossivel": 20,
    "animal_antropomorfizado": 15,
    "cenario_impossivel": 15,
    "natureza_transformada": 12,
    "objeto_com_vida": 12,
    "cena_emocional_ambigua": 10,
}
CONFIG_PADRAO = {
    "comfy_url": "http://127.0.0.1:8188",
    "checkpoint": "dreamshaper-xl-v2-turbo.safetensors",
    "lora": "surrealism.safetensors",
    "lora_peso": 0.8,
    "upscaler": "4x-UltraSharp.pth",
    "largura": 1024,
    "altura": 1024,
    "steps": 7,
    "cfg": 2.2,
    "sampler": "dpmpp_sde",
    "scheduler": "karras",
    "variacoes": 4,
    "seed_base": 104729,
    "timeout": 600,
    "saida": "saidas",
}


class DixitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Conceito:
    id: str
    arquetipo: str
    sujeito: str

    @property
    def prompt(self) -> str:
        return f"{self.sujeito}, {ESTILO}"


def carregar_config(caminho: str | Path) -> dict[str, Any]:
    arquivo = Path(caminho)
    try:
        with arquivo.open(encoding="utf-8") as stream:
            informado = json.load(stream)
    except FileNotFoundError as erro:
        raise DixitError(f"Configuração não encontrada: {arquivo}") from erro
    except json.JSONDecodeError as erro:
        raise DixitError(f"JSON inválido em {arquivo}: {erro}") from erro
    if not isinstance(informado, dict):
        raise DixitError("A configuração deve ser um objeto JSON.")
    config = CONFIG_PADRAO | informado
    if config["largura"] != 1024 or config["altura"] != 1024:
        raise DixitError("A geração deve usar 1024×1024 para manter o pipeline reproduzível.")
    if not 1 <= int(config["variacoes"]):
        raise DixitError("variacoes deve ser positivo.")
    if not 0.1 <= float(config["cfg"]) <= 5:
        raise DixitError("cfg fora da faixa segura para o modelo turbo.")
    if not 1 <= int(config["steps"]) <= 50:
        raise DixitError("steps deve estar entre 1 e 50.")
    if not 0 <= float(config["lora_peso"]) <= 2:
        raise DixitError("lora_peso deve estar entre 0 e 2.")
    if not str(config["checkpoint"]).strip() or not str(config["lora"]).strip():
        raise DixitError("Configure checkpoint e lora com os nomes exatos dos arquivos no ComfyUI.")
    return config


def ler_conceitos(caminho: str | Path) -> list[Conceito]:
    arquivo = Path(caminho)
    try:
        stream = arquivo.open(encoding="utf-8", newline="")
    except FileNotFoundError as erro:
        raise DixitError(f"Arquivo de conceitos não encontrado: {arquivo}") from erro
    conceitos: list[Conceito] = []
    with stream:
        for numero, linha in enumerate(csv.reader(stream, delimiter=";"), 1):
            if not linha or not any(campo.strip() for campo in linha):
                continue
            if len(linha) < 3:
                raise DixitError(f"{arquivo}:{numero}: esperado id;arquetipo;sujeito_cena")
            identificador, arquetipo, sujeito = (campo.strip() for campo in linha[:3])
            if not identificador.isdigit() or not arquetipo or not sujeito:
                raise DixitError(f"{arquivo}:{numero}: id numérico, arquétipo e sujeito são obrigatórios")
            conceitos.append(Conceito(identificador.zfill(3), arquetipo, sujeito))
    ids = [conceito.id for conceito in conceitos]
    if len(ids) != len(set(ids)):
        raise DixitError("Há IDs duplicados no arquivo de conceitos.")
    return conceitos


def mapa_conceitos(conceitos: list[Conceito]) -> dict[str, Conceito]:
    return {conceito.id: conceito for conceito in conceitos}


def seed_para(config: dict[str, Any], identificador: str, variacao: int) -> int:
    return int(config["seed_base"]) + (int(identificador) - 1) * int(config["variacoes"]) + variacao - 1


def diretorios(config: dict[str, Any]) -> dict[str, Path]:
    raiz = Path(config["saida"])
    return {
        "raiz": raiz,
        "geradas": raiz / "geradas",
        "upscaled": raiz / "upscaled",
        "impressao": raiz / "impressao",
    }


def caminho_variacao(config: dict[str, Any], identificador: str, variacao: int) -> Path:
    return diretorios(config)["geradas"] / f"carta_{identificador}_{variacao}.png"


def caminho_upscale(config: dict[str, Any], identificador: str) -> Path:
    return diretorios(config)["upscaled"] / f"carta_{identificador}.png"


def escrever_json(caminho: Path, dados: Any) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporario, caminho)


def ler_json(caminho: Path, padrao: Any) -> Any:
    try:
        with caminho.open(encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return padrao
    except json.JSONDecodeError as erro:
        raise DixitError(f"JSON inválido em {caminho}: {erro}") from erro


def dimensoes_png(conteudo: bytes) -> tuple[int, int]:
    if len(conteudo) < 24 or conteudo[:8] != b"\x89PNG\r\n\x1a\n" or conteudo[12:16] != b"IHDR":
        raise DixitError("O ComfyUI retornou um arquivo que não é um PNG válido.")
    return struct.unpack(">II", conteudo[16:24])


def adicionar_metadados_png(conteudo: bytes, metadados: dict[str, Any]) -> bytes:
    dimensoes_png(conteudo)
    texto = json.dumps(metadados, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    dados = b"dixit\0\0\0\0\0" + texto
    bloco = b"iTXt" + dados
    chunk = struct.pack(">I", len(dados)) + bloco + struct.pack(">I", zlib.crc32(bloco) & 0xFFFFFFFF)
    fim = conteudo.rfind(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    if fim < 0:
        raise DixitError("PNG sem bloco IEND.")
    return conteudo[:fim] + chunk + conteudo[fim:]


def ler_metadados_png(caminho: Path) -> dict[str, Any]:
    conteudo = caminho.read_bytes()
    dimensoes_png(conteudo)
    posicao = 8
    while posicao + 12 <= len(conteudo):
        tamanho = struct.unpack(">I", conteudo[posicao:posicao + 4])[0]
        tipo = conteudo[posicao + 4:posicao + 8]
        dados = conteudo[posicao + 8:posicao + 8 + tamanho]
        posicao += tamanho + 12
        if tipo not in (b"iTXt", b"tEXt"):
            continue
        palavra, separador, resto = dados.partition(b"\0")
        if not separador or palavra != b"dixit":
            continue
        try:
            if tipo == b"tEXt":
                return json.loads(resto.decode("latin-1"))
            comprimido = resto[0] == 1
            resto = resto[2:]
            _idioma, _sep, resto = resto.partition(b"\0")
            _traducao, _sep, texto = resto.partition(b"\0")
            if comprimido:
                texto = zlib.decompress(texto)
            return json.loads(texto.decode("utf-8"))
        except (ValueError, IndexError, json.JSONDecodeError, zlib.error) as erro:
            raise DixitError(f"Metadados dixit inválidos em {caminho}") from erro
    raise DixitError(f"Metadados dixit ausentes em {caminho}")


class ComfyClient:
    def __init__(self, url: str, timeout: int = 600):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _requisicao(self, requisicao: urllib.request.Request) -> bytes:
        try:
            with urllib.request.urlopen(requisicao, timeout=30) as resposta:
                return resposta.read()
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", errors="replace")
            raise DixitError(f"ComfyUI respondeu HTTP {erro.code}: {detalhe[:1000]}") from erro
        except urllib.error.URLError as erro:
            raise DixitError(f"Não foi possível acessar {self.url}: {erro.reason}") from erro

    def json(self, rota: str) -> Any:
        conteudo = self._requisicao(urllib.request.Request(self.url + rota))
        try:
            return json.loads(conteudo)
        except json.JSONDecodeError as erro:
            raise DixitError(f"Resposta JSON inválida em {rota}") from erro

    def modelos(self, pasta: str) -> list[str]:
        dados = self.json("/models/" + urllib.parse.quote(pasta))
        if not isinstance(dados, list):
            raise DixitError(f"Resposta inesperada ao listar modelos de {pasta}.")
        return dados

    def validar_modelo(self, pasta: str, nome: str) -> None:
        modelos = self.modelos(pasta)
        if nome not in modelos:
            amostra = ", ".join(modelos[:12]) or "nenhum"
            raise DixitError(f"Modelo '{nome}' não encontrado em {pasta}. Disponíveis: {amostra}")

    def enviar(self, workflow: dict[str, Any]) -> str:
        corpo = json.dumps({"prompt": workflow, "client_id": str(uuid.uuid4())}).encode("utf-8")
        requisicao = urllib.request.Request(
            self.url + "/prompt", data=corpo, headers={"Content-Type": "application/json"}
        )
        try:
            dados = json.loads(self._requisicao(requisicao))
        except json.JSONDecodeError as erro:
            raise DixitError("Resposta inválida do endpoint /prompt.") from erro
        if "prompt_id" not in dados:
            raise DixitError(f"ComfyUI recusou o workflow: {dados}")
        return dados["prompt_id"]

    def esperar(self, prompt_id: str) -> dict[str, Any]:
        inicio = time.monotonic()
        while time.monotonic() - inicio < self.timeout:
            historico = self.json("/history/" + urllib.parse.quote(prompt_id))
            if prompt_id in historico:
                item = historico[prompt_id]
                status = item.get("status", {})
                if status.get("status_str") == "error":
                    raise DixitError("Erro no ComfyUI: " + json.dumps(status, ensure_ascii=False)[:2000])
                if status.get("completed") or item.get("outputs"):
                    return item
            time.sleep(1)
        raise DixitError(f"Tempo limite de {self.timeout}s excedido para {prompt_id}.")

    def baixar_primeira_imagem(self, historico: dict[str, Any]) -> bytes:
        for saida in historico.get("outputs", {}).values():
            for imagem in saida.get("images", []):
                consulta = urllib.parse.urlencode({
                    "filename": imagem["filename"],
                    "subfolder": imagem.get("subfolder", ""),
                    "type": imagem.get("type", "output"),
                })
                return self._requisicao(urllib.request.Request(self.url + "/view?" + consulta))
        raise DixitError("O workflow terminou sem imagem de saída.")

    def executar_imagem(self, workflow: dict[str, Any]) -> bytes:
        prompt_id = self.enviar(workflow)
        return self.baixar_primeira_imagem(self.esperar(prompt_id))

    def upload_imagem(self, caminho: Path) -> str:
        limite = uuid.uuid4().hex
        nome = caminho.name
        partes = []
        for campo, valor in (("overwrite", "true"),):
            partes.extend([
                f"--{limite}\r\nContent-Disposition: form-data; name=\"{campo}\"\r\n\r\n{valor}\r\n".encode(),
            ])
        partes.extend([
            f"--{limite}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{nome}\"\r\nContent-Type: image/png\r\n\r\n".encode(),
            caminho.read_bytes(),
            b"\r\n",
            f"--{limite}--\r\n".encode(),
        ])
        requisicao = urllib.request.Request(
            self.url + "/upload/image",
            data=b"".join(partes),
            headers={"Content-Type": f"multipart/form-data; boundary={limite}"},
        )
        try:
            dados = json.loads(self._requisicao(requisicao))
        except json.JSONDecodeError as erro:
            raise DixitError("Resposta inválida ao enviar imagem.") from erro
        if not dados.get("name"):
            raise DixitError(f"Upload recusado pelo ComfyUI: {dados}")
        subpasta = dados.get("subfolder", "")
        return f"{subpasta}/{dados['name']}" if subpasta else dados["name"]
