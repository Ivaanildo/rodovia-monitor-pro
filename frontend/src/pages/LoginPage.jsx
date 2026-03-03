import { useState, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { supabase } from '@/services/supabase';
import styles from './LoginPage.module.css';

const LogoIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" className={styles.logoSvg}>
    <path d="M12 2L2 7l10 5 10-5-10-5z" />
    <path d="M2 17l10 5 10-5" />
    <path d="M2 12l10 5 10-5" />
  </svg>
);

const LoginPage = () => {
  const navigate = useNavigate();
  const cardRef = useRef(null);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [erro, setErro] = useState('');
  const [hasError, setHasError] = useState(false);

  const shake = useCallback(() => {
    const card = cardRef.current;
    if (!card) return;
    card.classList.remove(styles.shake);
    void card.offsetWidth;
    card.classList.add(styles.shake);
    card.addEventListener('animationend', () => card.classList.remove(styles.shake), { once: true });
  }, []);

  const showError = useCallback((msg) => {
    setErro(msg);
    setHasError(true);
    shake();
  }, [shake]);

  const clearError = useCallback(() => {
    setErro('');
    setHasError(false);
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    clearError();

    if (!email.trim() || !password) {
      showError('Preencha todos os campos');
      return;
    }

    setLoading(true);
    try {
      const { error } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password,
      });
      if (error) {
        if (error.message.includes('Invalid login')) {
          showError('Email ou senha incorretos');
        } else if (error.status === 429) {
          showError('Muitas tentativas. Aguarde alguns minutos.');
        } else {
          showError(error.message);
        }
        return;
      }
      navigate('/', { replace: true });
    } catch (err) {
      showError(err.message || 'Erro ao fazer login');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.page}>
      <div className={`${styles.orb} ${styles.orb1}`} />
      <div className={`${styles.orb} ${styles.orb2}`} />
      <div className={`${styles.orb} ${styles.orb3}`} />

      <div ref={cardRef} className={styles.card}>
        <div className={styles.logoWrap}>
          <LogoIcon />
        </div>
        <h1 className={styles.title}>RodoviaMonitor Pro</h1>
        <p className={styles.subtitle}>Monitoramento de rodovias em tempo real</p>

        {erro && (
          <div className={styles.errorMsg}>{erro}</div>
        )}

        <form onSubmit={handleSubmit} autoComplete="on" noValidate>
          <div className={styles.formGroup}>
            <label className={styles.label} htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              className={`${styles.input} ${hasError ? styles.inputError : ''}`}
              value={email}
              onChange={(e) => { setEmail(e.target.value); clearError(); }}
              autoComplete="email"
              placeholder="Digite seu email"
              autoFocus
              required
            />
          </div>

          <div className={styles.formGroup}>
            <label className={styles.label} htmlFor="password">Senha</label>
            <input
              id="password"
              type="password"
              className={`${styles.input} ${hasError ? styles.inputError : ''}`}
              value={password}
              onChange={(e) => { setPassword(e.target.value); clearError(); }}
              autoComplete="current-password"
              placeholder="Digite sua senha"
              required
            />
          </div>

          <button
            type="submit"
            className={`${styles.btn} ${loading ? styles.btnLoading : ''}`}
            disabled={loading}
          >
            {loading ? (
              <span className={styles.spinner} />
            ) : (
              <span>Entrar</span>
            )}
          </button>
        </form>

        <div className={styles.footer}>
          Acesso restrito a usuarios autorizados
        </div>
      </div>
    </div>
  );
};

export default LoginPage;
