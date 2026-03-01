import styles from './RealtimeIndicator.module.css';

const LABEL = {
  connecting: 'Conectando...',
  connected: 'Ao vivo',
};

/**
 * Indicador visual de estado da conexao WebSocket (Supabase Realtime).
 *
 * @param {{ status: 'connecting' | 'connected', ultimoCiclo?: string }} props
 */
const RealtimeIndicator = ({ status = 'connecting', ultimoCiclo }) => {
  const label = ultimoCiclo && status === 'connected'
    ? `Atualizado ${ultimoCiclo}`
    : LABEL[status] || 'Conectando...';

  return (
    <div className={styles.wrapper}>
      <span className={`${styles.dot} ${styles[status]}`} />
      <span className={styles.label}>{label}</span>
    </div>
  );
};

export default RealtimeIndicator;
