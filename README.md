# RodoviaMonitor Pro

Monitora o tráfego de **20 rodovias logísticas brasileiras** em tempo real, com dashboard web ao vivo e relatórios Excel para operações logísticas.

**Problema:** equipes de logística precisam saber, a cada ciclo de monitoramento, quais rodovias estão com incidentes, congestionamentos ou restrições — sem abrir manualmente dezenas de fontes de trânsito.

**Solução:** o RodoviaMonitor consulta 3 APIs de tráfego em paralelo (HERE, Google, TomTom), cruza os dados por trecho, persiste no Supabase e exibe em um dashboard ao vivo com Realtime WebSocket.

---

## Stack (estado Mar 2026)

| Camada | Tecnologia |
|--------|------------|
| Coleta de dados | Python 3.11 — `main.py` + GitHub Actions (cron horário) |
| Banco de dados | Supabase PostgreSQL (`ciclos` + `snapshots_rotas`) |
| Realtime | Supabase Realtime WebSocket (publicação `supabase_realtime`) |
| Frontend | React 19 + Vite 7 + CSS Modules — deploy Vercel |
| Autenticação | Supabase Auth (email/senha) |
| Custo | $0/mês (todos dentro dos free tiers) |

---

## Como funciona

```text
GitHub Actions (cron a cada hora)
  -> main.py --config config.json
      -> Carrega rota_logistica.json (20 rotas R01–R20)
      -> Coleta paralela (ThreadPoolExecutor)
          -> HERE Traffic (incidentes + fluxo, corredor segmentado)
          -> Google Maps (ETA com trânsito — chave pode estar expirada)
          -> TomTom (incidentes v5 + fluxo v4)
      -> Correlaciona dados por trecho (conflict detection)
      -> Calcula score de confiança (DataAdvisor)
      -> Persiste no Supabase (tabelas ciclos + snapshots_rotas)
      -> Gera relatório Excel (artefato do GitHub Actions)

Dashboard (Vercel — monitor-rodovias.vercel.app)
  -> Login via Supabase Auth
  -> Carrega último ciclo via REST (fetchInitialData)
  -> Escuta INSERTs via Supabase Realtime WebSocket
  -> Fallback: polling a cada 60s se Realtime não conectar
```

---

## Rotas monitoradas (20 corredores)

| ID | Corredor | Rodovia |
|----|----------|---------|
| R01 | SP (Cajamar) → PE (Cabo) | BR-116 / BR-101 |
| R02 | SP (Cajamar) → BA (Lauro de Freitas) | BR-381 / BR-116 |
| R03 | SP (Cajamar) → RJ (Pavuna) | BR-116 Dutra |
| R04 | SP (Cajamar) → MG (Betim) | BR-381 Fernão Dias |
| R05 | SP (Cajamar) → SC (Gov. Celso Ramos) | BR-116 Régis Bittencourt |
| R06 | SP (Cajamar) → RS (Sapucaia) | BR-116 |
| R07 | SP (Cajamar) → DF (Brasília) | BR-050 |
| R08 | MG (Betim) → PE (Cabo) | BR-116 |
| R09 | MG (Betim) → BA (Lauro de Freitas) | BR-116 |
| R10 | SP (Cajamar) → PR (Curitiba) | BR-116 |
| R11 | SC (GCR) → RJ (Capital) | BR-101 / BR-116 |
| R12 | SP (Cajamar) → GO (Goiânia) | BR-050 |
| R13 | SC (GCR) → PR (Curitiba) | BR-101 / BR-376 |
| R14 | SP (Cajamar) → ES (Serra) | BR-116 / BR-101 |
| R15 | RJ (Capital) → MG (Betim) | BR-040 |
| R16 | BA (Lauro de Freitas) → SE (Aracaju) | BR-101 |
| R17 | BA (Lauro de Freitas) → PE (Cabo) | BR-101 |
| R18 | SP (Cajamar) → MT (Cuiabá) | BR-364 |
| R19 | SP (Cajamar) → MS (Campo Grande) | BR-262 |
| R20 | RS (Sapucaia) → SC (GCR) | BR-101 |

---

## Setup completo (do zero)

Siga os guias na ordem:

1. [`docs/setup/01-SUPABASE.md`](docs/setup/01-SUPABASE.md) — Criar projeto, tabelas, RLS, Realtime e usuário
2. [`docs/setup/02-GITHUB.md`](docs/setup/02-GITHUB.md) — Configurar repositório e GitHub Actions (secrets)
3. [`docs/setup/03-VERCEL.md`](docs/setup/03-VERCEL.md) — Deploy do frontend e variáveis de ambiente

