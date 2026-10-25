"""Monta o cartão da oferta (1080x1080) com a marca do grupo, no estilo dos canais grandes:
foto do produto num painel branco, selo de desconto, loja, nome, preço "de/por" e o logo no rodapé.

Uso: cartao.gerar(oferta, foto_bytes, perfil) -> bytes JPEG (ou None se não der).
Arquivos de marca (fontes Inter, licença OFL, e os logos) ficam na pasta marca/ ou soltos junto do bot.py.
"""
from __future__ import annotations

import io
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:          # sem Pillow, o robô segue mandando a foto normal
    Image = None

_AQUI = Path(__file__).parent
# Fontes e logos podem ficar na pasta marca/ ou soltos junto do bot.py
MARCA = _AQUI / "marca" if (_AQUI / "marca").is_dir() else _AQUI

TEMAS = {
    "ingrid": {
        "fundo": (247, 243, 236), "texto": (41, 37, 34), "texto2": (110, 98, 90),
        "destaque": (90, 64, 50), "selo": (181, 138, 104), "selo_texto": (255, 255, 255),
        "rodape": (90, 64, 50), "rodape_texto": (247, 243, 236),
        "nome": "Achadinhos da Indy", "logo": "logo-ingrid.png",
    },
    "blackfriday": {
        "fundo": (14, 11, 17), "texto": (255, 255, 255), "texto2": (190, 182, 200),
        "destaque": (255, 210, 63), "selo": (255, 210, 63), "selo_texto": (17, 17, 17),
        "rodape": (255, 210, 63), "rodape_texto": (17, 17, 17),
        "nome": "Black Friday da Indy", "logo": "logo-blackfriday.png",
    },
}


def _fonte(arquivo: str, tamanho: int):
    try:
        return ImageFont.truetype(str(MARCA / arquivo), tamanho)
    except Exception:
        return ImageFont.load_default()


def _brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _quebra(desenho, texto: str, fonte, largura: int, linhas: int) -> list[str]:
    palavras, saida, atual = texto.split(), [], ""
    for p in palavras:
        teste = (atual + " " + p).strip()
        if desenho.textlength(teste, font=fonte) <= largura:
            atual = teste
        else:
            if atual:
                saida.append(atual)
            atual = p
        if len(saida) == linhas:
            break
    if atual and len(saida) < linhas:
        saida.append(atual)
    if len(saida) == linhas and " ".join(saida) != texto.strip():
        while saida[-1] and desenho.textlength(saida[-1] + "…", font=fonte) > largura:
            saida[-1] = saida[-1][:-1]
        saida[-1] = saida[-1].rstrip(" ,.-") + "…"
    return saida


def gerar(o: dict, foto: bytes | None, perfil: str = "ingrid") -> bytes | None:
    if Image is None:
        return None
    t = TEMAS.get(perfil, TEMAS["ingrid"])
    W = H = 1080
    img = Image.new("RGB", (W, H), t["fundo"])
    d = ImageDraw.Draw(img)

    # Painel branco com a foto do produto
    px0, py0, px1, py1 = 48, 48, W - 48, 640
    d.rounded_rectangle((px0, py0, px1, py1), radius=36, fill=(255, 255, 255))
    if foto:
        try:
            ft = Image.open(io.BytesIO(foto)).convert("RGBA")
            ft = ImageOps.contain(ft, (px1 - px0 - 120, py1 - py0 - 90), Image.LANCZOS)
            fx = px0 + (px1 - px0 - ft.width) // 2
            fy = py0 + (py1 - py0 - ft.height) // 2 + 10
            img.paste(ft, (fx, fy), ft)
        except Exception:
            foto = None
    if not foto:
        f = _fonte("InterDisplay-Black.otf", 64)
        for i, linha in enumerate(_quebra(d, o["nome"], f, px1 - px0 - 120, 4)):
            d.text(((px0 + px1) // 2, 230 + i * 80), linha, font=f, fill=(41, 37, 34), anchor="mm")

    # Selo de desconto
    desc = int(o.get("desconto") or 0)
    if desc > 0:
        fs = _fonte("InterDisplay-Black.otf", 64)
        txt = f"-{desc}%"
        larg = int(d.textlength(txt, font=fs)) + 60
        d.rounded_rectangle((px0 + 24, py0 + 24, px0 + 24 + larg, py0 + 24 + 96), radius=48, fill=t["selo"])
        d.text((px0 + 24 + larg // 2, py0 + 24 + 50), txt, font=fs, fill=t["selo_texto"], anchor="mm")

    # Loja
    loja = o.get("loja") or ""
    if loja:
        fl = _fonte("Inter-Bold.otf", 34)
        larg = int(d.textlength(loja, font=fl)) + 48
        d.rounded_rectangle((px1 - 24 - larg, py0 + 32, px1 - 24, py0 + 32 + 64), radius=32, fill=(28, 24, 32))
        d.text((px1 - 24 - larg // 2, py0 + 32 + 33), loja, font=fl, fill=(255, 255, 255), anchor="mm")

    # Nome do produto
    fn = _fonte("Inter-SemiBold.otf", 40)
    y = 672
    for linha in _quebra(d, o["nome"], fn, W - 96, 2):
        d.text((48, y), linha, font=fn, fill=t["texto"])
        y += 52

    # Preço de / por
    antes = o.get("preco_antes") or (o["preco"] / (1 - desc / 100) if 0 < desc < 100 else 0)
    yp = 790
    if antes and antes > o["preco"]:
        fa = _fonte("Inter-SemiBold.otf", 38)
        txt = "De " + _brl(antes)
        d.text((48, yp), txt, font=fa, fill=t["texto2"])
        lw = d.textlength(txt, font=fa)
        d.line((48 + d.textlength("De ", font=fa), yp + 24, 48 + lw, yp + 24), fill=t["texto2"], width=4)
        yp += 50
    fp = _fonte("InterDisplay-Black.otf", 104)
    d.text((44, yp - 8), _brl(o["preco"]), font=fp, fill=t["destaque"])

    # Rodapé com o logo do grupo
    ry = H - 118
    d.rectangle((0, ry, W, H), fill=t["rodape"])
    try:
        lg = Image.open(MARCA / t["logo"]).convert("RGBA").resize((150, 150), Image.LANCZOS)
        img.paste(lg, (W - 48 - 150, ry - 75), lg)
    except Exception:
        pass
    fr = _fonte("InterDisplay-Black.otf", 46)
    d.text((48, ry + 59), t["nome"], font=fr, fill=t["rodape_texto"], anchor="lm")

    saida = io.BytesIO()
    img.save(saida, "JPEG", quality=88, optimize=True)
    return saida.getvalue()
