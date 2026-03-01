"""
Módulo TomTom Traffic API.

Complementa HERE e Google com detecção adicional de incidentes e fluxo.

Endpoints:
  - Traffic Incidents v5: incidentes por bbox (acidente, obras, interdição, etc.)
  - Flow Segment Data v4: velocidade atual vs free-flow por ponto

Free tier: 2.500 requests/dia (non-tile), 5 QPS.
"""
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from sources.circuit import tomtom_incidents_breaker, tomtom_flow_breaker
from sources.km_calculator import enriquecer_incidente, haversine

logger = logging.getLogger(__name__)

# ===== HTTP Session thread-safe com retry =====
_thread_local = threading.local()


def _get_sessao():
    """Retorna uma requests.Session thread-local com retry automático."""
    if not hasattr(_thread_local, "sessao"):
        s = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            connect=2,
            read=2,
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _thread_local.sessao = s
    return _thread_local.sessao


def _parse_coords(valor):
    """Parseia coordenadas de dict ou string 'lat,lng'. Retorna (lat, lng) floats."""
    if isinstance(valor, dict):
        return float(valor.get("lat", 0)), float(valor.get("lng", 0))
    if isinstance(valor, str) and "," in valor:
        partes = valor.split("!")[0].split(",")
        try:
            return float(partes[0]), float(partes[1])
        except (ValueError, IndexError):
            pass
    return 0.0, 0.0


def _sanitizar_erro(erro, api_key=""):
    """Remove API key de mensagens de erro para evitar vazamento em logs."""
    msg = str(erro)
    if api_key:
        msg = msg.replace(api_key, "***")
    return msg


def _validar_json_response(resp, contexto=""):
    """Valida que a resposta é JSON e retorna o dict parseado."""
    content_type = resp.headers.get("Content-Type", "")
    if "json" not in content_type:
        logger.warning(f"{contexto} Content-Type inesperado: {content_type}")
    try:
        return resp.json()
    except ValueError:
        logger.error(f"{contexto} Resposta nao e JSON valido")
        return None


# ===== Mapeamento TomTom -> categorias internas =====
# iconCategory codes do TomTom Incidents API v5
CATEGORIA_MAP = {
    0: "Ocorr\u00eancia",      # Unknown
    1: "Colis\u00e3o",         # Accident
    2: "Condi\u00e7\u00e3o Clim\u00e1tica",  # Fog
    3: "Condi\u00e7\u00e3o Clim\u00e1tica",  # DangerousConditions
    4: "Condi\u00e7\u00e3o Clim\u00e1tica",  # Rain
    5: "Condi\u00e7\u00e3o Clim\u00e1tica",  # Ice
    6: "Engarrafamento",       # Jam
    7: "Bloqueio Parcial",     # LaneClosed
    8: "Ocorr\u00eancia",      # RoadClosed (classificado abaixo)
    9: "Obras na Pista",       # RoadWorks
    10: "Condi\u00e7\u00e3o Clim\u00e1tica",  # Wind
    11: "Condi\u00e7\u00e3o Clim\u00e1tica",  # Flooding
    14: "Colis\u00e3o",        # BrokenDownVehicle
}

# magnitudeOfDelay -> severidade_id interna (1-4)
SEVERIDADE_MAP = {
    0: 1,  # Unknown -> Baixa
    1: 1,  # Minor -> Baixa
    2: 2,  # Moderate -> Média
    3: 3,  # Major -> Alta
    4: 2,  # Undefined (road closures) -> Média
}

# Categorias de incidentes a filtrar (exclui clima leve)
CATEGORY_FILTER = "0,1,6,7,8,9,14"

_BLOQUEIO_TOTAL_TEXTOS = (
    "bloqueio total",
    "interdição total",
    "interdicao total",
    "via totalmente interditada",
    "todos os sentidos bloqueados",
    "ambos os sentidos bloqueados",
    "road closed",
)

