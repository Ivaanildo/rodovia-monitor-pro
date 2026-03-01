# Analise Critica de Precisao — Monitor de Rodovias

> Documento gerado em 2026-02-18 | Atualizado em 2026-02-19 | Versao: v2-MVP (melhorias #1-#10 implementadas)
> Objetivo: Mapear todas as limitacoes de precisao do sistema para orientar decisoes de melhoria.

---

## Indice

1. [Visao Geral](#1-visao-geral)
2. [HERE Traffic — Incidentes](#2-here-traffic--incidentes)
3. [HERE Traffic — Fluxo (Jam Factor)](#3-here-traffic--fluxo-jam-factor)
4. [Google Routes API v2](#4-google-routes-api-v2)
5. [Motor de Correlacao](#5-motor-de-correlacao)
6. [Calculadora de KM](#6-calculadora-de-km)
7. [Score de Confianca (DataAdvisor)](#7-score-de-confianca-dataadvisor)
8. [Relatorio Excel](#8-relatorio-excel)
9. [Pontos de Referencia (28 Rotas)](#9-pontos-de-referencia-28-rotas)
10. [Resumo Quantitativo](#10-resumo-quantitativo)
11. [Recomendacoes Priorizadas](#11-recomendacoes-priorizadas)

---

## 1. Visao Geral

O sistema coleta dados de 2 fontes (HERE Traffic + Google Routes v2) para 28 rotas brasileiras, correlaciona os resultados e gera um relatorio Excel. A precisao varia drasticamente dependendo da rota:

- **7 de 28 rotas (~25%)** usam corridor preciso (polyline do tracado real)
- **21 de 28 rotas (~75%)** caem em bbox (retangulo geografico) — precisao significativamente menor

O pipeline tem 7 estagios onde dados sao perdidos ou degradados:

```
HERE Routing v8 → Polyline → Downsample → Corridor/BBox → Filtro texto+distancia → Correlacao → Excel
                                                           (#1 haversine filter)     (#4 conflito)
Google Routes v2 → Duration + speedReadingIntervals → Classificacao → Correlacao → Excel
                               (#2 processado)                        (#3 multi-ocorrencia)
```

---

## 2. HERE Traffic — Incidentes

### 2.1 Corridor vs BBox: O Problema Central

**Arquivo:** `sources/here_traffic.py` — funcoes `consultar_incidentes()`, `_obter_corridor_ou_none()`

| Metodo | Rotas | Precisao | Como funciona |
|--------|-------|----------|---------------|
| Corridor | 7/28 (25%) | Alta | Polyline do tracado real + raio de 100m. Filtragem espacial: incidentes >150m do tracado sao descartados |
| BBox | 21/28 (75%) | Media | Retangulo geografico com padding de 20km. **Filtragem de distancia via polyline de referencia (500m)** |

> **[IMPLEMENTADO 2026-02-18]** Filtro haversine para resultados bbox. Incidentes a >500m da polyline
> de referencia (pontos dos segmentos) ou da polyline do Routing v8 (preservada mesmo quando corridor
> falha no downsampling) sao descartados. Funcoes: `_construir_polyline_referencia()`,
> `_dist_ponto_polyline_m()`. Parametro: `bbox_filter_radius_m=500`.

**Por que so 7 rotas usam corridor?**

1. HERE Routing v8 retorna polyline detalhada (1000+ pontos para rotas longas)
2. HERE Traffic v7 aceita no maximo **300 pontos** e **1200 chars** por corridor
3. `_downsample_polyline()` reduz pontos mas rotas longas nao cabem no limite
4. Rotas >500km (`ROTA_LONGA_SKIP_ROUTING_KM`) nem tentam corridor

**Consequencia do BBox (mitigada):**
- ~~Um incidente numa rua paralela a 15km da rodovia e capturado como se fosse na rodovia~~ Filtrado pelo threshold de 500m
- Incidentes em rodovias adjacentes muito proximas (<500m) ainda podem ser capturados
- Precisao da filtragem depende da qualidade dos pontos de referencia (gaps grandes = filtragem menos precisa)

### 2.2 Filtragem por Rodovia — Baseada em Texto

**Arquivo:** `sources/here_traffic.py` — funcao `_incidente_relevante_para_rodovia()`

```python
# Logica simplificada:
texto_codigo = _extrair_codigo_rodovia(descricao_here)  # Ex: "BR116"
if texto_codigo == filtro_codigo:
    return True  # Incidente relevante
```

**Problemas:**
- Depende da HERE API incluir o codigo da rodovia na descricao do incidente
- Se a API retorna apenas coordenadas sem nome de rodovia, o filtro **nao funciona**
- Nao ha verificacao geografica (o incidente esta geometricamente dentro do corredor?)
- "BR-116 via alternativa" pode casar com filtro "BR-116" mesmo sendo outra via

### 2.3 Filtro de Distancia — Corridor e BBox

**Arquivo:** `sources/here_traffic.py` — dentro de `consultar_incidentes()`

```python
# Threshold depende do metodo: corridor preciso (150m), bbox aproximado (500m)
threshold = (corridor_radius_m + corridor_margin_m) if metodo == "corridor" else bbox_filter_radius_m
if route_pts and dist > threshold:
    continue  # Descarta incidente longe da rota
```

> **[ATUALIZADO 2026-02-18]** Filtro agora funciona em ambos os modos:
> - **Corridor:** 150m (100+50) — polyline precisa do Routing v8
> - **BBox:** 500m (`bbox_filter_radius_m`) — polyline aproximada dos pontos de referencia ou preservada do Routing v8
>
> Fontes de `route_pts` para bbox (em ordem de prioridade):
> 1. Polyline do Routing v8 preservada quando downsampling falha (alta precisao)
> 2. `_construir_polyline_referencia(segmentos)` — pontos de referencia do config (precisao variavel)
>
> **Limitacao residual:** Rotas com poucos pontos de referencia (ex: BR-230 com 7 pontos para 4200km)
> tem filtragem menos precisa — gaps grandes entre pontos criam "zonas cegas" onde incidentes
> distantes da rodovia podem passar pelo filtro.

### 2.4 Polyline Downsampling — Ramer-Douglas-Peucker

**Arquivo:** `sources/here_traffic.py` — funcoes `_rdp_simplify()`, `_downsample_polyline()`

> **[IMPLEMENTADO 2026-02-19 — Melhoria #10]** Substituido keep-every-N por algoritmo
> Ramer-Douglas-Peucker (RDP) iterativo. Preserva pontos geometricamente significativos
> (curvas, inflexoes) enquanto descarta pontos em trechos retos.

```
Algoritmo: RDP iterativo com epsilon crescente (50m, 75m, 112m, ...)
Entrada:   4000+ pontos (polyline detalhada do Routing v8)
Saida:     ≤300 pontos preservando geometria
Melhoria:  Curvas e trevos preservados, incidentes em inflexoes nao saem do corridor
```

**O que mudou:**
- ~~Curvas detalhadas viram linhas retas~~ Curvas preservadas pelo RDP (pontos de inflexao mantidos)
- ~~Incidentes em inflexoes ficam fora~~ Pontos de inflexao tem prioridade na simplificacao
- Epsilon comeca em 50m e cresce por fator 1.5x ate caber nos limites (300 pts, 1200 chars)

**Limitacao residual:** Rotas muito longas (>500km) ainda pulam Routing v8 inteiramente → bbox direto.

---

## 3. HERE Traffic — Fluxo (Jam Factor)

### 3.1 Jam Factor — Media + Analise por Segmento

**Arquivo:** `sources/here_traffic.py` — funcao `consultar_fluxo_trafego()`

> **[IMPLEMENTADO 2026-02-19 — Melhoria #8]** Alem da media, o sistema agora rastreia
> `jam_factor_max`, `segmentos_congestionados` e `pct_congestionado` por segmento.
> O correlator promove status quando a media dilui congestionamento localizado.

```python
# Calculo atual — media + analise por segmento:
jam_por_segmento = []
for cf in current_flow:
    jf = cf.get("jamFactor", 0)
    total_jam += jf
    jam_por_segmento.append(jf)
avg_jam = total_jam / count
jam_max = max(jam_por_segmento)
segs_congestionados = sum(1 for j in jam_por_segmento if j >= 5)
```

**Thresholds de classificacao (media):**

| Jam Factor | Status | Problema (mitigado) |
|-----------|--------|----------|
| 0 - 2.0 | Normal | Promovido a Moderado/Intenso se jam_max >= 5/8 |
| 2.1 - 5.0 | Moderado | Promovido a Intenso se jam_max >= 8 com >= 2 segs |
| 5.1 - 8.0 | Intenso | OK |
| 8.1 - 10.0 | Parado | OK |

**Promocao por segmento (correlator.py):**
- `jam_factor_max >= 8` e `segmentos_congestionados >= 2` → status promovido para "Intenso"
- `jam_factor_max >= 5` e `segmentos_congestionados >= 1` e status "Normal" → "Moderado"

**Cenario corrigido (BR-381):**
- 50km congestionados (jam=8) + 380km livres (jam=0.5) → media 1.37
- `jam_factor_max = 8.0`, `segmentos_congestionados = 5` → **"Intenso"** (era "Normal")

### 3.2 ~~Dados de Segmento Ignorados~~ Dados de Segmento Utilizados

> **[CORRIGIDO 2026-02-19 — Melhoria #8]** Dados por segmento agora sao processados:

**Campos expostos no resultado:**
- `jam_factor_max` — pior segmento (para promocao de status)
- `segmentos_total` — total de segmentos com dados
- `segmentos_congestionados` — segmentos com jam >= 5
- `pct_congestionado` — % de segmentos congestionados

**Dados ainda nao utilizados:**
- `speed` por segmento individual (usada apenas na media)
- `freeFlow` por segmento individual
- `confidence` por segmento

---

## 4. Google Routes API v2

### 4.1 Classificacao de Trafego — Razao + Atraso Absoluto

**Arquivo:** `sources/google_maps.py` — funcao `classificar_transito()`

> **[IMPLEMENTADO 2026-02-19 — Melhoria #7]** Classificacao agora combina razao de duracao
> com atraso absoluto em minutos. Thresholds de atraso absoluto exigem razao minima para
> evitar falsos positivos.

```python
THRESHOLDS_RAZAO = {"Normal": 1.15, "Moderado": 1.40}
THRESHOLDS_ATRASO_ABS = {
    "Moderado": {"min_atraso_min": 10, "min_razao": 1.03},
    "Intenso":  {"min_atraso_min": 25, "min_razao": 1.05},
}
```

**Cenarios corrigidos:**
- Rota longa: 300min normal, 315min transito = 15min atraso, ratio 1.05 → **"Moderado"** (era "Normal")
- Rota longa: 300min normal, 325min transito = 25min atraso, ratio 1.08 → **"Intenso"** (era "Normal")
- Rota curta: 30min normal, 35min transito = ratio 1.17 → "Moderado" (sem mudanca)

**Problemas residuais:**

1. ~~**Ignora delay absoluto**~~ **Corrigido (#7)** — atraso absoluto >= 10min com ratio > 1.03 promove para Moderado; >= 25min com ratio > 1.05 promove para Intenso
2. **Fronteiras abruptas:** Razao 1.14 = "Normal", razao 1.16 = "Moderado" — salto sem gradiente
3. **Sem rotas alternativas:** `computeAlternativeRoutes: False` — nao sabe se existe caminho melhor

### 4.2 speedReadingIntervals — Processado para Localizacao de Congestionamento

**Arquivo:** `sources/google_maps.py` — funcao `_consultar_routes_v2()`
**Processamento:** `sources/correlator.py` — funcao `_analisar_speed_intervals()`

> **[IMPLEMENTADO 2026-02-18]** `_analisar_speed_intervals()` processa os intervalos de velocidade
> e calcula proporcao NORMAL/SLOW/TRAFFIC_JAM + zonas de congestionamento (trecho inicial/central/final).
> Integrado em `_gerar_observacao_detalhada()` para enriquecer observacoes com localizacao.
> 44 testes em `tests/test_speed_intervals.py`.

**O que `speedReadingIntervals` contem:**
- Velocidade por segmento da polyline (NORMAL, SLOW, TRAFFIC_JAM)
- Permite identificar ONDE o trafego esta lento
- Dados granulares por trecho da rota

**Como e processado:**
- Calcula % da rota em cada velocidade (ex: "20% lento, 10% parado")
- Identifica zonas proporcionais: 0-33% = trecho inicial, 33-66% = central, 66-100% = final
- Enriquece coluna Observacoes: ex: "lentidao no trecho central e final"
- Apenas enriquece texto — nao altera status nem ocorrencia

**Limitacao residual:** Sem polyline decodificada, zonas sao proporcionais (nao KM exatos).

---

## 5. Motor de Correlacao

### 5.1 HERE Sempre Sobrescreve Google no Status

**Arquivo:** `sources/correlator.py` — funcao de correlacao principal

```python
if here_status != "Sem dados":
    resultado["status"] = here_status  # HERE vence, sempre
```

**Problema original:** Se HERE Flow retorna "Normal" (media diluida, ver secao 3.1) mas Google detecta atraso de 40% (razao 1.40 = "Moderado"), o sistema reporta "Normal".

> **[MITIGADO 2026-02-19 — Melhoria #4]** `_detectar_conflito_fontes()` agora detecta discrepancias
> entre HERE Flow e Google Maps. Quando a diferenca de status e >= 2 niveis (ex: Normal vs Intenso):
> - Resultado recebe `conflito_fontes=True` + `conflito_detalhe` + `conflito_grau` (moderado/alto)
> - Confianca textual rebaixada em 1 nivel (Alta→Media, Media→Baixa)
> - `DataAdvisor` aplica penalidade de -20pts (alto) ou -10pts (moderado) no `confianca_pct`
> - Observacao inclui alerta: "⚠ Fontes divergem: HERE indica X mas Google indica Y"
> - Log WARNING emitido para monitoramento
>
> **Limitacao residual:** HERE ainda sobrescreve o status final. O conflito e sinalizado mas
> nao altera qual status e exibido — o operador decide com base no alerta.

### 5.2 ~~Apenas Uma Ocorrencia Reportada~~ Multiplas Ocorrencias Exibidas

**Arquivo:** `sources/correlator.py` — logica de selecao de ocorrencia

> **[CORRIGIDO 2026-02-18 — Melhoria #3]** `_formatar_ocorrencias_display()` agora exibe todas
> as categorias unicas separadas por "; " (ex: "Interdicao; Colisao; Obras na Pista").
> `resultado["ocorrencia_principal"]` preserva a mais grave para styling e status promotion.
> `_PESOS_OCORRENCIA` e constante compartilhada entre `_decidir_ocorrencia()` e `_formatar_ocorrencias_display()`.

```python
# Antes: melhor = max(ocorrencias, key=score)  # So o de maior peso
# Agora:
resultado["ocorrencia"] = _formatar_ocorrencias_display(ocorrencias)  # Todas as categorias
resultado["ocorrencia_principal"] = _decidir_ocorrencia(ocorrencias)   # Mais grave (styling)
```

**Cenario corrigido:**
- Rota tem: colisao no KM 50 + obras no KM 200 + interdicao parcial no KM 350
- Sistema reporta: "Interdicao; Colisao; Obras na Pista" na coluna Ocorrencia
- Styling usa `ocorrencia_principal` ("Interdicao") para cor da celula
- Resumo Executivo conta cada categoria individualmente via `split(";")`

### 5.3 Inferencia de Engarrafamento — Sem Causa Raiz

**Arquivo:** `sources/correlator.py` — fallback de ocorrencia

```python
if not resultado["ocorrencia"] and resultado.get("jam_factor", 0) >= 5:
    resultado["ocorrencia"] = "Engarrafamento"  # Inferido, nao observado

if not resultado["ocorrencia"] and resultado.get("atraso_min", 0) >= 15:
    resultado["ocorrencia"] = "Engarrafamento"  # Inferido novamente
```

**Problemas:**
- Nao distingue causa: acidente? chuva? volume natural? obras?
- Threshold `jam >= 5` e `atraso >= 15min` sao arbitrarios
- "Engarrafamento" inferido tem mesma aparencia no relatorio que um observado pela HERE API

### 5.4 Observacoes Detalhadas — Boa Cobertura com Ressalvas

**Arquivo:** `sources/correlator.py` — funcao `_gerar_observacao_detalhada()`

A funcao concatena informacoes de KM, trecho especifico e sentido nos fallbacks. E o componente mais informativo do pipeline, porem:
- Depende da qualidade dos dados upstream (KM impreciso = observacao imprecisa)
- Compactacao (`_compactar_descricao_operacional()`) remove duplicatas mas pode descartar contexto relevante

---

## 6. Calculadora de KM

### 6.1 Haversine + Interpolacao Linear — Limitacoes Geometricas

**Arquivo:** `sources/km_calculator.py` — funcoes `estimar_km()`, `_interpolar_km()`

```
Metodo: Calcula distancia haversine (linha reta) entre coordenadas do incidente
        e pontos de referencia. Interpola KM proporcionalmente.

Precisao real: ±2-5 km em trechos retos, ±10-20 km em trechos sinuosos
```

**Problema fundamental:** Haversine mede distancia em linha reta, nao ao longo da rodovia. Numa serra com curvas (ex: BR-040 Serra de Petropolis), a distancia real pela rodovia pode ser 2-3x a distancia em linha reta.

### 6.2 Gaps Enormes em Pontos de Referencia

**Arquivo:** `rotas_logistica.json`

Quando o gap entre dois pontos de referencia consecutivos excede o limite, o sistema abandona a interpolacao e retorna "proximo a X":

| Limite | Tipo de Rota | Comportamento |
|--------|-------------|---------------|
| 10 km | Metropolitana (5 rotas) | Interpolacao precisa |
| 60 km | Inter-municipal (19 rotas) | Interpolacao com margem |
| >60 km | Gaps reais em rotas longas | Fallback "proximo a X" |

**Rotas criticas com gaps excessivos:**

> **[MITIGADO 2026-02-19 — Melhoria #5]** Adicionados ~63 pontos de referencia intermediarios
> em 14 rotas com gaps >60km. Gap maximo reduzido de ~800km para ~300km (Transamazonica)
> e de ~170km para ~50km nas demais rotas. Ver detalhes em `rotas_logistica.json`.

| Rota | Extensao | Pontos Ref. (antes→agora) | Gap Maximo (antes→agora) | Precisao |
|------|----------|---------------------------|--------------------------|----------|
| BR-230 Transamazonica | ~4200 km | 7→17 | ~800→~300 km | Baixa (era Muito baixa) |
| BR-116 SP/Curitiba | ~400 km | 6→10 | ~100→~50 km | Moderada (era Baixa) |
| BR-381 BH/SP | ~560 km | 7→11 | ~130→~60 km | Moderada |
| BR-040 Brasilia/BH | ~730 km | 6→11 | ~180→~70 km | Moderada (era Baixa) |
| Marginal Tiete | ~25 km | 5 | ~8 km | Alta |

### 6.3 Confianca de Localizacao — Nao Propagada

**Arquivo:** `sources/km_calculator.py` — funcao `_calcular_confianca()`

```python
if gap_km > 120:    confianca *= 0.6    # Reducao de 40%
elif gap_km > 80:   confianca *= 0.75   # Reducao de 25%
```

O KM calculator calcula `confianca_localizacao` (0.0 a 1.0) mas esse valor:
- NAO aparece no relatorio Excel principal
- NAO influencia o score de confianca final (DataAdvisor)
- E descartado silenciosamente no pipeline

---

## 7. Score de Confianca (DataAdvisor)

### 7.1 ~~Freshness Score — Cutoffs Abruptos~~ Freshness Score — Decaimento Exponencial

**Arquivo:** `sources/advisor.py`

> **[IMPLEMENTADO 2026-02-19 — Melhoria #9]** Substituidos cutoffs abruptos por decaimento
> exponencial: `score = e^(-0.023 * age_minutes)`. Scores < 0.05 arredondados para 0.0.

```python
# Antes (cutoffs abruptos):
# if age_minutes <= 5:       return 1.0
# elif age_minutes <= 15:    return 0.8   # salto de 0.2 em 1 minuto!
# elif age_minutes <= 30:    return 0.5
# elif age_minutes <= 60:    return 0.2

# Agora (decaimento gradual):
score = math.exp(-0.023 * age_minutes)
# age=0: 1.000 | age=5: 0.891 | age=15: 0.708 | age=30: 0.502 | age=60: 0.252
```

**Problema corrigido:** Dado com 15min = 0.708. Dado com 16min = 0.692. Diferenca de ~2% por minuto
(era 37.5% de salto). Curva suave sem descontinuidades.

### 7.2 Formula de Confianca Final — Arbitraria

```python
confianca_final = round((base_conf * 0.55) + (op_conf * 0.45), 1)
```

| Componente | Peso | Origem | Problema |
|-----------|------|--------|----------|
| base_conf | 55% | Freshness x source_weight | Pesos de fonte sem validacao |
| op_conf | 45% | Gravidade do status | Circular: jam→status→score→confianca |

**Raciocinio circular:**
1. `jam_factor` → classifica status como "Parado"
2. Status "Parado" → `gravidade = 90`
3. `gravidade` → `operational_confidence = 0.9`
4. `operational_confidence` → `confianca_final` alta
5. **Conclusao errada:** "Tenho alta confianca de que esta parado" quando o jam_factor era uma media diluida

### 7.3 ~~Multi-Fonte = "Alta" Automaticamente~~ Multi-Fonte com Deteccao de Conflito

```python
if total_fontes >= 2:
    confianca = "Alta"

# [ADICIONADO 2026-02-19 — Melhoria #4] Conflito rebaixa confianca
if conflito:
    if confianca == "Alta":
        confianca = "Media"
    elif confianca == "Media":
        confianca = "Baixa"
```

> **[MITIGADO 2026-02-19]** Quando HERE e Google discordam (diff >= 2 niveis), confianca
> textual e rebaixada em 1 nivel. Adicionalmente, `DataAdvisor` aplica penalidade de
> -20pts (conflito alto) ou -10pts (conflito moderado) no `confianca_pct` numerico.
>
> **Problema residual:** Multi-fonte com concordancia parcial (diff == 1 nivel, ex: Normal vs
> Moderado) ainda recebe confianca "Alta" sem penalidade — por design, diferencas pequenas
> sao aceitaveis dado que as fontes medem coisas diferentes (flow vs duration).

---

## 8. Relatorio Excel

### 8.1 Dados que NAO Aparecem no Relatorio Principal

| Dado | Onde Existe | Aparece no Excel? | Status |
|------|-------------|-------------------|--------|
| ~~`localizacao_precisa`~~ | ~~correlator.py~~ | ~~Nao (apenas aba Incidentes)~~ | **Corrigido (#6)** — propagada pelo correlator, exibida na coluna KM/Local |
| `distancia_rota_m` | here_traffic.py | Nao | — |
| ~~`confianca_localizacao`~~ | ~~km_calculator.py~~ | ~~Nao~~ | **Corrigido (#6)** — propagada pelo correlator, exibida como % na coluna KM/Local + color-code |
| `velocidade_atual_kmh` | here_traffic.py | Nao (agregado em jam_factor) | — |
| `velocidade_livre_kmh` | here_traffic.py | Nao | — |
| ~~`speedReadingIntervals`~~ | ~~google_maps.py~~ | ~~Nao~~ | **Corrigido (#2)** — processado em `_analisar_speed_intervals()`, enriquece Observacoes |
| ~~Incidentes secundarios~~ | ~~correlator.py~~ | ~~Nao~~ | **Corrigido (#3)** — todas as categorias exibidas via `_formatar_ocorrencias_display()` |
| `conflito_fontes` / `conflito_detalhe` | correlator.py | Sim (na coluna Observacoes como alerta) | **Novo (#4)** |

### 8.2 Altura de Linha Limitada

**Arquivo:** `report/excel_generator.py`

```python
ws.row_dimensions[row].height = min(90, 22 + (obs_len // 60) * 8)
# Maximo: 90 pixels ≈ 3-4 linhas visiveis no Excel
```

Observacoes longas (rotas com multiplos incidentes) ficam truncadas visualmente. O usuario precisa clicar na celula e expandir manualmente para ver o texto completo.

### 8.3 Hyperlinks Sem Contexto

- **Waze:** Link generico de navegacao, sem parametro de "trafego atual"
- **Google Maps:** Link sem waypoints intermediarios ou preferencia de rota
- Usuario abre o mapa e ve a visao padrao, nao a visao com contexto do incidente

---

## 9. Pontos de Referencia (28 Rotas)

### 9.1 Distribuicao de Qualidade

**Arquivo:** `rotas_logistica.json`

```
Rotas com cobertura ALTA  (gap < 20km):   5 rotas  (metropolitanas)
Rotas com cobertura MEDIA (gap 20-60km): 22 rotas  (inter-municipais + inter-estaduais melhoradas)
Rotas com cobertura BAIXA (gap > 60km):   1 rota   (BR-230 Transamazonica, gaps ~300km residuais)
```

> **[ATUALIZADO 2026-02-19 — Melhoria #5]** 14 rotas que tinham gaps >60km receberam pontos
> intermediarios. Antes: 13 rotas com cobertura BAIXA. Agora: apenas BR-230 permanece com gaps
> >60km (reduzidos de ~800km para ~300km).

### 9.2 Rotas Sem Tratamento Metropolitano

Apenas 5 rotas tem `limite_gap_km` especial para areas metropolitanas:
- PE-008 Recife Centro
- Marginal Tiete
- Avenida Brasil RJ
- PE-001 Recife/Olinda
- PE-015 Recife

As demais 23 rotas usam o default de 60km, incluindo rotas que passam por areas metropolitanas (ex: BR-116 passando por SP).

---

## 10. Resumo Quantitativo

### Perda de Precisao por Estagio

| Estagio | Perda Estimada | Causa Principal | Arquivo |
|---------|---------------|-----------------|---------|
| Corridor → BBox (75% rotas) | Moderada (era Alta) | Polyline nao cabe em 1200 chars / 300 pts. **Mitigado:** filtro haversine 500m via polyline de referencia | here_traffic.py |
| Polyline downsampling | Baixa (era ~96%) | **Corrigido (#10):** RDP preserva geometria (curvas, inflexoes) em vez de stride fixo | here_traffic.py |
| Filtro de rodovia (texto) | Variavel | Depende da HERE incluir nome da via | here_traffic.py |
| Filtro distancia (corridor + bbox) | Baixa (era 0% bbox) | **Implementado:** corridor 150m, bbox 500m via `_construir_polyline_referencia()` | here_traffic.py |
| Jam Factor (media) | Baixa (era Alta) | **Corrigido (#8):** jam_factor_max + segmentos_congestionados promovem status | here_traffic.py + correlator.py |
| speedReadingIntervals | Baixa (era 100%) | **Implementado:** `_analisar_speed_intervals()` processa zonas proporcionais | correlator.py |
| Classificacao transito | Baixa (era Moderada) | **Corrigido (#7):** razao + atraso absoluto combinados na classificacao | google_maps.py |
| Correlacao (status) | Baixa (era Moderada) | HERE sobrescreve mas conflito sinalizado (#4) + promocao por segmento (#8) | correlator.py |
| Correlacao (ocorrencia) | Baixa (era Alta) | Todas as categorias exibidas (#3) | correlator.py |
| KM positioning | Moderada (era Alta) | Gaps reduzidos de >60km para ~30-50km (#5). Fallback "proximo a" em menos rotas | km_calculator.py |
| Confianca | Baixa (era Nao confiavel) | **Corrigido (#9):** decaimento exponencial sem saltos + conflito rebaixa (#4) | advisor.py |
| Excel (dados ocultos) | Baixa (era Moderada) | `confianca_localizacao` exibida como % + color-code (#6) | excel_generator.py |

### Classificacao de Rotas por Precisao Geral

| Nivel | Rotas (antes→agora) | Criterio |
|-------|---------------------|----------|
| Alta | 5→5 | Metropolitanas + corridor + gaps < 20km |
| Media | 2→16 | Corridor funciona + gaps moderados. **Melhoria #5** reduziu gaps de 14 rotas |
| Baixa | 8→6 | BBox + gaps 20-60km residuais |
| Muito Baixa | 13→1 | BR-230 Transamazonica (gaps ~300km residuais, extensao 4200km) |

---

## 11. Recomendacoes Priorizadas

### Implementado

| # | Melhoria | Data | Detalhes |
|---|----------|------|----------|
| 1 | **Filtro haversine nos resultados bbox** | 2026-02-18 | `_construir_polyline_referencia()` + threshold 500m para bbox, 150m para corridor. Preserva `route_pts` do Routing v8 mesmo quando downsampling falha. 6 testes em `test_here_traffic.py`. |
| 2 | **Processar `speedReadingIntervals`** | 2026-02-18 | `_analisar_speed_intervals()` calcula % NORMAL/SLOW/TRAFFIC_JAM e identifica zonas (trecho inicial/central/final). Integrado em `_gerar_observacao_detalhada()`. 44 testes em `test_speed_intervals.py`. |
| 3 | **Mostrar multiplas ocorrencias no relatorio** | 2026-02-18 | `_formatar_ocorrencias_display()` junta categorias unicas por "; ". `resultado["ocorrencia_principal"]` preserva a mais grave para styling/status promotion. `_PESOS_OCORRENCIA` constante compartilhada. Excel Resumo conta categorias individualmente via split(";"). 4 testes em `test_correlator.py`. |
| 4 | **Deteccao de conflito HERE vs Google** | 2026-02-19 | `_detectar_conflito_fontes()` compara niveis de status (Normal=0, Moderado=1, Intenso=2, Parado=3). Conflito quando diff >= 2 niveis. Grau "alto" se diff >= 3 ou atraso >= 15min ou jam_factor >= 5. `_avaliar_confianca()` rebaixa confianca em 1 nivel quando conflito. `DataAdvisor.enriquecer_dados()` aplica penalidade -20pts (alto) ou -10pts (moderado). Observacao inclui alerta "Fontes divergem". Campos: `conflito_fontes`, `conflito_detalhe`, `conflito_grau`. 17 testes em `test_conflito_fontes.py`. |
| 5 | **Pontos de referencia intermediarios** | 2026-02-19 | ~63 pontos adicionados em 14 rotas com gaps >60km. Gap maximo reduzido de ~800km para ~300km (Transamazonica) e ~170km para ~50km (demais rotas). Cidades intermediarias reais com coordenadas pesquisadas. Impacto: filtragem bbox mais precisa, estimativa de KM com interpolacao em vez de fallback "proximo a". |
| 6 | **Exibir `confianca_localizacao` no Excel** | 2026-02-19 | `confianca_localizacao` propagada pelo correlator.py ao resultado final. `_formatar_km_local()` exibe confianca como % na coluna KM/Local (ex: "KM 245 - Resende → RJ (85%)"). Color-code: verde (>=70%), amarelo (40-70%), vermelho (<40%). `_nivel_confianca_loc()` classifica valor numerico em nivel textual. `_STYLE_MAP["confianca_loc"]` para estilos. |
| 7 | **Classificacao com delay absoluto** | 2026-02-19 | `classificar_transito()` agora combina razao + atraso absoluto em minutos. `THRESHOLDS_ATRASO_ABS` define: Moderado (atraso >= 10min, razao > 1.03), Intenso (atraso >= 25min, razao > 1.05). Guard de razao minima evita falsos positivos. 10 testes em `test_melhorias_7_10.py`. |
| 8 | **Jam factor por segmento** | 2026-02-19 | `consultar_fluxo_trafego()` agora expoe `jam_factor_max`, `segmentos_total`, `segmentos_congestionados`, `pct_congestionado`. Correlator promove status: jam_max >= 8 + 2 segs → Intenso; jam_max >= 5 + 1 seg e Normal → Moderado. Resolve o problema BR-381 (50km parados diluidos pela media). 6 testes em `test_melhorias_7_10.py`. |
| 9 | **Freshness score gradual** | 2026-02-19 | `calculate_freshness_score()` usa decaimento exponencial `e^(-0.023 * age_minutes)` em vez de cutoffs abruptos. Scores < 0.05 arredondados para 0.0. Elimina saltos de 37.5% por 1 minuto de diferenca. 11 testes em `test_advisor.py` (incluindo teste de monotonicidade). |
| 10 | **Ramer-Douglas-Peucker** | 2026-02-19 | `_rdp_simplify()` iterativo substitui keep-every-N. Preserva curvas e inflexoes enquanto descarta pontos em trechos retos. Epsilon crescente (50m → 50km) garante convergencia. 8 testes em `test_melhorias_7_10.py`. |

### Roadmap Completo

Todas as 10 melhorias planejadas foram implementadas. Futuras melhorias podem incluir:
- Decodificacao de polyline Google para zonas de congestionamento em KM exatos (nao proporcionais)
- Velocidade por segmento individual (speed/freeFlow por trecho, nao apenas jam factor)
- Rotas alternativas via Google Maps (`computeAlternativeRoutes: True`)
