# Fase 4.1: dados que chegam e um roteiro para quem pode sair a qualquer segundo

Branch `feat/phase-4.1`, criada a partir da `main` (que já tem a 4.0). Nenhum push. Os testes não fazem chamadas reais: Google APIs e Gemini são simulados. Suíte completa: **1180 testes, OK** (6 pulados).

Uma vez a suíte caiu num erro nativo do OpenCV (reconhecimento facial num teste da QA de imagens), fora do que esta fase mexeu. Rodando de novo, passou.

| Commit | Seção |
|---|---|
| f6ce42a | A: motivo do 403, `analytics_doctor`, "Diagnóstico de dados", consultas pelo ID do canal |
| b07bdc0 | B: contrato de atenção em segundos de vídeo |
| 3d19b5a | C: mapa de atenção (Gemini) e linha do tempo no Roteiro |
| 440832b | D: simulação de 3 espectadores céticos e o portão |
| 8da16b7 | E: revisão de atenção (até 2 rodadas), status "não passou no portão de atenção", 4 dimensões novas e o limite de nota |
| c23ae64 | F: `attention_beats` e plano de perguntas no outline, bloco "escrever para o ouvido", duração por tipo |
| 644e5bd | G: calibração nos 7 roteiros arquivados, com as respostas gravadas |

## A. Por que os dados não chegam

`python -m scripts.analytics_doctor` mostra o problema exato:

- **Credenciais, escopos e canal: OK.** A conta autorizada resolve para *Private Gravity* (`UCLXUyBGw-ojvJDAT5RSOjZQ`), com todos os escopos.
- **YouTube Analytics API e YouTube Reporting API: desativadas** no projeto do Google Cloud **11872407110** (`accessNotConfigured`, "has not been used in project … or it is disabled").

**O que fazer (só você pode, é uma configuração da sua conta Google Cloud):**
1. Abra os dois links e clique em **Ativar**:
   - https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com?project=11872407110
   - https://console.cloud.google.com/apis/library/youtubereporting.googleapis.com?project=11872407110
2. Espere alguns minutos.
3. Clique em **Atualizar** em Desempenho, ou rode `python -m scripts.sync_publications`.

**O que mudou no código:**
- O `status.json` guarda `reason`, `message` e `fix` lidos do corpo do erro do Google.
- Desempenho mostra a correção em português e a mensagem original.
- O botão **🩺 Diagnóstico de dados** roda as mesmas checagens do comando, só leitura e sem imprimir tokens.
- As consultas usam `ids=channel==UCLXUyBGw-ojvJDAT5RSOjZQ` quando a credencial resolve para o canal certo.

**A primeira atualização completa ainda não rodou**, porque as APIs estão desligadas. Por isso faltam os números reais dos 6 vídeos longos, as curvas de retenção, as "Lições de retenção" da 4.0 e a correlação da simulação com a retenção real. Depois de ativar as APIs, rode estes dois comandos. O segundo já lê as simulações gravadas.

```powershell
python -m scripts.sync_publications
python -m scripts.attention_calibration
```

## B–D. O contrato, o mapa e a simulação

- **Tempo:** cada frase recebe início e fim em segundos, a partir do wpm calibrado da voz (153 wpm) e das pausas de narração.
- **Regras determinísticas:**
  - primeiros 5 s com estranheza, conflito ou ameaça;
  - evento incitante até 0:45 depois do cold open;
  - nada de mais de 25 s sem evento de atenção;
  - no máximo 2 frases seguidas só de descrição (a partir da 3ª é achado), e um trecho assim **nos primeiros 45 s depois do cold open é grave**;
  - frases para o ouvido: média ≤ 12 palavras, ≤ 15 % acima de 20, nenhuma acima de 30;
  - perguntas abertas (que se fecham, sempre uma viva, uma por cena).
- **Mapa de atenção:** uma chamada ao Gemini marca cada frase. Citações que não estão na frase são descartadas. A aba Roteiro ganhou a linha do tempo em blocos de 15 s (verde → vermelho), as perguntas vivas como barras, os achados como marcadores e as frases de cada bloco ao clicar.
- **Simulação:** três personas leem em blocos de 15 s e dizem interesse, se sairiam, por quê e a citação.
  - O portão exige: maioria não sai nos primeiros 30 s; mediana ≥ 7; nunca dois blocos seguidos < 5; bloco da virada ≥ 8.
  - As curvas aparecem como **"simulação (não é previsão)"**.

## G. Calibração nos 7 roteiros arquivados

Uma chamada de mapa e uma de simulação por episódio. As respostas ficam gravadas em `recorded/` para replay offline. A duração estimada vai de 7,8 a 13,8 min.

