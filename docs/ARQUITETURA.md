# Arquitetura — RodoviaMonitor Pro

> Versão atualizada: Mar 2026 | Sistema em produção com 20 rotas logísticas monitoradas.

---

## 1. Objetivo

Fornecer uma pipeline de monitoramento de trânsito para rotas logísticas, com coleta paralela de múltiplas fontes, correlação de sinais, persistência histórica em nuvem e painel web em tempo real.

---

## 2. Visão do sistema end-to-end

```mermaid
flowchart TD
    subgraph inputs [Entradas]
        cfg[config.json]
        rot[rota_logistica.json]
        env[Variáveis de ambiente\nAPI keys + SUPABASE_DB_URL]
    end

    subgraph coleta [Coleta — GitHub Actions / main.py]
        main[main.py\norquestrador]
        gm[google_maps.py\nRoutes API v2]
        here[here_traffic.py\nHERE Traffic v7]
        tt[tomtom_api.py\nTomTom Incidents/Flow v5]
        corr[correlator.py\ncorrelação por trecho]
        adv[advisor.py\nDataAdvisor]
        km[km_calculator.py\nestimativa KM]
    end

    subgraph banco [Supabase PostgreSQL]
        tb_ciclos[(ciclos)]
        tb_snap[(snapshots_rotas)]
    end

    subgraph rt [Supabase Realtime]
        ws[WebSocket\npostgres_changes]
    end

    subgraph frontend [Frontend — Vercel]
        auth[useAuth\nSupabase Auth]
        realtime[useSupabaseRealtime]
        painel[PainelPage]
        kpi[KpiCard]
        rcard[RotaCard]
        ticker[Ticker]
    end

    excel[Relatório Excel\nrelatorios/]

    cfg --> main
    rot --> main
    env --> main
    main --> gm
    main --> here
    main --> tt
    gm --> corr
    here --> corr
    tt --> corr
    corr --> km
    corr --> adv
    adv --> banco
    adv --> excel
    banco --> ws
    ws -->|"INSERT snapshots_rotas"| realtime
    realtime -->|polling 60s fallback| painel
    auth --> painel
    painel --> kpi
    painel --> rcard
    painel --> ticker
```

---

## 3. Fluxo de dados — sequência principal

```mermaid
sequenceDiagram
    participant GH as GitHub Actions
    participant main as main.py
    participant APIs as APIs externas
    participant corr as correlator.py
    participant adv as advisor.py
    participant supa as Supabase DB
    participant rt as Supabase Realtime
    participant fe as Frontend Vercel

    GH->>main: dispara (cron horário ou manual)
    main->>main: carregar config.json + rota_logistica.json
    main->>main: normalizar trechos (_normalizar_rota_logistica)
    par Coleta paralela
        main->>APIs: HERE Traffic — incidentes + flow
        main->>APIs: Google Routes v2 — duração + speedIntervals
        main->>APIs: TomTom — incidents + flow
    end
    APIs-->>main: resultados por trecho
    main->>corr: correlacionar_todos(trechos, gmaps, here, tomtom)
    corr-->>main: status, ocorrência, conflito por trecho
    main->>adv: enriquecer_dados (confianca_pct, fonte_escolhida)
    adv-->>main: dados enriquecidos
    main->>supa: salvar_ciclo (INSERT ciclos + snapshots_rotas)
    supa->>rt: trigger INSERT snapshots_rotas
    rt->>fe: WebSocket push (postgres_changes)
    fe->>fe: atualizar PainelPage (KpiCard, RotaCard, Ticker)
```

---

## 4. Componentes por camada_

```mermaid
flowchart LR
    subgraph orch [Orquestração]
        mainpy[main.py]
    end

    subgraph fontes [Fontes de dados]
        gm2[google_maps.py]
        here2[here_traffic.py]
        tt2[tomtom_api.py]
        circ[circuit.py\ncircuit breakers]
    end

    subgraph proc [Processamento]
        corr2[correlator.py]
        km2[km_calculator.py]
        adv2[advisor.py]
    end

    subgraph storage [Persistência]
        db[database.py\nengine PostgreSQL]
        mdl[models.py\nciclos + snapshots_rotas]
        repo[repository.py\nCRUD + purgar]
    end

    subgraph supa2 [Supabase Cloud]
        pg[(PostgreSQL)]
        rtime[Realtime]
        authsvc[Auth]
    end

    subgraph fe2 [Frontend — Vercel]
        supajscli[supabase.js\nclient]
        useAuth2[useAuth]
        useRT[useSupabaseRealtime]
        PainelPage2[PainelPage]
        comps[KpiCard / RotaCard\nTicker / RealtimeIndicator]
    end

    mainpy --> fontes
    mainpy --> proc
    proc --> storage
    storage --> pg
    pg --> rtime
    rtime --> useRT
    authsvc --> useAuth2
    supajscli --> useAuth2
    supajscli --> useRT
    useRT --> PainelPage2
    useAuth2 --> PainelPage2
    PainelPage2 --> comps
    circ -.->|protege| gm2
    circ -.->|protege| here2
    circ -.->|protege| tt2
```

