# Rotina Operacional Claude Pro — Opus `xhigh` (Pensar) + Haiku `low` (Executar)

## 1. Recuperação Obrigatória de Ciclo de 5 Horas (`data/state.db`)
- Ao iniciar ou retornar de qualquer bloqueio de janela de 5 horas (`5-hour limit` / `resets at`), consulte IMEDIATAMENTE `data/state.db`:
  ```sql
  SELECT task_id, channel_id, slot_time, stage, payload_json
  FROM task_checkpoints
  WHERE stage NOT IN ('COMPLETED', 'FAILED')
  ORDER BY slot_time ASC, updated_at ASC;
  ```
- Retome qualquer tarefa interrompida exatamente da etapa salva (`READY_TO_EXECUTE`, `RENDERING`, `PUBLISHING`) sem repetir o raciocínio já concluído.

## 2. Divisão Estrita de Modelos (Orçamento 4× Maior por Janela de 5h)
- **PENSAR (`claude --model opus --effort max`)**:
  - Escolher os momentos mais virais (0–2s hook explosivo em `pt-BR`) da live oficial `https://www.youtube.com/watch?v=KY4DWumZffg` (durante a live) ou lives de streamers PT-BR (após o fim da live).
  - Auditar o **Checklist de 13 Regras** antes de liberar qualquer plano para renderização.
- **EXECUTAR (`claude --model haiku --effort low`)**:
  - Gerar marcações de legendas `.ass`, JSONs de corte FFmpeg, payloads de agendamento nos 5 canais (`config/channels_matrix.yaml`) e registro imediato no Real Oficial.

## 3. Regras Invioláveis da Campanha Live (`KY4DWumZffg`)
1. **Fonte Única na Campanha**: SOMENTE `https://www.youtube.com/watch?v=KY4DWumZffg` enquanto estiver AO VIVO (`19/11 01:00` até `20/11 01:57` BRT — trava de segurança 3 min antes das `02:00`).
2. **Plataformas Válidas**: **TikTok** e **Instagram (Reels/Carrossel)** APENAS. Registre o link no painel Real Oficial **imediatamente** após postar (só contam views pós-registro; meta `> 5.000 views/vídeo`, R$ 4,00/1k, teto R$ 400/vídeo).
3. **Bloqueios Absolutos de Imagem**:
   - **NUNCA mostrar Samira Close** nem por 1 segundo (`07:04–08:10 BRT` — só cortar câmeras exclusivas de Skorpion e Gamer Costa).
   - **NUNCA mostrar The Burnt Peanut** nem por 1 segundo (blackout total `20:00–20:34 BRT`).
   - **Turbo Máximo PT-BR**: `22:30–22:57 BRT` (gameplay do Skorpion) e Horário Nobre `19:00–00:00 BRT`.
4. **Legenda, Hashtag Única e Comentário Fixado**:
   - Frase obrigatória na legenda: `"Assista ao vivo em youtube.com/youtube"`
   - **ÚNICA** hashtag permitida: `#launchdayonYT` (**PROIBIDO** qualquer outra hashtag como `#GTA6`, `#Reels`, `#TikTok`!).
   - **ZERO** logo ou marca d'água no vídeo (`burn_credit_watermark: false`).
   - **1º comentário FIXADO obrigatório**: `"Assista em https://www.youtube.com/watch?v=KY4DWumZffg"` (curtidas e comentários públicos/ligados).
   - **ZERO** GTA antigo, comparações, `"antes do GTA 6"`, clipes de IA ou tom negativo.
5. **Modo Pós-Live (`20/11` após `02:00 BRT` até o fim dos 5 dias)**:
   - Encerrar envios na campanha, **NUNCA apagar/arquivar/privar** vídeos já postados (contagem segue por +48h até `22/11 02:00 BRT`) e operar os 5 canais em modo 100% Cortes de Lives de Streamers PT-BR de GTA VI (`@Skorpion`, `@GamerCosta`, `@alanzoka`, `@loud_coringa`, `@PaulinhooLoko`, `@FunkyBlackCat`, `@Gaules`).
