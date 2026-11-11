const estado = { decks: [], atual: null, carta: null, jobLogs: null };
const $ = id => document.getElementById(id);
const esc = valor => String(valor ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const etiquetas = {geracao:'Geração',curadoria:'Curadoria',exportacao:'Exportação',concluido:'Concluído',pendente:'Pendente',aprovada:'Aprovada',reprovada:'Reprovada',executando:'Executando',erro:'Erro',parado:'Parado'};

async function api(caminho, opcoes) {
  const resposta = await fetch(caminho, opcoes);
  const tipo = resposta.headers.get('content-type') || '';
  const dados = tipo.includes('json') ? await resposta.json() : await resposta.text();
  if (!resposta.ok) throw new Error(dados.erro || dados || `HTTP ${resposta.status}`);
  return dados;
}

function post(caminho, dados = {}) {
  return api(caminho, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(dados)});
}

function tempo(segundos) {
  if (segundos === null || segundos === undefined) return '—';
  const valor = Math.max(0, Math.round(segundos));
  if (valor < 60) return `${valor}s`;
  if (valor < 3600) return `${Math.floor(valor / 60)}min ${valor % 60}s`;
  return `${Math.floor(valor / 3600)}h ${Math.floor((valor % 3600) / 60)}min`;
}

function toast(mensagem, erro = false) {
  const elemento = $('toast');
  elemento.textContent = mensagem;
  elemento.className = `toast${erro ? ' erro' : ''}`;
  elemento.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => elemento.hidden = true, erro ? 7000 : 3500);
}

function barra(atual, total) {
  const porcentagem = total ? Math.min(100, atual / total * 100) : 0;
  return `<div class="progresso"><i style="width:${porcentagem}%"></i></div><div class="progresso-info"><span>${atual} de ${total}</span><span>${Math.round(porcentagem)}%</span></div>`;
}

function renderDecks() {
  const destino = $('visao-decks');
  if (!estado.decks.length) {
    destino.innerHTML = '<p>Nenhum arquivo config*.json válido encontrado.</p>';
    return;
  }
  destino.innerHTML = `<div class="decks">${estado.decks.map(deck => `
    <button class="deck-card" data-deck="${esc(deck.id)}">
      <div class="deck-card-top"><div><h2>${esc(deck.nome)}</h2><p>${deck.total_cartas} cartas · ${deck.variacoes_por_carta} variações por conceito</p></div><span class="badge ${deck.etapa}">${etiquetas[deck.etapa] || deck.etapa}</span></div>
      ${barra(deck.candidatas, deck.candidatas_esperadas)}
      <div class="deck-numeros">
        <div><strong>${deck.aprovadas}</strong><small>aprovadas</small></div>
        <div><strong>${deck.pendentes}</strong><small>pendentes</small></div>
        <div><strong>${deck.upscales}</strong><small>upscales</small></div>
        <div><strong>${deck.impressoes}</strong><small>impressão</small></div>
      </div>
    </button>`).join('')}</div>`;
  destino.querySelectorAll('[data-deck]').forEach(botao => botao.onclick = () => abrirDeck(botao.dataset.deck));
}

function renderMetricas(deck) {
  const metricas = [
    [deck.total_cartas, 'conceitos'],
    [`${deck.candidatas}/${deck.candidatas_esperadas}`, 'candidatas geradas'],
    [`${deck.aprovadas}/${deck.total_cartas}`, 'cartas aprovadas'],
    [`${deck.upscales}/${deck.total_cartas}`, 'upscales 4096²'],
    [tempo(deck.tempo_decorrido_segundos), 'intervalo de geração'],
  ];
  $('metricas').innerHTML = metricas.map(([valor, nome]) => `<div class="metrica"><strong>${valor}</strong><small>${nome}</small></div>`).join('');
}

function imagemCarta(carta) {
  const variacao = carta.variacao_aprovada || carta.candidatas.find(item => item.existe)?.variacao;
  if (!variacao) return '<div class="sem-imagem">✧</div>';
  const opcao = carta.candidatas.find(item => item.variacao === variacao);
  return `<img loading="lazy" src="/api/imagem?deck=${encodeURIComponent(estado.atual.deck.id)}&id=${carta.id}&v=${variacao}&versao=${opcao?.versao || 0}" alt="Carta ${carta.id}">`;
}

