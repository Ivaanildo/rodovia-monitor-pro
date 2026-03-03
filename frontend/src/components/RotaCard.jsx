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
 * Card individual de rota com status colorido, flash animation ao atualizar,
 * e flip 3D in-place para exibir a descrição/observação no verso.
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

  const handleClick = () => {
    const next = !flipped;
    setFlipped(next);
    onFlip?.(next);
  };

  const status = rota.status || 'Sem dados';
  const atraso = rota.atraso_min > 0 ? `+${Math.round(rota.atraso_min)} min` : '';
  const conf = rota.confianca_pct != null ? `${rota.confianca_pct}%` : '';

  return (
    <div
      className={styles.cardWrapper}
      style={{ animationDelay: `${animDelay}s`, zIndex: flipped ? 10 : 1 }}
      onClick={handleClick}
      title={flipped ? 'Clique para voltar' : 'Clique para ver observacoes'}
    >
      <div className={`${styles.cardInner} ${flipped ? styles.flipped : ''}`}>

        {/* FACE FRENTE */}
        <div
          ref={cardRef}
          className={`${styles.card} ${styles.cardFront} ${STATUS_CLASS[status] || ''}`}
          data-status={status}
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

          <span className={styles.flipHint}>ver obs. &rarr;</span>
        </div>

        {/* FACE VERSO */}
        <div className={`${styles.card} ${styles.cardBack} ${STATUS_CLASS[status] || ''}`}>
          <div className={styles.backHeader}>
            <span className={styles.rodovia}>{rota.rodovia || '--'}</span>
            <span className={styles.backLabel}>Observacao</span>
          </div>
          <p className={styles.backDescricao}>
            {rota.descricao || 'Sem observacoes disponiveis para este trecho.'}
          </p>
          <span className={styles.flipHint}>&larr; voltar</span>
        </div>

      </div>
    </div>
  );
};

export default RotaCard;
