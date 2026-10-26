"""
Agente de Achadinhos
--------------------
Busca as melhores ofertas da Shopee (API oficial de afiliados) e/ou de uma
lista manual (ofertas.csv, para Mercado Livre e Amazon), monta a mensagem
pronta e posta no canal do Telegram.

Para o WhatsApp, o agente NAO dispara sozinho (automacao nao oficial pode
banir o numero). Ele manda a mesma mensagem num chat privado seu no
Telegram; voce so copia e cola no grupo/canal do WhatsApp (leva 10 segundos).

Rodar:  python bot.py            (posta de verdade)
        python bot.py --teste    (so mostra no terminal, nao posta nada)
"""

import csv
import hashlib
import re
import json
import os
import random
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from html import unescape as html_unescape

import cartao
import radar

PASTA = Path(__file__).parent
# PERFIL separa os dados de cada operação (ex.: "geral" -> perfis/geral/).
# Sem PERFIL (ou "ingrid") os arquivos ficam na raiz, como sempre foi.
PERFIL = os.getenv("PERFIL", "").strip().lower()
DADOS = PASTA if PERFIL in ("", "ingrid") else PASTA / "perfis" / PERFIL
DADOS.mkdir(parents=True, exist_ok=True)
HISTORICO = DADOS / "historico.json"
OFERTAS_MANUAIS = DADOS / "ofertas.csv"
VITRINE = DADOS / "vitrine.json"   # últimos achados, mostrados na página do grupo
GARIMPO = DADOS / "garimpo.json"   # tudo que o radar achou, mostrado no painel de vocês

# ---------- Configuracao (vem das variaveis de ambiente / segredos) ----------
SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "").strip()
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CANAL = os.getenv("TELEGRAM_CANAL", "").strip()          # ex: @achadinhosdaingrid (pode ser lista: @a,@b)
CANAIS_TELEGRAM = [c.strip() for c in TELEGRAM_CANAL.split(",") if c.strip()]
TELEGRAM_MEU_CHAT = os.getenv("TELEGRAM_MEU_CHAT", "").strip()    # seu chat privado (copia p/ WhatsApp)
NOME_GRUPO = os.getenv("NOME_GRUPO", "Achadinhos")
AMAZON_TAG = os.getenv("AMAZON_TAG", "").strip()          # ex: achadosindy-20
# WhatsApp automático via Z-API (não oficial: use o número dedicado do grupo)
ZAPI_INSTANCIA = os.getenv("ZAPI_INSTANCIA", "").strip()
ZAPI_TOKEN = os.getenv("ZAPI_TOKEN", "").strip()
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "").strip()
WHATSAPP_GRUPO = os.getenv("WHATSAPP_GRUPO", "").strip()           # ex: 120363...-group (pode ser lista: a,b,c)
GRUPOS_WHATSAPP = [g.strip() for g in WHATSAPP_GRUPO.split(",") if g.strip()]
# Cartão com a marca do grupo (foto + preço + logo). "nao" volta a mandar só a foto.
CARTAO = os.getenv("CARTAO", "sim").strip().lower() not in ("nao", "não", "0", "false")
# Alternativa grátis: Green-API (plano Developer, até 3 conversas: basta pro grupo)
GREEN_ID = os.getenv("GREEN_ID", "").strip()
GREEN_TOKEN = os.getenv("GREEN_TOKEN", "").strip()
GREEN_URL = (os.getenv("GREEN_URL", "").strip() or "https://api.green-api.com").rstrip("/")
# Radar de canais do Telegram: "aprovar" (te manda pra aprovar), "auto" (posta direto) ou "desligado"
RADAR_MODO = os.getenv("RADAR_MODO", "aprovar").strip().lower()
RADAR_SUGESTOES = int(os.getenv("RADAR_SUGESTOES", "5"))     # sugestões por rodada no modo aprovar

# Quantas ofertas por execucao e palavras-chave para garimpar
OFERTAS_POR_RODADA = int(os.getenv("OFERTAS_POR_RODADA", "3"))
# O que vocês aprovaram (painel/Telegram) sai TODO na mesma rodada, até esse limite de segurança
MAX_APROVADAS = int(os.getenv("MAX_APROVADAS", "10"))
PALAVRAS = [p.strip() for p in os.getenv(
    "PALAVRAS",
    "legging fitness,conjunto fitness feminino,top academia,short academia feminino,"
    "vestido feminino,skincare,maquiagem,organizador casa,utensilios cozinha,garrafa termica"
).split(",") if p.strip()]

# Filtros de qualidade: so posta o que vale a pena
DESCONTO_MINIMO = int(os.getenv("DESCONTO_MINIMO", "20"))   # % de desconto
NOTA_MINIMA = float(os.getenv("NOTA_MINIMA", "4.5"))        # estrelas
VENDAS_MINIMAS = int(os.getenv("VENDAS_MINIMAS", "100"))
DIAS_SEM_REPETIR = 7
# Garimpo: só guarda achados com pelo menos esse desconto (0 = guarda tudo). Black Friday usa 40.
DESCONTO_GARIMPO = int(os.getenv("DESCONTO_GARIMPO", "0"))
# Garimpo por categoria (nomes iguais aos do painel, separados por vírgula):
#   CATEGORIAS_GARIMPO = só essas entram  |  CATEGORIAS_FORA = essas não entram
CATEGORIAS_GARIMPO = {c.strip().lower() for c in os.getenv("CATEGORIAS_GARIMPO", "").split(",") if c.strip()}
CATEGORIAS_FORA = {c.strip().lower() for c in os.getenv("CATEGORIAS_FORA", "").split(",") if c.strip()}
# Modo "automatico": só posta sozinho achados do garimpo com até tantas horas (preço ainda fresco)
AUTO_HORAS = float(os.getenv("AUTO_HORAS", "6"))
# Categorias que saem primeiro quando há mais achados do que vagas na rodada (ex.: Black Friday)
# Variedade: o mesmo tipo de produto (ex.: fralda) só volta depois de tantas horas,
# e no máximo tantas ofertas da mesma categoria numa rodada
HORAS_MESMO_TIPO = float(os.getenv("HORAS_MESMO_TIPO", "12"))
MAX_POR_CATEGORIA = int(os.getenv("MAX_POR_CATEGORIA", "2"))
PRIORIDADE = [c.strip().lower() for c in os.getenv("CATEGORIAS_PRIORIDADE", "").split(",") if c.strip()]

SHOPEE_URL = "https://open-api.affiliate.shopee.com.br/graphql"
BR = timezone(timedelta(hours=-3))


