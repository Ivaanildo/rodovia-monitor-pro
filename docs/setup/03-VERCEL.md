# 03 - Deploy do Frontend na Vercel

Tempo estimado: ~10 minutos

Pre-requisitos:
- [01-SUPABASE.md](01-SUPABASE.md) concluido (voce vai precisar de SUPABASE_URL e ANON_KEY)
- [02-GITHUB.md](02-GITHUB.md) concluido (repositorio no GitHub)

---

## 1. Criar conta na Vercel

1. Acesse [vercel.com](https://vercel.com) e clique em **Sign Up**
2. Escolha **Continue with GitHub** (recomendado — conecta automaticamente)
3. Autorize o acesso ao GitHub quando solicitado

---

## 2. Importar repositorio

1. No dashboard da Vercel, clique em **Add New...** > **Project**
2. Na lista de repositorios, encontre `monitor-rodovias` e clique em **Import**
   - Se nao aparecer, clique em **Adjust GitHub App Permissions** e conceda acesso ao repositorio
3. Na tela de configuracao do projeto:

### Configure Project

| Campo | Valor |
|-------|-------|
| **Project Name** | `monitor-rodovias` (ou o nome que preferir) |
| **Framework Preset** | `Other` (deixe como detectado — Vercel deve detectar Vite) |
| **Root Directory** | Clique em **Edit** e digite: `monitor-rodovias` |
| **Build Command** | `cd frontend && npm install && npm run build` (ja configurado no vercel.json) |
| **Output Directory** | `frontend/dist` (ja configurado no vercel.json) |

**IMPORTANTE:** O **Root Directory** deve ser `monitor-rodovias` porque o repositorio usa a estrutura com subpasta (conforme descrito no guia [02-GITHUB.md](02-GITHUB.md), secao "Estrutura esperada do repositorio"). Sem isso, a Vercel nao encontra o `vercel.json` e o build falha.

---

## 3. Configurar variaveis de ambiente

Ainda na tela de configuracao (antes do deploy), expanda a secao **Environment Variables**.

Adicione 2 variaveis:

| Name | Value | Onde encontrar |
|------|-------|----------------|
| `VITE_SUPABASE_URL` | `https://xxxxx.supabase.co` | Supabase > Project Settings > API > Project URL |
| `VITE_SUPABASE_ANON_KEY` | `eyJhbGciOi...` | Supabase > Project Settings > API > anon/public key |

Para cada variavel:
1. Digite o **Name** no campo da esquerda
2. Cole o **Value** no campo da direita
3. Em **Environment**, deixe todos marcados (Production, Preview, Development)
4. Clique em **Add**

**IMPORTANTE:**
- O prefixo `VITE_` e obrigatorio — sem ele, o Vite nao expoe a variavel para o frontend
- A `ANON_KEY` e segura para o frontend (RLS protege os dados no Supabase)

---

## 4. Deploy

1. Clique em **Deploy**
2. Aguarde ~1-2 minutos para o build completar
3. Ao final, a Vercel mostrara o URL do seu site (ex: `monitor-rodovias.vercel.app`)

---

## 5. Testar em producao

### Login
1. Acesse o URL do deploy (ex: `https://monitor-rodovias.vercel.app`)
2. Faca login com o email/senha do usuario criado no Supabase (passo 5 do guia 01)
3. Voce deve ver o dashboard (pode estar vazio se ainda nao rodou uma coleta)

### Dados
- Se ja rodou o workflow do GitHub (guia 02), os dados devem aparecer automaticamente
- Se nao, dispare uma coleta manualmente no GitHub Actions e aguarde ~3 minutos

### Realtime
1. Abra o dashboard em uma aba do navegador
2. Dispare outra coleta no GitHub Actions
3. Os dados devem atualizar automaticamente no dashboard (sem dar refresh)

---

## 6. Deploys automaticos

A partir de agora, qualquer push para a branch `main` do repositorio fara deploy automatico na Vercel. Voce nao precisa fazer nada manualmente.

Para verificar deploys:
1. No dashboard da Vercel, clique no projeto
2. Aba **Deployments** mostra o historico

---

## Troubleshooting

### Build falha com "Cannot find module"
- Verifique se o **Root Directory** esta configurado como `monitor-rodovias`
- Confirme que `frontend/package.json` existe no repositorio

### Pagina em branco ou erro no console
- Abra o console do navegador (F12 > Console)
- Se aparecer erro sobre `VITE_SUPABASE_URL` undefined:
  - Verifique as Environment Variables no dashboard da Vercel
  - As variaveis devem comecar com `VITE_`
  - Apos alterar variaveis, faca um redeploy: **Deployments** > ultimo deploy > **...** > **Redeploy**

### Login nao funciona
- Confirme que o usuario foi criado no Supabase com **Auto Confirm** ativado
- Verifique no console do navegador se ha erros de CORS ou rede
- No Supabase, va em **Authentication** > **URL Configuration** e confirme que o URL da Vercel esta em **Site URL** e **Redirect URLs** (veja [01-SUPABASE.md](01-SUPABASE.md), passo 7)

### Realtime nao atualiza
- Confirme que Realtime esta habilitado na tabela `snapshots_rotas` (guia 01, passo 4)
- No console do navegador, verifique se ha conexao WebSocket ativa
- Teste com uma aba aberta e dispare uma coleta no GitHub Actions

### Dominio customizado
1. Na Vercel, va em **Settings** > **Domains**
2. Adicione seu dominio e siga as instrucoes de DNS
3. A Vercel gera SSL automaticamente

---

Proximo passo: [04-TESTE-FINAL.md](04-TESTE-FINAL.md) — Smoke test end-to-end
