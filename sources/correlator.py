"""
Motor de Correlacao de Dados.

Cruza dados de multiplas fontes sem duplicar.

Logica:
1. Para cada trecho, coleta dados de: Google Maps, HERE
2. Determina o STATUS final baseado na fonte mais confiavel
3. Determina a OCORRENCIA final priorizando fontes com detalhes
4. Monta a DESCRICAO combinada mais informativa
5. Gera links de referencia para Waze e Google Maps

Hierarquia de confianca para STATUS:
  HERE Flow (jam_factor) > Google Maps (duration_in_traffic)

Hierarquia de confianca para OCORRENCIAS:
  HERE Incidents > Inferencia Google Maps
"""
import logging
import re
import unicodedata
from datetime import datetime
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)
_TAG_PATTERN = re.compile(r"\[[^\]]+\]")
_ESPACO_PATTERN = re.compile(r"\s+")


def _extrair_coordenadas(trecho_config: dict) -> tuple:
    """
    Extrai coordenadas de origem e destino dos segmentos do trecho.

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

    return (p_origem["lat"], p_origem["lng"], p_destino["lat"], p_destino["lng"])


def gerar_link_waze(origem: str, destino: str, coordenadas: tuple = None) -> str:
    """Gera link do Waze para o trecho com origem e destino.

    Args:
        origem: Nome da cidade de origem
        destino: Nome da cidade de destino
        coordenadas: (lat_orig, lng_orig, lat_dest, lng_dest) ou None
    """
    if coordenadas:
        lat_o, lng_o, lat_d, lng_d = coordenadas
        return (
            f"https://www.waze.com/ul?ll={lat_d},{lng_d}&navigate=yes"
            f"&from=ll.{lat_o},{lng_o}"
        )
    return (
        f"https://www.waze.com/ul?q={quote(destino)}&navigate=yes"
        f"&from=q.{quote(origem)}"
    )


def gerar_link_gmaps(origem: str, destino: str, coordenadas: tuple = None) -> str:
    """Gera link do Google Maps para o trecho com camada de trafego.

    Args:
        origem: Nome da cidade de origem
        destino: Nome da cidade de destino
        coordenadas: (lat_orig, lng_orig, lat_dest, lng_dest) ou None
    """
    base = "https://www.google.com/maps/dir/?api=1"
    if coordenadas:
        lat_o, lng_o, lat_d, lng_d = coordenadas
        return (
            f"{base}&origin={lat_o},{lng_o}"
            f"&destination={lat_d},{lng_d}"
            f"&travelmode=driving&layer=traffic"
        )

    return (
        f"{base}&origin={quote(origem, safe='')}"
        f"&destination={quote(destino, safe='')}"
        f"&travelmode=driving&layer=traffic"
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
    if any(token in frag_alpha for token in ("alerta", "warning", "seguranca", "segurana", "safety")):
        return True

    if ":" in frag:
        prefixo = frag.split(":", 1)[0].strip()
        prefixo_alpha = re.sub(r"[^a-z]", "", prefixo)
        if (
            prefixo_alpha in ("inicio", "fim", "start", "end")
            or (prefixo_alpha.startswith("in") and prefixo_alpha.endswith("cio"))
        ):
            return True

    return False


def _compactar_descricao_operacional(texto: str, limite: int = 160) -> str:
    """Limpa descricao de incidente para caber no Excel sem ruido."""
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
        if len(partes) >= 2:
            break

    obs = ", ".join(partes)
    if len(obs) > limite:
        return obs[: limite - 3].rstrip() + "..."
    return obs


def correlacionar_trecho(
    trecho_config: dict,
    gmaps_data: Optional[dict] = None,
    here_incidentes: Optional[list] = None,
    here_fluxo: Optional[dict] = None,
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
    nome = trecho_config["nome"]
    rodovia = trecho_config.get("rodovia", "")

    resultado = {
        "trecho": nome,
        "rodovia": rodovia,
        "tipo": trecho_config.get("tipo", "federal"),
        "sentido": trecho_config.get("sentido", ""),
        "concessionaria": trecho_config.get("concessionaria", ""),
        "link_waze": gerar_link_waze(
            trecho_config["origem"], trecho_config["destino"],
            _extrair_coordenadas(trecho_config),
        ),
        "link_gmaps": gerar_link_gmaps(
            trecho_config["origem"], trecho_config["destino"],
            _extrair_coordenadas(trecho_config),
        ),
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
        "consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    descricao_partes = []
    ocorrencias_encontradas = []

    # ===== 1. DADOS GOOGLE MAPS (duracao/distancia) =====
    if gmaps_data and gmaps_data.get("status") != "Erro":
        resultado["fontes_utilizadas"].append("Google Maps")
        resultado["duracao_normal_min"] = gmaps_data.get("duracao_normal_min", 0)
        resultado["duracao_transito_min"] = gmaps_data.get("duracao_transito_min", 0)
        resultado["atraso_min"] = gmaps_data.get("atraso_min", 0)
        resultado["distancia_km"] = gmaps_data.get("distancia_km", 0)

        gmaps_status = gmaps_data.get("status", "Sem dados")
        resultado["status"] = gmaps_status

        atraso = gmaps_data.get("atraso_min", 0)
        if atraso > 0:
            normal = gmaps_data["duracao_normal_min"]
            atual = gmaps_data["duracao_transito_min"]
            descricao_partes.append(
                f"Atraso de ~{atraso} min "
                f"(normal: {normal}min, atual: {atual}min)"
            )

    # ===== 2. HERE FLOW (status mais preciso) =====
    if here_fluxo and here_fluxo.get("status") != "Sem dados":
        resultado["fontes_utilizadas"].append("HERE Flow")
        resultado["jam_factor"] = here_fluxo.get("jam_factor", 0)

        here_status = here_fluxo.get("status", "Sem dados")
        if here_status != "Sem dados":
            resultado["status"] = here_status

        vel_atual = here_fluxo.get("velocidade_atual_kmh", 0)
        vel_livre = here_fluxo.get("velocidade_livre_kmh", 0)
        if vel_atual > 0:
            descricao_partes.append(
                f"Velocidade: {vel_atual} km/h (livre: {vel_livre} km/h)"
            )

    # ===== 3. HERE INCIDENTS (ocorrencias — PRIORIDADE MAXIMA) =====
    if here_incidentes:
        resultado["fontes_utilizadas"].append("HERE Incidents")

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

    # ===== DECISAO FINAL: OCORRENCIA =====
    resultado["ocorrencia"] = _decidir_ocorrencia(ocorrencias_encontradas)

    # Sem incidente explicito, mas fluxo ruim -> marca engarrafamento operacional
    if not resultado["ocorrencia"] and resultado.get("jam_factor", 0) >= 5:
        resultado["ocorrencia"] = "Engarrafamento"
        ocorrencias_encontradas.append({
            "categoria": "Engarrafamento",
            "severidade_id": 2,
            "descricao": f"Jam Factor {resultado.get('jam_factor')}",
            "fonte": "HERE Flow",
        })

    # Se ha ocorrencia grave mas status era Normal, promove
    ocorrencia_norm = _normalizar_chave(resultado["ocorrencia"])
    if ocorrencia_norm in ("colisao", "interdicao") and resultado["status"] == "Normal":
        resultado["status"] = "Intenso"
    elif ocorrencia_norm in ("obras na pista", "engarrafamento") and resultado["status"] == "Normal":
        resultado["status"] = "Moderado"

    # ===== CONFIANCA / VALIDACAO =====
    confianca, fontes_conf, acao = _avaliar_confianca(resultado, ocorrencias_encontradas)
    resultado["confianca"] = confianca
    resultado["fontes_confirmacao"] = ", ".join(fontes_conf) if fontes_conf else ""
    resultado["acao_recomendada"] = acao

    # ===== DECISAO FINAL: DESCRICAO =====
    resultado["descricao"] = _gerar_observacao_breve(
        resultado, ocorrencias_encontradas, descricao_partes
    )

    # Fallback se nao teve nenhuma fonte
    if not resultado["fontes_utilizadas"]:
        resultado["status"] = "Sem dados"
        resultado["descricao"] = "Nenhuma fonte de dados disponivel para este trecho"
        resultado["fontes_utilizadas"].append("Nenhuma")

    return resultado


def _gerar_observacao_breve(resultado: dict, ocorrencias: list, descricao_partes: list) -> str:
    """
    Gera observacao breve e natural sobre a via, baseada nos dados reais.

    Prioridade:
    1. Se ha incidente HERE com descricao -> usa descricao real do incidente
    2. Se ha fluxo degradado (jam_factor) -> descreve transito com localizacao
    3. Se Google Maps mostra atraso -> menciona atraso
    4. Se tudo normal -> frase padrao curta
    """
    status = resultado.get("status", "Sem dados")
    ocorrencia = resultado.get("ocorrencia", "")
    rodovia = resultado.get("rodovia", "")
    jam = resultado.get("jam_factor", 0)
    loc_precisa = resultado.get("localizacao_precisa", "")
    ocorrencia_norm = _normalizar_chave(ocorrencia)

    # 1. Incidente real da HERE -> descricao direta com localizacao precisa
    for oc in ocorrencias:
        desc_raw = oc.get("descricao", "").strip()
        if desc_raw and oc.get("fonte") == "HERE" and len(desc_raw) > 5:
            obs = _compactar_descricao_operacional(desc_raw)
            if not obs:
                continue
            if loc_precisa:
                obs = f"{loc_precisa}: {obs}"
            if len(obs) > 170:
                obs = obs[:167] + "..."
            return obs

    # 2. Ocorrencia sem descricao detalhada -> frase com localizacao precisa
    loc = loc_precisa if loc_precisa else rodovia

    if ocorrencia_norm == "engarrafamento":
        if jam >= 8:
            return f"Transito parado em {loc}, fluxo muito lento"
        elif jam >= 5:
            return f"Transito intenso em {loc}, com pontos de retencao"
        else:
            return f"Transito moderado em {loc}, circulacao com pequenas retencoes"

    if ocorrencia_norm in ("colisao", "acidente"):
        return f"Acidente em {loc}, transito impactado no trecho"

    if ocorrencia_norm == "obras na pista":
        return f"Obras em andamento em {loc}, circulacao com retencoes"

    if ocorrencia_norm == "interdicao":
        return f"Via com interdicao em {loc}, buscar rota alternativa"

    if ocorrencia_norm == "condicao climatica":
        return f"Condicoes climaticas afetando a via em {loc}"

    # 3. Sem ocorrencia mas com atraso Google Maps
    atraso = resultado.get("atraso_min", 0)
    if atraso and atraso > 10:
        return f"Atraso de ~{atraso} min no trecho, transito {status.lower()}"

    # 4. Fluxo degradado sem ocorrencia especifica
    if status == "Moderado":
        return "Trecho apresenta transito moderado, com pontos de retencao"
    if status == "Intenso":
        return "Trecho apresenta transito intenso, com pontos de retencao"
    if status == "Parado":
        return "Transito parado no trecho, fluxo muito lento"

    # 5. Tudo normal
    if status == "Normal":
        return "Via sem anormalidades, fluxo livre"

    # 6. Sem dados
    return "Rodovia segue com transito normal, sem alteracoes"


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

    pesos = {
        "interdicao": 100,
        "colisao": 90,
        "acidente": 90,
        "obras na pista": 60,
        "condicao climatica": 50,
        "engarrafamento": 40,
        "ocorrencia": 10,
    }

    def score(oc):
        cat_norm = _normalizar_chave(oc.get("categoria", ""))
        cat_peso = pesos.get(cat_norm, 10)
        sev_peso = oc.get("severidade_id", 1) * 5
        fonte_bonus = {"HERE": 10}.get(oc.get("fonte", ""), 0)
        return cat_peso + sev_peso + fonte_bonus

    melhor = max(ocorrencias, key=score)
    return melhor["categoria"]


def _avaliar_confianca(resultado: dict, ocorrencias: list) -> tuple:
    """
    Avalia confianca da ocorrencia para uso operacional.
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

    fontes_ordenadas = sorted(fontes)
    total_fontes = len(fontes_ordenadas)

    if total_fontes >= 2:
        confianca = "Alta"
    elif total_fontes == 1:
        fonte_unica = fontes_ordenadas[0]
        if fonte_unica in ("HERE", "HERE Flow"):
            confianca = "Media"
        else:
            confianca = "Baixa"
    else:
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
        acao = "Operacao normal"

    return confianca, fontes_ordenadas, acao


def correlacionar_todos(
    trechos: list,
    gmaps_resultados: list = None,
    here_dados: dict = None,
) -> list:
    """
    Correlaciona dados de todas as fontes para todos os trechos.

    Args:
        trechos: Lista de trechos do config
        gmaps_resultados: Resultados do Google Maps (lista)
        here_dados: Dict com "incidentes" e "fluxo" do HERE

    Returns:
        Lista de dicts correlacionados, prontos para o relatorio
    """
    resultados = []

    # Indexa Google Maps por nome do trecho
    gmaps_index = {}
    if gmaps_resultados:
        for r in gmaps_resultados:
            gmaps_index[r.get("trecho", "")] = r

    # HERE ja vem indexado por nome
    here_incidentes = (here_dados or {}).get("incidentes", {})
    here_fluxo = (here_dados or {}).get("fluxo", {})

    for trecho in trechos:
        nome = trecho["nome"]

        correlacionado = correlacionar_trecho(
            trecho_config=trecho,
            gmaps_data=gmaps_index.get(nome),
            here_incidentes=here_incidentes.get(nome, []),
            here_fluxo=here_fluxo.get(nome, {}),
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
