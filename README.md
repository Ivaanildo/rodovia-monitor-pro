# RodoviaMonitor Pro

Bot que monitora o transito de rodovias brasileiras em tempo real e gera relatorios Excel para operacoes logisticas.

**Problema:** equipes de logistica precisam saber, a cada ciclo de monitoramento, quais rodovias estao com incidentes, congestionamentos ou restricoes — sem abrir manualmente dezenas de fontes de transito.

**Solucao:** o RodoviaMonitor consulta as APIs do HERE Traffic e Google Maps em paralelo, cruza os dados por trecho e entrega uma planilha pronta para decisao operacional.

---

## Como funciona

```text
CLI (main.py)
  -> Carrega config YAML + .env
  -> Coleta paralela (ThreadPoolExecutor)
      -> HERE Traffic (incidentes + fluxo)
      -> Google Maps (duracao com transito)
  -> Correlaciona dados por trecho
  -> Calcula score de confianca (modo MVP)
  -> Gera relatorio Excel
```

- **28 rotas** configuraveis por arquivo YAML
- **~12-15 segundos** por ciclo de coleta (paralelo)
- **Retry automatico** em erros HTTP transientes (429/5xx)
- **Circuit breaker** por fonte para evitar cascata de falhas
- **Custo: $0/mes** (APIs dentro dos free tiers)

## Requisitos

- Python 3.10+
- Chaves de API:
  - `GOOGLE_MAPS_API_KEY` — [Google Cloud Console](https://console.cloud.google.com/)
  - `HERE_API_KEY` — [HERE Developer](https://developer.here.com/)

## Setup

```bash
git clone https://github.com/Ivaanildo/rodovia-monitor-pro.git
cd rodovia-monitor-pro

python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
```

Crie o arquivo `.env` na raiz:

```env
GOOGLE_MAPS_API_KEY=sua_chave_google
HERE_API_KEY=sua_chave_here
```

## Uso

```bash
# Execucao unica (modo MVP)
python main.py --modo-mvp --config config_mvp.yaml

# Polling a cada 30 minutos
python main.py --modo-mvp --interval 30

# Modo completo (4 fontes)
python main.py --config config.yaml

# Agendamento por horarios fixos (06h, 12h, 18h)
python main.py --agendar

# Logs em JSON estruturado
python main.py --modo-mvp --log-json
```

O relatorio Excel e salvo em `relatorios/`.

## Estrutura do projeto

```
├── main.py                          # Orquestrador CLI
├── config.yaml                      # Config modo completo
├── config_mvp.yaml                  # Config modo MVP
├── pontos_referencia_28_rotas.yaml  # Base de rotas e segmentos
├── requirements.txt
├── sources/
│   ├── google_maps.py               # Client Google Maps
│   ├── here_traffic.py              # Client HERE Traffic
│   ├── correlator.py                # Motor de correlacao
│   ├── advisor.py                   # Score de confianca (MVP)
│   ├── km_calculator.py             # Estimativa de KM por trecho
│   └── circuit.py                   # Circuit breakers
├── report/
│   └── excel_generator.py           # Gerador de planilhas
├── tests/                           # Testes unitarios
├── tools/
│   └── validar_rotas_fatos.py       # Validador de rotas
└── docs/
    ├── ARQUITETURA.md
    └── OPERACAO.md
```

## Testes

```bash
python -m pytest tests/ -v --tb=short

# Com cobertura
python -m pytest tests/ --cov=sources --cov=report --cov-report=term-missing -q
```

## Resiliencia

| Mecanismo | Detalhe |
|-----------|---------|
| Retry HTTP | 3 tentativas, backoff 0.5s, cobre 429/5xx |
| Circuit Breaker | 5 falhas abre, reset em 60s |
| Validacao JSON | Verifica Content-Type antes de parsear |
| Sanitizacao | API keys nunca aparecem em logs |
| Degradacao | Alerta quando uma fonte falha em todos os trechos |

## Licenca

MIT
