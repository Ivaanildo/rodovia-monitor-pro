import { useState, useEffect } from 'react';
import styles from './Clock.module.css';

function getHHMM() {
  const now = new Date();
  const h = String(now.getHours()).padStart(2, '0');
  const m = String(now.getMinutes()).padStart(2, '0');
  return `${h}:${m}`;
}

/**
 * Relógio digital atualizado a cada 10 segundos.
 */
const Clock = () => {
  const [time, setTime] = useState(getHHMM());

  useEffect(() => {
    const id = setInterval(() => setTime(getHHMM()), 10_000);
    return () => clearInterval(id);
  }, []);

  return <span className={styles.clock}>{time}</span>;
};

export default Clock;
