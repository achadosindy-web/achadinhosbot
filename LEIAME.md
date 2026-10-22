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

## Modo rápido: mandar oferta pelo Telegram
Mande uma mensagem pro bot (no privado) assim:

    https://link-do-produto
    Nome curto do produto
    49,90 / 89,90
    cupom: ACHADO10   (opcional)

- Amazon: pode mandar o link normal do produto; o robô coloca a tag (segredo `AMAZON_TAG`).
- Mercado Livre: mande o link de afiliado (botão Compartilhar no app).
- O bot responde confirmando e a oferta sai na próxima rodada.
- Só aceita mensagens dos chats listados em `TELEGRAM_MEU_CHAT`.

## Radar de ofertas (roupas fitness da Amazon)
O robô lê canais públicos de promoção no Telegram (RADAR_CANAIS), fica só com roupa
fitness da Amazon e troca o link pelo de vocês (AMAZON_TAG).
- `RADAR_MODO: "aprovar"`: manda as sugestões numeradas no seu Telegram; responda `ok 3`
  (ou `ok 3 5`, `ok todas`) e elas entram na fila.
- `RADAR_MODO: "auto"`: posta direto, junto das outras ofertas.
- `RADAR_MODO: "desligado"`: não busca.

## WhatsApp automático (Z-API)
Opcional e não oficial: conecte **só o número dedicado do grupo** na Z-API.
Segredos: `ZAPI_INSTANCIA`, `ZAPI_TOKEN`, `ZAPI_CLIENT_TOKEN` e `WHATSAPP_GRUPO`
(código que termina em `-group`; mande "grupos" pro bot no Telegram para descobrir).
Com os 4 segredos, cada oferta vai pro grupo sozinha (intervalo de 20–45 s entre posts).
Se o envio falhar, a cópia chega no Telegram para colar à mão.

## Vitrine da página ("Olha o que rolou no grupo")
A cada postagem o robô guarda o achado em `vitrine.json` (nome, loja, preço e
horário, **sem link**) e copia esse arquivo pro repositório público `grupo`.
A página lê o arquivo e mostra os 6 últimos, separados em Hoje / Ontem / dia.
Pra ligar, crie o segredo `PAGINA_TOKEN` (token do GitHub com permissão de
escrita só no repositório `grupo`). Sem ele, a página mostra os exemplos fixos.

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

## Dois perfis no mesmo robô (Ingrid e Geral)
O mesmo robô roda duas operações separadas a cada horário:
- **ingrid**: grupo fitness, segredos de sempre, arquivos na raiz, painel em `/painel.html`.
- **geral**: achadinhos em geral, segredos terminados em `_GERAL`, arquivos em
  `perfis/geral/`, mesmo painel (`/painel.html`), na chave "Geral" do topo.

Segredos do perfil geral (Settings > Secrets and variables > Actions):
- `TELEGRAM_TOKEN_GERAL` (obrigatório): token de um **bot novo** do @BotFather.
  Enquanto ele não existir, o perfil geral não roda.
- `WHATSAPP_GRUPO_GERAL`: código do(s) grupo(s). Vários grupos separados por vírgula
  (ex.: `1203...@g.us,1203...@g.us`). Mande "grupos" pro bot geral para ver os códigos.
- `TELEGRAM_CANAL_GERAL` (opcional): canal(is) do Telegram, separados por vírgula.
- Opcionais, se faltarem usam os da Ingrid: `TELEGRAM_MEU_CHAT_GERAL`, `AMAZON_TAG_GERAL`,
  `GREEN_ID_GERAL`, `GREEN_TOKEN_GERAL`, `GREEN_URL_GERAL`.

O botão "Enviar agora" de cada painel roda só o perfil dele. O cron-job.org roda os dois.
`WHATSAPP_GRUPO` e `TELEGRAM_CANAL` da Ingrid também aceitam vários grupos/canais com vírgula.

## Perfil Black Friday
Terceiro perfil, igual ao geral, mas o garimpo só guarda achados com **40% OFF ou mais**
(`DESCONTO_GARIMPO` no postar.yml). Arquivos em `perfis/blackfriday/`, aba "Black Friday" no painel.
Segredos: `TELEGRAM_TOKEN_BF` (obrigatório, outro bot novo do @BotFather) e `WHATSAPP_GRUPO_BF`.
Opcionais (se faltarem, usam os da Ingrid): `TELEGRAM_CANAL_BF`, `TELEGRAM_MEU_CHAT_BF`, `AMAZON_TAG_BF`,
`GREEN_ID_BF`, `GREEN_TOKEN_BF`, `GREEN_URL_BF`. Para desligar depois da Black Friday, apague o segredo `TELEGRAM_TOKEN_BF`.
