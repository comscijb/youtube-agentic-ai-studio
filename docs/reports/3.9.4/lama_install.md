# LaMa: instalação para a remoção real (3.9.4-B)

O probe de `/object_info` deste ComfyUI não encontrou nenhum nó de LaMa ou MAT, e não há pasta `models/inpaint`.
Nada foi instalado. Sem LaMa, "Remover figura" apaga com o OpenCV (Telea) e restaura com o Klein; a interface mostra
o motor usado em cada resultado. Com os passos abaixo, o programa passa a usar o LaMa sozinho.

## Nós disponíveis hoje

| Nó | Situação |
|---|---|
| `SetLatentNoiseMask`, `LoadImageMask`, `VAEEncode` | instalados, usados na restauração |
| `DifferentialDiffusion`, `InpaintModelConditioning`, `VAEEncodeForInpaint`, `GrowMask`, `FeatherMask`, `ImageToMask`, `MaskToImage` | instalados, não usados |
| `FluxProFillNode`, `BriaGenFill` | nós pagos de parceiros, não usados |
| LaMa, MAT | **ausentes** |

## Passos (no computador com o ComfyUI)

1. Instale o pacote de nós na pasta `custom_nodes` do ComfyUI:

   ```bash
   git clone https://github.com/Acly/comfyui-inpaint-nodes
   ```

2. Baixe os pesos para `ComfyUI/models/inpaint/` (crie a pasta se não existir):

   ```
   https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt
   ```

3. Reinicie o ComfyUI.
4. Confira: `/object_info` deve listar `INPAINT_LoadInpaintModel` e `INPAINT_InpaintWithModel`, com `big-lama.pt` nas
   opções de modelo. Esses nomes de classe vêm do pacote e ainda não foram confirmados numa instalação real; se forem
   outros, ajuste `LAMA_NODES` em `video/qa/removal.py`.

## Licenças

- `Acly/comfyui-inpaint-nodes`: GPL-3.0. Fica no ComfyUI e é chamado por API; este repositório não copia o código.
- LaMa (`advimman/lama`): Apache-2.0.

## Alternativa sem ComfyUI

Com um modelo LaMa em ONNX em `models/lama/big-lama.onnx` e o `onnxruntime` instalado, o programa roda o LaMa na CPU.
Esse caminho só foi exercitado por leitura de código: não há modelo aqui para testar.
