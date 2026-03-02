#!/usr/bin/env python3
"""seed_waypoints.py - Densifica waypoints em rota_logistica.json usando HERE Routing v8.

Para cada rota, chama HERE Routing v8 para obter a polyline real,
calcula distancia acumulada ao longo da polyline e re-amostra pontos
equidistantes a cada ~85km (target para manter gaps 75-100km).

Uso:
    python seed_waypoints.py --dry-run          # mostra o que faria sem salvar
    python seed_waypoints.py                     # atualiza JSON (cria backup)
    python seed_waypoints.py --target-gap 90     # gap customizado (km)
    python seed_waypoints.py --routes R01,R06    # apenas rotas especificas
"""

import argparse
import copy
import json
import math
import os
import shutil
import sys
import time
from datetime import datetime, timezone

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("ERRO: requests nao instalado. pip install requests")
    sys.exit(1)

try:
    import flexpolyline as fp
except ImportError:
    print("ERRO: flexpolyline nao instalado. pip install flexpolyline")
    sys.exit(1)


# --- .env loader ---
def _carregar_env(caminho):
    """Carrega variaveis de .env sem sobrescrever existentes."""
    if not os.path.isfile(caminho):
        return
    with open(caminho, "r", encoding="utf-8") as f:
        for linha in f:
            txt = linha.strip()
            if not txt or txt.startswith("#"):
                continue
            if txt.lower().startswith("export "):
                txt = txt[7:].strip()
            if "=" not in txt:
                continue
            chave, valor = txt.split("=", 1)
            chave = chave.strip().lstrip("\ufeff")
            valor = valor.strip()
            if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ("'", '"'):
                valor = valor[1:-1]
            if chave and chave not in os.environ:
                os.environ[chave] = valor


# --- Constants ---
EARTH_R = 6_371_000.0  # metros
HERE_ROUTING_URL = "https://router.hereapi.com/v8/routes"
MAX_SEGMENT_KM = 450  # limite HERE corridor
DEFAULT_TARGET_GAP_KM = 85
DELAY_BETWEEN_ROUTES_S = 1.5
JSON_PATH = os.path.join(os.path.dirname(__file__), "rota_logistica.json")


def _haversine_m(lat1, lon1, lat2, lon2):
    """Distancia haversine em metros."""
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def _cumulative_distances(pts):
    """Retorna lista de distancias acumuladas (metros) ao longo da polyline."""
    dists = [0.0]
    for i in range(1, len(pts)):
        d = _haversine_m(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])
        dists.append(dists[-1] + d)
    return dists


def _resample_polyline(pts, target_gap_m):
    """Re-amostra pontos equidistantes ao longo da polyline.

    Retorna lista de (lat, lng) intermediarios (sem incluir origem/destino).
    """
    if len(pts) < 2:
        return []

    dists = _cumulative_distances(pts)
    total_m = dists[-1]
    if total_m <= target_gap_m:
        return []

    n_segments = max(1, round(total_m / target_gap_m))
    step = total_m / n_segments

    waypoints = []
    for seg_idx in range(1, n_segments):
        target_d = seg_idx * step
        # encontra o segmento da polyline onde target_d cai
        for i in range(1, len(dists)):
            if dists[i] >= target_d:
                frac = (target_d - dists[i - 1]) / max(1e-9, dists[i] - dists[i - 1])
                lat = pts[i - 1][0] + frac * (pts[i][0] - pts[i - 1][0])
                lng = pts[i - 1][1] + frac * (pts[i][1] - pts[i - 1][1])
                waypoints.append((round(lat, 6), round(lng, 6)))
                break

    return waypoints


def _max_gap_km(origin, via_pts, destination):
    """Calcula o maior gap (km) entre pontos consecutivos da rota."""
    all_pts = [origin] + via_pts + [destination]
    max_gap = 0.0
    for i in range(1, len(all_pts)):
        d = _haversine_m(all_pts[i - 1][0], all_pts[i - 1][1],
                         all_pts[i][0], all_pts[i][1]) / 1000.0
        if d > max_gap:
            max_gap = d
    return max_gap


