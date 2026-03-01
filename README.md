# RodoviaMonitor Pro

Bot que monitora o transito de rodovias brasileiras em tempo real, com dashboard web ao vivo e relatorios Excel para operacoes logisticas.

**Problema:** equipes de logistica precisam saber, a cada ciclo de monitoramento, quais rodovias estao com incidentes, congestionamentos ou restricoes — sem abrir manualmente dezenas de fontes de transito.

**Solucao:** o RodoviaMonitor consulta 3 APIs de trafego em paralelo (HERE, Google, TomTom), cruza os dados por trecho, exibe em um dashboard ao vivo e entrega planilhas prontas para decisao operacional.

---

## Como funciona

```text
CLI (main.py)
  -> Carrega config JSON + .env
  -> Coleta paralela (ThreadPoolExecutor)
      -> HERE Traffic (incidentes + fluxo, corredor segmentado)
      -> Google Maps (duracao com transito, speedReadingIntervals)
      -> TomTom (incidentes v5 + fluxo v4)
  -> Correlaciona dados por trecho (conflict detection, jam per segment)
  -> Calcula score de confianca (DataAdvisor)
  -> Gera relatorio Excel + alimenta dashboard web
```

- **20 rotas** configuraveis por arquivo JSON (com via_waypoints)
- **~68s por ciclo** (cache quente), ~98s primeiro ciclo
- **3 fontes de dados** cruzadas com deteccao de conflito entre fontes
- **Dashboard web** com SSE ao vivo (Painel TV)
- **Autenticacao** JWT com login/senha (fail-closed em producao)
- **Deploy Vercel** com configuracao pronta (serverless)
- **Retry automatico** em erros HTTP transientes (429/5xx)
- **Circuit breaker** por fonte para evitar cascata de falhas
- **Custo: $0/mes** (APIs dentro dos free tiers)

## Requisitos

