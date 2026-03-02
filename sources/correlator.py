"""
Motor de Correlacao de Dados.

Cruza dados de 3 fontes sem duplicar: HERE, TomTom, Google Maps.

Logica:
1. Para cada trecho, coleta dados de: Google Maps, HERE, TomTom
2. Determina o STATUS final baseado na fonte mais confiavel
3. Determina a OCORRENCIA final priorizando fontes com detalhes
4. Monta a DESCRICAO combinada mais informativa
5. Gera links de referencia para Waze e Google Maps

Hierarquia de confianca para STATUS:
  HERE Flow > TomTom Flow > Google Maps (duration_in_traffic)

Hierarquia de confianca para OCORRENCIAS:
  HERE Incidents > TomTom Incidents > Inferencia Google Maps
"""
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

_BRT = timezone(timedelta(hours=-3))
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)
_TAG_PATTERN = re.compile(r"\[[^\]]+\]")
_ESPACO_PATTERN = re.compile(r"\s+")
_RUIDO_TOKENS = {"alerta", "warning", "seguranca", "segurana", "safety"}
_PREFIXO_RUIDO = {"inicio", "fim", "start", "end"}
_PESOS_OCORRENCIA = {
    "interdicao": 100,
    "bloqueio parcial": 70,  # antes de "bloqueio" para match especifico primeiro
    "bloqueio": 100,
    "colisao": 90,
    "acidente": 90,
    "obras na pista": 60,
    "obras": 60,
    "condicao climatica": 50,
    "engarrafamento": 40,
    "congestionamento": 40,
    "ocorrencia": 10,
}

# Mapeamento de status para nivel numerico (para deteccao de conflito)
_STATUS_NIVEL = {
    "normal": 0,
    "moderado": 1,
    "intenso": 2,
    "parado": 3,
    "sem dados": -1,
}


