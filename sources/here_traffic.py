"""
Módulo HERE Traffic Incidents API.

RESOLVE OS GAPS CRÍTICOS:
- Gap 1: Detecta TIPO de incidente (acidente, obras, interdição)
- Gap 3: Retorna LOCALIZAÇÃO específica (coordenadas, descrição)
- Gap 4: Categorias padronizadas mapeáveis para a planilha

Free tier: 250.000 requests/mês (mais que suficiente)
"""
import hashlib
import json
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import pybreaker

from sources.km_calculator import enriquecer_incidente, haversine
from sources.circuit import here_breaker

try:
    import flexpolyline as fp
except ImportError:
    fp = None

logger = logging.getLogger(__name__)


def _sanitizar_erro(erro, api_key=""):
    """Remove API key de mensagens de erro para evitar vazamento em logs."""
    msg = str(erro)
    if api_key:
        msg = msg.replace(api_key, "***")
    return msg


def _validar_json_response(resp, contexto=""):
    """Valida que a resposta e JSON e retorna o dict parseado."""
    content_type = resp.headers.get("Content-Type", "")
    if "json" not in content_type:
        logger.warning(f"{contexto} Content-Type inesperado: {content_type}")

    try:
        return resp.json()
    except ValueError:
        logger.error(f"{contexto} Resposta nao e JSON valido")
        return None


# ===== HTTP Session com retry automático =====
_thread_local = threading.local()


def _get_sessao():
    """Retorna uma requests.Session thread-local com retry automático."""
    if not hasattr(_thread_local, "sessao"):
        s = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            connect=2,
            read=2,
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _thread_local.sessao = s
    return _thread_local.sessao


# ===== Mapeamento HERE -> categorias da planilha =====
CATEGORIA_MAP_STR = {
    "accident": "Colis\u00e3o",
    "brokenDownVehicle": "Colis\u00e3o",
    "roadClosure": "Ocorr\u00eancia",
    "laneRestriction": "Bloqueio Parcial",
    "roadHazard": "Ocorr\u00eancia",
    "construction": "Obras na Pista",
    "plannedEvent": "Obras na Pista",
    "congestion": "Engarrafamento",
    "slowTraffic": "Engarrafamento",
    "massEvent": "Engarrafamento",
    "weather": "Condi\u00e7\u00e3o Clim\u00e1tica",
    "vehicleRestriction": "Bloqueio Parcial",
    "other": "Ocorr\u00eancia",
}

CATEGORIA_MAP_INT = {
    0: "Engarrafamento",
    1: "Colis\u00e3o",
    2: "Ocorr\u00eancia",
    3: "Obras na Pista",
    4: "Condi\u00e7\u00e3o Clim\u00e1tica",
    5: "Ocorr\u00eancia",
    6: "Bloqueio Parcial",
    7: "Engarrafamento",
    8: "Engarrafamento",
    9: "Ocorr\u00eancia",
    10: "Engarrafamento",
    11: "Ocorr\u00eancia",
    12: "Obras na Pista",
    13: "Ocorr\u00eancia",
    14: "Colis\u00e3o",
}

SEVERIDADE_MAP = {1: "Baixa", 2: "Média", 3: "Alta", 4: "Crítica"}
CRITICALITY_TO_ID = {"low": 1, "minor": 2, "major": 3, "critical": 4}

# Keywords para classificação por texto (fallback)
_KEYWORDS_CATEGORIA = {
    "Colis\u00e3o": [
        "acidente", "colis\u00e3o", "colisao", "capotamento",
        "engavetamento", "tombamento",
    ],
    "Obras na Pista": ["obras", "trabalhos", "manuten\u00e7\u00e3o", "manutencao"],
    "Engarrafamento": ["congestion", "lentid\u00e3o", "lentidao", "engarrafamento"],
    "Condi\u00e7\u00e3o Clim\u00e1tica": ["chuva", "alagamento", "neblina", "clima"],
}

_CAUSA_HINTS_STR = {
    "accident": "acidente",
    "brokenDownVehicle": "acidente",
    "construction": "obra",
    "plannedEvent": "obra",
    "roadHazard": "risco",
    "weather": "clima",
}

_CAUSA_HINTS_INT = {
    1: "acidente",
    3: "obra",
    4: "clima",
    12: "obra",
    14: "acidente",
}

_TIPOS_BLOQUEIO_TOTAL_STR = {"roadClosure"}
_TIPOS_BLOQUEIO_PARCIAL_STR = {"laneRestriction", "vehicleRestriction"}
_TIPOS_BLOQUEIO_PARCIAL_INT = {6}

