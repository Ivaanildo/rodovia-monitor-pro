"""
Módulo HERE Traffic Incidents API.

RESOLVE OS GAPS CRÍTICOS:
- Gap 1: Detecta TIPO de incidente (acidente, obras, interdição)
- Gap 3: Retorna LOCALIZAÇÃO específica (coordenadas, descrição)
- Gap 4: Categorias padronizadas mapeáveis para a planilha

Free tier: 250.000 requests/mês (mais que suficiente)
"""
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import pybreaker

from sources.km_calculator import enriquecer_incidente
from sources.circuit import here_breaker

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
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            connect=2,
            read=2,
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _thread_local.sessao = s
    return _thread_local.sessao


# ===== Mapeamento HERE -> categorias da planilha =====
CATEGORIA_MAP_STR = {
    "accident": "Colisão",
    "brokenDownVehicle": "Colisão",
    "roadClosure": "Interdição",
    "laneRestriction": "Interdição",
    "roadHazard": "Interdição",
    "construction": "Obras na Pista",
    "plannedEvent": "Obras na Pista",
    "congestion": "Engarrafamento",
    "slowTraffic": "Engarrafamento",
    "massEvent": "Engarrafamento",
    "weather": "Condição Climática",
    "vehicleRestriction": "Ocorrência",
    "other": "Ocorrência",
}

CATEGORIA_MAP_INT = {
    0: "Engarrafamento", 1: "Colisão", 2: "Interdição", 3: "Obras na Pista",
    4: "Condição Climática", 5: "Ocorrência", 6: "Interdição", 7: "Engarrafamento",
    8: "Engarrafamento", 9: "Ocorrência", 10: "Engarrafamento", 11: "Interdição",
    12: "Obras na Pista", 13: "Ocorrência", 14: "Colisão",
}

SEVERIDADE_MAP = {1: "Baixa", 2: "Média", 3: "Alta", 4: "Crítica"}
CRITICALITY_TO_ID = {"low": 1, "minor": 2, "major": 3, "critical": 4}

# Keywords para classificação por texto (fallback)
_KEYWORDS_CATEGORIA = {
    "Colisão": ["acidente", "colisão", "colisao", "capotamento"],
    "Interdição": ["interdição", "interdicao", "bloqueio", "fechado"],
    "Obras na Pista": ["obras", "trabalhos", "manutenção", "manutencao"],
    "Engarrafamento": ["congestion", "lentidão", "lentidao", "engarrafamento"],
    "Condição Climática": ["chuva", "alagamento", "neblina", "clima"],
}


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


def _geocode_com_cache(api_key, endereco, cache):
    """Geocodifica com cache. Race conditions com threads são aceitáveis
    (pior caso: geocode duplicado, não erro de lógica)."""
    if endereco in cache:
        return cache[endereco]
    resultado = _geocode_endereco(api_key, endereco)
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


def _classificar_categoria_here(tipo, road_closed, texto):
    """Converte o tipo HERE para categoria de negócio."""
    if isinstance(tipo, str) and tipo:
        cat = CATEGORIA_MAP_STR.get(tipo)
        if cat:
            return cat
    elif isinstance(tipo, int):
        cat = CATEGORIA_MAP_INT.get(tipo)
        if cat:
            return cat

    if road_closed:
        return "Interdição"

    texto_lower = (texto or "").lower()
    for categoria, keywords in _KEYWORDS_CATEGORIA.items():
        if any(k in texto_lower for k in keywords):
            return categoria
    return "Ocorrência"


def _severidade_here(severidade_id_raw, criticality_raw):
    if isinstance(severidade_id_raw, int) and severidade_id_raw in SEVERIDADE_MAP:
        sev_id = severidade_id_raw
    else:
        sev_id = CRITICALITY_TO_ID.get((criticality_raw or "").lower(), 2)
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
        categoria = _classificar_categoria_here(tipo_raw, road_closed, texto_unificado)
        severidade_id, severidade = _severidade_here(
            inc.get("severity"), inc.get("criticality", ""),
        )

        # Localização (shape = lista de coordenadas)
        shape = location.get("shape", {})
        links = shape.get("links", [{}]) if shape else [{}]
        coords = links[0].get("points", []) if links else []
        lat = coords[0].get("lat") if coords else None
        lng = coords[0].get("lng") if coords else None

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
            "latitude": lat,
            "longitude": lng,
            "inicio": inc.get("startTime", ""),
            "fim": inc.get("endTime", ""),
            "fonte": "HERE Traffic",
            "consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    except Exception as e:
        logger.warning(f"Erro ao parsear incidente HERE: {e}")
        return None