# ------------------------------- Shopee --------------------------------------
def shopee_buscar(palavra: str, limite: int = 20) -> list[dict]:
    """Consulta productOfferV2 na API oficial de afiliados da Shopee Brasil."""
    query = """
    query($keyword: String, $limit: Int, $sortType: Int) {
      productOfferV2(keyword: $keyword, limit: $limit, sortType: $sortType) {
        nodes {
          itemId productName priceMin priceMax priceDiscountRate
          commissionRate sales ratingStar imageUrl offerLink shopName
        }
      }
    }"""
    # sortType 2 = mais vendidos (confira na doc do painel; da pra trocar)
    corpo = json.dumps({
        "query": query,
        "variables": {"keyword": palavra, "limit": limite,
                      "sortType": int(os.getenv("SHOPEE_SORT", "2"))},
    }, separators=(",", ":"))
    ts = str(int(time.time()))
    assinatura = hashlib.sha256(
        (SHOPEE_APP_ID + ts + corpo + SHOPEE_SECRET).encode()
    ).hexdigest()
    cab = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={SHOPEE_APP_ID}, "
                         f"Timestamp={ts}, Signature={assinatura}",
    }
    r = requests.post(SHOPEE_URL, data=corpo, headers=cab, timeout=30)
    r.raise_for_status()
    dados = r.json()
    if dados.get("errors"):
        raise RuntimeError(f"Shopee respondeu erro: {dados['errors']}")
    nos = dados["data"]["productOfferV2"]["nodes"] or []

    ofertas = []
    for n in nos:
        try:
            ofertas.append({
                "id": f"shopee-{n['itemId']}",
                "loja": "Shopee",
                "nome": n["productName"],
                "preco": float(n["priceMin"]),
                "desconto": int(n.get("priceDiscountRate") or 0),
                "comissao": float(n.get("commissionRate") or 0),
                "vendas": int(n.get("sales") or 0),
                "nota": float(n.get("ratingStar") or 0),
                "imagem": n.get("imageUrl"),
                "link": n["offerLink"],
            })
        except (KeyError, TypeError, ValueError):
            continue
    return ofertas


# --------------------------- Ofertas manuais ---------------------------------
def ler_manuais() -> list[dict]:
    """ofertas.csv: Mercado Livre, Amazon ou qualquer link de afiliado seu."""
    if not OFERTAS_MANUAIS.exists():
        return []
    ofertas = []
    with open(OFERTAS_MANUAIS, encoding="utf-8") as f:
        for i, l in enumerate(csv.DictReader(f)):
            if not (l.get("link") or "").strip() or l.get("postado", "").strip().lower() == "sim":
                continue
            preco = float(l["preco"].replace(",", "."))
            antes = float((l.get("preco_antes") or "0").replace(",", ".") or 0)
            ofertas.append({
                "id": "manual-" + hashlib.md5(l["link"].encode()).hexdigest()[:10],
                "loja": l.get("loja", "").strip() or "Oferta",
                "nome": l["produto"].strip(),
                "preco": preco,
                "preco_antes": antes,
                "desconto": round((1 - preco / antes) * 100) if antes > preco else 0,
                "comissao": 0, "vendas": 0, "nota": 5,
                "imagem": (l.get("imagem") or "").strip() or None,
                "link": l["link"].strip(),
                "cupom": (l.get("cupom") or "").strip(),
                "manual": True,
            })
    return ofertas


# ----------------------------- Selecao ---------------------------------------
def carregar_historico() -> dict:
    if HISTORICO.exists():
        return json.loads(HISTORICO.read_text())
    return {}


def salvar_historico(h: dict) -> None:
    limite = time.time() - 30 * 86400
    h = {k: v for k, v in h.items() if k.startswith("_") or v > limite}
    HISTORICO.write_text(json.dumps(h, indent=1))


def pontuar(o: dict) -> float:
    """Nota da oferta: desconto pesa mais, depois prova social e comissao.
    Categorias em CATEGORIAS_PRIORIDADE ganham bônus (a primeira da lista ganha mais)."""
    cat = (o.get("categoria") or "").lower()
    bonus = (len(PRIORIDADE) - PRIORIDADE.index(cat)) * 15 if cat in PRIORIDADE else 0
    return (bonus + o["desconto"] * 2
            + min(o["vendas"], 5000) / 100
            + o["comissao"] * 100
            + (o["nota"] - 4) * 10)


def escolher(ofertas: list[dict], historico: dict) -> list[dict]:
    corte = time.time() - DIAS_SEM_REPETIR * 86400
    boas = []
    for o in ofertas:
        if historico.get(o["id"], 0) > corte:
            continue
        if not o.get("manual") and not o.get("radar") and (o["desconto"] < DESCONTO_MINIMO
                                    or o["nota"] < NOTA_MINIMA
                                    or o["vendas"] < VENDAS_MINIMAS):
            continue
        boas.append(o)
    # manuais primeiro (voce escolheu a dedo), depois as melhores da Shopee
    boas.sort(key=lambda o: (not o.get("manual"), not o.get("radar"), -pontuar(o)))
    # Aprovadas por vocês (manuais) saem todas, até MAX_APROVADAS;
    # as automáticas (radar/Shopee) preenchem até OFERTAS_POR_RODADA.
    # Variedade nas automáticas: 1 por tipo de produto na rodada, o mesmo tipo só volta
    # depois de HORAS_MESMO_TIPO, e no máximo MAX_POR_CATEGORIA da mesma categoria por rodada.
    vistos, final = set(), []
    n_manual = n_auto = 0
    tipos_rodada, cats_rodada = set(), {}
    corte_tipo = time.time() - HORAS_MESMO_TIPO * 3600
    for o in boas:
        if o["id"] in vistos or chave_nome(o.get("nome", "")) in vistos:
            continue
        if o.get("manual"):
            if n_manual >= MAX_APROVADAS:
                continue
            n_manual += 1
        else:
            if n_auto >= OFERTAS_POR_RODADA:
                continue
            tipo = chave_tipo(o.get("nome", ""))
            cat = (o.get("categoria") or "Outros").lower()
            if tipo and (tipo in tipos_rodada or historico.get(tipo, 0) > corte_tipo):
                continue
            if cats_rodada.get(cat, 0) >= MAX_POR_CATEGORIA:
                continue
            if tipo:
                tipos_rodada.add(tipo)
            cats_rodada[cat] = cats_rodada.get(cat, 0) + 1
            n_auto += 1
        vistos.update({o["id"], chave_nome(o.get("nome", ""))})
        final.append(o)
    return final


# ----------------------------- Mensagem --------------------------------------
CHAMADAS = ["🔥 ACHADINHO DO DIA", "😱 PRECINHO ABSURDO", "⚡ CORRE QUE ACABA",
            "💥 OFERTA RELÂMPAGO", "🛒 VALE CADA CENTAVO"]


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_mensagem(o: dict) -> str:
    nome = o["nome"] if len(o["nome"]) <= 90 else o["nome"][:87] + "..."
    linhas = [random.choice(CHAMADAS), "", f"📦 {nome}", ""]
    antes = o.get("preco_antes") or (
        o["preco"] / (1 - o["desconto"] / 100) if 0 < o["desconto"] < 100 else 0)
    if antes and antes > o["preco"]:
        linhas.append(f"❌ De {brl(antes)}")
    linhas.append(f"✅ Por {brl(o['preco'])}" +
                  (f"  ({o['desconto']}% OFF)" if o["desconto"] else ""))
    if o.get("cupom"):
        linhas.append(f"🎟️ Cupom: {o['cupom']}")
    if o["vendas"] >= 1000:
        linhas.append(f"⭐ {o['nota']:.1f}".replace(".", ",") + " • +"
                      f"{o['vendas']//1000} mil vendidos")
    artigo = "no" if o["loja"].lower().startswith("mercado") else "na"
    linhas += ["", f"🛍️ Comprar {artigo} {o['loja']}:", o["link"], "",
               "⚠️ Preço e estoque podem mudar a qualquer momento."]
    return "\n".join(linhas)


