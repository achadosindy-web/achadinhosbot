"""
Catálogo Viral da Indy
----------------------
Monta o catalogo.json que a página catalogo.html (repositório "grupo") mostra.

  - Shopee: puxa da API oficial de afiliados os MAIS VENDIDOS de cada categoria,
    já com o link de afiliado de vocês (offerLink), a % de comissão e quanto isso dá em reais.
  - Amazon: entra o que o radar já achou nos canais (garimpo.json dos perfis),
    com o link de vocês (tag). A API da Amazon só libera com 10 vendas em 30 dias.
  - Mercado Livre continua no painel do garimpo (o link de vocês é gerado no app).

Não mexe no bot.py, no postar.yml nem no garimpo. Só escreve catalogo.json.

Rodar:  python catalogo.py           (de verdade, precisa SHOPEE_APP_ID e SHOPEE_SECRET)
        python catalogo.py --teste   (sem internet: gera um catálogo de exemplo pra testar a página)
"""

import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PASTA = Path(__file__).parent
SAIDA = PASTA / "catalogo.json"
BR = timezone(timedelta(hours=-3))
SHOPEE_URL = "https://open-api.affiliate.shopee.com.br/graphql"

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "").strip()
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "").strip()

# Filtros (dá pra trocar no catalogo.yml sem mexer aqui)
VENDAS_MIN = int(os.getenv("CATALOGO_VENDAS_MIN", "1000"))       # só produto que já vendeu isso (o catalogo.yml manda)
NOTA_MIN = float(os.getenv("CATALOGO_NOTA_MIN", "4.5"))          # estrelas
POR_CATEGORIA = int(os.getenv("CATALOGO_POR_CATEGORIA", "40"))   # quantos ficam em cada categoria
POR_BUSCA = int(os.getenv("CATALOGO_POR_BUSCA", "50"))           # quantos a Shopee devolve por palavra
HORAS_AMAZON = float(os.getenv("CATALOGO_HORAS_AMAZON", "48"))   # achados da Amazon de até tantas horas

# Comissão da Amazon por categoria (em %). Fica vazio até vocês preencherem com a tabela
# oficial do Associados (associados.amazon.com.br). Ex.: "Moda:10,Casa:8,Beleza:10"
COMISSAO_AMAZON = {}
for par in os.getenv("CATALOGO_COMISSAO_AMAZON", "").split(","):
    if ":" in par:
        cat, pct = par.split(":", 1)
        try:
            COMISSAO_AMAZON[cat.strip().lower()] = float(pct.replace(",", ".")) / 100
        except ValueError:
            pass

# O que o catálogo busca na Shopee, por categoria (mesmos nomes do painel)
PALAVRAS = {
    "Fitness": ["legging fitness", "calça legging fitness", "legging empina bumbum", "conjunto fitness feminino",
                "conjunto academia feminino", "top academia", "top fitness", "short academia feminino",
                "short fitness", "macaquinho fitness", "macacão fitness", "bermuda ciclista", "regata academia feminina",
                "cropped academia", "tênis academia feminino", "meia de compressão", "garrafa squeeze academia",
                "coqueteleira", "mini band elástico", "tapete yoga"],
    "Moda": ["vestido feminino", "vestido midi", "calça pantacourt", "calça wide leg", "cropped feminino",
             "blusa feminina", "saia midi", "conjunto feminino", "body feminino", "pijama feminino",
             "bolsa feminina", "bolsa transversal", "sandália feminina", "tênis feminino", "brinco feminino",
             "óculos de sol feminino"],
    "Beleza": ["skincare", "sérum facial", "protetor solar facial", "hidratante corporal", "maquiagem",
               "base líquida", "paleta de sombra", "batom matte", "máscara de cílios", "kit pincel maquiagem",
               "escova secadora", "chapinha", "perfume feminino", "body splash"],
    "Casa": ["organizador casa", "organizador de gaveta", "utensílios cozinha", "potes herméticos",
             "garrafa térmica", "copo térmico", "jogo de cama", "toalha de banho", "tapete sala",
             "luminária led", "difusor de aromas", "porta temperos", "cabide veludo", "caixa organizadora",
             "mop giratório"],
    "Bebê": ["roupa bebê", "body bebê", "brinquedo infantil", "brinquedo educativo", "kit higiene bebê",
             "mochila maternidade"],
    "Eletrônicos": ["fone bluetooth", "smartwatch", "carregador turbo", "caixa de som bluetooth",
                    "power bank", "suporte celular", "ring light", "mouse sem fio"],
    "Eletrodomésticos": ["air fryer", "mini processador", "aspirador portátil", "mixer de mão",
                         "sanduicheira", "chaleira elétrica", "ventilador portátil"],
}
# Trocar a lista sem mexer no código: "Fitness:legging|top academia;Casa:organizador|pote"
if os.getenv("CATALOGO_PALAVRAS", "").strip():
    PALAVRAS = {}
    for bloco in os.getenv("CATALOGO_PALAVRAS").split(";"):
        if ":" in bloco:
            cat, lista = bloco.split(":", 1)
            PALAVRAS[cat.strip()] = [p.strip() for p in lista.split("|") if p.strip()]