_BLOQUEIO_PARCIAL_TEXTOS = (
    "faixa fechada",
    "faixa bloqueada",
    "faixa interditada",
    "uma faixa",
    "meia pista",
    "pare e siga",
    "desvio operacional",
    "tráfego fluindo",
    "trafego fluindo",
)

_CAUSA_TEXTO = {
    "acidente": "acidente",
    "colisão": "acidente",
    "colisao": "acidente",
    "capotamento": "acidente",
    "engavetamento": "acidente",
    "tombamento": "acidente",
    "obras": "obra",
    "work": "obra",
    "chuva": "clima",
    "alagamento": "clima",
    "neblina": "clima",
    "fog": "clima",
    "flood": "clima",
    "wind": "clima",
    "deslizamento": "risco",
    "queda de barreira": "risco",
    "obstáculo": "risco",
    "obstaculo": "risco",
    "hazard": "risco",
}

# ===== BBox helpers =====


def _texto_contem_qualquer(texto, termos):
    return any(termo in texto for termo in termos)


def _detectar_causa_tomtom(icon_cat, texto):
    if icon_cat in (1, 14):
        return "acidente"
    if icon_cat == 9:
        return "obra"
    if icon_cat in (2, 3, 4, 5, 10, 11):
        return "clima"

    texto_lower = (texto or "").lower()
    for termo, causa in _CAUSA_TEXTO.items():
        if termo in texto_lower:
            return causa
    return "indefinida"


def _detectar_bloqueio_escopo_tomtom(icon_cat, texto):
    texto_lower = (texto or "").lower()

    if icon_cat == 8:
        return "total"

    if _texto_contem_qualquer(texto_lower, _BLOQUEIO_TOTAL_TEXTOS):
        return "total"

    if icon_cat == 7:
        return "parcial"

    if _texto_contem_qualquer(texto_lower, _BLOQUEIO_PARCIAL_TEXTOS):
        return "parcial"

    return "nenhum"


def _classificar_categoria_tomtom(icon_cat, texto):
    bloqueio_escopo = _detectar_bloqueio_escopo_tomtom(icon_cat, texto)
    causa_detectada = _detectar_causa_tomtom(icon_cat, texto)

    if bloqueio_escopo == "total":
        return "Interdi\u00e7\u00e3o", bloqueio_escopo, causa_detectada

    if causa_detectada == "acidente":
        return "Colis\u00e3o", bloqueio_escopo, causa_detectada

    if bloqueio_escopo == "parcial":
        return "Bloqueio Parcial", bloqueio_escopo, causa_detectada

    return CATEGORIA_MAP.get(icon_cat, "Ocorr\u00eancia"), bloqueio_escopo, causa_detectada


def _calcular_bbox(origem_lat, origem_lng, destino_lat, destino_lng, padding_km=15.0):
    """Calcula bounding box com padding ao redor de dois pontos."""
    pad = padding_km * 0.009  # ~1 grau ≈ 111km
    south = max(-90.0, min(origem_lat, destino_lat) - pad)
    north = min(90.0, max(origem_lat, destino_lat) + pad)
    west = max(-180.0, min(origem_lng, destino_lng) - pad)
    east = min(180.0, max(origem_lng, destino_lng) + pad)
    return west, south, east, north


def _bbox_area_km2(west, south, east, north):
    """Estima área do bbox em km² (aproximação simples)."""
    lat_mid = (south + north) / 2.0
    km_per_deg_lat = 111.0
    km_per_deg_lng = 111.0 * math.cos(math.radians(lat_mid))
    return abs(north - south) * km_per_deg_lat * abs(east - west) * km_per_deg_lng


def _formatar_bbox_tomtom(west, south, east, north):
    """Formata bbox no formato TomTom: minLon,minLat,maxLon,maxLat."""
    return f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}"