# ----------------------------- Telegram --------------------------------------
def telegram(chat: str, texto: str, imagem: str | None) -> None:
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    if imagem and len(texto) <= 1000:
        if isinstance(imagem, bytes):
            r = requests.post(f"{base}/sendPhoto", timeout=60, data={"chat_id": chat, "caption": texto},
                              files={"photo": ("oferta.jpg", imagem, "image/jpeg")})
        else:
            r = requests.post(f"{base}/sendPhoto", timeout=30,
                              data={"chat_id": chat, "photo": imagem, "caption": texto})
        if r.ok:
            return
    dados = {"chat_id": chat, "text": texto}
    # Link de vitrine do Mercado Livre (meli.la / /social/) gera prévia de outro
    # produto; nesses casos desliga a prévia automática.
    if re.search(r"meli\.la/|mercadolivre\.com\.br/social/", texto):
        dados["link_preview_options"] = json.dumps({"is_disabled": True})
    r = requests.post(f"{base}/sendMessage", timeout=30, data=dados)
    r.raise_for_status()



# ----------------------------- WhatsApp (Z-API) -------------------------------
def _zapi_ids() -> tuple[str, str]:
    """Aceita o ID/token puros ou a URL inteira colada por engano no segredo."""
    junto = f"{ZAPI_INSTANCIA} {ZAPI_TOKEN}"
    m = re.search(r"instances/([A-Za-z0-9]+)/token/([A-Za-z0-9]+)", junto)
    if m:
        return m.group(1), m.group(2)
    inst = re.findall(r"[A-Za-z0-9]{16,}", ZAPI_INSTANCIA)
    tok = re.findall(r"[A-Za-z0-9]{16,}", ZAPI_TOKEN)
    return (inst[0] if inst else ZAPI_INSTANCIA), (tok[0] if tok else ZAPI_TOKEN)


def _zapi_diagnostico() -> str:
    inst, tok = _zapi_ids()
    return (f"(conferência: ID da instância com {len(inst)} caracteres — o normal é 32; "
            f"token com {len(tok)} — o normal é 24; Client-Token com {len(ZAPI_CLIENT_TOKEN)})")


def _zapi(caminho: str, dados: dict | None = None, metodo: str = "post"):
    inst, tok = _zapi_ids()
    url = f"https://api.z-api.io/instances/{inst}/token/{tok}/{caminho}"
    r = requests.request(metodo, url, json=dados, timeout=60,
                         headers={"Client-Token": ZAPI_CLIENT_TOKEN})
    r.raise_for_status()
    return r.json() if r.content else {}


def _imagem_para_whatsapp(imagem: str | None) -> str | None:
    """URL pública vai direto; foto mandada no Telegram (file_id) vira base64."""
    if not imagem:
        return None
    if isinstance(imagem, bytes):
        import base64
        return "data:image/jpeg;base64," + base64.b64encode(imagem).decode()
    if imagem.startswith("http"):
        return imagem
    try:
        base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
        info = requests.get(f"{base}/getFile", params={"file_id": imagem}, timeout=30).json()
        caminho = info["result"]["file_path"]
        foto = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{caminho}", timeout=60)
        foto.raise_for_status()
        import base64
        return "data:image/jpeg;base64," + base64.b64encode(foto.content).decode()
    except Exception as e:
        print(f"[aviso] não consegui baixar a foto do Telegram: {e}")
        return None


def _usa_green() -> bool:
    return bool(GREEN_ID and GREEN_TOKEN)


def whatsapp_ligado() -> bool:
    if not GRUPOS_WHATSAPP:
        return False
    return _usa_green() or all([ZAPI_INSTANCIA, ZAPI_TOKEN, ZAPI_CLIENT_TOKEN])


def _green(metodo_api: str, **kw):
    url = f"{GREEN_URL}/waInstance{GREEN_ID}/{metodo_api}/{GREEN_TOKEN}"
    metodo = kw.pop("metodo", "post")
    r = requests.request(metodo, url, timeout=60, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def _grupo_green(g: str) -> str:
    return g.replace("-group", "@g.us") if g.endswith("-group") else g


def _imagem_bytes(imagem) -> bytes | None:
    if not imagem:
        return None
    if isinstance(imagem, bytes):
        return imagem
    try:
        if imagem.startswith("http"):
            r = requests.get(imagem, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        else:
            base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
            info = requests.get(f"{base}/getFile", params={"file_id": imagem}, timeout=30).json()
            r = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/"
                             f"{info['result']['file_path']}", timeout=60)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"[aviso] não consegui baixar a imagem: {e}")
        return None


def _whatsapp_um(grupo: str, texto: str, imagem: str | None, foto: bytes | None) -> None:
    if _usa_green():
        chat = _grupo_green(grupo)
        if foto:
            resp = _green("sendFileByUpload", data={"chatId": chat, "caption": texto},
                          files={"file": ("achadinho.jpg", foto, "image/jpeg")})
        else:
            resp = _green("sendMessage", json={"chatId": chat, "message": texto})
        print(f"[whatsapp] Green-API -> {chat}: {resp}")
        if not (isinstance(resp, dict) and resp.get("idMessage")):
            raise RuntimeError(f"Green-API não confirmou o envio: {resp}")
        return
    img = _imagem_para_whatsapp(imagem)
    if img:
        resp = _zapi("send-image", {"phone": grupo, "image": img, "caption": texto})
    else:
        resp = _zapi("send-text", {"phone": grupo, "message": texto})
    print(f"[whatsapp] Z-API -> {grupo}: {resp}")
    if isinstance(resp, dict) and resp.get("error"):
        raise RuntimeError(f"Z-API recusou: {resp}")


def whatsapp(texto: str, imagem: str | None) -> int:
    """Manda para todos os grupos de WHATSAPP_GRUPO. Devolve em quantos deu certo."""
    foto = _imagem_bytes(imagem) if _usa_green() else None   # baixa a foto uma vez só
    certos, erros = 0, []
    for i, grupo in enumerate(GRUPOS_WHATSAPP):
        if i:
            time.sleep(random.randint(6, 12))   # respiro entre um grupo e outro
        try:
            _whatsapp_um(grupo, texto, imagem, foto)
            certos += 1
        except Exception as e:
            erros.append(f"{grupo}: {e}")
            print(f"[aviso] WhatsApp {grupo} falhou: {e}")
    if not certos:
        raise RuntimeError("; ".join(erros) or "nenhum grupo configurado")
    return certos


def _foto_amazon(link: str) -> str | None:
    """A Amazon bloqueia robôs na página, mas serve a foto pelo código do produto (ASIN)."""
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", link)
    if not m:
        return None
    for modelo in ("https://images-na.ssl-images-amazon.com/images/P/{a}.01._SCLZZZZZZZ_.jpg",
                   "https://m.media-amazon.com/images/P/{a}.01._SCLZZZZZZZ_.jpg"):
        url = modelo.format(a=m.group(1))
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) > 2000:
                return url
        except Exception:
            continue
    return None


def _foto_valida(b: bytes | None) -> bool:
    """Descarta imagem vazia, pixel de 1x1 ou tudo de uma cor só."""
    if not b or len(b) < 1500:
        return False
    try:
        from PIL import Image, ImageStat
        import io
        im = Image.open(io.BytesIO(b)).convert("RGB")
        if im.width < 150 or im.height < 150:
            return False
        return max(ImageStat.Stat(im.resize((64, 64))).stddev) > 8
    except ImportError:
        return True
    except Exception:
        return False


