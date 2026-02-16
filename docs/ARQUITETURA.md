# Arquitetura - RodoviaMonitor Pro

## 1. Objetivo

Fornecer uma pipeline de monitoramento de transito para rotas logisticas, com coleta paralela, correlacao de sinais e saida operacional em Excel.

## 2. Contexto do sistema

Entradas:

- Configuracoes YAML (`config.yaml` / `config_mvp.yaml`).
- Base de rotas e pontos de referencia (`pontos_referencia_28_rotas.yaml`).
- Credenciais de API no ambiente (`GOOGLE_MAPS_API_KEY`, `HERE_API_KEY`).

Saidas:

- Relatorios Excel em `relatorios/`.
- Logs de execucao em stdout (texto ou JSON).

## 3. Visao de componentes

```text
+-------------------------+
| main.py (orquestrador)  |
+------------+------------+
             |
             v
+-------------------------+      +--------------------------+
| Coleta paralela         |----->| sources/google_maps.py   |
| (ThreadPoolExecutor)    |      +--------------------------+
|                         |----->| sources/here_traffic.py  |
+------------+------------+      +--------------------------+
             |
             v
+-------------------------+
| sources/correlator.py   |
| Unificacao por trecho   |
+------------+------------+
             |
             v
+-------------------------+
| sources/advisor.py      |
| (apenas no modo MVP)    |
+------------+------------+
             |
             v
+-------------------------+
| report/excel_generator.py|
+-------------------------+
```

## 4. Fluxo de execucao

1. `main.py` carrega `.env`, YAML e lista de trechos.
2. Valida se as fontes estao habilitadas e com chave configurada.
3. Dispara coleta paralela das fontes ativas.
4. Cada fonte consulta todos os trechos com paralelismo interno.
5. `correlator.py` combina status, ocorrencia, descricao e links por trecho.
6. No modo MVP, `DataAdvisor` calcula score de confianca (`confianca_pct`).
7. `excel_generator.py` materializa o relatorio final.

## 5. Responsabilidades por modulo

- `main.py`
  - CLI, carga de config, scheduler, polling e orquestracao.
- `sources/google_maps.py`
  - Consulta Directions/Routes API com fallback e classificacao por atraso.
- `sources/here_traffic.py`
  - Consulta Incidents e Flow, parse de eventos e classificacao de fluxo.
- `sources/km_calculator.py`
  - Estimativa de KM e trecho especifico por interpolacao geografica.
- `sources/correlator.py`
  - Regras de priorizacao de status/ocorrencia e consolidacao final.
- `sources/advisor.py`
  - Score de confianca baseado em freshness e peso por fonte (MVP).
- `sources/circuit.py`
  - Circuit breakers para APIs externas.
- `report/excel_generator.py`
  - Renderizacao de planilha (modo completo ou simplificado).

## 6. Concorrencia

Camadas de paralelismo:

- Orquestracao: `main.py` executa HERE e Google simultaneamente (`max_workers=2`).
- HERE: processa trechos em paralelo (`max_workers=5`).
- Google: processa trechos em paralelo (`max_workers=3`).

## 7. Resiliencia e seguranca

- Retry HTTP com `urllib3.Retry` para 429 e 5xx.
- Circuit breaker por fonte (`fail_max=5`, `reset_timeout=60s`).
- Validacao de resposta JSON antes do parse.
- Sanitizacao de erro para ocultar API keys em logs.
- Alertas de degradacao quando fonte retorna falha em todos os trechos.

## 8. Modelo de dados consolidado (por trecho)

Campos principais gerados apos correlacao:

- Identificacao: `trecho`, `rodovia`, `tipo`, `concessionaria`, `sentido`.
- Mobilidade: `status`, `jam_factor`, `duracao_normal_min`, `duracao_transito_min`, `atraso_min`.
- Incidente: `ocorrencia`, `descricao`, `km_ocorrencia`, `trecho_especifico`, `localizacao_precisa`.
- Operacao: `confianca`, `acao_recomendada`, `fontes_utilizadas`, `fontes_confirmacao`, `consultado_em`.

## 9. Decisoes tecnicas

- Sem banco de dados: foco em simplicidade e entrega operacional via Excel.
- Correlacao orientada a regras: previsibilidade para uso em operacao logistica.
- CLI unica para execucao, polling e agendamento: menor custo operacional.
- Config declarativa em YAML para ajustar fontes e politicas sem alterar codigo.

## 10. Extensao futura

- Adicionar camada de persistencia historica (parquet/DB).
- Publicar API interna para consumo em dashboard.
- Incluir observabilidade com metricas (latencia por fonte, taxa de erro, degradacao por rota).
- Evoluir a correlacao para motor de regras versionado por perfil de operacao.