# --------------------------- Categoria pelo nome -----------------------------
# 18/11: a Shopee devolve de tudo numa busca ("calça legging" trazia calça jeans e pantalona).
# Por isso a categoria final vem do NOME do produto; a da palavra buscada só vale se o nome não disser nada.
import re
_REGRAS = [
    ("Fitness", r"fitness|academia|treino|legging|leggin|esportiv|dry ?fit|yoga|pilates|corrida|crossfit|muscula|"
                r"compress[aã]o|nadador|ciclista|squeeze|coqueteleira|halter|caneleira|mini ?band|\bgym\b|ciclismo"),
    ("Bebê", r"beb[eê]|infantil|crian[cç]a|menin[oa]|maternidade|fralda|brinquedo|chupeta|mamadeira"),
    ("Beleza", r"perfume|body splash|maquiag|batom|\bbase\b|s[ée]rum|skin ?care|hidratante|protetor solar|shampoo|"
               r"condicionador|cabelo|capilar|escova secadora|chapinha|prancha|c[ií]lios|sombra|pincel|esmalte|\bcreme\b|facial"),
    ("Eletrodomésticos", r"air ?fryer|fritadeira|liquidificador|mixer|processador|sanduicheira|chaleira|ventilador|"
                         r"aspirador|cafeteira|panela el[eé]trica|batedeira|ferro de passar|forno el[eé]trico"),
    ("Eletrônicos", r"\bfone|headphone|earbud|smartwatch|smart watch|rel[oó]gio inteligente|carregador|caixa de som|"
                    r"power ?bank|cabo usb|ring light|mouse|teclado|suporte (de )?celular|headset|bluetooth"),
    ("Casa", r"organizador|\bpotes?\b|garrafa|\bcopo|toalha|len[cç]ol|jogo de cama|cobre ?leito|edredom|tapete|cortina|"
             r"lumin[aá]ria|difusor|tempero|cabide|caixa organizadora|\bmop\b|cozinha|utens[ií]lio|panela|travesseiro"),
    ("Moda", r"cal[cç]a|vestido|blusa|cropped|saia|\bbody\b|pijama|bolsa|sand[aá]lia|t[eê]nis|brinco|colar|[oó]culos|"
             r"carteira|suti[aã]|short|regata|conjunto|jaqueta|rel[oó]gio|chinelo|camiseta|camisa|macac"),
]


def categoria_pelo_nome(nome: str, padrao: str) -> str:
    for cat, regra in _REGRAS:
        if re.search(regra, nome or "", re.I):
            return cat
    return padrao


# ------------------------------- Shopee --------------------------------------
def shopee_mais_vendidos(palavra: str) -> list[dict]:
    """productOfferV2 ordenado por vendas (sortType 2), igual ao que o robô já usa.
    18/11: a Shopee às vezes responde "error 10000 System Error" quando recebe muitas buscas
    seguidas. Agora o robô espera e tenta de novo (até 3 vezes), pedindo menos produtos."""
    ultimo = None
    for tentativa, (espera, limite) in enumerate([(0, POR_BUSCA), (4, 30), (10, 20)]):
        if espera:
            time.sleep(espera)
        try:
            return _shopee_consulta(palavra, limite)
        except RuntimeError as e:
            ultimo = e
            if "10000" not in str(e):
                raise
    raise ultimo


def _shopee_consulta(palavra: str, limite: int) -> list[dict]:
    import requests
    query = """
    query($keyword: String, $limit: Int, $sortType: Int) {
      productOfferV2(keyword: $keyword, limit: $limit, sortType: $sortType) {
        nodes {
          itemId productName priceMin priceMax priceDiscountRate
          commissionRate sales ratingStar imageUrl offerLink shopName
        }
      }
    }"""
    corpo = json.dumps({"query": query,
                        "variables": {"keyword": palavra, "limit": limite, "sortType": 2}},
                       separators=(",", ":"))
    ts = str(int(time.time()))
    assinatura = hashlib.sha256((SHOPEE_APP_ID + ts + corpo + SHOPEE_SECRET).encode()).hexdigest()
    cab = {"Content-Type": "application/json",
           "Authorization": f"SHA256 Credential={SHOPEE_APP_ID}, Timestamp={ts}, Signature={assinatura}"}
    r = requests.post(SHOPEE_URL, data=corpo, headers=cab, timeout=30)
    r.raise_for_status()
    dados = r.json()
    if dados.get("errors"):
        raise RuntimeError(f"Shopee respondeu erro: {dados['errors']}")
    return dados["data"]["productOfferV2"]["nodes"] or []