def _create_session():
    """Cria session com retry."""
    sess = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    return sess


def _call_routing_v8(session, api_key, origin, destination, via_coords=None):
    """Chama HERE Routing v8 e retorna lista de (lat, lng) da polyline stitched."""
    params = {
        "transportMode": "car",
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "return": "polyline,summary",
        "apikey": api_key,
    }
    if via_coords:
        params["via"] = [f"{lat},{lng}!passThrough=true" for lat, lng in via_coords]

    timeout = 30 if via_coords else 15
    resp = session.get(HERE_ROUTING_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    routes = data.get("routes") or []
    if not routes:
        return None, 0.0

    sections = routes[0].get("sections") or []
    all_pts = []
    total_length_m = 0.0
    for sec in sections:
        poly = sec.get("polyline")
        if poly:
            decoded = fp.decode(poly)
            for p in decoded:
                pt = (p[0], p[1])  # strip to 2D
                if not all_pts or all_pts[-1] != pt:
                    all_pts.append(pt)
        summary = sec.get("summary") or {}
        total_length_m += summary.get("length", 0)

    return all_pts, total_length_m / 1000.0


def process_route(session, api_key, route, target_gap_km, dry_run=True):
    """Processa uma rota: chama Routing v8, re-amostra waypoints."""
    rid = route["id"]
    here = route["here"]
    origin_str = here["origin"]
    dest_str = here["destination"]

    origin = tuple(float(x) for x in origin_str.split(","))
    destination = tuple(float(x) for x in dest_str.split(","))

    # via points existentes
    existing_via = here.get("via", [])
    existing_coords = []
    for v in existing_via:
        parts = v.split("!")[0].split(",")
        existing_coords.append((float(parts[0]), float(parts[1])))

    # Calcula gap atual
    current_gap = _max_gap_km(origin, existing_coords, destination)

    if current_gap <= target_gap_km:
        print(f"  [{rid}] OK - gap atual {current_gap:.0f}km <= target {target_gap_km}km, pulando")
        return None

    print(f"  [{rid}] Gap atual: {current_gap:.0f}km > {target_gap_km}km")
    print(f"         Chamando HERE Routing v8 com {len(existing_coords)} via points...")

    polyline_pts, distance_km = _call_routing_v8(
        session, api_key, origin, destination, existing_coords or None
    )

    if not polyline_pts:
        print(f"         ERRO: Routing v8 nao retornou polyline")
        return None

    if distance_km <= 0:
        # Fallback: calcula pela polyline
        dists = _cumulative_distances(polyline_pts)
        distance_km = dists[-1] / 1000.0

    print(f"         Polyline: {len(polyline_pts)} pontos, {distance_km:.0f}km")

    # Re-amostrar
    target_gap_m = target_gap_km * 1000
    new_via = _resample_polyline(polyline_pts, target_gap_m)

    if not new_via:
        print(f"         Rota muito curta para re-amostragem")
        return None

    # Validar que nenhum segmento > MAX_SEGMENT_KM
    all_check = [origin] + new_via + [destination]
    max_seg = 0
    for i in range(1, len(all_check)):
        d = _haversine_m(all_check[i - 1][0], all_check[i - 1][1],
                         all_check[i][0], all_check[i][1]) / 1000.0
        if d > max_seg:
            max_seg = d

    new_gap = _max_gap_km(origin, new_via, destination)
    print(f"         Novo: {len(new_via)} via points, gap max {new_gap:.0f}km (era {current_gap:.0f}km)")

    if max_seg > MAX_SEGMENT_KM:
        print(f"         AVISO: segmento max {max_seg:.0f}km > {MAX_SEGMENT_KM}km!")

    if dry_run:
        return None

    # Atualizar rota
    new_via_strs = [f"{lat},{lng}!passThrough=true" for lat, lng in new_via]
    route["here"]["via"] = new_via_strs
    route["waypoints_status"] = {
        "has_waypoints": True,
        "source": "seed_waypoints.py: HERE Routing v8 polyline resampling",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_points": len(new_via),
        "distance_km": round(distance_km, 1),
    }
    # Atualizar limite_gap_km para ~target + margem
    route["limite_gap_km"] = round(target_gap_km * 1.25)

    return {
        "id": rid,
        "old_via": len(existing_coords),
        "new_via": len(new_via),
        "old_gap": current_gap,
        "new_gap": new_gap,
        "distance_km": distance_km,
    }


def main():
    parser = argparse.ArgumentParser(description="Densifica waypoints via HERE Routing v8")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Mostra o que faria sem salvar (default)")
    parser.add_argument("--target-gap", type=float, default=DEFAULT_TARGET_GAP_KM,
                        help=f"Gap alvo em km (default: {DEFAULT_TARGET_GAP_KM})")
    parser.add_argument("--routes", type=str, default=None,
                        help="IDs de rotas separados por virgula (ex: R01,R06)")
    parser.add_argument("--json-path", type=str, default=JSON_PATH,
                        help="Caminho do rota_logistica.json")
    args = parser.parse_args()

    # Carrega .env do diretorio do script e do cwd
    script_dir = os.path.dirname(os.path.abspath(__file__))
    _carregar_env(os.path.join(script_dir, ".env"))
    _carregar_env(os.path.join(os.getcwd(), ".env"))

    api_key = os.environ.get("HERE_API_KEY", "")
    if not api_key:
        print("ERRO: HERE_API_KEY nao definida. Export a variavel de ambiente.")
        sys.exit(1)

    json_path = args.json_path
    if not os.path.exists(json_path):
        print(f"ERRO: Arquivo nao encontrado: {json_path}")
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    routes = data.get("routes", [])
    if not routes:
        print("ERRO: Nenhuma rota encontrada no JSON")
        sys.exit(1)

    # Filtrar rotas se especificado
    route_filter = None
    if args.routes:
        route_filter = set(args.routes.upper().split(","))

    target_gap = args.target_gap
    dry_run = args.dry_run

    mode_str = "DRY-RUN" if dry_run else "LIVE"
    print(f"=== seed_waypoints.py [{mode_str}] ===")
    print(f"    Target gap: {target_gap}km")
    print(f"    Rotas: {', '.join(route_filter) if route_filter else 'todas'}")
    print(f"    JSON: {json_path}")
    print()

    session = _create_session()
    results = []
    skipped = 0
    errors = 0

    for i, route in enumerate(routes):
        rid = route["id"]
        if route_filter and rid not in route_filter:
            continue

        try:
            result = process_route(session, api_key, route, target_gap, dry_run)
            if result:
                results.append(result)
        except Exception as e:
            err_msg = str(e)
            # Sanitize API key from error
            if api_key and len(api_key) > 8:
                err_msg = err_msg.replace(api_key, "***")
            print(f"  [{rid}] ERRO: {err_msg}")
            errors += 1

        # Rate limit between API calls
        if i < len(routes) - 1:
            time.sleep(DELAY_BETWEEN_ROUTES_S)

    print()
    print(f"=== Resumo ===")
    print(f"    Atualizadas: {len(results)}")
    print(f"    Erros: {errors}")

    if results:
        print()
        print(f"    {'Rota':<6} {'Via(old)':<10} {'Via(new)':<10} {'Gap(old)':<10} {'Gap(new)':<10} {'Dist(km)':<10}")
        for r in results:
            print(f"    {r['id']:<6} {r['old_via']:<10} {r['new_via']:<10} "
                  f"{r['old_gap']:<10.0f} {r['new_gap']:<10.0f} {r['distance_km']:<10.0f}")

    if dry_run:
        print()
        print("    (dry-run: nenhuma alteracao salva. Remova --dry-run para aplicar)")
        return

    if not results:
        print("    Nada a salvar.")
        return

    # Backup
    backup_path = json_path + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(json_path, backup_path)
    print(f"    Backup: {backup_path}")

    # Salvar
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"    Salvo: {json_path}")


if __name__ == "__main__":
    main()