def foto_boa(o: dict) -> tuple[str | None, bytes | None]:
    """Procura uma foto de verdade do produto, conferindo cada uma.
    Ordem: foto oficial da Amazon -> fotos da página da Amazon -> foto da página do produto
    -> foto que veio do canal (último recurso)."""
    candidatas = []
    asin = radar.asin_de(o["link"])
    if asin:
        candidatas += [f"https://m.media-amazon.com/images/P/{asin}.01._SCLZZZZZZZ_.jpg",
                       f"https://images-na.ssl-images-amazon.com/images/P/{asin}.01._SCLZZZZZZZ_.jpg"]
        candidatas += radar.imagens_na_amazon(asin)
    elif not ("amazon." in o["link"] or "amzn." in o["link"]):
        og = imagem_do_link(o["link"])
        if og:
            candidatas.append(og)
    if o.get("imagem"):
        candidatas.append(o["imagem"])
    for c in candidatas:
        b = _imagem_bytes(c)
        if _foto_valida(b):
            return c, b
    print(f"[aviso] não achei foto boa para: {o['nome'][:50]}")
    return o.get("imagem"), None


def foto_do_produto(o: dict):
    """Foto oficial do produto, nunca a arte de outro canal (que vem com o logo deles).
    Amazon: foto oficial pelo código do produto. Outras lojas: foto da página do produto.
    Foto que veio de canal do Telegram só é usada se não houver outra."""
    atual = o.get("imagem")
    do_canal = isinstance(atual, str) and ("telesco.pe" in atual or "telegram" in atual)
    if "amazon." in o["link"] or "amzn." in o["link"]:
        oficial = _foto_amazon(o["link"])
        if oficial:
            return oficial
    if not atual or do_canal:
        return imagem_do_link(o["link"]) or atual
    return atual


def imagem_do_link(link: str) -> str | None:
    """Pega a foto do produto da prévia do link (og:image), como o Telegram faz."""
    if "amazon." in link:
        foto = _foto_amazon(link)
        if foto:
            return foto
    try:
        r = requests.get(link, timeout=20, allow_redirects=True,
                         headers={"User-Agent": "TelegramBot (like TwitterBot)"})
        m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', r.text) or
             re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', r.text))
        return html_unescape(m.group(1)) if m else None
    except Exception as e:
        print(f"[aviso] não consegui pegar a foto do link: {e}")
        return None


def _como_lista(resp) -> list:
    """A Z-API às vezes devolve a lista direto, às vezes dentro de um objeto."""
    if isinstance(resp, list):
        return [x for x in resp if isinstance(x, dict)]
    if isinstance(resp, dict):
        for v in resp.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def listar_grupos_whatsapp() -> str:
    if _usa_green():
        contatos = _green("getContacts", metodo="get") or []
        grupos = [c for c in contatos if isinstance(c, dict) and
                  (c.get("type") == "group" or str(c.get("id", "")).endswith("@g.us"))]
        if not grupos:
            return f"Não achei grupos nesse número (Green-API). Resposta: {str(contatos)[:300]}"
        return ("Grupos do WhatsApp conectado (Green-API):\n\n" +
                "\n".join(f"• {g.get('name') or '(sem nome)'}\n  {g.get('id')}" for g in grupos) +
                "\n\nCopie o código do grupo (termina em @g.us) para o segredo WHATSAPP_GRUPO (no perfil geral, WHATSAPP_GRUPO_GERAL; vários grupos separados por vírgula).")
    tentativas = []
    grupos = []
    for caminho in ("groups?page=1&pageSize=50", "chats?page=1&pageSize=200"):
        try:
            resp = _zapi(caminho, metodo="get")
        except Exception as e:
            tentativas.append(f"{caminho.split('?')[0]}: {e}")
            continue
        itens = _como_lista(resp)
        grupos = [g for g in itens if g.get("isGroup") or str(g.get("phone", "")).endswith("-group")]
        if grupos:
            break
        tentativas.append(f"{caminho.split('?')[0]}: {str(resp)[:300]}")
    if not grupos:
        return ("Não achei grupos nesse número do WhatsApp. Resposta da Z-API:\n" +
                "\n".join(tentativas) + "\n\n" + _zapi_diagnostico())
    linhas = [f"• {g.get('name') or g.get('subject') or '(sem nome)'}\n  {g.get('phone')}" for g in grupos]
    return ("Grupos do WhatsApp conectado:\n\n" + "\n".join(linhas) +
            "\n\nCopie o código do grupo (termina em -group) para o segredo WHATSAPP_GRUPO (no perfil geral, WHATSAPP_GRUPO_GERAL; vários grupos separados por vírgula).")


def marcar_manual_postada(link: str) -> None:
    linhas = list(csv.DictReader(open(OFERTAS_MANUAIS, encoding="utf-8")))
    campos = list(linhas[0].keys()) if linhas else []
    if "postado" not in campos:
        campos.append("postado")
    for l in linhas:
        if l["link"].strip() == link:
            l["postado"] = "sim"
    with open(OFERTAS_MANUAIS, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas)


# ------------------- Modo rapido: ofertas mandadas pro bot -------------------
import re
from urllib.parse import urlparse

HORARIOS = [9, 12, 15, 19, 21]
AJUDA = (
    "🤖 Me mande uma oferta assim (pode ser como legenda de uma foto do produto):\n\n"
    "https://link-do-produto\n"
    "Nome curto do produto\n"
    "49,90 / 89,90\n"
    "cupom: ACHADO10   (opcional)\n\n"
    "• Amazon: pode mandar o link normal do produto, eu coloco a tag de afiliado.\n"
    "• Mercado Livre: mande o link de afiliado (botão Compartilhar no app).\n"
    "• O primeiro preço é o atual; o segundo, o antigo (opcional).\n"
    "A oferta sai na próxima rodada.\n\n"
    "🔎 Radar: quando eu te mandar sugestões numeradas, responda\n"
    "ok 3   (ou: ok 3 5 7   |   ok todas)\n"
    "e elas entram na fila."
)
_NUM = r"(?:R\$\s*)?\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|(?:R\$\s*)?\d+(?:[.,]\d{1,2})?"
_LINHA_PRECO = re.compile(
    rf"^\s*(?:de\s+|por\s+)?({_NUM})\s*(?:(?:/|\||-|a|por|de)\s*({_NUM}))?\s*$", re.I)


def _valor(txt: str) -> float:
    t = txt.replace("R$", "").strip()
    if "," in t:                       # 1.299,90 -> 1299.90
        t = t.replace(".", "").replace(",", ".")
    return float(t)


def _loja(url: str) -> str:
    h = urlparse(url).netloc.lower()
    if "amazon" in h or "amzn" in h:
        return "Amazon"
    if "mercadoli" in h or "mercadolibre" in h or "meli" in h:
        return "Mercado Livre"
    if "shopee" in h or "shp.ee" in h:
        return "Shopee"
    return "Oferta"


def link_amazon(url: str) -> str:
    """Link de produto da Amazon -> link limpo com a tag de afiliado."""
    m = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", url)
    if m and AMAZON_TAG:
        return f"https://www.amazon.com.br/dp/{m.group(1)}?tag={AMAZON_TAG}"
    return url  # link curto (amzn.to) ou já com tag: usa como veio


def _nome_da_url(url: str) -> str:
    p = urlparse(url).path
    m = re.search(r"/([^/]+)/dp/", p) or re.search(r"MLB-?\d+-([^/_]+)", p)
    return m.group(1).replace("-", " ").strip()[:80].capitalize() if m else ""


_UA_PREVIA = {"User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
              "Accept-Language": "pt-BR,pt;q=0.9"}


