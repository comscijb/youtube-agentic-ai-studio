# LaMa: instalação para a remoção real (3.9.4-B)

**Instalado em 2026-11-04** no ComfyUI do Comfy Desktop (instância `FLUX`). "Remover figura" escolhe o motor sozinho:
LaMa quando os nós e o modelo aparecem em `/object_info`, depois ONNX local e, por último, OpenCV. Cada resultado mostra
o motor usado.

## O que foi instalado

| Item | Local | Versão ou tamanho | Licença |
|---|---|---|---|
| `Acly/comfyui-inpaint-nodes` | `ComfyUI-Installs/FLUX/ComfyUI/custom_nodes/` | 1.4.4, commit `5b76817` | GPL-3.0 |
| `big-lama.pt` | `ComfyUI-Installs/FLUX/ComfyUI/models/inpaint/` | 205.669.692 bytes | LaMa (`advimman/lama`): Apache-2.0 |

O pacote fica no ComfyUI e é chamado por API; este repositório não copia o código dele. As dependências já existiam no
ambiente (`kornia` 0.8.3).

## Como repetir

O `comfy` não está instalado nesta máquina (o ComfyUI vem do Comfy Desktop), então o pacote foi clonado à mão:

1. Em `custom_nodes`: `git clone https://github.com/Acly/comfyui-inpaint-nodes`
2. Em `models/inpaint` (crie a pasta): baixe
   `https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt`.
   Ela precisa ficar na instalação, não em `ComfyUI-Shared`: o pacote registra só a pasta `models` da instalação.
3. Reinicie o ComfyUI pelo Comfy Desktop, ou com um POST em `/v2/manager/reboot`.
4. Confira em `/object_info`: `INPAINT_LoadInpaintModel`, com `big-lama.pt` nas opções, e `INPAINT_InpaintWithModel`.

**Atualizar ou trocar a instalação do Comfy Desktop pode exigir repetir estes passos.**

## Alternativa sem ComfyUI

Com um modelo LaMa em ONNX em `models/lama/big-lama.onnx` e o `onnxruntime` instalado, o programa roda o LaMa na CPU.
Esse caminho não foi executado de verdade, porque não há modelo aqui para testar.
