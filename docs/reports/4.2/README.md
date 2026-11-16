# Fase 4.2: o vídeo precisa parecer vivo

Branch `feat/phase-4.2`, empilhada sobre a `feat/phase-4.1`: a `main` ainda não tem a 4.1 (o seu commit 355ba63 está na 4.1), e esta fase usa o mapa de atenção dela. Nenhum push. Testes sem chamadas reais: ComfyUI e Gemini simulados. Os testes não fazem upscale de verdade e não bloqueiam a renderização. Toda função nova tem um interruptor, ligado por padrão, em **Configurações → Ritmo / Som / Música**.

Suíte completa: **1226 testes, OK** (6 pulados).

| Commit | Seção |
|---|---|
| 37600d0 | A: ritmo visual (vistas derivadas, upscale, cortes nas palavras, relatório de ritmo, orçamento de GPU) |
| 4951d93 | B: frases-chave na tela |
| 130fb04 | C: cor e luz por momento, transições, proteção de fotossensibilidade |
| 1a5805e | D: efeitos sonoros (biblioteca licenciada, folha de efeitos, níveis, mixagem) |
| 5c6991b | E: música que segue a história |
| 3ec9cb6 | F: mapa de atenção do vídeo, correções em um clique, motivo para renderizar com achado grave |
| 857fb17 | G: `preview_cut`, retenção aos 0:30 e 1:00 (corte novo × antigo); ajustes de áudio que a prévia revelou |
| 7774b19 | H: Configurações → Ritmo / Som / Música |
| 26607ad | correção: um arquivo de profundidade corrompido é recalculado em vez de parar a renderização |

## Upscaler (A)

O ComfyUI já tem **`RealESRGAN_x4plus_anime_6B.pth`** (licença BSD-3, uso comercial permitido) e o nó `ImageUpscaleWithModel`, então nada precisou ser instalado.
- Só as imagens que ganham recortes passam pelo upscale. O resultado fica guardado em `images/shots/upscaled/`, no dobro do tamanho original.
- Sem modelo, os recortes usam Lanczos e ficam com pelo menos 60 % do quadro.

## Ritmo da parábola atual: "The Man Who Bought the Sunrise"

| | Antes (publicado) | Depois (corte 4.2) |
|---|---|---|
| Imagens ou vistas | 41 planos | **110 vistas** a partir das mesmas 41 imagens |
| Média por imagem | **10,5 s** | **3,9 s** |
| Máximo | 16,6 s | 6,7 s (limite 7 s) |
| Trechos > 7 s sem mudança | 36 | 0 |

Por cena, todas dentro da meta do seu momento:

| Cena | Momento | Vistas | Média | Máx. | Meta |
|---|---|---|---|---|---|
| The Morning Omen | cold open | 3 | 2,1 s | 2,2 s | 2–3 s |
| The Deed Sealed in Wax | desenvolvimento | 15 | 4,4 s | 5,2 s | 3–5 s |
| The Toll at the Ridge | desenvolvimento | 16 | 4,3 s | 5,2 s | 3–5 s |
| The Tower Above the Valley | desenvolvimento | 18 | 4,1 s | 5,3 s | 3–5 s |
| The Migration to Dusk | tensão (virada) | 23 | 3,1 s | 4,0 s | 2–4 s |
| The Sunset Wedding | desenvolvimento | 18 | 4,0 s | 4,9 s | 3–5 s |
| The Empty Dawn Bell | tensão (clímax) | 12 | 3,6 s | 4,6 s | 2–4 s |
| The Rusted Gate… | reflexão | 5 | 5,7 s | 6,7 s | 4–7 s |

Os cortes caem nos limites das palavras, de preferência no início de frases e nos eventos do mapa de atenção. Os recortes nunca cortam um rosto e respeitam a linha dos olhos; recortes de textura vazia são descartados.

O planejador sugere **2 imagens extras** (a virada e a imagem final repetem enquadramentos), cerca de 60 s de GPU, mostradas na GUI antes de renderizar. A sugestão não é automática.

O cold open do Sunrise tem só 6 s (uma frase), então ganhou 3 vistas, não 4–6. A sequência de 4–6 cortes em 15–20 s aparece em cold opens dessa duração.

Nove imagens do Sunrise têm 1672×941, abaixo de 1920×1080; a checagem pré-render já avisava isso.

## Frases-chave na tela (B)

Sete na prévia do episódio inteiro:
- "From tomorrow, the morning has a price." (1:08)
- "Instead, they walked west." (3:48)
- "Nobody came." (6:24)
- e mais quatro.

Cada frase aparece palavra por palavra, só com o que já foi narrado. Fica no terço superior, ou embaixo com as legendas escondidas quando há um rosto no alto. O contraste é ≥ 4,5. O cold open do Sunrise não é uma pergunta, então não ganhou frase-chave: no cold open só a pergunta pode aparecer, nunca a resposta.

## Cor e transições (C)

