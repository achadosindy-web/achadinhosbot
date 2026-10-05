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

PASTA = Path(__file__).parent
HISTORICO = PASTA / "historico.json"
OFERTAS_MANUAIS = PASTA / "ofertas.csv"
VITRINE = PASTA / "vitrine.json"   # últimos achados, mostrados na página do grupo

# ---------- Configuracao (vem das variaveis de ambiente / segredos) ----------
SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "").strip()
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CANAL = os.getenv("TELEGRAM_CANAL", "").strip()          # ex: @achadinhosdaingrid
TELEGRAM_MEU_CHAT = os.getenv("TELEGRAM_MEU_CHAT", "").strip()    # seu chat privado (copia p/ WhatsApp)
NOME_GRUPO = os.getenv("NOME_GRUPO", "Achadinhos")
AMAZON_TAG = os.getenv("AMAZON_TAG", "").strip()          # ex: achadosindy-20

# Quantas ofertas por execucao e palavras-chave para garimpar
OFERTAS_POR_RODADA = int(os.getenv("OFERTAS_POR_RODADA", "3"))
PALAVRAS = [p.strip() for p in os.getenv(
    "PALAVRAS",
    "fone bluetooth,air fryer,organizador,skincare,tenis feminino,"
    "garrafa termica,luminaria,utensilios cozinha,brinquedo,carregador"
).split(",") if p.strip()]

# Filtros de qualidade: so posta o que vale a pena
DESCONTO_MINIMO = int(os.getenv("DESCONTO_MINIMO", "20"))   # % de desconto
NOTA_MINIMA = float(os.getenv("NOTA_MINIMA", "4.5"))        # estrelas
VENDAS_MINIMAS = int(os.getenv("VENDAS_MINIMAS", "100"))
DIAS_SEM_REPETIR = 7

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
    """Nota da oferta: desconto pesa mais, depois prova social e comissao."""
    return (o["desconto"] * 2
            + min(o["vendas"], 5000) / 100
            + o["comissao"] * 100
            + (o["nota"] - 4) * 10)


def escolher(ofertas: list[dict], historico: dict) -> list[dict]:
    corte = time.time() - DIAS_SEM_REPETIR * 86400
    boas = []
    for o in ofertas:
        if historico.get(o["id"], 0) > corte:
            continue
        if not o.get("manual") and (o["desconto"] < DESCONTO_MINIMO
                                    or o["nota"] < NOTA_MINIMA
                                    or o["vendas"] < VENDAS_MINIMAS):
            continue
        boas.append(o)
    # manuais primeiro (voce escolheu a dedo), depois as melhores da Shopee
    boas.sort(key=lambda o: (not o.get("manual"), -pontuar(o)))
    vistos, final = set(), []
    for o in boas:
        if o["id"] in vistos:
            continue
        vistos.add(o["id"])
        final.append(o)
        if len(final) >= OFERTAS_POR_RODADA:
            break
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
    "A oferta sai na próxima rodada."
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
        return None, "faltou o preço (ex: 49,90 / 89,90)"
    loja = _loja(url)
    nome = nome or _nome_da_url(url) or "Achadinho do dia"
    if loja == "Amazon":
        url = link_amazon(url)
    preco, antes = min(precos), (max(precos) if len(precos) > 1 else 0)
    return {"loja": loja, "produto": nome, "preco": f"{preco:.2f}",
            "preco_antes": f"{antes:.2f}" if antes > preco else "",
            "cupom": cupom, "link": url, "imagem": "", "postado": ""}, ""


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


def ler_caixa_do_bot(historico: dict) -> int:
    """Lê o que vocês mandaram pro bot e coloca na fila (ofertas.csv)."""
    autorizados = {c.strip() for c in TELEGRAM_MEU_CHAT.split(",") if c.strip()}
    if not TELEGRAM_TOKEN or not autorizados:
        return 0
    offset = int(historico.get("_tg_offset", 0))
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                     params={"offset": offset, "timeout": 0,
                             "allowed_updates": json.dumps(["message"])}, timeout=30)
    r.raise_for_status()
    novas = 0
    linhas_existentes = set()
    if OFERTAS_MANUAIS.exists():
        linhas_existentes = {l.get("link", "").strip() for l in csv.DictReader(open(OFERTAS_MANUAIS, encoding="utf-8"))}
    for up in r.json().get("result", []):
        historico["_tg_offset"] = up["update_id"] + 1
        msg = up.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        texto = (msg.get("text") or msg.get("caption") or "").strip()
        if chat not in autorizados or not texto:
            continue
        if texto.lower() in ("/start", "ajuda", "/ajuda", "help"):
            _responder(chat, AJUDA)
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
        _responder(chat, f"✅ Na fila: {oferta['produto']} por {brl(float(oferta['preco']))} "
                         f"({oferta['loja']}). Sai na próxima rodada ({_proximo_horario()}).{aviso}")
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
    ofertas = ler_manuais()

    if SHOPEE_APP_ID and SHOPEE_SECRET:
        for palavra in random.sample(PALAVRAS, k=min(3, len(PALAVRAS))):
            try:
                ofertas += shopee_buscar(palavra)
            except Exception as e:  # uma palavra falhar nao derruba o resto
                print(f"[aviso] Shopee '{palavra}': {e}")
    elif not ofertas:
        print("Sem credenciais da Shopee e sem ofertas no ofertas.csv. Nada a postar.")
        return

    escolhidas = escolher(ofertas, historico)
    if not escolhidas:
        print("Nenhuma oferta passou nos filtros desta vez.")
        return

    for o in escolhidas:
        msg = montar_mensagem(o)
        print("=" * 50 + "\n" + msg + "\n")
        if teste:
            continue
        if TELEGRAM_CANAL:
            telegram(TELEGRAM_CANAL, msg, o["imagem"])
        for chat in [c.strip() for c in TELEGRAM_MEU_CHAT.split(",") if c.strip()]:
            try:
                telegram(chat, "📋 Copie e cole no WhatsApp:\n\n" + msg, None)
            except Exception as e:
                print(f"[aviso] não consegui mandar a cópia para {chat}: {e}")
        historico[o["id"]] = time.time()
        registrar_vitrine(o)
        if o.get("manual"):
            marcar_manual_postada(o["link"])
        time.sleep(3)

    if not teste:
        salvar_historico(historico)
    print(f"{len(escolhidas)} oferta(s) {'simulada(s)' if teste else 'postada(s)'} "
          f"em {datetime.now(BR):%d/%m %H:%M}.")


if __name__ == "__main__":
    main()
