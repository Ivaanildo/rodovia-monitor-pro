#!/usr/bin/env python3
"""
Script de verificacao de APIs configuradas
Verifica se as chaves de API estao presentes e validas
"""
import os
import sys
from pathlib import Path

# Adiciona o diretorio raiz ao path
sys.path.insert(0, str(Path(__file__).parent))

# Carrega .env se existir
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and not key in os.environ:
                    os.environ[key] = value


def check_api_key(name, env_var):
    """Verifica se uma chave de API esta configurada"""
    key = os.getenv(env_var, "").strip()

    if not key:
        return "[NAO CONFIGURADA]", None

    if "SUA_" in key.upper() or "AQUI" in key.upper():
        return "[EXEMPLO - nao substituida]", key[:20] + "..."

    if len(key) < 10:
        return "[SUSPEITA - muito curta]", key[:20] + "..."

    return "[OK - CONFIGURADA]", key[:20] + "..."


def main():
    print("=" * 70)
    print("VERIFICACAO DE APIs - Monitor Rodovias")
    print("=" * 70)
    print()

    # Verifica cada API
    apis = [
        ("Google Maps API", "GOOGLE_MAPS_API_KEY", True),
        ("HERE Traffic API", "HERE_API_KEY", True),
        ("HyperBrowser API", "HYPERBROWSER_API_KEY", False),
    ]

    all_required_ok = True
    has_hyperbrowser = False

    for name, env_var, required in apis:
        status, preview = check_api_key(name, env_var)
        required_text = "OBRIGATORIA" if required else "Opcional"

        print(f"{name}")
        print(f"  Variavel: {env_var}")
        print(f"  Status: {status}")
        if preview:
            print(f"  Preview: {preview}")
        print(f"  Tipo: {required_text}")
        print()

        if required and "NAO CONFIGURADA" in status:
            all_required_ok = False

        if env_var == "HYPERBROWSER_API_KEY" and "OK" in status:
            has_hyperbrowser = True

    # Verifica banco de dados (opcional para modernizacao)
    print("CONFIGURACOES DE MODERNIZACAO (Opcional)")
    print("-" * 70)

    db_url = os.getenv("DATABASE_URL", "")
    redis_url = os.getenv("REDIS_URL", "")

    if db_url and "postgresql" in db_url:
        print("[OK] PostgreSQL configurado")
    else:
        print("[--] PostgreSQL nao configurado (necessario para rotas dinamicas)")

    if redis_url and "redis" in redis_url:
        print("[OK] Redis configurado")
    else:
        print("[--] Redis nao configurado (necessario para cache e filas)")

    print()
    print("=" * 70)
    print("RESUMO")
    print("=" * 70)


    if all_required_ok:
        print("[OK] Todas as APIs obrigatorias estao configuradas!")
        print("     Sistema atual (CLI + 28 rotas fixas) pode funcionar")
    else:
        print("[!!] APIs obrigatorias faltando!")
        print("     Configure GOOGLE_MAPS_API_KEY e HERE_API_KEY no arquivo .env")

    print()

    if has_hyperbrowser:
        print("[OK] HyperBrowser configurado!")
        print("     >> PRONTO PARA PASSO 3: Implementar scraping inteligente")
        print("     >> Pode comecar com scrapers de Waze e concessionarias")
    else:
        print("[--] HyperBrowser NAO configurado")
        print("     Sistema funcionara apenas com Google Maps e HERE APIs")
        print("     Para scraping inteligente, configure HYPERBROWSER_API_KEY")
        print("     Obter chave em: https://hyperbrowser.ai")

    print()

    if db_url and redis_url:
        print("[OK] Infraestrutura de modernizacao configurada!")
        print("     PostgreSQL + Redis detectados")
        print("     Pode implementar rotas dinamicas")
    else:
        print("[--] Infraestrutura de modernizacao nao configurada")
        print("     Sistema funcionara em modo CLI (28 rotas fixas)")
        print("     Para rotas dinamicas, configure DATABASE_URL e REDIS_URL")

    print()
    print("=" * 70)

    # Retorna codigo de saida
    if all_required_ok and has_hyperbrowser:
        print(">> STATUS: PRONTO PARA PASSO 3 (Modernizacao completa)")
        return 0
    elif all_required_ok:
        print(">> STATUS: SISTEMA ATUAL OK (sem scraping inteligente)")
        return 0
    else:
        print(">> STATUS: CONFIGURACAO INCOMPLETA")
        return 1


if __name__ == "__main__":
    sys.exit(main())
