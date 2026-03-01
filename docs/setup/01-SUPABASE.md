# 01 - Configurar Supabase

Tempo estimado: ~15 minutos

---

## 1. Criar conta e projeto

1. Acesse [supabase.com](https://supabase.com) e clique em **Start your project**
2. Faca login com GitHub (recomendado) ou email
3. Clique em **New Project**
4. Preencha:
   - **Name:** `rodovia-monitor` (ou o nome que preferir)
   - **Database Password:** anote essa senha em local seguro — voce vai precisar dela
   - **Region:** escolha o mais proximo (ex: `South America (Sao Paulo)`)
   - **Plan:** Free (0/month)
5. Clique em **Create new project** e aguarde ~2 minutos

---

## 2. Criar as tabelas (SQL Editor)

1. No menu lateral esquerdo, clique em **SQL Editor** (icone de terminal)
2. Clique em **New query**
3. Cole o SQL abaixo **inteiro** e clique em **Run** (botao verde ou Ctrl+Enter):

```sql
-- ============================================================
-- Schema: RodoviaMonitor Pro — Supabase PostgreSQL
-- ============================================================

-- Tabela de ciclos de coleta
CREATE TABLE IF NOT EXISTS ciclos (
    id            SERIAL PRIMARY KEY,
    ts            TEXT    NOT NULL,           -- "DD/MM/YYYY HH:MM:SS"
    ts_iso        TEXT    NOT NULL,           -- "2026-02-25T14:30:00"
    fontes        TEXT    NOT NULL DEFAULT '[]',  -- JSON list
    total_trechos INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ciclos_ts_iso ON ciclos (ts_iso);

-- Tabela de snapshots por rota por ciclo
CREATE TABLE IF NOT EXISTS snapshots_rotas (
    id              SERIAL PRIMARY KEY,
    ciclo_id        INTEGER NOT NULL REFERENCES ciclos(id) ON DELETE CASCADE,
    trecho          TEXT    NOT NULL,
    rodovia         TEXT,
    sentido         TEXT,
    status          TEXT    NOT NULL,
    ocorrencia      TEXT,
    atraso_min      DOUBLE PRECISION,
    confianca_pct   DOUBLE PRECISION,
    conflito_fontes INTEGER NOT NULL DEFAULT 0,
    ts_iso          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sr_trecho_ts ON snapshots_rotas (trecho, ts_iso DESC);
CREATE INDEX IF NOT EXISTS idx_sr_ts_iso    ON snapshots_rotas (ts_iso DESC);
```

4. Voce deve ver a mensagem **Success. No rows returned** — isso e normal para DDL

**Verificacao:** No menu lateral, clique em **Table Editor**. Voce deve ver as tabelas `ciclos` e `snapshots_rotas`.

---

## 3. Habilitar RLS (Row Level Security)

O Supabase exige RLS para acesso via API do frontend. Vamos criar politicas permissivas para leitura.

1. Ainda no **SQL Editor**, crie uma **New query** e cole:

```sql
-- Habilitar RLS
ALTER TABLE ciclos ENABLE ROW LEVEL SECURITY;
ALTER TABLE snapshots_rotas ENABLE ROW LEVEL SECURITY;

-- Politica: qualquer usuario autenticado pode LER
CREATE POLICY "Leitura publica ciclos"
    ON ciclos FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY "Leitura publica snapshots"
    ON snapshots_rotas FOR SELECT
    TO authenticated
    USING (true);

-- Politica: service_role (backend) pode tudo
CREATE POLICY "Backend full access ciclos"
    ON ciclos FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Backend full access snapshots"
    ON snapshots_rotas FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
```

2. Clique em **Run**
3. Deve aparecer **Success** novamente

---

## 4. Habilitar Realtime

Para que o dashboard atualize automaticamente quando novos dados chegarem:

1. No menu lateral, clique em **Table Editor**
2. Clique na tabela **snapshots_rotas**
3. No canto superior direito, clique no icone de engrenagem (configuracoes da tabela)
4. Ative a opcao **Enable Realtime**
5. Clique em **Save**

**Alternativa via SQL** (se preferir):

```sql
ALTER PUBLICATION supabase_realtime ADD TABLE snapshots_rotas;
```

---

## 5. Criar usuario operador

Para que os usuarios facam login no dashboard:

1. No menu lateral, clique em **Authentication**
2. Clique na aba **Users**
3. Clique em **Add user** > **Create new user**
4. Preencha:
   - **Email:** email do operador (ex: `operador@empresa.com`)
   - **Password:** senha segura
   - Marque **Auto Confirm User** (pula verificacao de email)
5. Clique em **Create user**

Repita para cada operador que precisar de acesso.

---

## 6. Obter credenciais

Voce vai precisar de 3 valores. Para encontra-los:

1. No menu lateral, clique em **Project Settings** (icone de engrenagem no rodape)
2. Clique em **API** no submenu

### SUPABASE_URL
- Campo **Project URL**
- Formato: `https://xxxxxxxxxxxxx.supabase.co`
- Usado no frontend como `VITE_SUPABASE_URL`

### SUPABASE_ANON_KEY
- Campo **Project API keys** > **anon / public**
- E uma string longa que comeca com `eyJ...`
- Usado no frontend como `VITE_SUPABASE_ANON_KEY`
- Seguro para expor no frontend (RLS protege os dados)

### SUPABASE_DB_URL (connection string PostgreSQL)
1. Va em **Project Settings** > **Database**
2. Secao **Connection string** > aba **URI**
3. Selecione **Mode: Transaction** (pooler — recomendado)
4. Copie a string e substitua `[YOUR-PASSWORD]` pela senha do banco definida no passo 1

O formato final e:
```
postgresql://postgres.[ref]:[SUA-SENHA]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
```

**IMPORTANTE:** Use a porta **6543** (pooler/transaction mode), NAO 5432.
- Porta 5432 = conexao direta (limitada a poucas conexoes)
- Porta 6543 = pooler (suporta muitas conexoes, ideal para serverless/GH Actions)

---

## Troubleshooting

### "relation does not exist"
- Verifique se executou o SQL do passo 2 sem erros
- Confirme no Table Editor que as tabelas existem

### "permission denied for table"
- Verifique se as politicas RLS foram criadas (passo 3)
- O backend (GH Actions) usa a connection string direta, que tem role `postgres` — nao e afetado por RLS

### Conexao recusada / timeout
- Confirme que esta usando porta **6543** (pooler), nao 5432
- Verifique se a senha no URL esta correta (sem `[YOUR-PASSWORD]` literal)
- No Supabase: **Project Settings** > **Database** > confirme que **Connection pooling** esta ativo

### Realtime nao funciona
- Confirme que habilitou Realtime na tabela `snapshots_rotas` (passo 4)
- No frontend, verifique que `VITE_SUPABASE_URL` e `VITE_SUPABASE_ANON_KEY` estao corretos
- Abra o console do navegador (F12) e procure por erros de WebSocket

---

Proximo passo: [02-GITHUB.md](02-GITHUB.md) — Configurar repositorio e GitHub Actions
