"""
MÃ³dulo de CÃ¡lculo de KM e LocalizaÃ§Ã£o Precisa.

Estima a posiÃ§Ã£o quilomÃ©trica (KM) de incidentes a partir de coordenadas
lat/lng usando interpolaÃ§Ã£o linear entre pontos de referÃªncia conhecidos.

FunÃ§Ãµes principais:
- haversine(): distÃ¢ncia entre 2 pontos geogrÃ¡ficos
- estimar_km(): interpola KM a partir de coordenadas
- detectar_sentido(): identifica direÃ§Ã£o do incidente
- identificar_trecho_local(): retorna "entre X e Y"
- enriquecer_incidente(): funÃ§Ã£o orquestradora
"""
import math
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Raio mÃ©dio da Terra em km
RAIO_TERRA_KM = 6371.0
MAX_GAP_INTERMUNICIPAL_KM = 60
MAX_GAP_METROPOLITANO_KM = 20
_HINTS_METROPOLITANOS = (
    "marginal",
    "avenida",
    "ponte",
    "centro",
    "urbano",
    "recife",
    "olinda",
    "paulista",
    "deodoro",
    "jaguare",
    "jaguarÃ©",
    "sao paulo",
    "sÃ£o paulo",
    "taboao",
    "taboÃ£o",
    "embu",
    "itapecerica",
    "diadema",
    "sao bernardo",
    "sÃ£o bernardo",
)
_SUFIXO_PONTO_PATTERN = re.compile(
    r"\s*-\s*(?:in[iÃ­]cio|fim)\b.*$",
    flags=re.IGNORECASE,
)
_PARENTESES_FINAIS_PATTERN = re.compile(r"\s*\([^)]*\)\s*$")


def _limpar_nome_local(local: str) -> str:
    """Normaliza nome de ponto para exibicao curta no relatorio."""
    txt = (local or "").strip()
    if not txt:
        return ""
    txt = _SUFIXO_PONTO_PATTERN.sub("", txt)
    txt = _PARENTESES_FINAIS_PATTERN.sub("", txt)
    return " ".join(txt.split()).strip(" -")


def _eh_trecho_metropolitano(local1: str, local2: str) -> bool:
    txt = f"{_limpar_nome_local(local1)} {_limpar_nome_local(local2)}".casefold()
    return any(hint in txt for hint in _HINTS_METROPOLITANOS)


def _limite_gap_km(p1: dict, p2: dict, gap_limit_km: Optional[float]) -> float:
    if gap_limit_km is not None:
        return float(gap_limit_km)

    limit_p1 = p1.get("limite_gap_km")
    limit_p2 = p2.get("limite_gap_km")
    if limit_p1 is not None:
        return float(limit_p1)
    if limit_p2 is not None:
        return float(limit_p2)

    if _eh_trecho_metropolitano(p1.get("local", ""), p2.get("local", "")):
        return MAX_GAP_METROPOLITANO_KM

    return MAX_GAP_INTERMUNICIPAL_KM


def _local_mais_proximo_por_km(km_estimado: float, p1: dict, p2: dict) -> str:
    d1 = abs(km_estimado - p1["km"])
    d2 = abs(km_estimado - p2["km"])
    escolhido = p1 if d1 <= d2 else p2
    return _limpar_nome_local(escolhido.get("local", ""))


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calcula distÃ¢ncia em km entre dois pontos usando fÃ³rmula de Haversine."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)

    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return RAIO_TERRA_KM * c


