import { useState, useEffect, useCallback } from 'react';
import { supabase } from '@/services/supabase';

/**
 * Hook de autenticacao via Supabase Auth.
 * Verifica sessao existente e escuta mudancas de estado.
 *
 * @returns {{
 *   user: object | null,
 *   loading: boolean,
 *   authenticated: boolean,
 *   logout: () => Promise<void>
 * }}
 */
export function useAuth() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setUser(session?.user ?? null);
      setLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        setUser(session?.user ?? null);
      }
    );

    return () => {
      subscription.unsubscribe();
    };
  }, []);

  const logout = useCallback(async () => {
    await supabase.auth.signOut();
    window.location.href = '/login';
  }, []);

  return {
    user,
    loading,
    authenticated: user !== null,
    logout,
  };
}
