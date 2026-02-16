"""
Modulo de consulta Google Maps para tempo de rota.

Estrategia:
1) Tenta Directions API (legado)
2) Se legado negar por API antiga, faz fallback para Routes API v2
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

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


# Thresholds para classificacao de transito
THRESHOLDS = {
    "Normal": 1.15,
    "Moderado": 1.40,
    "Intenso": float("inf"),
}

ROUTING_PREFERENCES_VALIDAS = {
    "TRAFFIC_UNAWARE",
    "TRAFFIC_AWARE",
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
    if duracao_normal <= 0:
        return "Sem dados"
    razao = duracao_transito / duracao_normal
    if razao <= THRESHOLDS["Normal"]:
        return "Normal"
    if razao <= THRESHOLDS["Moderado"]:
        return "Moderado"
    return "Intenso"


def _parse_duration_seconds(value):
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        txt = value.strip().lower()
        if txt.endswith("s"):
            try:
                return int(float(txt[:-1]))
            except ValueError:
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


def _montar_detalhes(status, atraso, warnings):
    if status == "Normal":
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


def _consultar_legacy(api_key, origem, destino):
    """Consulta Directions API legado."""
    resp = _get_sessao().get(
        "https://maps.googleapis.com/maps/api/directions/json",
        params={
            "origin": origem, "destination": destino, "key": api_key,
            "departure_time": "now", "language": "pt-BR", "alternatives": "false",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = _validar_json_response(resp, contexto="Google Directions Legacy")
    if data is None:
        return False, {"status": "PARSE_ERROR", "error_message": "Resposta nao e JSON valido"}

    if data.get("status") != "OK":
        return False, {
            "status": data.get("status", "UNKNOWN"),
            "error_message": data.get("error_message", ""),
        }

    leg = data["routes"][0]["legs"][0]
    return True, {
        "duracao_normal_s": _parse_duration_seconds(leg["duration"]["value"]),
        "duracao_transito_s": _parse_duration_seconds(
            leg.get("duration_in_traffic", leg["duration"]).get("value")
        ),
        "distancia_m": int(leg.get("distance", {}).get("value", 0) or 0),
        "warnings": data["routes"][0].get("warnings", []),
    }


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
        "origin": {"address": origem},
        "destination": {"address": destino},
        "travelMode": "DRIVE",
        "routingPreference": routing_preference,
        "computeAlternativeRoutes": False,
        "languageCode": "pt-BR",
        "units": "METRIC",
        "departureTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
            erro = resp.json().get("error", {})
            msg = erro.get("message", resp.text[:200])
        except ValueError:
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
    """Consulta dados de rota. Legado primeiro, fallback Routes API v2."""
    routing_preference = _normalizar_routing_preference(routing_preference)
    traffic_model = _normalizar_traffic_model(traffic_model)

    resultado = {
        "trecho": trecho_nome,
        "status": "Erro",
        "duracao_normal_min": 0,
        "duracao_transito_min": 0,
        "atraso_min": 0,
        "distancia_km": 0,
        "razao_transito": 0,
        "detalhes": "",
        "fonte": "Google Maps",
        "consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "routing_preference": routing_preference,
        "traffic_model": traffic_model or "",
        "route_token": "",
        "traffic_on_polyline": [],
    }

    # 1) Directions legado
    usar_legado = routing_preference == "TRAFFIC_AWARE"
    if usar_legado:
        try:
            ok, payload = _consultar_legacy(api_key, origem, destino)
            if ok:
                _aplicar_metricas(
                    resultado, payload["duracao_normal_s"], payload["duracao_transito_s"],
                    payload["distancia_m"], payload.get("warnings", []),
                    fonte="Google Maps Directions",
                )
                logger.info(
                    f"[{trecho_nome}] {resultado['status']} - {resultado['duracao_transito_min']}min "
                    f"(normal: {resultado['duracao_normal_min']}min) [Directions]"
                )
                return resultado

            status = payload.get("status", "UNKNOWN")
            msg = payload.get("error_message", "")
            logger.warning(f"[{trecho_nome}] Directions status: {status} | {msg}")

            legado_desativado = (
                status == "REQUEST_DENIED"
                and "legacy api" in (msg or "").lower()
            )
            if not legado_desativado and status in ("ZERO_RESULTS", "NOT_FOUND"):
                resultado["detalhes"] = f"API retornou: {status}"
                return resultado
        except requests.exceptions.RequestException as e:
            logger.warning(f"[{trecho_nome}] Falha Directions legado: {_sanitizar_erro(e, api_key)}")
        except (KeyError, IndexError, TypeError) as e:
            logger.warning(f"[{trecho_nome}] Parsing Directions legado falhou: {e}")
    else:
        logger.info(
            f"[{trecho_nome}] Pulando Directions legado por routingPreference={routing_preference}"
        )

    # 2) Routes API v2
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
    """Wrapper para paralelização por trecho."""
    nome = trecho["nome"]
    logger.info(f"Google Maps [{idx}/{total}]: {nome}")
    try:
        resultado = consultar_trecho(
            api_key=api_key,
            origem=trecho["origem"],
            destino=trecho["destino"],
            trecho_nome=nome,
            routing_preference=opcoes.get("routing_preference", "TRAFFIC_AWARE"),
            traffic_model=opcoes.get("traffic_model"),
        )
    except pybreaker.CircuitBreakerError:
        logger.warning(f"[{nome}] Circuit breaker Google aberto, pulando trecho")
        resultado = {
            "trecho": nome, "status": "Erro", "duracao_normal_min": 0,
            "duracao_transito_min": 0, "atraso_min": 0, "distancia_km": 0,
            "razao_transito": 0, "detalhes": "Circuit breaker aberto",
            "fonte": "Google Maps",
            "consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "routing_preference": opcoes.get("routing_preference", "TRAFFIC_AWARE"),
            "traffic_model": opcoes.get("traffic_model") or "",
            "route_token": "",
            "traffic_on_polyline": [],
        }
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
        # Coleta resultados na ordem de submissão
        resultados_por_idx = {}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                resultados_por_idx[idx] = future.result()
            except Exception as e:
                nome = trechos[idx - 1]["nome"]
                msg_safe = _sanitizar_erro(e, api_key)
                logger.error(f"[{nome}] Erro ao consultar Google Maps: {msg_safe}")
                resultados_por_idx[idx] = {
                    "trecho": nome, "status": "Erro", "duracao_normal_min": 0,
                    "duracao_transito_min": 0, "atraso_min": 0, "distancia_km": 0,
                    "razao_transito": 0, "detalhes": f"Erro: {msg_safe}",
                    "fonte": "Google Maps",
                    "consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "routing_preference": "",
                    "traffic_model": "",
                    "route_token": "",
                    "traffic_on_polyline": [],
                    "rodovia": trechos[idx - 1].get("rodovia", ""),
                    "tipo": trechos[idx - 1].get("tipo", "federal"),
                    "concessionaria": trechos[idx - 1].get("concessionaria", ""),
                }

        # Mantém ordem original dos trechos
        for i in range(1, total + 1):
            if i in resultados_por_idx:
                resultados.append(resultados_por_idx[i])

    return resultados
