"""Radar de ofertas: lê canais PÚBLICOS de promoção no Telegram (página t.me/s/...),
fica só com produtos da Amazon do nicho escolhido e troca o link pelo de vocês.

Não precisa seguir os canais: a página pública t.me/s/<canal> é lida direto.
"""
from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime, timezone

import requests

AMAZON_TAG = os.getenv("AMAZON_TAG", "").strip()
CANAIS = [c.strip().lstrip("@") for c in os.getenv(
    "RADAR_CANAIS", "urubupromo,pobregram,pelandobr,promotop,Postou_Achou,fitcomestilo_ofertas,ofertafitness,grupodeachadinhos,achadinhosdavava,achadinhosdamanoella,modafemininaofertas,laniceachados,glowupestilofeminino,segredodoscosmeticos,belezaempromocao,skincareecapilar,topmaquiagemeflores,acheinashopee_casa,achadoscasaedecor,achadinhosdadri,mundocasaeachados,achadosfitnessbr,modafitnesselook,glowupfitnessbr,achadosshopebrasil,achadosimperdiveisbr,achadinhosdarachel,ofertasfemininas,detectordecupons").split(",") if c.strip()]
HORAS_MAX = float(os.getenv("RADAR_HORAS", "8"))      # ignora posts mais velhos que isso
UA = {"User-Agent": "Mozilla/5.0 (compatible; AchadinhosBot/1.0)"}

# ----------------------------- Nicho: roupas fitness ---------------------------
_FITNESS = r"(fitness|academia|treino|esportiv|dry ?fit|compress[aã]o|yoga|pilates|corrida|running|crossfit|muscula[cç][aã]o|workout|\bgym\b|\bsport)"
_ROUPA = r"(cal[cç]a|short|bermuda|\btop\b|cropped|camiseta|camisa|regata|conjunto|macaqu|macac[aã]o|suti[aã]|\bbra\b|meia|jaqueta|corta[- ]?vento|moletom|blusa|body|saia|bodysuit|roupa)"
_SEMPRE = r"(legging|leggin|conjunto fitness|top fitness|short fitness|calça fitness|suti[aã] esportivo|top esportivo)"
_FORA = r"(whey|creatina|suplement|halter|anilha|esteira|bicicleta|smartwatch|rel[oó]gio|fone|garrafa|squeeze|coqueteleira|t[eê]nis|celular|notebook|tv\b|monitor|game|playstation|xbox|tapete|colchonete|el[aá]stico|caneleira|luva)"


_CATEGORIAS = [
    ("Fitness", r"fitness|academia|treino|legging|esportiv|dry ?fit|compress[aã]o|yoga|pilates|corrida|whey|creatina|halter|top esportivo"),
    ("Beleza", r"perfume|maquiag|batom|base l[ií]quida|s[eé]rum|hidratante|shampoo|condicionador|creme|skin ?care|protetor solar|esmalte|secador|chapinha|escova alisadora|cabelo"),
    ("Casa", r"cozinha|panela|frigideira|airfryer|air fryer|fritadeira|liquidificador|organizador|pote|toalha|len[cç]ol|travesseiro|edredom|cortina|tapete|aspirador|mop|vassoura|lumin[aá]ria|decora|copo|caneca|garrafa t[eé]rmica|cafeteira|chaleira|forno|micro-?ondas|geladeira|fog[aã]o|ventilador|limpeza|lava"),
    ("Moda", r"camiseta|camisa|vestido|cal[cç]a|bermuda|short|saia|blusa|jaqueta|moletom|t[eê]nis|sand[aá]lia|chinelo|bolsa|mochila|carteira|rel[oó]gio|[oó]culos|cueca|calcinha|suti[aã]|meia|bon[eé]"),
    ("Eletrônicos", r"fone|celular|smartphone|iphone|galaxy|xiaomi|notebook|tablet|\btv\b|smart ?tv|monitor|carregador|power ?bank|caixa de som|alexa|echo|kindle|ssd|mouse|teclado|headset|smartwatch|playstation|xbox|nintendo|game"),
]


def categoria(texto: str) -> str:
    t = texto.lower()
    for nome, padrao in _CATEGORIAS:
        if re.search(padrao, t):
            return nome
    return "Outros"


def e_roupa_fitness(texto: str) -> bool:
    t = texto.lower()
    if re.search(_FORA, t):
        return False
    if re.search(_SEMPRE, t):
        return True
    return bool(re.search(_FITNESS, t) and re.search(_ROUPA, t))


# ----------------------------- Leitura do canal -------------------------------
_POST = re.compile(r'<div class="tgme_widget_message_wrap.*?(?=<div class="tgme_widget_message_wrap|\Z)', re.S)


