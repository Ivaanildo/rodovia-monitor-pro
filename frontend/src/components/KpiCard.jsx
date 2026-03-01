import styles from './KpiCard.module.css';

const VARIANT_CLASSES = {
  total: styles.total,
  normal: styles.normal,
  moderado: styles.moderado,
  intenso: styles.intenso,
  parado: styles.parado,
  'sem-dados': styles.semDados,
};

/**
 * Card de KPI da sidebar — Total, Normal, Moderado, Intenso, Parado, Sem dados.
 *
 * @param {{ variant: string, label: string, value: number | string, total: number }} props
 */
const KpiCard = ({ variant = 'total', label, value, total = 0 }) => {
  const variantClass = VARIANT_CLASSES[variant] || '';
  const pct = total > 0 && variant !== 'total' && variant !== 'sem-dados'
    ? Math.round((value / total) * 100)
    : 0;
  const showBar = variant !== 'total' && variant !== 'sem-dados';
  const isPulsingParado = variant === 'parado' && Number(value) > 0;

  return (
    <div className={`${styles.card} ${variantClass}`}>
      <span className={styles.dot} />
      <span className={`${styles.value} ${isPulsingParado ? styles.pulsingCritical : ''}`}>
        {value ?? '--'}
      </span>
      <span className={styles.label}>{label}</span>
      {showBar && (
        <div className={styles.bar}>
          <div
            className={styles.barFill}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
};

export default KpiCard;