def calcular_bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calcula bearing (azimute) em graus de ponto1 para ponto2. Retorna 0-360."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlng = math.radians(lng2 - lng1)

    x = math.sin(dlng) * math.cos(lat2_r)
    y = (math.cos(lat1_r) * math.sin(lat2_r)
         - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlng))

    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def estimar_km(lat: float, lng: float, pontos_referencia: list) -> dict:
    """
    Estima posiÃ§Ã£o quilomÃ©trica a partir de coordenadas lat/lng.

    Algoritmo:
    1. Calcula distÃ¢ncia do incidente a cada ponto de referÃªncia
    2. Encontra os 2 pontos mais prÃ³ximos consecutivos
    3. Interpola KM com base na proporÃ§Ã£o de distÃ¢ncia

    Args:
        lat, lng: coordenadas do incidente
        pontos_referencia: lista de dicts com {km, lat, lng, local}

    Returns:
        dict com km_estimado, confianca (0-1), ponto_mais_proximo
    """
    if not pontos_referencia or len(pontos_referencia) < 2:
        return {"km_estimado": None, "confianca": 0.0, "ponto_mais_proximo": None}

    if lat is None or lng is None:
        return {"km_estimado": None, "confianca": 0.0, "ponto_mais_proximo": None}

    # Ordenar por KM
    pontos = sorted(pontos_referencia, key=lambda p: p["km"])

    # Calcular distÃ¢ncia a cada ponto
    distancias = []
    for p in pontos:
        d = haversine(lat, lng, p["lat"], p["lng"])
        distancias.append({"ponto": p, "distancia_km": d})

    # Ponto mais prÃ³ximo
    mais_proximo = min(distancias, key=lambda x: x["distancia_km"])

    # Se muito longe de todos os pontos (>50km), baixa confianÃ§a
    if mais_proximo["distancia_km"] > 50:
        return {
            "km_estimado": mais_proximo["ponto"]["km"],
            "confianca": 0.2,
            "ponto_mais_proximo": mais_proximo["ponto"].get("local", ""),
        }

    # Encontrar par de pontos consecutivos que melhor "enquadra" o incidente
    melhor_par = None
    melhor_erro = float("inf")

    for i in range(len(pontos) - 1):
        p1 = pontos[i]
        p2 = pontos[i + 1]

        d1 = haversine(lat, lng, p1["lat"], p1["lng"])
        d2 = haversine(lat, lng, p2["lat"], p2["lng"])
        d_entre = haversine(p1["lat"], p1["lng"], p2["lat"], p2["lng"])

        if d_entre == 0:
            continue

        # ProporÃ§Ã£o ao longo do segmento
        proporcao = d1 / (d1 + d2)

        # Erro: quanto o incidente estÃ¡ fora da linha reta entre p1-p2
        erro = abs((d1 + d2) - d_entre)

        if erro < melhor_erro:
            melhor_erro = erro
            melhor_par = {
                "p1": p1, "p2": p2,
                "d1": d1, "d2": d2,
                "d_entre": d_entre,
                "proporcao": proporcao,
            }

    if melhor_par is None:
        return {
            "km_estimado": mais_proximo["ponto"]["km"],
            "confianca": 0.3,
            "ponto_mais_proximo": mais_proximo["ponto"].get("local", ""),
        }

    # Interpolar KM
    km1 = melhor_par["p1"]["km"]
    km2 = melhor_par["p2"]["km"]
    km_estimado = km1 + (km2 - km1) * melhor_par["proporcao"]

    # ConfianÃ§a baseada na qualidade da interpolaÃ§Ã£o
    desvio_lateral = melhor_erro  # km fora da linha reta
    if desvio_lateral < 2:
        confianca = 1.0
    elif desvio_lateral < 5:
        confianca = 0.8
    elif desvio_lateral < 15:
        confianca = 0.6
    else:
        confianca = 0.4

    return {
        "km_estimado": round(km_estimado, 1),
        "confianca": confianca,
        "ponto_mais_proximo": mais_proximo["ponto"].get("local", ""),
    }


def detectar_sentido(lat: float, lng: float, segmento: dict) -> Optional[str]:
    """
    Detecta sentido do incidente relativo ao segmento.

    Usa o campo 'sentido' do segmento como referÃªncia principal.
    """
    if not segmento:
        return None
    return segmento.get("sentido")


def identificar_trecho_local(
    km_estimado: float,
    pontos_referencia: list,
    gap_limit_km: Optional[float] = None,
) -> str:
    """
    Identifica trecho local em formato curto para uso operacional.

    Exemplo: "Resende -> Rio de Janeiro"
    Para gaps grandes, retorna "proximo a <local>" para evitar inferencia imprecisa.
    """
    if not pontos_referencia or km_estimado is None:
        return ""

    pontos = sorted(pontos_referencia, key=lambda p: p["km"])

    if km_estimado <= pontos[0]["km"]:
        local = _limpar_nome_local(pontos[0].get("local", ""))
        return f"proximo a {local}" if local else ""

    if km_estimado >= pontos[-1]["km"]:
        local = _limpar_nome_local(pontos[-1].get("local", ""))
        return f"proximo a {local}" if local else ""

    for i in range(len(pontos) - 1):
        p1 = pontos[i]
        p2 = pontos[i + 1]
        if p1["km"] <= km_estimado <= p2["km"]:
            local1 = _limpar_nome_local(p1.get("local", ""))
            local2 = _limpar_nome_local(p2.get("local", ""))

            gap_km = abs(p2["km"] - p1["km"])
            limite = _limite_gap_km(p1, p2, gap_limit_km)
            if gap_km > limite:
                local_proximo = _local_mais_proximo_por_km(km_estimado, p1, p2)
                return f"proximo a {local_proximo}" if local_proximo else ""

            if local1 and local2:
                return f"{local1} -> {local2}"
            return local1 or local2

    return ""


