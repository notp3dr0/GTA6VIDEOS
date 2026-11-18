# Rotina Operacional Antigravity Ultra — Carbon (Orquestrar) + Hydrogen (Trabalho Braçal)

## 1. Papel no Pool Tri-IA (Failover Zero-Downtime nos 5 Dias)
- Enquanto você estiver fora de casa com o 2º PC ligado por 5 dias, o **Antigravity Ultra** atua como espinha dorsal contínua:
  - **Carbon (`gemini-2.5-pro`)**: Orquestra os 5 canais PT-BR (`config/channels_matrix.yaml`), valida visão/frames na janela sensível das `07:04–08:10 BRT` (garantindo **zero segundos de Samira Close**), coordena o turbo do Skorpion (`22:30–22:57 BRT`) e assume instantaneamente qualquer lote quando Claude Pro ou Codex Plus estiverem na janela de cooldown de 5 horas.
  - **Hydrogen (`gemini-2.5-flash-lite`)**: Executa todo o trabalho braçal em alta escala (sincronização de legendas `.ass`, checagem das 13 regras em lote, empacotamento dos 60 cortes/dia = 120 posts TikTok + Reels e telemetria SQLite).

## 2. Comandos Slash prontos para deixar rodando no Antigravity Ultra
- **`/goal`**:
  > "Manter os 5 canais PT-BR (`config/channels_matrix.yaml`) produzindo 12 cortes de live por dia cada (60 cortes/dia = 120 posts no TikTok e Instagram Reels), respeitando 100% as 13 regras da live oficial `https://www.youtube.com/watch?v=KY4DWumZffg` (`#launchdayonYT` única, frase `Assista ao vivo em youtube.com/youtube`, comentário fixado `Assista em https://www.youtube.com/watch?v=KY4DWumZffg`, zero marca d'água, bloqueio total de Samira Close e The Burnt Peanut), registrando cada post imediatamente no Real Oficial e assumindo qualquer checkpoint pendente em `data/state.db` durante os limites de 5h do Claude Pro e Codex Plus."
- **`/teamwork`**:
  > "Coordenar orquestradores Carbon com operários Hydrogen (`flash_lite`) para manter a fila dos 5 canais abastecida 24/7 durante os 5 dias (modo Campanha Live até `20/11 01:57 BRT` e modo Cortes de Lives de Streamers PT-BR após `02:00 BRT`, sem jamais apagar ou privar posts da campanha)."