def _limpa(trecho: str) -> str:
    trecho = re.sub(r"<br\s*/?>", "\n", trecho)
    trecho = re.sub(r"<[^>]+>", "", trecho)
    return html.unescape(trecho).strip()


PAGINAS = int(os.getenv("RADAR_PAGINAS", "3"))       # cada página = ~20 posts


def ler_canal(canal: str) -> list[dict]:
    posts, antes = [], None
    for _ in range(PAGINAS):
        url = f"https://t.me/s/{canal}" + (f"?before={antes}" if antes else "")
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        novos = _ler_pagina(r.text)
        if not novos:
            break
        posts += novos
        ids = [int(p["id"].split("/")[-1]) for p in novos if p["id"].split("/")[-1].isdigit()]
        if not ids:
            break
        antes = min(ids)
        time.sleep(0.5)
    return posts


def _ler_pagina(texto_html: str) -> list[dict]:
    posts = []
    for bloco in _POST.findall(texto_html):
        m_id = re.search(r'data-post="([^"]+)"', bloco)
        m_txt = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', bloco, re.S)
        if not (m_id and m_txt):
            continue
        links = [html.unescape(h) for h in re.findall(r'href="(https?://[^"]+)"', m_txt.group(1))]
        links += [html.unescape(h) for h in re.findall(r'href="(https?://[^"]+)"', bloco)
                  if "amzn" in h or "amazon." in h]
        foto = re.search(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", bloco)
        quando = re.search(r'<time datetime="([^"]+)"', bloco)
        posts.append({
            "id": m_id.group(1),
            "texto": _limpa(m_txt.group(1)),
            "links": list(dict.fromkeys(links)),
            "foto": foto.group(1) if foto else None,
            "quando": quando.group(1) if quando else "",
        })
    return posts


# ----------------------------- Amazon -----------------------------------------
def asin_de(url: str) -> str | None:
    m = re.search(r"/(?:dp|gp/product|gp/aw/d|d)/([A-Z0-9]{10})(?:[/?#]|$)", url)
    return m.group(1) if m else None


def resolver_amazon(url: str) -> str | None:
    """Segue o link curto (amzn.to) só até descobrir o código do produto (ASIN)."""
    for _ in range(5):
        asin = asin_de(url)
        if asin:
            return asin
        if "amzn." not in url and "amazon." not in url:
            return None
        if "amazon." in url and "amzn." not in url:
            return None                       # página da Amazon sem produto (busca, cupom...)
        r = requests.head(url, headers=UA, allow_redirects=False, timeout=20)
        prox = r.headers.get("Location")
        if not prox:
            return None
        url = prox
    return asin_de(url)


def loja_do_link(url: str) -> str:
    u = url.lower()
    if "amzn." in u or "amazon.com" in u:
        return "Amazon"
    if "meli.la" in u or "mercadoli" in u or "mercadolibre" in u:
        return "Mercado Livre"
    if "shopee" in u or "shp.ee" in u:
        return "Shopee"
    return ""


_IGNORAR = ("t.me/", "telegram.", "instagram.", "whatsapp.", "chat.whatsapp", "youtube.", "tiktok.")


def seguir(url: str, saltos: int = 4) -> str:
    """Segue encurtadores (bit.ly, links do próprio canal...) até cair numa loja."""
    for _ in range(saltos):
        if loja_do_link(url):
            return url
        try:
            r = requests.head(url, headers=UA, allow_redirects=False, timeout=15)
        except Exception:
            return url
        prox = r.headers.get("Location")
        if not prox:
            return url
        url = requests.compat.urljoin(url, prox)
    return url


def link_nosso(asin: str) -> str:
    return f"https://www.amazon.com.br/dp/{asin}" + (f"?tag={AMAZON_TAG}" if AMAZON_TAG else "")


# ----------------------------- Texto do post -----------------------------------
_RS = r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)"


def _num(t: str) -> float:
    return float(t.replace(".", "").replace(",", "."))


def precos(texto: str) -> tuple[float, float]:
    """(preço atual, preço antigo ou 0). Ignora valores de parcela (10x de R$ 9,90)."""
    t = re.sub(r"\d+\s*x\s*(de\s*)?R\$\s*[\d.,]+", " ", texto, flags=re.I)
    de_por = re.search(r"de\s*:?\s*" + _RS + r".{0,40}?por\s*:?\s*" + _RS, t, re.I | re.S)
    if de_por:
        antes, agora = _num(de_por.group(1)), _num(de_por.group(2))
        return (agora, antes) if antes > agora else (agora, 0.0)
    valores = [_num(v) for v in re.findall(_RS, t)]
    valores = [v for v in valores if v > 0]
    if not valores:
        return 0.0, 0.0
    if len(valores) >= 2 and max(valores) > min(valores) * 1.05:
        return min(valores), max(valores)
    return valores[0], 0.0