---

## 5. Arquitetura de deploy

```mermaid
flowchart TD
    subgraph githubactions [GitHub Actions]
        cron["cron: a cada hora\n+ disparo manual"]
        runner[Ubuntu runner\nPython 3.11]
        mainrun[main.py]
    end

    subgraph supabasecloud [Supabase Cloud — grátis]
        pg2[(PostgreSQL\npor pooler :6543)]
        rt2[Realtime WebSocket]
        authcloud[Auth\nemail + senha]
    end

    subgraph vercel [Vercel — grátis]
        build[Build: npm run build\nVite + React 19]
        cdn[CDN global\nSPA estático]
    end

    devlocal[Dev local\nmain.py + .env]

    cron --> runner --> mainrun
    mainrun -->|SUPABASE_DB_URL| pg2
    pg2 --> rt2
    rt2 -->|WebSocket| cdn
    authcloud -->|JWT anon key| cdn
    devlocal -->|SUPABASE_DB_URL| pg2
```

> **Custo:** $0/mês. Sem servidor próprio, sem VPS, sem container gerenciado.

---

## 6. Contexto do sistema

### Entradas

| Origem | Conteúdo |
|--------|----------|
| `config.json` | APIs habilitadas, chunk_size, chunk_delay_s, agendamento |
| `rota_logistica.json` | 20 rotas (R01–R20) com hubs, waypoints, rodovia_logica, limite_gap_km |
| Variáveis de ambiente | `GOOGLE_MAPS_API_KEY`, `HERE_API_KEY`, `TOMTOM_API_KEY`, `SUPABASE_DB_URL` |

### Saídas

| Destino | Conteúdo |
|---------|----------|
| Supabase `ciclos` | Registro por execução (ts, fontes ativas, total de trechos) |
| Supabase `snapshots_rotas` | Status + ocorrência + atraso + confiança por trecho e ciclo |
| Excel em `relatorios/` | Planilha completa com todos os trechos (gerado localmente) |
| Painel Vercel | Dashboard em tempo real via Realtime/polling |

---

## 7. Responsabilidades por módulo

| Módulo | Responsabilidade |
|--------|-----------------|
| `main.py` | CLI, carga de config, scheduler, polling, orquestração de coleta e persistência |
| `sources/google_maps.py` | Routes API v2 — duração, atraso, speedReadingIntervals, classificação de status |
| `sources/here_traffic.py` | Incidentes v7 + Flow v7 — corridor/bbox, chunking, RDP downsampling |
| `sources/tomtom_api.py` | Incidents v5 + Flow v4 — bbox por trecho, ponto médio para flow |
| `sources/km_calculator.py` | Estimativa de KM por haversine + interpolação; confiança espacial |
| `sources/correlator.py` | Priorização de status/ocorrência, detecção de conflito entre fontes |
| `sources/advisor.py` | Score de confiança (freshness exponencial + peso por fonte + operacional) |
| `sources/circuit.py` | Circuit breakers para APIs externas (fail_max=5, reset_timeout=60s) |
| `storage/database.py` | Engine PostgreSQL via SUPABASE_DB_URL (pooler :6543) |
| `storage/models.py` | Definição das tabelas `ciclos` e `snapshots_rotas` (SQLAlchemy Core) |
| `storage/repository.py` | CRUD: salvar_ciclo, buscar histórico, tendências, purgar antigos |
| `report/excel_generator.py` | Planilha Excel com status, ocorrências, confiança e links |
| `frontend/src/services/supabase.js` | Cliente Supabase (VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY) |
| `frontend/src/hooks/useAuth.js` | Autenticação Supabase Auth (sessão, login, logout) |
| `frontend/src/hooks/useSupabaseRealtime.js` | WebSocket Realtime; desiste após 3 falhas → modo polling |
| `frontend/src/pages/PainelPage.jsx` | Dashboard: busca ciclo + snapshots; upsert por Realtime; KPIs |

---

## 8. Concorrência

```mermaid
flowchart TD
    main2[main.py]
    subgraph pool1 ["ThreadPool max_workers=2 (fontes)"]
        here3[HERE Traffic]
        google3[Google Maps]
        tomtom3[TomTom]
    end
    subgraph pool2 ["HERE: chunks de 7 trechos (sequencial entre chunks)"]
        subgraph pool3 ["ThreadPool max_workers=5 (por chunk)"]
            t1[Trecho 1]
            t2[Trecho 2]
            t3[Trecho ...]
        end
    end
    subgraph pool4 ["Google: ThreadPool max_workers=3"]
        g1[Trecho 1]
        g2[Trecho ...]
    end
    subgraph pool5 ["TomTom: ThreadPool max_workers=4"]
        tm1[Trecho 1]
        tm2[Trecho ...]
    end

    main2 --> pool1
    here3 --> pool2
    google3 --> pool4
    tomtom3 --> pool5
```

