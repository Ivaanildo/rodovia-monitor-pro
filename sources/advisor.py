"""
Conselheiro de Confiança (DataAdvisor) - RodoviaMonitor Pro (MVP)
================================================

Escolhe a melhor fonte de dados baseado em freshness + confiabilidade.
Simplifica a validação para 2 fontes: HERE Traffic + Google Maps.

Score de confiança = freshness_score × source_weight × 100 (0-100%)
"""
import math
from datetime import datetime, timezone
import unicodedata


def _parse_timestamp(ts):
    """Parse timestamp string/datetime em datetime timezone-aware (UTC).

    Suporta:
    - datetime objects (naive → UTC, aware → preservado)
    - ISO com 'Z' (2026-02-16T12:34:56Z)
    - ISO com offset (2026-02-16T12:34:56-03:00)
    - ISO com microsegundos (2026-02-16T12:34:56.123456)
    - Formato padrão (2026-02-16 12:34:56)
    - Formato BR (16/02/2026 12:34)

    Args:
        ts: datetime, string ou None

    Returns:
        datetime timezone-aware (UTC) ou None se não parseável
    """
    if ts is None:
        return None

    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)

    if not isinstance(ts, str) or not ts.strip():
        return None

    s = ts.strip()

    # ISO com 'Z' → converter para +00:00
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    # Tentar fromisoformat primeiro (lida com offsets e microsegundos)
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # Fallback para formatos explícitos (interpretados como UTC)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _normalizar_texto(valor):
    base = unicodedata.normalize("NFKD", str(valor or ""))
    sem_acento = "".join(ch for ch in base if not unicodedata.combining(ch))
    return sem_acento.strip().lower()