_BLOQUEIO_TOTAL_TEXTOS = (
    "bloqueio total",
    "interdição total",
    "interdicao total",
    "via totalmente interditada",
    "via totalmente interditado",
    "totalmente interditada",
    "totalmente interditado",
    "todas as faixas bloqueadas",
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

_RISCO_TEXTOS = (
    "deslizamento",
    "queda de barreira",
    "queda de árvore",
    "queda de arvore",
    "árvore na pista",
    "arvore na pista",
    "obstáculo",
    "obstaculo",
    "hazard",
    "risco",
)


def _calcular_bbox_limites(origem_lat, origem_lng, destino_lat, destino_lng,
                           padding_km=20.0):
    pad = padding_km * 0.009
    south = max(-90.0, min(origem_lat, destino_lat) - pad)
    north = min(90.0, max(origem_lat, destino_lat) + pad)
    west = max(-180.0, min(origem_lng, destino_lng) - pad)
    east = min(180.0, max(origem_lng, destino_lng) + pad)
    return west, south, east, north


def _formatar_bbox_here(west, south, east, north):
    return f"bbox:{west:.6f},{south:.6f},{east:.6f},{north:.6f}"


def _gerar_bboxes_here(origem_lat, origem_lng, destino_lat, destino_lng,
                       padding_km=15.0, max_span_grau=0.35, max_boxes=2):
    """Gera bboxes válidos para HERE (máx 1 grau por bbox)."""
    west, south, east, north = _calcular_bbox_limites(
        origem_lat, origem_lng, destino_lat, destino_lng, padding_km=padding_km
    )

    if abs(east - west) <= max_span_grau and abs(north - south) <= max_span_grau:
        return [_formatar_bbox_here(west, south, east, north)]

    span_total = max(abs(destino_lat - origem_lat), abs(destino_lng - origem_lng))
    passo = max(0.1, max_span_grau * 0.9)
    n_boxes = min(max_boxes, max(2, int(math.ceil(span_total / passo)) + 1))
    half = max_span_grau / 2.0

    bboxes = set()
    for i in range(n_boxes):
        t = i / (n_boxes - 1) if n_boxes > 1 else 0
        lat = origem_lat + (destino_lat - origem_lat) * t
        lng = origem_lng + (destino_lng - origem_lng) * t
        bboxes.add(_formatar_bbox_here(
            max(-180.0, lng - half), max(-90.0, lat - half),
            min(180.0, lng + half), min(90.0, lat + half),
        ))
    return list(bboxes)


# ===== Polyline Corridor (HERE Routing v8 + Traffic v7 corridor) =====
EARTH_R = 6_371_000.0  # raio da Terra em metros

# Distância haversine acima da qual o Routing v8 é pulado (corredor nunca
# caberá em 1200 chars mesmo após downsampling)
ROTA_LONGA_SKIP_ROUTING_KM = 500

_route_polyline_cache = {}
_route_polyline_lock = threading.Lock()
_route_polyline_inflight = {}


def _route_cache_key(origem_coords, destino_coords, transport_mode="car"):
    """Cria chave de cache estável para polyline de rota."""
    o = (round(origem_coords[0], 6), round(origem_coords[1], 6))
    d = (round(destino_coords[0], 6), round(destino_coords[1], 6))
    return (transport_mode, o, d)


def _costurar_polylines(polylines):
    """Costura multiplas polylines em uma unica, removendo duplicatas de juncao."""
    if not polylines:
        return None
    if len(polylines) == 1:
        return polylines[0]
    if not fp:
        return polylines[0]

    pts = []
    for pl in polylines:
        dec = fp.decode(pl)
        if not dec:
            continue
        if pts and dec[0] == pts[-1]:
            dec = dec[1:]
        pts.extend(dec)

    return fp.encode(pts) if pts else polylines[0]


def _obter_polyline_rota_v8(api_key, origem_coords, destino_coords,
                            transport_mode="car", via_coords=None):
    """Obtém polyline da rota via HERE Routing v8 (Flexible Polyline Encoding).

    Returns:
        dict: {"stitched": polyline_str, "sections": [polyline_str, ...]}
        None: se falhou
    """
    params = {
        "transportMode": transport_mode,
        "origin": f"{origem_coords[0]},{origem_coords[1]}",
        "destination": f"{destino_coords[0]},{destino_coords[1]}",
        "return": "polyline",
        "apikey": api_key,
    }
    if via_coords:
        params["via"] = [
            f"{lat},{lng}!passThrough=true" for lat, lng in via_coords
        ]
    timeout = 25 if via_coords else 15
    resp = _get_sessao().get(
        "https://router.hereapi.com/v8/routes", params=params, timeout=timeout,
    )
    resp.raise_for_status()
    data = _validar_json_response(resp, contexto="[Routing v8]") or {}

    routes = data.get("routes") or []
    if not routes:
        return None
    sections = routes[0].get("sections") or []
    polylines = [s.get("polyline") for s in sections if s.get("polyline")]

    if not polylines:
        return None

    stitched = _costurar_polylines(polylines)
    return {"stitched": stitched, "sections": polylines}


def _montar_in_corridor(polyline, radius_m):
    """Formata parâmetro 'in' para corridor da Traffic API v7."""
    return f"corridor:{polyline};r={int(radius_m)}"


def _dist_ponto_segmento_m(p, a, b):
    """Distância ponto→segmento usando projeção equiretangular local (metros)."""
    (lat, lon) = p
    (lat1, lon1) = a
    (lat2, lon2) = b

    latr = math.radians(lat)
    x = math.radians(lon) * math.cos(latr) * EARTH_R
    y = math.radians(lat) * EARTH_R

    x1 = math.radians(lon1) * math.cos(math.radians(lat1)) * EARTH_R
    y1 = math.radians(lat1) * EARTH_R
    x2 = math.radians(lon2) * math.cos(math.radians(lat2)) * EARTH_R
    y2 = math.radians(lat2) * EARTH_R

    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)

    t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    projx = x1 + t * dx
    projy = y1 + t * dy
    return math.hypot(x - projx, y - projy)


def _dist_ponto_polyline_m(p, poly_pts):
    """Menor distância (metros) de um ponto a uma polyline."""
    if not poly_pts or len(poly_pts) < 2:
        return float("inf")
    best = float("inf")
    for i in range(len(poly_pts) - 1):
        d = _dist_ponto_segmento_m(p, poly_pts[i], poly_pts[i + 1])
        if d < best:
            best = d
    return best


_HERE_MAX_CORRIDOR_POINTS = 300


def _rdp_simplify(pontos, epsilon_m):
    """Simplificacao Ramer-Douglas-Peucker iterativa (evita recursion limit).

    Preserva pontos geometricamente significativos (curvas, trevos)
    enquanto descarta pontos em trechos retos — superior ao keep-every-N
    que pode descartar pontos de inflexao criticos.

    Args:
        pontos: lista de tuplas (lat, lng) em 2D
        epsilon_m: tolerancia em metros — pontos com distancia menor
                   ao segmento simplificado sao descartados

    Returns:
        lista simplificada de tuplas (lat, lng)
    """
    n = len(pontos)
    if n <= 2:
        return list(pontos)

    keep = [False] * n
    keep[0] = True
    keep[n - 1] = True
    stack = [(0, n - 1)]

    while stack:
        start, end = stack.pop()
        if end - start <= 1:
            continue

        max_dist = 0.0
        max_idx = start
        for i in range(start + 1, end):
            d = _dist_ponto_segmento_m(pontos[i], pontos[start], pontos[end])
            if d > max_dist:
                max_dist = d
                max_idx = i

        if max_dist > epsilon_m:
            keep[max_idx] = True
            stack.append((start, max_idx))
            stack.append((max_idx, end))

    return [pontos[i] for i in range(n) if keep[i]]


