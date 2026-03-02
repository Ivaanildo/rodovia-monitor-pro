# Geocoding e Precisão — Contexto para o RodoviaMonitor Pro

> Objetivo: explicar como APIs de geocoding (ex.: [publicapis.io/category/geocoding](https://publicapis.io/category/geocoding)) podem complementar o projeto para **melhor precisão de colisão por engarrafamento** e **waypoints** em `rota_logistica.json`, e o que é realista em termos de "acuracia 100%".

---

## 1. Onde está a imprecisão hoje

### 1.1 Waypoints no `rota_logistica.json`

- Os waypoints foram gerados por **"HERE polyline sampling"**: a rota é calculada (Routing v8), a polyline é amostrada em N pontos e esses pontos viram `via` com `!passThrough=true`.
- Esses pontos estão **sobre o traçado calculado pela HERE**, mas:
  - Podem ficar ligeiramente deslocados após simplificação (RDP) ou mudanças na rede HERE.
  - Para **HERE Traffic** e **TomTom**, o matching de incidentes/flow é por **segmento de via**. Um waypoint a dezenas de metros da rodovia pode cair em outro segmento → "Sem dados" ou engarrafamento atribuído ao trecho errado.
- Em rotas longas, quando o **corridor** não cabe no limite (300 pts / 1200 chars), o sistema usa **bbox** e filtra incidentes a **500 m** da polyline de referência. Waypoints mais precisos melhoram essa polyline e, portanto, a colisão "engarrafamento/incidente na minha rota".

### 1.2 Origem e destino (hubs)

- Hoje: `origem`/`destino` vêm de `lat`/`lng` no JSON ou são geocodificados pela **HERE Geocoding** quando o trecho vem em texto (ex.: endereço).
- Se o hub estiver errado ou em convenção diferente (ex.: centro do município vs portaria do CD), a rota e os waypoints derivados podem não seguir exatamente o corredor logístico real.

### 1.3 Resumo do pipeline de precisão

```
rota_logistica.json (origem, destino, via[])
    → main.py _normalizar_rota_logistica() → trechos com via_waypoints
    → HERE Traffic: corridor (polyline do Routing v8) ou bbox
    → Incidentes filtrados por: distância à polyline (150 m corridor / 500 m bbox) + texto (BR-xxx)
    → Google Routes: duração + speedReadingIntervals (zonas proporcionais)
```

Quanto **mais alinhados** origem, destino e waypoints estiverem com a **rede viária real** usada pelas APIs (HERE/TomTom/Google), melhor a colisão "engarrafamento ↔ minha rota".

---

## 2. O que APIs de geocoding fazem (e o que não fazem)

| Recurso | O que é | Ajuda no projeto? |
|--------|---------|--------------------|
| **Forward geocoding** | Endereço/texto → (lat, lng) | Sim: normalizar hubs, validar endereços, cruzar com HERE para consistência. |
| **Reverse geocoding** | (lat, lng) → endereço/nome da via | Sim: validar se um waypoint “cai” em rodovia (ex.: nome contém "BR-116") ou em via paralela. |
| **Snap-to-road / Map matching** | (lat, lng) → ponto mais próximo **na rede viária** | **Sim, e é o que mais aumenta precisão** para waypoints; não é “geocoding” clássico, mas algumas APIs de mapa oferecem. |

- **Geocoding clássico** não coloca o ponto “na rodovia”; coloca no endereço ou no centro do lugar. Para “pino exatamente na rodovia”, o ideal é **snap-to-road** (Mapbox Map Matching, HERE, OpenRouteService, etc.).
- APIs listadas em [publicapis.io/category/geocoding](https://publicapis.io/category/geocoding) ajudam principalmente em: **normalizar hubs**, **validar coordenadas** e **validar waypoints** (reverse: “esse lat,lng é mesmo na BR-116?”).

---

## 3. Como APIs de geocoding podem complementar

### 3.1 Normalização de hubs (forward geocoding)

- **Objetivo:** Ter origem/destino consistentes e corretos antes de gerar rotas e waypoints.
- **Uso:** Para cada `hub` em `rota_logistica.json`, opcionalmente:
  - Chamar uma API de geocoding (ex.: **OpenCage**, **Nominatim**, **HERE Geocoding**) com "Hub X, Cidade, Brasil".
  - Comparar (lat, lng) retornado com o que está no JSON; se divergir além de um limiar (ex.: 500 m), marcar para revisão ou atualizar.
- **Benefício:** Menos rotas calculadas a partir de “centro do município” quando o CD fica em ponto específico; waypoints derivados seguem melhor o corredor real.

### 3.2 Validação de waypoints (reverse geocoding)

- **Objetivo:** Saber se um waypoint está em rodovia ou em via paralela.
- **Uso:** Para cada `via` (lat, lng):
  - Chamar **reverse geocoding** (OpenCage, Nominatim, HERE, etc.).
  - Verificar se o endereço/nome da via retornado contém a rodovia esperada (ex.: "BR-116", "BR-101") ou indica rodovia (ex.: "Rodovia", "BR").
- **Benefício:** Waypoints que caem em “Rua paralela” ou “Fazenda” podem ser sinalizados para ajuste ou snap-to-road; reduz falsos “na rota” e melhora a noção de colisão por engarrafamento.

### 3.3 Cruzar com HERE (que você já usa)

- Você já usa **HERE Geocoding** em `here_traffic.py` (`_geocode_endereco`, `_parse_ou_geocode`) para origem/destino em texto.
- Pode **complementar** com outra fonte (ex.: OpenCage ou Nominatim):
  - Se HERE e OpenCage devolverem coordenadas muito diferentes para o mesmo hub, é sinal para revisar o dado.
  - Isso não substitui HERE para o fluxo principal; apenas adiciona uma camada de validação/normalização.

### 3.4 Snap-to-road (não é “só” geocoding)

- Para **máxima precisão dos waypoints**, o ideal é **snap-to-road** (map matching):
  - Entrada: (lat, lng).
  - Saída: ponto na rede viária mais próximo (ou polyline ajustada).
- Serviços úteis:
  - **Mapbox Map Matching API**
  - **HERE** (ex.: considerando recurso de matching na mesma conta que você já usa)
  - **OpenRouteService** (directions + geocoding; pode ajudar a obter pontos “na rota”)
- Isso reduz “waypoint ao lado da rodovia” e melhora diretamente a colisão com segmentos de tráfego HERE/TomTom.

---

## 4. APIs do publicapis.io que fazem sentido

| API | Recurso | Uso no projeto |
|-----|---------|-----------------|
| **HERE Geocoding** | Forward + reverse | Já usado; pode expandir para reverse nos waypoints e validação de hubs. |
| **OpenCage** | Forward + reverse, boa cobertura Brasil | Normalizar hubs, validar waypoints (reverse). |
| **Nominatim** (OSM) | Forward + reverse, gratuito | Mesmo uso que OpenCage; alternativa sem custo. |
| **OpenRouteService** | Directions, geocoding, isochrones | Pontos na rota / snap; complementar ao HERE Routing. |
| **Mapbox** | Geocoding + **Map Matching** | Snap-to-road para waypoints (maior ganho de precisão). |
| **Google Maps** | Geocoding + Routes (já usa) | Geocoding para normalizar endereços de hubs, se quiser manter tudo no mesmo ecossistema. |

- **Prioridade para “colisão por engarrafamento” e waypoints:**  
  1) **Snap-to-road** (Mapbox/HERE/ORS),  
  2) **Reverse geocoding** para validar waypoints,  
  3) **Forward geocoding** para normalizar hubs.