def _parse_lat_lng(s: str) -> Optional[tuple]:
    """Converte string 'lat,lng' em (lat, lng). Retorna None se invalido."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if "," not in s:
        return None
    try:
        parts = s.split(",", 1)
        lat, lng = float(parts[0].strip()), float(parts[1].strip())
        return (lat, lng)
    except (ValueError, IndexError):
        return None


def _coordenadas_trecho_api(trecho_config: dict) -> Optional[tuple]:
    """
    Extrai coordenadas de origem e destino EXATAS usadas na consulta da API
    (campos origem/destino do YAML, no formato "lat,lng").
    Garante que os links Waze/Google Maps apontem para o mesmo trecho consultado.

    Retorna:
        (origem_lat, origem_lng, destino_lat, destino_lng) ou None se indisponivel
    """
    origem_ll = trecho_config.get("origem", "")
    destino_ll = trecho_config.get("destino", "")
    o = _parse_lat_lng(origem_ll)
    d = _parse_lat_lng(destino_ll)
    if o and d:
        return (o[0], o[1], d[0], d[1])
    return None


def _extrair_coordenadas(trecho_config: dict) -> Optional[tuple]:
    """
    Extrai coordenadas de origem e destino dos segmentos do trecho.
    Usado como fallback quando origem/destino nao estao em formato "lat,lng".

    Retorna:
        (origem_lat, origem_lng, destino_lat, destino_lng) ou None se indisponivel
    """
    segmentos = trecho_config.get("segmentos", [])
    if not segmentos:
        return None

    primeiro_seg = segmentos[0]
    ultimo_seg = segmentos[-1]

    pontos_inicio = primeiro_seg.get("pontos_referencia", [])
    pontos_fim = ultimo_seg.get("pontos_referencia", [])

    if not pontos_inicio or not pontos_fim:
        return None

    p_origem = pontos_inicio[0]
    p_destino = pontos_fim[-1]

    try:
        return (
            float(p_origem["lat"]), float(p_origem["lng"]),
            float(p_destino["lat"]), float(p_destino["lng"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _coordenadas_para_links(trecho_config: dict) -> Optional[tuple]:
    """
    Coordenadas para montar links Waze/Google Maps.
    Prioriza as mesmas coordenadas usadas na API (origem/destino do trecho).
    """
    return _coordenadas_trecho_api(trecho_config) or _extrair_coordenadas(trecho_config)


def _montar_descricao_trecho_consultado(trecho_config: dict, coordenadas: Optional[tuple]) -> str:
    """
    Monta descricao explicita da secao ponto a ponto consultada (auditoria).
    Inclui rodovia, sentido e coordenadas origem -> destino para evitar ambiguidade
    em trechos com intersecoes (ex.: BR-116 que cruza outras BRs).
    """
    partes = []
    rodovia = trecho_config.get("rodovia", "").strip()
    sentido = trecho_config.get("sentido", "").strip()
    if rodovia:
        partes.append(rodovia)
    if sentido:
        partes.append(sentido)

    if coordenadas:
        lat_o, lng_o, lat_d, lng_d = coordenadas
        partes.append(f"Origem: {lat_o:.6f},{lng_o:.6f}")
        partes.append(f"Destino: {lat_d:.6f},{lng_d:.6f}")
        # Nomes dos locais do primeiro/ultimo ponto dos segmentos (se existirem)
        segmentos = trecho_config.get("segmentos", [])
        if segmentos:
            pts_ini = segmentos[0].get("pontos_referencia", [])
            pts_fim = segmentos[-1].get("pontos_referencia", [])
            if pts_ini and pts_fim:
                loc_orig = pts_ini[0].get("local", "").strip()
                loc_dest = pts_fim[-1].get("local", "").strip()
                if loc_orig or loc_dest:
                    partes.append(f"({loc_orig or '?'} → {loc_dest or '?'})")
    if not partes:
        return ""
    return " | ".join(partes)


def gerar_link_waze(origem: str, destino: str, coordenadas: tuple = None) -> str:
    """Gera link do Waze para o trecho.
    Usa as mesmas coordenadas de destino da consulta da API (ponto a ponto).
    Nota: a API do Waze nao suporta rota origem+destino; o link abre navegacao ate o destino.
    Para rota completa use o link do Google Maps.
    """
    if coordenadas:
        lat_o, lng_o, lat_d, lng_d = coordenadas
        return f"https://www.waze.com/ul?ll={lat_d},{lng_d}&navigate=yes"
    return f"https://www.waze.com/ul?q={quote(destino)}&navigate=yes"


def gerar_link_gmaps(origem: str, destino: str, coordenadas: tuple = None) -> str:
    """Gera link do Google Maps para o trecho ponto a ponto (origem -> destino).
    Usa as mesmas coordenadas enviadas à API para garantir que a rota aberta
    seja exatamente a seção consultada. Abre com camada de tráfego visível.
    """
    base = "https://www.google.com/maps/dir/?api=1"
    if coordenadas:
        lat_o, lng_o, lat_d, lng_d = coordenadas
        return (
            f"{base}&origin={lat_o},{lng_o}"
            f"&destination={lat_d},{lng_d}"
            f"&travelmode=driving"
            f"&layer=traffic"
        )

    return (
        f"{base}&origin={quote(origem, safe='')}"
        f"&destination={quote(destino, safe='')}"
        f"&travelmode=driving"
        f"&layer=traffic"
    )


def _normalizar_chave(texto: str) -> str:
    base = unicodedata.normalize("NFKD", str(texto or ""))
    sem_acento = "".join(ch for ch in base if not unicodedata.combining(ch))
    return _ESPACO_PATTERN.sub(" ", sem_acento).strip().lower()


def _eh_fragmento_ruido(fragmento: str) -> bool:
    frag = _normalizar_chave(fragmento)
    if not frag:
        return True

    frag_alpha = re.sub(r"[^a-z]", "", frag)
    if any(token in frag_alpha for token in _RUIDO_TOKENS):
        return True

    if ":" in frag:
        prefixo = frag.split(":", 1)[0].strip()
        prefixo_alpha = re.sub(r"[^a-z]", "", prefixo)
        if (
            prefixo_alpha in _PREFIXO_RUIDO
            or (prefixo_alpha.startswith("in") and prefixo_alpha.endswith("cio"))
        ):
            return True

    return False


def _compactar_descricao_operacional(texto: str) -> str:
    """Limpa descricao de incidente, removendo ruido e duplicatas."""
    bruto = _TAG_PATTERN.sub("", str(texto or ""))
    bruto = re.sub(r"\b(?:in[ií]cio|fim|start|end)\s*:[^|;]*", "", bruto, flags=re.IGNORECASE)
    bruto = re.sub(r"\balertas?\b[^|;]*", "", bruto, flags=re.IGNORECASE)
    partes = []
    vistos = set()

    for bloco in re.split(r"[|;]", bruto):
        bloco_limpo = _ESPACO_PATTERN.sub(" ", bloco).strip(" ,.-")
        bloco_limpo = re.sub(
            r"\s*-\s*(?:in\S*cio|fim|start|end)\s*:\s*.*$",
            "",
            bloco_limpo,
            flags=re.IGNORECASE,
        ).strip(" ,.-")
        if _eh_fragmento_ruido(bloco_limpo):
            continue
        chave = _normalizar_chave(bloco_limpo)
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        partes.append(bloco_limpo)

    obs = ", ".join(partes)
    return obs


def _construir_resumo_velocidade(
    pct_slow: float, pct_jam: float, zonas: list,
) -> str:
    """Monta resumo em portugues sobre a velocidade da rota."""
    if pct_jam + pct_slow == 0:
        return "Rota com fluxo livre"

    partes_pct = []
    if pct_slow > 0:
        partes_pct.append(f"{pct_slow:.0f}% lento")
    if pct_jam > 0:
        partes_pct.append(f"{pct_jam:.0f}% parado")
    pct_texto = ", ".join(partes_pct)

    if zonas:
        local_texto = " e ".join(zonas)
    else:
        local_texto = "rota"

    if pct_jam >= 20:
        prefixo = "Congestionamento intenso"
    elif pct_jam > 0:
        prefixo = "Congestionamento"
    else:
        prefixo = "Transito lento"

    return f"{prefixo} no {local_texto} ({pct_texto})"


def _analisar_speed_intervals(intervals: list) -> Optional[dict]:
    """
    Analisa speedReadingIntervals do Google Routes API v2.

    Calcula a proporcao de cada velocidade (NORMAL, SLOW, TRAFFIC_JAM)
    e identifica a localizacao relativa do congestionamento.

    Args:
        intervals: Lista de dicts com startPolylinePointIndex,
                   endPolylinePointIndex e speed.

    Returns:
        Dict com analise ou None se dados insuficientes.
    """
    if not intervals or not isinstance(intervals, list):
        return None

    total_pontos = 0
    contagem = {"NORMAL": 0, "SLOW": 0, "TRAFFIC_JAM": 0}
    zonas_raw = []

    for iv in intervals:
        if not isinstance(iv, dict):
            continue
        inicio = iv.get("startPolylinePointIndex", 0) or 0
        fim = iv.get("endPolylinePointIndex", 0) or 0
        speed = iv.get("speed", "NORMAL") or "NORMAL"

        segmento = max(0, fim - inicio)
        if segmento == 0:
            continue

        if fim > total_pontos:
            total_pontos = fim

        if speed in contagem:
            contagem[speed] += segmento
        else:
            contagem["NORMAL"] += segmento

        if speed in ("SLOW", "TRAFFIC_JAM"):
            zonas_raw.append((inicio, fim))

    if total_pontos == 0:
        return None

    pct_normal = round(contagem["NORMAL"] / total_pontos * 100, 1)
    pct_slow = round(contagem["SLOW"] / total_pontos * 100, 1)
    pct_jam = round(contagem["TRAFFIC_JAM"] / total_pontos * 100, 1)

    tem_congestionamento = (pct_slow + pct_jam) > 0

    zona_flags = {"inicial": False, "central": False, "final": False}
    for inicio, fim in zonas_raw:
        for pt in (inicio, fim):
            frac = pt / total_pontos
            if frac <= 0.33:
                zona_flags["inicial"] = True
            elif frac <= 0.66:
                zona_flags["central"] = True
            else:
                zona_flags["final"] = True

    zonas_congestionamento = [
        f"trecho {nome}"
        for nome in ("inicial", "central", "final")
        if zona_flags[nome]
    ]

    resumo = _construir_resumo_velocidade(pct_slow, pct_jam, zonas_congestionamento)

    return {
        "pct_normal": pct_normal,
        "pct_slow": pct_slow,
        "pct_jam": pct_jam,
        "tem_congestionamento": tem_congestionamento,
        "zonas_congestionamento": zonas_congestionamento,
        "resumo": resumo,
    }


def _formatar_local_ocorrencia(ocorrencia: dict, local_padrao: str) -> str:
    """Monta localizacao textual da ocorrencia com prioridade para KM/Local."""
    localizacao = str(ocorrencia.get("localizacao_precisa", "") or "").strip()
    if localizacao:
        return localizacao

    km = ocorrencia.get("km_estimado")
    trecho = str(ocorrencia.get("trecho_especifico", "") or "").strip()

    km_txt = ""
    if km is not None:
        try:
            km_txt = f"KM {float(km):.1f}"
        except (TypeError, ValueError):
            km_txt = f"KM {km}"

    if km_txt and trecho:
        return f"{km_txt} - {trecho}"
    if km_txt:
        return km_txt
    if trecho:
        return trecho
    return local_padrao


_TERMOS_GENERICOS_INTERDICAO = (
    "fechado", "fechada", "estrada fechada", "road closed", "via fechada",
    "interditado", "interditada", "bloqueado", "bloqueada", "totalmente bloqueada",
    "bloqueio total", "fechamento total", "pista fechada",
    "interdição total", "interdicao total",
    "via totalmente interditada", "via totalmente interditado",
    "totalmente interditada", "totalmente interditado",
    "todas as faixas bloqueadas", "ambos os sentidos bloqueados",
)


def _extrair_motivo_interdicao(descricao: str) -> str:
    """
    Extrai o motivo operacional de uma descricao de interdicao.

    Remove termos genericos de fechamento (ex: "estrada fechada") para isolar
    a causa real. Se nao restar texto util, retorna o fallback padrao.
    """
    if not descricao:
        return "motivo nao informado pela fonte"

    texto = _compactar_descricao_operacional(descricao)
    if not texto:
        return "motivo nao informado pela fonte"

    texto_lower = texto.lower()
    for termo in _TERMOS_GENERICOS_INTERDICAO:
        texto_lower = texto_lower.replace(termo, "")

    # Remove pontuacao residual e espacos extras
    motivo = " ".join(texto_lower.split()).strip(" .,;:-")
    return motivo if motivo else "motivo nao informado pela fonte"


def _resolver_bloqueio_escopo(ocorrencia: dict) -> str:
    escopo = str(ocorrencia.get("bloqueio_escopo", "") or "").strip().lower()
    if escopo in {"total", "parcial", "nenhum"}:
        return escopo

    cat_norm = _normalizar_chave(ocorrencia.get("categoria", ""))
    if cat_norm == "interdicao":
        return "total"

    if ocorrencia.get("road_closed"):
        return "total"

    if cat_norm == "bloqueio parcial":
        return "parcial"

    return "nenhum"


def _motivo_ocorrencia(ocorrencia: dict, fallback: str = "") -> str:
    causa = str(ocorrencia.get("causa_detectada", "") or "").strip().lower()
    descricao = str(ocorrencia.get("descricao", "") or "")
    motivo = _extrair_motivo_interdicao(descricao)
    if motivo and motivo != "motivo nao informado pela fonte":
        return motivo

    if causa == "acidente":
        return "acidente"
    if causa == "obra":
        return "obra na pista"
    if causa == "risco":
        return "risco na pista"
    if causa == "clima":
        return "condicoes climaticas"

    return fallback or "motivo nao informado pela fonte"


def _montar_contexto_ocorrencias(ocorrencias: list, local_padrao: str) -> str:
    """Gera observacao com todas as ocorrencias relevantes do trecho."""
    if not ocorrencias:
        return ""

    def _score(oc):
        return (
            int(oc.get("severidade_id", 1)),
            1 if oc.get("fonte") == "HERE" else 0,
        )

    partes = []
    vistos = set()
    for oc in sorted(ocorrencias, key=_score, reverse=True):
        categoria = str(oc.get("categoria", "Ocorrencia") or "Ocorrencia").strip()
        fonte = str(oc.get("fonte", "") or "").strip()
        local = _formatar_local_ocorrencia(oc, local_padrao)
        descricao_raw = oc.get("descricao", "")

        escopo_bloqueio = _resolver_bloqueio_escopo(oc)

        # Interdicao e reservada para fechamento total da via
        cat_norm = _normalizar_chave(categoria)
        if cat_norm == "interdicao":
            prefixo = "Interdição Total"
            if fonte:
                prefixo += f" ({fonte})"
            motivo = _motivo_ocorrencia(oc)
            chave = _normalizar_chave(f"{prefixo}|{local}|{motivo}")
            if chave and chave not in vistos:
                vistos.add(chave)
                partes.append(f"{prefixo} em {local}: {motivo}")
            continue

        if cat_norm == "bloqueio parcial":
            prefixo = "Bloqueio Parcial"
            if fonte:
                prefixo += f" ({fonte})"
            motivo = _compactar_descricao_operacional(descricao_raw)
            if not motivo:
                motivo = "faixa fechada, tráfego segue com retenção"
            chave = _normalizar_chave(f"{prefixo}|{local}|{motivo}|{escopo_bloqueio}")
            if chave and chave not in vistos:
                vistos.add(chave)
                partes.append(f"{prefixo} em {local}: {motivo}")
            continue

        descricao = _compactar_descricao_operacional(descricao_raw)

        chave = _normalizar_chave(f"{categoria}|{local}|{descricao}|{fonte}")
        if not chave or chave in vistos:
            continue
        vistos.add(chave)

        prefixo = f"{categoria}"
        if fonte:
            prefixo += f" ({fonte})"

        if descricao:
            partes.append(f"{prefixo} em {local}: {descricao}")
        else:
            partes.append(f"{prefixo} em {local}")

    return " | ".join(partes)


def correlacionar_trecho(
    trecho_config: dict,
    gmaps_data: Optional[dict] = None,
    here_incidentes: Optional[list] = None,
    here_fluxo: Optional[dict] = None,
    tomtom_incidentes: Optional[list] = None,
    tomtom_fluxo: Optional[dict] = None,
) -> dict:
    """
    Correlaciona dados de todas as fontes para um unico trecho.

    Produz um resultado unificado com:
    - status: Normal / Moderado / Intenso
    - ocorrencia: tipo mais relevante encontrado
    - descricao: texto combinado mais informativo
    - fontes: quais fontes contribuiram
    - links: Waze + Google Maps para referencia

    Returns:
        dict pronto para o relatorio Excel
    """
    nome = trecho_config.get("nome", "")
    origem = trecho_config.get("origem", "")
    destino = trecho_config.get("destino", "")
    rodovia = trecho_config.get("rodovia", "")

    if not nome:
        logger.error("trecho_config sem campo 'nome': %s", list(trecho_config.keys()))

    coords_links = _coordenadas_para_links(trecho_config)
    resultado = {
        "trecho": nome,
        "rodovia": rodovia,
        "tipo": trecho_config.get("tipo", "federal"),
        "sentido": trecho_config.get("sentido", ""),
        "concessionaria": trecho_config.get("concessionaria", ""),
        "link_waze": gerar_link_waze(origem, destino, coords_links),
        "link_gmaps": gerar_link_gmaps(origem, destino, coords_links),
        "trecho_consultado_descricao": _montar_descricao_trecho_consultado(trecho_config, coords_links),
        "trecho_consultado_origem": f"{coords_links[0]:.6f},{coords_links[1]:.6f}" if coords_links else "",
        "trecho_consultado_destino": f"{coords_links[2]:.6f},{coords_links[3]:.6f}" if coords_links else "",
        "status": "Sem dados",
        "ocorrencia": "",
        "km_ocorrencia": None,
        "trecho_especifico": "",
        "localizacao_precisa": "",
        "duracao_normal_min": 0,
        "duracao_transito_min": 0,
        "atraso_min": 0,
        "distancia_km": 0,
        "jam_factor": 0,
        "descricao": "",
        "fontes_utilizadas": [],
        "fontes_confirmacao": "",
        "confianca": "Baixa",
        "acao_recomendada": "Monitorar",
        "incidentes_detalhados": [],
        "consultado_em": datetime.now(_BRT).strftime("%Y-%m-%d %H:%M:%S"),
    }

    _fontes = set()
    descricao_partes = []
    ocorrencias_encontradas = []
    analise_velocidade = None
    _google_status = "Sem dados"
    _here_flow_status = "Sem dados"
    _here_flow_status_raw = "Sem dados"

    # ===== 1. DADOS GOOGLE MAPS (duracao/distancia) =====
    if gmaps_data and gmaps_data.get("status") != "Erro":
        _fontes.add("Google Maps")
        resultado["duracao_normal_min"] = gmaps_data.get("duracao_normal_min", 0)
        resultado["duracao_transito_min"] = gmaps_data.get("duracao_transito_min", 0)
        resultado["atraso_min"] = gmaps_data.get("atraso_min", 0)
        resultado["distancia_km"] = gmaps_data.get("distancia_km", 0)

        gmaps_status = gmaps_data.get("status", "Sem dados")
        _google_status = gmaps_status
        resultado["status"] = gmaps_status

        atraso = gmaps_data.get("atraso_min", 0) or 0
        if atraso > 0:
            normal = gmaps_data.get("duracao_normal_min", 0)
            atual = gmaps_data.get("duracao_transito_min", 0)
            descricao_partes.append(
                f"Atraso de ~{atraso} min "
                f"(normal: {normal}min, atual: {atual}min)"
            )

        # Analise de velocidade por segmentos (speedReadingIntervals)
        speed_intervals = gmaps_data.get("traffic_on_polyline", [])
        analise_velocidade = _analisar_speed_intervals(speed_intervals)

    # ===== 2. HERE FLOW (status mais preciso) =====
    if here_fluxo and here_fluxo.get("status") != "Sem dados":
        _fontes.add("HERE Flow")
        resultado["jam_factor"] = here_fluxo.get("jam_factor", 0)
        resultado["jam_factor_max"] = here_fluxo.get("jam_factor_max", 0)
        resultado["segmentos_congestionados"] = here_fluxo.get("segmentos_congestionados", 0)
        resultado["pct_congestionado"] = here_fluxo.get("pct_congestionado", 0)

        here_status = here_fluxo.get("status", "Sem dados")
        _here_flow_status = here_status        # status RAW antes de qualquer promocao
        _here_flow_status_raw = here_status    # preservado para conflito de fontes
        if here_status != "Sem dados":
            resultado["status"] = here_status

        # Promove status quando a media dilui congestionamento localizado.
        # Ex: 50km jam=8 + 380km jam=0.5 → media 1.37 "Normal", mas max=8 → "Intenso"
        # Politica conservadora: exige cobertura real de segmentos para evitar
        # que um unico ponto severo infle o status de toda a rota.
        jam_max = here_fluxo.get("jam_factor_max", 0)
        segs_congest = here_fluxo.get("segmentos_congestionados", 0)
        pct_congest = here_fluxo.get("pct_congestionado", 0) or 0

        if (
            jam_max >= 8
            and segs_congest >= 3
            and pct_congest >= 20
            and resultado["status"] in ("Normal", "Moderado")
        ):
            resultado["status"] = "Intenso"
            _here_flow_status = "Intenso"
            descricao_partes.append(
                f"Congestionamento severo em {segs_congest} segmento(s) (jam max: {jam_max}, {pct_congest:.0f}% do trecho)"
            )
        elif (
            jam_max >= 5
            and segs_congest >= 2
            and pct_congest >= 10
            and resultado["status"] == "Normal"
        ):
            resultado["status"] = "Moderado"
            _here_flow_status = "Moderado"
            descricao_partes.append(
                f"Congestionamento localizado em {segs_congest} segmento(s) (jam max: {jam_max})"
            )

        vel_atual = here_fluxo.get("velocidade_atual_kmh", 0)
        vel_livre = here_fluxo.get("velocidade_livre_kmh", 0)
        if vel_atual > 0:
            descricao_partes.append(
                f"Velocidade: {vel_atual} km/h (livre: {vel_livre} km/h)"
            )

    # ===== 3. HERE INCIDENTS (ocorrencias — PRIORIDADE MAXIMA) =====
    if here_incidentes:
        _fontes.add("HERE Incidents")

        for inc in here_incidentes:
            resultado["incidentes_detalhados"].append(inc)

            cat = inc.get("categoria", "")
            desc = inc.get("descricao", "")

            if cat:
                ocorrencias_encontradas.append({
                    "categoria": cat,
                    "severidade_id": inc.get("severidade_id", 1),
                    "descricao": desc,
                    "fonte": "HERE",
                    "km_estimado": inc.get("km_estimado"),
                    "trecho_especifico": inc.get("trecho_especifico", ""),
                    "localizacao_precisa": inc.get("localizacao_precisa", ""),
                    "road_closed": inc.get("road_closed", False),
                    "bloqueio_escopo": inc.get("bloqueio_escopo", ""),
                    "causa_detectada": inc.get("causa_detectada", ""),
                })

            if desc:
                descricao_partes.append(f"[HERE] {desc}")

    # Propaga localizacao precisa do incidente mais severo
    if here_incidentes:
        melhor_inc = max(
            here_incidentes,
            key=lambda i: i.get("severidade_id", 0),
            default=None,
        )
        if melhor_inc and melhor_inc.get("km_estimado") is not None:
            resultado["km_ocorrencia"] = melhor_inc["km_estimado"]
            resultado["trecho_especifico"] = melhor_inc.get("trecho_especifico", "")
            resultado["localizacao_precisa"] = melhor_inc.get("localizacao_precisa", "")
            resultado["confianca_localizacao"] = melhor_inc.get("confianca_localizacao", 0.0)

    # ===== 4. INCIDENTES TOMTOM =====
    if tomtom_incidentes:
        _fontes.add("TomTom Incidents")
        for inc in tomtom_incidentes:
            resultado["incidentes_detalhados"].append(inc)
            cat = inc.get("categoria", "")
            if cat:
                ocorrencias_encontradas.append({
                    "categoria": cat,
                    "severidade_id": inc.get("severidade_id", 1),
                    "descricao": inc.get("descricao", ""),
                    "fonte": "TomTom",
                    "km_estimado": inc.get("km_estimado"),
                    "trecho_especifico": inc.get("trecho_especifico", ""),
                    "localizacao_precisa": inc.get("localizacao_precisa", ""),
                    "bloqueio_escopo": inc.get("bloqueio_escopo", ""),
                    "causa_detectada": inc.get("causa_detectada", ""),
                })

    # ===== 5. FLUXO TOMTOM =====
    if tomtom_fluxo and tomtom_fluxo.get("status") not in ("Sem dados", None):
        _fontes.add("TomTom Flow")
        tt_status = tomtom_fluxo.get("status", "Normal")
        tt_jam = tomtom_fluxo.get("jam_factor", 0)

        # Merge: TomTom Flow complementa HERE Flow — mais severo vence
        nivel_atual = _STATUS_NIVEL.get(resultado["status"].lower(), -1)
        nivel_tt = _STATUS_NIVEL.get(tt_status.lower(), -1)
        if nivel_tt > nivel_atual:
            resultado["status"] = tt_status

        # Road closure do TomTom promove para Parado
        if tomtom_fluxo.get("road_closure"):
            resultado["status"] = "Parado"

    # ===== DECISAO FINAL: OCORRENCIA =====
    ocorrencia_principal = _decidir_ocorrencia(ocorrencias_encontradas)
    resultado["ocorrencia"] = ocorrencia_principal
    resultado["ocorrencia_principal"] = ocorrencia_principal

    # Sem incidente explicito, mas fluxo ruim -> marca engarrafamento operacional
    if not resultado["ocorrencia"] and resultado.get("jam_factor", 0) >= 5:
        resultado["ocorrencia"] = "Engarrafamento"
        ocorrencias_encontradas.append({
            "categoria": "Engarrafamento",
            "severidade_id": 2,
            "descricao": f"Jam Factor {resultado.get('jam_factor')}",
            "fonte": "HERE Flow",
            "km_estimado": resultado.get("km_ocorrencia"),
            "trecho_especifico": resultado.get("trecho_especifico", ""),
            "localizacao_precisa": resultado.get("localizacao_precisa", ""),
        })

    # Sem incidente mas Google mostra atraso significativo -> infere engarrafamento
    if not resultado["ocorrencia"] and resultado.get("atraso_min", 0) >= 15:
        resultado["ocorrencia"] = "Engarrafamento"
        ocorrencias_encontradas.append({
            "categoria": "Engarrafamento",
            "severidade_id": 1,
            "descricao": f"Atraso {resultado['atraso_min']} min",
            "fonte": "Google Maps",
            "km_estimado": resultado.get("km_ocorrencia"),
            "trecho_especifico": resultado.get("trecho_especifico", ""),
            "localizacao_precisa": resultado.get("localizacao_precisa", ""),
        })

    # Se ha ocorrencia grave mas status nao reflete, promove
    ocorrencia_norm = _normalizar_chave(resultado["ocorrencia"])
    if ocorrencia_norm in ("colisao", "interdicao") and resultado["status"] in ("Normal", "Sem dados"):
        resultado["status"] = "Intenso"
    elif ocorrencia_norm == "bloqueio parcial":
        # Bloqueio parcial: status depende do jam_factor HERE (faixa fechada, mas trafego passa)
        jam_bp = resultado.get("jam_factor_max", 0) or resultado.get("jam_factor", 0)
        if jam_bp >= 8 and resultado["status"] in ("Normal", "Sem dados"):
            resultado["status"] = "Intenso"
        elif jam_bp >= 5 and resultado["status"] in ("Normal", "Sem dados"):
            resultado["status"] = "Moderado"
        # jam < 5 sem dados de fluxo: nao promove, permanece Normal
    elif ocorrencia_norm in ("obras na pista", "engarrafamento") and resultado["status"] in ("Normal", "Sem dados"):
        resultado["status"] = "Moderado"

    # ===== DETECCAO DE CONFLITO ENTRE FONTES =====
    # Usa status_here_raw (antes de promocao por jam_factor) para refletir fonte real
    conflito = _detectar_conflito_fontes(
        here_status=_here_flow_status_raw,
        google_status=_google_status,
        atraso_min=resultado.get("atraso_min", 0) or 0,
        jam_factor=resultado.get("jam_factor", 0) or 0,
    )
    resultado["conflito_fontes"] = bool(conflito)
    resultado["conflito_detalhe"] = conflito.get("detalhe", "") if conflito else ""
    resultado["conflito_grau"] = conflito.get("grau", "") if conflito else ""

    if conflito:
        logger.warning(
            "Conflito de fontes em [%s]: %s", nome, conflito["detalhe"]
        )

    # ===== CONFIANCA / VALIDACAO =====
    confianca, fontes_conf, acao = _avaliar_confianca(
        resultado, ocorrencias_encontradas, conflito=conflito,
    )
    resultado["confianca"] = confianca
    resultado["fontes_confirmacao"] = ", ".join(fontes_conf) if fontes_conf else ""
    resultado["acao_recomendada"] = acao

    # Converte set -> lista ordenada (deterministico)
    resultado["fontes_utilizadas"] = sorted(_fontes)

    # Multi-ocorrencia: exibe todas as categorias na coluna Ocorrencia
    if len(ocorrencias_encontradas) > 1:
        resultado["ocorrencia"] = _formatar_ocorrencias_display(
            ocorrencias_encontradas
        )

    # ===== DECISAO FINAL: DESCRICAO =====
    resultado["descricao"] = _gerar_observacao_detalhada(
        resultado, ocorrencias_encontradas, descricao_partes,
        analise_velocidade=analise_velocidade,
    )

    # Adiciona alerta de conflito na observacao
    # Removido a pedido do usuario
    # if conflito:
    #     resultado["descricao"] += f" | ⚠ Fontes divergem: {conflito['detalhe']}"

    # Fallback se nao teve nenhuma fonte
    if not resultado["fontes_utilizadas"]:
        resultado["status"] = "Sem dados"
        resultado["descricao"] = "Nenhuma fonte de dados disponivel para este trecho"
        resultado["fontes_utilizadas"] = ["Nenhuma"]

    return resultado


def _gerar_observacao_detalhada(
    resultado: dict, ocorrencias: list, descricao_partes: list,
    analise_velocidade: Optional[dict] = None,
) -> str:
    """
    Gera observacao detalhada sobre a via, baseada nos dados reais.

    Prioridade:
    1. Se ha incidente HERE com descricao -> usa descricao real do incidente
    2. Se ha fluxo degradado (jam_factor) -> descreve transito com localizacao
    3. Se Google Maps mostra atraso -> menciona atraso
    4. Se tudo normal -> frase padrao curta
    Inclui KM, trecho_especifico e sentido quando disponiveis.
    """
    status = resultado.get("status", "Sem dados")
    ocorrencia = resultado.get("ocorrencia", "")
    rodovia = resultado.get("rodovia", "")
    jam = resultado.get("jam_factor", 0)
    loc_precisa = resultado.get("localizacao_precisa", "")
    km_ocorrencia = resultado.get("km_ocorrencia")
    trecho_especifico = str(resultado.get("trecho_especifico", "") or "").strip()
    sentido = str(resultado.get("sentido", "") or "").strip()
    ocorrencia_norm = _normalizar_chave(ocorrencia)

    # 1. Ocorrencias detectadas -> listar todas para contexto operacional.
    contexto_ocorrencias = _montar_contexto_ocorrencias(
        ocorrencias,
        loc_precisa if loc_precisa else rodovia,
    )
    if contexto_ocorrencias:
        extras = []
        atraso = resultado.get("atraso_min", 0)
        if atraso and atraso > 0:
            extras.append(f"Atraso estimado: {atraso} min")
        if analise_velocidade and analise_velocidade.get("tem_congestionamento"):
            zonas = analise_velocidade.get("zonas_congestionamento", [])
            if zonas:
                extras.append(f"Google: lentidao no {' e '.join(zonas)}")
        if extras:
            return f"{contexto_ocorrencias} | {' | '.join(extras)}"
        return contexto_ocorrencias

    # 2. Ocorrencia sem dados detalhados -> frase com localizacao enriquecida
    if loc_precisa:
        loc = loc_precisa
    else:
        partes_loc = []
        if km_ocorrencia is not None:
            try:
                partes_loc.append(f"KM {float(km_ocorrencia):.1f}")
            except (TypeError, ValueError):
                partes_loc.append(f"KM {km_ocorrencia}")
        if trecho_especifico:
            partes_loc.append(trecho_especifico)
        if not partes_loc:
            partes_loc.append(rodovia)
        loc = " - ".join(partes_loc)

    sentido_sufixo = f", sentido {sentido}" if sentido else ""

    if ocorrencia_norm == "engarrafamento":
        if jam >= 8:
            return f"Transito parado em {loc}{sentido_sufixo}, fluxo muito lento"
        elif jam >= 5:
            return f"Transito intenso em {loc}{sentido_sufixo}, com pontos de retencao"
        else:
            return f"Transito moderado em {loc}{sentido_sufixo}, circulacao com pequenas retencoes"

    if ocorrencia_norm in ("colisao", "acidente"):
        atraso_colisao = resultado.get("atraso_min", 0) or 0
        if atraso_colisao > 0:
            obs = f"Colisao no {loc}{sentido_sufixo} causando {atraso_colisao} min de atraso"
            jam_col = resultado.get("jam_factor", 0) or 0
            if jam_col >= 7:
                obs += " — fila significativa no trecho"
            return obs
        return f"Acidente em {loc}{sentido_sufixo}, transito impactado no trecho"

    if ocorrencia_norm == "obras na pista":
        return f"Obras em andamento em {loc}{sentido_sufixo}, circulacao com retencoes"

    if ocorrencia_norm == "interdicao":
        return f"Interdição total em {loc}{sentido_sufixo} — retorno obrigatorio, buscar rota alternativa"

    if ocorrencia_norm == "bloqueio parcial":
        jam_bp = resultado.get("jam_factor_max", 0) or resultado.get("jam_factor", 0)
        if jam_bp >= 8:
            return f"Bloqueio parcial (faixa fechada) em {loc}{sentido_sufixo} — retencao severa, transito intenso"
        elif jam_bp >= 5:
            return f"Bloqueio parcial (faixa fechada) em {loc}{sentido_sufixo} — retencao moderada no trecho"
        else:
            return f"Faixa fechada em {loc}{sentido_sufixo}, transito passa com reducao de velocidade"

    if ocorrencia_norm == "condicao climatica":
        return f"Condicoes climaticas afetando a via em {loc}{sentido_sufixo}"

    # 3. Sem ocorrencia mas com atraso Google Maps
    atraso = resultado.get("atraso_min", 0)
    if atraso and atraso > 10:
        zona_info = ""
        if analise_velocidade and analise_velocidade.get("tem_congestionamento"):
            zonas = analise_velocidade.get("zonas_congestionamento", [])
            if zonas:
                zona_info = f" ({' e '.join(zonas)})"
        return f"Atraso de ~{atraso} min em {loc}{sentido_sufixo}{zona_info}, transito {status.lower()}"

    # 4. Fluxo degradado sem ocorrencia especifica
    zona_sufixo = ""
    if analise_velocidade and analise_velocidade.get("tem_congestionamento"):
        zonas = analise_velocidade.get("zonas_congestionamento", [])
        if zonas:
            zona_sufixo = f", lentidao no {' e '.join(zonas)}"

    if status == "Moderado":
        return f"Trecho {loc}{sentido_sufixo} apresenta transito moderado{zona_sufixo}"
    if status == "Intenso":
        return f"Trecho {loc}{sentido_sufixo} apresenta transito intenso{zona_sufixo}"
    if status == "Parado":
        return f"Transito parado no trecho {loc}{sentido_sufixo}{zona_sufixo}"

    # 5. Tudo normal
    if status == "Normal":
        return f"Via {loc}{sentido_sufixo} sem anormalidades, fluxo livre"

    # 6. Sem dados
    return f"Rodovia {rodovia}{sentido_sufixo} segue com transito normal, sem alteracoes"


def _score_ocorrencia(oc: dict) -> int:
    """Score de prioridade para uma ocorrencia (maior = mais grave)."""
    cat_norm = _normalizar_chave(oc.get("categoria", ""))
    cat_peso = next((v for k, v in _PESOS_OCORRENCIA.items() if k in cat_norm), 10)
    sev_peso = oc.get("severidade_id", 1) * 5
    fonte_bonus = {"HERE": 10}.get(oc.get("fonte", ""), 0)
    return cat_peso + sev_peso + fonte_bonus


def _decidir_ocorrencia(ocorrencias: list) -> str:
    """
    Decide qual ocorrencia mostrar baseado em prioridade e severidade.

    Prioridade:
    1. Interdicao (mais grave — via bloqueada)
    2. Colisao / Acidente
    3. Obras na Pista
    4. Engarrafamento
    5. Condicao Climatica
    6. Vazio (sem ocorrencia)
    """
    if not ocorrencias:
        return ""
    melhor = max(ocorrencias, key=_score_ocorrencia)
    return melhor.get("categoria", "Incidente")


def _formatar_ocorrencias_display(ocorrencias: list) -> str:
    """
    Formata todas as ocorrencias para exibicao na coluna Ocorrencia.
    Retorna categorias unicas ordenadas por score, separadas por '; '.
    Ex: "Interdicao; Colisao; Obras na Pista"

    Caso especial: Colisao/Acidente + Engarrafamento coexistindo → "Engarrafamento por Colisao"
    (relacao causal explicita, sem duplicar os dois rotulos separados).
    """
    if not ocorrencias:
        return ""

    norms = {_normalizar_chave(oc.get("categoria", "")) for oc in ocorrencias}
    tem_colisao = bool(norms & {"colisao", "acidente"})
    tem_engarrafamento = bool(norms & {"engarrafamento", "congestionamento"})

    vistos = set()
    categorias = []
    for oc in sorted(ocorrencias, key=_score_ocorrencia, reverse=True):
        cat = oc.get("categoria", "Incidente")
        cat_norm = _normalizar_chave(cat)

        # Quando colisao + engarrafamento coexistem, funde em rotulo causal unico
        if tem_colisao and tem_engarrafamento:
            if cat_norm in ("engarrafamento", "congestionamento"):
                continue  # sera representado pelo rotulo combinado
            if cat_norm in ("colisao", "acidente") and "engarrafamento_por_colisao" not in vistos:
                vistos.add("engarrafamento_por_colisao")
                vistos.add(cat_norm)
                categorias.append("Engarrafamento por Colisao")
                continue

        if cat_norm and cat_norm not in vistos:
            vistos.add(cat_norm)
            categorias.append(cat)

    return "; ".join(categorias)


def _detectar_conflito_fontes(
    here_status: str,
    google_status: str,
    atraso_min: float = 0,
    jam_factor: float = 0,
) -> Optional[dict]:
    """
    Detecta conflito de status entre HERE Flow e Google Maps.

    Retorna dict com grau e detalhe do conflito, ou None se sem conflito.
    Conflito = diferenca >= 2 niveis entre as fontes (ex: Normal vs Intenso).
    Diferenca de 1 nivel (Normal vs Moderado) nao e conflito.
    """
    here_norm = _normalizar_chave(here_status)
    google_norm = _normalizar_chave(google_status)

    nivel_here = _STATUS_NIVEL.get(here_norm, -1)
    nivel_google = _STATUS_NIVEL.get(google_norm, -1)

    # Precisa de ambas as fontes com dados validos
    if nivel_here < 0 or nivel_google < 0:
        return None

    diff = abs(nivel_here - nivel_google)

    # Diferenca de 1 nivel e aceitavel (Normal vs Moderado)
    if diff < 2:
        return None

    # HERE Normal mas Google com atraso real significativo
    if nivel_here == 0 and atraso_min >= 15:
        return {
            "grau": "alto",
            "detalhe": (
                f"HERE indica Normal mas Google mostra atraso de {atraso_min} min"
            ),
            "here_status": here_status,
            "google_status": google_status,
        }

    # Google Normal mas HERE com jam factor alto
    if nivel_google == 0 and jam_factor >= 5:
        return {
            "grau": "alto",
            "detalhe": (
                f"Google indica Normal mas HERE mostra Jam Factor {jam_factor}"
            ),
            "here_status": here_status,
            "google_status": google_status,
        }

    # Conflito generico >= 2 niveis
    grau = "alto" if diff >= 3 else "moderado"
    return {
        "grau": grau,
        "detalhe": (
            f"HERE indica {here_status} mas Google indica {google_status}"
        ),
        "here_status": here_status,
        "google_status": google_status,
    }


def _avaliar_confianca(
    resultado: dict, ocorrencias: list, conflito: Optional[dict] = None,
) -> tuple:
    """
    Avalia confianca da ocorrencia para uso operacional.

    Quando ha conflito entre fontes, rebaixa confianca em 1 nivel
    e ajusta acao recomendada para incluir revalidacao.

    Retorna: (confianca, fontes_confirmacao, acao_recomendada)
    """
    status = resultado.get("status", "")
    ocorrencia_norm = _normalizar_chave(resultado.get("ocorrencia", ""))

    fontes = set()
    for oc in ocorrencias:
        fonte = oc.get("fonte", "")
        if fonte:
            fontes.add(fonte)

    if resultado.get("jam_factor", 0) > 0:
        fontes.add("HERE Flow")

    # Google Maps com atraso real conta como fonte de confirmacao
    if resultado.get("atraso_min", 0) > 0:
        fontes.add("Google Maps")

    fontes_ordenadas = sorted(fontes)
    total_fontes = len(fontes_ordenadas)

    if total_fontes >= 2:
        confianca = "Alta"
    elif total_fontes == 1:
        confianca = "Media"
    else:
        confianca = "Baixa"

    # Conflito entre fontes rebaixa confianca em 1 nivel
    if conflito:
        if confianca == "Alta":
            confianca = "Media"
        elif confianca == "Media":
            confianca = "Baixa"

    if ocorrencia_norm in ("interdicao", "colisao", "acidente"):
        if confianca == "Alta":
            acao = "Acionar contingencia logistica"
        elif confianca == "Media":
            acao = "Validar em Waze/Maps e ajustar rota"
        else:
            acao = "Revisao manual imediata"
    elif ocorrencia_norm in ("obras na pista", "engarrafamento") or status in ("Moderado", "Intenso", "Parado"):
        if confianca == "Alta":
            acao = "Replanejar janela de entrega"
        else:
            acao = "Monitorar e revalidar em 15 min"
    else:
        if conflito:
            acao = "Monitorar e revalidar em 15 min"
        else:
            acao = "Operacao normal"

    return confianca, fontes_ordenadas, acao


def correlacionar_todos(
    trechos: list,
    gmaps_resultados: list = None,
    here_dados: dict = None,
    tomtom_dados: dict = None,
) -> list:
    """
    Correlaciona dados de todas as fontes para todos os trechos.

    Args:
        trechos: Lista de trechos do config
        gmaps_resultados: Resultados do Google Maps (lista)
        here_dados: Dict com "incidentes" e "fluxo" do HERE
        tomtom_dados: Dict com "incidentes" e "fluxo" do TomTom

    Returns:
        Lista de dicts correlacionados, prontos para o relatorio
    """
    resultados = []

    # Indexa Google Maps por nome normalizado do trecho
    gmaps_index = {}
    if gmaps_resultados:
        for r in gmaps_resultados:
            gmaps_index[_normalizar_chave(r.get("trecho", ""))] = r

    # HERE ja vem indexado por nome
    here_incidentes = (here_dados or {}).get("incidentes", {})
    here_fluxo = (here_dados or {}).get("fluxo", {})
    tomtom_inc = (tomtom_dados or {}).get("incidentes", {})
    tomtom_fluxo = (tomtom_dados or {}).get("fluxo", {})

    for trecho in trechos:
        nome = trecho.get("nome", "")
        nome_norm = _normalizar_chave(nome)

        correlacionado = correlacionar_trecho(
            trecho_config=trecho,
            gmaps_data=gmaps_index.get(nome_norm),
            here_incidentes=here_incidentes.get(nome, []),
            here_fluxo=here_fluxo.get(nome, {}),
            tomtom_incidentes=tomtom_inc.get(nome, []),
            tomtom_fluxo=tomtom_fluxo.get(nome, {}),
        )

        resultados.append(correlacionado)

        # Log resumido
        status = correlacionado["status"]
        oc = correlacionado["ocorrencia"]
        fontes = ", ".join(correlacionado["fontes_utilizadas"])
        log_msg = f"  [{nome}] Status={status}"
        if oc:
            log_msg += f" | Ocorrencia={oc}"
        log_msg += f" | Fontes: {fontes}"
        logger.info(log_msg)

    return resultados
