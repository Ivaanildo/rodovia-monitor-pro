# Documentacao - RodoviaMonitor Pro

## Conteudo

- [Arquitetura](ARQUITETURA.md)
- [Operacao](OPERACAO.md)

## Guias de Setup (Deploy Producao)

Siga na ordem para configurar o ambiente completo:

1. [Supabase (Banco de Dados)](setup/01-SUPABASE.md) — Criar projeto, tabelas, RLS, Realtime e usuario
2. [GitHub Actions (Coleta)](setup/02-GITHUB.md) — Repositorio, secrets e workflow automatico
3. [Vercel (Frontend)](setup/03-VERCEL.md) — Deploy do dashboard React
4. [Teste Final](setup/04-TESTE-FINAL.md) — Checklist de validacao end-to-end

## Leitura recomendada

1. `README.md` para setup e execucao rapida.
2. `docs/ARQUITETURA.md` para entender o desenho tecnico e fluxo de dados.
3. `docs/OPERACAO.md` para rotina operacional, troubleshooting e monitoramento.
4. `docs/setup/` para deploy em producao (Supabase + GitHub Actions + Vercel).

## Escopo atual

- Integracoes ativas: HERE Traffic, Google Maps e TomTom.
- Persistencia: Supabase PostgreSQL + relatorios Excel.
- Backend: GitHub Actions (cron hora/hora).
- Frontend: React SPA na Vercel (Realtime via Supabase).
- Interface CLI: `main.py` (para execucao local/debug).