def nome_produto(texto: str) -> str:
    candidatas = []
    for linha in texto.splitlines():
        l = linha.strip(" -•*🔥⚡️✅❌👉➡️💥🛒")
        if not l or "R$" in l or "http" in l or re.search(r"\bcupom\b|\blink\b|\bcompre\b|\bfrete\b", l, re.I):
            continue
        letras = sum(c.isalpha() for c in l)
        if letras < 8:
            continue
        maiusc = sum(c.isupper() for c in l) / max(letras, 1)
        candidatas.append((maiusc > 0.7, -letras, l))   # prefere linha normal e longa
    if not candidatas:
        return ""
    candidatas.sort()
    return re.sub(r"\s+", " ", candidatas[0][2])[:90]


def cupom(texto: str) -> str:
    m = re.search(r"cupom\s*:?\s*([A-Z0-9]{4,20})\b", texto, re.I)
    return m.group(1).upper() if m and not m.group(1).isdigit() else ""


# ----------------------------- Radar ------------------------------------------
def buscar(ja_vistos: set[str], nicho: str | None = None) -> list[dict]:
    """Roupa fitness achada nos canais.
    Amazon: já volta com o link de vocês (converter=True).
    Mercado Livre/Shopee: volta como garimpo (converter=False), pra vocês gerarem o link no app."""
    agora = datetime.now(timezone.utc)
    achadas, vistos_aqui = [], set()
    total = recentes = fitness = 0
    por_loja: dict[str, int] = {}
    saltos_restantes = 40                       # limite de links desconhecidos seguidos por rodada
    amazon_restantes = 80                       # limite de links da Amazon resolvidos por rodada
    for canal in CANAIS:
        try:
            posts = ler_canal(canal)
        except Exception as e:
            print(f"[radar] não consegui ler @{canal}: {e}")
            continue
        print(f"[radar] @{canal}: {len(posts)} posts lidos")
        for p in posts:
            total += 1
            try:
                if p["quando"]:
                    idade = (agora - datetime.fromisoformat(p["quando"])).total_seconds() / 3600
                    if idade > HORAS_MAX:
                        continue
            except ValueError:
                pass
            recentes += 1
            nicho = nicho or os.getenv("RADAR_NICHO", "fitness")
            if nicho == "fitness" and not e_roupa_fitness(p["texto"]):
                continue
            fitness += 1
            alvo, loja = None, ""
            for l in p["links"]:
                if any(x in l.lower() for x in _IGNORAR):
                    continue
                loja = loja_do_link(l)
                if not loja and saltos_restantes > 0:
                    saltos_restantes -= 1
                    l = seguir(l)
                    loja = loja_do_link(l)
                if loja:
                    alvo = l
                    break
            por_loja[loja or "outra/sem link"] = por_loja.get(loja or "outra/sem link", 0) + 1
            if not alvo:
                continue
            preco, antes = precos(p["texto"])
            if not preco:
                continue
            base = {
                "loja": loja,
                "nome": nome_produto(p["texto"]) or "Achadinho fitness",
                "preco": preco,
                "preco_antes": antes,
                "desconto": round((1 - preco / antes) * 100) if antes > preco else 0,
                "comissao": 0, "vendas": 0, "nota": 5,
                "imagem": p["foto"],
                "cupom": cupom(p["texto"]),
                "radar": True,
                "fonte": f"@{canal}",
                "categoria": "Fitness" if e_roupa_fitness(p["texto"]) else categoria(p["texto"]),
                "quando": p["quando"],
                "link_original": alvo,
                "post": f"https://t.me/{p['id']}",
            }
            if loja == "Amazon":
                if amazon_restantes <= 0:
                    continue
                amazon_restantes -= 1
                try:
                    asin = resolver_amazon(alvo)
                except Exception as e:
                    print(f"[radar] link da Amazon não resolveu ({alvo}): {e}")
                    continue
                if not asin or f"radar-{asin}" in ja_vistos or f"radar-{asin}" in vistos_aqui:
                    continue
                base.update(id=f"radar-{asin}", link=link_nosso(asin), converter=True)
            else:
                chave = "garimpo-" + re.sub(r"\W+", "", base["nome"].lower())[:40]
                if chave in ja_vistos or chave in vistos_aqui:
                    continue
                base.update(id=chave, link=alvo, converter=False)
            vistos_aqui.add(base["id"])
            achadas.append(base)
        time.sleep(1)
    print(f"[radar] resumo: {total} posts, {recentes} recentes (<{HORAS_MAX:g}h), "
          f"{fitness} no nicho ({nicho or 'fitness'}); por loja: {por_loja}; aproveitáveis: {len(achadas)}")
    achadas.sort(key=lambda o: not o["converter"])   # Amazon primeiro
    return achadas
