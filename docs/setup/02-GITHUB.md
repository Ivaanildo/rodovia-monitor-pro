# 02 - Configurar GitHub e Actions

Tempo estimado: ~10 minutos

Pre-requisito: [01-SUPABASE.md](01-SUPABASE.md) concluido (voce vai precisar da SUPABASE_DB_URL)

---

## 1. Criar repositorio

1. Acesse [github.com](https://github.com) e faca login
2. Clique em **+** (canto superior direito) > **New repository**
3. Preencha:
   - **Repository name:** `monitor-rodovias` (ou o nome que preferir)
   - **Visibility:** **Public** (obrigatorio para GitHub Actions gratuito ilimitado)
   - NAO marque "Add README" (nos ja temos um)
4. Clique em **Create repository**

---

## 2. Estrutura esperada do repositorio

Antes de fazer push, confirme que a estrutura local esta correta. O repositorio deve ser inicializado na **pasta raiz** (a que contem as subpastas), nao dentro de `monitor-rodovias`:

```
raiz/                          <- git init aqui
├── .github/
│   └── workflows/
│       └── monitor.yml        <- workflow de coleta automatica
└── monitor-rodovias/          <- codigo Python + frontend React
    ├── config.json            <- "rotas_referencia_arquivo": "./rota_logistica.json"
    ├── main.py
    ├── requirements.txt
    ├── rota_logistica.json    <- OBRIGATORIO: dentro de monitor-rodovias/
    ├── frontend/
    └── ...
```

**IMPORTANTE:** O arquivo `rota_logistica.json` deve estar **dentro de `monitor-rodovias/`** (na mesma pasta do `config.json`), pois o `config.json` o referencia como `"./rota_logistica.json"`. O workflow executa com `working-directory: monitor-rodovias`, por isso o caminho relativo `./` aponta para essa pasta. Sem o arquivo, a coleta nao encontra as rotas e falha.

---

## 3. Fazer push do codigo

No terminal, na **pasta raiz** do projeto (a que contem `monitor-rodovias` e `.github`):

```bash
git init
git add .
git commit -m "Initial commit: Monitor de Rodovias v2-MVP"

git remote add origin https://github.com/SEU-USUARIO/monitor-rodovias.git
git branch -M main
git push -u origin main
```

Substitua `SEU-USUARIO` pelo seu username do GitHub.

**Dica:** Se voce ja tem o repositorio criado e com codigo, pule para o passo 4.

---

## 4. Configurar Secrets (variaveis de ambiente)

Os secrets sao variaveis de ambiente seguras que o GitHub Actions usa durante a execucao.

1. No repositorio, clique em **Settings** (aba no topo)
2. No menu lateral esquerdo, clique em **Secrets and variables** > **Actions**
3. Clique em **New repository secret**
4. Adicione os 4 secrets abaixo, um por vez:

| Name | Valor | Onde encontrar |
|------|-------|----------------|
| `GOOGLE_MAPS_API_KEY` | Sua chave da Google Cloud | [Google Cloud Console](https://console.cloud.google.com) > APIs & Services > Credentials |
| `HERE_API_KEY` | Sua chave HERE | [developer.here.com](https://developer.here.com) > Projects > API Keys |
| `TOMTOM_API_KEY` | Sua chave TomTom | [developer.tomtom.com](https://developer.tomtom.com) > Dashboard > Keys |
| `SUPABASE_DB_URL` | Connection string PostgreSQL | Guia [01-SUPABASE.md](01-SUPABASE.md), passo 6 |

Para cada secret:
1. **Name:** cole o nome exatamente como na tabela (maiusculas, underscores)
2. **Secret:** cole o valor (chave da API ou URL do banco)
3. Clique em **Add secret**

**IMPORTANTE:** Nao coloque aspas ao redor dos valores. Cole o valor puro.

### Como obter as chaves de API

Se ainda nao tem as chaves das APIs de transito, siga as instrucoes abaixo:

**Google Maps (Routes API)**
1. Acesse o [Google Cloud Console](https://console.cloud.google.com) e crie um projeto
2. Va em **APIs & Services** > **Enable APIs and Services**
3. Busque e habilite a **Routes API** (anteriormente "Directions API")
4. Va em **APIs & Services** > **Credentials** > **Create Credentials** > **API key**
5. **Atencao:** e necessario ter uma conta de faturamento ativa. O Google oferece $200/mes de creditos gratuitos — para o volume deste projeto, o custo real tende a ser $0.

**HERE Traffic**
1. Crie conta gratuita em [developer.here.com](https://developer.here.com)
2. Va em **Projects** > selecione ou crie um projeto
3. Na aba **API Keys**, clique em **Create API Key**
4. Copie a chave gerada

**TomTom**
1. Crie conta gratuita em [developer.tomtom.com](https://developer.tomtom.com)
2. Faca login e acesse o **Dashboard**
3. Va em **My Apps** > **Add new App**
4. Na lista de APIs, habilite **Traffic API**
5. Copie a **API Key** exibida

---

## 5. Verificar o workflow

O workflow ja esta configurado em `.github/workflows/monitor.yml`. Ele:

- Roda automaticamente a cada hora (cron `0 * * * *`)
- Pode ser disparado manualmente (workflow_dispatch)
- Instala Python 3.11 e dependencias
- Executa `python main.py --config config.json`
- Faz upload do relatorio Excel como artefato (retencao: 7 dias)

---

## 6. Testar manualmente

1. No repositorio, clique na aba **Actions**
2. No menu lateral esquerdo, clique em **Monitor de Rodovias**
3. Clique no botao **Run workflow** (lado direito)
4. Selecione branch `main` e clique em **Run workflow**
5. Aguarde ~2-3 minutos para o workflow completar

---

## 7. Verificar resultado

### Logs
1. Clique no run que acabou de executar (lista no centro da pagina)
2. Clique no job **coletar**
3. Expanda o step **Executar coleta** para ver os logs

O que esperar nos logs:
```
[INFO] Iniciando coleta...
[INFO] HERE Traffic: 28/28 trechos consultados
[INFO] Google Maps: 28/28 trechos consultados
[INFO] TomTom: 28/28 trechos consultados
[INFO] Correlacao concluida: 28 resultados
[INFO] Relatorio salvo: relatorios/rodoviamonitor_pro_20260228_1400.xlsx
[INFO] Banco atualizado: ciclo #1, 28 snapshots
```

### Artefato Excel
1. Na pagina do run, role para baixo ate a secao **Artifacts**
2. Clique em `relatorio-X` para baixar o arquivo `.zip`
3. Extraia e abra o `.xlsx` no Excel

### Dados no Supabase
1. Va ao Supabase > **Table Editor**
2. Clique em `ciclos` — deve ter 1 registro
3. Clique em `snapshots_rotas` — deve ter ~28 registros (1 por trecho)

---

## Troubleshooting

### Workflow nao aparece na aba Actions
- Confirme que o arquivo `.github/workflows/monitor.yml` existe no repositorio
- Faca push novamente se necessario

### Erro "secret not found" ou API retorna 401/403
- Verifique se os nomes dos secrets estao corretos (maiusculas exatas)
- Confirme que os valores nao tem espacos extras ou aspas
- Teste a chave manualmente: `curl "https://api.tomtom.com/...?key=SUA_KEY"`

### Erro de conexao com Supabase
- Verifique se `SUPABASE_DB_URL` usa porta **6543** (pooler)
- Confirme que a senha no URL esta correta
- Teste localmente: `SUPABASE_DB_URL="..." python -c "from storage.database import get_engine; get_engine().connect()"`

### Erro "arquivo de rotas nao encontrado" ou dados vazios
- Confirme que `rota_logistica.json` esta **dentro de `monitor-rodovias/`** (na mesma pasta do `config.json`)
- No `config.json`, o campo `rotas_referencia_arquivo` deve ser `"./rota_logistica.json"`
- Se o arquivo estiver em outra localizacao, ajuste o caminho no `config.json` de acordo

### Timeout (>20 minutos)
- O workflow tem `timeout-minutes: 20` como protecao
- Se demorar muito, verifique se alguma API esta fora do ar
- Consulte os logs para identificar qual fonte esta lenta

---

Proximo passo: [03-VERCEL.md](03-VERCEL.md) — Deploy do frontend