def _downsample_polyline(pontos, corridor_radius_m, trecho_nome, max_chars=1200):
    """Reduz pontos de uma polyline até caber em max_chars E ≤ 300 pontos.

    HERE Traffic API v7 impõe limite de 300 pontos por corridor.
    Estratégia: Ramer-Douglas-Peucker com epsilon crescente.
    Preserva geometria (curvas e inflexões) em vez de stride fixo.
    Usa apenas (lat, lng) 2D para compatibilidade com HERE Traffic API.
    Retorna (polyline_str, pontos_2d_mantidos) ou (None, None) se impossível.
    """
    if not fp or not pontos or len(pontos) < 2:
        return None, None

    # Normalizar para 2D (lat, lng) — remove altitude se presente
    pontos_2d = [(p[0], p[1]) for p in pontos]

    # Se ja cabe sem simplificar, retorna direto
    if len(pontos_2d) <= _HERE_MAX_CORRIDOR_POINTS:
        try:
            poly_str = fp.encode(pontos_2d)
            candidato = _montar_in_corridor(poly_str, corridor_radius_m)
            if len(candidato) <= max_chars:
                return poly_str, pontos_2d
        except Exception:
            pass

    # RDP com epsilon crescente ate caber nos limites
    epsilon_m = 50  # tolerancia inicial em metros
    while epsilon_m <= 50_000:
        simplified = _rdp_simplify(pontos_2d, epsilon_m)

        if len(simplified) <= _HERE_MAX_CORRIDOR_POINTS:
            try:
                poly_str = fp.encode(simplified)
            except Exception:
                return None, None

            candidato = _montar_in_corridor(poly_str, corridor_radius_m)
            if len(candidato) <= max_chars:
                logger.info(
                    f"[{trecho_nome}] Polyline RDP: "
                    f"{len(pontos)} -> {len(simplified)} pontos "
                    f"(epsilon={epsilon_m}m), {len(candidato)} chars"
                )
                return poly_str, simplified

        if len(simplified) <= 2:
            logger.warning(
                f"[{trecho_nome}] RDP com 2 pontos ainda excede limite "
                f"({len(_montar_in_corridor(fp.encode(simplified), corridor_radius_m))} chars). "
                f"Usando bbox."
            )
            return None, None

        epsilon_m = int(epsilon_m * 1.5)

    logger.warning(
        f"[{trecho_nome}] RDP nao conseguiu simplificar polyline "
        f"({len(pontos)} pontos). Usando bbox."
    )
    return None, None


def _obter_routing_cache(api_key, origem_coords, destino_coords,
                         trecho_nome, via_coords=None):
    """Obtem resultado do Routing v8 (com cache thread-safe).

    Returns:
        dict: {"stitched": polyline_str, "sections": [str, ...]} ou None
    """
    cache_key = _route_cache_key(origem_coords, destino_coords, "car")
    dono_da_busca = False
    with _route_polyline_lock:
        cached = _route_polyline_cache.get(cache_key)
        inflight = None
        if cached is None:
            inflight = _route_polyline_inflight.get(cache_key)
            if inflight is None:
                inflight = threading.Event()
                _route_polyline_inflight[cache_key] = inflight
                dono_da_busca = True

    if cached is None:
        if dono_da_busca:
            resultado_busca = None
            try:
                resultado_busca = _obter_polyline_rota_v8(
                    api_key, origem_coords, destino_coords, "car",
                    via_coords=via_coords,
                )
            except Exception as e:
                logger.warning(f"[{trecho_nome}] Routing v8 falhou (vai de bbox): "
                               f"{_sanitizar_erro(e, api_key)}")
            finally:
                with _route_polyline_lock:
                    if resultado_busca:
                        _route_polyline_cache[cache_key] = resultado_busca
                    waiter = _route_polyline_inflight.pop(cache_key, None)
                    if waiter is not None:
                        waiter.set()
                    cached = _route_polyline_cache.get(cache_key)
        else:
            inflight.wait()
            with _route_polyline_lock:
                cached = _route_polyline_cache.get(cache_key)

    return cached


def _obter_corridor_ou_none(api_key, origem_coords, destino_coords,
                            trecho_nome, corridor_radius_m):
    """Tenta obter polyline e montar corridor para rotas curtas.

    Retorna (in_corridor, route_pts) ou (None, None).
    Rotas longas (>500km haversine) sao tratadas por _obter_corridor_strategy.
    """
    if not fp:
        return None, None

    # Rotas longas sao tratadas pelo caminho segmentado
    dist_km = haversine(
        origem_coords[0], origem_coords[1],
        destino_coords[0], destino_coords[1],
    )
    if dist_km > ROTA_LONGA_SKIP_ROUTING_KM:
        return None, None

    cached = _obter_routing_cache(
        api_key, origem_coords, destino_coords, trecho_nome,
    )
    if not cached:
        return None, None

    route_polyline = cached["stitched"]

    # Decodifica pontos para uso em filtragem e possivel downsampling
    route_pts = None
    try:
        route_pts = fp.decode(route_polyline)
    except Exception:
        pass

    in_corridor = _montar_in_corridor(route_polyline, corridor_radius_m)

    # HERE Traffic API v7 rejeita corridors muito longos (HTTP 400).
    max_corridor_chars = 1200

    if len(in_corridor) > max_corridor_chars:
        logger.info(
            f"[{trecho_nome}] Corridor URI longo ({len(in_corridor)} chars), "
            f"tentando downsample da polyline..."
        )
        route_pts_original = route_pts
        poly_reduzida, route_pts = _downsample_polyline(
            route_pts, corridor_radius_m, trecho_nome, max_chars=max_corridor_chars
        )
        if poly_reduzida is None:
            return None, route_pts_original
        in_corridor = _montar_in_corridor(poly_reduzida, corridor_radius_m)

    return in_corridor, route_pts


def _splittar_polyline_nos_waypoints(full_pts, origem_coords, via_coords, destino_coords):
    """Divide uma polyline nos pontos mais proximos dos via waypoints.

    passThrough=true faz o Routing v8 retornar 1 unica secao. Esta funcao
    divide a polyline resultante em N+1 segmentos nos waypoints intermediarios.

    Args:
        full_pts: lista de tuplas (lat, lng, [alt]) da polyline completa
        origem_coords: (lat, lng) da origem
        via_coords: lista de (lat, lng) dos waypoints intermediarios
        destino_coords: (lat, lng) do destino

    Returns:
        lista de listas de tuplas 2D [(lat, lng), ...] — um por segmento
    """
    if not full_pts or not via_coords:
        return [[(p[0], p[1]) for p in full_pts]] if full_pts else []

    # Converte para 2D
    pts_2d = [(p[0], p[1]) for p in full_pts]

    # Encontra o indice do ponto mais proximo para cada via waypoint
    split_indices = []
    search_start = 0
    for wp_lat, wp_lng in via_coords:
        best_idx = search_start
        best_dist = float("inf")
        # Busca a partir do ultimo split para manter ordem
        for j in range(search_start, len(pts_2d)):
            d = haversine(wp_lat, wp_lng, pts_2d[j][0], pts_2d[j][1])
            if d < best_dist:
                best_dist = d
                best_idx = j
        split_indices.append(best_idx)
        search_start = best_idx + 1

    # Divide a polyline nos indices encontrados
    segments = []
    prev = 0
    for idx in split_indices:
        # Cada segmento inclui o ponto de split (sobreposicao intencional)
        end = min(idx + 1, len(pts_2d))
        if end > prev:
            segments.append(pts_2d[prev:end])
        prev = idx

    # Ultimo segmento: do ultimo split ate o fim
    if prev < len(pts_2d):
        segments.append(pts_2d[prev:])

    return segments


