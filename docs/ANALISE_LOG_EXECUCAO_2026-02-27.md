# Analise do Log de Execucao - 27/02/2026

## Contexto

Este documento resume e explica o log da execucao iniciada em `27/02/2026 22:47:33` e finalizada em `22:53:04`.

Resumo operacional:

- `20` trechos carregados e processados
- fontes ativas: `Google Maps`, `HERE Traffic` e `TomTom`
- duracao aproximada do ciclo: `5m31s`
- completude da coleta: `100%`
- relatorio Excel gerado com sucesso

## Leitura geral do ciclo

O ciclo seguiu esta ordem:

1. carrega os trechos do arquivo JSON
2. inicializa as fontes disponiveis
3. consulta `Google Maps`, `HERE` e `TomTom`
4. correlaciona os dados das fontes
5. calcula confianca
6. gera o relatorio Excel

## Inicio da execucao

Linhas como:

- `Trechos carregados (formato routes) ...: 20`
- `Trechos configurados: 20`
- `Fontes ativas: Google Maps, HERE Traffic, TomTom`
- `Google Routes config: routing_preference=TRAFFIC_AWARE_OPTIMAL`

significam que:

- o arquivo de rotas foi lido corretamente
- ha `20` rotas validas no lote
- as tres integracoes estavam habilitadas
- o Google esta usando calculo de rota com sensibilidade a transito

## Bloco Google Maps

Linhas como:

- `Google Maps [4/20]: ...`
- `[trecho] Moderado - 430min (normal: 416min) [Routes v2/TRAFFIC_AWARE_OPTIMAL]`

indicam:

- qual trecho esta sendo consultado no momento
- o tempo atual com transito (`430min`)
- o tempo de referencia sem transito (`416min`)
- a classificacao operacional derivada do atraso (`Normal`, `Moderado` ou `Intenso`)

Na pratica, o Google esta sendo usado principalmente para:

- ETA com transito
- comparacao entre tempo normal e tempo atual
- reforco na classificacao de severidade

## Bloco HERE Traffic

Linhas como:

- `HERE: processando 20 trechos em 3 chunk(s) de ate 7`
- `HERE chunk 1/3: 7 trechos`

indicam que o HERE processa os trechos em lotes, com pausa entre os lotes, para reduzir risco de erro por volume ou limite de API.

### Segmentacao de corridor

Linhas como:

- `Secao 1/11 corridor longo (...), tentando downsample...`
- `Polyline RDP: 2986 -> 223 pontos ...`
- `Corridor segmentado: 11/11 secoes com corridor (via 10 waypoints)`

significam:

- a rota foi dividida em varias secoes porque e longa
- a polyline foi simplificada para caber nos limites da API
- o sistema conseguiu montar corridors por secao usando waypoints

`downsample` e a reducao do numero de pontos da geometria da rota. Isso evita que a API rejeite corridors grandes demais.

### Incidentes HERE

Linhas como:

- `HERE bruto=2 | filtrado=1 incidente(s) via corridor_segmentado`
- `1 incidente(s): Interdicao`

significam:

- a API retornou uma quantidade inicial de incidentes (`bruto`)
- depois o sistema filtrou apenas os que realmente fazem sentido para a rota (`filtrado`)
- o resultado final foi usado na correlacao

Quando aparece `via corridor_segmentado`, a consulta foi feita usando o caminho real da rota, o que e mais preciso.

Quando aparece `via bbox`, houve fallback para caixa geografica, o que e menos preciso, mas ainda util.

### Fluxo HERE

Linhas como:

- `Fluxo: Normal (jam=0.4)`
- `Fluxo: Normal (jam=1.2)`

indicam o estado medio do trafego no trecho.

Interpretacao do `jam`:

- `0` = livre
- ate `2` = Normal
- ate `5` = Moderado
- ate `8` = Intenso
- acima disso = Parado

O HERE tambem tenta evitar que a media esconda pontos ruins. Por isso ele considera:

- `jam_factor` medio
- `jam_factor_max`
- quantidade de segmentos congestionados

## Bloco TomTom

Linhas como:

- `TomTom: processando 20 trechos (4 workers)`
- `TomTom [1/20]: ...`

