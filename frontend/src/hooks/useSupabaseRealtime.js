import { useState, useEffect, useRef } from 'react';
import { supabase } from '@/services/supabase';

const MAX_FALHAS = 3;

/**
 * Hook que escuta Realtime do Supabase para novos snapshots de rotas.
 * Após MAX_FALHAS tentativas sem sucesso, desiste e retorna status 'polling'.
 *
 * @param {(payload: object) => void} onInsert - Callback chamado a cada INSERT em snapshots_rotas
 * @returns {{ status: 'connecting' | 'connected' | 'polling' }}
 */
export function useSupabaseRealtime(onInsert) {
  const [status, setStatus] = useState('connecting');
  const onInsertRef = useRef(onInsert);
  const falhasRef = useRef(0);
  const desistidoRef = useRef(false);

  useEffect(() => {
    onInsertRef.current = onInsert;
  }, [onInsert]);

  useEffect(() => {
    let channel = null;

    function conectar() {
      if (desistidoRef.current) return;

      channel = supabase
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
        .subscribe((st) => {
          if (st === 'SUBSCRIBED') {
            falhasRef.current = 0;
            setStatus('connected');
          } else if (st === 'CHANNEL_ERROR' || st === 'TIMED_OUT' || st === 'CLOSED') {
            falhasRef.current += 1;
            if (falhasRef.current >= MAX_FALHAS) {
              desistidoRef.current = true;
              supabase.removeChannel(channel);
              setStatus('polling');
            } else {
              setStatus('connecting');
            }
          }
        });
    }

    conectar();

    return () => {
      desistidoRef.current = true;
      if (channel) supabase.removeChannel(channel);
    };
  }, []);

  return { status };
}