function renderCartas() {
  if (!estado.atual) return;
  const busca = $('busca').value.trim().toLocaleLowerCase();
  const status = $('filtro-status').value;
  const arquetipo = $('filtro-arquetipo').value;
  const cartas = estado.atual.cartas.filter(carta =>
    (!busca || carta.sujeito.toLocaleLowerCase().includes(busca) || carta.id.includes(busca)) &&
    (!status || carta.status === status) && (!arquetipo || carta.arquetipo === arquetipo)
  );
  $('resultado-filtro').textContent = `${cartas.length} cartas`;
  $('grade-cartas').innerHTML = cartas.map(carta => `
    <article class="carta ${carta.geradas ? '' : 'incompleta'}" data-carta="${carta.id}">
      <div class="thumb">${imagemCarta(carta)}<span class="badge ${carta.status}">${etiquetas[carta.status]}</span></div>
      <div class="carta-info"><strong>CARTA ${carta.id}</strong><p>${esc(carta.sujeito)}</p><div class="carta-meta"><span>${carta.geradas}/${estado.atual.deck.variacoes_por_carta} candidatas</span><span title="Tempo estimado pelas gravações; o detalhe mostra medições exatas quando disponíveis">${tempo(carta.tempo_total)}</span></div></div>
    </article>`).join('') || '<p>Nenhuma carta corresponde aos filtros.</p>';
  $('grade-cartas').querySelectorAll('[data-carta]').forEach(elemento => elemento.onclick = () => abrirCarta(elemento.dataset.carta));
}

function renderDeck() {
  const {deck} = estado.atual;
  $('deck-nome').textContent = deck.nome;
  $('deck-resumo').textContent = `${deck.total_cartas} conceitos · ${deck.reprovadas} aguardando correção · ${deck.impressoes} prontas para impressão`;
  $('folha').hidden = !deck.folha_contato;
  $('folha').onclick = () => window.open(`/api/folha-contato?deck=${encodeURIComponent(deck.id)}`, '_blank');
  document.querySelectorAll('[data-acao]').forEach(botao => botao.disabled = deck.ocupado);
  const processos = deck.processos_externos.map(p => `${p.acao} (PID ${p.pid})`).join(', ');
  const aviso = $('aviso-processo');
  aviso.hidden = !deck.ocupado;
  aviso.textContent = deck.job_ativo ? `Ação ${deck.job_ativo.acao} em execução pelo painel.` : processos ? `Processo externo detectado: ${processos}. As ações foram bloqueadas para evitar duplicação.` : '';
  renderMetricas(deck);
  const seletor = $('filtro-arquetipo');
  const anterior = seletor.value;
  const arquetipos = [...new Set(estado.atual.cartas.map(c => c.arquetipo))].sort();
  seletor.innerHTML = '<option value="">Todos os arquétipos</option>' + arquetipos.map(a => `<option value="${a}">${a.replaceAll('_', ' ')}</option>`).join('');
  seletor.value = anterior;
  renderCartas();
}

async function carregarDeck(id) {
  estado.atual = await api(`/api/decks/${encodeURIComponent(id)}`);
  renderDeck();
}

async function abrirDeck(id) {
  await carregarDeck(id);
  $('titulo').textContent = estado.atual.deck.nome;
  $('subtitulo').textContent = 'Conceitos, curadoria e produção';
  $('visao-decks').hidden = true;
  $('visao-deck').hidden = false;
}

function mostrarDecks() {
  estado.atual = null;
  $('visao-decks').hidden = false;
  $('visao-deck').hidden = true;
  $('titulo').textContent = 'Meus decks';
  $('subtitulo').textContent = 'Acompanhe conceitos, candidatas e impressão.';
}

async function abrirCarta(id) {
  if (!estado.atual) return;
  try {
    const detalhe = await api(`/api/decks/${encodeURIComponent(estado.atual.deck.id)}/cartas/${id}`);
    estado.carta = detalhe;
    $('modal-id').textContent = `CARTA ${id}`;
    renderModal();
    $('modal-carta').showModal();
  } catch (erro) { toast(erro.message, true); }
}