def dados_da_pagina(url: str) -> dict:
    """Nome, foto e preço que a loja mostra na prévia do link (o mesmo que o WhatsApp usa).
    Serve pra quando vocês mandam só o link (ex.: Shopee copiado do app)."""
    try:
        r = requests.get(url, headers=_UA_PREVIA, timeout=20, allow_redirects=True)
        h = r.text or ""
    except Exception as e:
        print(f"[aviso] não consegui abrir o link pra ler os dados: {e}")
        return {}
    def meta(*nomes):
        for n in nomes:
            m = (re.search(rf'<meta[^>]+(?:property|name|itemprop)=["\']{n}["\'][^>]+content=["\']([^"\']+)', h, re.I) or
                 re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name|itemprop)=["\']{n}["\']', h, re.I))
            if m:
                return html_unescape(m.group(1)).strip()
        return ""
    titulo = meta("og:title", "twitter:title")
    if not titulo:
        m = re.search(r"<title[^>]*>([^<]+)</title>", h, re.I)
        titulo = html_unescape(m.group(1)).strip() if m else ""
    titulo = re.sub(r"\s*[|\-–]\s*(Shopee Brasil|Shopee|Mercado Livre|MercadoLivre|Magalu|Amazon\.com\.br).*$", "", titulo, flags=re.I)
    titulo = re.sub(r"^Amazon\.com\.br\s*:\s*", "", titulo, flags=re.I)
    preco = 0.0
    bruto = meta("product:price:amount", "og:price:amount", "price")
    if bruto:
        try:
            preco = float(bruto.replace(".", "").replace(",", ".")) if "," in bruto else float(bruto)
        except ValueError:
            preco = 0.0
    antes = 0.0
    if not preco:
        p_agora, p_antes = radar.precos(meta("og:description", "description"))
        preco, antes = p_agora, p_antes
    return {"nome": titulo[:90], "imagem": meta("og:image", "twitter:image"), "preco": preco, "preco_antes": antes}


def interpretar(texto: str) -> tuple[dict | None, str]:
    """Transforma a mensagem numa oferta. Devolve (oferta, problema)."""
    urls = re.findall(r"https?://\S+", texto)
    if not urls:
        return None, "não achei nenhum link"
    url = urls[0].rstrip(").,")
    resto = texto.replace(urls[0], "\n")
    nome, cupom, precos = "", "", []
    for linha in [l.strip() for l in resto.splitlines() if l.strip()]:
        if linha.lower().startswith("cupom"):
            cupom = linha.split(":", 1)[-1].replace("cupom", "").strip()
            continue
        m = _LINHA_PRECO.match(linha)
        if m and not precos:
            precos = [_valor(g) for g in m.groups() if g]
            continue
        if not nome:
            nome = linha.replace(",", " ")[:90]
    if not precos:
        # Post copiado de outro grupo/canal: usa o leitor do radar (emojis, "De R$ ... por R$ ...")
        p_agora, p_antes = radar.precos(texto)
        if p_agora:
            precos = [p_agora] + ([p_antes] if p_antes else [])
            nome = radar.nome_produto(resto) or nome
            if not cupom:
                cupom = radar.cupom(texto)
    pagina = {}
    if not precos or not nome:
        # Mandou só o link (ou link + preço): busca nome, foto e preço na própria loja
        pagina = dados_da_pagina(url)
        if not precos and pagina.get("preco"):
            precos = [pagina["preco"]] + ([pagina["preco_antes"]] if pagina.get("preco_antes") else [])
        if not nome and pagina.get("nome"):
            nome = pagina["nome"]
    if not precos:
        return None, ("não consegui ver o preço na loja. Manda de novo assim:\n" + url + "\n23,80"
                      if pagina.get("nome") else "faltou o preço (ex: 49,90 / 89,90)")
    if re.search(r"CODIGO-DO-PRODUTO|link-do-produto|SEU_LINK", url, re.I) or nome.lower() in ("nome do produto", "nome curto do produto"):
        return None, "essa é a mensagem de exemplo; mande o link e o nome de um produto de verdade"
    loja = _loja(url)
    nome = nome or _nome_da_url(url) or "Achadinho do dia"
    if loja == "Amazon":
        if "amzn." in url:
            try:
                asin = radar.resolver_amazon(url)
                if asin:
                    url = radar.link_nosso(asin)
            except Exception:
                pass
        url = link_amazon(url)
        if not radar.asin_de(url):
            return None, "esse link da Amazon não tem o código do produto (abra o produto e copie o link da página dele)"
    preco, antes = min(precos), (max(precos) if len(precos) > 1 else 0)
    return {"loja": loja, "produto": nome, "preco": f"{preco:.2f}",
            "preco_antes": f"{antes:.2f}" if antes > preco else "",
            "cupom": cupom, "link": url, "imagem": pagina.get("imagem", ""), "postado": ""}, ""


def _proximo_horario() -> str:
    agora = datetime.now(BR)
    for h in HORARIOS:
        if agora.hour < h:
            return f"hoje às {h}h"
    return f"amanhã às {HORARIOS[0]}h"


def _responder(chat, texto):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": chat, "text": texto}, timeout=20)
    except Exception:
        pass


