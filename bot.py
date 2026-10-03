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

# ---------- Configuracao (vem das variaveis de ambiente / segredos) ----------
SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CANAL = os.getenv("TELEGRAM_CANAL", "")          # ex: @achadinhosdaingrid
TELEGRAM_MEU_CHAT = os.getenv("TELEGRAM_MEU_CHAT", "")    # seu chat privado (copia p/ WhatsApp)
NOME_GRUPO = os.getenv("NOME_GRUPO", "Achadinhos")

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
    h = {k: v for k, v in h.items() if v > limite}
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
    linhas += ["", f"🛍️ Comprar na {o['loja']}:", o["link"], "",
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
    r = requests.post(f"{base}/sendMessage", timeout=30,
                      data={"chat_id": chat, "text": texto})
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


# ------------------------------- Principal -----------------------------------
def main() -> None:
    teste = "--teste" in sys.argv
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

    historico = carregar_historico()
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
        if TELEGRAM_MEU_CHAT:
            telegram(TELEGRAM_MEU_CHAT, "📋 Copie e cole no WhatsApp:\n\n" + msg, None)
        historico[o["id"]] = time.time()
        if o.get("manual"):
            marcar_manual_postada(o["link"])
        time.sleep(3)

    if not teste:
        salvar_historico(historico)
    print(f"{len(escolhidas)} oferta(s) {'simulada(s)' if teste else 'postada(s)'} "
          f"em {datetime.now(BR):%d/%m %H:%M}.")


if __name__ == "__main__":
    main()