> Entre chunks HERE há delay de `chunk_delay_s` (default 1.5 s) para respeitar rate limits.

---

## 9. Modelo de dados consolidado (por trecho)

Campos gerados após correlação e enriquecimento pelo DataAdvisor, persistidos em `snapshots_rotas`:

| Campo | Tipo | Origem |
|-------|------|--------|
| `trecho` | texto | rota_logistica.json |
| `rodovia` | texto | rota_logistica.json |
| `sentido` | texto | rota_logistica.json |
| `status` | texto | correlator (HERE Flow > TomTom > Google) |
| `ocorrencia` | texto | correlator (múltiplas categorias separadas por `;`) |
| `atraso_min` | número | Google Routes / HERE |
| `confianca_pct` | número 0–100 | DataAdvisor |
| `conflito_fontes` | booleano | correlator (`_detectar_conflito_fontes`) |
| `ts_iso` | texto ISO 8601 | main.py (timestamp do ciclo) |
| `ciclo_id` | inteiro FK | `ciclos.id` |

Campos presentes na correlação mas **não** persistidos no banco (apenas Excel/log):

- `jam_factor`, `jam_factor_max`, `segmentos_congestionados`, `pct_congestionado`
- `km_ocorrencia`, `trecho_especifico`, `localizacao_precisa`, `confianca_localizacao`
- `duracao_normal_min`, `duracao_transito_min`
- `acao_recomendada`, `fontes_utilizadas`, `conflito_detalhe`

---

## 10. Schema do banco (Supabase)

```sql
-- Tabela de ciclos de execução
CREATE TABLE ciclos (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ DEFAULT NOW(),
    ts_iso     TEXT NOT NULL DEFAULT '',
    fontes     JSONB,
    total_trechos INTEGER
);

-- Tabela de snapshots por trecho
CREATE TABLE snapshots_rotas (
    id              BIGSERIAL PRIMARY KEY,
    ciclo_id        BIGINT REFERENCES ciclos(id) ON DELETE CASCADE,
    trecho          TEXT,
    rodovia         TEXT,
    sentido         TEXT,
    status          TEXT,
    ocorrencia      TEXT,
    atraso_min      NUMERIC,
    confianca_pct   NUMERIC,
    conflito_fontes BOOLEAN,
    ts_iso          TEXT NOT NULL DEFAULT ''
);

-- Índices
CREATE INDEX ON snapshots_rotas (ts_iso);
CREATE INDEX ON snapshots_rotas (trecho, ts_iso);
```

> `snapshots_rotas` está publicada no canal `supabase_realtime` para push de INSERTs ao frontend.

---

## 11. Resiliência e segurança

| Mecanismo | Onde | Configuração |
|-----------|------|-------------|
| Retry HTTP | urllib3.Retry | 429 e 5xx; backoff automático |
| Circuit breaker | `circuit.py` | fail_max=5, reset_timeout=60s por fonte |
| Validação JSON | antes do parse | evita falha por resposta vazia ou HTML de erro |
| Sanitização de logs | em todos os módulos | API keys nunca aparecem em stdout/stderr |
| Pool pre_ping | database.py | detecta conexões mortas com Supabase |
| Timeout GitHub Actions | monitor.yml | timeout-minutes: 20 |

---

## 12. Legado — não usar em features novas

| Componente | Motivo para não usar |
|------------|---------------------|
| `web/app.py` (FastAPI) | Substituído pelo GitHub Actions + Supabase |
| `useSse.js` / `SseIndicator` | Substituído por `useSupabaseRealtime` + `RealtimeIndicator` |
| `services/api.js` | Substituído pelo cliente Supabase direto |

---

## 13. Documentação relacionada

- [COMO_FUNCIONA.md](COMO_FUNCIONA.md) — guia completo do sistema (início recomendado)
- [ALGORITMOS.md](ALGORITMOS.md) — fluxogramas dos algoritmos internos
- [PRECISAO_E_CONFIANCA.md](PRECISAO_E_CONFIANCA.md) — gaps e % de confiança
- [ANALISE_PRECISAO.md](../ANALISE_PRECISAO.md) — análise técnica completa de precisão
- [GEOCODING_PRECISAO.md](GEOCODING_PRECISAO.md) — precisão de waypoints e geocoding
- [OPERACAO.md](OPERACAO.md) — guia operacional e troubleshooting
- [setup/](setup/) — deploy completo (Supabase, GitHub, Vercel)
