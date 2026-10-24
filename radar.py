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
    "RADAR_CANAIS", "urubupromo,pobregram,pelandobr,promotop,Postou_Achou,fitcomestilo_ofertas,ofertafitness,grupodeachadinhos,achadinhosdavava,achadinhosdamanoella,modafemininaofertas,laniceachados,glowupestilofeminino,segredodoscosmeticos,belezaempromocao,skincareecapilar,topmaquiagemeflores,acheinashopee_casa,achadoscasaedecor,achadinhosdadri,mundocasaeachados,achadosfitnessbr,modafitnesselook,glowupfitnessbr,achadosshopebrasil,achadosimperdiveisbr,achadinhosdarachel,ofertasfemininas,detectordecupons,guerradealsfit").split(",") if c.strip()]
HORAS_MAX = float(os.getenv("RADAR_HORAS", "12"))      # ignora posts mais velhos que isso
UA = {"User-Agent": "Mozilla/5.0 (compatible; AchadinhosBot/1.0)"}

# ----------------------------- Nicho: roupas fitness ---------------------------
_FITNESS = r"(fitness|academia|treino|esportiv|dry ?fit|compress[aã]o|yoga|pilates|corrida|running|crossfit|muscula[cç][aã]o|workout|\bgym\b|\bsport)"
_ROUPA = r"(cal[cç]a|short|bermuda|\btop\b|cropped|camiseta|camisa|regata|conjunto|macaqu|macac[aã]o|suti[aã]|\bbra\b|meia|jaqueta|corta[- ]?vento|moletom|blusa|body|saia|bodysuit|roupa)"
_SEMPRE = r"(legging|leggin|conjunto fitness|top fitness|short fitness|calça fitness|suti[aã] esportivo|top esportivo)"
_FORA = r"(whey|creatina|suplement|halter|anilha|esteira|bicicleta|smartwatch|rel[oó]gio|fone|garrafa|squeeze|coqueteleira|t[eê]nis|celular|notebook|tv\b|monitor|game|playstation|xbox|tapete|colchonete|el[aá]stico|caneleira|luva)"


_CATEGORIAS = [
    ("Fitness", r"fitness|academia|treino|legging|esportiv|dry ?fit|compress[aã]o|yoga|pilates|corrida|whey|creatina|halter|top esportivo"),
    ("Beleza", r"perfume|maquiag|batom|base l[ií]quida|s[eé]rum|hidratante|shampoo|condicionador|creme|skin ?care|protetor solar|esmalte|secador|chapinha|escova alisadora|cabelo"),
    ("Eletrodomésticos", r"air ?fryer|fritadeira|liquidificador|batedeira|mixer|processador de alimentos|multiprocessador|cafeteira|espresso|nespresso|dolce gusto|chaleira el[eé]trica|sanduicheira|grill|forno el[eé]trico|micro-?ondas|geladeira|refrigerador|freezer|frigobar|fog[aã]o|cooktop|coifa|depurador|lava[- ]?lou[cç]as|m[aá]quina de lavar|lavadora|lava e seca|secadora|tanquinho|aspirador|rob[oô] aspirador|ventilador|climatizador|ar[- ]condicionado|purificador|bebedouro|ferro de passar|vaporizador|panela el[eé]trica|panela de press[aã]o el[eé]trica|panela de arroz|torradeira|pipoqueira|iogurteira|adega"),
    ("Casa", r"cozinha|panela|frigideira|organizador|pote|toalha|len[cç]ol|travesseiro|edredom|cortina|tapete|mop|vassoura|lumin[aá]ria|decora|copo|caneca|garrafa t[eé]rmica|chaleira|forno|limpeza|lava"),
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


PAGINAS = int(os.getenv("RADAR_PAGINAS", "5"))       # cada página = ~20 posts


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
        # botões abaixo do post ("Comprar", "Ver oferta") e links soltos no bloco
        links += [html.unescape(h) for h in re.findall(r'href="(https?://[^"]+)"', bloco)
                  if "t.me/" not in h and "telegram." not in h]
        # links escritos no texto sem virar link clicável
        links += re.findall(r"(?:https?://)?(?:amzn\.to|meli\.la|s\.shopee\.com\.br|shope\.ee)/[A-Za-z0-9]+", _limpa(m_txt.group(1)))
        links = [l if l.startswith("http") else "https://" + l for l in links]
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


UA_NAV = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
          "Accept-Language": "pt-BR,pt;q=0.9"}
_LOJA_NA_PAGINA = re.compile(
    r"https?://(?:amzn\.to|(?:www\.)?amazon\.com\.br|meli\.la|(?:produto\.|www\.)?mercadolivre\.com\.br|"
    r"s\.shopee\.com\.br|shope\.ee|(?:www\.)?shopee\.com\.br)/[^\s\"'<>\\]+", re.I)
_cache_seguir: dict[str, str] = {}