- **Por momento:** tensão com mais contraste, pretos mais fundos e meios-tons frios. Virada com dessaturação breve, mantendo só o carmim (ou o dourado), e voltando aos poucos em 2,5 s. Final mais quente e suave. O cold open mantém o tratamento da 3.9.7.
- **Transições:** corte seco na tensão, crossfade nas cenas calmas, mergulho no preto de 0,4 s em saltos de tempo e na volta do cold open.
- **Fotossensibilidade:** o passo de luminância fica ≤ 10 % por quadro, com testes nas rampas de cor e nos mergulhos. Na prévia, o film look mediu 2,3 %.

## Efeitos sonoros (D): o que falta baixar

`assets/sfx/` está vazia, então a prévia saiu **sem efeitos**. A folha de efeitos foi gerada (uma chamada ao Gemini) e pede **bell, birds, coins, crowd_murmur, door, drone, heartbeat, impact, paper, riser, room_tone, wind**.

A biblioteca inteira tem 22 etiquetas. Rode o comando abaixo para listar o que falta:

```powershell
python -m scripts.sfx_library
```

Baixe à mão da **YouTube Audio Library → Efeitos sonoros**, ou sons CC0. Ponha cada arquivo em `assets/sfx/<etiqueta>/` e registre em `assets/sfx/LICENSES.json`. Arquivos sem registro, ou com licença que exige atribuição, nunca são usados.

## Música (E): índice e lacunas

`music library/music_index.json` classifica as 22 faixas por clima e energia (RMS e densidade de ataques, em terços dentro da biblioteca):
- dramáticas: 12 faixas;
- melancólicas: 6;
- **sombrias: só 4. É a lacuna.** Vale acrescentar faixas sombrias da YouTube Audio Library, de preferência de energia baixa e média.

No Sunrise, o plano usou três faixas, com crossfade de 3 s:
- *Renaissance Castle* na apresentação;
- *Cafe Regrette* na tensão e na virada;
- *No.2 Remembering Her* no final.

A música cai a quase silêncio 1,5 s antes da linha da virada e volta depois dela, com um crescendo sob a última frase narrada. O cartão final continua silencioso. Não existe uma vinheta de logo; isso fica para quando você tiver uma.

**O que a prévia ensinou e eu corrigi (G):**
- as faixas variam 14 dB entre si, então cada faixa do plano é nivelada a −20 dB RMS antes do volume;
- o próprio fundo musical passa por um compressor antes do ducking, porque um trecho alto da faixa subia 18 dB sob a voz;
- a checagem de inteligibilidade só julga janelas de fala de verdade, não as pausas.

## Mapa de atenção do vídeo da prévia (F)

A prévia (`previews/2026-11-12-the-man-who-bought-the-sunrise/`) tem o minuto novo (`new_0-60.mp4`) e o antigo (`old_0-60.mp4`), recortado do vídeo publicado. Áudio final: −14,7 LUFS, pico −1,5 dBTP.

O mapa de atenção do vídeo do episódio inteiro deu **6 achados, todos de inteligibilidade**. Três estão no primeiro minuto, aos 0:45, 0:46 e 0:51: a voz fica só 9,7, 9,3 e 5,5 dB acima da trilha na faixa de 1–4 kHz, abaixo dos 10 dB mínimos. São reais, num trecho alto de *Renaissance Castle*. O botão "Baixar a música" resolve; depois, rode a etapa Música de novo. Antes dos ajustes de áudio eram 27 achados.

As outras checagens passam:
- nenhum plano parado por mais de 7 s;
- nenhum trecho de 20 s sem evento;
- os primeiros 5 s têm movimento, a música e a primeira palavra (aos 0,3 s);
- frases-chave legíveis;
- nenhum efeito colado num acento.

Bins de 5 s no primeiro minuto (narração · imagem · som): o primeiro tem 1 · 3 · 1. Os seguintes têm cortes de imagem em todos, mas eventos de narração só aos 0:30 (o mapa de 4.1 do Sunrise marca poucas perguntas, que é o problema de roteiro que a 4.1 ataca) e **nenhum evento de som depois dos 5 s**, porque ainda não há efeitos sonoros. Com os efeitos baixados, o mesmo minuto ganha camas de ambiente, ações e acentos.

## Retenção aos 0:30 e 1:00 (G)

Desempenho compara a média de retenção aos 0:30 e 1:00 dos episódios com o corte 4.2 contra os antigos. Os envios novos são marcados com `cut_version = "4.2"`. A comparação só tem números depois que os próximos episódios forem publicados e o Analytics trouxer as curvas.

## Comandos

```powershell
python -m scripts.preview_cut --project 2026-11-12-the-man-who-bought-the-sunrise --seconds 60   # o minuto novo e o antigo, em previews/<slug>/
python -m scripts.sfx_library                                                                    # etiquetas de efeito que faltam
python gui.py   # Roteiro → Mapa de atenção + Ritmo do vídeo + Mapa de atenção do vídeo; Configurações → Ritmo / Som / Música
```