- Python 3.10+
- Chaves de API:
  - `GOOGLE_MAPS_API_KEY` — [Google Cloud Console](https://console.cloud.google.com/)
  - `HERE_API_KEY` — [HERE Developer](https://developer.here.com/)
  - `TOMTOM_API_KEY` — [TomTom Developer](https://developer.tomtom.com/)

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
TOMTOM_API_KEY=sua_chave_tomtom
```

## Uso

```bash
# Execucao unica
python main.py --config config.json

# Polling a cada 30 minutos
python main.py --config config.json --interval 30

# Agendamento por horarios fixos (06h, 12h, 18h)
python main.py --agendar

# Logs em JSON estruturado
python main.py --config config.json --log-json
```

O relatorio Excel e salvo em `relatorios/`.

## Dashboard Web

O dashboard e iniciado automaticamente com o servidor na porta 8080.

- **Painel TV** — Cards de rota com auto-scroll, KPIs, ticker de alertas
- **SSE** — Atualizacoes push em tempo real a cada ciclo de coleta
- **API REST** — 16 endpoints (dados, GeoJSON, historico, tendencias, CSV export)

### Frontend React + Vite (novo)

O frontend foi migrado de HTML estatico para React + Vite em `frontend/`:

```bash
# Desenvolvimento (requer o backend rodando na porta 8080)
cd monitor-rodovias/frontend
npm install
npm run dev        # http://localhost:5173

# Build de producao
npm run build      # gera frontend/dist/
```

**Arquitetura do frontend:**

```
frontend/src/
  services/
    api.js          # apiFetch centralizado (intercepta 401 → /login)
  hooks/
    useAuth.js      # GET /api/auth/me + logout
    useSse.js       # EventSource com backoff exponencial e cleanup
  components/
    KpiCard         # Metrica com barra proporcional
    RotaCard        # Card de rota com flash animation ao atualizar
    Ticker          # Faixa de alertas criticos com scroll infinito
    SseIndicator    # Indicador connecting/connected/error
    Clock           # Relogio atualizado a cada 10s
  pages/
    LoginPage       # Glassmorphism + shake animation + loading state
    PainelPage      # Sidebar KPIs + grid de rotas + auto-scroll
  App.jsx           # BrowserRouter + ProtectedRoute (redireciona para /login)
```

O proxy do Vite (`/api` e `/events` → `http://localhost:8080`) permite desenvolvimento local sem CORS. O `CORSMiddleware` do FastAPI libera `http://localhost:5173` com `credentials: true`.

### Autenticacao

O dashboard e protegido por login/senha. Para configurar:

```bash
# 1. Gerar chave secreta
python auth_cli.py secret

# 2. Gerar hash da senha
python auth_cli.py hash "sua_senha_segura"

# 3. Configurar variaveis de ambiente
export AUTH_SECRET=cole_o_secret_gerado
export AUTH_USERS='[{"username":"admin","password_hash":"$2b$12$cole_hash_aqui","role":"admin"}]'
export AUTH_COOKIE_SECURE=false  # false para HTTP local, true para HTTPS/producao
```

Comandos do CLI de autenticacao:

```bash
python auth_cli.py hash "senha"          # Gera hash bcrypt
python auth_cli.py secret                # Gera AUTH_SECRET aleatorio
python auth_cli.py usuarios              # Gera exemplo de AUTH_USERS
python auth_cli.py criar-usuario admin   # Cria usuario interativamente
```

**Seguranca implementada:**

| Protecao | Detalhe |
|----------|---------|
| Brute force | Rate limit 5 tentativas / 5 min por IP |
| XSS | Cookie HttpOnly (JS nao acessa o token) |
| CSRF | SameSite=Lax no cookie |
| Clickjacking | X-Frame-Options: DENY |
| MITM | HSTS (Strict-Transport-Security) |
| Session hijacking | Cookie Secure + expiracao configuravel |
| Enumeracao | Mensagem generica no erro de login |
| Misconfig em producao | Sem `AUTH_SECRET`/`AUTH_USERS`, endpoints privados retornam 503 |
| Header spoofing | `X-Forwarded-For` so entra no rate limit com `AUTH_TRUST_X_FORWARDED_FOR=true` |

> Sem as variaveis `AUTH_SECRET` e `AUTH_USERS`, o bypass automatico so e permitido fora de producao.
> Em producao, a aplicacao passa a falhar de forma segura (503 nos endpoints privados).

## Deploy no Vercel

O projeto inclui configuracao pronta para deploy serverless no Vercel.

```bash
# 1. Instale o Vercel CLI
npm i -g vercel

# 2. Build do frontend antes do deploy
cd monitor-rodovias/frontend
npm run build

# 3. Deploy a partir da raiz do projeto
cd ..
vercel deploy
```

Configure as variaveis de ambiente no painel do Vercel (Settings > Environment Variables):

| Variavel | Obrigatoria | Descricao |
|----------|-------------|-----------|
| `AUTH_SECRET` | Sim | Chave JWT (64 hex chars, gerada com `auth_cli.py secret`) |
| `AUTH_USERS` | Sim | JSON array de usuarios (gerado com `auth_cli.py`) |
| `HERE_API_KEY` | Sim | Chave API HERE Traffic |
| `GOOGLE_MAPS_API_KEY` | Sim | Chave API Google Maps |
| `TOMTOM_API_KEY` | Sim | Chave API TomTom |

**Configuracao Vercel (`vercel.json`):**
- Build Python serverless: `api/index.py` via `@vercel/python`
- Build frontend React: `frontend/package.json` via `@vercel/static-build` (output em `dist/`)
- Rotas: `/api/*` e `/events` → Python serverless; demais rotas → `frontend/dist/index.html` (SPA fallback)

**Limitacoes no Vercel:**
- SSE nao funciona (timeout serverless) — frontend faz polling automatico
- SQLite nao persiste (filesystem read-only) — historico desabilitado
- Cold start de 2-5s na primeira request

## Estrutura do projeto

```
├── main.py                          # Orquestrador CLI
├── config.json                      # Configuracao principal
├── rotas_logistica.json             # Base de rotas e segmentos
├── auth_cli.py                      # CLI para gerenciar autenticacao
├── vercel.json                      # Config deploy Vercel (Python + Vite)
├── requirements.txt
├── api/
│   ├── index.py                     # Entrypoint serverless (Mangum)
│   └── requirements.txt             # Deps para Vercel
├── frontend/                        # Frontend React + Vite (NOVO)
│   ├── index.html                   # Entry HTML (Plus Jakarta Sans)
│   ├── vite.config.js               # Proxy /api e /events + alias @/
│   ├── package.json
│   └── src/
│       ├── main.jsx                 # Monta <App /> no #root
│       ├── App.jsx                  # BrowserRouter + ProtectedRoute
│       ├── index.css                # Reset global
│       ├── services/
│       │   └── api.js               # apiFetch centralizado
│       ├── hooks/
│       │   ├── useAuth.js           # Autenticacao (me + logout)
│       │   └── useSse.js            # SSE com backoff exponencial
│       ├── components/
│       │   ├── KpiCard.jsx/.module.css
│       │   ├── RotaCard.jsx/.module.css
│       │   ├── Ticker.jsx/.module.css
│       │   ├── SseIndicator.jsx/.module.css
│       │   └── Clock.jsx/.module.css
│       └── pages/
│           ├── LoginPage.jsx/.module.css
│           └── PainelPage.jsx/.module.css
├── sources/
│   ├── google_maps.py               # Client Google Maps Routes API v2
│   ├── here_traffic.py              # Client HERE Traffic v7 + Routing v8
│   ├── tomtom_api.py                # Client TomTom Incidents v5 + Flow v4
│   ├── correlator.py                # Motor de correlacao multi-fonte
│   ├── advisor.py                   # Score de confianca (DataAdvisor)
│   ├── km_calculator.py             # Estimativa de KM por trecho
│   └── circuit.py                   # Circuit breakers por API
├── report/
│   └── excel_generator.py           # Gerador de planilhas Excel
├── web/
│   ├── app.py                       # FastAPI (16 endpoints + CORS + auth)
│   ├── auth.py                      # JWT + bcrypt + rate limiter
│   ├── state.py                     # DataStore (thread-safe, SSE bridge)
│   ├── rotas_geojson.py             # GeoJSON loader
│   └── static/
│       ├── login.html               # Pagina de login legada (HTML puro)
│       └── painel.html              # Painel TV legado (HTML puro)
├── storage/
│   ├── database.py                  # SQLAlchemy engine (WAL mode)
│   ├── models.py                    # Tabelas: ciclos + snapshots_rotas
│   └── repository.py               # RotaRepository (CRUD + tendencias)
└── tests/                           # Testes unitarios
```

## Testes

```bash
python -m pytest tests/ -v --tb=short

# Com cobertura
python -m pytest tests/ --cov=sources --cov=report --cov-report=term-missing -q
```

## Checkup de Seguranca

Checklist executado em **February 28, 2026**:

- **Semgrep (SAST)**: scan concluido com `0 findings` apos correcoes no frontend e endurecimento da autenticacao.
- **Snyk test**: `monitor-rodovias/requirements.txt` e `monitor-rodovias/api/requirements.txt` testados sem vulnerabilidades conhecidas.
- **Snyk monitor**: snapshots publicados para os projetos `monitor-rodovias` e `api`.
- **MCP scan (ecossistema local)**: configuracoes locais verificadas sem servidores MCP ativos nas configs inspecionadas.

Correcoes aplicadas durante a auditoria:

- Remocao de sinks `innerHTML` marcados pelo Semgrep no frontend.
- Middleware de auth ajustado para comportamento fail-closed em producao quando `AUTH_SECRET`/`AUTH_USERS` estiverem ausentes.
- Rate limit de login endurecido para ignorar `X-Forwarded-For` por padrao.
- `AUTH_TOKEN_EXPIRY_HOURS` passa a definir tambem o `max_age` real do cookie.
- Banco SQLite local (`dados/*.db`) adicionado ao `.gitignore`.

Comandos uteis para repetir os checks:

```bash
# SAST
semgrep --config auto .

# Dependencias
npx -y snyk test --all-projects --org=86316336-d970-419f-87bf-38da5fdea0a7

# Monitoramento continuo
npx -y snyk monitor --all-projects --org=86316336-d970-419f-87bf-38da5fdea0a7

# Configs MCP locais
npx -y @contextware/mcp-scan configs --include-local
```

## Gotchas (Windows)

**Encoding cp1252 em print():** O terminal do Windows usa `cp1252` por padrao, que nao suporta caracteres Unicode como box-drawing, setas ou travessao. Use apenas ASCII nos outputs de print().

## Resiliencia

| Mecanismo | Detalhe |
|-----------|---------|
| Retry HTTP | 3 tentativas, backoff 0.5s, cobre 429/5xx |
| Circuit Breaker | 5 falhas abre, reset em 60s (separado por API) |
| Validacao JSON | Verifica Content-Type antes de parsear |
| Sanitizacao | API keys nunca aparecem em logs |
| Degradacao | Alerta quando uma fonte falha em todos os trechos |
| Auth JWT | Token HttpOnly + rate limit + security headers |

## Licenca

MIT
