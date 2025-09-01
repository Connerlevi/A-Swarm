// Production WebSocket Hook with Resilience & Performance Fixes
// Addresses: stale closures, exponential backoff, pong timeout, proper cleanup

import { useEffect, useRef, useState, useCallback } from 'react'
import { SwarmEvent, ConnectionStatus } from '../types'

type Timeout = ReturnType<typeof setTimeout>

interface WebSocketConfig {
  url: string
  token?: string                 // short-lived auth
  reconnectBaseMs?: number       // base backoff
  maxReconnectAttempts?: number
  heartbeatIntervalMs?: number
  pingTimeoutMs?: number
  maxEvents?: number
  resumeFrom?: string | null     // optional initial resume token
}

interface WebSocketState {
  events: SwarmEvent[]
  connectionStatus: ConnectionStatus
  error: string | null
  eventCount: number
  latency: number | null
  reconnectAttempts: number
}

export function useWebSocketEvents(cfg: WebSocketConfig) {
  const {
    url,
    token,
    reconnectBaseMs = 750,
    maxReconnectAttempts = 10,
    heartbeatIntervalMs = 30_000,
    pingTimeoutMs = 5_000,
    maxEvents = 1000,
    resumeFrom = null,
  } = cfg

  const [state, setState] = useState<WebSocketState>({
    events: [],
    connectionStatus: 'disconnected',
    error: null,
    eventCount: 0,
    latency: null,
    reconnectAttempts: 0,
  })

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<Timeout | null>(null)
  const heartbeatTimeoutRef = useRef<Timeout | null>(null)
  const pongTimeoutRef = useRef<Timeout | null>(null)
  const lastPingRef = useRef<number>(0)
  const reconnectAttemptsRef = useRef<number>(0)
  const isMountedRef = useRef<boolean>(true)
  const shouldReconnectRef = useRef<boolean>(true)
  const lastEventIdRef = useRef<string | null>(resumeFrom)

  const clearTimer = (r: { current: Timeout | null }) => {
    if (r.current) clearTimeout(r.current as Timeout)
    r.current = null
  }

  const clearAllTimers = useCallback(() => {
    clearTimer(reconnectTimeoutRef)
    clearTimer(heartbeatTimeoutRef)
    clearTimer(pongTimeoutRef)
  }, [])

  const scheduleHeartbeat = useCallback(() => {
    clearTimer(heartbeatTimeoutRef)
    if (wsRef.current?.readyState !== WebSocket.OPEN) return
    lastPingRef.current = Date.now()
    wsRef.current!.send(JSON.stringify({ type: 'ping' }))

    // If no pong in pingTimeoutMs, force a reconnect
    clearTimer(pongTimeoutRef)
    pongTimeoutRef.current = setTimeout(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current?.close(4000, 'pong-timeout')
      }
    }, pingTimeoutMs)

    heartbeatTimeoutRef.current = setTimeout(scheduleHeartbeat, heartbeatIntervalMs)
  }, [heartbeatIntervalMs, pingTimeoutMs])

  const backoffDelay = (attempt: number) => {
    const exp = Math.min(attempt, 6) // cap exponent
    const base = reconnectBaseMs * Math.pow(2, exp)
    const jitter = Math.floor(Math.random() * 250)
    return Math.min(base + jitter, 30_000)
  }

  const connect = useCallback(() => {
    if (!isMountedRef.current) return
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return

    setState(prev => ({ ...prev, connectionStatus: 'connecting', error: null }))
    try {
      const u = new URL(url)
      if (token) u.searchParams.set('token', token)
      if (lastEventIdRef.current) u.searchParams.set('resume', lastEventIdRef.current)

      const ws = new WebSocket(u.toString())
      wsRef.current = ws

      ws.onopen = () => {
        if (!isMountedRef.current) return
        reconnectAttemptsRef.current = 0
        setState(prev => ({
          ...prev,
          connectionStatus: 'connected',
          error: null,
          reconnectAttempts: 0,
        }))
        clearAllTimers()
        // Optional hello; some servers expect it:
        ws.send(JSON.stringify({ type: 'hello', resume: lastEventIdRef.current ?? null }))
        scheduleHeartbeat()
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)

          if (data.type === 'pong') {
            clearTimer(pongTimeoutRef)
            setState(prev => ({ ...prev, latency: Date.now() - lastPingRef.current }))
            return
          }

          if (data.type === 'event' || data.type === 'elevation') {
            const e: SwarmEvent = {
              id: data.id || `evt-${Date.now()}-${Math.random()}`,
              ts: data.ts || Date.now(),
              ring: data.ring ?? 1,
              severity: data.severity ?? 'medium',
              source: data.source ?? 'unknown',
              summary: data.summary ?? data.message ?? 'Event received',
              details: data.details,
              node_name: data.node_name,
              score: data.score,
              witness_count: data.witness_count,
              containment_action: data.containment_action,
            }
            lastEventIdRef.current = e.id
            setState(prev => ({
              ...prev,
              events: [e, ...prev.events].slice(0, maxEvents),
              eventCount: prev.eventCount + 1,
            }))
            return
          }
        } catch (err) {
          console.error('WS parse error', err)
          setState(prev => ({ ...prev, error: `Parse error: ${String(err)}` }))
        }
      }

      ws.onerror = (err) => {
        console.error('WS error', err)
        if (!isMountedRef.current) return
        setState(prev => ({ ...prev, connectionStatus: 'error', error: 'Connection error' }))
      }

      ws.onclose = (evt) => {
        if (!isMountedRef.current) return
        clearAllTimers()
        wsRef.current = null

        setState(prev => ({ ...prev, connectionStatus: 'disconnected' }))

        if (!shouldReconnectRef.current) return
        const attempts = reconnectAttemptsRef.current + 1
        reconnectAttemptsRef.current = attempts

        if (evt.code === 1000) return // normal close
        if (attempts > maxReconnectAttempts) {
          setState(prev => ({ ...prev, error: 'Max reconnect attempts reached' }))
          return
        }
        const delay = backoffDelay(attempts)
        reconnectTimeoutRef.current = setTimeout(() => {
          if (isMountedRef.current) connect()
        }, delay)
        setState(prev => ({ ...prev, reconnectAttempts: attempts }))
      }
    } catch (err) {
      setState(prev => ({ ...prev, connectionStatus: 'error', error: `Failed to open WS: ${String(err)}` }))
    }
  }, [url, token, maxReconnectAttempts, reconnectBaseMs, scheduleHeartbeat, clearAllTimers])

  const sendMessage = useCallback((message: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message))
    } else {
      console.warn('WS not connected; drop message')
    }
  }, [])

  const clearEvents = useCallback(() => {
    setState(prev => ({ ...prev, events: [], eventCount: 0 }))
  }, [])

  const reconnect = useCallback(() => {
    if (wsRef.current) wsRef.current.close(1000, 'manual-reconnect')
    reconnectAttemptsRef.current = 0
    setState(prev => ({ ...prev, reconnectAttempts: 0 }))
    connect()
  }, [connect])

  useEffect(() => {
    isMountedRef.current = true
    shouldReconnectRef.current = true
    connect()
    return () => {
      shouldReconnectRef.current = false
      isMountedRef.current = false
      clearAllTimers()
      wsRef.current?.close(1000, 'unmount')
      wsRef.current = null
    }
  }, [connect, clearAllTimers])

  // Optional: reduce noise when tab hidden (pause heartbeat)
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === 'hidden') clearTimer(heartbeatTimeoutRef)
      else scheduleHeartbeat()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [scheduleHeartbeat])

  return {
    ...state,
    sendMessage,
    clearEvents,
    reconnect,
  }
}