indicam que o TomTom roda em paralelo com `4` workers.

### Erros em incidentes

As linhas mais importantes sao:

- `TomTom Incidents erro: 400 Client Error: Bad Request ...`

Isso indica que a requisicao de incidentes foi aceita pela rede, mas rejeitada pela API como invalida.

Como o erro foi recorrente em praticamente todos os trechos, o mais provavel e um problema estrutural na chamada, por exemplo:

- `bbox` em formato ou ordem inadequada
- parametros do endpoint incompatveis com a versao usada
- combinacao de filtros nao aceita pela API

Consequencia pratica:

- o TomTom quase nao contribuiu com incidentes nesta execucao
- isso fica evidente na linha final: `TomTom: 0 incidente(s) total em 20 trechos`

### Erros em fluxo

Algumas linhas mostram:

- `TomTom Flow erro: 400 Client Error: Bad Request ...`

Nesses casos, o sistema continua e registra:

- `TomTom Fluxo: Sem dados (jam=0)`

Ou seja, o ciclo nao para; apenas perde aquela contribuicao especifica.

## Repeticao de blocos HERE

Os blocos de `Secao X/Y`, `Polyline RDP` e `Corridor segmentado` podem aparecer duas vezes para o mesmo trecho.

Isso e esperado porque o sistema faz duas consultas separadas no HERE:

- uma para `incidents`
- outra para `flow`

Nao e duplicacao incorreta; sao duas etapas diferentes sobre a mesma rota.

## Correlacao entre fontes

Quando aparece:

- `Correlacionando dados das fontes...`

o sistema junta os resultados de `Google Maps`, `HERE` e `TomTom` e decide:

- `status` final do trecho
- `ocorrencia` principal
- quais fontes sustentam a decisao

Exemplo:

- `Status=Intenso | Ocorrencia=Interdicao | Fontes: Google Maps, HERE Flow, HERE Incidents, TomTom Flow`

Interpretacao:

- havia evidencias suficientes de severidade alta
- a principal causa identificada foi `Interdicao`
- o TomTom ajudou apenas com fluxo, nao com incidentes

## Conflito de fontes

Linha como:

- `Conflito de fontes em [Rio de Janeiro (Capital) -> Minas Gerais (Betim)]: HERE indica Normal mas Google mostra atraso de 35 min`

significa que:

- o HERE avaliou o fluxo medio como `Normal`
- o Google observou atraso relevante no tempo de viagem
- as fontes divergiram de forma suficiente para gerar alerta interno

Quando isso ocorre:

- o sistema ainda fecha um resultado
- mas reduz a confianca da leitura

## Resultado final do ciclo

Linhas finais:

- `HERE Traffic: 25 incidente(s) encontrado(s)`
- `TomTom: 0 incidente(s) encontrado(s)`
- `Confianca: 12 alta | 8 media | 0 baixa`
- `Resultado: {'Intenso': 8, 'Normal': 6, 'Moderado': 6} | 14 ocorrencia(s)`
- `Completude da coleta: 20/20 trechos com dados (100.0%)`

Leitura objetiva:

- a fonte principal de incidentes foi o `HERE`
- o `TomTom` falhou para incidentes nesta execucao
- a cobertura geral foi completa
- o processamento terminou com dados suficientes para todos os trechos

## Conclusao operacional

Esta execucao foi bem-sucedida.

O que funcionou:

- carga dos `20` trechos
- consultas do `Google Maps`
- consultas do `HERE` com segmentacao e fallback
- correlacao e calculo de confianca
- geracao do relatorio Excel

O principal problema observado:

- `TomTom Incidents` retornando `400 Bad Request` de forma recorrente

Impacto real:

- o sistema continuou operando normalmente porque `Google Maps` e `HERE` sustentaram a analise
- a perda ficou concentrada na camada de incidentes do TomTom

## Arquivo gerado no ciclo analisado

Relatorio salvo em:

- `C:\\Users\\Administrador\\Desktop\\Automacao Monitaramento de rotas Logisticas\\monitor-rodovias\\relatorios\\rodoviamonitor_pro_20260227_225304.xlsx`
