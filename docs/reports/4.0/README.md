# Fase 4.0: descoberta

Branch `feat/phase-4.0`, empilhada sobre a `feat/phase-3.9.9`, porque a `main` ainda não tem a 3.9.8 nem a 3.9.9. Nenhum push. Os testes não fazem chamadas reais (YouTube Data/Analytics e Gemini simulados). A suíte completa passa: **1133 testes, OK** (6 pulados).

## Commits

| Commit | Seção |
|---|---|
| 07ddfbf | A: clareza de tópico (critério de tópico no score do título, premissa e linha da série no topo da descrição, até 8 tags, 2–3 hashtags, anti-repetição) |
| 4c1f14d | B: títulos e descrições traduzidos, painel "Traduções", `--localize` no backfill, lembrete de dublagem |
| ee2e03d | B (ajuste): `pt` = português do Brasil, `es` = espanhol latino-americano, "Chapters" traduzido |
| d54ce21 | C: status da atualização em Desempenho, "Autorizar de novo", loop de troca de embalagem |
| c67c9d8 | D: aprendizado de retenção (curva, quedas → cena e frase, resumo do canal, orientação suave nos prompts) |
| 0957f64 | E + G: checklist "Depois do envio", playlist automática, linha do Hype no cartão final, rascunhos da Comunidade |
| f3f15c6 | F: horário fixo por tipo (Nova York), Shorts com pelo menos 3 h entre si, seção "Descoberta" em Configurações |
| 8d6214f | H: exportar Shorts para TikTok / Reels |

E e G estão num commit só porque os rascunhos da Comunidade vivem dentro do checklist, no mesmo módulo (`uploader/after_upload.py`).

## Por que `data/analytics/` estava vazio

A atualização em segundo plano (`refresh_due_safely`, chamada pela sincronização a cada 30 min) roda sem abrir o navegador. O token salvo do YouTube não renova mais (`RefreshError`), o código levanta `YouTubeAuthError`, e o erro só ia para o console como um aviso. Nada era gravado, então a pasta nunca foi criada e o painel Desempenho não dizia nada.

A causa provável é o app OAuth estar em modo **Testing**. Nesse modo o Google expira o token de atualização a cada 7 dias. Os envios funcionavam porque pedem o consentimento no navegador; a sincronização e o Analytics não podem.

**Correção:**
- Toda tentativa grava `data/analytics/status.json` (última tentativa, último sucesso, erro, se precisa autorizar).
- Desempenho mostra a última atualização e o erro.
- Quando falta autorização, aparece o botão **🔑 Autorizar de novo**: ele abre o navegador, você entra com a conta do canal, e a atualização roda em seguida.
- `python -m scripts.sync_publications` agora também atualiza o Analytics.

**Primeira atualização real:** ainda não aconteceu. Ela depende do seu login no navegador, que só você pode fazer. O status registrado agora é: nenhum sucesso, autorização necessária. Para não expirar de novo a cada 7 dias, publique o app OAuth (Google Cloud → OAuth consent screen → Publish app).

## A. Tópico

- O score do título ganhou o critério **topic**, com peso 12 %. Vale 5 quando o título traz um conceito buscável e 1 quando não traz. O conceito vem das `topic_words` do episódio, das palavras do tipo ou de um vocabulário de conceitos de história.
- A validação de títulos (sem títulos-tese) continua igual.
- O prompt dos títulos pede um conceito buscável ao lado do gancho.
- **Descrição**, nesta ordem:
  1. primeira linha: a premissa com uma palavra de tópico (o Gemini escreve `premise_line` e `topic_words` junto com as partes da descrição);
  2. a linha da série por tipo;
  3. o gancho, os capítulos (pelo menos um título de capítulo depois do primeiro traz o tópico), o CTA e o crédito.
- **Tags:** até 8 (tópico + tipo + as do roteiro); nomes em `TAG_BLOCKED_NAMES` nunca entram.
- **Hashtags:** `#PrivateGravity` + a do tipo (`#thoughtexperiment` / `#parable` / `#psychologicalfiction`) + uma de tópico.
- A revisão antes de publicar mostra "sem tópico" e **bloqueia** uma palavra-chave repetida mais de 2 vezes na prosa. A lista de capítulos e as hashtags não contam como prosa.

## B. Traduções (um episódio)

Rodei uma chamada real para "They Paid the Ferryman to Forget", com a descrição montada a partir do arquivo. Os 5 idiomas passaram na validação: horários dos capítulos iguais, título ≤ 100 e nenhuma linha em inglês esquecida. Dados completos em [report_data.json](report_data.json).

- pt: *Eles Pagaram ao Barqueiro para Esquecer*
- es: *Le pagaron al barquero para olvidar*
- fr: *Ils ont payé le passeur pour oublier*
- de: *Sie bezahlten den Fährmann, um zu vergessen*
- hi: *उन्होंने भूलने के लिए नाविक को भुगतान किया*

A amostra em português saiu em **português europeu** ("Subscreve", "partilha", "Autarca"), e o cabeçalho "Chapters" ficou em inglês. Corrigi o contrato no commit ee2e03d: `pt` agora é português do Brasil e "Chapters" é traduzido. A amostra acima é de antes da correção. Gere de novo no painel Traduções para ver a versão brasileira.

No envio, as traduções vão em `localizations` com `defaultLanguage="en"`. O painel **Traduções** na revisão mostra o português primeiro e é editável por idioma. Os Shorts mantêm `#Shorts` no título traduzido. O resumo depois do envio lembra de **ativar a dublagem automática** no Studio → Legendas (a API não faz isso).

