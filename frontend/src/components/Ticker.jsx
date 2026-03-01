import { useMemo } from 'react';
import styles from './Ticker.module.css';

const STATUS_ORDER = { Parado: 0, Intenso: 1 };

/**
 * Faixa de alertas críticos na base do painel (Intenso + Parado).
 *
 * @param {{ dados: object[] }} props
 */
const Ticker = ({ dados = [] }) => {
  const criticos = useMemo(() => {
    return dados
      .filter((d) => d.status === 'Intenso' || d.status === 'Parado')
      .sort((a, b) => (STATUS_ORDER[a.status] ?? 4) - (STATUS_ORDER[b.status] ?? 4));
  }, [dados]);

  const temAlertas = criticos.length > 0;

  const labelText = temAlertas
    ? `${criticos.length} ALERTA${criticos.length > 1 ? 'S' : ''}`
    : 'OK';

  const plainLen = useMemo(() => {
    return criticos.reduce((acc, d) => {
      const atraso = d.atraso_min > 0 ? ` (+${Math.round(d.atraso_min)}min)` : '';
      const occ = d.ocorrencia ? ` | ${d.ocorrencia}` : '';
      return acc + `[${d.status}] ${d.rodovia} - ${d.trecho}${atraso}${occ}`.length + 8;
    }, 0);
  }, [criticos]);

  const duration = Math.max(20, plainLen * 0.12);

  return (
    <footer className={`${styles.bar}`}>
      <span className={`${styles.label} ${!temAlertas ? styles.labelOk : ''}`}>
        {labelText}
      </span>
      <div className={styles.track}>
        {temAlertas ? (
          <span
            className={styles.content}
            style={{ animationDuration: `${duration}s` }}
          >
            {criticos.map((d, i) => {
              const atraso = d.atraso_min > 0 ? ` (+${Math.round(d.atraso_min)}min)` : '';
              const occ = d.ocorrencia ? ` | ${d.ocorrencia}` : '';
              const text = `[${d.status}] ${d.rodovia} - ${d.trecho}${atraso}${occ}`;
              const segClass = d.status === 'Parado' ? styles.segParado : styles.segIntenso;

              return (
                <span key={i}>
                  <span className={`${styles.seg} ${segClass}`}>{text}</span>
                  {i < criticos.length - 1 && <span className={styles.sep}> /// </span>}
                </span>
              );
            })}
            <span className={styles.sep}> /// </span>
            {/* Duplicação para loop infinito seamless */}
            {criticos.map((d, i) => {
              const atraso = d.atraso_min > 0 ? ` (+${Math.round(d.atraso_min)}min)` : '';
              const occ = d.ocorrencia ? ` | ${d.ocorrencia}` : '';
              const text = `[${d.status}] ${d.rodovia} - ${d.trecho}${atraso}${occ}`;
              const segClass = d.status === 'Parado' ? styles.segParado : styles.segIntenso;

              return (
                <span key={`dup-${i}`}>
                  <span className={`${styles.seg} ${segClass}`}>{text}</span>
                  {i < criticos.length - 1 && <span className={styles.sep}> /// </span>}
                </span>
              );
            })}
          </span>
        ) : (
          <span className={`${styles.content} ${styles.contentStatic}`}>
            Todas as rotas operando normalmente
          </span>
        )}
      </div>
    </footer>
  );
};

export default Ticker;
