# Documentação — RodoviaMonitor Pro

> Sistema em produção em **https://monitor-rodovias.vercel.app** — 20 rotas logísticas monitoradas.

---

## Início recomendado

Novo no projeto? Comece aqui:

→ **[Como funciona o sistema](COMO_FUNCIONA.md)** — visão geral do sistema, pipeline em 5 passos, onde está o quê e leitura recomendada.

---

## Documentação técnica

| Documento | Conteúdo |
|-----------|---------|
| [ARQUITETURA.md](ARQUITETURA.md) | Diagramas Mermaid do sistema completo, componentes por camada, deploy, schema do banco, concorrência e resiliência |
| [ALGORITMOS.md](ALGORITMOS.md) | Fluxogramas dos algoritmos: normalização de rotas, coleta paralela, corridor vs bbox, RDP, correlação, confiança, KM |
| [PRECISAO_E_CONFIANCA.md](PRECISAO_E_CONFIANCA.md) | Resumo executivo dos gaps de precisão, fórmula de `confianca_pct`, confiança textual, conflito de fontes |
| [OPERACAO.md](OPERACAO.md) | Guia operacional: pré-requisitos, modos de execução, sinais de degradação, troubleshooting |
| [GEOCODING_PRECISAO.md](GEOCODING_PRECISAO.md) | Precisão de waypoints, corridor/bbox, geocoding e snap-to-road |

## Análise aprofundada

| Documento | Conteúdo |
|-----------|---------|
| [../ANALISE_PRECISAO.md](../ANALISE_PRECISAO.md) | Análise técnica completa de precisão — histórico das 10 melhorias implementadas, perda por estágio, rotas por nível de precisão |

---

## Guias de Setup (Deploy Produção)

Siga na ordem para configurar o ambiente completo:

1. [01-SUPABASE.md](setup/01-SUPABASE.md) — Criar projeto, tabelas, RLS, Realtime e usuário
2. [02-GITHUB.md](setup/02-GITHUB.md) — Repositório, secrets e workflow automático (cron horário)
3. [03-VERCEL.md](setup/03-VERCEL.md) — Deploy do dashboard React
4. [04-TESTE-FINAL.md](setup/04-TESTE-FINAL.md) — Checklist de validação end-to-end

---

## Leitura recomendada por perfil

### Para apresentar o projeto
1. [COMO_FUNCIONA.md](COMO_FUNCIONA.md) — visão geral e pipeline
2. [ARQUITETURA.md](ARQUITETURA.md) — diagramas e componentes
3. [PRECISAO_E_CONFIANCA.md](PRECISAO_E_CONFIANCA.md) — gaps e confiança

### Para entender os algoritmos
1. [ALGORITMOS.md](ALGORITMOS.md) — fluxogramas detalhados
2. [ANALISE_PRECISAO.md](../ANALISE_PRECISAO.md) — análise técnica e histórico

### Para operar o sistema
1. [OPERACAO.md](OPERACAO.md) — comandos e troubleshooting
2. [COMO_FUNCIONA.md](COMO_FUNCIONA.md#8-onde-está-o-quê) — mapa de arquivos

### Para fazer deploy do zero
1. [setup/01-SUPABASE.md](setup/01-SUPABASE.md) a [setup/04-TESTE-FINAL.md](setup/04-TESTE-FINAL.md) — em ordem

---

## Escopo atual (Mar 2026)

- **Rotas monitoradas:** 20 rotas R01–R20 em `rota_logistica.json`
- **Fontes ativas:** HERE Traffic v7, Google Routes v2, TomTom v5/v4
- **Backend:** GitHub Actions (cron horário) + `main.py`
- **Banco:** Supabase PostgreSQL — tabelas `ciclos` e `snapshots_rotas`
- **Realtime:** Supabase Realtime (WebSocket) com fallback polling 60 s
- **Frontend:** React 19 + Vite 7 na Vercel com Supabase Auth
- **Legado (não usar em features novas):** `web/app.py` (FastAPI), `useSse.js`, `api.js`
