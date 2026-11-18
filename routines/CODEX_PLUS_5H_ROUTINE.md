# Rotina Operacional Codex Plus — `sol-6.2` `xhigh` (Pensar) + `low` (Executar)

## 1. Recuperação Automática de Ciclo de 5 Horas (`data/state.db`)
- Sempre que iniciar um turno ou sair da janela de cooldown de 5 horas, inspecione `task_checkpoints` em `data/state.db` (`stage NOT IN ('COMPLETED', 'FAILED')`) e retome a partir do passo exato onde parou (ou assuma tarefas deixadas pelo Claude Pro em cooldown).

## 2. Alocação Estrita de Raciocínio (`sol-6.2`)
- **PENSAR (`codex exec -m sol-6.2 -c model_reasoning_effort="xhigh"`)**:
  - Engenharia de retenção nos **2 primeiros segundos**, diferenciação de ângulo editorial entre os 5 canais PT-BR (`config/channels_matrix.yaml`) sem sobreposição de timestamps (`channel_segment_locks`), e auditoria rigorosa das **13 Regras da Campanha Live `KY4DWumZffg`**.
- **EXECUTAR (`codex exec -m sol-6.2 -c model_reasoning_effort="low"`)**:
  - Processamento mecânico em lote (60 cortes/dia = 120 publicações TikTok + Instagram Reels), geração de arquivos `.ass` sincronizados, validação de hashes únicos FFmpeg e submissão imediata via Real Oficial MCP/API.

## 3. Checklist Obrigatório das 13 Regras (`KY4DWumZffg`)
- **Fonte Única**: `https://www.youtube.com/watch?v=KY4DWumZffg` somente com a live NO AR (`19/11 01:00` – `20/11 01:57` BRT). Dublagem e legendas em `pt-BR` são permitidas e incentivadas.
- **Plataformas Pagantes**: **TikTok** + **Instagram Reels** apenas. Registrar link no Real Oficial no segundo seguinte ao upload.
- **Banimento Total de Tela**: **Samira Close** (`07:04–08:10` BRT — zero segundos em tela!) e **The Burnt Peanut** (`20:00–20:34` BRT — blackout total!).
- **Formatação Exata**:
  - Legenda: `"Assista ao vivo em youtube.com/youtube"` + `#launchdayonYT` (**NENHUMA outra hashtag!**).
  - Vídeo: **ZERO** logo, **ZERO** marca d'água, **ZERO** letra de música na tela, **ZERO** GTA antigo/comparação/IA/crítica.
  - 1º Comentário Fixado: `"Assista em https://www.youtube.com/watch?v=KY4DWumZffg"`.
- **Pós-Live (`20/11 02:00 BRT` em diante)**: Manter todos os posts públicos/intactos pelas +48h de apuração e virar os 5 canais para Cortes de Lives de Streamers Brasileiros de GTA VI.