def item_shopee(n: dict, cat: str) -> dict | None:
    try:
        preco = float(n["priceMin"])
        preco_max = float(n.get("priceMax") or preco)
        taxa = float(n.get("commissionRate") or 0)
        desconto = int(n.get("priceDiscountRate") or 0)
        link = (n.get("offerLink") or "").strip()
    except (KeyError, TypeError, ValueError):
        return None
    if not link or preco <= 0:
        return None
    return {
        "id": f"shopee-{n['itemId']}",
        "loja": "Shopee",
        "nome": " ".join(str(n["productName"]).split())[:120],
        "preco": round(preco, 2),
        "preco_max": round(preco_max, 2) if preco_max > preco * 1.01 else 0,
        "preco_antes": round(preco / (1 - desconto / 100), 2) if 0 < desconto < 90 else 0,
        "desconto": desconto if 0 < desconto < 90 else 0,
        "comissao_pct": round(taxa * 100, 1),
        "comissao_rs": round(preco * taxa, 2),
        "vendas": int(n.get("sales") or 0),
        "nota": round(float(n.get("ratingStar") or 0), 1),
        "imagem": n.get("imageUrl") or "",
        "link": link,
        "categoria": categoria_pelo_nome(str(n["productName"]), cat),
        "vendedor": n.get("shopName") or "",
    }


def buscar_shopee() -> list[dict]:
    if not (SHOPEE_APP_ID and SHOPEE_SECRET):
        print("[catalogo] sem SHOPEE_APP_ID/SHOPEE_SECRET: pulei a Shopee.")
        return []
    por_cat: dict[str, dict] = {}
    for cat, palavras in PALAVRAS.items():
        achados = por_cat.setdefault(cat, {})
        for palavra in palavras:
            try:
                nos = shopee_mais_vendidos(palavra)
            except Exception as e:
                print(f"[aviso] Shopee '{palavra}': {e}")
                continue
            bons = 0
            maior = max((int(n.get("sales") or 0) for n in nos), default=0)
            for n in nos:
                it = item_shopee(n, cat)
                if not it or it["vendas"] < VENDAS_MIN or (it["nota"] and it["nota"] < NOTA_MIN):
                    continue
                achados.setdefault(it["id"], it)
                bons += 1
            print(f"[catalogo] Shopee {cat} / '{palavra}': {len(nos)} vieram, {bons} passaram no filtro "
                  f"(o mais vendido tinha {maior} vendas)")
            time.sleep(2)                       # respiro pra API (com 1s ela dava "System Error")
    # Junta tudo, mais vendidos primeiro, e corta em POR_CATEGORIA pela categoria FINAL (a do nome)
    todos, vistos = [], set()
    for achados in por_cat.values():
        for it in achados.values():
            if it["id"] not in vistos:
                vistos.add(it["id"])
                todos.append(it)
    todos.sort(key=lambda i: -i["vendas"])
    saida, conta = [], {}
    for it in todos:
        if conta.get(it["categoria"], 0) >= POR_CATEGORIA:
            continue
        conta[it["categoria"]] = conta.get(it["categoria"], 0) + 1
        saida.append(it)
    print(f"[catalogo] Shopee por categoria: {conta}")
    return saida


