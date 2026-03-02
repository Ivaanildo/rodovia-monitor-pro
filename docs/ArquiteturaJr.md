# Arquitetura para Iniciantes

> Se voce acabou de chegar no projeto, comece por aqui.
> Este documento explica como as pecas se conectam usando linguagem simples.

---

## 1. O que o sistema faz (em 1 paragrafo)

O **RodoviaMonitor** verifica o transito em 20 rotas logisticas brasileiras a cada hora.
Ele consulta 3 servicos de mapas (Google, HERE e TomTom), cruza as informacoes,
calcula um nivel de confianca e salva tudo num banco de dados na nuvem.
Um painel web mostra os resultados em tempo real para o time de logistica.

---

## 2. As 3 pecas do sistema

O sistema e composto por **3 servicos independentes** que nunca se falam diretamente entre si.
Eles se comunicam apenas atraves do banco de dados (Supabase).

Pense assim:

| Peca | Analogia | O que faz |
|------|----------|-----------|
| **GitHub Actions** | Cozinheiro | Roda o script Python a cada hora. Consulta as APIs, processa os dados e salva no banco. |
| **Supabase** | Geladeira | Guarda os dados (PostgreSQL) e avisa o frontend quando tem dado novo (Realtime). |
| **Vercel** | Garcom | Serve o painel web (React) para o usuario. Le os dados direto do Supabase. |

Pontos importantes:

- O **cozinheiro** (GitHub Actions) coloca comida na **geladeira** (Supabase).
- O **garcom** (Vercel) pega comida da **geladeira** (Supabase) e leva para o cliente.
- O cozinheiro e o garcom **nunca se encontram**. Eles so interagem com a geladeira.

---

## 3. O que acontece a cada hora (passo a passo)

```
1. GitHub Actions acorda (cron a cada hora)
       |
2. Instala Python 3.11 e dependencias (pip install)
       |
3. Roda: python main.py --config config.json --modo-mvp
       |
4. main.py carrega config.json + rota_logistica.json (20 rotas)
       |
5. Para cada rota, consulta 3 APIs em paralelo:
       |--- Google Routes API  (duracao, atraso)
       |--- HERE Traffic API   (incidentes, fluxo)
       |--- TomTom API         (incidentes, fluxo)
       |
6. correlator.py cruza os dados das 3 fontes
       |--- Decide o status: Normal / Moderado / Intenso / Parado
       |--- Identifica ocorrencias: Obras, Colisao, Interdicao...
       |--- Detecta conflitos entre fontes
       |
7. advisor.py calcula confianca (0-100%)
       |
8. Salva no Supabase PostgreSQL (tabelas: ciclos + snapshots_rotas)
       |
9. Supabase Realtime detecta o INSERT e manda push via WebSocket
       |
10. Frontend React (Vercel) recebe o push e atualiza o painel
```

Tempo total: ~70 segundos por ciclo.

---

## 4. Diagrama: caminho dos dados

```
+-------------------+      +-------------------+      +-------------------+
|                   |      |                   |      |                   |
|  GitHub Actions   |      |     Supabase      |      |      Vercel       |
|  (Python runner)  |      |  (banco + realtime)|     |  (frontend React) |
|                   |      |                   |      |                   |
|  main.py          |      |  PostgreSQL       |      |  painel web       |
|    |              |      |    |              |      |    |              |
|    v              |      |    v              |      |    |              |
|  google_maps.py --+----->|  ciclos           |      |    |              |
|  here_traffic.py -+----->|  snapshots_rotas --+----->|  useRealtime()   |
|  tomtom_api.py ---+----->|    |              |      |    |              |
|    |              |      |    v              |      |    v              |
|  correlator.py    |      |  Realtime engine  +----->|  PainelPage.jsx   |
|  advisor.py       |      |  (WebSocket push) |      |  (tabela + KPIs)  |
|                   |      |                   |      |                   |
+-------------------+      +-------------------+      +-------------------+

     ESCRITA -->                STORAGE               <-- LEITURA

        Eles NUNCA se conectam diretamente!
        Toda comunicacao passa pelo Supabase.
```

---

## 5. "O Python se conecta ao Vercel?"

**NAO.** Eles nunca se falam.

- O Python (GitHub Actions) escreve dados no **Supabase**.
- O React (Vercel) le dados do **Supabase**.
- O Vercel serve apenas arquivos estaticos (HTML, CSS, JS). Nao roda Python.
- O GitHub Actions roda Python mas nao sabe que o Vercel existe.

Se o Vercel cair, a coleta de dados continua normalmente.
Se o GitHub Actions parar, o painel continua funcionando (mostra os dados antigos).

---

## 6. Onde ficam as senhas (API keys)

As senhas ficam em **2 lugares diferentes**, dependendo de quem precisa delas:

### GitHub Actions (para o Python)

Configurado em: **Settings > Secrets and variables > Actions**

```
Secrets necessarios:
  GOOGLE_MAPS_API_KEY   --> chave da Google Routes API
  HERE_API_KEY          --> chave da HERE Traffic API
  TOMTOM_API_KEY        --> chave da TomTom API
  SUPABASE_DB_URL       --> string de conexao PostgreSQL do Supabase
```

