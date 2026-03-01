import { useEffect, useRef } from 'react';
import styles from './RotaCard.module.css';

const STATUS_CLASS = {
  Normal: styles.normal,
  Moderado: styles.moderado,
  Intenso: styles.intenso,
  Parado: styles.parado,
  'Sem dados': styles.semDados,
};

const BADGE_CLASS = {
  Normal: styles.badgeNormal,
  Moderado: styles.badgeModerado,
  Intenso: styles.badgeIntenso,
  Parado: styles.badgeParado,
  'Sem dados': styles.badgeSemDados,
};

function occClass(ocorrencia) {
  const o = (ocorrencia || '').toLowerCase();
  if (o.includes('interd')) return styles.occParado;
  if (o.includes('acidente')) return styles.occIntenso;
  if (o.includes('engarraf') || o.includes('congest')) return styles.occModerado;
  return styles.occDefault;
}

/**
 * Card individual de rota com status colorido e flash animation ao atualizar.
 *
 * @param {{ rota: object, animDelay?: number }} props
 */
const RotaCard = ({ rota, animDelay = 0 }) => {
  const cardRef = useRef(null);
  const prevStatusRef = useRef(rota.status);

  useEffect(() => {
    if (prevStatusRef.current !== rota.status && cardRef.current) {
      const card = cardRef.current;
      card.classList.remove(styles.updated);
      void card.offsetWidth;
      card.classList.add(styles.updated);

      const handleEnd = () => card.classList.remove(styles.updated);
      card.addEventListener('animationend', handleEnd, { once: true });
    }
    prevStatusRef.current = rota.status;
  }, [rota.status]);

  const status = rota.status || 'Sem dados';
  const atraso = rota.atraso_min > 0 ? `+${Math.round(rota.atraso_min)} min` : '';
  const conf = rota.confianca_pct != null ? `${rota.confianca_pct}%` : '';

  return (
    <div
      ref={cardRef}
      className={`${styles.card} ${STATUS_CLASS[status] || ''}`}
      data-status={status}
      style={{ animationDelay: `${animDelay}s` }}
    >
      <div className={styles.header}>
        <span className={styles.rodovia}>{rota.rodovia || '--'}</span>
        <span className={`${styles.badge} ${BADGE_CLASS[status] || ''}`}>{status}</span>
      </div>

      <div className={styles.trecho} title={rota.trecho || ''}>
        {rota.trecho || '--'}
      </div>

      <div className={styles.detail}>
        {rota.sentido && (
          <span className={styles.sentido}>{rota.sentido}</span>
        )}
        {atraso && (
          <>
            <span className={styles.separator}>•</span>
            <span className={styles.atraso}>{atraso}</span>
          </>
        )}
        {rota.ocorrencia && (
          <>
            <span className={styles.separator}>•</span>
            <span className={`${styles.ocorrencia} ${occClass(rota.ocorrencia)}`}>
              {rota.ocorrencia}
            </span>
          </>
        )}
        {conf && (
          <span className={styles.conf}>
            <span className={styles.confLabel}>conf.</span>
            {conf}
          </span>
        )}
        {rota.conflito_fontes && (
          <span className={styles.conflito}>CONFLITO</span>
        )}
      </div>
    </div>
  );
};

export default RotaCard;