# ------------------------------- Amazon --------------------------------------
def amazon_do_garimpo() -> list[dict]:
    """Achados da Amazon que o radar já guardou no garimpo (todos os perfis), com o link de vocês."""
    arquivos = [PASTA / "garimpo.json"] + sorted((PASTA / "perfis").glob("*/garimpo.json"))
    limite = datetime.now(BR) - timedelta(hours=HORAS_AMAZON)
    saida, vistos = [], set()
    for arq in arquivos:
        try:
            itens = json.loads(arq.read_text(encoding="utf-8")).get("itens", [])
        except Exception:
            continue
        for i in itens:
            if i.get("loja") != "Amazon" or not i.get("converter") or i.get("id") in vistos:
                continue
            try:
                if datetime.fromisoformat(i.get("achado_em") or i.get("quando")) < limite:
                    continue
            except Exception:
                continue
            vistos.add(i["id"])
            cat = i.get("categoria") or "Outros"
            taxa = COMISSAO_AMAZON.get(cat.lower())
            preco = float(i.get("preco") or 0)
            if preco <= 0:
                continue
            saida.append({
                "id": i["id"], "loja": "Amazon", "nome": i.get("nome", "")[:120],
                "preco": round(preco, 2), "preco_max": 0,
                "preco_antes": round(float(i.get("preco_antes") or 0), 2),
                "desconto": int(i.get("desconto") or 0),
                "comissao_pct": round(taxa * 100, 1) if taxa else None,
                "comissao_rs": round(preco * taxa, 2) if taxa else None,
                "vendas": 0, "nota": 0,
                "imagem": i.get("imagem") or "", "link": i["link"],
                "categoria": cat, "vendedor": "",
                "achado_em": i.get("achado_em") or i.get("quando"),
            })
    return saida


# ------------------------------- Teste ---------------------------------------
def exemplo() -> list[dict]:
    """Catálogo falso pra testar a página sem internet nem chave."""
    random.seed(7)
    nomes = {
        "Fitness": ["Legging Cintura Alta Sem Costura", "Conjunto Fitness Top e Short", "Top Nadador Bojo Removível"],
        "Moda": ["Calça Pantacourt Viscose Tamanho G GG", "Vestido Midi Canelado", "Bolsa Tiracolo Feminina"],
        "Beleza": ["Sérum Vitamina C 30ml", "Kit Pincéis Maquiagem 12 peças", "Escova Secadora 3 em 1"],
        "Casa": ["Kit 10 Potes Herméticos", "Organizador de Gaveta 6 Divisórias", "Garrafa Térmica 1L Inox"],
        "Bebê": ["Kit Body Bebê 5 peças", "Brinquedo Jogo da Memória Infantil"],
        "Eletrônicos": ["Fone Bluetooth TWS", "Smartwatch Tela AMOLED"],
        "Eletrodomésticos": ["Mini Processador Elétrico 250ml"],
    }
    saida = []
    for cat, lista in nomes.items():
        for k, nome in enumerate(lista):
            preco = round(random.uniform(15, 160), 2)
            taxa = random.choice([0.04, 0.07, 0.08, 0.1, 0.12, 0.2, 0.24])
            desc = random.choice([0, 15, 30, 45])
            saida.append({
                "id": f"shopee-teste{cat[:3]}{k}", "loja": "Shopee", "nome": nome,
                "preco": preco, "preco_max": round(preco * 1.3, 2) if k == 1 else 0,
                "preco_antes": round(preco / (1 - desc / 100), 2) if desc else 0, "desconto": desc,
                "comissao_pct": round(taxa * 100, 1), "comissao_rs": round(preco * taxa, 2),
                "vendas": random.choice([180, 1200, 5400, 23000, 87000]), "nota": random.choice([4.6, 4.8, 4.9]),
                "imagem": "", "link": f"https://s.shopee.com.br/teste{k}", "categoria": cat, "vendedor": "Loja Teste",
            })
    saida.append({"id": "radar-B0TESTE123", "loja": "Amazon", "nome": "Air Fryer 4L Digital (exemplo)",
                  "preco": 299.9, "preco_max": 0, "preco_antes": 449.9, "desconto": 33,
                  "comissao_pct": None, "comissao_rs": None, "vendas": 0, "nota": 0, "imagem": "",
                  "link": "https://www.amazon.com.br/dp/B0TESTE123?tag=teste-20",
                  "categoria": "Eletrodomésticos", "vendedor": "", "achado_em": datetime.now(BR).isoformat(timespec="minutes")})
    return saida


# ------------------------------- Principal -----------------------------------
def main() -> None:
    teste = "--teste" in sys.argv
    if teste:
        itens = exemplo()
    else:
        itens = buscar_shopee()
        amz = amazon_do_garimpo()
        print(f"[catalogo] Amazon do garimpo: {len(amz)} achado(s)")
        itens += amz
        if not itens and SAIDA.exists():
            print("[catalogo] nada veio desta vez: mantive o catálogo anterior.")
            return
    SAIDA.write_text(json.dumps({
        "atualizado": datetime.now(BR).isoformat(timespec="minutes"),
        "exemplo": teste,
        "itens": itens,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    lojas = {}
    for i in itens:
        lojas[i["loja"]] = lojas.get(i["loja"], 0) + 1
    print(f"[catalogo] {len(itens)} produto(s) no catálogo: {lojas}")


if __name__ == "__main__":
    main()