function renderModal() {
  const detalhe = estado.carta;
  const base = estado.atual.cartas.find(c => c.id === detalhe.conceito.id);
  const aprovada = base?.variacao_aprovada;
  const arquetipos = [...new Set(estado.atual.cartas.map(c => c.arquetipo))].sort();
  $('modal-conteudo').innerHTML = `
    <div class="editor">
      <label for="editar-sujeito">CONCEITO EM INGLÊS</label>
      <textarea id="editar-sujeito">${esc(detalhe.conceito.sujeito)}</textarea>
      <div class="editor-linha"><select id="editar-arquetipo">${arquetipos.map(a => `<option value="${a}" ${a === detalhe.conceito.arquetipo ? 'selected' : ''}>${a.replaceAll('_', ' ')}</option>`).join('')}</select><button class="botao" id="salvar-conceito">Salvar conceito</button></div>
    </div>
    <div class="candidatas-modal">${detalhe.candidatas.map(item => {
      if (!item.existe) return `<div class="candidata-modal"><div class="sem-imagem">Variação ${item.variacao} pendente</div></div>`;
      return `<div class="candidata-modal ${aprovada === item.variacao ? 'escolhida' : ''}">
        <div class="variacao-titulo">VARIAÇÃO ${item.variacao}</div>
        <img src="/api/imagem?deck=${encodeURIComponent(estado.atual.deck.id)}&id=${detalhe.conceito.id}&v=${item.variacao}&versao=${Math.floor((item.mtime || 0) * 1000)}" alt="Variação ${item.variacao}">
        <div class="dados">SEED ${item.seed ?? '—'}<br>TEMPO ${tempo(item.segundos)}${item.estimado === false ? '' : ' (estimado)'}<br>${((item.tamanho_bytes || 0) / 1048576).toFixed(1)} MB</div>
        <details class="prompt-variacao"><summary>Prompt utilizado</summary><p>${esc(item.prompt || 'Prompt não disponível nos metadados.')}</p></details>
        <button class="botao ${aprovada === item.variacao ? 'primario' : ''}" data-aprovar="${item.variacao}">${aprovada === item.variacao ? 'Selecionada' : 'Aprovar variação'}</button>
      </div>`;
    }).join('')}</div>
    <div class="modal-rodape"><button class="botao perigo" id="reprovar-carta">Reprovar conceito</button><button class="botao" id="regerar-carta">Regerar esta carta</button></div>`;
  $('modal-conteudo').querySelectorAll('[data-aprovar]').forEach(botao => botao.onclick = () => aprovar(Number(botao.dataset.aprovar)));
  $('reprovar-carta').onclick = reprovar;
  $('regerar-carta').onclick = regerarCarta;
  $('salvar-conceito').onclick = salvarConceito;
}

async function aprovar(variacao) {
  const id = estado.carta.conceito.id;
  try {
    await post(`/api/decks/${encodeURIComponent(estado.atual.deck.id)}/cartas/${id}/curadoria`, {status:'aprovada', variacao});
    $('modal-carta').close();
    await carregarDeck(estado.atual.deck.id);
    toast(`Carta ${id} aprovada.`);
  } catch (erro) { toast(erro.message, true); }
}

async function reprovar() {
  const id = estado.carta.conceito.id;
  try {
    await post(`/api/decks/${encodeURIComponent(estado.atual.deck.id)}/cartas/${id}/curadoria`, {status:'reprovada', variacao:null});
    $('modal-carta').close();
    await carregarDeck(estado.atual.deck.id);
    toast(`Carta ${id} marcada para correção.`);
  } catch (erro) { toast(erro.message, true); }
}

async function salvarConceito() {
  const id = estado.carta.conceito.id;
  try {
    await post(`/api/decks/${encodeURIComponent(estado.atual.deck.id)}/cartas/${id}/conceito`, {sujeito:$('editar-sujeito').value, arquetipo:$('editar-arquetipo').value});
    await carregarDeck(estado.atual.deck.id);
    $('modal-carta').close();
    toast('Conceito salvo. Use “Regerar esta carta” para atualizar as candidatas existentes.');
  } catch (erro) { toast(erro.message, true); }
}

