import { useState, useEffect, useRef } from 'react';
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
  if (o.includes('colisão') || o.includes('colisao') || o.includes('acidente')) return styles.occIntenso;
  if (o.includes('bloqueio parcial')) return styles.occBloqueioP;
  if (o.includes('engarraf') || o.includes('congest')) return styles.occModerado;
  return styles.occDefault;
}

/**
 * Card com 2 faces (crossfade via opacity swap).
 * Clique alterna entre frente (dados) e verso (observacao).
 *
 * @param {{ rota: object, animDelay?: number, onFlip?: (isFlipped: boolean) => void }} props
 */
const RotaCard = ({ rota, animDelay = 0, onFlip }) => {
  const cardRef = useRef(null);
  const prevStatusRef = useRef(rota.status);
  const [flipped, setFlipped] = useState(false);

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

  const toggle = () => {
    const next = !flipped;
    setFlipped(next);
    onFlip?.(next);
  };

  const status = rota.status || 'Sem dados';
  const atraso = rota.atraso_min > 0 ? `+${Math.round(rota.atraso_min)} min` : '';
  const conf = rota.confianca_pct != null ? `${rota.confianca_pct}%` : '';

  return (
    <div
      ref={cardRef}
      className={`${styles.card} ${STATUS_CLASS[status] || ''} ${flipped ? styles.cardFlipped : ''}`}
      style={{ animationDelay: `${animDelay}s` }}
      data-status={status}
      onClick={toggle}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } }}
      aria-label={flipped ? 'Clique para voltar' : 'Clique para ver observacoes'}
    >
      {/* ── Face Frente ── */}
      <div className={`${styles.face} ${flipped ? styles.faceHidden : styles.faceVisible}`}>
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
              <span className={styles.separator}>&bull;</span>
              <span className={styles.atraso}>{atraso}</span>
            </>
          )}
          {rota.ocorrencia && (
            <>
              <span className={styles.separator}>&bull;</span>
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

        <span className={styles.flipHint}>
          ver obs. <span className={styles.arrow}>&rarr;</span>
        </span>
      </div>

      {/* ── Face Verso ── */}
      <div className={`${styles.face} ${flipped ? styles.faceVisible : styles.faceHidden}`}>
        <div className={styles.header}>
          <span className={styles.rodovia}>{rota.rodovia || '--'}</span>
          <span className={styles.obsLabel}>OBSERVACAO</span>
        </div>

        <p className={styles.obsText}>
          {rota.descricao || 'Sem observacoes disponiveis para este trecho.'}
        </p>

        <span className={styles.flipHint}>
          <span className={styles.arrow}>&larr;</span> voltar
        </span>
      </div>
    </div>
  );
};

export default RotaCard;