def _normalizar_texto(valor):
    return "".join(ch for ch in (valor or "").upper() if ch.isalnum())


def _incidente_relevante_para_rodovia(incidente, rodovia_filtro):
    """Filtra incidentes para manter apenas os da rodovia do trecho."""
    if not rodovia_filtro:
        return True

    texto = f"{incidente.get('rodovia_afetada', '')} {incidente.get('descricao', '')}"
    texto_norm = _normalizar_texto(texto)
    filtro_norm = _normalizar_texto(rodovia_filtro)

    if not filtro_norm:
        return True

    if rodovia_filtro.upper().startswith("BR-"):
        numero = "".join(ch for ch in rodovia_filtro if ch.isdigit())
        if not numero:
            return True
        return f"BR{numero}" in texto_norm or ("BR" in texto_norm and numero in texto_norm)

    return filtro_norm in texto_norm


@here_breaker
def consultar_incidentes(api_key, origem, destino, trecho_nome="",
                         geocode_cache=None, rodovia_filtro="", segmentos=None):
    """Consulta incidentes de tráfego na HERE Traffic API."""
    if geocode_cache is None:
        geocode_cache = {}

    incidentes = []

    try:
        origem_coords = _parse_ou_geocode(api_key, origem, geocode_cache)
        destino_coords = _parse_ou_geocode(api_key, destino, geocode_cache)

        if not origem_coords or not destino_coords:
            logger.warning(f"[{trecho_nome}] Não foi possível geocodificar origem/destino")
            return incidentes

        bboxes = _gerar_bboxes_here(
            origem_coords[0], origem_coords[1],
            destino_coords[0], destino_coords[1],
        )

        # Consulta HERE Traffic Incidents v7
        resultados_brutos = []
        for bbox in bboxes:
            resp = _get_sessao().get(
                "https://data.traffic.hereapi.com/v7/incidents",
                params={"apiKey": api_key, "in": bbox,
                        "locationReferencing": "shape", "lang": "pt-BR"},
                timeout=12,
            )
            resp.raise_for_status()
            data = _validar_json_response(resp, contexto=f"[{trecho_nome}] HERE Incidents")
            if data is not None:
                resultados_brutos.extend(data.get("results", []))

        # Deduplicação por ID
        unicos = {}
        for item in resultados_brutos:
            item_id = item.get("id") or str(item.get("incidentDetails", {})) + str(item.get("location", {}))
            unicos[item_id] = item

        for item in unicos.values():
            incidente = _parse_incidente(item, trecho_nome)
            if incidente and _incidente_relevante_para_rodovia(incidente, rodovia_filtro):
                if segmentos:
                    enriquecer_incidente(incidente, segmentos)
                incidentes.append(incidente)

        logger.info(
            f"[{trecho_nome}] HERE bruto={len(unicos)} | filtrado={len(incidentes)} "
            f"incidente(s) em {len(bboxes)} bbox(s)"
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"[{trecho_nome}] Erro HERE API: {_sanitizar_erro(e, api_key)}")
    except Exception as e:
        logger.error(f"[{trecho_nome}] Erro ao processar HERE: {_sanitizar_erro(e, api_key)}")

    return incidentes