O workflow (`.github/workflows/monitor.yml`) injeta esses secrets como variaveis
de ambiente. O Python le com `os.environ["GOOGLE_MAPS_API_KEY"]`.

### Vercel (para o React frontend)

Configurado em: **Vercel > Project Settings > Environment Variables**

```
Variaveis necessarias:
  VITE_SUPABASE_URL       --> URL do projeto Supabase (ex: https://xxx.supabase.co)
  VITE_SUPABASE_ANON_KEY  --> chave publica (anon) do Supabase
```

O prefixo `VITE_` e obrigatorio para o Vite expor a variavel no frontend.
A chave `anon` e publica por design (protegida por Row Level Security no banco).

### Regra de ouro

| Segredo | Onde fica | Quem usa |
|---------|-----------|----------|
| API keys (Google, HERE, TomTom) | GitHub Secrets | Python (coleta) |
| SUPABASE_DB_URL | GitHub Secrets | Python (escrita no banco) |
| VITE_SUPABASE_URL | Vercel Env Vars | React (leitura do banco) |
| VITE_SUPABASE_ANON_KEY | Vercel Env Vars | React (leitura do banco) |

**NUNCA** coloque API keys no codigo-fonte ou no repositorio.

---

## 7. Como fazer deploy de cada parte

### 7a. GitHub Actions (coleta Python)

Nao precisa fazer nada manualmente. O deploy e automatico:

1. Faca push para a branch `main`
2. O arquivo `.github/workflows/monitor.yml` define o cron
3. O GitHub Actions roda automaticamente no horario configurado

Para rodar manualmente: va em **Actions > Monitor > Run workflow**.

### 7b. Supabase (banco de dados)

O banco ja esta criado. Se precisar recriar as tabelas:

1. Acesse o **Supabase Dashboard** > **SQL Editor**
2. Execute o SQL de criacao das tabelas (veja `ARQUITETURA.md` secao 10)
3. Habilite o **Realtime** na tabela `snapshots_rotas`:
   - Database > Tables > snapshots_rotas > Realtime: ON

### 7c. Vercel (frontend React)

```bash
# Na pasta frontend/
cd frontend

# Primeira vez: vincular ao projeto Vercel
vercel link

# Deploy para producao
vercel --prod
```

Ou configure o deploy automatico:
1. Conecte o repositorio GitHub no Vercel
2. Defina `frontend` como Root Directory
3. Todo push na `main` faz deploy automatico

---

## 8. FAQ Junior

### "Preciso rodar tudo junto na minha maquina?"

Nao. Cada parte roda independente. Para desenvolvimento local:

- **Testar coleta Python:** `python main.py --config config.json` (precisa das API keys no `.env`)
- **Testar frontend:** `cd frontend && npm run dev` (precisa das variaveis VITE_*)
- Voce pode testar um sem o outro.

### "O que e o Supabase?"

E um servico que te da um banco PostgreSQL + autenticacao + Realtime de graca.
Pense nele como um "Firebase, mas com PostgreSQL de verdade".
Site: https://supabase.com

### "O que sao circuit breakers?"

Se uma API externa (ex: Google) comecar a dar erro, o circuit breaker "desliga"
as chamadas para ela por 60 segundos. Isso evita:
- Gastar cota da API com requisicoes que vao falhar
- Travar o sistema esperando timeout

Apos 60s, ele tenta de novo. Se funcionar, volta ao normal.

### "O que e ThreadPoolExecutor?"

E o jeito do Python fazer varias coisas ao mesmo tempo. Em vez de consultar
as 20 rotas uma por uma (lento), ele consulta varias em paralelo (rapido).

### "O que e o Realtime do Supabase?"

Quando o Python salva dados novos no banco, o Supabase automaticamente avisa
o frontend via WebSocket (uma conexao permanente). Assim o painel atualiza
sem precisar ficar fazendo refresh.

### "Se der erro nas APIs, o que acontece?"

O sistema tem 3 camadas de protecao:
1. **Retry:** tenta de novo 3 vezes com espera crescente
2. **Circuit breaker:** se continuar falhando, para de tentar por 60s
3. **Degradacao:** se uma fonte falha, as outras continuam (resultado parcial)

O painel mostra os dados disponiveis, mesmo que uma fonte esteja fora.

### "Quanto custa rodar isso?"

$0/mes. Todos os servicos estao dentro dos limites gratuitos:
- GitHub Actions: 2000 min/mes gratis
- Supabase: 500MB banco + 2GB transferencia gratis
- Vercel: 100GB bandwidth gratis
- APIs de mapas: dentro dos free tiers

### "Onde vejo os logs de execucao?"

No GitHub: **Actions > Monitor > (clique na execucao)**.
Cada step mostra os logs em tempo real (quantas rotas processou, erros, etc).

---

## Proximo passo

Agora que voce entende a visao geral, leia:
- [COMO_FUNCIONA.md](COMO_FUNCIONA.md) -- explicacao detalhada do sistema
- [OPERACAO.md](OPERACAO.md) -- guia de operacao e troubleshooting
- [ARQUITETURA.md](ARQUITETURA.md) -- versao tecnica completa (com diagramas Mermaid)
