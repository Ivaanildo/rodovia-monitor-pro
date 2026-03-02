# Algoritmos — RodoviaMonitor Pro

> Este documento descreve os algoritmos e lógicas internas do sistema, com fluxogramas em Mermaid.
> Para o contexto de arquitetura veja [ARQUITETURA.md](ARQUITETURA.md).
> Para análise detalhada de precisão veja [ANALISE_PRECISAO.md](../ANALISE_PRECISAO.md).

---

## Índice

1. [Normalização de rotas](#1-normalização-de-rotas)
2. [Coleta paralela e chunking](#2-coleta-paralela-e-chunking)
3. [HERE: corridor vs bbox](#3-here-corridor-vs-bbox)
4. [Downsampling RDP (Ramer-Douglas-Peucker)](#4-downsampling-rdp-ramer-douglas-peucker)
5. [Correlação de status e ocorrência](#5-correlação-de-status-e-ocorrência)
6. [Detecção de conflito entre fontes](#6-detecção-de-conflito-entre-fontes)
7. [Cálculo de confiança — DataAdvisor](#7-cálculo-de-confiança--dataadvisor)
8. [Estimativa de KM e trecho local](#8-estimativa-de-km-e-trecho-local)
9. [Classificação de trânsito Google](#9-classificação-de-trânsito-google)
10. [Classificação de fluxo HERE — Jam Factor](#10-classificação-de-fluxo-here--jam-factor)

---

## 1. Normalização de rotas

**Arquivo:** `main.py` — `_normalizar_rota_logistica(rota)`

Converte cada entrada do `rota_logistica.json` (formato `routes`) para o formato interno usado pelo sistema durante a coleta.

### Fluxograma

```mermaid
flowchart TD
    entrada["Entrada: rota do rota_logistica.json\n(routes[])"]
    temHere{Tem campo\nhere.origin/destination?}
    usaHere["origem/destino = here.origin / here.destination\n(string lat,lng)"]
    temLatLng{Tem lat/lng\nno hub?}
    usaLatLng["origem/destino = 'lat,lng'\n(coordenadas do hub)"]
    usaStr["origem/destino = str(orig/dest)\n(fallback texto)"]
    temNome{tem nome\norigem e destino?}
    retNone["return None\n(trecho ignorado)"]
    montaSegs["Monta segmentos com pontos_referencia:\norigem (km=0) + via[] + destino (km=distancia)"]
    temVia{Tem waypoints\nvia[] em here?}
    kmVia["KM estimado = distance_km * (i+1)/(n+1)\nse não: (i+1)*10 km"]
    montaTrecho["Saída: trecho interno\n{nome, origem, destino, rodovia, sentido, tipo,\nconcessionaria, segmentos, via_waypoints, limite_gap_km}"]

    entrada --> temHere
    temHere -->|Sim| usaHere
    temHere -->|Não| temLatLng
    temLatLng -->|Sim| usaLatLng
    temLatLng -->|Não| usaStr
    usaHere --> temNome
    usaLatLng --> temNome
    usaStr --> temNome
    temNome -->|Não| retNone
    temNome -->|Sim| montaSegs
    montaSegs --> temVia
    temVia -->|Sim| kmVia
    temVia -->|Não| montaTrecho
    kmVia --> montaTrecho
```

### Campos de saída (trecho interno)

| Campo | Conteúdo |
|-------|----------|
| `nome` | "HubOrigem -> HubDestino" |
| `origem` / `destino` | string `"lat,lng"` ou endereço |
| `rodovia` | `" / ".join(rodovia_logica)` |
| `segmentos` | lista de `{km, lat, lng, local}` (pontos_referencia) |
| `via_waypoints` | lista de `(lat, lng)` dos pontos intermediários |
| `limite_gap_km` | limite para identificação de trecho local (opcional) |

---

## 2. Coleta paralela e chunking

**Arquivo:** `main.py` — `executar_coleta()`, `_coletar_fonte()`

O orquestrador dispara as três fontes em paralelo usando `ThreadPoolExecutor`. A HERE Traffic aplica um chunking adicional para respeitar rate limits.

### Fluxograma do ciclo completo

```mermaid
flowchart TD
    inicio["main.py: executar_coleta()"]
    carrega["Carrega trechos normalizados\n(config[trechos])"]
    validaApis["Verifica APIs ativas\n(chave + enabled no config)"]
    subgraph paralelo ["ThreadPoolExecutor max_workers=2 (fontes)"]
        colHere["_coletar_fonte(HERE)\nhere_consultar()"]
        colGm["_coletar_fonte(Google)\ngmaps_consultar()"]
        colTt["_coletar_fonte(TomTom)\ntomtom_consultar()"]
    end
    aguarda["Aguarda todas as futures\n(timeout por fonte)"]
    correlaciona["correlacionar_todos()\n(status, ocorrência, conflito por trecho)"]
    advisor["DataAdvisor.enriquecer_dados()\n(confianca_pct, fonte_escolhida)"]
    persiste["_repo.salvar_ciclo()\n(INSERT ciclos + snapshots_rotas)"]
    excel["gerar_relatorio()\n(Excel em relatorios/)"]
    fimCiclo["Fim do ciclo\n(aguarda próximo agendamento)"]

    inicio --> carrega --> validaApis
    validaApis --> paralelo
    paralelo --> aguarda
    aguarda --> correlaciona --> advisor --> persiste --> excel --> fimCiclo
```

### Chunking HERE

```mermaid
flowchart TD
    hereStart["here_consultar(api_key, trechos, config)"]
    chunkSize["chunk_size = config.get('chunk_size', 7)\nchunk_delay_s = config.get('chunk_delay_s', 1.5)"]
    dividir["Dividir trechos em blocos de chunk_size"]
    subgraph loop ["Para cada chunk (sequencial)"]
        subgraph innerPool ["ThreadPoolExecutor max_workers=5"]
            proc1["_processar_trecho(trecho1)"]
            proc2["_processar_trecho(trecho2)"]
            procN["_processar_trecho(...)"]
        end
        delay["time.sleep(chunk_delay_s)\n(entre chunks)"]
    end
    resultado["Resultado: {incidentes: {nome: [...]}, fluxo: {nome: {...}}}"]

    hereStart --> chunkSize --> dividir --> loop --> resultado
    innerPool --> delay
```

> O chunking sequencial entre blocos garante que a HERE API não seja sobrecarregada.
> Dentro de cada chunk, os trechos são processados em paralelo (max 5 workers).

---

## 3. HERE: corridor vs bbox

**Arquivo:** `sources/here_traffic.py` — `_obter_corridor_ou_none()`, `consultar_incidentes()`

A HERE Traffic API aceita dois métodos de filtro espacial: **corridor** (polyline precisa) e **bbox** (retângulo aproximado). A escolha impacta diretamente a precisão dos dados.

### Fluxograma de decisão

```mermaid
flowchart TD
    inicio2["_processar_trecho(trecho)"]
    distancia{Distância haversine\norigem→destino\n> 500 km?}
    skipRouting["Pula Routing v8\n(ROTA_LONGA_SKIP_ROUTING_KM)"]
    chamRouting["Chama HERE Routing v8\n(origin, destination, via)\nreturn=polyline"]
    temPolyline{Polyline\nobtida?}
    contaPts{"Polyline cabe nos\nlimites da API?\n≤ 300 pts\n≤ 1200 chars?"}
    usaCorridor["Usa CORRIDOR\nraio 100 m\nfiltro incidentes: 150 m"]
    rdp["Tenta RDP downsampling\n(veja seção 4)"]
    cabeAposRdp{Cabe após\nRDP?}
    salvaPolyRef["Salva polyline original\ncomo polyline de referência"]
    usaBbox["Usa BBOX\npadding 20 km\nfiltro incidentes: 500 m"]
    filtroHaversine["Filtra incidentes por haversine:\ndistância à polyline de referência\n(pontos do config ou Routing v8)"]
    filtroTexto["Filtro texto: código da rodovia\n(ex: 'BR116' na descrição)"]
    resultado2["Incidentes filtrados + Flow por trecho"]

    inicio2 --> distancia
    distancia -->|Sim| skipRouting --> usaBbox
    distancia -->|Não| chamRouting
    chamRouting --> temPolyline
    temPolyline -->|Não| usaBbox
    temPolyline -->|Sim| contaPts
    contaPts -->|Sim| usaCorridor
    contaPts -->|Não| rdp
    rdp --> cabeAposRdp
    cabeAposRdp -->|Sim| usaCorridor
    cabeAposRdp -->|Não| salvaPolyRef --> usaBbox
    usaCorridor --> filtroTexto
    usaBbox --> filtroHaversine --> filtroTexto
    filtroTexto --> resultado2
```

### Comparativo corridor × bbox

| Aspecto | Corridor | BBox |
|---------|----------|------|
| % de rotas (atual) | ~25% | ~75% |
| Filtro espacial | 150 m da polyline | 500 m da polyline de referência |
| Precisão | Alta | Média |
| Quando usado | Rotas < 500 km com polyline < 300 pts | Demais casos |
| Risco | Incidentes fora da rota | Incidentes em vias adjacentes < 500 m |

---

## 4. Downsampling RDP (Ramer-Douglas-Peucker)

**Arquivo:** `sources/here_traffic.py` — `_rdp_simplify()`, `_downsample_polyline()`

Quando a polyline do Routing v8 excede 300 pontos ou 1200 caracteres, o RDP é aplicado iterativamente para reduzir pontos preservando a geometria (curvas, inflexões).

### Algoritmo

```mermaid
flowchart TD
    rdpIn["Entrada: polyline N pontos\nLimite: 300 pts / 1200 chars"]
    epsilon["Epsilon inicial = 50 m"]
    aplicaRdp["Aplica RDP com epsilon atual\n(preserva pontos de inflexão\ndistantes > epsilon da linha reta)"]
    cabeAgora{Resultado\n≤ 300 pts e\n≤ 1200 chars?}
    retornaRdp["Retorna polyline simplificada\n(geometria preservada)"]
    aumentaEps["epsilon *= 1.5\n(ex: 50m → 75m → 112m → ...)"]
    maxEps{epsilon >\n50.000 m?}
    fallback["Fallback: keep-every-N\n(stride uniforme)"]

    rdpIn --> epsilon --> aplicaRdp --> cabeAgora
    cabeAgora -->|Sim| retornaRdp
    cabeAgora -->|Não| aumentaEps --> maxEps
    maxEps -->|Não| aplicaRdp
    maxEps -->|Sim| fallback
```

> **Por que RDP e não stride fixo?** O stride remove pontos uniformemente, podendo simplificar uma curva a uma linha reta. O RDP prioriza pontos geometricamente significativos (ex.: entrada de trevo, curva de serra), resultando em corridor mais preciso com menos pontos.

---

## 5. Correlação de status e ocorrência

**Arquivo:** `sources/correlator.py` — `correlacionar_trecho()`

Combina os dados das três fontes em um único status e ocorrência por trecho, aplicando regras de prioridade e promoção.

### Fluxograma de status

```mermaid
flowchart TD
    entrada3["Entrada por trecho:\nhere_status, here_flow, tomtom_flow,\ngoogle_status, jam_factor_max,\nsegmentos_congestionados, pct_congestionado"]

    temHereFlow{HERE Flow\ndisponível?}
    usaHereFlow["status = here_flow_status\n(prioridade máxima)"]
    temTomFlow{TomTom Flow\ndisponível?}
    usaTomFlow["status = tomtom_flow_status"]
    usaGoogle["status = google_status\n(fallback)"]

    promTT{TomTom road\nclosure?}
    promParado["status = 'Parado'"]

    promJam{"jam_factor_max >= 8\nE segmentos_congestionados >= 2\nE status não é Parado?"}
    promIntenso["status = 'Intenso'"]

    promJam2{"jam_factor_max >= 5\nE segmentos_congestionados >= 1\nE status = 'Normal'?"}
    promModerado["status = 'Moderado'"]

    incGrave{"Ocorrência grave\n(interdição/colisão)\nE status = 'Normal'?"}
    promOcorr["status = 'Moderado'\nou 'Intenso'\n(por categoria)"]

    saida3["status final do trecho"]

    entrada3 --> temHereFlow
    temHereFlow -->|Sim| usaHereFlow
    temHereFlow -->|Não| temTomFlow
    temTomFlow -->|Sim| usaTomFlow
    temTomFlow -->|Não| usaGoogle
    usaHereFlow --> promTT
    usaTomFlow --> promTT
    usaGoogle --> promTT
    promTT -->|Sim| promParado
    promTT -->|Não| promJam
    promParado --> promJam
    promJam -->|Sim| promIntenso
    promJam -->|Não| promJam2
    promIntenso --> incGrave
    promJam2 -->|Sim| promModerado
    promJam2 -->|Não| incGrave
    promModerado --> incGrave
    incGrave -->|Sim| promOcorr
    incGrave -->|Não| saida3
    promOcorr --> saida3
```

### Fluxograma de ocorrência

```mermaid
flowchart TD
    entOcorr["Incidentes HERE + TomTom"]
    score["Score por categoria\n(_PESOS_OCORRENCIA):\ninterdição=10, bloqueio=9,\ncolisão=8, obras=6,\nengarrafamento=4..."]
    severidade["+ bônus por severidade\n+ bônus HERE (fonte primária)"]
    multiOcorr["Formata todas as categorias\nunidas por ';'\nex: 'Interdição; Colisão; Obras'"]
    principal["ocorrencia_principal = mais grave\n(para styling e promoção de status)"]
    fallbackJam{"Sem ocorrência\nE jam_factor >= 5?"}
    fallbackAtraso{"Sem ocorrência\nE atraso_min >= 15?"}
    inferido["ocorrencia = 'Engarrafamento'\n(inferido, não observado)"]
    semOcorr["ocorrencia = ''"]

    entOcorr --> score --> severidade --> multiOcorr --> principal
    principal --> fallbackJam
    fallbackJam -->|Não| fallbackAtraso
    fallbackJam -->|Sim| inferido
    fallbackAtraso -->|Sim| inferido
    fallbackAtraso -->|Não| semOcorr
```

---

## 6. Detecção de conflito entre fontes

**Arquivo:** `sources/correlator.py` — `_detectar_conflito_fontes()`

Quando HERE e Google divergem significativamente na avaliação de tráfego, o sistema sinaliza conflito e penaliza a confiança.

### Mapeamento de níveis

| Status | Nível numérico |
|--------|---------------|
| Normal | 0 |
| Moderado | 1 |
| Intenso | 2 |
| Parado | 3 |
| Sem dados | — (ignorado) |

### Fluxograma

```mermaid
flowchart TD
    entConfl["Entrada:\nhere_status, google_status,\natraso_min, jam_factor"]
    diff["diff = |nivel_here - nivel_google|"]
    conflito2{diff >= 2\nou outros\ncritérios?}
    criterios["Outros critérios de conflito:\n- HERE Normal + atraso_min >= 15\n- Google Normal + jam_factor >= 5"]
    grau{"diff >= 3\nou atraso >= 15\nou jam >= 5?"}
    alto["conflito_grau = 'alto'\npenalidade DataAdvisor: -20 pts\nconfianca_textual: rebaixa 1 nível"]
    moderado["conflito_grau = 'moderado'\npenalidade DataAdvisor: -10 pts\nconfianca_textual: rebaixa 1 nível"]
    semConflito["conflito_fontes = False\nsem penalidade"]
    saida4["conflito_fontes, conflito_grau,\nconflito_detalhe\n(ex: 'HERE Normal / Google Intenso')"]

    entConfl --> diff --> conflito2
    conflito2 -->|Não| criterios --> conflito2
    conflito2 -->|Sim| grau
    grau -->|Alto| alto
    grau -->|Não| moderado
    conflito2 -->|Ainda não| semConflito
    alto --> saida4
    moderado --> saida4
    semConflito --> saida4
```

---

## 7. Cálculo de confiança — DataAdvisor

**Arquivo:** `sources/advisor.py` — `DataAdvisor.enriquecer_dados()`

O `DataAdvisor` calcula `confianca_pct` (0–100) combinando frescor dos dados (freshness), peso por fonte e score operacional.

### Diagrama de blocos

```mermaid
flowchart LR
    subgraph inputs4 [Entradas]
        age["Idade dos dados\n(age_minutes)"]
        fonte["Fonte escolhida\n(here, google, tomtom)"]
        status4["Status final\n(Normal, Moderado, Intenso, Parado)"]
        espacial["Precisão espacial\n(km_estimado, localizacao_precisa)"]
        nfontes["Nº de fontes\nconsultadas"]
        conflito3["conflito_fontes\n(bool)"]
    end

    subgraph freshness [Freshness Score]
        exp["score = e^(-0.023 × age_minutes)\n\nage=0 → 1.0\nage=15 → 0.71\nage=30 → 0.50\nage=60 → 0.25"]
    end

    subgraph peso [Peso por fonte]
        pesos["here_incident: 1.0\nhere_flow: 0.9\ntomtom_incident: 0.9\ntomtom_flow: 0.85\ngoogle_duration: 0.8"]
    end

    subgraph base [Score base]
        calcBase["base = freshness × peso × 100\n(melhor fonte disponível)"]
    end

    subgraph operacional [Score operacional]
        grav["gravidade status:\nParado=90, Intenso=70,\nModerado=50, Normal=30"]
        precEspacial["precisão espacial:\n+15 se km_estimado\n+10 se trecho_especifico\n+5 se localizacao_precisa"]
        numFontes["nº fontes:\n+5 por fonte adicional"]
        opConf["op_conf = grav + precEspacial + numFontes\n(normalizado 0–100)"]
    end

    subgraph formula [Fórmula final]
        calc["confianca_pct =\n0.55 × base + 0.45 × operacional\nlimitado 0–100"]
        penal["Penalidade por conflito:\n-20 pts (grau alto)\n-10 pts (grau moderado)"]
        final["confianca_pct final"]
    end

    age --> exp
    fonte --> pesos
    exp --> calcBase
    pesos --> calcBase
    calcBase --> calc
    status4 --> grav
    espacial --> precEspacial
    nfontes --> numFontes
    grav --> opConf
    precEspacial --> opConf
    numFontes --> opConf
    opConf --> calc
    calc --> penal
    conflito3 --> penal
    penal --> final
```

### Confiança textual

| `confianca_pct` | Confiança textual |
|-----------------|------------------|
| ≥ 70 | Alta |
| 40–69 | Média |
| < 40 | Baixa |

> Quando há conflito de fontes (diff ≥ 2 níveis), a confiança textual é rebaixada em 1 nível (Alta → Média → Baixa), independente do valor numérico.

---

## 8. Estimativa de KM e trecho local

**Arquivo:** `sources/km_calculator.py` — `estimar_km()`, `identificar_trecho_local()`

Estima o KM de um incidente ao longo da rodovia usando interpolação geográfica pelos pontos de referência.

### Fluxograma

```mermaid
flowchart TD
    entKm["Entrada:\n(lat, lng) do incidente\npontos_referencia do trecho"]
    haversine["Calcula distância haversine\nentre o ponto e cada\nponto de referência"]
    maisProximo["Identifica ponto de referência\nmais próximo (dist_min)"]
    maxDist{"dist_min >\n50 km?\n(MAX_DISTANCIA_REFERENCIA_KM)"}
    baixaConf["confianca = 0.2\nkm_estimado = aproximado"]
    parAdj["Identifica par adjacente\nao ponto mais próximo\n(antes e depois)"]
    interpola["Interpola KM:\nkm = km_A + (km_B - km_A) ×\n(dist_A / (dist_A + dist_B))"]
    gap["gap_km = distância entre\nos dois pontos adjacentes"]
    confGap{"gap_km > 120?"}
    reduz40["confianca *= 0.6\n(-40%)"]
    confGap2{"gap_km > 80?"}
    reduz25["confianca *= 0.75\n(-25%)"]
    saida5["km_estimado, confianca_localizacao\n(0.0 a 1.0)"]

    entKm --> haversine --> maisProximo --> maxDist
    maxDist -->|Sim| baixaConf --> saida5
    maxDist -->|Não| parAdj --> interpola --> gap --> confGap
    confGap -->|Sim| reduz40 --> saida5
    confGap -->|Não| confGap2
    confGap2 -->|Sim| reduz25 --> saida5
    confGap2 -->|Não| saida5
```

### Identificação de trecho local

```mermaid
flowchart TD
    entLocal["Entrada:\nkm_estimado\npontos_referencia\nlimite_gap_km (do trecho)"]
    tipoRota{Tipo de rota}
    limMetro["limite = 20 km\n(metropolitana)"]
    limInter["limite = 60 km\n(inter-municipal)"]
    limConfig["limite = limite_gap_km\n(configurado na rota)"]
    verificaGap{"gap entre pontos A e B\n> limite?"}
    proxA["localizacao = 'próximo a A'\n(sem trecho específico)"]
    entreAB["localizacao = 'A → B'\n(trecho_especifico definido)"]

    entLocal --> tipoRota
    tipoRota -->|Metropolitana| limMetro
    tipoRota -->|Inter-municipal| limInter
    tipoRota -->|Configurado| limConfig
    limMetro --> verificaGap
    limInter --> verificaGap
    limConfig --> verificaGap
    verificaGap -->|Sim| proxA
    verificaGap -->|Não| entreAB
```

> **Limitação:** a distância é calculada em linha reta (haversine), não ao longo da rodovia. Em trechos sinuosos (ex.: BR-040 Serra de Petrópolis), o erro pode ser de ±10–20 km.

---

## 9. Classificação de trânsito Google

**Arquivo:** `sources/google_maps.py` — `classificar_transito()`

Combina razão de duração e atraso absoluto para classificar o status de cada rota.

### Lógica

```python
razao = duracao_transito / duracao_normal
atraso_min = (duracao_transito - duracao_normal) / 60

# Thresholds de razão
THRESHOLDS_RAZAO = {"Normal": 1.15, "Moderado": 1.40}

# Thresholds de atraso absoluto (requerem razão mínima)
THRESHOLDS_ATRASO_ABS = {
    "Moderado": {"min_atraso_min": 10, "min_razao": 1.03},
    "Intenso":  {"min_atraso_min": 25, "min_razao": 1.05},
}
```

### Exemplos práticos

| Rota | Duração normal | Duração c/ trânsito | Razão | Atraso | Status |
|------|---------------|--------------------|----|--------|--------|
| Curta (30 min) | 30 min | 36 min | 1.20 | 6 min | **Moderado** (razão > 1.15) |
| Longa (300 min) | 300 min | 315 min | 1.05 | 15 min | **Moderado** (atraso ≥ 10 + razão > 1.03) |
| Longa (300 min) | 300 min | 325 min | 1.08 | 25 min | **Intenso** (atraso ≥ 25 + razão > 1.05) |
| Longa (300 min) | 300 min | 310 min | 1.03 | 10 min | **Normal** (razão abaixo de 1.15, atraso abaixo de 10 min) |

---

## 10. Classificação de fluxo HERE — Jam Factor

**Arquivo:** `sources/here_traffic.py` — `consultar_fluxo_trafego()`

O Jam Factor (0–10) é calculado pela média dos segmentos de fluxo, com análise adicional por segmento para evitar que congestionamentos localizados sejam diluídos.

### Thresholds (média)

| Jam Factor médio | Status base |
|-----------------|-------------|
| 0 – 2.0 | Normal |
| 2.1 – 5.0 | Moderado |
| 5.1 – 8.0 | Intenso |
| 8.1 – 10.0 | Parado |

### Promoção por segmento (correlator)

```mermaid
flowchart TD
    jamMedia["jam_factor = média de todos\nos segmentos da rota"]
    jamMax["jam_factor_max = máximo\nsegs_congestionados = nº segs com jam >= 5\npct_congestionado = % segs com jam >= 5"]
    prom1{"jam_factor_max >= 8\nE segs_cong >= 2\nE status != Parado?"}
    toIntenso["status = 'Intenso'\n(congestionamento grave localizado)"]
    prom2{"jam_factor_max >= 5\nE segs_cong >= 1\nE status = 'Normal'?"}
    toModerado["status = 'Moderado'\n(congestionamento localizado)"]
    mantem["mantém status da média"]

    jamMedia --> jamMax --> prom1
    prom1 -->|Sim| toIntenso
    prom1 -->|Não| prom2
    prom2 -->|Sim| toModerado
    prom2 -->|Não| mantem
```

**Exemplo real (BR-381):** 50 km congestionados (jam=8) + 380 km livres (jam=0.5) → média 1.37 → status "Normal" **sem** promoção. Com promoção: `jam_factor_max=8`, `segs_congestionados=5` → status **"Intenso"**.

---

## Documentação relacionada

- [ARQUITETURA.md](ARQUITETURA.md) — visão do sistema e componentes
- [PRECISAO_E_CONFIANCA.md](PRECISAO_E_CONFIANCA.md) — gaps e % de confiança
- [ANALISE_PRECISAO.md](../ANALISE_PRECISAO.md) — análise técnica detalhada
- [COMO_FUNCIONA.md](COMO_FUNCIONA.md) — guia de apresentação do sistema
