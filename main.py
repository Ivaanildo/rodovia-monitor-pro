#!/usr/bin/env python3
"""
RodoviaMonitor Pro - Bot de Monitoramento de Transito
=====================================================

Coleta dados de 3 fontes em paralelo:
  1. HERE Traffic API (incidentes + fluxo de trafego)
  2. TomTom Traffic API (incidentes + fluxo complementar)
  3. Google Maps Routes API v2 (duracao com transito)

Cruza tudo no motor de correlacao e gera relatorio Excel.
Persiste resultados no Supabase PostgreSQL.

Uso:
    python main.py --config config.json          # Executa uma vez
    python main.py --config config.json --agendar # Roda nos horarios configurados
    python main.py --interval 30                 # Polling automatico a cada N minutos
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BRT = timezone(timedelta(hours=-3))

import requests

from sources.google_maps import consultar_todos as gmaps_consultar
from sources.here_traffic import consultar_todos as here_consultar
from sources import tomtom_consultar_todos as tomtom_consultar
from sources.correlator import correlacionar_todos
from sources.advisor import DataAdvisor
from report.excel_generator import gerar_relatorio

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


class JSONFormatter(logging.Formatter):
    """Formatter que emite logs em JSON estruturado."""
    def format(self, record):
        return json.dumps({
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        })


def _configurar_logging(json_mode=False):
    """Configura logging no formato texto (padrao) ou JSON estruturado."""
    handler = logging.StreamHandler(sys.stdout)
    if json_mode:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        ))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


# Default: texto (pode ser reconfigurado via --log-json)
_configurar_logging(json_mode=False)
logger = logging.getLogger(__name__)

# Repositorio PostgreSQL (Supabase)
_repo = None


def _iniciar_storage() -> None:
    """Inicializa o repositorio PostgreSQL via SUPABASE_DB_URL."""
    global _repo
    try:
        from storage.database import get_engine
        from storage.repository import RotaRepository
    except ImportError as exc:
        logger.error(
            f"Modulo storage nao encontrado ({exc}). "
            "Execute: pip install sqlalchemy psycopg2-binary"
        )
        return

    try:
        engine = get_engine()
        _repo = RotaRepository(engine)
        logger.info(
            f"Storage PostgreSQL iniciado "
            f"(ciclos={_repo.contar_ciclos()})"
        )
    except Exception as exc:
        logger.error(f"Falha ao iniciar storage PostgreSQL: {exc}")


API_KEY_ENV_BY_FONTE = {
    "google_maps": "GOOGLE_MAPS_API_KEY",
    "here": "HERE_API_KEY",
    "tomtom": "TOMTOM_API_KEY",
}
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_env_line(linha, numero_linha=0):
    """Parseia uma linha de .env e retorna (chave, valor) ou None."""
    txt = linha.strip()
    if not txt or txt.startswith("#"):
        return None
    if txt.lower().startswith("export "):
        txt = txt[7:].strip()
    if "=" not in txt:
        return None

    chave, valor = txt.split("=", 1)
    chave = chave.strip().lstrip("\ufeff")
    if not ENV_KEY_PATTERN.match(chave):
        if numero_linha:
            logger.warning(f"Linha {numero_linha} ignorada no .env: chave invalida.")
        return None

    valor = valor.strip()
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ("'", '"'):
        valor = valor[1:-1]
    return chave, valor


def _carregar_env_arquivo(caminho):
    """Carrega variaveis de ambiente de um arquivo .env local.

    Regras:
    - linhas vazias/comentarios sao ignorados
    - aceita prefixo opcional 'export '
    - nao sobrescreve variaveis ja definidas no ambiente
    """
    env_path = Path(caminho)
    if not env_path.is_file():
        return

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for idx, linha in enumerate(f, start=1):
                parsed = _parse_env_line(linha, numero_linha=idx)
                if not parsed:
                    continue
                chave, valor = parsed
                if chave and chave not in os.environ:
                    os.environ[chave] = valor
    except OSError as e:
        logger.warning(f"Falha ao ler arquivo .env '{env_path}': {e}")


def _carregar_arquivo_estruturado(caminho_path: Path) -> dict:
    """Carrega um arquivo estruturado JSON."""
    if caminho_path.suffix != ".json":
        raise ValueError(f"Arquivo deve ser .json: {caminho_path}")
    with open(caminho_path, "r", encoding="utf-8") as f:
        return json.load(f) or {}


def carregar_config(caminho="config.json"):
    caminho_path = Path(caminho).resolve()

    if caminho_path.suffix != ".json":
        raise ValueError(f"Arquivo de config deve ser .json: {caminho}")

    if not caminho_path.is_file():
        raise FileNotFoundError(f"Config nao encontrado: {caminho}")

    config = _carregar_arquivo_estruturado(caminho_path)
    config["__config_path"] = str(caminho_path)

    arquivo_rotas = config.get("rotas_referencia_arquivo")
    if arquivo_rotas:
        config["trechos"] = _carregar_trechos_de_arquivo(str(caminho_path), arquivo_rotas)

    return config


def _resolver_caminho_relativo(caminho_base, caminho_alvo, exigir_existencia=True):
    """Resolve caminho alvo relativo ao arquivo base."""
    if os.path.isabs(caminho_alvo):
        return caminho_alvo

    base_dir = os.path.dirname(os.path.abspath(caminho_base))
    candidato = os.path.normpath(os.path.join(base_dir, caminho_alvo))
    if os.path.exists(candidato) or not exigir_existencia:
        return candidato

    return os.path.abspath(caminho_alvo)


def _normalizar_rota_logistica(rota: dict) -> dict | None:
    """Converte uma entrada do formato rota_logistica.json para o formato interno."""
    orig = rota.get("origem", {})
    dest = rota.get("destino", {})
    here = rota.get("here", {})

    hub_orig = orig.get("hub", "") if isinstance(orig, dict) else str(orig)
    hub_dest = dest.get("hub", "") if isinstance(dest, dict) else str(dest)

    if here.get("origin"):
        str_orig = here["origin"]
    elif isinstance(orig, dict) and orig.get("lat") is not None:
        str_orig = f"{orig['lat']},{orig['lng']}"
    else:
        str_orig = str(orig)

    if here.get("destination"):
        str_dest = here["destination"]
    elif isinstance(dest, dict) and dest.get("lat") is not None:
        str_dest = f"{dest['lat']},{dest['lng']}"
    else:
        str_dest = str(dest)

    rodovias = rota.get("rodovia_logica", [])
    rodovia = " / ".join(rodovias) if rodovias else ""

    nome = f"{hub_orig} -> {hub_dest}" if hub_orig and hub_dest else rota.get("id", "")
    sentido = nome

    via = here.get("via", [])
    n_via = len(via)

    ws = rota.get("waypoints_status", {})
    distance_km = ws.get("distance_km") if isinstance(ws, dict) else None

    pontos = []
    if isinstance(orig, dict) and orig.get("lat") is not None:
        pontos.append({"km": 0, "lat": orig["lat"], "lng": orig["lng"], "local": hub_orig})
    for i, v in enumerate(via):
        coords_str = v.split("!")[0].strip()
        try:
            lat_v, lng_v = [float(x) for x in coords_str.split(",")]
            if distance_km is not None:
                km_v = round(distance_km * (i + 1) / (n_via + 1))
            else:
                km_v = (i + 1) * 10
            pontos.append({"km": km_v, "lat": lat_v, "lng": lng_v, "local": f"Via-{i + 1}"})
        except ValueError:
            pass
    if isinstance(dest, dict) and dest.get("lat") is not None:
        dest_km = round(distance_km) if distance_km is not None else (n_via + 1) * 10
        pontos.append({"km": dest_km, "lat": dest["lat"], "lng": dest["lng"], "local": hub_dest})

    segmentos = [{"pontos_referencia": pontos}] if pontos else []

    via_coords = []
    for v in via:
        coords_str = v.split("!")[0].strip()
        try:
            lat_v, lng_v = [float(x) for x in coords_str.split(",")]
            via_coords.append((lat_v, lng_v))
        except ValueError:
            pass

    if not nome or not str_orig or not str_dest:
        return None

    trecho: dict = {
        "nome": nome,
        "origem": str_orig,
        "destino": str_dest,
        "rodovia": rodovia,
        "sentido": sentido,
        "tipo": rota.get("tipo", "federal"),
        "concessionaria": rota.get("concessionaria", "outros"),
        "segmentos": segmentos,
        "via_waypoints": via_coords,
    }
    if rota.get("limite_gap_km") is not None:
        trecho["limite_gap_km"] = rota["limite_gap_km"]
    return trecho


def _carregar_trechos_de_arquivo(caminho_config, arquivo_rotas):
    caminho_arquivo = _resolver_caminho_relativo(caminho_config, arquivo_rotas)

    if not os.path.exists(caminho_arquivo):
        raise FileNotFoundError(
            f"Arquivo de rotas nao encontrado: {arquivo_rotas} (resolvido como {caminho_arquivo})"
        )

    dados = _carregar_arquivo_estruturado(Path(caminho_arquivo))

    # Suporte ao formato novo (rota_logistica.json): campo "routes"
    if "routes" in dados and isinstance(dados["routes"], list):
        trechos = []
        for rota in dados["routes"]:
            if not isinstance(rota, dict):
                continue
            trecho = _normalizar_rota_logistica(rota)
            if trecho:
                trechos.append(trecho)
        if not trechos:
            raise ValueError(f"Nenhum trecho valido encontrado em {caminho_arquivo}.")
        logger.info(f"Trechos carregados (formato routes) de '{caminho_arquivo}': {len(trechos)}")
        return trechos

    # Formato legado: campo "rotas"
    rotas = dados.get("rotas", [])
    if not isinstance(rotas, list):
        raise ValueError(
            f"Estrutura invalida em {caminho_arquivo}: esperado campo 'rotas' ou 'routes' como lista."
        )

    campos = ("nome", "origem", "destino", "rodovia", "sentido")
    trechos = []
    for rota in rotas:
        if not isinstance(rota, dict):
            continue
        trecho = {c: rota.get(c, "") for c in campos}
        trecho["tipo"] = rota.get("tipo", "federal")
        trecho["concessionaria"] = rota.get("concessionaria", "outros")
        trecho["segmentos"] = rota.get("segmentos", [])

        if trecho["nome"] and trecho["origem"] and trecho["destino"]:
            trechos.append(trecho)

    if not trechos:
        raise ValueError(f"Nenhum trecho valido encontrado em {caminho_arquivo}.")

    logger.info(f"Trechos carregados de '{caminho_arquivo}': {len(trechos)}")
    return trechos


def _obter_api_key(config, nome):
    env_var = API_KEY_ENV_BY_FONTE.get(nome, "")
    if not env_var:
        return ""
    key = (os.getenv(env_var, "") or "").strip()
    if not key or "SUA_" in key.upper():
        return ""
    return key


def _api_disponivel(config, nome):
    sec = config.get(nome, {})
    if not sec.get("enabled", False):
        return False
    return bool(_obter_api_key(config, nome))


def _coletar_fonte(nome_fonte, func, *args):
    """Wrapper para coleta de uma fonte com tratamento de erro."""
    try:
        return func(*args)
    except Exception as e:
        logger.error(f"ERRO {nome_fonte}: {e}")
        return [] if nome_fonte == "Google Maps" else {"incidentes": {}, "fluxo": {}}


def _avaliar_completude_coleta(dados):
    """Avalia se a coleta obteve dados reais."""
    total = len(dados)
    sem_dados = sum(
        1 for d in dados
        if d.get("status") in ("Sem dados", "Erro")
    )
    com_dados = total - sem_dados
    resumo = {
        "total": total,
        "com_dados": com_dados,
        "sem_dados": sem_dados,
        "cobertura_pct": round(com_dados / total * 100, 1) if total > 0 else 0,
    }
    return com_dados >= 1, resumo


def executar_diagnostico_apis(config):
    """Testa conectividade real com HERE, TomTom e Google APIs. Retorna True se todas ok."""
    logger.info("=" * 60)
    logger.info("DIAGNOSTICO DE APIs -- RodoviaMonitor Pro")
    logger.info("=" * 60)

    resultados = {}

    # --- HERE API ---
    here_key = _obter_api_key(config, "here")
    if here_key:
        try:
            resp = requests.get(
                "https://geocode.search.hereapi.com/v1/geocode",
                params={"q": "Sao Paulo, Brasil", "apiKey": here_key, "limit": 1},
                timeout=10,
            )
            if resp.status_code == 200:
                resultados["here"] = ("OK", f"HTTP {resp.status_code}")
            elif resp.status_code == 401:
                resultados["here"] = ("FALHA", "HTTP 401 -- API key invalida ou sem permissao")
            elif resp.status_code == 403:
                resultados["here"] = ("FALHA", "HTTP 403 -- Quota atingida ou key bloqueada")
            else:
                resultados["here"] = ("AVISO", f"HTTP {resp.status_code}")
        except Exception as e:
            resultados["here"] = ("FALHA", f"Erro de conexao: {e}")
    else:
        resultados["here"] = ("NAO_CONFIGURADA", "HERE_API_KEY nao encontrada ou invalida")

    # --- Google Maps API ---
    gmaps_key = _obter_api_key(config, "google_maps")
    if gmaps_key:
        try:
            resp = requests.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes",
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": gmaps_key,
                    "X-Goog-FieldMask": "routes.duration",
                },
                json={
                    "origin": {"address": "Sao Paulo, Brasil"},
                    "destination": {"address": "Campinas, Brasil"},
                    "travelMode": "DRIVE",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                resultados["google"] = ("OK", f"HTTP {resp.status_code}")
            elif resp.status_code == 403:
                resultados["google"] = ("FALHA", "HTTP 403 -- Routes API nao habilitada ou key invalida")
            else:
                try:
                    msg = resp.json().get("error", {}).get("message", resp.text[:200])
                except (ValueError, AttributeError):
                    msg = resp.text[:200]
                resultados["google"] = ("AVISO", f"HTTP {resp.status_code} -- {msg}")
        except Exception as e:
            resultados["google"] = ("FALHA", f"Erro de conexao: {e}")
    else:
        resultados["google"] = ("NAO_CONFIGURADA", "GOOGLE_MAPS_API_KEY nao encontrada ou invalida")

    # --- TomTom API ---
    tomtom_key = _obter_api_key(config, "tomtom")
    if tomtom_key:
        try:
            resp = requests.get(
                "https://api.tomtom.com/traffic/services/5/incidentDetails",
                params={
                    "key": tomtom_key,
                    "bbox": "-46.7,-23.6,-46.6,-23.5",
                    "categoryFilter": "1",
                    "timeValidityFilter": "present",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                resultados["tomtom"] = ("OK", f"HTTP {resp.status_code}")
            elif resp.status_code in (401, 403):
                resultados["tomtom"] = ("FALHA", f"HTTP {resp.status_code} -- API key invalida ou sem permissao")
            else:
                resultados["tomtom"] = ("AVISO", f"HTTP {resp.status_code}")
        except Exception as e:
            resultados["tomtom"] = ("FALHA", f"Erro de conexao: {e}")
    else:
        resultados["tomtom"] = ("NAO_CONFIGURADA", "TOMTOM_API_KEY nao encontrada ou invalida")

    # --- Resultado ---
    all_ok = True
    for api, (status, detalhe) in resultados.items():
        icone = "[OK]" if status == "OK" else "[FALHA]" if status in ("FALHA", "NAO_CONFIGURADA") else "[AVISO]"
        logger.info(f"  {icone} {api.upper()}: {detalhe}")
        if status in ("FALHA", "NAO_CONFIGURADA"):
            all_ok = False

    if all_ok:
        logger.info("Todas as APIs responderam corretamente. Sistema pronto.")
    else:
        logger.error(
            "Uma ou mais APIs nao estao funcionando. "
            "Verifique as variaveis de ambiente GOOGLE_MAPS_API_KEY, HERE_API_KEY e TOMTOM_API_KEY."
        )
    logger.info("=" * 60)
    return all_ok


def _obter_config_google_maps(config):
    """Retorna config Google Maps sem dados sensiveis para roteamento."""
    gmaps_cfg = config.get("google_maps", {})
    if not isinstance(gmaps_cfg, dict):
        return {}
    return {
        "routing_preference": gmaps_cfg.get("routing_preference", "TRAFFIC_AWARE_OPTIMAL"),
        "traffic_model": gmaps_cfg.get("traffic_model"),
        "diagnostico": gmaps_cfg.get("diagnostico", {}),
    }


def executar_coleta(config, modo_mvp=False, intervalo_min=30):
    """Executa o ciclo completo: coleta paralela, correlacao e relatorio."""
    modo_label = "MVP" if modo_mvp else "FULL"
    logger.info("=" * 60)
    logger.info(f"RODOVIAMONITOR PRO {modo_label} -- {datetime.now(_BRT).strftime('%d/%m/%Y %H:%M:%S')}")
    if modo_mvp:
        logger.info("Modo: Simplificado (HERE + TomTom + Google Maps)")
    logger.info("=" * 60)

    trechos = config.get("trechos", [])
    logger.info(f"Trechos configurados: {len(trechos)}")

    gmaps_ok = _api_disponivel(config, "google_maps")
    here_ok = _api_disponivel(config, "here")
    tomtom_ok = _api_disponivel(config, "tomtom")

    fontes_ativas = []
    if gmaps_ok:
        fontes_ativas.append("Google Maps")
    if here_ok:
        fontes_ativas.append("HERE Traffic")
    if tomtom_ok:
        fontes_ativas.append("TomTom")

    logger.info(f"Fontes ativas: {', '.join(fontes_ativas) or 'NENHUMA'}")
    if gmaps_ok:
        logger.info("Google Maps: foco em ETA/fluxo (congestionamento). Incidentes detalhados seguem via HERE.")
    if gmaps_ok:
        gmaps_cfg_log = _obter_config_google_maps(config)
        logger.info(
            "Google Routes config: "
            f"routing_preference={gmaps_cfg_log.get('routing_preference', 'TRAFFIC_AWARE_OPTIMAL')}"
        )
    if not fontes_ativas:
        logger.error(
            "Nenhuma fonte de dados disponivel. "
            "Configure GOOGLE_MAPS_API_KEY, HERE_API_KEY e/ou TOMTOM_API_KEY como variavel de ambiente."
        )
        return None

    # ===== COLETA PARALELA (HERE + TomTom + Google) =====
    gmaps_resultados = []
    here_dados = {"incidentes": {}, "fluxo": {}}
    tomtom_dados = {"incidentes": {}, "fluxo": {}}

    max_w = int(gmaps_ok) + int(here_ok) + int(tomtom_ok)
    max_w = max(max_w, 1)
    with ThreadPoolExecutor(max_workers=max_w) as pool:
        futures = {}
        if gmaps_ok:
            gmaps_key = _obter_api_key(config, "google_maps")
            gmaps_cfg = _obter_config_google_maps(config)
            futures["Google Maps"] = pool.submit(
                _coletar_fonte, "Google Maps", gmaps_consultar, gmaps_key, trechos, gmaps_cfg
            )
        if here_ok:
            here_key = _obter_api_key(config, "here")
            here_cfg = config.get("here", {})
            futures["HERE Traffic"] = pool.submit(
                _coletar_fonte, "HERE Traffic", here_consultar, here_key, trechos, here_cfg
            )
        if tomtom_ok:
            tomtom_key = _obter_api_key(config, "tomtom")
            tomtom_cfg = config.get("tomtom", {})
            futures["TomTom"] = pool.submit(
                _coletar_fonte, "TomTom", tomtom_consultar, tomtom_key, trechos, tomtom_cfg
            )

        for nome_fonte, future in futures.items():
            resultado = future.result()
            if nome_fonte == "Google Maps":
                gmaps_resultados = resultado
                logger.info(f"  Google Maps: {len(gmaps_resultados)} trechos consultados")
            elif nome_fonte == "HERE Traffic":
                here_dados = resultado
                total_inc = sum(len(v) for v in here_dados.get("incidentes", {}).values())
                logger.info(f"  HERE Traffic: {total_inc} incidente(s) encontrado(s)")
            elif nome_fonte == "TomTom":
                tomtom_dados = resultado
                total_tt = sum(len(v) for v in tomtom_dados.get("incidentes", {}).values())
                logger.info(f"  TomTom: {total_tt} incidente(s) encontrado(s)")

    # ===== ALERTA DE DEGRADACAO =====
    gmaps_falhas = sum(1 for r in gmaps_resultados if r.get("status") == "Erro") if gmaps_resultados else 0
    if gmaps_ok and gmaps_falhas == len(trechos):
        logger.warning("DEGRADACAO: Google Maps falhou em TODOS os trechos. Dados apenas do HERE.")

    here_inc_total = sum(len(v) for v in here_dados.get("incidentes", {}).values())
    here_fluxo_ok = sum(
        1 for v in here_dados.get("fluxo", {}).values()
        if v.get("status") != "Sem dados"
    )
    if here_ok and here_inc_total == 0 and here_fluxo_ok == 0:
        logger.warning("DEGRADACAO: HERE retornou dados vazios para TODOS os trechos.")

    tomtom_inc_total = sum(len(v) for v in tomtom_dados.get("incidentes", {}).values())
    tomtom_fluxo_ok = sum(
        1 for v in tomtom_dados.get("fluxo", {}).values()
        if v.get("status") != "Sem dados"
    )
    if tomtom_ok and tomtom_inc_total == 0 and tomtom_fluxo_ok == 0:
        logger.warning("DEGRADACAO: TomTom retornou dados vazios para TODOS os trechos.")

    # ===== CORRELACAO =====
    logger.info("Correlacionando dados das fontes...")
    dados = correlacionar_todos(
        trechos=trechos,
        gmaps_resultados=gmaps_resultados,
        here_dados=here_dados,
        tomtom_dados=tomtom_dados,
    )

    # ===== CONFIANCA (DataAdvisor em ambos os modos) =====
    logger.info("Calculando scores de confianca (DataAdvisor)...")
    advisor = DataAdvisor()
    dados = advisor.enriquecer_dados(
        dados, here_dados, gmaps_resultados,
        intervalo_polling_min=intervalo_min,
        tomtom_dados=tomtom_dados,
    )
    alta = sum(1 for d in dados if d.get("confianca_pct", 0) > 80)
    media = sum(1 for d in dados if 50 <= d.get("confianca_pct", 0) <= 80)
    baixa = sum(1 for d in dados if d.get("confianca_pct", 0) < 50)
    logger.info(f"  Confianca: {alta} alta | {media} media | {baixa} baixa")

    # ===== PERSISTENCIA PostgreSQL (Supabase) =====
    if _repo is not None:
        try:
            ciclo_id = _repo.salvar_ciclo(dados, fontes_ativas=fontes_ativas)
            logger.info(f"  Storage: ciclo {ciclo_id} persistido ({len(dados)} trechos)")
            storage_cfg = config.get("storage", {}) if isinstance(config, dict) else {}
            retencao = int(storage_cfg.get("retencao_dias", 90))
            _repo.purgar_antigos(retencao)
        except Exception as exc:
            logger.warning(f"  Storage: falha ao persistir ciclo -- {exc}")

    if not modo_mvp:
        status_count = {}
        oc_count = 0
        for d in dados:
            s = d.get("status", "?")
            status_count[s] = status_count.get(s, 0) + 1
            if d.get("ocorrencia"):
                oc_count += 1
        logger.info(f"  Resultado: {dict(status_count)} | {oc_count} ocorrencia(s)")

    # ===== VALIDACAO DE COMPLETUDE =====
    dados_ok, resumo_dados = _avaliar_completude_coleta(dados)
    logger.info(
        f"Completude da coleta: {resumo_dados['com_dados']}/{resumo_dados['total']} "
        f"trechos com dados ({resumo_dados['cobertura_pct']}%)"
    )

    if not dados_ok:
        logger.error(
            "COLETA VAZIA: Todos os trechos retornaram 'Sem dados' ou 'Erro'. "
            "Verifique as API keys (GOOGLE_MAPS_API_KEY, HERE_API_KEY, TOMTOM_API_KEY) e a "
            "conectividade de rede. Use --check-apis para diagnostico."
        )
        return None

    if resumo_dados["sem_dados"] > 0:
        logger.warning(
            f"COBERTURA PARCIAL: {resumo_dados['sem_dados']} trecho(s) sem dados. "
            f"Relatorio gerado com dados disponiveis."
        )

    # ===== RELATORIO EXCEL =====
    rel = config.get("relatorio", {})
    pasta = rel.get("pasta_saida", "./relatorios")
    caminho_config = config.get("__config_path", os.path.join(os.getcwd(), "config.json"))
    pasta = _resolver_caminho_relativo(caminho_config, pasta, exigir_existencia=False)
    prefixo = rel.get("prefixo", "rodoviamonitor_mvp" if modo_mvp else "rodoviamonitor_pro")

    logger.info("Gerando relatorio Excel...")
    caminho = gerar_relatorio(
        dados_correlacionados=dados,
        pasta_saida=pasta,
        prefixo=prefixo,
        modo_simplificado=modo_mvp,
        resumo_coleta=resumo_dados,
    )
    logger.info(f"  Salvo: {caminho}")
    logger.info("=" * 60)

    return caminho


def executar_com_intervalo(config, intervalo_min, modo_mvp=False):
    """Executa coleta repetidamente a cada N minutos."""
    logger.info(f"Polling automatico a cada {intervalo_min} minutos. Ctrl+C para parar.")

    while True:
        try:
            resultado = executar_coleta(config, modo_mvp=modo_mvp, intervalo_min=intervalo_min)
            if resultado is None:
                logger.error("Coleta falhou. Tentando novamente no proximo ciclo.")
        except Exception as e:
            logger.error(f"Erro na execucao: {e}")

        logger.info(f"Proxima execucao em {intervalo_min} minutos...")
        time.sleep(intervalo_min * 60)


def agendar(config):
    horarios = config.get("agendamento", {}).get("horarios", ["06:00", "12:00", "18:00"])
    logger.info(f"Agendamento ativo: {', '.join(horarios)}")
    logger.info("Pressione Ctrl+C para parar.")

    executados = set()
    while True:
        agora = datetime.now(_BRT)
        hora = agora.strftime("%H:%M")
        chave = f"{agora.strftime('%Y-%m-%d')}_{hora}"

        if hora in horarios and chave not in executados:
            try:
                executar_coleta(config)
                executados.add(chave)
            except Exception as e:
                logger.error(f"Erro na execucao: {e}")

        executados = {e for e in executados if e.startswith(agora.strftime("%Y-%m-%d"))}
        time.sleep(30)


def main():
    parser = argparse.ArgumentParser(description="RodoviaMonitor Pro")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--agendar", action="store_true")
    parser.add_argument("--modo-mvp", action="store_true",
                        help="Modo MVP simplificado (apenas HERE + Google Maps)")
    parser.add_argument("--interval", type=int, default=None,
                        help="Polling automatico a cada N minutos (ex: --interval 30)")
    parser.add_argument("--log-json", action="store_true",
                        help="Emitir logs em formato JSON estruturado")
    parser.add_argument("--check-apis", action="store_true",
                        help="Verifica conectividade com APIs configuradas e sai")
    args = parser.parse_args()

    # Carrega .env do diretorio atual e do diretorio do arquivo de config (se existir).
    _carregar_env_arquivo(Path.cwd() / ".env")
    _carregar_env_arquivo(Path(args.config).resolve().parent / ".env")

    if args.log_json:
        _configurar_logging(json_mode=True)

    try:
        config = carregar_config(args.config)
    except FileNotFoundError:
        logger.error(f"Config nao encontrado: {args.config}")
        sys.exit(1)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    if args.check_apis:
        ok = executar_diagnostico_apis(config)
        sys.exit(0 if ok else 1)

    # Inicializa storage PostgreSQL (Supabase)
    _iniciar_storage()

    intervalo = config.get("intervalo_minutos", 0) if args.interval is None else args.interval

    if intervalo > 0:
        executar_com_intervalo(config, intervalo, modo_mvp=args.modo_mvp)
    elif args.agendar:
        agendar(config)
    else:
        caminho = executar_coleta(config, modo_mvp=args.modo_mvp)
        if caminho:
            print(f"\nRelatorio gerado: {caminho}")
        else:
            print("\nNenhum relatorio gerado. Verifique as API keys.")
            sys.exit(1)


if __name__ == "__main__":
    main()