class DataAdvisor:
    """Escolhe a melhor fonte baseado em freshness + confiabilidade."""

    SOURCE_WEIGHTS = {
        "here_incident": 1.0,    # Dados estruturados de incidentes (mais preciso)
        "tomtom_incident": 0.90, # Incidentes complementares TomTom
        "here_flow": 0.9,        # Jam factor + velocidade
        "tomtom_flow": 0.85,     # Flow TomTom (currentSpeed vs freeFlow)
        "google_duration": 0.8,  # Proxy por variação de tempo
    }

    # Intervalos de atualização conhecidos por fonte (em minutos)
    UPDATE_INTERVALS = {
        "here_incident": 2,
        "here_flow": 1,
        "tomtom_incident": 2,    # TomTom atualiza ~a cada 2 min
        "tomtom_flow": 2,        # TomTom flow ~a cada 2 min
        "google_duration": 5,
    }

    # Constante de decaimento exponencial para freshness.
    # Calibrado para: age=5min → ~0.89, age=15min → ~0.71, age=30min → ~0.50,
    # age=60min → ~0.25.  Elimina saltos abruptos entre faixas.
    _FRESHNESS_DECAY_K = 0.023
    _FRESHNESS_MIN_SCORE = 0.05  # abaixo disso arredonda para 0

    def calculate_freshness_score(self, timestamp, *, now=None):
        """Score baseado em quão recente é o dado (decaimento exponencial).

        Curva: score = e^(-k * age_minutes), onde k = 0.023.
        Scores < 0.05 são arredondados para 0.0.

        Args:
            timestamp: datetime ou string ISO format
            now: Override do horário atual (para testes). Default: UTC now.

        Returns:
            float entre 0.0 e 1.0
        """
        now = now or datetime.now(timezone.utc)
        dt = _parse_timestamp(timestamp)

        if dt is None:
            return 0.0

        age_minutes = (now - dt.astimezone(timezone.utc)).total_seconds() / 60

        # Clamp timestamps futuros (clock skew) para idade 0
        if age_minutes < 0:
            age_minutes = 0.0

        score = math.exp(-self._FRESHNESS_DECAY_K * age_minutes)
        return round(score, 3) if score >= self._FRESHNESS_MIN_SCORE else 0.0

    def get_best_source(self, trecho_data):
        """Retorna fonte mais confiável no momento.

        Args:
            trecho_data: dict {source_name: {'timestamp': ..., 'data': ...}}

        Returns:
            dict com source, confidence, data, all_scores
        """
        if not trecho_data:
            return {
                "source": "nenhuma",
                "confidence": 0,
                "data": {},
                "all_scores": {},
            }

        scores = {}
        for source_name, data in trecho_data.items():
            ts = data.get("timestamp", "")
            freshness = self.calculate_freshness_score(ts)
            weight = self.SOURCE_WEIGHTS.get(source_name, 0.5)

            scores[source_name] = {
                "confidence": round(freshness * weight * 100, 1),
                "data": data,
                "freshness": freshness,
                "weight": weight,
            }

        best = max(scores.items(), key=lambda x: x[1]["confidence"])

        # Se todas as fontes têm confiança 0, não há dados confiáveis
        if best[1]["confidence"] == 0:
            return {
                "source": "nenhuma",
                "confidence": 0,
                "data": {},
                "all_scores": scores,
            }

        return {
            "source": best[0],
            "confidence": best[1]["confidence"],
            "data": best[1]["data"],
            "all_scores": scores,
        }

    def calcular_proxima_atualizacao(self, source_name, intervalo_polling_min=30,
                                     last_timestamp=None, *, now=None):
        """Calcula minutos até a próxima atualização da fonte.

        Considera a idade do dado para estimar quando a próxima consulta
        será útil (em vez de retornar apenas o intervalo estático).

        Args:
            source_name: nome da fonte (here_incident, google_duration, etc.)
            intervalo_polling_min: intervalo de polling do sistema
            last_timestamp: timestamp da última consulta (opcional)
            now: Override do horário atual (para testes)

        Returns:
            int minutos até próxima atualização (min 1, max intervalo_polling_min)
        """
        api_interval = self.UPDATE_INTERVALS.get(source_name, 5)

        if not last_timestamp or source_name == "nenhuma":
            return min(api_interval, intervalo_polling_min)

        now = now or datetime.now(timezone.utc)
        dt = _parse_timestamp(last_timestamp)

        if dt is None:
            return min(api_interval, intervalo_polling_min)

        age_minutes = (now - dt.astimezone(timezone.utc)).total_seconds() / 60
        if age_minutes < 0:
            age_minutes = 0.0

        # Tempo restante até próximo ciclo da API
        time_until_next = api_interval - (age_minutes % api_interval)

        return int(max(1, min(time_until_next, intervalo_polling_min)))

    def _calcular_score_operacional(self, dado, *, here_incs, fontes_ativas):
        """Score operacional (0-100) com gravidade e precisao de local."""
        status = _normalizar_texto(dado.get("status", ""))
        ocorrencia = _normalizar_texto(dado.get("ocorrencia", ""))

        # Gravidade operacional da situacao.
        if (
            any(k in ocorrencia for k in ("interdicao", "colisao", "acidente"))
            or status in ("parado", "intenso")
        ):
            gravidade = 90
        elif (
            any(k in ocorrencia for k in ("obras na pista", "engarrafamento", "congestionamento"))
            or status == "moderado"
        ):
            gravidade = 70
        elif status == "normal":
            gravidade = 40
        else:
            gravidade = 30

        # Precisao espacial para apoiar decisao logistica.
        loc_score = 0
        if dado.get("km_ocorrencia") is not None:
            loc_score += 15
        if str(dado.get("trecho_especifico", "") or "").strip():
            loc_score += 10
        if str(dado.get("localizacao_precisa", "") or "").strip():
            loc_score += 10
        loc_score = min(25, loc_score)

        ocorrencias_score = min(20, len(here_incs) * 8)
        fontes_score = min(20, len(fontes_ativas) * 10)

        return round(min(100.0, (gravidade * 0.6) + loc_score + ocorrencias_score + fontes_score), 1)

    def enriquecer_dados(self, dados_correlacionados, here_dados, gmaps_resultados,
                         intervalo_polling_min=30, tomtom_dados=None):
        """Adiciona scores de confiança aos dados correlacionados.

        Para cada trecho, calcula:
        - confianca_pct: score de confiança em porcentagem (0-100)
        - fonte_escolhida: fonte com maior confiança (vencedora real)
        - fontes_consultadas: todas as fontes consultadas (para auditoria)
        - proxima_atualizacao_min: minutos até próxima atualização

        Args:
            dados_correlacionados: lista de dicts do correlator
            here_dados: dict com 'incidentes' e 'fluxo'
            gmaps_resultados: lista de resultados do Google Maps
            intervalo_polling_min: intervalo de polling
            tomtom_dados: dict com 'incidentes' e 'fluxo' do TomTom

        Returns:
            dados_correlacionados enriquecidos com campos de confiança
        """
        # Indexar resultados do Google Maps por trecho
        gmaps_por_trecho = {}
        for r in (gmaps_resultados or []):
            gmaps_por_trecho[r.get("trecho", "")] = r

        incidentes = here_dados.get("incidentes", {}) if here_dados else {}
        fluxo = here_dados.get("fluxo", {}) if here_dados else {}
        tt_incidentes = tomtom_dados.get("incidentes", {}) if tomtom_dados else {}
        tt_fluxo = tomtom_dados.get("fluxo", {}) if tomtom_dados else {}

        for dado in dados_correlacionados:
            trecho_nome = dado.get("trecho", "")
            fontes = {}

            # HERE incidentes
            here_incs = incidentes.get(trecho_nome, [])
            if here_incs:
                ts = here_incs[0].get("consultado_em", "")
                fontes["here_incident"] = {"timestamp": ts, "data": here_incs}

            # HERE fluxo (só conta se tem dados reais, não "Sem dados")
            here_flx = fluxo.get(trecho_nome, {})
            if here_flx and here_flx.get("status") not in ("Sem dados", "", None):
                ts = here_flx.get("consultado_em", "")
                fontes["here_flow"] = {"timestamp": ts, "data": here_flx}

            # TomTom incidentes
            tt_incs = tt_incidentes.get(trecho_nome, [])
            if tt_incs:
                ts = tt_incs[0].get("consultado_em", "")
                fontes["tomtom_incident"] = {"timestamp": ts, "data": tt_incs}

            # TomTom fluxo
            tt_flx = tt_fluxo.get(trecho_nome, {})
            if tt_flx and tt_flx.get("status") not in ("Sem dados", "", None):
                ts = tt_flx.get("consultado_em", "")
                fontes["tomtom_flow"] = {"timestamp": ts, "data": tt_flx}

            # Google Maps (só conta se tem dados reais, não "Erro")
            gmaps = gmaps_por_trecho.get(trecho_nome, {})
            if gmaps and gmaps.get("status") not in ("Erro", "Sem dados", "", None):
                ts = gmaps.get("consultado_em", "")
                fontes["google_duration"] = {"timestamp": ts, "data": gmaps}

            # Calcular melhor fonte
            best = self.get_best_source(fontes)

            base_conf = float(best["confidence"])
            op_conf = self._calcular_score_operacional(
                dado,
                here_incs=here_incs,
                fontes_ativas=fontes,
            )
            confianca_final = 0.0
            if fontes:
                # Blend: recencia/fonte + gravidade/precisao operacional.
                confianca_final = round((base_conf * 0.55) + (op_conf * 0.45), 1)
                confianca_final = max(0.0, min(100.0, confianca_final))

            # Penalidade por conflito entre fontes
            if dado.get("conflito_fontes"):
                grau = dado.get("conflito_grau", "moderado")
                penalidade = 20.0 if grau == "alto" else 10.0
                confianca_final = max(0.0, confianca_final - penalidade)

            dado["confianca_base_pct"] = round(base_conf, 1)
            dado["confianca_operacional_pct"] = round(op_conf, 1)
            dado["confianca_pct"] = confianca_final

            # Fonte escolhida = vencedora real
            dado["fonte_escolhida"] = self._nome_fonte_legivel(best["source"])

            # Fontes consultadas = todas as fontes que tinham dados
            nomes_fontes = sorted(set(
                self._nome_fonte_legivel(k) for k in fontes.keys()
            ))
            dado["fontes_consultadas"] = " + ".join(nomes_fontes) if nomes_fontes else "Sem dados"

            # Timestamp da fonte escolhida para cálculo age-aware
            chosen_ts = ""
            if best["source"] == "here_incident" and here_incs:
                chosen_ts = here_incs[0].get("consultado_em", "")
            elif best["source"] == "here_flow" and here_flx:
                chosen_ts = here_flx.get("consultado_em", "")
            elif best["source"] == "tomtom_incident" and tt_incs:
                chosen_ts = tt_incs[0].get("consultado_em", "")
            elif best["source"] == "tomtom_flow" and tt_flx:
                chosen_ts = tt_flx.get("consultado_em", "")
            elif best["source"] == "google_duration" and gmaps:
                chosen_ts = gmaps.get("consultado_em", "")

            dado["proxima_atualizacao_min"] = self.calcular_proxima_atualizacao(
                best["source"], intervalo_polling_min, last_timestamp=chosen_ts
            )

            # Manter compatibilidade com campo 'confianca' textual
            if confianca_final > 80:
                dado["confianca"] = "Alta"
            elif confianca_final >= 50:
                dado["confianca"] = "Média"
            elif confianca_final > 0:
                dado["confianca"] = "Baixa"
            else:
                dado["confianca"] = "Sem dados"

        return dados_correlacionados

    def _nome_fonte_legivel(self, source_key):
        """Converte key interna para nome legível."""
        nomes = {
            "here_incident": "HERE",
            "here_flow": "HERE",
            "tomtom_incident": "TomTom",
            "tomtom_flow": "TomTom",
            "google_duration": "Google",
            "nenhuma": "Sem dados",
        }
        return nomes.get(source_key, source_key)
