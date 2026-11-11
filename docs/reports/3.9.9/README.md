# Fase 3.9.9: imagens que seguem a narração

Branch `feat/phase-3.9.9`, empilhada sobre a 3.9.8 (a `main` ainda não tem a 3.9.8). Nenhum push. Os testes não fazem chamadas reais (Gemini, ComfyUI e API de imagem ficam simulados).

## Commits

| Commit | Seção |
|---|---|
| 6f34143 | sua mudança na GUI ("Gerar de novo os genéricos"), commitada como sua |
| 309698f | A: o local de cada plano vem da sua própria narração; locais por momento; guarda de palavras-chave; `check_adherence` |
| 7991641 | B: uma folha de encenação por cena com citações de evidência; frases de encenação e contato nos prompts; continuidade |
| b037ffd | C: checagem prompt × narração antes de desenhar (determinística + 1 chamada Gemini por cena) |
| 4a5feb0 | D: checagem imagem × narração com visão (opcional por projeto), candidatos de "fundido com objeto", "Gerar de novo com o prompt corrigido" |
| 85f6896 | correção: os `\b` dos regex tinham virado caracteres de backspace (ver abaixo) |
| 0e010de | E: escada de remoção conservadora, "Substituir por" automático, checagem de novidade |
| 790aa2f | F: provedor externo opcional (OpenAI images), teto por projeto, sempre confirmado |
| 1e7648f | G: testes (28 novos, todos com mocks) |
| 1c5cad7 | correção: a folha de encenação é salva cena a cena (uma falha de cota depois não perde as já feitas) |

### Bug encontrado nos testes da G

Um script de patch escreveu `\b` dentro de uma string Python comum, e cada `\b` virou o caractere de backspace. Com isso, cinco padrões nunca casavam: pessoas sem nome ("shopper", "stranger"), memórias ("had", "remembered"), "in the <lugar>", móveis e "seat". Os números de A e C abaixo já foram refeitos com a correção. Os de C vieram de reaplicar as respostas salvas do Gemini, sem nova chamada. Os testes novos cobrem esses padrões.

## A. Locais que a narração contradiz

`python -m scripts.check_adherence --folder "D:/Private Gravity/Archive/<slug>/text"`: os dois projetos estão arquivados, e `--project <slug>` funciona para projetos em `projects/`.

**She Filmed Every Moment… (2)**, 71 planos: 8 locais errados, 3 para revisar, 1 contradição ([locations_she_filmed_2.json](locations_she_filmed_2.json)).

- s04_06 a s04_13: `supermarket_aisle` → `car_interior`, um local novo por momento (`enclosed`, com bancos). Evidências: "in the car", "drive home", "rearview", "dashboard", "passenger window", "steering wheel"; os planos sem palavra própria seguem por continuidade dentro do carro.
- Para revisar (o lugar aparece numa memória, então o plano não é movido):
  - s02_03: "bedroom";
  - s03_06: "every tearful tantrum in the hallway". Uma lista de memórias sem verbo de memória: "every" e "the first" agora também contam como memória.
  - s05_05: "kitchen counter".
- Contradição: s04_13 "Framed from outside" com a narração dentro do carro. É corrigida para "seen inside the car".

**The Ferryman…**, 70 planos: 0 locais errados, 3 para revisar ([locations_ferryman.json](locations_ferryman.json)):

- s03_03: "office";
- s04_03: "driving";
- s09_01: "office".

São lembranças do passageiro, por isso ficam só como revisão. "landing" saiu da categoria hallway porque o Ferryman tem um cais ("boat landing").

Um local novo sem referência aprovada é desenhado só pelo texto (`compose_shot` só usa referência aprovada).

## B. Encenação da cena 4 (chamada real)

[staging_s04.json](staging_s04.json). Dois momentos, cada entrada com a citação da narração que a justifica (entradas sem citação válida são descartadas):

- Momento [0–6], `supermarket_aisle`: Hannah e Leo em pé no corredor de cereais ("an unfamiliar woman stopped them in the cereal aisle").
- Momento [7–23], `car_interior`: Hannah no banco do motorista e Leo sentado no banco de trás ("her son in the rearview mirror"); relação "at the steering wheel".

