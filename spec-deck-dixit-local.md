# Spec — Geração Local de Deck para Dixit

**Versão:** 1.0
**Hardware alvo:** RTX 5060 8 GB (Blackwell, sm_120)
**Stack:** ComfyUI + SDXL (DreamShaper XL Turbo) + LoRA de estilo + upscale seletivo
**Entregável:** 84 cartas ilustradas em alta resolução, prontas para impressão

---

## 1. Objetivo

Gerar localmente um deck completo de cartas no estilo Dixit: ilustrações surrealistas, sonhadoras, com pintura suave e composição simbólica — onde cada imagem sugestiona múltiplas interpretações, sem ser literal demais nem abstrata demais.

### Princípio central do Dixit (define o critério de aceite)

Uma boa carta é **evocativa, não descritiva**. Ela deve:

- Poder ser descrita por mais de uma frase ("parece uma despedida", "parece liberdade", "parece uma armadilha bonita")
- Não conter texto, logotipos ou rostos frontais claros (evita "a carta do homem do chapéu")
- Misturar o familiar com o impossível (escada que sobe pro céu, criança carregando a lua, peixe voando sobre cidade)

---

## 2. Stack e Setup

### 2.1 Ambiente

| Componente | Versão / Nota |
|---|---|
| ComfyUI | Última estável |
| PyTorch | build CUDA 12.8+ (**obrigatório** — builds antigos não reconhecem Blackwell) |
| Checkpoint | `Lykon/dreamshaper-xl-v2-turbo` (FP16) |
| LoRA de estilo | Surrealismo/pintura de livro infantil (Civitai: "surrealism", "storybook", "whimsical illustration"), peso 0.7–0.9 |
| Upscaler | 4x-UltraSharp (ou Ultimate SD Upscale nas selecionadas) |

### 2.2 Parâmetros de geração (base)

```
Tamanho:        1024 × 1024 (carta quadrada — formato nativo do SDXL)
Sampler:        DPM++ SDE Karras (ou o sampler turbo recomendado no model card)
Steps:          6–8 (modelo turbo)
CFG:            2.0–2.5 (turbo exige CFG baixo; CFG alto quebra e satura)
Batch:          4 imagens por conceito
VRAM esperada:  ~7 GB em 1024×1024 — dentro do orçamento de 8 GB
```

> **Nota sobre CFG:** em modelo turbo o CFG é baixo por design. A suavidade "sonhadora" do Dixit se beneficia disso — não tente subir CFG pra "melhorar detalhe", o efeito é o contrário.

---

## 3. Sistema de Prompts

### 3.1 Template

```
{sujeito_cena}, surrealist illustration, dreamlike, whimsical storybook art,
soft painterly brushwork, dominant golden yellow, cobalt blue and warm white palette,
balanced yellow blue and white color blocking, symbolic composition, gentle volumetric light, subtle grain, no text
```

**Negative prompt fixo:**

```
text, watermark, logo, signature, photorealistic, 3d render, cgi,
harsh lighting, oversaturated, blurry faces, extra limbs, low quality
```

### 3.2 Estratégia de consistência do deck

A identidade visual vem de **três camadas fixas** em todos os prompts:

1. **LoRA de estilo** (peso constante em todas as gerações) — o "artista" do deck
2. **Sufixo de estilo** idêntico (o trecho após o sujeito no template acima)
3. **Paleta travada** — escolher 1 descrição de paleta e não variar entre cartas

A variável é apenas o `{sujeito_cena}`.

### 3.3 Banco de conceitos (84 cartas)

Gerar 84 conceitos distribuídos em arquétipos — evita que o deck fique repetitivo:

| Arquétipo | Qtde | Exemplos de sujeito |
|---|---|---|
| Figura humana + objeto impossível | 20 | mulher regando plantas de dentro de uma gaiola; velho pescando estrelas num telhado |
| Animal antropomorfizado | 15 | raposa de terno esperando ônibus; coruja lendo à luz de vaga-lumes |
| Cenário impossível | 15 | cidade dentro de uma bolha de sabão; escada rolante pro céu noturno |
| Natureza transformada | 12 | árvore com portas no tronco; rio que flui pra cima |
| Objetos com vida | 12 | guarda-chuva voando sozinho sobre o mar; mala aberta de onde saem borboletas |
| Cena emocional ambígua | 10 | dois guarda-chuvas se afastando na chuva; cadeira vazia apontada pro por do sol |

Cada conceito é 1 linha num arquivo `conceitos.txt` (formato: `id;arquetipo;sujeito_cena`).

---

## 4. Pipeline de Produção

```
[1] conceitos.txt (84 linhas)
      │
[2] Batch: 4 variações por conceito (336 imagens, ~1–2h na 5060)
      │
[3] Curadoria: escolher 1 por conceito (ver §5)
      │
[4] Rodada de correção: regerar conceitos sem candidata aprovada
      │      (ajustar prompt, nunca ajustar só a seed)
[5] Upscale 4x apenas das 84 aprovadas → 4096×4096
      │
[6] Pós: crop final + exportação para layout de impressão
      │      (63×88 mm @ 300dpi = 744×1039 px — sobra de margem)
[7] QA final do deck completo
```

### Automação

Script de batch (ComfyUI API via `prompt` endpoint, ou modo fila nativo) que:

- Lê `conceitos.txt`
- Injeta o sujeito no template
- Salva como `carta_{id}_{variacao}.png` com metadados (prompt, seed, LoRA, peso)

---

## 5. Critérios de Aceite

### Por carta (etapa 3)

- [ ] Evocativa: sugere ≥2 interpretações distintas (teste: mostrar pra alguém e pedir uma frase — se a frase for só descrição literal do que está na imagem, **reprovada**)
- [ ] Sem texto, watermark ou elementos gráficos estranhos
- [ ] Sem defeitos de anatomia gritantes (mãos quebradas, rostos derretidos em primeiro plano)
- [ ] Paleta e nível de "pintura" compatíveis com o restante do deck

### Do deck (etapa 7)

- [ ] 84 cartas, nenhuma duplicada ou quase-duplicada (mesmo sujeito, ângulo diferente)
- [ ] Folha de estilo consistente: alguém olhando 10 cartas aleatórias diz "são do mesmo jogo"
- [ ] Distribuição de arquétipos dentro de ±20% do planejado na tabela §3.3
- [ ] Teste de mesa: jogar 1 rodada com 6 cartas — se ninguém erra porque era "óbvio demais" nem porque era "impossível de descrever", o deck funciona

---

## 6. Riscos e Mitigações

| Risco | Mitigação |
|---|---|
| VRAM estourar com LoRA + SDXL juntos | Reduzir batch para 1; habilitar `--lowvram` ou tiled VAE |
| Estilo derivar entre conceitos | Seed do LoRA fixo; revisar paleta a cada 20 cartas aprovadas |
| Cartas "óbvias demais" | Regra: se o sujeito cabe numa palavra só ("gato", "casa"), adicionar um elemento impossível |
| Erro "no kernel image" na 5060 | Confirmar PyTorch CUDA 12.8+ antes de qualquer troubleshooting |
| Direitos autorais do estilo | Não citar artistas vivos nos prompts; usar termos de técnica ("oil painting", "storybook"), não nomes |

---

## 7. Definição de Pronto

- 84 PNGs finais em 4096×4096 + versão em tamanho de impressão
- `conceitos.txt` final versionado com prompt completo e seed de cada carta aprovada (reprodutibilidade total)
- 1 folha de contato (contact sheet) do deck inteiro pra avaliação visual rápida
