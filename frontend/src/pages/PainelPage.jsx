import { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { useSupabaseRealtime } from '@/hooks/useSupabaseRealtime';
import { supabase } from '@/services/supabase';
import KpiCard from '@/components/KpiCard';
import RotaCard from '@/components/RotaCard';
import Ticker from '@/components/Ticker';
import RealtimeIndicator from '@/components/RealtimeIndicator';
import Clock from '@/components/Clock';
import styles from './PainelPage.module.css';

const STATUS_ORDER = { Parado: 0, Intenso: 1, Moderado: 2, Normal: 3, 'Sem dados': 4 };

function calcKpis(dados) {
  const counts = { Normal: 0, Moderado: 0, Intenso: 0, Parado: 0, 'Sem dados': 0 };
  dados.forEach((d) => {
    const s = d.status || 'Sem dados';
    if (s in counts) counts[s]++;
    else counts['Sem dados']++;
  });
  return { ...counts, total: dados.length };
}

const PainelPage = () => {
  const { user, logout } = useAuth();
  const [dados, setDados] = useState([]);
  const [ultimoCiclo, setUltimoCiclo] = useState('');
  const [loading, setLoading] = useState(true);
  const routesRef = useRef(null);
  const rafRef = useRef(null);
  const cicloIdRef = useRef(null);
  const pauseScrollRef = useRef(false);

  const [expandedTrecho, setExpandedTrecho] = useState(null);

  const onCardToggle = useCallback((trecho) => {
    setExpandedTrecho(prev => {
      const next = prev === trecho ? null : trecho;
      pauseScrollRef.current = next !== null;
      return next;
    });
  }, []);

  const fetchInitialData = useCallback(async () => {
    try {
      // Busca o ultimo ciclo
      const { data: ciclo } = await supabase
        .from('ciclos')
        .select('id, ts, ts_iso')
        .order('ts_iso', { ascending: false })
        .limit(1)
        .single();

      if (!ciclo) {
        setLoading(false);
        return;
      }

      cicloIdRef.current = ciclo.id;

      // Extrai HH:MM do timestamp
      const parts = ciclo.ts.split(' ');
      const hhmm = parts[1] ? parts[1].substring(0, 5) : ciclo.ts;
      setUltimoCiclo(hhmm);

      // Busca snapshots desse ciclo
      const { data: snapshots } = await supabase
        .from('snapshots_rotas')
        .select('trecho, rodovia, sentido, status, ocorrencia, atraso_min, confianca_pct, conflito_fontes, descricao')
        .eq('ciclo_id', ciclo.id)
        .order('trecho');

      if (snapshots) {
        setDados(snapshots.map((r) => ({
          ...r,
          conflito_fontes: Boolean(r.conflito_fontes),
        })));
      }
    } catch (err) {
      console.warn('[PainelPage] Erro ao buscar dados iniciais:', err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Callback do Realtime: ao receber INSERT, recarrega o ciclo inteiro
  const onRealtimeInsert = useCallback((newRow) => {
    // Se o ciclo mudou, recarrega tudo
    if (newRow.ciclo_id !== cicloIdRef.current) {
      cicloIdRef.current = newRow.ciclo_id;
      fetchInitialData();
    } else {
      // Mesmo ciclo: upsert no estado
      setDados((prev) => {
        const idx = prev.findIndex((d) => d.trecho === newRow.trecho);
        const mapped = {
          trecho: newRow.trecho,
          rodovia: newRow.rodovia,
          sentido: newRow.sentido,
          status: newRow.status,
          ocorrencia: newRow.ocorrencia,
          atraso_min: newRow.atraso_min,
          confianca_pct: newRow.confianca_pct,
          conflito_fontes: Boolean(newRow.conflito_fontes),
          descricao: newRow.descricao || '',
        };
        if (idx >= 0) {
          const copy = [...prev];
          copy[idx] = mapped;
          return copy;
        }
        return [...prev, mapped];
      });
    }
  }, [fetchInitialData]);

  const { status: realtimeStatus } = useSupabaseRealtime(onRealtimeInsert);

  useEffect(() => {
    fetchInitialData();
  }, [fetchInitialData]);

  // Polling de fallback: quando Realtime nao conecta, recarrega a cada 60s
  useEffect(() => {
    if (realtimeStatus !== 'polling') return;
    const id = setInterval(fetchInitialData, 60_000);
    return () => clearInterval(id);
  }, [realtimeStatus, fetchInitialData]);

  // Auto-scroll na area de rotas
  useEffect(() => {
    const area = routesRef.current;
    if (!area) return;

    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    if (area.scrollHeight <= area.clientHeight + 2) {
      area.scrollTop = 0;
      return;
    }

    let lastTs = null;
    const PX_PER_SEC = 22;

    function tick(ts) {
      if (lastTs !== null && !pauseScrollRef.current) {
        const delta = Math.min(ts - lastTs, 100);
        const max = area.scrollHeight - area.clientHeight;
        area.scrollTop += PX_PER_SEC * delta / 1000;
        if (area.scrollTop >= max) area.scrollTop = 0;
      }
      lastTs = ts;
      rafRef.current = requestAnimationFrame(tick);
    }

    const timer = setTimeout(() => {
      rafRef.current = requestAnimationFrame(tick);
    }, 800);

    return () => {
      clearTimeout(timer);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [dados]);

  const sorted = [...dados].sort((a, b) => {
    const oa = STATUS_ORDER[a.status] ?? 4;
    const ob = STATUS_ORDER[b.status] ?? 4;
    if (oa !== ob) return oa - ob;
    return (b.atraso_min || 0) - (a.atraso_min || 0);
  });

  const kpis = calcKpis(dados);

  return (
    <div className={styles.page}>
      <div className={`${styles.orb} ${styles.orb1}`} />
      <div className={`${styles.orb} ${styles.orb2}`} />
      <div className={`${styles.orb} ${styles.orb3}`} />

      <div className={styles.grid}>
        {/* Sidebar */}
        <aside className={styles.sidebar}>
          {/* Brand */}
          <div className={styles.brand}>
            <div className={styles.brandTop}>
              <div className={styles.brandLogo}>
                <span className={styles.logoText}>RM</span>
                <span className={styles.logoPro}>Pro</span>
              </div>
              <Clock />
            </div>
            <div className={styles.brandStatus}>
              <RealtimeIndicator status={realtimeStatus} ultimoCiclo={ultimoCiclo} />
              <button className={styles.btnLogout} onClick={logout} title="Sair">
                Sair
              </button>
            </div>
          </div>

          {/* KPIs */}
          <KpiCard variant="total"    label="Total"    value={kpis.total} />
          <KpiCard variant="normal"   label="Normal"   value={kpis.Normal}   total={kpis.total} />
          <KpiCard variant="moderado" label="Moderado" value={kpis.Moderado} total={kpis.total} />
          <KpiCard variant="intenso"  label="Intenso"  value={kpis.Intenso}  total={kpis.total} />
          <KpiCard variant="parado"   label="Parado"   value={kpis.Parado}   total={kpis.total} />
          <KpiCard variant="sem-dados" label="Sem dados" value={kpis['Sem dados']} />
        </aside>

        {/* Area de rotas */}
        <main ref={routesRef} className={styles.routesArea}>
          {loading ? (
            <div className={styles.loadingState}>
              <div className={styles.spinner} />
              Aguardando dados do servidor...
            </div>
          ) : sorted.length === 0 ? (
            <div className={styles.loadingState}>Nenhum trecho monitorado</div>
          ) : (
            sorted.map((rota, i) => (
              <RotaCard
                key={rota.trecho || i}
                rota={rota}
                animDelay={parseFloat((Math.random() * 0.3).toFixed(2))}
                expanded={expandedTrecho === rota.trecho}
                onToggle={() => onCardToggle(rota.trecho)}
              />
            ))
          )}
        </main>
      </div>

      <Ticker dados={dados} />
    </div>
  );
};

export default PainelPage;