### Variáveis de ambiente necessárias

**GitHub Actions Secrets** (Settings → Secrets → Actions):

| Secret | Descrição |
|--------|-----------|
| `GOOGLE_MAPS_API_KEY` | Google Maps Routes API v2 |
| `HERE_API_KEY` | HERE Traffic + Routing |
| `TOMTOM_API_KEY` | TomTom Incidents + Flow |
| `SUPABASE_DB_URL` | Connection string PostgreSQL do Supabase (porta 6543 — pooler) |

**Vercel Environment Variables** (Settings → Environment Variables):

| Variável | Descrição |
|----------|-----------|
| `VITE_SUPABASE_URL` | URL do projeto Supabase (`https://xxx.supabase.co`) |
| `VITE_SUPABASE_ANON_KEY` | Anon key do projeto Supabase (JWT — deve ser exato, sem espaços) |

> **Atenção:** o Supabase Realtime valida o JWT com rigor. Qualquer caractere diferente na `VITE_SUPABASE_ANON_KEY` resulta em erro WebSocket 400. Copie a chave diretamente de **Project Settings → API → anon/public**.

---

## Desenvolvimento local

```bash
git clone https://github.com/Ivaanildo/rodovia-monitor-pro.git
cd rodovia-monitor-pro

# Backend Python
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux/Mac
pip install -r requirements.txt

# Coleta manual (requer secrets no .env)
python main.py --config config.json

# Frontend React
cd frontend
npm install
npm run dev       # http://localhost:5173
npm run build     # gera dist/
```

Crie `.env` na raiz do repositório para desenvolvimento local:

```env
GOOGLE_MAPS_API_KEY=sua_chave_google
HERE_API_KEY=sua_chave_here
TOMTOM_API_KEY=sua_chave_tomtom
SUPABASE_DB_URL=postgresql://postgres.[ref]:[senha]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
```

---

## Estrutura do projeto

```
monitor-rodovias/
├── main.py                      # Orquestrador CLI (coleta + persistência)
├── config.json                  # Config: APIs, chunk_size, agendamento
├── rota_logistica.json          # 20 rotas (R01–R20) com waypoints HERE
├── requirements.txt
├── .github/
│   └── workflows/
│       └── monitor.yml          # GitHub Actions: cron horário + manual
├── frontend/                    # React 19 + Vite 7 (deploy Vercel)
│   ├── src/
│   │   ├── services/
│   │   │   └── supabase.js      # Cliente Supabase (URL + anon key)
│   │   ├── hooks/
│   │   │   ├── useAuth.js       # Supabase Auth (login/logout/sessão)
│   │   │   └── useSupabaseRealtime.js  # WebSocket + fallback polling
│   │   ├── components/
│   │   │   ├── KpiCard          # Métrica com barra proporcional
│   │   │   ├── RotaCard         # Card de rota com status colorido
│   │   │   ├── Ticker           # Faixa de alertas críticos
│   │   │   ├── RealtimeIndicator # Indicador: Ao vivo / Polling 60s
│   │   │   └── Clock            # Relógio atualizado a cada 10s
│   │   └── pages/
│   │       ├── LoginPage        # Login via Supabase Auth
│   │       └── PainelPage       # Dashboard: KPIs + cards + polling
│   └── package.json
├── sources/
│   ├── google_maps.py           # Google Maps Routes API v2
│   ├── here_traffic.py          # HERE Traffic v7 + Routing v8
│   ├── tomtom_api.py            # TomTom Incidents v5 + Flow v4
│   ├── correlator.py            # Motor de correlação multi-fonte
│   ├── advisor.py               # Score de confiança (DataAdvisor)
│   └── circuit.py               # Circuit breakers por API
├── storage/
│   ├── database.py              # SQLAlchemy engine (Supabase PostgreSQL)
│   ├── models.py                # Tabelas: ciclos + snapshots_rotas
│   └── repository.py            # RotaRepository (salvar_ciclo, histórico)
├── report/
│   └── excel_generator.py       # Gerador de planilhas Excel
└── docs/
    └── setup/
        ├── 01-SUPABASE.md
        ├── 02-GITHUB.md
        └── 03-VERCEL.md
```

---

## Schema do banco (Supabase PostgreSQL)