def _por_na_fila(oferta: dict, existentes: set) -> bool:
    if oferta["link"] in existentes:
        return False
    novo = not OFERTAS_MANUAIS.exists() or OFERTAS_MANUAIS.stat().st_size == 0
    campos = ["loja", "produto", "preco", "preco_antes", "cupom", "link", "imagem", "postado"]
    with open(OFERTAS_MANUAIS, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        if novo:
            w.writeheader()
        w.writerow(oferta)
    existentes.add(oferta["link"])
    return True


def oferta_do_formulario() -> int:
    """Oferta preenchida no formulário "Postar algo que vi" do painel (chega pelo GitHub)."""
    link = os.getenv("OFERTA_LINK", "").strip()
    if not link:
        return 0
    nome = os.getenv("OFERTA_NOME", "").strip()
    preco = os.getenv("OFERTA_PRECO", "").strip()
    antes = os.getenv("OFERTA_PRECO_ANTES", "").strip()
    cupom_txt = os.getenv("OFERTA_CUPOM", "").strip()
    texto = f"{link}\n{nome}\n{preco}" + (f" / {antes}" if antes else "") + (f"\ncupom: {cupom_txt}" if cupom_txt else "")
    oferta, problema = interpretar(texto)
    if not oferta:
        print(f"[formulário] não deu pra postar: {problema}")
        for chat in [c.strip() for c in TELEGRAM_MEU_CHAT.split(",") if c.strip()]:
            _responder(chat, f"⚠️ A oferta do painel não saiu: {problema}.")
        return 0
    existentes = set()
    if OFERTAS_MANUAIS.exists():
        existentes = {l.get("link", "").strip() for l in csv.DictReader(open(OFERTAS_MANUAIS, encoding="utf-8"))}
    if not _por_na_fila(oferta, existentes):
        print("[formulário] essa oferta já estava na fila.")
        return 0
    print(f"[formulário] na fila: {oferta['produto']} ({oferta['loja']})")
    return 1


def _radar_para_fila(o: dict) -> dict:
    return {"loja": o["loja"], "produto": o["nome"], "preco": f"{o['preco']:.2f}",
            "preco_antes": f"{o['preco_antes']:.2f}" if o.get("preco_antes") else "",
            "cupom": o.get("cupom", ""), "link": o["link"], "imagem": o.get("imagem") or "",
            "postado": ""}


def _aprovar_radar(chat: str, texto: str, historico: dict, existentes: set) -> int:
    pend = historico.get("_radar_pend", {})
    if not pend:
        _responder(chat, "Não tenho sugestões do radar esperando aprovação agora.")
        return 0
    if re.search(r"tod[ao]s|tudo", texto, re.I):
        nums = list(pend.keys())
    else:
        nums = re.findall(r"\d+", texto)
    aceitas, nomes = 0, []
    for n in nums:
        o = pend.pop(n, None)
        if o and _por_na_fila(o, existentes):
            aceitas += 1
            nomes.append(f"#{n} {o['produto'][:40]}")
    historico["_radar_pend"] = pend
    if aceitas:
        _responder(chat, f"✅ Na fila ({aceitas}): " + "; ".join(nomes) +
                   ". Já saem nesta rodada (ou na seguinte, se a fila estiver cheia).")
    else:
        _responder(chat, "⚠️ Não achei esses números entre as sugestões pendentes.")
    return aceitas


def sugerir_radar(historico: dict) -> int:
    """Modo aprovar: manda as achadas do radar pro Telegram de vocês, numeradas."""
    chats = [c.strip() for c in TELEGRAM_MEU_CHAT.split(",") if c.strip()]
    achadas = radar.buscar(set(historico))[:RADAR_SUGESTOES]
    if not achadas or not chats:
        return 0
    pend = historico.get("_radar_pend", {})
    n = int(historico.get("_radar_n", 0))
    for o in achadas:
        historico[o["id"]] = time.time()          # não sugere de novo
        if not o.get("converter"):
            antes = f" (de {brl(o['preco_antes'])})" if o.get("preco_antes") else ""
            legenda = (f"⛏️ Garimpo ({o['loja']})\n{o['nome']}\n{brl(o['preco'])}{antes}\n"
                       f"fonte: {o['fonte']}\n{o['link']}\n\n"
                       f"👉 Gostou? Abra no app, gere o SEU link de afiliado e me mande no formato de sempre.")
            for chat in chats:
                try:
                    telegram(chat, legenda, o.get("imagem"))
                except Exception as e:
                    print(f"[aviso] radar: não consegui mandar o garimpo para {chat}: {e}")
            continue
        n += 1
        pend[str(n)] = _radar_para_fila(o)
        antes = f" (de {brl(o['preco_antes'])})" if o.get("preco_antes") else ""
        cup = f"\n🎟️ Cupom: {o['cupom']}" if o.get("cupom") else ""
        legenda = (f"🔎 Radar #{n}\n{o['nome']}\n{brl(o['preco'])}{antes}{cup}\n"
                   f"fonte: {o['fonte']}\n{o['link']}\n\n👉 Pra postar, responda: ok {n}")
        for chat in chats:
            try:
                telegram(chat, legenda, o.get("imagem"))
            except Exception as e:
                print(f"[aviso] radar: não consegui mandar a sugestão para {chat}: {e}")
    historico["_radar_pend"] = dict(list(pend.items())[-40:])
    historico["_radar_n"] = n
    return len(achadas)


# ----------------------------- Painel do garimpo -------------------------------
def _gid(texto: str) -> str:
    return hashlib.md5(texto.encode()).hexdigest()[:6]


def _ler_garimpo() -> dict:
    try:
        return json.loads(GARIMPO.read_text(encoding="utf-8"))
    except Exception:
        return {"itens": []}


def _usuario_do_bot(historico: dict) -> str:
    if historico.get("_bot_user"):
        return historico["_bot_user"]
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=20).json()
        historico["_bot_user"] = r["result"]["username"]
    except Exception as e:
        print(f"[aviso] não consegui ler o nome do bot: {e}")
    return historico.get("_bot_user", "")


