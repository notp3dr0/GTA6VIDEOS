# Kit Rápido de Criação das 5 Contas (TikTok + Instagram Reels + Real Oficial)

> **Por que você mesmo precisa criar as contas no celular (leva ~15 min)?**
> 1. **Anti-Shadowban (IP Residencial/4G)**: Contas criadas por robô ou em IP de servidor em nuvem nascem com *shadowban* (`0 views`) no TikTok e no Instagram — e na campanha você precisa passar de **5.000 views por vídeo** para receber os **R\$ 4,00/1k**.
> 2. **Verificação Humana & PIX**: TikTok, Instagram e o painel da **Real Oficial** exigem código SMS/e-mail, CAPTCHA e chave **PIX vinculada ao seu CPF/CNPJ** para liberar pagamentos.
> 3. **Tudo mastigado abaixo**: É só copiar e colar nome, `@usuario`, bio pronta (100% dentro da Regra #8 — sem se passar por Rockstar/YouTube) e subir o avatar exclusivo de cada conta que já deixamos pronto em `assets/branding/`.

---

## Truque para criar rápido (sem precisar de 5 chips de celular)

- **No Instagram (15 min p/ as 5 contas)**:
  1. No app do Instagram no celular, toque no seu perfil → menu `☰` → **Configurações** → role até o final → **Adicionar conta** → **Criar nova conta**.
  2. Você pode usar o **mesmo e-mail Gmail com `+`** (ex.: `seuemail+ch1@gmail.com`, `seuemail+ch2@gmail.com`... todos caem na sua caixa principal!) e alternar entre as 5 contas com 1 toque para fixar o 1º comentário.
  3. Em cada conta: vá em **Configurações → Tipo de conta e ferramentas → Mudar para conta profissional → Criador de conteúdo (Gamer / Criador de vídeo)** e mantenha **Conta Pública + Curtidas e Comentários públicos**.
- **No TikTok**:
  1. Crie com `seuemail+tt1@gmail.com` até `+tt5@gmail.com` (ou login Google/Apple secundário). No app do TikTok você consegue manter múltiplas contas logadas no mesmo aparelho (**Perfil → seta ao lado do nome → Adicionar conta**).
  2. Ative **Conta Corporativa / Criador** apenas se quiser usar API direta; mantenha **Biblioteca de Músicas Comerciais** e **Comentários/Curtidas abertos para Todos**.

---

## Dados Prontos para Copiar e Colar (5 Canais PT-BR)

### Conta 1 — `ch1_plantao_oficial`
- **Foto de Perfil**: `assets/branding/avatar_ch1_cortes_leonida.jpg`
- **Nome de Exibição**: `Cortes Live Leonida`
- **@Usuário Principal**: `cortesliveleonida` *(opções caso já exista: `cortesliveleonidabr` / `liveleonidacortes`)*
- **Bio (Copiar e Colar)**:
  ```text
  🎬 Melhores reações e momentos insanos de lives em Leonida!
  ⚡ Cortes em tempo real em PT-BR
  📌 Página de fãs independente (sem afiliação oficial)
  ```

### Conta 2 — `ch2_leonida_segredos`
- **Foto de Perfil**: `assets/branding/avatar_ch2_radar_vice.jpg`
- **Nome de Exibição**: `Radar Streamers Vice`
- **@Usuário Principal**: `radarstreamersvice` *(opções: `radarstreamersvicebr` / `radarlivevice`)*
- **Bio (Copiar e Colar)**:
  ```text
  🔎 Descobertas ao vivo, mapa e segredos dos streamers em Vice City!
  🎮 Cortes diários em PT-BR
  📌 Página de fãs independente (sem afiliação oficial)
  ```

### Conta 3 — `ch3_gta6_dicas_pro`
- **Foto de Perfil**: `assets/branding/avatar_ch3_plays_gta6.jpg`
- **Nome de Exibição**: `Plays & Clutches Leonida`
- **@Usuário Principal**: `playsgta6live` *(opções: `playsgta6livebr` / `playsleonidalive`)*
- **Bio (Copiar e Colar)**:
  ```text
  🔥 Jogadas de alto nível, fugas e gameplay ao vivo dos streamers!
  🎯 Cortes rápidos em PT-BR
  📌 Página de fãs independente (sem afiliação oficial)
  ```

### Conta 4 — `ch4_vice_motores`
- **Foto de Perfil**: `assets/branding/avatar_ch4_fugas_motores.jpg`
- **Nome de Exibição**: `Fugas & Motores Live`
- **@Usuário Principal**: `fugasmotoreslive` *(opções: `fugasmotoreslivebr` / `motoresvicelive`)*
- **Bio (Copiar e Colar)**:
  ```text
  🏎️ Perseguições ao vivo, carros e adrenalina nas ruas de Vice City!
  💨 Cortes de lives em PT-BR
  📌 Página de fãs independente (sem afiliação oficial)
  ```

### Conta 5 — `ch5_vice_stories`
- **Foto de Perfil**: `assets/branding/avatar_ch5_resenha_vice.jpg`
- **Nome de Exibição**: `Cortes Resenha Vice`
- **@Usuário Principal**: `cortesresenhavice` *(opções: `cortesresenhavicebr` / `resenhavicelive`)*
- **Bio (Copiar e Colar)**:
  ```text
  😂 Momentos engraçados, hype positivo e cortes dublados/legendados PT-BR!
  🎙️ Melhores lives em tempo real
  📌 Página de fãs independente (sem afiliação oficial)
  ```

---

## Passo Final: Vincular na Real Oficial e Ativar o Fluxo do Tópico 3 (Pin em 1 Toque)

1. Abra no navegador do seu PC/celular:
   `https://app.realoficial.com.br/c/gta-vi-youtube?ro_vid=a56ec50b-725e-4c73-a353-4e3b2e80678b`
2. Cadastre sua conta, configure sua **chave PIX** e vincule os perfis de **TikTok e Instagram** que você criou acima.
3. Gere seu token MCP/API no painel da Real Oficial e cole em `REAL_OFICIAL_MCP_TOKEN` no arquivo `.env` do 2º PC.
4. Configure `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` no `.env`: sempre que o enxame publicar um corte durante a live (`19/11 01h00` a `20/11 02h00 BRT`), você recebe no Telegram o link direto do post + o bloco copiável com 1 toque:
   - `Assista em https://www.youtube.com/watch?v=KY4DWumZffg`
   - No **Instagram**, o sistema já posta esse comentário automaticamente via API — você só abre e toca em **Fixar (Pin)**.
   - No **TikTok**, você toca no bloco do Telegram (copia na hora), cola no 1º comentário do vídeo, **Fixa (Pin)** e garante que o link entrou no painel da Real Oficial!
