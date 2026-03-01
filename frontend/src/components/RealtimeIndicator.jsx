import styles from './RealtimeIndicator.module.css';

const LABEL = {
  connecting: 'Conectando...',
  connected: 'Ao vivo',
  polling: 'Polling 60s',
};

/**
 * Indicador visual de estado da conexao WebSocket (Supabase Realtime).
 *
 * @param {{ status: 'connecting' | 'connected' | 'polling', ultimoCiclo?: string }} props
 */
const RealtimeIndicator = ({ status = 'connecting', ultimoCiclo }) => {
  const label = ultimoCiclo && status === 'connected'
    ? `Atualizado ${ultimoCiclo}`
    : ultimoCiclo && status === 'polling'
    ? `Polling ${ultimoCiclo}`
    : LABEL[status] || 'Conectando...';

  return (
    <div className={styles.wrapper}>
      <span className={`${styles.dot} ${styles[status]}`} />
      <span className={styles.label}>{label}</span>
    </div>
  );
};

export default RealtimeIndicator;
