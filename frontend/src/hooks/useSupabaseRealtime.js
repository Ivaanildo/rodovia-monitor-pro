import { useState, useEffect, useRef } from 'react';
import { supabase } from '@/services/supabase';

/**
 * Hook que escuta Realtime do Supabase para novos snapshots de rotas.
 * Substitui o antigo useSse.
 *
 * @param {(payload: object) => void} onInsert - Callback chamado a cada INSERT em snapshots_rotas
 * @returns {{ status: 'connecting' | 'connected' }}
 */
export function useSupabaseRealtime(onInsert) {
  const [status, setStatus] = useState('connecting');
  const onInsertRef = useRef(onInsert);

  useEffect(() => {
    onInsertRef.current = onInsert;
  }, [onInsert]);

  useEffect(() => {
    const channel = supabase
      .channel('snapshots-realtime')
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'snapshots_rotas' },
        (payload) => {
          if (onInsertRef.current) {
            onInsertRef.current(payload.new);
          }
        }
      )
      .subscribe((status) => {
        if (status === 'SUBSCRIBED') {
          setStatus('connected');
        } else {
          setStatus('connecting');
        }
      });

    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  return { status };
}
