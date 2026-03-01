# Operacao - RodoviaMonitor Pro

## 1. Pre-requisitos

- Python 3.10+
- Dependencias instaladas (`pip install -r requirements.txt`)
- Arquivo `.env` com:
  - `GOOGLE_MAPS_API_KEY`
  - `HERE_API_KEY`

## 2. Modos de execucao

### Execucao unica

```bash
python main.py --config config.json
```

### Modo MVP

```bash
python main.py --modo-mvp --config config_mvp.json
```

### Polling continuo

```bash
python main.py --modo-mvp --interval 30
```

### Agendamento por horarios

```bash
python main.py --agendar
```

### Log estruturado

```bash
python main.py --modo-mvp --log-json
```

## 3. Resultado esperado

- Arquivo `.xlsx` criado em `relatorios/`.
- Log com resumo por fonte, incluindo quantidade de incidentes e status por trecho.

## 4. Sinais operacionais importantes no log

- `DEGRADACAO: Google Maps falhou em TODOS os trechos`
- `DEGRADACAO: HERE retornou dados vazios para TODOS os trechos`
- `Circuit breaker ... aberto`

Quando algum desses sinais ocorrer, considere o ciclo com confiabilidade reduzida.

## 5. Procedimento de troubleshooting

1. Validar se o `.env` existe e contem chaves corretas.
2. Confirmar `"enabled": true` para as fontes no JSON usado.
3. Executar uma rodada unica com `--log-json` para diagnostico.
4. Verificar se houve fallback para Routes API v2 ou abertura de circuit breaker.
5. Repetir coleta apos 1-2 minutos para descartar falha transiente de rede/API.

## 6. Testes e validacao antes de deploy

```bash
python -m pytest tests/ -v --tb=short
python -m pytest tests/ --cov=sources --cov=report --cov-report=term-missing --no-cov-on-fail -q
```

Atalhos Windows:

- `run_tests.bat`
- `run_validation.bat`

## 7. Boas praticas operacionais

- Evitar comitar `.env` ou qualquer segredo.
- Manter configuracoes versionadas por ambiente (ex.: `config_mvp.json` para operacao leve).
- Acompanhar pasta `relatorios/` para evitar crescimento sem controle.
- Revisar periodicamente a lista de rotas e pontos de referencia.

## 8. Checklist de readiness

- Chaves API validas e ativas.
- Arquivo de rotas consistente (campos obrigatorios por trecho).
- Testes unitarios passando.
- Comando de execucao definido para o modo desejado (unico, polling ou agendamento).