| Episódio | Duração est. | Graves nos primeiros 45 s (mapa) | Saída simulada (noturno · ideias · vindo de Short) | Mediana | Portão |
|---|---|---|---|---|---|
| Envy | 11,4 min | primeiros 5 s | 1:35 · fica · 1:35 | 7 | falha (2 blocos < 5 a partir de 1:00) |
| Only One | 13,8 min | primeiros 5 s, sem pergunta viva, ar morto, descrição | 1:15 · fica · 0:45 | 5 | falha |
| **Thought Town** | 10,3 min | **descrição na abertura, primeiros 5 s** | **0:10 · fica · 0:08** | 5 | falha (2 de 3 saem nos primeiros 30 s) |
| Ferryman | 12,0 min | descrição, primeiros 5 s, sem pergunta viva | 1:45 · fica · 1:45 | 7 | falha (virada 6) |
| Filmed Childhood | 11,6 min | descrição (2×), primeiros 5 s | 2:30 · 9:45 · 0:45 | 5 | falha (virada 5) |
| Everyone Was Him | 11,1 min | descrição na abertura | 0:35 · 3:18 · 0:33 | 5 | falha |
| **Sunrise** | 7,8 min | **descrição na abertura** (3 frases de exposição antes da revelação aos 0:37) | 2:10 · fica · 2:20 | 4 | falha (virada 4) |

**O esperado se confirmou:** Thought Town e Sunrise são marcados nos primeiros 45 s. Na primeira passada, o Sunrise passava no mapa: o teaser abre uma pergunta e a revelação vem aos 0:37. Por isso a descrição na janela de abertura virou achado grave. Os 7 episódios passaram a ser marcados na abertura.

O Thought Town também é o caso que a simulação pega com mais força: os dois espectadores impacientes saem aos 8–10 s. Isso combina com o dado que você trouxe (média de 1:37 em 9:48 no episódio 3).

**Motivos mais repetidos:**
- flashback ou memória da infância que para o presente ("kills the present narrative momentum");
- exposição sobre o passado;
- a mesma cena ritual repetida (Ferryman);
- a tese explicada depois de já ter sido mostrada (persona "ideias").

## E. Revisão até segurar

- Quando B ou D falham, só as cenas com problema são reescritas, com instruções tiradas dos achados e dos motivos das personas. São **no máximo 2 rodadas**, e C e D rodam de novo depois de cada uma.
- As proteções continuam: fidelidade ao brief e sem colar o brief, falas do autor palavra por palavra, guarda de cronologia e regras do cold open.
- **Mais curto é permitido:** cenas cortadas de propósito não têm piso de palavras (`attention_shortened`), e o total pode cair abaixo de 90 %. A revisão registra a duração antes e depois em `attention_review.json`.
- O que ainda falhar vai para a revisão humana como **"não passou no portão de atenção"**. Para enviar mesmo assim, é preciso escrever o motivo; ele fica gravado na aprovação.
- A revisão editorial ganhou `opening_grip`, `momentum`, `curiosity_management` e `ear_readability`.
- Com um achado grave de atenção, `dramatic_progression` e `momentum` ficam limitadas a 6. Não existe uma dimensão chamada `pacing`; `momentum` é a dimensão de ritmo.
- Relatórios antigos, de 15 ou 21 dimensões, continuam válidos.

## F. Escrever para atenção

- O outline agora planeja `attention_beats` por cena (como abre, a revelação parcial de dentro, como termina) e um `loop_plan` de perguntas. O plano é validado com as regras de B antes de qualquer cena ser escrita.
- O prompt das cenas ganhou o bloco **"WRITE FOR THE EAR, FOR A PHONE"**: começar em movimento, um detalhe sensorial em vez de um parágrafo de cenário, frases curtas, terminar numa pergunta, revelação ou virada, nada de "The morning was quiet…". A voz literária do canal continua, nunca caça-clique.
- **Duração (sua decisão de 15/11):**
  - `ATTENTION_TARGET_MINUTES = {"A": 9, "B": 9, "C": 7}`, usada quando o brief não define minutos;
  - o padrão geral passou de 15 para 9 min, e o mínimo aceito de 8 para 6;
  - na GUI, a "Duração-alvo" abre em **"Automática por tipo (A 9 · B 9 · C 7 min)"**, e 7 e 9 também aparecem como opções.

## Comandos

```powershell
python -m scripts.analytics_doctor          # diz exatamente por que os dados estavam bloqueados e o que fazer
python -m scripts.attention_calibration     # depois de ativar as APIs: correlação da simulação com a retenção real
python gui.py   # Desempenho → Diagnóstico de dados; Roteiro → Mapa de atenção + simulação; revisão com o portão de atenção
```