```sql
-- Um registro por ciclo de coleta
CREATE TABLE ciclos (
    id            SERIAL PRIMARY KEY,
    ts            TEXT NOT NULL,        -- "DD/MM/YYYY HH:MM:SS"
    ts_iso        TEXT NOT NULL,        -- "2026-03-01T02:40:00"
    fontes        TEXT NOT NULL DEFAULT '[]',  -- JSON: APIs usadas
    total_trechos INTEGER NOT NULL DEFAULT 0
);

-- Um registro por (ciclo × trecho)
CREATE TABLE snapshots_rotas (
    id              SERIAL PRIMARY KEY,
    ciclo_id        INTEGER NOT NULL REFERENCES ciclos(id) ON DELETE CASCADE,
    trecho          TEXT NOT NULL,
    rodovia         TEXT,
    sentido         TEXT,
    status          TEXT NOT NULL,      -- Normal | Moderado | Intenso | Parado | Sem dados
    ocorrencia      TEXT,
    atraso_min      DOUBLE PRECISION,
    confianca_pct   DOUBLE PRECISION,
    conflito_fontes INTEGER NOT NULL DEFAULT 0,
    ts_iso          TEXT NOT NULL
);
```

> RLS habilitado: `authenticated` pode SELECT; `service_role` (backend) tem acesso total.
> Realtime habilitado: `ALTER PUBLICATION supabase_realtime ADD TABLE snapshots_rotas;`

---

## Resiliência

| Mecanismo | Detalhe |
|-----------|---------|
| Retry HTTP | 3 tentativas, backoff 0.5s, cobre 429/5xx |
| Circuit Breaker | 5 falhas abre, reset em 60s (separado por API) |
| Realtime fallback | Se WebSocket falhar 3x, ativa polling automático a cada 60s |
| Degradação | Alerta quando uma fonte falha em todos os trechos |
| Google Maps | Chave pode expirar — sistema continua com HERE + TomTom |

---

## Git Workflow & Padrão de Commits

> **Regra cardinal:** o GitHub Actions usa `actions/checkout@v4` — ele **sempre clona o repositório do GitHub**, nunca o código local. Alterações que não foram commitadas e enviadas com `git push` **jamais chegam ao CI**.

### Fluxo obrigatório após qualquer mudança

```bash
git add <arquivos-alterados>
git commit -m "tipo(escopo): mensagem"
git push origin main
```

### Conventional Commits com escopo

```
tipo(escopo): mensagem imperativa concisa

Corpo opcional — explica o PORQUÊ, não o quê.
Para bugs: descreve causa raiz e como foi confirmado o fix.
```

| Tipo | Quando usar |
|------|-------------|
| `feat` | Nova funcionalidade |
| `fix` | Correção de bug (causa raiz no corpo) |
| `chore` | Manutenção: deps, CI, configs |
| `refactor` | Melhoria interna sem mudar comportamento |
| `docs` | Documentação, README, comentários |
| `test` | Adição ou correção de testes |

| Escopo | Cobre |
|--------|-------|
| `(sources)` | here_traffic, google_maps, tomtom, correlator |
| `(storage)` | database, models, repository |
| `(frontend)` | React — hooks, components, pages |
| `(ci)` | GitHub Actions workflows |
| `(config)` | config.json, rota_logistica.json |
| `(docs)` | README, docs/, memory/ |

### Exemplos de commit sênior

```bash
# Bug — com causa raiz documentada
git commit -m "fix(storage): write descricao column that was arriving NULL in Supabase

GH Actions clones from GitHub (actions/checkout@v4). Local changes never
reach CI without push. Column existed in Python model but repo was stale.
Confirmed fix: run 2026-03-02, Supabase shows descricao populated."

# Feature — com contexto de decisão
git commit -m "feat(frontend): add 3D flip card to display rota.descricao on click

Back face renders the operational description. pauseScrollRef pauses
auto-scroll while a card is flipped to avoid disorienting the user."
```

### Checklist pós-push

- [ ] `git push origin main` enviou sem erro
- [ ] GitHub Actions → último run → status verde (ou aguardar trigger manual)
- [ ] Supabase → Table Editor → `snapshots_rotas` → dado esperado aparece (ou frontend ao vivo)

### Último deploy verificado

| Data | Commit | O que foi validado |
|------|--------|--------------------|
| 2026-03-02 | `93a1855` | Coluna `descricao` populada no Supabase; flip card exibe texto no frontend |

---

## Testes

```bash
python -m pytest tests/ -v --tb=short

# Com cobertura
python -m pytest tests/ --cov=sources --cov=report --cov-report=term-missing -q
```

---

## Licença

MIT
