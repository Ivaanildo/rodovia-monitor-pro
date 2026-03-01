# 04 - Teste Final (Smoke Test End-to-End)

Use este checklist para validar que tudo esta funcionando apos completar os guias 01, 02 e 03.

---

## Checklist

### Supabase (Banco de Dados)

- [ ] **Tabelas existem:** Va em Table Editor e confirme que `ciclos` e `snapshots_rotas` aparecem
- [ ] **RLS ativo:** Clique em cada tabela > icone de engrenagem > confirme que RLS esta habilitado (cadeado fechado)
- [ ] **Realtime ativo:** Na tabela `snapshots_rotas`, confirme que Realtime esta habilitado
- [ ] **Usuario criado:** Va em Authentication > Users e confirme que o operador aparece com status "Confirmed"

### GitHub Actions (Coleta de Dados)

- [ ] **Estrutura do repositorio:** confirme que `rota_logistica.json` esta dentro de `monitor-rodovias/` e que `config.json` tem `"rotas_referencia_arquivo": "./rota_logistica.json"` — sem isso a coleta falha
- [ ] **Secrets configurados:** Settings > Secrets > confirme 4 secrets: `GOOGLE_MAPS_API_KEY`, `HERE_API_KEY`, `TOMTOM_API_KEY`, `SUPABASE_DB_URL`
- [ ] **Workflow existe:** Aba Actions > confirme que "Monitor de Rodovias" aparece no menu lateral
- [ ] **Primeira execucao:** Dispare manualmente (Run workflow) e aguarde completar com sucesso (check verde)
- [ ] **Logs OK:** No step "Executar coleta", confirme que aparece "Relatorio salvo" e "Banco atualizado"
- [ ] **Artefato Excel:** Na pagina do run, secao Artifacts, baixe e abra o relatorio

### Dados no Banco

Apos a primeira execucao com sucesso:

- [ ] **Ciclo criado:** Supabase > Table Editor > `ciclos` deve ter pelo menos 1 registro
- [ ] **Snapshots criados:** `snapshots_rotas` deve ter registros proporcional ao numero de rotas em `rota_logistica.json` (1 por trecho monitorado por execucao)
- [ ] **Campos preenchidos:** Verifique que `status`, `trecho`, `rodovia` tem valores (nao sao todos null)

### Frontend (Dashboard)

- [ ] **Deploy OK:** Acesse o URL da Vercel (ex: `monitor-rodovias.vercel.app`) — deve carregar a pagina de login
- [ ] **Login funciona:** Faca login com email/senha do usuario do Supabase
- [ ] **Dashboard carrega:** Apos login, o dashboard deve exibir os dados da ultima coleta
- [ ] **Tabela com dados:** A aba Tabela deve mostrar os trechos com status, ocorrencia, atraso

### Realtime (Atualizacao Automatica)

- [ ] **Teste de atualizacao:**
  1. Mantenha o dashboard aberto no navegador
  2. Va ao GitHub Actions e dispare outra execucao manual
  3. Aguarde ~3 minutos para a coleta completar
  4. O dashboard deve atualizar automaticamente (sem dar F5)

### Excel (Relatorio)

- [ ] **Download do artefato:** GitHub Actions > run > Artifacts > baixar ZIP
- [ ] **Arquivo valido:** Extraia e abra o `.xlsx` — deve ter abas com dados formatados
- [ ] **Dados coerentes:** Status, rodovias e atrasos devem refletir condicoes reais das rodovias

---

## Resumo da Arquitetura Funcionando

Se todos os itens acima estao marcados, voce tem:

```
GitHub Actions (cron hora/hora)
    |
    v
Python coleta dados de 3 APIs
(Google Maps + HERE Traffic + TomTom)
    |
    v
Salva no Supabase PostgreSQL
    |
    +---> Realtime WebSocket ---> Dashboard React (Vercel)
    |
    +---> Relatorio Excel (artefato GitHub)
```

**Custo mensal: $0** (todos os servicos dentro dos free tiers)

---

## Se algo deu errado

| Problema | Guia de referencia |
|----------|--------------------|
| Tabelas nao existem ou erro de SQL | [01-SUPABASE.md](01-SUPABASE.md) passo 2 |
| Erro de permissao no banco | [01-SUPABASE.md](01-SUPABASE.md) passo 3 |
| Dados vazios ou "arquivo de rotas nao encontrado" | [02-GITHUB.md](02-GITHUB.md) — verificar `rota_logistica.json` dentro de `monitor-rodovias/` |
| Workflow falha no GitHub | [02-GITHUB.md](02-GITHUB.md) troubleshooting |
| Build falha na Vercel | [03-VERCEL.md](03-VERCEL.md) troubleshooting |
| Login nao funciona | [01-SUPABASE.md](01-SUPABASE.md) passo 7 + [03-VERCEL.md](03-VERCEL.md) troubleshooting |
| Realtime nao atualiza | [01-SUPABASE.md](01-SUPABASE.md) passo 4 |

---

## Proximos passos

Com a infraestrutura funcionando, voce pode:

1. **Monitorar:** O cron roda automaticamente a cada hora — nao precisa fazer nada
2. **Verificar:** Acesse o dashboard periodicamente ou configure alertas
3. **Baixar relatorios:** Os Excel ficam disponiveis por 7 dias nos artefatos do GitHub
4. **Adicionar operadores:** Crie mais usuarios no Supabase > Authentication > Users