def _obter_corridors_segmentados(api_key, origem_coords, destino_coords,
                                  trecho_nome, corridor_radius_m, via_coords):
    """Obtem corridors segmentados para rotas longas usando via waypoints.

    Faz 1 chamada Routing v8 com via waypoints, recebe a polyline completa,
    e divide nos waypoints para montar corridor independente por segmento.

    Returns:
        (corridors_list, full_route_pts)
        corridors_list: [(in_corridor_or_none, section_pts), ...]
        full_route_pts: pontos decodificados da rota inteira (2D)
    """
    cached = _obter_routing_cache(
        api_key, origem_coords, destino_coords, trecho_nome,
        via_coords=via_coords,
    )
    if not cached:
        return [], None

    stitched = cached.get("stitched")
    section_polylines = cached.get("sections", [])

    # Decodifica rota completa
    full_pts_raw = None
    if stitched:
        try:
            full_pts_raw = fp.decode(stitched)
        except Exception:
            pass
    if not full_pts_raw:
        return [], None

    full_route_pts = [(p[0], p[1]) for p in full_pts_raw]

    # Se API retornou multiplas secoes, usa-las diretamente
    if len(section_polylines) > 1:
        segments_pts = []
        for sp in section_polylines:
            try:
                decoded = fp.decode(sp)
                segments_pts.append([(p[0], p[1]) for p in decoded])
            except Exception:
                segments_pts.append(None)
    else:
        # passThrough=true retorna 1 secao — splittar nos waypoints
        segments_pts = _splittar_polyline_nos_waypoints(
            full_pts_raw, origem_coords, via_coords, destino_coords,
        )

    max_corridor_chars = 1200
    corridors = []

    for i, seg_pts in enumerate(segments_pts):
        if seg_pts is None or len(seg_pts) < 2:
            corridors.append((None, None))
            continue

        # Encode segmento
        try:
            seg_poly = fp.encode(seg_pts)
        except Exception:
            corridors.append((None, seg_pts))
            continue

        in_corridor = _montar_in_corridor(seg_poly, corridor_radius_m)

        if len(in_corridor) > max_corridor_chars:
            logger.info(
                f"[{trecho_nome}] Secao {i+1}/{len(segments_pts)} corridor "
                f"longo ({len(in_corridor)} chars), tentando downsample..."
            )
            poly_reduzida, pts_reduzidos = _downsample_polyline(
                seg_pts, corridor_radius_m,
                f"{trecho_nome}:seg{i+1}", max_chars=max_corridor_chars,
            )
            if poly_reduzida is None:
                corridors.append((None, seg_pts))
                continue
            in_corridor = _montar_in_corridor(poly_reduzida, corridor_radius_m)
            seg_pts = pts_reduzidos if pts_reduzidos else seg_pts

        corridors.append((in_corridor, seg_pts))

    n_ok = sum(1 for c, _ in corridors if c is not None)
    logger.info(
        f"[{trecho_nome}] Corridor segmentado: {n_ok}/{len(segments_pts)} secoes "
        f"com corridor (via {len(via_coords)} waypoints)"
    )
    return corridors, full_route_pts


# HERE Traffic v7 rejeita corridors com comprimento > 500km (E608055)
_MAX_CORRIDOR_DISTANCE_KM = 450  # margem de seguranca


def _obter_corridor_strategy(api_key, origem_coords, destino_coords,
                              trecho_nome, corridor_radius_m, via_coords=None):
    """Determina estrategia de corridor: single, segmented, ou bbox-only.

    Logica:
    - Rotas curtas (<450km rodoviaria estimada): corridor unico
    - Rotas com via_coords: corridor segmentado (divide nos waypoints)
    - Sem via_coords e rota longa: bbox fallback

    Returns:
        dict: {"mode": str, "corridors": [(in_corridor, pts), ...], "route_pts": list}
    """
    if not fp:
        return {"mode": "bbox", "corridors": [], "route_pts": None}

    dist_km = haversine(
        origem_coords[0], origem_coords[1],
        destino_coords[0], destino_coords[1],
    )

    # Rotas curtas sem waypoints: corridor unico (caminho existente)
    if dist_km <= _MAX_CORRIDOR_DISTANCE_KM and not via_coords:
        in_corridor, route_pts = _obter_corridor_ou_none(
            api_key, origem_coords, destino_coords, trecho_nome, corridor_radius_m,
        )
        if in_corridor:
            return {"mode": "single", "corridors": [(in_corridor, route_pts)],
                    "route_pts": route_pts}
        return {"mode": "bbox", "corridors": [], "route_pts": route_pts}

    # Rotas com waypoints: sempre usar segmentacao
    # (garante que cada segmento < 500km, evita E608055)
    if via_coords:
        corridors, full_pts = _obter_corridors_segmentados(
            api_key, origem_coords, destino_coords, trecho_nome,
            corridor_radius_m, via_coords,
        )
        if corridors and any(c is not None for c, _ in corridors):
            return {"mode": "segmented", "corridors": corridors,
                    "route_pts": full_pts}
        logger.warning(
            f"[{trecho_nome}] Corridor segmentado falhou completamente, "
            f"usando bbox."
        )
        return {"mode": "bbox", "corridors": [], "route_pts": full_pts}

    # Rota sem waypoints: tentar corridor unico se nao muito longa
    if dist_km <= ROTA_LONGA_SKIP_ROUTING_KM:
        in_corridor, route_pts = _obter_corridor_ou_none(
            api_key, origem_coords, destino_coords, trecho_nome, corridor_radius_m,
        )
        if in_corridor:
            return {"mode": "single", "corridors": [(in_corridor, route_pts)],
                    "route_pts": route_pts}
        return {"mode": "bbox", "corridors": [], "route_pts": route_pts}

    logger.info(
        f"[{trecho_nome}] Rota longa ({dist_km:.0f}km) sem via waypoints, "
        f"usando bbox."
    )
    return {"mode": "bbox", "corridors": [], "route_pts": None}