Frase que entra no prompt (s04_06): *"The woman from the first image, seated in the driver's seat, her hands on the wheel; the man from the second image, seated in the back seat, both inside the car."*

Planos de um momento fechado trocam o enquadramento "visto de trás, pequeno" por "seen inside the car through its windows". A checagem de continuidade acusa dois planos vizinhos do mesmo momento com locais ou posições diferentes.

## C. Aderência do prompt (cena 4)

[prompt_adherence_s04.json](prompt_adherence_s04.json). Uma chamada para a cena, ~35 mil caracteres, cerca de 30 s. Custo estimado: US$ 0, porque `GEMINI_PRICE_*` ainda está em 0 (preencha em config/.env para ver o valor).

Linha na GUI: **"Aderência do prompt: 5 plano(s) ajustado(s), 0 para revisar"**.

- s04_01 e s04_02: a mulher desconhecida que a narração cita ("an unfamiliar woman", "the stranger gasped") agora é permitida (`others_present`), com um patch no prompt contando as pessoas.
- s04_06 e s04_08: patch de posição (Leo no banco de trás, Hannah olhando pelo retrovisor).
- s04_13: ação corrigida de forma determinística ("Framed from outside" → dentro do carro).
- Citações que não estão na narração não contam como evidência.

## D. Aderência das imagens (visão)

Rodada real sobre os renders **originais** do She Filmed-2: a versão v1 do histórico, de antes das correções manuais. Quando o plano nunca foi refeito, usei a imagem final, que é a original. Saída: [image_adherence_v1.json](image_adherence_v1.json). A especificação usada já é a que segue a narração (locais da seção A e encenação da B).

**Taxa de aderência: 29 de 61 planos com personagens (48 %).** Um plano (s01_07) ficou sem checagem porque o Gemini estourou o prazo.

| Falha | Planos |
|---|---|
| posição da encenação (sentado no banco de trás, atrás do balcão…) | 27 |
| cenário diferente do local | 18 |
| pessoa a mais | 4 |
| personagem fundido com objeto | 1 |

- **Cena 4:** os planos do carro desenhados no corredor do mercado (s04_07, s04_09, s04_12) falham em "car interior" e nas posições dos bancos, que é exatamente o erro que motivou a fase. s04_01 a s04_05 falham "no corredor de cereais": a pergunta é literal demais para um corredor genérico.
- **Fusão:** s03_05 v1 (o tronco da Hannah saindo da ilha) foi candidato do detector e a visão confirmou. s07_01 também foi candidato, mas a visão viu a pessoa atrás do balcão e rejeitou, como o desenho previa.
- **Cenário:** parte das 18 falhas são perguntas rígidas ("modern suburban kitchen" num close sem fundo legível). Por isso as falhas são dicas para revisar, e não uma regeneração automática.

Limitação desta rodada: o Gemini ficou sobrecarregado (503 em todos os modelos) e as folhas de encenação das cenas 5 a 7 não foram geradas. Nesses planos as perguntas cobrem só presença, pessoa a mais, fusão e cenário, sem posição. Custo: 61 chamadas de visão; o valor fica em US$ 0 porque `GEMINI_PRICE_PER_IMAGE` não está configurado.

A opção vem desligada por projeto ("Checar aderência das imagens"). Antes de rodar, a GUI mostra quantas chamadas serão feitas e o custo (`GEMINI_PRICE_PER_IMAGE`). As imagens vão a 768 px. As falhas viram dicas "fora da narração: …" e "personagem fundido com objeto". "Gerar de novo com o prompt corrigido" funciona por plano (no painel Detalhes) e em lote.

**Detector de "fundido com objeto":** corpo que termina exatamente numa borda horizontal forte de móvel, sem pernas. Ele achou a fusão real (s03_05 v1, o tronco da Hannah saindo da ilha da cozinha), mas também gente legitimamente atrás de um balcão (s07_01). O mapa de profundidade não separa os dois casos (diferença de 0,24 na fusão contra 0,26 atrás do balcão). Por isso o detector só gera um candidato e a pergunta de visão decide; a profundidade fica apenas registrada.