def atualizar_painel(historico: dict) -> int:
    """Junta o que o radar achou em garimpo.json (as últimas 48h, até 250 itens)."""
    dados = _ler_garimpo()
    itens = {i["gid"]: i for i in dados.get("itens", []) if i.get("gid")}
    conhecidos = {i.get("id") for i in itens.values()}
    novos = radar.buscar(conhecidos, nicho="tudo")
    if SHOPEE_APP_ID and SHOPEE_SECRET:
        for palavra in random.sample(PALAVRAS, k=min(3, len(PALAVRAS))):
            try:
                achados = shopee_buscar(palavra)
            except Exception as e:
                print(f"[aviso] Shopee '{palavra}': {e}")
                continue
            for o in achados:
                if o["id"] in conhecidos or o["id"] in historico:
                    continue
                if o.get("nota", 0) and o["nota"] < 4.5:
                    continue
                d = o.get("desconto") or 0
                o.update(converter=True, categoria=radar.categoria(o["nome"]), fonte="Shopee",
                         preco_antes=round(o["preco"] / (1 - d / 100), 2) if 0 < d < 100 else 0)
                novos.append(o)
            print(f"[painel] Shopee '{palavra}': {len(achados)} produto(s)")
    agora = datetime.now(BR)
    adicionados = 0
    fotos_buscadas = 0
    for o in novos:
        gid = _gid(o["id"])
        if gid in itens:
            continue
        if DESCONTO_GARIMPO and (o.get("desconto") or 0) < DESCONTO_GARIMPO:
            continue
        cat = (o.get("categoria") or "Outros").lower()
        if (CATEGORIAS_GARIMPO and cat not in CATEGORIAS_GARIMPO) or cat in CATEGORIAS_FORA:
            continue
        adicionados += 1
        if not o.get("imagem") and fotos_buscadas < 25:
            fotos_buscadas += 1                 # post do canal sem foto: busca a foto do produto
            try:
                o["imagem"], _ = foto_boa(o)
            except Exception:
                pass
        itens[gid] = {
            "gid": gid, "id": o["id"], "loja": o["loja"], "nome": o["nome"],
            "preco": o["preco"], "preco_antes": o.get("preco_antes") or 0,
            "desconto": o.get("desconto") or 0, "cupom": o.get("cupom", ""),
            "imagem": o.get("imagem") or "", "link": o["link"],
            "converter": bool(o.get("converter")), "categoria": o.get("categoria", "Outros"),
            "fonte": o.get("fonte", ""), "post": o.get("post", ""),
            "quando": o.get("quando") or agora.isoformat(timespec="minutes"),
            "achado_em": agora.isoformat(timespec="minutes"),
        }
    limite = agora - timedelta(hours=48)
    def recente(i):
        try:
            return datetime.fromisoformat(i.get("achado_em") or i.get("quando")) >= limite
        except Exception:
            return True
    aprovados = set(historico.get("_aprovados", []))
    lista = [dict(i, aprovado=i["gid"] in aprovados) for i in itens.values() if recente(i)]
    lista.sort(key=lambda i: i.get("achado_em", ""), reverse=True)
    GARIMPO.write_text(json.dumps({
        "atualizado": agora.isoformat(timespec="minutes"),
        "bot": _usuario_do_bot(historico),
        "itens": lista[:250],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return adicionados


def chave_nome(nome: str) -> str:
    return "nome-" + re.sub(r"\W+", "", (nome or "").lower())[:40]


_PALAVRAS_VAZIAS = {"kit", "jogo", "conjunto", "combo", "pack", "pacote", "caixa", "cx", "par", "pares",
                    "novo", "nova", "super", "mega", "mini", "oferta", "promo", "original", "de", "da", "do",
                    "com", "para", "e", "o", "a", "un", "und", "unidades", "unidade"}

def chave_tipo(nome: str) -> str:
    """O tipo do produto (a primeira palavra que diz o que é): 'Fralda Pom Pom XG' e
    'Fralda Huggies P' viram 'tipo-fralda'; 'Kit 2 Travesseiros' vira 'tipo-travesseiro'."""
    for p in re.findall(r"[a-zà-ú]+", (nome or "").lower()):
        if len(p) < 3 or p in _PALAVRAS_VAZIAS:
            continue
        if p.endswith("es") and len(p) > 5 and p[-3] in "rsz":
            p = p[:-2]
        elif p.endswith("s") and len(p) > 4:
            p = p[:-1]
        return "tipo-" + p
    return ""


def ofertas_do_garimpo(historico: dict) -> list[dict]:
    """Modo automático: achados do garimpo que já têm o link de vocês (Amazon do radar e Shopee)."""
    limite = datetime.now(BR) - timedelta(hours=AUTO_HORAS)
    aprovados = set(historico.get("_aprovados", []))
    saida = []
    for i in _ler_garimpo().get("itens", []):
        if not i.get("converter") or i.get("gid") in aprovados or i.get("id") in historico:
            continue
        if historico.get(chave_nome(i.get("nome")), 0) > time.time() - DIAS_SEM_REPETIR * 86400:
            continue   # mesmo produto já foi postado com outro link
        try:
            if datetime.fromisoformat(i.get("achado_em") or i.get("quando")) < limite:
                continue
        except Exception:
            continue
        saida.append({
            "id": i["id"], "gid": i["gid"], "loja": i["loja"], "nome": i["nome"],
            "preco": float(i["preco"]), "preco_antes": float(i.get("preco_antes") or 0),
            "desconto": int(i.get("desconto") or 0), "cupom": i.get("cupom", ""),
            "imagem": i.get("imagem") or None, "link": i["link"],
            "vendas": 0, "nota": 5, "comissao": 0, "radar": True,
            "categoria": i.get("categoria", "Outros"),
        })
    return saida


def marcar_postados_no_garimpo(gids: list[str], historico: dict) -> None:
    """Mostra no painel (como aprovado) o que o modo automático já postou."""
    if not gids:
        return
    aprovados = historico.get("_aprovados", [])
    aprovados += [g for g in gids if g not in aprovados]
    historico["_aprovados"] = aprovados[-500:]
    dados = _ler_garimpo()
    for i in dados.get("itens", []):
        if i.get("gid") in gids:
            i["aprovado"] = True
    if dados.get("itens"):
        GARIMPO.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


_PREFIXOS = {"ml": "https://meli.la/", "sp": "https://s.shopee.com.br/", "az": "https://amzn.to/"}


def _aprovar_do_painel(chat: str, comando: str, historico: dict, existentes: set) -> int:
    m = re.match(r"^(ok|ml|sp|az)_([a-f0-9]{6})(?:_([A-Za-z0-9]+))?$", comando)
    if not m:
        return -1
    tipo, gid, codigo = m.groups()
    item = next((i for i in _ler_garimpo().get("itens", []) if i.get("gid") == gid), None)
    if not item:
        _responder(chat, "⚠️ Não achei esse achado no painel (ele pode ter saído, o painel guarda só 48h).")
        return 0
    if tipo == "ok":
        if not item.get("converter"):
            _responder(chat, "⚠️ Esse achado precisa do SEU link: cole o seu link no painel antes de postar.")
            return 0
        link = item["link"]
    else:
        if not codigo:
            _responder(chat, "⚠️ Faltou o seu link. Cole o link no painel e toque em Postar de novo.")
            return 0
        link = _PREFIXOS[tipo] + codigo
        if tipo == "az":
            try:
                asin = radar.resolver_amazon(link)
                link = radar.link_nosso(asin) if asin else link_amazon(link)
            except Exception:
                pass
    oferta = {"loja": item["loja"], "produto": item["nome"], "preco": f"{float(item['preco']):.2f}",
              "preco_antes": f"{float(item['preco_antes']):.2f}" if item.get("preco_antes") else "",
              "cupom": item.get("cupom", ""), "link": link, "imagem": item.get("imagem", ""), "postado": ""}
    if not _por_na_fila(oferta, existentes):
        _responder(chat, "ℹ️ Esse achado já estava na fila.")
        return 0
    aprovados = historico.get("_aprovados", [])
    if gid not in aprovados:
        aprovados.append(gid)
    historico["_aprovados"] = aprovados[-500:]
    _responder(chat, f"✅ Na fila pelo painel: {item['nome']} por {brl(float(item['preco']))} "
                     f"({item['loja']}). Já vai sair nesta rodada.")
    return 1


def ler_caixa_do_bot(historico: dict) -> int:
    """Lê o que vocês mandaram pro bot e coloca na fila (ofertas.csv)."""
    autorizados = {c.strip() for c in TELEGRAM_MEU_CHAT.split(",") if c.strip()}
    if not TELEGRAM_TOKEN or not autorizados:
        print("[caixa] TELEGRAM_TOKEN ou TELEGRAM_MEU_CHAT vazio: não li as mensagens do robô.")
        return 0
    offset = int(historico.get("_tg_offset", 0))
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                     params={"offset": offset, "timeout": 0,
                             "allowed_updates": json.dumps(["message"])}, timeout=30)
    r.raise_for_status()
    print(f"[caixa] {len(r.json().get('result', []))} mensagem(ns) nova(s) no robô; "
          f"{len(autorizados)} chat(s) autorizado(s).")
    novas = 0
    linhas_existentes = set()
    if OFERTAS_MANUAIS.exists():
        linhas_existentes = {l.get("link", "").strip() for l in csv.DictReader(open(OFERTAS_MANUAIS, encoding="utf-8"))}
    for up in r.json().get("result", []):
        historico["_tg_offset"] = up["update_id"] + 1
        msg = up.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        texto = (msg.get("text") or msg.get("caption") or "").strip()
        if not texto:
            continue
        if chat not in autorizados:
            print(f"[caixa] mensagem ignorada: chat {chat} não está no TELEGRAM_MEU_CHAT")
            if (msg.get("chat") or {}).get("type") == "private":
                _responder(chat, f"⚠️ Ainda não te conheço. Seu ID é {chat}: peça pra incluir "
                                 "esse número no TELEGRAM_MEU_CHAT e mande o link de novo.")
            continue
        print(f"[caixa] mensagem recebida de {chat}")
        comando = re.sub(r"^/start\s+", "", texto.strip())
        if comando != texto.strip() or re.match(r"^(ok|ml|sp|az)_[a-f0-9]{6}", comando):
            r_painel = _aprovar_do_painel(chat, comando, historico, linhas_existentes)
            if r_painel >= 0:
                novas += r_painel
                continue
        if texto.lower() in ("/start", "ajuda", "/ajuda", "help"):
            _responder(chat, AJUDA)
            continue
        if re.match(r"^ok\b", texto, re.I):
            novas += _aprovar_radar(chat, texto, historico, linhas_existentes)
            continue
        if texto.lower() in ("grupos", "/grupos"):
            try:
                _responder(chat, listar_grupos_whatsapp())
            except Exception as e:
                _responder(chat, f"⚠️ Não consegui falar com a Z-API: {e}")
            continue
        oferta, problema = interpretar(texto)
        if oferta and msg.get("photo"):
            oferta["imagem"] = msg["photo"][-1]["file_id"]   # foto mandada pelo usuário
        if not oferta:
            _responder(chat, f"⚠️ Não entendi essa oferta: {problema}.\n\n{AJUDA}")
            continue
        if oferta["link"] in linhas_existentes:
            _responder(chat, "ℹ️ Essa oferta já estava na fila.")
            continue
        novo = not OFERTAS_MANUAIS.exists() or OFERTAS_MANUAIS.stat().st_size == 0
        with open(OFERTAS_MANUAIS, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(oferta.keys()))
            if novo:
                w.writeheader()
            w.writerow(oferta)
        linhas_existentes.add(oferta["link"])
        novas += 1
        aviso = ""
        if oferta["loja"] == "Amazon" and not AMAZON_TAG and "tag=" not in oferta["link"]:
            aviso = "\n⚠️ Falta configurar a tag da Amazon (AMAZON_TAG): esse link pode não render comissão."
        elif oferta["loja"] in ("Shopee", "Mercado Livre"):
            aviso = ("\n⚠️ Confere se esse link é o SEU. Se você copiou de outro grupo, "
                     "troca pelo seu link de afiliado, senão a comissão vai pra quem postou.")
        _responder(chat, f"✅ Na fila: {oferta['produto']} por {brl(float(oferta['preco']))} "
                         f"({oferta['loja']}). Já vai sair nesta rodada.{aviso}")
    return novas


# ----------------------------- Vitrine da página ------------------------------
def registrar_vitrine(o: dict) -> None:
    """Guarda o achado (sem link) para a seção 'Olha o que rolou no grupo'."""
    try:
        itens = json.loads(VITRINE.read_text(encoding="utf-8")) if VITRINE.exists() else []
    except ValueError:
        itens = []
    antes = o.get("preco_antes") or (
        o["preco"] / (1 - o["desconto"] / 100) if 0 < o["desconto"] < 100 else 0)
    nome = o["nome"] if len(o["nome"]) <= 60 else o["nome"][:57].rstrip() + "..."
    itens.insert(0, {
        "nome": nome,
        "loja": o["loja"],
        "preco": round(o["preco"], 2),
        "preco_antes": round(antes, 2) if antes > o["preco"] else 0,
        "quando": datetime.now(BR).isoformat(timespec="minutes"),
    })
    VITRINE.write_text(json.dumps(itens[:12], ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------- Principal -----------------------------------
def main() -> None:
    teste = "--teste" in sys.argv
    historico = carregar_historico()
    if not teste:
        try:
            n = ler_caixa_do_bot(historico)
            if n:
                print(f"{n} oferta(s) nova(s) recebida(s) pelo Telegram.")
        except Exception as e:
            print(f"[aviso] não consegui ler as mensagens do bot: {e}")
        salvar_historico(historico)
        try:
            oferta_do_formulario()
        except Exception as e:
            print(f"[aviso] formulário do painel: {e}")
    ofertas = ler_manuais()

    if RADAR_MODO == "auto":
        try:
            achadas = [o for o in radar.buscar(set(historico)) if o.get("converter")]
            print(f"[radar] {len(achadas)} oferta(s) da Amazon prontas pra postar.")
            ofertas += achadas
        except Exception as e:
            print(f"[aviso] radar falhou: {e}")
    elif RADAR_MODO == "automatico":
        # Atualiza o painel e posta sozinho o que já tem o link de vocês.
        # O que precisa de link manual (Mercado Livre) continua no painel pra aprovar.
        if not teste:
            try:
                n = atualizar_painel(historico)
                print(f"[painel] {n} achado(s) novo(s) no painel do garimpo.")
                salvar_historico(historico)
            except Exception as e:
                print(f"[aviso] painel falhou: {e}")
        auto = ofertas_do_garimpo(historico)
        print(f"[auto] {len(auto)} achado(s) do garimpo prontos pra postar sozinhos.")
        ofertas += auto
    elif RADAR_MODO == "painel" and not teste:
        try:
            n = atualizar_painel(historico)
            print(f"[painel] {n} achado(s) novo(s) no painel do garimpo.")
            salvar_historico(historico)
        except Exception as e:
            print(f"[aviso] painel falhou: {e}")
    elif RADAR_MODO == "aprovar" and not teste:
        try:
            n = sugerir_radar(historico)
            print(f"[radar] {n} sugestão(ões) enviada(s) para aprovação.")
            salvar_historico(historico)
        except Exception as e:
            print(f"[aviso] radar falhou: {e}")

    if SHOPEE_APP_ID and SHOPEE_SECRET and RADAR_MODO == "auto":
        for palavra in random.sample(PALAVRAS, k=min(3, len(PALAVRAS))):
            try:
                ofertas += shopee_buscar(palavra)
            except Exception as e:  # uma palavra falhar nao derruba o resto
                print(f"[aviso] Shopee '{palavra}': {e}")
    if not ofertas:
        print("Nenhuma oferta aprovada na fila (ofertas.csv). Nada a postar.")
        return

    escolhidas = escolher(ofertas, historico)
    if not escolhidas:
        print("Nenhuma oferta passou nos filtros desta vez.")
        return

    for o in escolhidas:
        foto_bytes = None
        if not teste:
            o["imagem"], foto_bytes = foto_boa(o)
        msg = montar_mensagem(o)
        print("=" * 50 + "\n" + msg + "\n")
        if teste:
            continue
        envio = o["imagem"]
        if CARTAO:
            try:
                card = cartao.gerar(o, foto_bytes, PERFIL or "ingrid")
                if card:
                    envio = card
            except Exception as e:
                print(f"[aviso] não consegui montar o cartão: {e}")
        for canal in CANAIS_TELEGRAM:
            try:
                telegram(canal, msg, envio)
            except Exception as e:
                print(f"[aviso] não consegui postar no canal {canal}: {e}")
        postou_zap = 0
        if whatsapp_ligado():
            try:
                postou_zap = whatsapp(msg, envio)
            except Exception as e:
                print(f"[aviso] WhatsApp automático falhou: {e}")
        parcial = (f" (em {postou_zap} de {len(GRUPOS_WHATSAPP)} grupos)"
                   if 0 < postou_zap < len(GRUPOS_WHATSAPP) else "")
        aviso_copia = (f"✅ Já postado no WhatsApp automaticamente{parcial}.\n\n" if postou_zap else
                       ("⚠️ Não consegui postar no WhatsApp sozinho. Copie e cole:\n\n"
                        if whatsapp_ligado() else "📋 Copie e cole no WhatsApp:\n\n"))
        for chat in [c.strip() for c in TELEGRAM_MEU_CHAT.split(",") if c.strip()]:
            try:
                telegram(chat, aviso_copia + msg, None)
            except Exception as e:
                print(f"[aviso] não consegui mandar a cópia para {chat}: {e}")
        if whatsapp_ligado():
            time.sleep(random.randint(20, 45))   # intervalo humano entre posts no WhatsApp
        historico[o["id"]] = time.time()
        historico[chave_nome(o["nome"])] = time.time()
        if chave_tipo(o["nome"]):
            historico[chave_tipo(o["nome"])] = time.time()
        if o.get("gid"):
            marcar_postados_no_garimpo([o["gid"]], historico)
        registrar_vitrine(o)
        salvar_historico(historico)   # salva na hora: se a rodada cair no meio, não repete o que já foi
        if o.get("manual"):
            marcar_manual_postada(o["link"])
        time.sleep(3)

    if not teste:
        salvar_historico(historico)
    print(f"{len(escolhidas)} oferta(s) {'simulada(s)' if teste else 'postada(s)'} "
          f"em {datetime.now(BR):%d/%m %H:%M}.")


if __name__ == "__main__":
    main()
