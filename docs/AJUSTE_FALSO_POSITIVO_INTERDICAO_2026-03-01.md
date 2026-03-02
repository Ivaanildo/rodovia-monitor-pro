# Ajuste de Falsos Positivos de Interdição

> Data: 2026-03-01  
> Contexto: correções aplicadas após análise do relatório `rodoviamonitor_pro_20260301_102724.xlsx`

---

## Objetivo

Reduzir falsos positivos de `Interdição` no monitoramento logístico, garantindo que:

- `Interdição` represente apenas fechamento total confirmado da via
- acidentes sem fechamento total apareçam como `Colisão`
- fechamento de faixa com tráfego fluindo apareça como `Bloqueio Parcial`
- ocorrências em ruas e avenidas locais fora da BR monitorada não contaminem a rota logística

---

## Problemas encontrados

### 1. Classificação excessiva de interdição

Antes do ajuste, incidentes com sinal de bloqueio ou descrições genéricas podiam ser promovidos para `Interdição` com muita facilidade.

Consequências:

- bloqueios parciais eram tratados como se a via estivesse totalmente fechada
- acidentes com desvio possível podiam aparecer como `Interdição`
- a descrição operacional sugeria retorno obrigatório mesmo quando ainda havia passagem

### 2. Falso positivo urbano por filtro frouxo da HERE

Mesmo após corrigir a classificação, o relatório ainda mostrava casos como:

- `entre av amazonas e r santa maria`

Esses eventos eram locais, urbanos, e não citavam explicitamente `BR-116` ou `BR-101`, mas ainda assim entravam em rotas como:

- `Minas Gerais (Betim) -> Pernambuco (Cabo)`
- `São Paulo (Cajamar) -> Pernambuco (Cabo)`

Causa raiz:

- o filtro da HERE aceitava incidentes sem código de rodovia quando eles caíam dentro do `corridor`
- como a rota inclui origem, destino e waypoints, um trecho urbano próximo ao traçado podia ser tratado como relevante mesmo sem evidência textual de BR

---

## Correções aplicadas

## 1. Nova regra de classificação de incidentes

Arquivos impactados:

- `sources/here_traffic.py`
- `sources/tomtom_api.py`
- `sources/correlator.py`

### Nova semântica de saída

- `Interdição`: apenas quando há evidência de fechamento total
- `Bloqueio Parcial`: faixa fechada, restrição de pista ou passagem com retenção
- `Colisão`: acidente sem evidência de fechamento total

### Regra usada

- Se a fonte indicar bloqueio total por flag estruturada ou texto explícito:
  - categoria final = `Interdição`
- Se não houver bloqueio total e houver indício de acidente:
  - categoria final = `Colisão`
- Se houver bloqueio parcial sem fechamento total:
  - categoria final = `Bloqueio Parcial`

### Campos internos adicionados

Para melhorar a rastreabilidade interna, os incidentes normalizados passaram a carregar:

- `bloqueio_escopo`: `total`, `parcial` ou `nenhum`
- `causa_detectada`: `acidente`, `obra`, `risco`, `clima` ou `indefinida`

No TomTom também foi incluído:

- `icon_category_raw`

### Efeito na descrição

A descrição do correlator foi ajustada para refletir a nova semântica:

- `Interdição Total (...)` quando o fechamento total for confirmado
- explicitação do motivo quando disponível (ex.: acidente, deslizamento)
- linguagem de `Bloqueio Parcial` sem sugerir retorno obrigatório

---

## 2. Filtro semântico mais rígido por rodovia na HERE

Arquivo impactado:

- `sources/here_traffic.py`

Função alterada:

- `_incidente_relevante_para_rodovia()`

### Regra anterior

Se a rota estivesse em `corridor` ou `corridor_segmentado`, um incidente sem código de rodovia podia ser aceito apenas porque estava geometricamente dentro do corredor.

Isso deixava passar eventos urbanos locais em:

- avenidas
- ruas
- acessos municipais

### Regra nova