def seguir(url: str, saltos: int = 4) -> str:
    """Descobre a loja por trás de encurtadores e sites de promoção (pelando, promotop...).
    Segue os redirecionamentos e, se cair numa página que não é loja, procura o link da loja dentro dela."""
    if loja_do_link(url):
        return url
    if url in _cache_seguir:
        return _cache_seguir[url]
    final = url
    try:
        r = requests.get(url, headers=UA_NAV, timeout=8, allow_redirects=True)
        final = r.url
        if not loja_do_link(final) and "text/html" in r.headers.get("Content-Type", ""):
            achados = [html.unescape(m) for m in _LOJA_NA_PAGINA.findall(r.text[:600_000])]
            # prefere link curto de afiliado/produto; ignora páginas genéricas da loja
            achados = [a for a in achados if not re.search(r"\.(png|jpe?g|webp|svg|js|css)(\?|$)", a, re.I)]
            produto = [a for a in achados if re.search(r"amzn\.to|meli\.la|s\.shopee|shope\.ee|/dp/|/MLB-?\d|/p/MLB|-i\.\d+\.\d+", a, re.I)]
            if produto or achados:
                final = (produto or achados)[0]
    except Exception:
        pass
    _cache_seguir[url] = final
    return final


def link_nosso(asin: str) -> str:
    return f"https://www.amazon.com.br/dp/{asin}" + (f"?tag={AMAZON_TAG}" if AMAZON_TAG else "")


# ----------------------------- Texto do post -----------------------------------
# Preço depois de "R$". Aceita 59,90 | 59.90 | 3.675,66 | 3675,66 | 3675 (sem ponto de milhar).
# O (?!\d) no fim garante que o número foi lido inteiro (antes, "3675,66" virava 367).
_RS = r"R\s?\$\s*(\d+\.\d{2}(?![\d.,])|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?(?!\d)|\d+(?:[.,]\d{1,2})?(?!\d))"


def _num(t: str) -> float:
    if re.fullmatch(r"\d+\.\d{1,2}", t):          # 59.90 (ponto como decimal)
        return float(t)
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
        # preço sem "R$": "Por 59,90", "Preço: 89,90", "💰 49,90"
        m = re.search(r"(?:\bpor\b|pre[cç]o|valor|apenas|\bs[oó]\b|💰|💵|🔥|➡️|👉)\s*:?\s*"
                      r"(\d{1,3}(?:\.\d{3})+(?:,\d{2})?(?!\d)|\d+,\d{2}(?!\d)|\d{2,5}(?![\d.,]))(?![\d,.]*\s*(?:x\b|%|vezes|parcela))", t, re.I)
        if m:
            return _num(m.group(1)), 0.0
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


_NAO_CUPOM = {"OFF", "FRETE", "GRATIS", "GRÁTIS", "HOJE", "AQUI", "AGORA", "APP", "SITE", "LINK", "LOJA", "PRIME", "DESCONTO"}


def cupom(texto: str) -> str:
    """Código depois de 'cupom' ou 'código', escrito em MAIÚSCULAS no post (ex.: CASA20, BLACK50)."""
    for m in re.finditer(r"(?i:cupom|c[oó]digo|\bc[oó]d\.?)(?i:\s+de\s+desconto)?\s*[:\-–]?\s*([A-Z0-9]{4,20})\b", texto):
        cod = m.group(1)
        if cod.isdigit() or cod in _NAO_CUPOM or not re.search(r"[A-Z]", cod):
            continue
        return cod
    return ""


# ----------------------------- Radar ------------------------------------------
def buscar(ja_vistos: set[str], nicho: str | None = None) -> list[dict]:
    """Roupa fitness achada nos canais.
    Amazon: já volta com o link de vocês (converter=True).
    Mercado Livre/Shopee: volta como garimpo (converter=False), pra vocês gerarem o link no app."""
    agora = datetime.now(timezone.utc)
    achadas, vistos_aqui = [], set()
    total = recentes = fitness = sem_preco = repetidos = 0
    ex_sem_preco, ex_sem_link = [], []
    por_loja: dict[str, int] = {}
    saltos_restantes = 400                      # limite de links desconhecidos seguidos por rodada
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
                if len(ex_sem_link) < 5 and p["links"]:
                    l0 = next((l for l in p["links"] if not any(x in l.lower() for x in _IGNORAR)), p["links"][0])
                    ex_sem_link.append(f"@{canal}: {l0[:70]} => {_cache_seguir.get(l0, '(não seguido)')[:90]}")
                continue
            preco, antes = precos(p["texto"])
            if not preco:
                sem_preco += 1
                if len(ex_sem_preco) < 3:
                    ex_sem_preco.append(f"@{canal}: " + p["texto"].replace("\n", " / ")[:140])
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
                if not asin:
                    continue
                if f"radar-{asin}" in ja_vistos or f"radar-{asin}" in vistos_aqui:
                    repetidos += 1
                    continue
                base.update(id=f"radar-{asin}", link=link_nosso(asin), converter=True)
            else:
                chave = "garimpo-" + re.sub(r"\W+", "", base["nome"].lower())[:40]
                if chave in ja_vistos or chave in vistos_aqui:
                    repetidos += 1
                    continue
                base.update(id=chave, link=alvo, converter=False)
            vistos_aqui.add(base["id"])
            achadas.append(base)
        time.sleep(1)
    print(f"[radar] resumo: {total} posts, {recentes} recentes (<{HORAS_MAX:g}h), "
          f"{fitness} no nicho ({nicho or 'fitness'}); por loja: {por_loja}; sem preço: {sem_preco}; "
          f"repetidos/já no painel: {repetidos}; aproveitáveis: {len(achadas)}")
    for e in ex_sem_preco:
        print(f"[radar]   exemplo sem preço -> {e}")
    for e in ex_sem_link:
        print(f"[radar]   exemplo sem link de loja -> {e}")
    achadas.sort(key=lambda o: not o["converter"])   # Amazon primeiro
    return achadas