def enriquecer_incidente(incidente: dict, segmentos: list) -> dict:
    """
    Enriquece um incidente HERE com dados de localizaÃ§Ã£o precisa.

    Adiciona ao incidente:
    - km_estimado: posiÃ§Ã£o KM estimada
    - sentido: direÃ§Ã£o (ex: "Sul", "Norteâ†’Sul")
    - trecho_especifico: "entre X e Y"
    - confianca_localizacao: 0.0-1.0
    - rodovia_segmento: rodovia do segmento correspondente
    - nome_popular: nome popular da rodovia (ex: "RÃ©gis Bittencourt")

    Args:
        incidente: dict com lat/lng do incidente HERE
        segmentos: lista de segmentos do config

    Returns:
        incidente enriquecido (mesmo dict, modificado in-place)
    """
    # Valores default
    incidente.setdefault("km_estimado", None)
    incidente.setdefault("sentido", None)
    incidente.setdefault("trecho_especifico", "")
    incidente.setdefault("confianca_localizacao", 0.0)
    incidente.setdefault("rodovia_segmento", "")
    incidente.setdefault("nome_popular", "")

    if not segmentos:
        return incidente

    lat = incidente.get("latitude")
    lng = incidente.get("longitude")

    if lat is None or lng is None:
        return incidente

    # Encontrar melhor segmento (o mais prÃ³ximo geograficamente)
    melhor_resultado = None
    melhor_confianca = -1

    for seg in segmentos:
        pontos = seg.get("pontos_referencia", [])
        if not pontos:
            continue

        resultado = estimar_km(lat, lng, pontos)

        if resultado["confianca"] > melhor_confianca:
            melhor_confianca = resultado["confianca"]
            melhor_resultado = resultado
            melhor_resultado["segmento"] = seg

    if melhor_resultado and melhor_resultado["km_estimado"] is not None:
        seg = melhor_resultado["segmento"]
        pontos = seg.get("pontos_referencia", [])

        incidente["km_estimado"] = melhor_resultado["km_estimado"]
        incidente["confianca_localizacao"] = melhor_resultado["confianca"]
        incidente["sentido"] = detectar_sentido(lat, lng, seg)
        incidente["trecho_especifico"] = identificar_trecho_local(
            melhor_resultado["km_estimado"],
            pontos,
            gap_limit_km=seg.get("limite_gap_km"),
        )
        incidente["rodovia_segmento"] = seg.get("rodovia", "")
        incidente["nome_popular"] = seg.get("nome_popular", "")

        # Enriquecer descriÃ§Ã£o
        desc_parts = []
        rodovia = seg.get("rodovia", "")
        nome_pop = seg.get("nome_popular", "")

        if rodovia:
            label = f"{rodovia} ({nome_pop})" if nome_pop else rodovia
            desc_parts.append(label)

        km = melhor_resultado["km_estimado"]
        if melhor_resultado["confianca"] >= 0.6:
            desc_parts.append(f"KM {km}")
        else:
            desc_parts.append(f"KM ~{km} (estimado)")

        sentido = incidente["sentido"]
        if sentido:
            desc_parts.append(f"sentido {sentido}")

        trecho = incidente["trecho_especifico"]
        if trecho:
            desc_parts.append(trecho)

        incidente["localizacao_precisa"] = ", ".join(desc_parts)

        logger.debug(
            f"Incidente enriquecido: {incidente['localizacao_precisa']} "
            f"(confianÃ§a: {melhor_resultado['confianca']:.0%})"
        )

    return incidente