Quando a rota monitorada tem código reconhecível no filtro (`BR-116`, `BR-101`, `BR-050`, etc.):

- o incidente só é aceito se também citar um código de rodovia compatível
- se o incidente não citar nenhum código explícito:
  - ele é rejeitado, mesmo em `corridor`
  - ele é rejeitado, mesmo em `corridor_segmentado`
  - ele é rejeitado em `bbox`

Quando o filtro não tem código reconhecível:

- o fallback textual legado é mantido para compatibilidade

### Resultado esperado

Um evento como:

- `entre av amazonas e r santa maria`

deixa de ser associado a uma rota filtrada por `BR-116` ou `BR-116 / BR-101` se ele não citar a BR explicitamente.

---

## Exemplo prático corrigido

### Antes

Relatório podia exibir:

- `Interdição Total (HERE) em KM 18.8, proximo a Minas Gerais: entre av amazonas e r santa maria - , estrada`

para a rota:

- `Minas Gerais (Betim) -> Pernambuco (Cabo)`

Mesmo sem o incidente citar `BR-116`.

### Depois

Esse incidente deixa de ser elegível para a rota, porque:

- a rota tem `rodovia_logica = ["BR-116"]`
- o incidente não informa nenhum código de rodovia
- a geometria sozinha não é mais suficiente para validar relevância

---

## Testes adicionados e atualizados

Arquivos de teste impactados:

- `tests/test_here_traffic.py`
- `tests/test_correlator.py`
- `tests/test_correlator_ab.py`
- `tests/test_tomtom_api.py`

### Cobertura adicionada

- `roadClosed=true` classifica como `Interdição`
- `laneRestriction` classifica como `Bloqueio Parcial`
- acidente sem fechamento total classifica como `Colisão`
- `roadHazard` genérico sem totalidade não vira `Interdição`
- `roadHazard` com `bloqueio total` vira `Interdição`
- incidente sem BR explícita é rejeitado em `corridor`
- incidente sem BR explícita é rejeitado em `corridor_segmentado`
- incidente sem BR explícita é rejeitado em rota multi-BR
- fallback legado textual permanece funcional quando o filtro não tem código reconhecível

### Execução validada

Foi validado com:

- `python -m pytest monitor-rodovias\tests\test_here_traffic.py -q`

Resultado:

- `35 passed`

Também foi validado diretamente que um incidente com descrição:

- `entre av amazonas e r santa maria - , estrada`

retorna `False` no filtro de relevância para:

- `BR-116`
- `BR-116 / BR-101`

---

## Tradeoff assumido

Ao endurecer o filtro por rodovia, o sistema passa a priorizar a redução de falso positivo.

Tradeoff aceito:

- alguns incidentes reais em BR podem deixar de entrar se a HERE não informar explicitamente o código da rodovia

Essa escolha foi deliberada porque, para o uso logístico, um falso positivo urbano em via local atrapalha mais a operação do que um falso negativo eventual sem identificação de BR.

---

## Como validar em produção

Após uma nova coleta:

1. Gerar um novo relatório Excel
2. Revisar as rotas:
   - `Minas Gerais (Betim) -> Pernambuco (Cabo)`
   - `São Paulo (Cajamar) -> Pernambuco (Cabo)`
3. Confirmar que ocorrências baseadas em:
   - `av amazonas`
   - `r santa maria`
   não aparecem mais como interdição da rota
4. Confirmar que incidentes que citem explicitamente:
   - `BR-116`
   - `BR-101`
   continuam aparecendo normalmente

---

## Resumo executivo

Foram aplicadas duas correções complementares:

1. Ajuste da semântica de `Interdição`, para reservar o rótulo apenas a fechamento total real.
2. Endurecimento do filtro da HERE por rodovia, exigindo que o incidente cite a BR monitorada quando a rota possui `rodovia_logica` reconhecível.

Com isso, o monitoramento fica mais coerente com o uso logístico real e reduz o ruído de ocorrências urbanas fora da rodovia principal da rota.
