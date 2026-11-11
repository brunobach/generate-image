from __future__ import annotations

import json
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .core import (
    Conceito,
    DixitError,
    caminho_variacao,
    diretorios,
    escrever_json,
)
from .pipeline import carregar_selecoes

PAGINA = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curadoria — Deck Dixit</title>
<style>
:root{color-scheme:dark;--bg:#151918;--panel:#202725;--line:#394541;--accent:#d3aa68;--teal:#75aaa2;--danger:#cb8178}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#eee;font:16px system-ui,sans-serif}
header{position:sticky;top:0;z-index:2;background:#151918f2;border-bottom:1px solid var(--line);padding:16px 24px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}
h1{font-size:20px;margin:0}header p{margin:0;color:#b9c2be}main{max-width:1500px;margin:auto;padding:24px}.contagem{font-weight:650;color:var(--accent)}
.carta{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:24px}.carta.aprovada{border-color:var(--teal)}.carta.reprovada{border-color:var(--danger)}
.titulo{display:flex;align-items:start;gap:16px;justify-content:space-between;margin-bottom:14px}.titulo h2{font-size:17px;margin:0 0 5px}.titulo p{margin:0;color:#b9c2be;line-height:1.45}.estado{white-space:nowrap;font-size:13px;color:var(--accent)}
.candidatas{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.candidata{min-width:0}.candidata img{display:block;width:100%;aspect-ratio:1;object-fit:cover;border-radius:7px;background:#101312}.candidata button,.rejeitar{width:100%;margin-top:8px;padding:9px;border:1px solid var(--line);border-radius:6px;background:#2a3431;color:#eee;cursor:pointer}.candidata button:hover{border-color:var(--teal)}.candidata button.escolhida{background:var(--teal);color:#10201d;font-weight:700}.rejeitar{max-width:240px;color:#efaaa2}.ausente{display:grid;place-items:center;aspect-ratio:1;background:#101312;border-radius:7px;color:#89938f}
@media(max-width:900px){.candidatas{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:480px){main{padding:12px}.candidatas{grid-template-columns:1fr}.titulo{display:block}.estado{display:block;margin-top:8px}}
</style>
</head>
<body>
<header><h1>Curadoria do deck</h1><p class="contagem" id="contagem"></p><p>Escolha uma candidata por conceito ou reprove o conceito para corrigir o prompt.</p></header>
<main id="deck"></main>
<script>
const conceitos = __CONCEITOS__;
let selecoes = __SELECOES__;
const total = conceitos.length;
function elemento(tag, classe, texto) {
  const no = document.createElement(tag);
  if (classe) no.className = classe;
  if (texto !== undefined) no.textContent = texto;
  return no;
}
function atualizarContagem() {
  const valores = Object.values(selecoes);
  const aprovadas = valores.filter(x => x.status === 'aprovada').length;
  const reprovadas = valores.filter(x => x.status === 'reprovada').length;
  document.getElementById('contagem').textContent = `${aprovadas}/${total} aprovadas · ${reprovadas} para correção`;
}
function desenhar() {
  const deck = document.getElementById('deck');
  deck.replaceChildren();
  for (const conceito of conceitos) {
    const selecao = selecoes[conceito.id] || {};
    const carta = elemento('section', `carta ${selecao.status || ''}`);
    const titulo = elemento('div', 'titulo');
    const descricao = elemento('div');
    descricao.append(elemento('h2', '', `Carta ${conceito.id} · ${conceito.arquetipo}`));
    descricao.append(elemento('p', '', conceito.sujeito));
    titulo.append(descricao, elemento('span', 'estado', selecao.status === 'aprovada' ? `Aprovada: variação ${selecao.variacao}` : selecao.status === 'reprovada' ? 'Aguardando correção de prompt' : 'Pendente'));
    carta.append(titulo);
    const grade = elemento('div', 'candidatas');
    for (const candidata of conceito.candidatas) {
      const bloco = elemento('div', 'candidata');
      if (candidata.existe) {
        const imagem = elemento('img');
        imagem.src = candidata.url;
        imagem.alt = `Carta ${conceito.id}, variação ${candidata.variacao}`;
        imagem.loading = 'lazy';
        bloco.append(imagem);
      } else {
        bloco.append(elemento('div', 'ausente', 'Ainda não gerada'));
      }
      const botao = elemento('button', selecao.variacao === candidata.variacao && selecao.status === 'aprovada' ? 'escolhida' : '', `Escolher variação ${candidata.variacao}`);
      botao.disabled = !candidata.existe;
      botao.onclick = () => salvar(conceito.id, 'aprovada', candidata.variacao);
      bloco.append(botao);
      grade.append(bloco);
    }
    carta.append(grade);
    const rejeitar = elemento('button', 'rejeitar', 'Reprovar e corrigir prompt');
    rejeitar.onclick = () => salvar(conceito.id, 'reprovada', null);
    carta.append(rejeitar);
    deck.append(carta);
  }
  atualizarContagem();
}
async function salvar(id, status, variacao) {
  const resposta = await fetch('/selecao', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,status,variacao})});
  if (!resposta.ok) { alert(await resposta.text()); return; }
  selecoes = await resposta.json();
  desenhar();
}
desenhar();
</script>
</body>
</html>
"""


def curar(
    config: dict[str, Any], conceitos: list[Conceito], porta: int, abrir_navegador: bool = True
) -> None:
    caminho_selecoes = diretorios(config)["raiz"] / "curadoria.json"
    variacoes = int(config["variacoes"])
    ids = {conceito.id for conceito in conceitos}
    bloqueio = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _responder(self, status: int, conteudo: bytes, tipo: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(conteudo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(conteudo)

        def _json(self, dados: Any, status: int = 200) -> None:
            self._responder(status, json.dumps(dados, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            url = urllib.parse.urlparse(self.path)
            if url.path == "/":
                candidatas = []
                for conceito in conceitos:
                    opcoes = []
                    for variacao in range(1, variacoes + 1):
                        caminho = caminho_variacao(config, conceito.id, variacao)
                        existe = caminho.is_file()
                        versao = caminho.stat().st_mtime_ns if existe else 0
                        opcoes.append({
                            "variacao": variacao,
                            "existe": existe,
                            "url": f"/imagem?id={conceito.id}&v={variacao}&versao={versao}",
                        })
                    candidatas.append({
                        "id": conceito.id,
                        "arquetipo": conceito.arquetipo,
                        "sujeito": conceito.sujeito,
                        "candidatas": opcoes,
                    })
                with bloqueio:
                    selecoes = carregar_selecoes(config)
                pagina = PAGINA.replace(
                    "__CONCEITOS__", json.dumps(candidatas, ensure_ascii=False).replace("</", "<\\/")
                ).replace(
                    "__SELECOES__", json.dumps(selecoes, ensure_ascii=False).replace("</", "<\\/")
                )
                self._responder(200, pagina.encode("utf-8"), "text/html; charset=utf-8")
                return
            if url.path == "/imagem":
                parametros = urllib.parse.parse_qs(url.query)
                identificador = parametros.get("id", [""])[0]
                try:
                    variacao = int(parametros.get("v", ["0"])[0])
                except ValueError:
                    variacao = 0
                if identificador not in ids or not 1 <= variacao <= variacoes:
                    self._responder(404, b"Candidata invalida", "text/plain; charset=utf-8")
                    return
                caminho = caminho_variacao(config, identificador, variacao)
                if not caminho.is_file():
                    self._responder(404, b"Candidata nao encontrada", "text/plain; charset=utf-8")
                    return
                self._responder(200, caminho.read_bytes(), "image/png")
                return
            self._responder(404, b"Nao encontrado", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            if self.path != "/selecao":
                self._responder(404, b"Nao encontrado", "text/plain; charset=utf-8")
                return
            try:
                tamanho = int(self.headers.get("Content-Length", "0"))
                if not 0 < tamanho <= 10000:
                    raise DixitError("Requisição inválida.")
                dados = json.loads(self.rfile.read(tamanho))
                identificador = dados["id"]
                status = dados["status"]
                if identificador not in ids or status not in ("aprovada", "reprovada"):
                    raise DixitError("Carta ou status inválido.")
                with bloqueio:
                    selecoes = carregar_selecoes(config)
                    selecao = {"status": status, "atualizado_em": datetime.now(timezone.utc).isoformat()}
                    if status == "aprovada":
                        variacao = dados.get("variacao")
                        if not isinstance(variacao, int) or not 1 <= variacao <= variacoes:
                            raise DixitError("Variação inválida.")
                        if not caminho_variacao(config, identificador, variacao).is_file():
                            raise DixitError("A candidata selecionada não existe.")
                        selecao["variacao"] = variacao
                    selecoes[identificador] = selecao
                    escrever_json(caminho_selecoes, selecoes)
                self._json(selecoes)
            except (DixitError, KeyError, ValueError, json.JSONDecodeError) as erro:
                self._responder(400, str(erro).encode("utf-8"), "text/plain; charset=utf-8")

        def log_message(self, formato: str, *args: Any) -> None:
            print(f"Curadoria: {formato % args}")

    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
    endereco = f"http://127.0.0.1:{porta}"
    print(f"Curadoria disponível em {endereco}. Pressione Ctrl+C para encerrar.")
    if abrir_navegador:
        webbrowser.open(endereco)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nCuradoria encerrada; as escolhas estão salvas.")
    finally:
        servidor.server_close()
