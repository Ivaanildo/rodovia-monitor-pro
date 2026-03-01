"""
Modulo de consulta Google Maps para tempo de rota via Routes API v2.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pybreaker
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from sources.circuit import google_breaker

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


# ===== HTTP Session thread-local com retry =====
_thread_local = threading.local()


def _get_sessao():
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


# Thresholds para classificacao de transito (razao duracao_transito / duracao_normal)
THRESHOLDS_RAZAO = {
    "Normal": 1.15,
    "Moderado": 1.40,
}

# Thresholds de atraso absoluto (minutos) — complementam a razao para rotas longas
# onde o atraso absoluto e significativo mas a razao e baixa
THRESHOLDS_ATRASO_ABS = {
    "Moderado": {"min_atraso_min": 10, "min_razao": 1.03},
    "Intenso": {"min_atraso_min": 25, "min_razao": 1.05},
}

ROUTING_PREFERENCES_VALIDAS = {
    "TRAFFIC_UNAWARE",
    "TRAFFIC_AWARE_OPTIMAL",
}

TRAFFIC_MODELS_VALIDOS = {
    "BEST_GUESS",
    "PESSIMISTIC",
    "OPTIMISTIC",
}


def _normalizar_routing_preference(valor, padrao="TRAFFIC_AWARE_OPTIMAL"):
    pref = str(valor or "").strip().upper()
    if pref in ROUTING_PREFERENCES_VALIDAS:
        return pref
    if valor:
        logger.warning(
            f"routingPreference invalido '{valor}'. Usando padrao '{padrao}'. "
            f"Validos: {sorted(ROUTING_PREFERENCES_VALIDAS)}"
        )
    return padrao


def _normalizar_traffic_model(valor):
    if valor is None:
        return None
    model = str(valor).strip().upper()
    if not model:
        return None
    if model in TRAFFIC_MODELS_VALIDOS:
        return model
    logger.warning(
        f"trafficModel invalido '{valor}'. Ignorando. "
        f"Validos: {sorted(TRAFFIC_MODELS_VALIDOS)}"
    )
    return None


def _normalizar_nome_trecho(nome):
    return str(nome or "").strip().casefold()


def classificar_transito(duracao_normal, duracao_transito):
    """Classifica transito combinando razao de duracao + atraso absoluto.

    A razao sozinha ignora atrasos absolutos significativos em rotas longas.
    Ex: 300min normal, 325min transito = razao 1.08 ("Normal" por razao)
    mas 25min de atraso absoluto justifica "Intenso".

    Thresholds de atraso absoluto exigem razao minima para evitar falsos
    positivos por ruido (min_razao 1.03 para Moderado, 1.05 para Intenso).
    """
    if duracao_normal <= 0:
        return "Sem dados"
    razao = duracao_transito / duracao_normal
    atraso_min = max(0, (duracao_transito - duracao_normal)) / 60

    # Intenso: razao alta OU atraso absoluto grande com razao minima
    th_intenso = THRESHOLDS_ATRASO_ABS["Intenso"]
    if razao > THRESHOLDS_RAZAO["Moderado"] or (
        atraso_min >= th_intenso["min_atraso_min"]
        and razao > th_intenso["min_razao"]
    ):
        return "Intenso"

    # Moderado: razao moderada OU atraso absoluto moderado com razao minima
    th_moderado = THRESHOLDS_ATRASO_ABS["Moderado"]
    if razao > THRESHOLDS_RAZAO["Normal"] or (
        atraso_min >= th_moderado["min_atraso_min"]
        and razao > th_moderado["min_razao"]
    ):
        return "Moderado"

    return "Normal"


def _parse_duration_seconds(value):
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        txt = value.strip().lower()
        if txt.endswith("s"):
            txt = txt[:-1]
        try:
            return int(float(txt))
        except ValueError:
            if value:
                logger.warning(f"Formato de duracao inesperado: '{value}'")
            return 0
    if value:
        logger.warning(f"Formato de duracao nao reconhecido: '{value}' (tipo={type(value).__name__})")
    return 0


def _resolver_config_google(config_google):
    """Normaliza configuracoes de roteamento do Google Routes."""
    cfg = config_google if isinstance(config_google, dict) else {}
    diagnostico = cfg.get("diagnostico", {})
    if not isinstance(diagnostico, dict):
        diagnostico = {}

    trechos_diag_raw = diagnostico.get("trechos", [])
    trechos_diag = set()
    if isinstance(trechos_diag_raw, list):
        trechos_diag = {
            _normalizar_nome_trecho(t)
            for t in trechos_diag_raw
            if str(t or "").strip()
        }

    config_resolvida = {
        "routing_preference_padrao": _normalizar_routing_preference(
            cfg.get("routing_preference", "TRAFFIC_AWARE_OPTIMAL")
        ),
        "traffic_model_padrao": _normalizar_traffic_model(cfg.get("traffic_model")),
        "diagnostico_enabled": bool(diagnostico.get("enabled", False)),
        "diagnostico_aplicar_em_todos": bool(diagnostico.get("aplicar_em_todos", False)),
        "diagnostico_trechos": trechos_diag,
        "diagnostico_routing_preference": _normalizar_routing_preference(
            diagnostico.get("routing_preference", "TRAFFIC_AWARE_OPTIMAL"),
            padrao="TRAFFIC_AWARE_OPTIMAL",
        ),
        "diagnostico_traffic_model": _normalizar_traffic_model(diagnostico.get("traffic_model")),
    }
    if (
        config_resolvida["diagnostico_enabled"]
        and not config_resolvida["diagnostico_aplicar_em_todos"]
        and not config_resolvida["diagnostico_trechos"]
    ):
        logger.warning(
            "Google diagnostico enabled=true, mas sem trechos e sem aplicar_em_todos. "
            "Nenhum trecho usara o modo diagnostico."
        )
    return config_resolvida


def _resolver_opcoes_trecho(nome_trecho, cfg):
    """Resolve routingPreference/trafficModel a ser usado em um trecho."""
    usar_diagnostico = False
    if cfg["diagnostico_enabled"]:
        trecho_norm = _normalizar_nome_trecho(nome_trecho)
        usar_diagnostico = (
            cfg["diagnostico_aplicar_em_todos"]
            or trecho_norm in cfg["diagnostico_trechos"]
        )

    if usar_diagnostico:
        return {
            "routing_preference": cfg["diagnostico_routing_preference"],
            "traffic_model": cfg["diagnostico_traffic_model"],
        }

    return {
        "routing_preference": cfg["routing_preference_padrao"],
        "traffic_model": cfg["traffic_model_padrao"],
    }


def _resultado_erro(nome, detalhes="", routing_preference="", traffic_model=""):
    """Factory para resultado padrao de erro."""
    return {
        "trecho": nome,
        "status": "Erro",
        "duracao_normal_min": 0,
        "duracao_transito_min": 0,
        "atraso_min": 0,
        "distancia_km": 0,
        "razao_transito": 0,
        "detalhes": detalhes,
        "fonte": "Google Maps",
        "consultado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "routing_preference": routing_preference,
        "traffic_model": traffic_model,
        "route_token": "",
        "traffic_on_polyline": [],
    }


def _montar_detalhes(status, atraso, warnings):
    if status in ("Sem dados", "Erro"):
        detalhes = "Dados indisponiveis para este trecho"
    elif status == "Normal":
        detalhes = "Rodovia segue com transito normal, sem alteracoes"
    elif status == "Moderado":
        detalhes = f"Transito moderado, atraso de ~{atraso} min sobre o normal"
    else:
        detalhes = f"Transito intenso, atraso de ~{atraso} min sobre o normal"

    if warnings:
        detalhes += f" | Alertas: {'; '.join(str(w) for w in warnings)}"
    return detalhes


def _aplicar_metricas(resultado, duracao_normal_s, duracao_transito_s,
                      distancia_m, warnings, fonte):
    duracao_normal_min = round(duracao_normal_s / 60) if duracao_normal_s else 0
    duracao_transito_min = round(duracao_transito_s / 60) if duracao_transito_s else 0
    atraso = max(0, duracao_transito_min - duracao_normal_min)
    status = classificar_transito(duracao_normal_s, duracao_transito_s)
    razao = round(duracao_transito_s / duracao_normal_s, 2) if duracao_normal_s > 0 else 0

    resultado.update({
        "status": status,
        "duracao_normal_min": duracao_normal_min,
        "duracao_transito_min": duracao_transito_min,
        "atraso_min": atraso,
        "distancia_km": round((distancia_m or 0) / 1000, 1),
        "razao_transito": razao,
        "detalhes": _montar_detalhes(status, atraso, warnings or []),
        "fonte": fonte,
    })



def _parse_coordenadas(valor):
    """Tenta parsear string 'lat,lng' e retorna waypoint para Routes API v2.

    Se for coordenadas validas, retorna {"location": {"latLng": {...}}}.
    Caso contrario, retorna {"address": valor}.
    """
    try:
        parts = str(valor).split(",")
        if len(parts) == 2:
            lat, lng = float(parts[0].strip()), float(parts[1].strip())
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return {"location": {"latLng": {"latitude": lat, "longitude": lng}}}
    except (ValueError, IndexError):
        pass
    return {"address": valor}


def _consultar_routes_v2(api_key, origem, destino, routing_preference="TRAFFIC_AWARE_OPTIMAL",
                         traffic_model=None):
    """Consulta Routes API v2."""
    field_mask = [
        "routes.duration",
        "routes.staticDuration",
        "routes.distanceMeters",
        "routes.warnings",
    ]
    body = {
        "origin": _parse_coordenadas(origem),
        "destination": _parse_coordenadas(destino),
        "travelMode": "DRIVE",
        "routingPreference": routing_preference,
        "computeAlternativeRoutes": False,
        "languageCode": "pt-BR",
        "units": "METRIC",
        "departureTime": (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if routing_preference != "TRAFFIC_UNAWARE":
        # Solicita granularidade de velocidade por segmentos da rota.
        body["extraComputations"] = ["TRAFFIC_ON_POLYLINE"]
        field_mask.append("routes.travelAdvisory.speedReadingIntervals")

    if routing_preference == "TRAFFIC_AWARE_OPTIMAL":
        field_mask.extend([
            "routes.routeToken",
        ])
        if traffic_model:
            body["trafficModel"] = traffic_model

    resp = _get_sessao().post(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": ",".join(field_mask),
        },
        json=body,
        timeout=30,
    )

    if resp.status_code >= 400:
        try:
            erro_data = resp.json()
            erro = erro_data.get("error", {}) if isinstance(erro_data, dict) else {}
            msg = erro.get("message", resp.text[:200])
        except (ValueError, AttributeError):
            msg = resp.text[:200]
        return False, {"status": f"HTTP_{resp.status_code}", "error_message": msg}

    data = _validar_json_response(resp, contexto="Google Routes v2")
    if data is None:
        return False, {"status": "PARSE_ERROR", "error_message": "Resposta nao e JSON valido"}

    routes = data.get("routes", [])
    if not routes:
        return False, {"status": "NO_ROUTES", "error_message": "Routes API nao retornou rotas"}

    route = routes[0]
    duracao_transito_s = _parse_duration_seconds(route.get("duration"))
    duracao_normal_s = _parse_duration_seconds(route.get("staticDuration")) or duracao_transito_s

    return True, {
        "duracao_normal_s": duracao_normal_s,
        "duracao_transito_s": duracao_transito_s,
        "distancia_m": int(route.get("distanceMeters", 0) or 0),
        "warnings": route.get("warnings", []),
        "route_token": route.get("routeToken", ""),
        "traffic_on_polyline": route.get("travelAdvisory", {}).get("speedReadingIntervals", []),
    }


@google_breaker
def consultar_trecho(api_key, origem, destino, trecho_nome="",
                     routing_preference="TRAFFIC_AWARE_OPTIMAL", traffic_model=None):
    """Consulta dados de rota via Routes API v2."""
    routing_preference = _normalizar_routing_preference(routing_preference)
    traffic_model = _normalizar_traffic_model(traffic_model)

    resultado = _resultado_erro(
        trecho_nome,
        routing_preference=routing_preference,
        traffic_model=traffic_model or "",
    )

    try:
        ok, payload = _consultar_routes_v2(
            api_key,
            origem,
            destino,
            routing_preference=routing_preference,
            traffic_model=traffic_model,
        )
        if ok:
            _aplicar_metricas(
                resultado, payload["duracao_normal_s"], payload["duracao_transito_s"],
                payload["distancia_m"], payload.get("warnings", []),
                fonte="Google Routes API v2",
            )
            resultado["route_token"] = payload.get("route_token", "") or ""
            resultado["traffic_on_polyline"] = payload.get("traffic_on_polyline", []) or []
            logger.info(
                f"[{trecho_nome}] {resultado['status']} - {resultado['duracao_transito_min']}min "
                f"(normal: {resultado['duracao_normal_min']}min) "
                f"[Routes v2/{routing_preference}]"
            )
            return resultado

        status = payload.get("status", "UNKNOWN")
        msg = payload.get("error_message", "")
        resultado["detalhes"] = f"Routes API retornou: {status} | {msg}"
        logger.warning(f"[{trecho_nome}] Routes API status: {status} | {msg}")
    except requests.exceptions.RequestException as e:
        msg_safe = _sanitizar_erro(e, api_key)
        resultado["detalhes"] = f"Erro de conexao: {msg_safe}"
        logger.error(f"[{trecho_nome}] Erro Routes API: {msg_safe}")
    except (KeyError, IndexError, TypeError) as e:
        resultado["detalhes"] = f"Erro ao processar resposta: {e}"
        logger.error(f"[{trecho_nome}] Erro de parsing Routes API: {e}")

    return resultado


def _consultar_trecho_wrapper(api_key, trecho, idx, total, opcoes):
    """Wrapper para paralelizacao por trecho."""
    nome = trecho["nome"]
    logger.info(f"Google Maps [{idx}/{total}]: {nome}")
    try:
        resultado = consultar_trecho(
            api_key=api_key,
            origem=trecho["origem"],
            destino=trecho["destino"],
            trecho_nome=nome,
            routing_preference=opcoes.get("routing_preference", "TRAFFIC_AWARE_OPTIMAL"),
            traffic_model=opcoes.get("traffic_model"),
        )
    except pybreaker.CircuitBreakerError:
        logger.warning(f"[{nome}] Circuit breaker Google aberto, pulando trecho")
        resultado = _resultado_erro(
            nome,
            detalhes="Circuit breaker aberto",
            routing_preference=opcoes.get("routing_preference", "TRAFFIC_AWARE_OPTIMAL"),
            traffic_model=opcoes.get("traffic_model") or "",
        )
    resultado["rodovia"] = trecho.get("rodovia", "")
    resultado["tipo"] = trecho.get("tipo", "federal")
    resultado["concessionaria"] = trecho.get("concessionaria", "")
    return resultado


def consultar_todos(api_key, trechos, config_google=None):
    """Consulta todos os trechos em paralelo (max 3 workers)."""
    resultados = []
    total = len(trechos)
    config_resolvida = _resolver_config_google(config_google)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(
                _consultar_trecho_wrapper,
                api_key,
                t,
                i,
                total,
                _resolver_opcoes_trecho(t.get("nome", ""), config_resolvida),
            ): i
            for i, t in enumerate(trechos, 1)
        }
        # Coleta resultados na ordem de submissao
        resultados_por_idx = {}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                resultados_por_idx[idx] = future.result()
            except Exception as e:
                trecho_info = trechos[idx - 1]
                nome = trecho_info.get("nome", f"trecho_{idx}")
                msg_safe = _sanitizar_erro(e, api_key)
                logger.error(f"[{nome}] Erro ao consultar Google Maps: {msg_safe}")
                res_erro = _resultado_erro(nome, detalhes=f"Erro: {msg_safe}")
                res_erro["rodovia"] = trecho_info.get("rodovia", "")
                res_erro["tipo"] = trecho_info.get("tipo", "federal")
                res_erro["concessionaria"] = trecho_info.get("concessionaria", "")
                resultados_por_idx[idx] = res_erro

        # Mantem ordem original dos trechos
        for i in range(1, total + 1):
            if i in resultados_por_idx:
                resultados.append(resultados_por_idx[i])

    return resultados
