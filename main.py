#!/usr/bin/env python3
"""
RodoviaMonitor Pro - Bot de Monitoramento de Transito
=====================================================

Coleta dados de 2 fontes em paralelo:
  1. Google Maps Directions API (duracao com transito)
  2. HERE Traffic API (incidentes + fluxo de trafego)

Cruza tudo no motor de correlacao e gera relatorio Excel.

Uso:
    python main.py                  # Executa uma vez (modo completo)
    python main.py --modo-mvp       # Executa modo MVP (HERE + Google Maps)
    python main.py --agendar        # Roda nos horarios configurados
    python main.py --interval 30    # Polling automatico a cada N minutos
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yaml

from sources.google_maps import consultar_todos as gmaps_consultar
from sources.here_traffic import consultar_todos as here_consultar
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

API_KEY_ENV_BY_FONTE = {
    "google_maps": "GOOGLE_MAPS_API_KEY",
    "here": "HERE_API_KEY",
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


def carregar_config(caminho="config.yaml"):
    caminho_path = Path(caminho).resolve()

    if caminho_path.suffix not in (".yaml", ".yml"):
        raise ValueError(f"Arquivo de config deve ser .yaml ou .yml: {caminho}")

    if not caminho_path.is_file():
        raise FileNotFoundError(f"Config nao encontrado: {caminho}")

    with open(caminho_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    arquivo_rotas = config.get("rotas_referencia_arquivo")
    if arquivo_rotas:
        config["trechos"] = _carregar_trechos_de_arquivo(str(caminho_path), arquivo_rotas)

    return config


def _resolver_caminho_relativo(caminho_base, caminho_alvo):
    if os.path.isabs(caminho_alvo):
        return caminho_alvo

    base_dir = os.path.dirname(os.path.abspath(caminho_base))
    candidato = os.path.normpath(os.path.join(base_dir, caminho_alvo))
    if os.path.exists(candidato):
        return candidato

    return os.path.abspath(caminho_alvo)


def _carregar_trechos_de_arquivo(caminho_config, arquivo_rotas):
    caminho_arquivo = _resolver_caminho_relativo(caminho_config, arquivo_rotas)

    if not os.path.exists(caminho_arquivo):
        raise FileNotFoundError(
            f"Arquivo de rotas nao encontrado: {arquivo_rotas} (resolvido como {caminho_arquivo})"
        )

    with open(caminho_arquivo, "r", encoding="utf-8") as f:
        dados = yaml.safe_load(f) or {}

    rotas = dados.get("rotas", [])
    if not isinstance(rotas, list):
        raise ValueError(
            f"Estrutura invalida em {caminho_arquivo}: esperado campo 'rotas' como lista."
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
    logger.info(f"RODOVIAMONITOR PRO {modo_label} — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    if modo_mvp:
        logger.info("Modo: Simplificado (HERE + Google Maps)")
    logger.info("=" * 60)

    trechos = config.get("trechos", [])
    logger.info(f"Trechos configurados: {len(trechos)}")

    gmaps_ok = _api_disponivel(config, "google_maps")
    here_ok = _api_disponivel(config, "here")

    fontes_ativas = []
    if gmaps_ok:
        fontes_ativas.append("Google Maps")
    if here_ok:
        fontes_ativas.append("HERE Traffic")

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
            "Configure GOOGLE_MAPS_API_KEY e/ou HERE_API_KEY como variavel de ambiente."
        )
        return None

    # ===== COLETA PARALELA (HERE + Google ) =====
    gmaps_resultados = []
    here_dados = {"incidentes": {}, "fluxo": {}}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {}
        if gmaps_ok:
            gmaps_key = _obter_api_key(config, "google_maps")
            gmaps_cfg = _obter_config_google_maps(config)
            futures["Google Maps"] = pool.submit(
                _coletar_fonte, "Google Maps", gmaps_consultar, gmaps_key, trechos, gmaps_cfg
            )
        if here_ok:
            here_key = _obter_api_key(config, "here")
            futures["HERE Traffic"] = pool.submit(
                _coletar_fonte, "HERE Traffic", here_consultar, here_key, trechos
            )

        for nome_fonte, future in futures.items():
            resultado = future.result()
            if nome_fonte == "Google Maps":
                gmaps_resultados = resultado
                logger.info(f"  Google Maps: {len(gmaps_resultados)} trechos consultados")
            else:
                here_dados = resultado
                total_inc = sum(len(v) for v in here_dados.get("incidentes", {}).values())
                logger.info(f"  HERE Traffic: {total_inc} incidente(s) encontrado(s)")

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
        logger.warning("DEGRADACAO: HERE retornou dados vazios para TODOS os trechos. Dados apenas do Google.")

    # ===== CORRELACAO =====
    logger.info("Correlacionando dados das fontes...")
    dados = correlacionar_todos(
        trechos=trechos,
        gmaps_resultados=gmaps_resultados,
        here_dados=here_dados,
    )

    # ===== CONFIANCA (DataAdvisor — apenas MVP) =====
    if modo_mvp:
        logger.info("Calculando scores de confianca (DataAdvisor)...")
        advisor = DataAdvisor()
        dados = advisor.enriquecer_dados(
            dados, here_dados, gmaps_resultados,
            intervalo_polling_min=intervalo_min,
        )
        alta = sum(1 for d in dados if d.get("confianca_pct", 0) > 80)
        media = sum(1 for d in dados if 50 <= d.get("confianca_pct", 0) <= 80)
        baixa = sum(1 for d in dados if d.get("confianca_pct", 0) < 50)
        logger.info(f"  Confianca: {alta} alta | {media} media | {baixa} baixa")
    else:
        status_count = {}
        oc_count = 0
        for d in dados:
            s = d.get("status", "?")
            status_count[s] = status_count.get(s, 0) + 1
            if d.get("ocorrencia"):
                oc_count += 1
        logger.info(f"  Resultado: {dict(status_count)} | {oc_count} ocorrencia(s)")

    # ===== RELATORIO EXCEL =====
    rel = config.get("relatorio", {})
    pasta = rel.get("pasta_saida", "./relatorios")
    prefixo = rel.get("prefixo", "rodoviamonitor_mvp" if modo_mvp else "rodoviamonitor_pro")

    logger.info("Gerando relatorio Excel...")
    caminho = gerar_relatorio(
        dados_correlacionados=dados,
        pasta_saida=pasta,
        prefixo=prefixo,
        modo_simplificado=modo_mvp,
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
        agora = datetime.now()
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
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--agendar", action="store_true")
    parser.add_argument("--modo-mvp", action="store_true",
                        help="Modo MVP simplificado (apenas HERE + Google Maps)")
    parser.add_argument("--interval", type=int, default=0,
                        help="Polling automatico a cada N minutos (ex: --interval 30)")
    parser.add_argument("--log-json", action="store_true",
                        help="Emitir logs em formato JSON estruturado")
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

    intervalo = args.interval or config.get("intervalo_minutos", 0)

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