**Backfill dos vídeos publicados:** ainda não rodei. Precisa da sua aprovação e da autorização do YouTube.

```powershell
python -m scripts.youtube_backfill --video-id w-XazyuEUv8 --localize --with-shorts
python -m scripts.youtube_backfill --video-id Sc3IbtGx-6k --localize --with-shorts
python -m scripts.youtube_backfill --video-id WKswj78bnKo --localize --with-shorts
python -m scripts.youtube_backfill --video-id Fsy9Md4a4yw --localize --with-shorts
python -m scripts.youtube_backfill --video-id 7m4-xEecXFI --localize --with-shorts
```

O ledger tem 5 episódios longos publicados, não 4: o "She Filmed" também está publicado. `--dry-run` mostra as traduções sem mudar nada.

## C. Troca de embalagem

A regra fica editável em Configurações → Descoberta:

1. Passadas **72 h** da publicação, com **≥ 300 impressões** e CTR abaixo da mediana do canal (ou de **3 %** enquanto houver menos de 2 vídeos com dados), o sistema propõe primeiro a próxima miniatura da galeria, mantendo o título.
2. Mais 72 h depois, se o CTR continuar baixo, propõe o próximo título aprovado de `titles.json`.

Nada é automático. Desempenho mostra "Trocar miniatura?" ou "Trocar título?" com antes, depois e os números, e um clique aplica (`thumbnails.set` / `videos.update` com o snippet inteiro devolvido). Cada troca fica no ledger (`packaging_swaps`) com o horário e os números de antes. "Exportar para Test & Compare" grava as 3 miniaturas e os títulos em `exports/test_compare/<slug>/`, para subir no Studio, que decide por tempo assistido por impressão.

Como ainda não há dados, nenhuma troca está proposta agora.

## D. Lições de retenção

A curva (`audienceWatchRatio`, `relativeRetentionPerformance` por `elapsedVideoTimeRatio`) é buscada para cada vídeo longo junto com o resto do cache. As maiores quedas, ignorando os primeiros 3 %, são mapeadas para a cena, o tipo de seção e a frase narrada naquele momento.

O texto sai assim: "queda de 18 % aos 0:35, no fim da abertura (cena 1, "The First Stone"); frase: «…»".

O resumo `data/analytics/retention_lessons.json` guarda os últimos 10 vídeos. As 3 lições mais consistentes (vistas em pelo menos 2 vídeos) entram nos prompts do outline e do roteiro como **orientação suave**, nunca como regra, e aparecem em Desempenho.

**Lições dos vídeos publicados:** indisponíveis até a primeira atualização autorizada. Sem dados, os prompts não mudam.

## E/G. Checklist "Depois do envio" (último episódio publicado)

"She Watched 9,000 Videos of Her Son and Found Hers" (`7m4-xEecXFI`), realista:

- **Tela final:** "He Cut Off His Daughter for a Warm Glow" (`Sc3IbtGx-6k`), mesmo tipo, + Inscrever-se.
- **Card:** aos 7:33, início da virada ("The Missing Witness"), apontando para o mesmo vídeo.
- **Vídeo relacionado:** os 3 Shorts → `https://www.youtube.com/watch?v=7m4-xEecXFI`.
- **Comentário fixado:** este projeto arquivado não tem `description_parts.json`, então o item aparece vazio. Os episódios novos têm o texto.
- **Dublagem automática:** o lembrete.
- **Playlist:** "Configure os IDs das 3 playlists em Configurações". A partir daí cada episódio entra na playlist do seu tipo via `playlistItems.insert`.
- **Comunidade (G), rascunhos:**
  - enquete "Who was right? …" com 2–4 opções;
  - teaser para a véspera com a miniatura.
  - Nunca são postados. O botão "Escrever rascunhos da Comunidade com Gemini" faz versões melhores que o texto padrão.

Os itens são marcáveis e ficam no ledger (`after_upload`). Desempenho avisa o que falta nos vídeos com mais de 24 h. O cartão final agora tem a linha "If this stayed with you, hype it and share it."; não pede like nem inscrição em troca de nada.

## F. Próximos horários (America/New_York)

| Tipo | Próximo | Horário |
|---|---|---|
| Parábola sombria | sábado 14/11 18:00 | 20:00 aqui |
| Ficção psicológica realista | terça 17/11 18:00 | 20:00 aqui |
| Experimento mental | quinta 19/11 18:00 | 20:00 aqui |

O diálogo de envio já abre em "Agendar" com o próximo horário do tipo preenchido; "Publicar agora" continua disponível. A mudança de horário de verão mantém 18:00 no relógio local. Os Shorts começam no horário do episódio e nunca ficam a menos de 3 h um do outro.

## H. TikTok / Reels

Cada Short renderizado ganha o botão "Exportar para TikTok / Reels". Ele copia o mesmo arquivo 9:16 e cria um `.txt` com uma legenda por plataforma (gancho, 3–5 hashtags, "Full story on YouTube: Private Gravity") em `exports/social/<slug>/`. Nada é postado. `exports/` está no `.gitignore`.

## Comandos

```powershell
python -m scripts.sync_publications                                       # autoriza de novo (navegador) e atualiza o Analytics
python -m scripts.youtube_backfill --video-id <id> --localize --with-shorts --dry-run
python -m scripts.youtube_backfill --video-id <id> --localize --with-shorts
python gui.py   # Desempenho (Autorizar de novo, Atualizar, trocas, Depois do envio), Traduções na revisão, agendamento por tipo, Configurações → Descoberta
```