def _gerar_bbox_tomtom(origem_lat, origem_lng, destino_lat, destino_lng,
                       padding_km=15.0, max_area_km2=10000):
    """Gera bbox para TomTom (máx 10.000 km²). Reduz padding se necessário."""
    for attempt_pad in [padding_km, 10.0, 5.0, 2.0]:
        west, south, east, north = _calcular_bbox(
            origem_lat, origem_lng, destino_lat, destino_lng, padding_km=attempt_pad
        )
        area = _bbox_area_km2(west, south, east, north)
        if area <= max_area_km2:
            return _formatar_bbox_tomtom(west, south, east, north)

    # Último recurso: bbox mínimo sem padding
    west, south, east, north = _calcular_bbox(
        origem_lat, origem_lng, destino_lat, destino_lng, padding_km=0.5
    )
    area = _bbox_area_km2(west, south, east, north)
    if area > max_area_km2:
        return None  # Rota longa demais para bbox TomTom
    return _formatar_bbox_tomtom(west, south, east, north)


# ===== Incidents API v5 =====

@tomtom_incidents_breaker
def consultar_incidentes(api_key, trecho):
    """Consulta incidentes TomTom para um trecho."""
    nome = trecho.get("nome", "")
    origem = trecho.get("origem", {})
    destino = trecho.get("destino", {})

    o_lat, o_lng = _parse_coords(origem)
    d_lat, d_lng = _parse_coords(destino)

    if o_lat == 0 or d_lat == 0:
        return []

    bbox_str = _gerar_bbox_tomtom(o_lat, o_lng, d_lat, d_lng)
    if bbox_str is None:
        logger.info(f"[{nome}] TomTom Incidents ignorado: rota longa demais para bbox 10000 km2")
        return []

    params = {
        "key": api_key,
        "bbox": bbox_str,
        "fields": (
            "{incidents{type,geometry{type,coordinates},"
            "properties{id,iconCategory,magnitudeOfDelay,events{description,code,iconCategory},"
            "startTime,endTime,from,to,length,delay,roadNumbers,timeValidity,"
            "probabilityOfOccurrence,numberOfReports,lastReportTime}}}"
        ),
        "language": "pt-PT",
        "categoryFilter": CATEGORY_FILTER,
        "timeValidityFilter": "present",
    }

    try:
        resp = _get_sessao().get(
            "https://api.tomtom.com/traffic/services/5/incidentDetails",
            params=params,
            timeout=15,
        )
        if resp.status_code == 400:
            body = resp.text[:500] if resp.text else ""
            logger.warning(f"[{nome}] TomTom Incidents 400 | bbox={bbox_str} | resp={body}")
            return []
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"[{nome}] TomTom Incidents erro: {_sanitizar_erro(e, api_key)}")
        return []

    data = _validar_json_response(resp, f"[{nome}] TomTom Incidents")
    if not data:
        return []

    raw_incidents = data.get("incidents", [])

    # Filtrar incidentes por proximidade ao trecho
    segmentos = trecho.get("segmentos", [])
    incidentes = []
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for inc in raw_incidents:
        props = inc.get("properties", {})
        geom = inc.get("geometry", {})

        icon_cat = props.get("iconCategory", 0)
        magnitude = props.get("magnitudeOfDelay", 0)
        severidade_id = SEVERIDADE_MAP.get(magnitude, 1)

        # Extrair coordenadas do incidente
        coords = geom.get("coordinates", [])
        if not coords:
            continue

        # Ponto representativo (primeiro ponto para Point, meio para LineString)
        geom_type = geom.get("type", "Point")
        if geom_type == "Point" and len(coords) >= 2:
            inc_lng, inc_lat = coords[0], coords[1]
        elif geom_type == "LineString" and len(coords) > 0:
            mid = len(coords) // 2
            inc_lng, inc_lat = coords[mid][0], coords[mid][1]
        else:
            continue

        # Filtro de distância: incidente deve estar a até 500m da linha do trecho
        dist_ok = _verificar_proximidade(
            inc_lat, inc_lng, o_lat, o_lng, d_lat, d_lng, max_dist_m=500
        )
        if not dist_ok:
            continue

        # Montar descrição
        events = props.get("events", [])
        descricao_parts = [e.get("description", "") for e in events if e.get("description")]
        texto_classificacao = " | ".join(descricao_parts)
        categoria, bloqueio_escopo, causa_detectada = _classificar_categoria_tomtom(
            icon_cat, texto_classificacao,
        )
        descricao = "; ".join(descricao_parts) if descricao_parts else categoria

        # Localização textual
        loc_from = props.get("from", "")
        loc_to = props.get("to", "")
        trecho_especifico = ""
        if loc_from and loc_to:
            trecho_especifico = f"{loc_from} → {loc_to}"
        elif loc_from:
            trecho_especifico = loc_from

        delay_s = props.get("delay", 0) or 0

        incidente = {
            "trecho": nome,
            "categoria": categoria,
            "severidade_id": severidade_id,
            "descricao": descricao,
            "bloqueio_escopo": bloqueio_escopo,
            "causa_detectada": causa_detectada,
            "icon_category_raw": icon_cat,
            "latitude": inc_lat,
            "longitude": inc_lng,
            "trecho_especifico": trecho_especifico,
            "localizacao_precisa": trecho_especifico,
            "delay_s": delay_s,
            "fonte": "TomTom",
            "consultado_em": agora,
        }

        # Enriquecer com KM estimado se possível
        if segmentos:
            incidente = enriquecer_incidente(incidente, segmentos)

        incidentes.append(incidente)

    logger.info(
        f"[{nome}] TomTom bruto={len(raw_incidents)} | filtrado={len(incidentes)} incidente(s)"
    )
    return incidentes