def _geocode_endereco(api_key, endereco):
    """Geocodifica um endereço usando HERE Geocoding API."""
    try:
        consulta = endereco if "brasil" in endereco.lower() else f"{endereco}, Brasil"
        resp = _get_sessao().get(
            "https://geocode.search.hereapi.com/v1/geocode",
            params={"q": consulta, "apiKey": api_key, "limit": 1,
                    "lang": "pt-BR", "in": "countryCode:BRA"},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            pos = items[0]["position"]
            return (pos["lat"], pos["lng"])
    except Exception as e:
        logger.warning(f"Geocoding falhou para '{endereco}': {_sanitizar_erro(e, api_key)}")
    return None


_cache_lock = threading.Lock()


def _geocode_com_cache(api_key, endereco, cache):
    """Geocodifica com cache thread-safe."""
    with _cache_lock:
        if endereco in cache:
            return cache[endereco]
    resultado = _geocode_endereco(api_key, endereco)
    with _cache_lock:
        cache[endereco] = resultado
    return resultado


def _parse_ou_geocode(api_key, endereco, cache):
    """Tenta parsear como coordenadas, senão geocodifica."""
    try:
        parts = endereco.split(",")
        if len(parts) == 2:
            lat, lng = float(parts[0].strip()), float(parts[1].strip())
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return (lat, lng)
    except (ValueError, IndexError):
        pass
    return _geocode_com_cache(api_key, endereco, cache)


def _extrair_texto_here(campo):
    """Extrai texto de campo HERE (pode ser dict com 'value' ou string)."""
    if isinstance(campo, dict):
        return campo.get("value", "")
    return str(campo) if campo else ""


def _texto_contem_qualquer(texto, termos):
    return any(termo in texto for termo in termos)


def _detectar_causa_textual(texto):
    texto_lower = (texto or "").lower()
    if not texto_lower:
        return "indefinida"
    if _texto_contem_qualquer(texto_lower, _KEYWORDS_CATEGORIA["Colis\u00e3o"]):
        return "acidente"
    if _texto_contem_qualquer(texto_lower, _KEYWORDS_CATEGORIA["Obras na Pista"]):
        return "obra"
    if _texto_contem_qualquer(texto_lower, _KEYWORDS_CATEGORIA["Condi\u00e7\u00e3o Clim\u00e1tica"]):
        return "clima"
    if _texto_contem_qualquer(texto_lower, _RISCO_TEXTOS):
        return "risco"
    return "indefinida"


def _detectar_bloqueio_escopo_here(tipo, road_closed, texto):
    texto_lower = (texto or "").lower()

    if road_closed:
        return "total"

    if isinstance(tipo, str) and tipo in _TIPOS_BLOQUEIO_TOTAL_STR:
        return "total"

    if _texto_contem_qualquer(texto_lower, _BLOQUEIO_TOTAL_TEXTOS):
        return "total"

    if isinstance(tipo, str) and tipo in _TIPOS_BLOQUEIO_PARCIAL_STR:
        return "parcial"

    if isinstance(tipo, int) and tipo in _TIPOS_BLOQUEIO_PARCIAL_INT:
        return "parcial"

    if _texto_contem_qualquer(texto_lower, _BLOQUEIO_PARCIAL_TEXTOS):
        return "parcial"

    return "nenhum"


def _detectar_causa_here(tipo, texto):
    if isinstance(tipo, str) and tipo in _CAUSA_HINTS_STR:
        return _CAUSA_HINTS_STR[tipo]
    if isinstance(tipo, int) and tipo in _CAUSA_HINTS_INT:
        return _CAUSA_HINTS_INT[tipo]
    return _detectar_causa_textual(texto)


def _classificar_categoria_here(tipo, road_closed, texto):
    """Converte o tipo HERE para categoria de negócio."""
    bloqueio_escopo = _detectar_bloqueio_escopo_here(tipo, road_closed, texto)
    causa_detectada = _detectar_causa_here(tipo, texto)

    if bloqueio_escopo == "total":
        return "Interdi\u00e7\u00e3o", bloqueio_escopo, causa_detectada

    if causa_detectada == "acidente":
        return "Colis\u00e3o", bloqueio_escopo, causa_detectada

    if bloqueio_escopo == "parcial":
        return "Bloqueio Parcial", bloqueio_escopo, causa_detectada

    if isinstance(tipo, str) and tipo:
        cat = CATEGORIA_MAP_STR.get(tipo)
        if cat:
            return cat, bloqueio_escopo, causa_detectada
    elif isinstance(tipo, int):
        cat = CATEGORIA_MAP_INT.get(tipo)
        if cat:
            return cat, bloqueio_escopo, causa_detectada

    texto_lower = (texto or "").lower()
    for categoria, keywords in _KEYWORDS_CATEGORIA.items():
        if any(k in texto_lower for k in keywords):
            return categoria, bloqueio_escopo, causa_detectada
    return "Ocorr\u00eancia", bloqueio_escopo, causa_detectada


def _severidade_here(severidade_id_raw, criticality_raw):
    if isinstance(severidade_id_raw, int) and severidade_id_raw in SEVERIDADE_MAP:
        sev_id = severidade_id_raw
    else:
        crit_lower = (criticality_raw or "").lower()
        sev_id = CRITICALITY_TO_ID.get(crit_lower)
        if sev_id is None:
            sev_id = 2
            logger.debug(
                f"Severidade fallback para 2 (Media): "
                f"severity_raw={severidade_id_raw}, criticality_raw={criticality_raw}"
            )
    return sev_id, SEVERIDADE_MAP.get(sev_id, "Média")


def _parse_incidente(item, trecho_nome):
    """Parseia um incidente retornado pela HERE API."""
    try:
        inc = item.get("incidentDetails", {})
        location = item.get("location", {})

        desc_text = _extrair_texto_here(inc.get("summary", {}))
        desc_extra = _extrair_texto_here(inc.get("description", {}))
        type_desc = _extrair_texto_here(inc.get("typeDescription", {}))

        road_info = inc.get("roadInfo", {})
        road_name = (road_info.get("name", "") or road_info.get("id", "")) if road_info else ""

        # Monta descrição sem duplicatas
        partes = [f"[{road_name}]"] if road_name else []
        vistos = set()
        for texto in (desc_text, desc_extra, type_desc):
            if texto and texto not in vistos:
                partes.append(texto)
                vistos.add(texto)

        texto_unificado = " | ".join(partes)
        road_closed = bool(inc.get("roadClosed", False))
        tipo_raw = inc.get("type", "")
        categoria, bloqueio_escopo, causa_detectada = _classificar_categoria_here(
            tipo_raw, road_closed, texto_unificado,
        )
        severidade_id, severidade = _severidade_here(
            inc.get("severity"), inc.get("criticality", ""),
        )

        # Localização — centróide de todos os pontos da geometria
        shape = location.get("shape", {}) or {}
        links = shape.get("links", []) or []
        all_pts = []
        for lk in links:
            all_pts.extend(lk.get("points", []) or [])

        lat = lng = None
        if all_pts:
            lat = sum(p.get("lat", 0) for p in all_pts) / len(all_pts)
            lng = sum(p.get("lng", 0) for p in all_pts) / len(all_pts)

        return {
            "trecho": trecho_nome,
            "categoria": categoria,
            "categoria_here_id": tipo_raw,
            "severidade": severidade,
            "severidade_id": severidade_id,
            "descricao": texto_unificado or "Sem descrição",
            "rodovia_afetada": road_name,
            "tipo_here": str(tipo_raw),
            "criticality_here": str(inc.get("criticality", "")),
            "road_closed": road_closed,
            "bloqueio_escopo": bloqueio_escopo,
            "causa_detectada": causa_detectada,
            "latitude": lat,
            "longitude": lng,
            "inicio": inc.get("startTime", ""),
            "fim": inc.get("endTime", ""),
            "fonte": "HERE Traffic",
            "consultado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "shape_points_count": len(all_pts),
        }

    except Exception as e:
        logger.warning(f"Erro ao parsear incidente HERE: {e}")
        return None


def _normalizar_texto(valor):
    return "".join(ch for ch in (valor or "").upper() if ch.isalnum())


_RODOVIA_PATTERN = re.compile(
    r"(BR|SP|RJ|MG|PR|SC|RS|BA|PE|CE|GO|MT|MS|ES|PA|AM|MA|PI|RN|PB|AL|SE|TO|RO|AC|AP|RR|DF)\s*-?\s*(\d{2,3})",
    re.IGNORECASE,
)


def _extrair_codigo_rodovia(texto):
    """Extrai código de rodovia normalizado (ex: 'BR116', 'SP330')."""
    match = _RODOVIA_PATTERN.search(texto)
    if match:
        return f"{match.group(1).upper()}{match.group(2)}"
    return None


def _extrair_todos_codigos_rodovia(texto):
    """Extrai TODOS os codigos de rodovia do texto (ex: 'BR116', 'BR101')."""
    matches = _RODOVIA_PATTERN.findall(texto)
    return {f"{uf.upper()}{num}" for uf, num in matches}


def _incidente_relevante_para_rodovia(incidente, rodovia_filtro, modo="bbox"):
    """Filtra incidentes para manter apenas os da rodovia do trecho.

    Suporta multi-rodovia: "BR-116 / BR-101" aceita incidentes em ambas.

    Em modo corridor/corridor_segmentado, a API HERE ja filtrou geograficamente
    (somente incidentes dentro do raio do corridor sao retornados). Portanto:
    - Se o incidente NAO tem codigo de rodovia no texto → aceitar (confiar na geometria)
    - Se o incidente TEM codigo de rodovia → verificar se bate com o filtro
    Em modo bbox (area retangular), o filtro de nome e mais rigoroso para evitar
    rodovias paralelas na mesma regiao.
    """
    if not rodovia_filtro:
        return True

    texto = f"{incidente.get('rodovia_afetada', '')} {incidente.get('descricao', '')}"

    # Extrair TODOS os codigos do filtro (ex: "BR-116 / BR-101" -> {"BR116", "BR101"})
    filtro_codigos = _extrair_todos_codigos_rodovia(rodovia_filtro)
    texto_codigos = _extrair_todos_codigos_rodovia(texto)

    if filtro_codigos:
        # Se o incidente tem codigos de rodovia → verificar intersecao
        if texto_codigos:
            if filtro_codigos & texto_codigos:
                return True
            # Codigos presentes mas nenhum bate — rejeitar em qualquer modo
            return False

        # Incidente SEM codigo de rodovia no texto:
        # - Em corridor: confiar na geometria (API ja filtrou)
        # - Em bbox: rejeitar (poderia ser rodovia paralela)
        if modo in ("corridor", "corridor_segmentado"):
            return True

        # Bbox: ultimo recurso — texto normalizado generico
        texto_norm = _normalizar_texto(texto)
        filtro_norm = _normalizar_texto(rodovia_filtro)
        return filtro_norm in texto_norm if filtro_norm else True

    # Filtro sem codigos reconheciveis: texto normalizado generico
    texto_norm = _normalizar_texto(texto)
    filtro_norm = _normalizar_texto(rodovia_filtro)
    if not filtro_norm:
        return True
    return filtro_norm in texto_norm


def _construir_polyline_referencia(segmentos):
    """Constroi polyline aproximada a partir dos pontos de referencia dos segmentos.

    Usada como fallback para filtragem de distancia quando route_pts nao esta
    disponivel (rotas >500km ou Routing v8 falhou).
    """
    if not segmentos:
        return None
    pontos = []
    for seg in segmentos:
        for pr in seg.get("pontos_referencia", []):
            try:
                pontos.append((float(pr["lat"]), float(pr["lng"])))
            except (KeyError, TypeError, ValueError):
                continue
    return pontos if len(pontos) >= 2 else None


def _consultar_incidents_via_corridor(api_key, in_corridor, trecho_nome):
    """Faz uma consulta de incidentes usando um corridor especifico."""
    resp = _get_sessao().get(
        "https://data.traffic.hereapi.com/v7/incidents",
        params={
            "apiKey": api_key,
            "in": in_corridor,
            "locationReferencing": "shape",
            "lang": "pt-BR",
        },
        timeout=12,
    )
    resp.raise_for_status()
    data = _validar_json_response(
        resp, contexto=f"[{trecho_nome}] HERE Incidents(corridor)",
    )
    return data.get("results", []) if data else []


@here_breaker
def consultar_incidentes(api_key, origem, destino, trecho_nome="",
                         geocode_cache=None, rodovia_filtro="", segmentos=None,
                         corridor_radius_m=100, corridor_margin_m=50,
                         bbox_filter_radius_m=500, via_coords=None):
    """Consulta incidentes de trafego na HERE Traffic API.

    Estrategia: corridor (rota real) primeiro, bbox como fallback.
    Para rotas longas (>500km) com via_coords, usa corridor segmentado.
    Filtragem de distancia aplicada em ambos os modos (corridor: 150m, bbox: 500m).
    """
    if geocode_cache is None:
        geocode_cache = {}

    incidentes = []

    try:
        origem_coords = _parse_ou_geocode(api_key, origem, geocode_cache)
        destino_coords = _parse_ou_geocode(api_key, destino, geocode_cache)

        if not origem_coords or not destino_coords:
            logger.warning(f"[{trecho_nome}] Nao foi possivel geocodificar origem/destino")
            return incidentes

        resultados_brutos = []
        metodo = "bbox"

        # Determina estrategia de corridor
        strategy = _obter_corridor_strategy(
            api_key, origem_coords, destino_coords, trecho_nome,
            corridor_radius_m, via_coords=via_coords,
        )
        route_pts = strategy["route_pts"]

        # 1) Tentar corridors (single ou segmented)
        if strategy["mode"] in ("single", "segmented"):
            for in_corridor, _section_pts in strategy["corridors"]:
                if in_corridor is None:
                    continue
                try:
                    results = _consultar_incidents_via_corridor(
                        api_key, in_corridor, trecho_nome,
                    )
                    resultados_brutos.extend(results)
                except requests.exceptions.RequestException as e:
                    logger.warning(
                        f"[{trecho_nome}] Corridor incidents falhou para segmento: "
                        f"{_sanitizar_erro(e, api_key)}"
                    )
            if resultados_brutos:
                metodo = "corridor" if strategy["mode"] == "single" else "corridor_segmentado"

        # 2) Fallback para bbox se corridor falhou ou veio vazio
        if not resultados_brutos:
            # Para rotas longas com via_coords, gerar bboxes ao longo dos waypoints
            # (nao apenas origem+destino que ignora o meio da rota)
            if via_coords:
                todos_pts = [origem_coords] + list(via_coords) + [destino_coords]
                bboxes_set = set()
                for pt in todos_pts:
                    pt_lat, pt_lng = (pt[0], pt[1]) if hasattr(pt[0], '__float__') else (pt[0], pt[1])
                    bxs = _gerar_bboxes_here(pt_lat, pt_lng, pt_lat, pt_lng, padding_km=40.0)
                    bboxes_set.update(bxs)
                bboxes = list(bboxes_set)
            else:
                bboxes = _gerar_bboxes_here(
                    origem_coords[0], origem_coords[1],
                    destino_coords[0], destino_coords[1],
                )
            for bbox in bboxes:
                try:
                    resp = _get_sessao().get(
                        "https://data.traffic.hereapi.com/v7/incidents",
                        params={"apiKey": api_key, "in": bbox,
                                "locationReferencing": "shape", "lang": "pt-BR"},
                        timeout=12,
                    )
                    resp.raise_for_status()
                    data = _validar_json_response(
                        resp, contexto=f"[{trecho_nome}] HERE Incidents(bbox)",
                    )
                    if data is not None:
                        resultados_brutos.extend(data.get("results", []))
                except Exception as bbox_err:
                    logger.debug(f"[{trecho_nome}] Bbox {bbox} falhou: {bbox_err}")

        # Fallback: polyline de referencia para filtragem bbox
        if not route_pts and segmentos:
            route_pts = _construir_polyline_referencia(segmentos)

        # Deduplicacao por ID
        unicos = {}
        for item in resultados_brutos:
            item_id = item.get("id")
            if not item_id:
                payload = json.dumps(
                    {"d": item.get("incidentDetails", {}),
                     "l": item.get("location", {})},
                    sort_keys=True, default=str,
                )
                item_id = hashlib.md5(payload.encode()).hexdigest()
            unicos[item_id] = item

        for item in unicos.values():
            incidente = _parse_incidente(item, trecho_nome)
            if not incidente:
                continue

            # Pos-filtro por distancia a rota PRIMEIRO (corridor: 150m, bbox: 500m)
            # O filtro geografico e o principal; o filtro de rodovia e secundario.
            inc_lat = incidente.get("latitude")
            inc_lng = incidente.get("longitude")
            dist_ok_geografico = False
            if route_pts and inc_lat is not None and inc_lng is not None:
                p = (inc_lat, inc_lng)
                dist = _dist_ponto_polyline_m(p, route_pts)
                incidente["distancia_rota_m"] = round(dist, 1)
                threshold = (corridor_radius_m + corridor_margin_m) if metodo != "bbox" else bbox_filter_radius_m
                if dist > threshold:
                    continue
                dist_ok_geografico = True

            # Filtro de rodovia:
            # - Em bbox mode COM confirmacao geografica: aceitar incidentes sem codigo
            # - Em corridor/segmentado: ja aceita incidentes sem codigo (confiar na API)
            # - Sem confirmacao geografica: aplicar filtro rigoroso
            modo_efetivo = metodo
            if dist_ok_geografico and metodo == "bbox":
                # Passou filtro geografico em bbox: tratar como corridor para o filtro de rodovia
                modo_efetivo = "corridor"
            if not _incidente_relevante_para_rodovia(incidente, rodovia_filtro, modo=modo_efetivo):
                continue

            if segmentos:
                enriquecer_incidente(incidente, segmentos)
            incidentes.append(incidente)

        logger.info(
            f"[{trecho_nome}] HERE bruto={len(unicos)} | filtrado={len(incidentes)} "
            f"incidente(s) via {metodo}"
        )

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            logger.warning(f"[{trecho_nome}] HERE quota atingida (429). Circuit breaker vai abrir.")
        logger.error(f"[{trecho_nome}] Erro HERE API: {_sanitizar_erro(e, api_key)}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"[{trecho_nome}] Erro HERE API: {_sanitizar_erro(e, api_key)}")
    except Exception as e:
        logger.error(f"[{trecho_nome}] Erro ao processar HERE: {_sanitizar_erro(e, api_key)}")

    return incidentes


@here_breaker
def consultar_fluxo_trafego(api_key, origem, destino, trecho_nome="",
                            geocode_cache=None, via_coords=None):
    """Consulta fluxo de trafego (velocidade atual vs livre) via HERE Flow API."""
    if geocode_cache is None:
        geocode_cache = {}

    resultado = {
        "trecho": trecho_nome,
        "status": "Sem dados",
        "jam_factor": 0,
        "velocidade_atual_kmh": 0,
        "velocidade_livre_kmh": 0,
        "fonte": "HERE Flow",
        "consultado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        origem_coords = _parse_ou_geocode(api_key, origem, geocode_cache)
        destino_coords = _parse_ou_geocode(api_key, destino, geocode_cache)

        if not origem_coords or not destino_coords:
            return resultado

        results = []

        # Determina estrategia de corridor (reusa cache do consultar_incidentes)
        strategy = _obter_corridor_strategy(
            api_key, origem_coords, destino_coords, trecho_nome, 100,
            via_coords=via_coords,
        )

        # 1) Tentar corridors (single ou segmented)
        if strategy["mode"] in ("single", "segmented"):
            for in_corridor, _section_pts in strategy["corridors"]:
                if in_corridor is None:
                    continue
                try:
                    resp = _get_sessao().get(
                        "https://data.traffic.hereapi.com/v7/flow",
                        params={"apiKey": api_key, "in": in_corridor,
                                "locationReferencing": "shape"},
                        timeout=12,
                    )
                    resp.raise_for_status()
                    data = _validar_json_response(
                        resp, contexto=f"[{trecho_nome}] HERE Flow(corridor)",
                    )
                    if data is not None:
                        results.extend(data.get("results", []))
                except requests.exceptions.RequestException as e:
                    logger.warning(
                        f"[{trecho_nome}] Corridor flow falhou para segmento: "
                        f"{_sanitizar_erro(e, api_key)}"
                    )

        # 2) Fallback para bbox
        if not results:
            bboxes = _gerar_bboxes_here(
                origem_coords[0], origem_coords[1],
                destino_coords[0], destino_coords[1],
                padding_km=10.0,
            )
            for bbox in bboxes:
                resp = _get_sessao().get(
                    "https://data.traffic.hereapi.com/v7/flow",
                    params={"apiKey": api_key, "in": bbox,
                            "locationReferencing": "shape"},
                    timeout=12,
                )
                resp.raise_for_status()
                data = _validar_json_response(
                    resp, contexto=f"[{trecho_nome}] HERE Flow(bbox)",
                )
                if data is not None:
                    results.extend(data.get("results", []))

        if not results:
            return resultado

        total_speed = total_free = total_jam = count = 0
        jam_por_segmento = []
        for r in results:
            cf = r.get("currentFlow", {})
            speed = cf.get("speed", 0)
            free_flow = cf.get("freeFlow", 0)
            if speed > 0 and free_flow > 0:
                total_speed += speed
                total_free += free_flow
                jf = cf.get("jamFactor", 0)
                total_jam += jf
                jam_por_segmento.append(jf)
                count += 1

        if count > 0:
            # HERE Flow v7 retorna velocidade em m/s — converter para km/h
            avg_speed = round((total_speed / count) * 3.6, 1)
            avg_free = round((total_free / count) * 3.6, 1)
            avg_jam = max(0.0, min(10.0, round(total_jam / count, 1)))

            if avg_speed > 250 or avg_free > 250:
                logger.warning(
                    f"[{trecho_nome}] Velocidade suspeita apos conversao m/s->km/h: "
                    f"atual={avg_speed} livre={avg_free}. "
                    f"Verificar se HERE Flow v7 mudou unidade de retorno."
                )

            if avg_jam <= 2:
                status = "Normal"
            elif avg_jam <= 5:
                status = "Moderado"
            elif avg_jam <= 8:
                status = "Intenso"
            else:
                status = "Parado"

            # Analise por segmento — identifica congestionamento localizado
            # que a media dilui (ex: 50km parados + 380km livres = media Normal)
            jam_max = round(max(jam_por_segmento), 1)
            segs_congestionados = sum(1 for j in jam_por_segmento if j >= 5)
            pct_congestionado = round(segs_congestionados / count * 100, 1)

            resultado.update({
                "status": status,
                "jam_factor": avg_jam,
                "jam_factor_max": jam_max,
                "segmentos_total": count,
                "segmentos_congestionados": segs_congestionados,
                "pct_congestionado": pct_congestionado,
                "velocidade_atual_kmh": avg_speed,
                "velocidade_livre_kmh": avg_free,
            })

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            logger.warning(f"[{trecho_nome}] HERE quota atingida (429). Circuit breaker vai abrir.")
        logger.error(f"[{trecho_nome}] Erro HERE Flow: {_sanitizar_erro(e, api_key)}")
        raise
    except Exception as e:
        logger.error(f"[{trecho_nome}] Erro HERE Flow: {_sanitizar_erro(e, api_key)}")

    return resultado


def _processar_trecho(api_key, trecho, idx, total, geocode_cache):
    """Processa incidentes + fluxo para um trecho (executado em thread)."""
    nome = trecho["nome"]
    via_coords = trecho.get("via_waypoints") or None
    logger.info(f"HERE [{idx}/{total}]: {nome}")

    try:
        incs = consultar_incidentes(
            api_key=api_key, origem=trecho["origem"], destino=trecho["destino"],
            trecho_nome=nome, geocode_cache=geocode_cache,
            rodovia_filtro=trecho.get("rodovia", ""),
            segmentos=trecho.get("segmentos", []),
            via_coords=via_coords,
        )
    except pybreaker.CircuitBreakerError:
        logger.warning(f"[{nome}] Circuit breaker HERE aberto, pulando incidentes")
        incs = []

    try:
        fluxo = consultar_fluxo_trafego(
            api_key=api_key, origem=trecho["origem"], destino=trecho["destino"],
            trecho_nome=nome, geocode_cache=geocode_cache,
            via_coords=via_coords,
        )
    except pybreaker.CircuitBreakerError:
        logger.warning(f"[{nome}] Circuit breaker HERE aberto, pulando fluxo")
        fluxo = {"trecho": nome, "status": "Sem dados", "jam_factor": 0,
                 "velocidade_atual_kmh": 0, "velocidade_livre_kmh": 0,
                 "fonte": "HERE Flow",
                 "consultado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}

    if incs:
        logger.info(f"  [{nome}] {len(incs)} incidente(s): {', '.join(i['categoria'] for i in incs)}")
    logger.info(f"  [{nome}] Fluxo: {fluxo['status']} (jam={fluxo['jam_factor']})")

    return nome, incs, fluxo


def consultar_todos(api_key, trechos, here_config=None):
    """Consulta incidentes e fluxo para todos os trechos em chunks sequenciais."""
    if here_config is None:
        here_config = {}

    chunk_size = max(1, int(here_config.get("chunk_size", 7)))
    chunk_delay_s = float(here_config.get("chunk_delay_s", 1.5))

    geocode_cache = {}
    resultado = {"incidentes": {}, "fluxo": {}}
    total = len(trechos)

    chunks = [trechos[i:i + chunk_size] for i in range(0, total, chunk_size)]
    n_chunks = len(chunks)

    logger.info(
        f"HERE: processando {total} trechos em {n_chunks} chunk(s) "
        f"de ate {chunk_size} (delay={chunk_delay_s}s entre chunks)"
    )

    for chunk_idx, chunk in enumerate(chunks, 1):
        logger.info(f"HERE chunk {chunk_idx}/{n_chunks}: {len(chunk)} trechos")

        offset = sum(len(chunks[i]) for i in range(chunk_idx - 1))

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(
                    _processar_trecho, api_key, t,
                    offset + i, total, geocode_cache,
                ): t["nome"]
                for i, t in enumerate(chunk, 1)
            }
            for future in as_completed(futures):
                try:
                    nome, incs, fluxo = future.result()
                    resultado["incidentes"][nome] = incs
                    resultado["fluxo"][nome] = fluxo
                except Exception as e:
                    nome = futures[future]
                    logger.error(f"[{nome}] Erro ao processar trecho: {_sanitizar_erro(e, api_key)}")
                    resultado["incidentes"][nome] = []
                    resultado["fluxo"][nome] = {
                        "trecho": nome, "status": "Sem dados",
                        "jam_factor": 0, "velocidade_atual_kmh": 0,
                        "velocidade_livre_kmh": 0, "fonte": "HERE Flow",
                        "consultado_em": datetime.now(timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    }

        # Delay entre chunks (não após o último)
        if chunk_idx < n_chunks:
            logger.debug(f"HERE chunk delay: {chunk_delay_s}s")
            time.sleep(chunk_delay_s)

    return resultado