async function regerarCarta() {
  const id = estado.carta.conceito.id;
  if (!confirm(`Substituir as quatro candidatas da carta ${id}?`)) return;
  try {
    await post(`/api/decks/${encodeURIComponent(estado.atual.deck.id)}/acoes`, {acao:'gerar', ids:id, substituir:true});
    $('modal-carta').close();
    await atualizarTudo();
    toast(`Regeração da carta ${id} iniciada.`);
  } catch (erro) { toast(erro.message, true); }
}

async function executarAcao(acao) {
  if (!estado.atual) return;
  try {
    await post(`/api/decks/${encodeURIComponent(estado.atual.deck.id)}/acoes`, {acao});
    toast(`Ação ${acao} iniciada.`);
    await atualizarTudo();
  } catch (erro) { toast(erro.message, true); }
}

async function renderJobs() {
  const jobs = await api('/api/jobs');
  $('jobs-resumo').textContent = jobs.length ? `${jobs.filter(j => j.status === 'executando').length} executando · ${jobs.length} no histórico` : 'Nenhum job iniciado pelo painel';
  $('jobs').innerHTML = jobs.map(job => `<div class="job"><span class="badge ${job.status}">${etiquetas[job.status] || job.status}</span><span>${esc(job.deck_nome)}</span><span>${esc(job.acao)}</span><span>${tempo(job.duracao)}</span><span><button data-log="${job.id}">Log</button>${job.status === 'executando' ? ` <button class="parar" data-parar="${job.id}">Parar</button>` : ''}</span></div>`).join('');
  $('jobs').querySelectorAll('[data-log]').forEach(botao => botao.onclick = () => verLogs(botao.dataset.log));
  $('jobs').querySelectorAll('[data-parar]').forEach(botao => botao.onclick = () => pararJob(botao.dataset.parar));
}

async function verLogs(id) {
  try {
    const job = await api(`/api/jobs/${id}`);
    estado.jobLogs = id;
    $('logs').hidden = false;
    $('logs').textContent = `${job.deck_nome} · ${job.acao} · ${job.status}\n\n` + (job.logs.map(l => l.texto).join('\n') || 'Aguardando saída…');
    $('logs').scrollTop = $('logs').scrollHeight;
  } catch (erro) { toast(erro.message, true); }
}

async function pararJob(id) {
  if (!confirm('Interromper este processo? A última imagem incompleta poderá precisar ser regerada.')) return;
  try { await post(`/api/jobs/${id}/parar`); toast('Solicitação de interrupção enviada.'); await renderJobs(); }
  catch (erro) { toast(erro.message, true); }
}

async function atualizarComfy() {
  try {
    const dados = await api('/api/comfy');
    const dispositivo = dados.system.devices?.[0]?.name || 'ComfyUI online';
    $('comfy-texto').textContent = dispositivo.replace('cuda:0 ', '').split(': cudaMallocAsync')[0];
    $('comfy-fila').textContent = `${dados.queue.queue_running?.length || 0} executando · ${dados.queue.queue_pending?.length || 0} na fila`;
    $('comfy-luz').className = 'online';
  } catch {
    $('comfy-texto').textContent = 'ComfyUI offline';
    $('comfy-fila').textContent = '';
    $('comfy-luz').className = 'offline';
  }
}

async function atualizarTudo() {
  try {
    estado.decks = await api('/api/decks');
    renderDecks();
    await renderJobs();
    if (estado.atual && !$('modal-carta').open) await carregarDeck(estado.atual.deck.id);
    if (estado.jobLogs) await verLogs(estado.jobLogs);
  } catch (erro) { toast(erro.message, true); }
  atualizarComfy();
}

$('voltar').onclick = mostrarDecks;
$('nav-decks').onclick = mostrarDecks;
$('atualizar').onclick = atualizarTudo;
$('fechar-modal').onclick = () => $('modal-carta').close();
$('busca').oninput = renderCartas;
$('filtro-status').onchange = renderCartas;
$('filtro-arquetipo').onchange = renderCartas;
document.querySelectorAll('[data-acao]').forEach(botao => botao.onclick = () => executarAcao(botao.dataset.acao));
atualizarTudo();
setInterval(atualizarTudo, 4000);
