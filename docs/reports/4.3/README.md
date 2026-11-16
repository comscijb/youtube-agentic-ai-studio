# Fase 4.3: Shorts escritos como Shorts

Branch `feat/phase-4.3`, a partir da `main` (que já tem a 4.2). Há um commit por seção (A/B, C, D, E, F, G, H) e mais duas correções encontradas nas rodadas reais. Nada foi enviado ao YouTube e não houve push.

## O que mudou

| Seção | O quê | Onde |
|---|---|---|
| A/B | Cinco formatos com roteiro próprio: Gancho, Dilema, A Frase, Contagem para a virada e POV. Uma chamada ao Gemini propõe todos os roteiros. Checagens por código: o gancho vem primeiro, frases curtas, duração na faixa do formato, CTA presente, nada da virada nem do final, citações que existem no roteiro, título dentro da política. Depois, a simulação de deslize em blocos de 3 s e uma reescrita dirigida; o que continua falhando vai para revisão humana com os motivos. | `video/short_formats.py` |
| C | A mesma voz do canal (Kokoro), frase a frase, a 0,95 (o episódio usa 0,88), com pausas de 0,15 s. Acento sonoro no 0,0 s, a faixa Dramática de maior energia com ducking sob a voz e um sting no CTA. | `video/short_voice.py` |
| D | Edição 9:16: a imagem mais forte primeiro (nunca preta), com o texto do gancho fora do rosto. Vistas de 1–2,5 s, cortadas no início das palavras e nunca mais de 3 s sem mudança. Legendas de 1–3 palavras com a palavra falada em destaque e palavras-chave em dourado. Cartão final com a miniatura desfocada e "Full story on the channel ↑". Em A Frase, o loop fecha sem emenda. | `video/short_edit.py` |
| E | Descrição com a frase-gancho, "Full story: <URL>", a hashtag do tipo e 1–2 de tópico. Rascunho de comentário fixado por Short. "Vídeo relacionado" virou o 1º item do checklist pós-envio. Formatos alternados e Shorts espalhados pela semana, nunca a menos de 3 h um do outro. | `uploader/shorts_upload.py`, `uploader/after_upload.py` |
| F | Desempenho por formato nos últimos 30 dias e sugestão de pesos depois de 3 Shorts do mesmo formato, aplicada com um clique. | `uploader/short_stats.py`, `video/shorts_settings.py` |
| G | Estúdio de Shorts na GUI e a seção Configurações → 📱 Shorts. | `gui.py`, `index.html` |
| H | `scripts.shorts_backfill` para episódios já publicados. | `scripts/shorts_backfill.py` |

As funções novas ficam atrás de configurações. Os recortes do episódio (o modo antigo) continuam disponíveis como "Recortes do episódio", desligados por padrão. O "Short original (história própria)" continua como antes.

## Mudanças de padrão (atenção)

- **Shorts por episódio:** `SHORTS_PER_EPISODE` passou de 3 para **4**.
- **Agenda:** o `SHORTS_SCHEDULE` padrão agora espalha os Shorts pela semana, nos horários 12:00, 18:00 e 21:00. O ritmo antigo de "um por dia às 18:00" continua funcionando se `spread_days` for removido.
- **Testes antigos:** dois testes da 3.7 foram fixados nesse ritmo diário, que continua suportado.

## Roteiros propostos para a parábola atual ("He Charged for the Sunrise Until No One Came")

Rodei o backfill de verdade (Gemini, Kokoro, ffmpeg; nada enviado):

```powershell
python -m scripts.shorts_backfill --project 2026-11-12-the-man-who-bought-the-sunrise --formats hook,dilemma,line,countdown,pov
```

- **Restauração:** só 50 arquivos do arquivo morto (roteiro, brief, outline, palavras, lista de planos e suas imagens, fundo da miniatura). O projeto continua arquivado.
- **Chamadas ao Gemini:** roteiros, deslize, reescrita e deslize de novo.
- **Plano completo:** `docs/reports/4.3/sunrise_shorts_plan.json`.

