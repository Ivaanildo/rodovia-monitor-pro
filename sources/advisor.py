"""
Conselheiro de ConfianÃ§a (DataAdvisor) - RodoviaMonitor Pro (MVP)
================================================

Escolhe a melhor fonte de dados baseado em freshness + confiabilidade.
Simplifica a validaÃ§Ã£o para 2 fontes: HERE Traffic + Google Maps.

Score de confianÃ§a = freshness_score Ã— source_weight Ã— 100 (0-100%)
"""
from datetime import datetime


class DataAdvisor:
    """Escolhe a melhor fonte baseado em freshness + confiabilidade."""

    SOURCE_WEIGHTS = {
        "here_incident": 1.0,   # Dados estruturados de incidentes
        "here_flow": 0.9,       # Jam factor + velocidade
        "google_duration": 0.8, # Proxy por variaÃ§Ã£o de tempo
    }

    # Intervalos de atualizaÃ§Ã£o conhecidos por fonte (em minutos)
    UPDATE_INTERVALS = {
        "here_incident": 2,
        "here_flow": 1,
        "google_duration": 5,
    }

    def calculate_freshness_score(self, timestamp):
        """Score baseado em quÃ£o recente Ã© o dado.

        Args:
            timestamp: datetime ou string ISO format

        Returns:
            float entre 0.0 e 1.0
        """
        if isinstance(timestamp, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M"):
                try:
                    timestamp = datetime.strptime(timestamp, fmt)
                    break
                except ValueError:
                    continue
            else:
                return 0.0

        age_minutes = (datetime.now() - timestamp).total_seconds() / 60

        if age_minutes <= 5:
            return 1.0
        elif age_minutes <= 15:
            return 0.8
        elif age_minutes <= 30:
            return 0.5
        elif age_minutes <= 60:
            return 0.2
        else:
            return 0.0  # Dados muito antigos, desconsiderar

    def get_best_source(self, trecho_data):
        """Retorna fonte mais confiÃ¡vel no momento.

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

        return {
            "source": best[0],
            "confidence": best[1]["confidence"],
            "data": best[1]["data"],
            "all_scores": scores,
        }

    def calcular_proxima_atualizacao(self, source_name, intervalo_polling_min=30):
        """Calcula minutos atÃ© a prÃ³xima atualizaÃ§Ã£o da fonte.

        Args:
            source_name: nome da fonte (here_incident, google_duration, etc.)
            intervalo_polling_min: intervalo de polling do sistema

        Returns:
            int minutos atÃ© prÃ³xima atualizaÃ§Ã£o
        """
        api_interval = self.UPDATE_INTERVALS.get(source_name, 5)
        return min(api_interval, intervalo_polling_min)

    def enriquecer_dados(self, dados_correlacionados, here_dados, gmaps_resultados,
                         intervalo_polling_min=30):
        """Adiciona scores de confianÃ§a aos dados correlacionados.

        Para cada trecho, calcula:
        - confianca_pct: score de confianÃ§a em porcentagem (0-100)
        - fonte_vencedora: fonte com maior confianÃ§a
        - proxima_atualizacao_min: minutos atÃ© prÃ³xima atualizaÃ§Ã£o

        Args:
            dados_correlacionados: lista de dicts do correlator
            here_dados: dict com 'incidentes' e 'fluxo'
            gmaps_resultados: lista de resultados do Google Maps
            intervalo_polling_min: intervalo de polling

        Returns:
            dados_correlacionados enriquecidos com campos de confianÃ§a
        """
        # Indexar resultados do Google Maps por trecho
        gmaps_por_trecho = {}
        for r in (gmaps_resultados or []):
            gmaps_por_trecho[r.get("trecho", "")] = r

        incidentes = here_dados.get("incidentes", {}) if here_dados else {}
        fluxo = here_dados.get("fluxo", {}) if here_dados else {}

        for dado in dados_correlacionados:
            trecho_nome = dado.get("trecho", "")
            fontes = {}

            # HERE incidentes
            here_incs = incidentes.get(trecho_nome, [])
            if here_incs:
                ts = here_incs[0].get("consultado_em", "")
                fontes["here_incident"] = {"timestamp": ts, "data": here_incs}

            # HERE fluxo
            here_flx = fluxo.get(trecho_nome, {})
            if here_flx:
                ts = here_flx.get("consultado_em", "")
                fontes["here_flow"] = {"timestamp": ts, "data": here_flx}

            # Google Maps
            gmaps = gmaps_por_trecho.get(trecho_nome, {})
            if gmaps:
                ts = gmaps.get("consultado_em", "")
                fontes["google_duration"] = {"timestamp": ts, "data": gmaps}

            # Calcular melhor fonte
            best = self.get_best_source(fontes)

            dado["confianca_pct"] = best["confidence"]
            # Mostrar todas as fontes consultadas, nÃ£o sÃ³ a vencedora
            nomes_fontes = sorted(set(
                self._nome_fonte_legivel(k) for k in fontes.keys()
            ))
            dado["fonte_vencedora"] = " + ".join(nomes_fontes) if nomes_fontes else "Sem dados"
            dado["proxima_atualizacao_min"] = self.calcular_proxima_atualizacao(
                best["source"], intervalo_polling_min
            )

            # Manter compatibilidade com campo 'confianca' textual
            if best["confidence"] > 80:
                dado["confianca"] = "Alta"
            elif best["confidence"] >= 50:
                dado["confianca"] = "MÃ©dia"
            else:
                dado["confianca"] = "Baixa"

        return dados_correlacionados

    def _nome_fonte_legivel(self, source_key):
        """Converte key interna para nome legÃ­vel."""
        nomes = {
            "here_incident": "HERE",
            "here_flow": "HERE",
            "google_duration": "Google",
            "nenhuma": "Sem dados",
        }
        return nomes.get(source_key, source_key)