---

## 5. Sobre “acuracia 100%”

- **100% de acurácia** em rede viária **não é atingível** na prática:
  - Diferenças entre provedores (HERE vs TomTom vs Google).
  - Obras, desvios, mudanças de sentido, atraso de atualização.
  - Segmentação diferente de vias (um “engarrafamento” pode ser um segmento na HERE e dois no TomTom).
- O que **é** possível:
  - **Maximizar** precisão: waypoints **na** rodovia (snap-to-road), hubs normalizados (geocoding), validação por reverse geocoding.
  - Manter e melhorar filtros já existentes (distância à polyline 150 m / 500 m, texto BR-xxx).
  - Documentar limitações (como em `ANALISE_PRECISAO.md`) e tratar “alta precisão” como meta, não como garantia absoluta.

---

## 6. Sugestões práticas de integração

### 6.1 Script de validação de waypoints (reverse geocoding)

- Script (ex.: Python) que:
  - Lê `rota_logistica.json`.
  - Para cada rota, para cada `via` (lat, lng):
    - Chama reverse geocoding (OpenCage ou Nominatim).
    - Verifica se o resultado contém a rodovia esperada (`rodovia_logica`).
  - Gera relatório: waypoints “OK” vs “suspeitos” (não batem com a rodovia).
- Uso: rodar antes de publicar alterações em `rota_logistica.json` ou em pipeline de CI.

### 6.2 Normalização de hubs (forward geocoding)

- Ao adicionar/editar rotas:
  - Geocodificar cada `hub` com uma API (ex.: OpenCage) e comparar com `lat`/`lng` do JSON.
  - Se diferença > ~500 m, alertar ou atualizar coordenadas a partir do geocoding.
- Pode ser um script separado ou passo no mesmo script de validação.

### 6.3 Snap-to-road dos waypoints (quando possível)

- Se integrar Mapbox ou HERE Map Matching (ou ORS):
  - Para cada waypoint em `via`, chamar snap-to-road e substituir (lat, lng) pelo ponto na rodovia.
  - Regenerar `rota_logistica.json` ou manter uma “versão refinada” usada só pela coleta.
- Maior impacto na precisão da colisão engarrafamento/waypoints.

### 6.4 Variáveis de ambiente

- Não hardcodar chaves. Exemplo:
  - `OPENCAGE_API_KEY` ou `NOMINATIM_*` para geocoding de validação.
  - `MAPBOX_ACCESS_TOKEN` se usar Map Matching.
- Documentar no README e em `docs/setup` quais APIs opcionais são usadas para precisão.

---

## 7. Resumo

- **Sim**, as APIs listadas em [publicapis.io/category/geocoding](https://publicapis.io/category/geocoding) podem **complementar** o projeto:
  - **Forward geocoding:** normalizar e validar hubs (origem/destino).
  - **Reverse geocoding:** validar se waypoints estão na rodovia esperada.
- Para **melhor precisão de colisão por engarrafamento** e waypoints, o maior ganho vem de:
  1. **Snap-to-road** (Mapbox/HERE/OpenRouteService), quando possível.
  2. Validação de waypoints com **reverse geocoding**.
  3. Normalização de hubs com **forward geocoding** (HERE já usado + OpenCage/Nominatim para cruzar).
- **“Acuracia 100%”** não é realista; o que faz sentido é **maximizar** precisão com esses recursos e manter filtros e documentação de limitações já existentes no projeto.

Se quiser, o próximo passo pode ser: (a) um script de validação de waypoints + hubs usando uma API gratuita (ex.: Nominatim) ou (b) esboço de integração de snap-to-road com uma API específica (ex.: Mapbox ou HERE).