## E. Escada de remoção

| máscara (fração do recorte) | começa em |
|---|---|
| < 6 % | pequena: LaMa + denoise 0,6 |
| 6–15 % | média: 0,75 |
| > 15 % | grande: 0,85 (e sugere gerar de novo) |

Depois vêm "maior" (0,92, máscara 35 % maior) e "gerar de novo". Com o "Substituir por" vazio, o texto é montado a partir do entorno e do local ("bare wall and floorboards, plain, empty, uncluttered"). Um texto escrito pelo usuário recebe "plain, empty, uncluttered" no fim.

**Checagem de novidade:** marca uma pessoa nova dentro da máscara, ou muito mais bordas dentro do reparo do que no anel em volta (> 1,5×). Um resultado novo nunca fica em primeiro.

Rodadas reais no espelho do Ferryman ([removal_ladder_runs.json](removal_ladder_runs.json), folhas `removal_*.jpg`):

- s01_04 (máscara grande, começa em 0,85): 1 limpo, 1 com silhueta (tom), 1 em 0,92 com uma pessoa nova, marcada "objeto novo" e ranqueada por último.
- s01_04 com "dark river water, low mist": 2 limpos, 1 com pessoa nova.
- s07_07: nenhum limpo. Em 0,85 apareceu uma silhueta e mais uma pessoa; em 0,92 a pessoa continuou na máscara. Para esse plano a saída é "gerar de novo".

**Limites:**

- As duas máscaras reais são grandes, então os degraus 0,6 e 0,75 só foram exercitados nos testes.
- A densidade de bordas não pega o poste inventado da 3.9.6 (razão 1,10, igual a um bom reparo). Pessoas novas são pegas; objetos sutis, não. Por isso "plain, empty, uncluttered" entra sempre.

## F. Provedor externo (sem chamada real)

Fica desligado por padrão: `EXTERNAL_IMAGE_PROVIDER=""`. Para ligar:

- `EXTERNAL_IMAGE_PROVIDER=openai`;
- a chave em `OPENAI_API_KEY`, lida do ambiente e nunca impressa;
- `EXTERNAL_IMAGE_MODEL` (padrão `gpt-image-1`);
- `EXTERNAL_IMAGE_PRICE` (US$ por imagem). Com 0, nada é enviado.

Cada plano ganha o botão "Refazer com provedor externo":

1. A GUI pede o teto do projeto (gravado em project.json, com o gasto acumulado).
2. Ela confirma o custo antes de cada envio. O servidor recusa sem `confirmed`, além do teto ou sem preço configurado. Nada é automático.
3. O envio leva o prompt composto (com a encenação), as folhas dos personagens e a referência do local, sem o quadro de estilo.
4. O resultado passa pelo caminho de importação da 3.9.4-D (encaixe 1920×1080, margens, cópia, histórico, `source: "external"`) e pela checagem de identidade.
5. O registro editorial lista esses planos em `shots_external_provider`.

Os testes simulam a sessão HTTP e confirmam que a chave não aparece no corpo da requisição.

## G. Testes

`tests/test_narration_adherence.py`, 28 testes: guarda de local na cena 4, memórias, contradição dentro/fora, esquema e evidências da encenação, frases (bancos, dentro do carro, atrás/na frente de móveis), continuidade, aplicação do patch da checagem de prompt (mock), perguntas de visão, heurística de fusão (caixas e profundidade sintéticas), escada por tamanho de máscara, "Substituir por" automático, penalidade de novidade, provedor desligado, teto e importação (sessão simulada).

Suíte completa: **1078 testes, OK** (6 pulados), em cerca de 230 s.

## Comandos

```bash
python -m scripts.check_adherence --project 2026-11-09-she-filmed-every-moment-of-her-son-s-childhood-2
```

O projeto está arquivado. Para ele, use:

```bash
python -m scripts.check_adherence --folder "D:/Private Gravity/Archive/2026-11-09-she-filmed-every-moment-of-her-son-s-childhood-2/text"
```

```bash
python gui.py
```