def _verificar_proximidade(inc_lat, inc_lng, o_lat, o_lng, d_lat, d_lng, max_dist_m=500):
    """Verifica se um ponto está próximo da linha reta origem-destino."""
    # Distância do ponto à reta (aproximação simples via ponto mais próximo)
    # Projeta o ponto no segmento e calcula distância
    dx = d_lng - o_lng
    dy = d_lat - o_lat
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq < 1e-10:
        return haversine(inc_lat, inc_lng, o_lat, o_lng) <= max_dist_m / 1000.0

    t = max(0, min(1, ((inc_lng - o_lng) * dx + (inc_lat - o_lat) * dy) / seg_len_sq))
    proj_lat = o_lat + t * dy
    proj_lng = o_lng + t * dx

    dist_km = haversine(inc_lat, inc_lng, proj_lat, proj_lng)
    return dist_km * 1000.0 <= max_dist_m


# ===== Flow Segment Data v4 =====

@tomtom_flow_breaker
def consultar_fluxo(api_key, trecho):
    """Consulta fluxo TomTom (velocidade atual vs free-flow) para um trecho."""
    nome = trecho.get("nome", "")
    origem = trecho.get("origem", {})
    destino = trecho.get("destino", {})

    o_lat, o_lng = _parse_coords(origem)
    d_lat, d_lng = _parse_coords(destino)

    if o_lat == 0 or d_lat == 0:
        return _fluxo_vazio(nome)

    # Ponto sobre a rodovia real (se disponivel) para consulta de flow
    route_pts = trecho.get("route_pts", [])
    if route_pts and len(route_pts) >= 2:
        mid_idx = len(route_pts) // 2
        mid_lat, mid_lng = route_pts[mid_idx][0], route_pts[mid_idx][1]
    else:
        mid_lat = (o_lat + d_lat) / 2.0
        mid_lng = (o_lng + d_lng) / 2.0

    params = {
        "key": api_key,
        "point": f"{mid_lat:.6f},{mid_lng:.6f}",
        "unit": "KMPH",
    }

    try:
        resp = _get_sessao().get(
            "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/14/json",
            params=params,
            timeout=15,
        )
        if resp.status_code == 400:
            body = resp.text[:500] if resp.text else ""
            logger.warning(f"[{nome}] TomTom Flow 400 | point={params['point']} | resp={body}")
            return _fluxo_vazio(nome)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"[{nome}] TomTom Flow erro: {_sanitizar_erro(e, api_key)}")
        return _fluxo_vazio(nome)

    data = _validar_json_response(resp, f"[{nome}] TomTom Flow")
    if not data:
        return _fluxo_vazio(nome)

    flow = data.get("flowSegmentData", {})
    current_speed = flow.get("currentSpeed", 0)
    free_flow_speed = flow.get("freeFlowSpeed", 0)
    confidence = flow.get("confidence", 0.0)
    road_closure = flow.get("roadClosure", False)

    # Calcular jam_factor compatível com HERE (0=livre, 10=parado)
    if road_closure:
        jam_factor = 10.0
    elif free_flow_speed > 0:
        jam_factor = round(10.0 * max(0, 1.0 - current_speed / free_flow_speed), 1)
    else:
        jam_factor = 0.0

    # Classificar status
    if road_closure:
        status = "Parado"
    elif jam_factor <= 3:
        status = "Normal"
    elif jam_factor <= 5:
        status = "Moderado"
    elif jam_factor <= 8:
        status = "Intenso"
    else:
        status = "Parado"

    return {
        "trecho": nome,
        "status": status,
        "jam_factor": jam_factor,
        "velocidade_atual_kmh": current_speed,
        "velocidade_livre_kmh": free_flow_speed,
        "confidence": confidence,
        "road_closure": road_closure,
        "fonte": "TomTom Flow",
        "consultado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _fluxo_vazio(nome):
    """Retorna estrutura de fluxo vazia (sem dados)."""
    return {
        "trecho": nome,
        "status": "Sem dados",
        "jam_factor": 0,
        "velocidade_atual_kmh": 0,
        "velocidade_livre_kmh": 0,
        "confidence": 0.0,
        "road_closure": False,
        "fonte": "TomTom Flow",
        "consultado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ===== Entry point: consultar_todos =====

def _processar_trecho(api_key, trecho, idx, total):
    """Processa incidentes + fluxo para um trecho."""
    nome = trecho.get("nome", "")
    logger.info(f"TomTom [{idx}/{total}]: {nome}")

    incidentes = consultar_incidentes(api_key, trecho)
    fluxo = consultar_fluxo(api_key, trecho)

    logger.info(f"  [{nome}] TomTom Fluxo: {fluxo['status']} (jam={fluxo['jam_factor']})")

    return nome, incidentes, fluxo


def consultar_todos(api_key, trechos, tomtom_config=None):
    """Consulta incidentes e fluxo TomTom para todos os trechos."""
    if tomtom_config is None:
        tomtom_config = {}

    max_workers = int(tomtom_config.get("max_workers", 4))
    submit_delay_s = float(tomtom_config.get("submit_delay_s", 0.2))

    resultado = {"incidentes": {}, "fluxo": {}}
    total = len(trechos)

    logger.info(f"TomTom: processando {total} trechos ({max_workers} workers)")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for i, trecho in enumerate(trechos, 1):
            future = pool.submit(_processar_trecho, api_key, trecho, i, total)
            futures[future] = trecho["nome"]
            if i < total and submit_delay_s > 0:
                time.sleep(submit_delay_s)

        for future in as_completed(futures):
            try:
                nome, incs, fluxo = future.result()
                if incs:
                    resultado["incidentes"][nome] = incs
                resultado["fluxo"][nome] = fluxo
            except Exception as e:
                nome = futures[future]
                logger.warning(f"[{nome}] TomTom erro: {_sanitizar_erro(e, api_key)}")

    total_inc = sum(len(v) for v in resultado["incidentes"].values())
    logger.info(f"TomTom: {total_inc} incidente(s) total em {total} trechos")

    return resultado