| Short | Formato | Título | Duração (renderizado) | Resultado do portão |
|---|---|---|---|---|
| short_01 | Gancho | The father who claimed the morning sky | 33,6 s | revisar: média de 12,3 palavras por frase; o swiper desliza a partir do 3º bloco ("vira prosa de conto de fadas") |
| short_02 | Dilema | A dark parable of the sky merchant | 22,6 s | revisar: 2 blocos de deslize ("a escolha já foi feita, depois vira enchimento"; "isca de comentário forçada") |
| short_03 | A Frase | The father who sold the dawn | 12,8 s | revisar: 34 palavras (mínimo 40); loop julgado natural |
| short_04 | Contagem | The father who built a tower over dawn | 30,4 s | revisar: desliza do 3º bloco em diante ("detalhe de construção mata a urgência") |
| short_05 | POV | POV: Pay to see the sunrise | 25,3 s | revisar: o título com "POV:" cai na regra do título contra subtítulo com dois-pontos; 3 blocos de deslize |

**Leitura:** nenhum dos cinco passou sozinho, e o motivo é bom. O portão fez o trabalho dele.

- **O que a reescrita corrigiu:**
  - os vazamentos do que o cold open esconde (o portão e a vila);
  - um título fora da política;
  - um texto de tela longo demais.
- **O que continua:** o swiper desliza quando o roteiro vira descrição (muro, torre, moedas) em vez de tensão.
- **O caminho no estúdio:** é exatamente o caso do "Gerar outra versão" ou de uma edição à mão.
  - O melhor candidato é o **Dilema**: deslize só no fim, pergunta clara.
  - Depois vem **A Frase**: loop natural, falta só ganhar 6 palavras.

Na primeira rodada real (antes da correção) o Dilema e A Frase passaram. Ela mostrou dois defeitos meus, já corrigidos:

- o Gemini repetia o CTA dentro do roteiro, então ele seria falado duas vezes;
- a regra de conceito no título não recebia os conceitos do episódio, e a reescrita usava rótulos genéricos e até do tipo errado ("Thought Experiment" numa parábola). Agora isso reprova.

**Ponto em aberto:** o título "POV: …" bate na política de títulos (sem subtítulo com dois-pontos). Para o formato POV vale decidir entre liberar o "POV:" só no título desse formato ou manter o "POV:" apenas no texto de tela, que já é obrigatório. Mantive a regra como está.

## Um exemplo renderizado por formato

Em `projects/2026-11-12-the-man-who-bought-the-sunrise/shorts/`:

| Arquivo | Formato | Vistas | Maior vista | Loudness do MP4 |
|---|---|---|---|---|
| `short_01.mp4` | Gancho | 18 | 2,2 s | −14,9 LUFS |
| `short_02.mp4` | Dilema | 11 | 2,2 s | −14,9 LUFS |
| `short_03.mp4` | A Frase | 8 | 2,2 s | −14,7 LUFS; diferença entre o último e o primeiro quadro: 0,0001 |
| `short_04.mp4` | Contagem | 17 | 2,0 s | −14,9 LUFS |
| `short_05.mp4` | POV | 13 | 2,1 s | −14,9 LUFS |

O que as renderizações mostraram e já está corrigido:

- **Primeiro quadro:** era uma faixa 16:9 com fundo desfocado (as mãos não cabiam num recorte 9:16). Agora a escolha prefere imagens que enchem o quadro 9:16, e saiu o rosto do mercador.
- **Texto do gancho:** caía sobre os olhos. Agora vai para cima do rosto ou logo abaixo dele.
- **Loudness:** as mixagens saíam em −15,7 LUFS. Uma compressão leve, ganho e limitador trouxeram para cerca de −14,8 LUFS, com pico de −4 dBTP no MP4. Ainda fica 0,7–0,9 LU abaixo do alvo.

Limites atuais:

- **Efeitos sonoros:** `assets/sfx/` continua vazia, então os Shorts saíram **sem o acento no 0,0 s e sem o sting**; o relatório de cada um diz `missing_sfx: impact/whoosh`. Basta registrar um arquivo de `impact` ou `whoosh` em `assets/sfx/LICENSES.json`.
- **Primeiro quadro repetido:** quatro dos cinco Shorts abrem com a mesma imagem (o rosto do mercador), porque as cenas citadas se sobrepõem. Funciona, mas repete; numa próxima rodada vale variar a abertura entre os Shorts de um episódio.
- **Estimativa de duração:** fica 1–4 s acima da real (o Gancho estimou 38 s e saiu com 33,6 s). O portão de duração erra para o lado seguro.

## Plano de backfill dos 6 episódios publicados

Os dry-runs abaixo apenas listam: nada foi copiado e não houve chamada ao Gemini.

| Episódio (slug) | Tipo | URL no ledger | Arquivos a restaurar | Formatos sugeridos |
|---|---|---|---|---|
| 2026-10-21-the-man-who-needed-everyone-to-envy-him | A (realista) | w-XazyuEUv8 | 67 (5 já no SSD, 2 fora do arquivo) | hook, dilemma ("Who was right?"), line |
| 2026-10-29-he-lied-about-his-dead-wife-to-see-what-it-would-say | A | Sc3IbtGx-6k | 84 | hook, dilemma, line |
| 2026-11-03-everyone-heard-what-he-thought | B (experimento mental) | WKswj78bnKo | 69 | hook, dilemma, line (o comando do prompt) |
| 2026-11-06-the-ferryman-who-took-memories-as-fare | C (parábola) | Fsy9Md4a4yw | 79 | hook, line, countdown |
| 2026-11-09-she-filmed-every-moment-of-her-son-s-childhood-2 | A | 7m4-xEecXFI | 80 | hook, dilemma, pov |
| 2026-11-11-he-woke-up-and-everyone-in-the-world-was-him | B | Ii1S7bFw94s | 77 | dilemma, pov, line |

Cada episódio custa 3–4 chamadas ao Gemini e alguns minutos de render na CPU. Os Shorts ficam no portão de aprovação do projeto com a URL do episódio gravada no plano (`backfill.episode_url`).

**Falta o envio:** o envio de Shorts de um episódio já publicado ainda não tem botão próprio. Hoje o portão sobe o episódio junto. É o próximo passo natural, se você quiser publicar esses Shorts.

## Linha de base por formato (Shorts existentes, todos "recorte")

Do cache do Analytics (atualizado em 16/11, sem nova chamada à API), últimos 30 dias:

| Formato | Shorts | Com dados | Views | Views/Short | % assistido | Duração média | Inscritos | Cliques p/ episódio |
|---|---|---|---|---|---|---|---|---|
| recorte | 15 | 11 | 186 | 12,4 | 48,6 % | 26,5 s | 0 | 0 |

O melhor recorte é "He Realized Everyone Was Editing Their Thoughts", com 136 views e 46,8 % assistido. O Short de 195 views citado no prompt não está no cache dos últimos 30 dias. Os formatos novos passam a ser comparados com essa linha a partir dos primeiros envios. A sugestão de pesos aparece depois de 3 Shorts de um mesmo formato.

## Testes

- **Novos arquivos:**
  - `test_short_formats`
  - `test_short_voice` (mixagem real com ffmpeg)
  - `test_short_edit` (inclui um render pequeno)
  - `test_shorts_packaging`
  - `test_short_stats`
  - `test_shorts_studio`
  - `test_shorts_backfill` (arquivo morto simulado)
- **Gemini, TTS e YouTube:** simulados nos testes.
- **Suíte completa:** 1298 testes OK (6 pulados), depois de todas as correções.

## Comandos

```powershell
python gui.py   # Projeto → Shorts: roteiros por formato, simulação de deslize, prévia, renderizar, aprovar
python -m scripts.shorts_backfill --project 2026-11-03-everyone-heard-what-he-thought --formats hook,dilemma,line
python -m scripts.shorts_backfill --project <slug> --dry-run      # só lista o que seria restaurado
```
