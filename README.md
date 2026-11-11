# Ateliê Dixit

Pipeline local para criar decks de cartas ilustradas com ComfyUI, SDXL e LoRA. Inclui geração de candidatas, curadoria, correção de prompts, upscale 4x, exportação para impressão, folha de contato, QA técnico e painel web.

## Requisitos

- Windows, Linux ou macOS com Python 3.12 recomendado.
- ComfyUI instalado como subdiretório `ComfyUI/`.
- GPU NVIDIA compatível. Para RTX 5060/Blackwell, use PyTorch com CUDA 12.8 ou superior.
- Aproximadamente 8 GB de VRAM para o fluxo SDXL configurado.

## Instalação

```bash
python -m pip install -r requirements.txt
git clone https://github.com/comfyanonymous/ComfyUI.git ComfyUI
python -m pip install -r ComfyUI/requirements.txt
```

Instale o PyTorch CUDA adequado à GPU antes das dependências do ComfyUI. Não use builds somente de CPU.

Baixe estes modelos:

| Arquivo | Origem | Destino |
|---|---|---|
| `DreamShaperXL_Turbo_v2_1.safetensors` | [Lykon/dreamshaper-xl-v2-turbo](https://huggingface.co/Lykon/dreamshaper-xl-v2-turbo) | `ComfyUI/models/checkpoints/` |
| `StorybookRedmondV2-KidsBook-KidsRedmAF.safetensors` | [StorybookRedmond v2](https://civitai.com/models/132127) | `ComfyUI/models/loras/` |
| `4x-UltraSharp.pth` | [lokCX/4x-Ultrasharp](https://huggingface.co/lokCX/4x-Ultrasharp) | `ComfyUI/models/upscale_models/` |

Os nomes já estão preenchidos em `config.json` e `config_deck100.json`. Se usar outros arquivos, atualize a configuração correspondente.

## Executar

Inicie o ComfyUI em um terminal:

```bash
cd ComfyUI
python main.py --listen 127.0.0.1 --port 8188 --lowvram
```

Em outro terminal, na raiz do projeto:

```bash
python deck_dixit.py validar
python deck_dixit.py painel
```

Abra `http://127.0.0.1:8765`. O painel mostra decks, conceitos, candidatas, prompts, seeds, tempos, curadoria, logs e fila do ComfyUI. Ele também permite iniciar geração, upscale, finalização e QA.

## Fluxo por terminal

```bash
python deck_dixit.py gerar
python deck_dixit.py curar
python deck_dixit.py upscale
python deck_dixit.py finalizar
python deck_dixit.py qa
```

A geração produz quatro candidatas por conceito e reaproveita arquivos existentes. A curadoria salva as escolhas imediatamente. O upscale exige que todas as cartas do deck estejam aprovadas.

Para corrigir conceitos reprovados, crie um arquivo `id;arquetipo;novo_sujeito` e execute:

```bash
python deck_dixit.py regerar --correcoes correcoes.txt
```

A regeração exige alteração real do prompt; não permite apenas trocar a seed.

## Decks incluídos

- `conceitos.txt` + `config.json`: deck original com 84 conceitos.
- `conceitos_deck100.txt` + `config_deck100.json`: deck com 100 novos conceitos e saída independente.

Para operar o segundo deck por terminal:

```bash
python deck_dixit.py --config config_deck100.json --conceitos conceitos_deck100.txt gerar
python deck_dixit.py --config config_deck100.json --conceitos conceitos_deck100.txt curar --porta 8766
```

O painel descobre automaticamente os arquivos `config*.json`. O campo `conceitos` da configuração aponta para o arquivo de conceitos e `saida` define o diretório de produção.

## Saídas

Cada diretório de saída contém:

- `geradas/`: candidatas em 1024×1024.
- `upscaled/`: cartas aprovadas em 4096×4096.
- `impressao/`: versões em 744×1039 px, 300 dpi, para 63×88 mm.
- `folha_contato.png`: visão geral do deck.
- `curadoria.json`, `deck.json`, `manifesto.jsonl` e `qa.json`: proveniência e verificações.

Prompt, negative prompt, seed, checkpoint, LoRA, parâmetros e duração são incorporados aos PNGs. Ao finalizar, o arquivo de conceitos recebe prompt completo, seed e variação aprovada.

A paleta fixa atual é amarelo-dourado, azul-cobalto e branco-quente. Gerações, modelos, ComfyUI e arquivos temporários não são versionados.