@here_breaker
def consultar_fluxo_trafego(api_key, origem, destino, trecho_nome="",
                            geocode_cache=None):
    """Consulta fluxo de tráfego (velocidade atual vs livre) via HERE Flow API."""
    if geocode_cache is None:
        geocode_cache = {}

    resultado = {
        "trecho": trecho_nome,
        "status": "Sem dados",
        "jam_factor": 0,
        "velocidade_atual_kmh": 0,
        "velocidade_livre_kmh": 0,
        "fonte": "HERE Flow",
        "consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        origem_coords = _parse_ou_geocode(api_key, origem, geocode_cache)
        destino_coords = _parse_ou_geocode(api_key, destino, geocode_cache)

        if not origem_coords or not destino_coords:
            return resultado

        bboxes = _gerar_bboxes_here(
            origem_coords[0], origem_coords[1],
            destino_coords[0], destino_coords[1],
            padding_km=10.0,
        )

        results = []
        for bbox in bboxes:
            resp = _get_sessao().get(
                "https://data.traffic.hereapi.com/v7/flow",
                params={"apiKey": api_key, "in": bbox,
                        "locationReferencing": "shape"},
                timeout=12,
            )
            resp.raise_for_status()
            data = _validar_json_response(resp, contexto=f"[{trecho_nome}] HERE Flow")
            if data is not None:
                results.extend(data.get("results", []))

        if not results:
            return resultado

        total_speed = total_free = total_jam = count = 0
        for r in results:
            cf = r.get("currentFlow", {})
            speed = cf.get("speed", 0)
            free_flow = cf.get("freeFlow", 0)
            if speed > 0 and free_flow > 0:
                total_speed += speed
                total_free += free_flow
                total_jam += cf.get("jamFactor", 0)
                count += 1

        if count > 0:
            avg_speed = round((total_speed / count) * 3.6, 1)
            avg_free = round((total_free / count) * 3.6, 1)
            avg_jam = max(0.0, min(10.0, round(total_jam / count, 1)))

            if avg_jam <= 2:
                status = "Normal"
            elif avg_jam <= 5:
                status = "Moderado"
            elif avg_jam <= 8:
                status = "Intenso"
            else:
                status = "Parado"

            resultado.update({
                "status": status,
                "jam_factor": avg_jam,
                "velocidade_atual_kmh": avg_speed,
                "velocidade_livre_kmh": avg_free,
            })

    except Exception as e:
        logger.error(f"[{trecho_nome}] Erro HERE Flow: {_sanitizar_erro(e, api_key)}")

    return resultado


def _processar_trecho(api_key, trecho, idx, total, geocode_cache):
    """Processa incidentes + fluxo para um trecho (executado em thread)."""
    nome = trecho["nome"]
    logger.info(f"HERE [{idx}/{total}]: {nome}")

    try:
        incs = consultar_incidentes(
            api_key=api_key, origem=trecho["origem"], destino=trecho["destino"],
            trecho_nome=nome, geocode_cache=geocode_cache,
            rodovia_filtro=trecho.get("rodovia", ""),
            segmentos=trecho.get("segmentos", []),
        )
    except pybreaker.CircuitBreakerError:
        logger.warning(f"[{nome}] Circuit breaker HERE aberto, pulando incidentes")
        incs = []

    try:
        fluxo = consultar_fluxo_trafego(
            api_key=api_key, origem=trecho["origem"], destino=trecho["destino"],
            trecho_nome=nome, geocode_cache=geocode_cache,
        )
    except pybreaker.CircuitBreakerError:
        logger.warning(f"[{nome}] Circuit breaker HERE aberto, pulando fluxo")
        fluxo = {"trecho": nome, "status": "Sem dados", "jam_factor": 0,
                 "velocidade_atual_kmh": 0, "velocidade_livre_kmh": 0,
                 "fonte": "HERE Flow",
                 "consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    if incs:
        logger.info(f"  [{nome}] {len(incs)} incidente(s): {', '.join(i['categoria'] for i in incs)}")
    logger.info(f"  [{nome}] Fluxo: {fluxo['status']} (jam={fluxo['jam_factor']})")

    return nome, incs, fluxo


def consultar_todos(api_key, trechos):
    """Consulta incidentes e fluxo para todos os trechos em paralelo."""
    geocode_cache = {}
    resultado = {"incidentes": {}, "fluxo": {}}
    total = len(trechos)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_processar_trecho, api_key, t, i, total, geocode_cache): t["nome"]
            for i, t in enumerate(trechos, 1)
        }
        for future in as_completed(futures):
            try:
                nome, incs, fluxo = future.result()
                resultado["incidentes"][nome] = incs
                resultado["fluxo"][nome] = fluxo
            except Exception as e:
                nome = futures[future]
                logger.error(f"[{nome}] Erro ao processar trecho: {e}")
                resultado["incidentes"][nome] = []
                resultado["fluxo"][nome] = {"trecho": nome, "status": "Sem dados",
                                            "jam_factor": 0, "velocidade_atual_kmh": 0,
                                            "velocidade_livre_kmh": 0, "fonte": "HERE Flow",
                                            "consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    return resultado
