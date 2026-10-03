# Agente de Achadinhos — como colocar pra rodar

Ele roda sozinho e de graça no GitHub, 5x por dia (9h, 12h, 15h, 19h e 21h).
Em cada rodada pega as melhores ofertas e:
- posta no **canal do Telegram** automaticamente (com foto);
- manda a mesma mensagem pro **seu Telegram privado**, pra você copiar e colar no **WhatsApp**.

> Por que o WhatsApp não é automático? Robôs não oficiais no WhatsApp são a
> principal causa de número banido. Copiar e colar 2–3 ofertas leva 1 minuto
> por rodada e protege o grupo que vocês vão pagar anúncio pra encher.

## De onde vêm as ofertas
1. **Shopee (automático)** — quando a conta de afiliado for aprovada, pegue o
   *App ID* e o *Secret* no painel de afiliados (seção Open API).
   O agente busca por palavra-chave e só posta o que tem ≥20% de desconto,
   nota ≥4,5 e 100+ vendas.
2. **Mercado Livre / Amazon (manual)** — cole os produtos no `ofertas.csv`
   com o seu link de afiliado. O agente posta primeiro esses e marca como
   `postado=sim`.

## Passo a passo (uns 30 minutos, uma vez só)
1. **Bot do Telegram:** no Telegram, fale com `@BotFather` → `/newbot` →
   guarde o *token*.
2. **Canal:** crie um canal público (ex.: `@achadinhosdaingrid`) e adicione
   o bot como administrador.
3. **Seu chat privado:** mande "oi" pro seu bot, depois abra
   `https://api.telegram.org/bot<TOKEN>/getUpdates` e copie o número em
   `"chat":{"id": ...}`.
4. **GitHub:** crie uma conta grátis, crie um repositório **privado** e suba
   todos os arquivos desta pasta.
5. Em *Settings → Secrets and variables → Actions*, crie:
   | Nome | Valor |
   |---|---|
   | `TELEGRAM_TOKEN` | token do BotFather |
   | `TELEGRAM_CANAL` | `@nomedocanal` |
   | `TELEGRAM_MEU_CHAT` | o número do passo 3 |
   | `SHOPEE_APP_ID` | (quando aprovar) |
   | `SHOPEE_SECRET` | (quando aprovar) |
6. Vá em *Actions → Postar achadinhos → Run workflow* pra testar na hora.

## Ajustes fáceis
- Nichos que o público curte: troque `PALAVRAS` (no `bot.py` ou como variável).
- Mais ou menos posts: `OFERTAS_POR_RODADA` no arquivo `postar.yml`.
- Horários: linhas `cron` no `postar.yml` (estão em UTC, Brasília = UTC−3).
- Testar no computador sem postar nada: `python bot.py --teste`.
