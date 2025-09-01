import React, { useState, useCallback, useMemo } from 'react'
import { FixedSizeList as List } from 'react-window'
import { useWebSocketEvents } from '../hooks/useWebSocketEvents'
import { SwarmEvent, Ring, EventSeverity } from '../types'

// Stable severity class map to avoid recreation per row
const severityClass: Record<string, string> = {
  low: 'text-blue-400 bg-blue-500/20',
  medium: 'text-yellow-400 bg-yellow-500/20',
  high: 'text-orange-400 bg-orange-500/20',
  critical: 'text-red-400 bg-red-500/20',
}

// Production WebSocket URL with fallback
const WS_URL = import.meta.env.VITE_ASWARM_WS_URL ?? (location.protocol === 'https:' ? `wss://${location.host}/ws/events` : 'ws://localhost:8000/ws/events')

interface MetricCardProps {
  title: string
  value: string | number
  unit?: string
  status: 'good' | 'warn' | 'critical'
  target?: string
}

const MetricCard: React.FC<MetricCardProps> = ({ title, value, unit, status, target }) => {
  const statusColors = {
    good: 'text-green-400 border-green-500/30 bg-green-500/10',
    warn: 'text-yellow-400 border-yellow-500/30 bg-yellow-500/10',
    critical: 'text-red-400 border-red-500/30 bg-red-500/10'
  }

  return (
    <div className={`border rounded-lg p-4 ${statusColors[status]}`}>
      <div className="text-sm font-medium opacity-75">{title}</div>
      <div className="text-2xl font-bold font-mono tabular-nums">
        {value}{unit && <span className="text-sm opacity-75 ml-1">{unit}</span>}
      </div>
      {target && <div className="text-xs opacity-60">SLO: {target}</div>}
    </div>
  )
}

interface EventRowProps {
  index: number
  style: React.CSSProperties
  data: SwarmEvent[]
}

const EventRow: React.FC<EventRowProps> = React.memo(({ index, style, data }) => {
  const event = data[index]
  if (!event) return null

  // Defensive clamping for ring value
  const ringSafe = Math.max(1, Math.min(5, Number(event.ring) || 1))
  const ringColor = `hsl(${(ringSafe - 1) * 60}, 70%, 60%)`
  const sev = severityClass[event.severity] ?? severityClass.low

  return (
    <div style={style} className="px-4 py-2 border-b border-gray-700/50 hover:bg-gray-800/50">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div 
            className="w-2 h-2 rounded-full" 
            style={{ backgroundColor: ringColor }}
          />
          <span className={`px-2 py-1 rounded text-xs font-medium ${sev}`}>
            {String(event.severity).toUpperCase()}
          </span>
          <span className="text-gray-300 font-mono text-sm" title={new Date(event.ts).toISOString()}>
            {new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(event.ts))}
          </span>
        </div>
        <div className="text-right">
          <div className="text-sm text-gray-300">{event.source}</div>
          {event.node_name && (
            <div className="text-xs text-gray-500">{event.node_name}</div>
          )}
        </div>
      </div>
      <div className="mt-1 text-gray-200 line-clamp-2">{event.summary}</div>
      {event.details && (
        <div className="mt-1 text-sm text-gray-400 line-clamp-1">{event.details}</div>
      )}
      <div className="flex items-center mt-2 space-x-4 text-xs text-gray-500">
        <span>Ring {ringSafe}</span>
        {event.score !== undefined && event.score !== null && <span>Score: {event.score}</span>}
        {event.witness_count !== undefined && event.witness_count !== null && <span>Witnesses: {event.witness_count}</span>}
        {event.containment_action && <span>Action: {event.containment_action}</span>}
      </div>
    </div>
  )
})

interface RingControlProps {
  ring: Ring
  active: boolean
  onToggle: (ring: Ring) => void
  onEscalate: (ring: Ring) => void
}

const RingControl: React.FC<RingControlProps> = ({ ring, active, onToggle, onEscalate }) => {
  const ringNames = {
    1: 'Detection',
    2: 'Analysis', 
    3: 'Containment',
    4: 'Isolation',
    5: 'Elimination'
  }

  const ringColor = `hsl(${(ring - 1) * 60}, 70%, 60%)`

  return (
    <div className="border border-gray-600 rounded-lg p-3 bg-gray-800/50">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center space-x-2">
          <div 
            className="w-3 h-3 rounded-full" 
            style={{ backgroundColor: ringColor }}
          />
          <span className="font-medium">Ring {ring}</span>
        </div>
        <button
          onClick={() => onToggle(ring)}
          className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
            active 
              ? 'bg-green-500/20 text-green-400 border border-green-500/30' 
              : 'bg-gray-600/50 text-gray-400 border border-gray-600'
          }`}
        >
          {active ? 'ACTIVE' : 'INACTIVE'}
        </button>
      </div>
      <div className="text-sm text-gray-300 mb-2">{ringNames[ring]}</div>
      <button
        onClick={() => onEscalate(ring)}
        disabled={ring >= 5}
        className="w-full px-3 py-1 bg-orange-500/20 text-orange-400 border border-orange-500/30 rounded text-sm font-medium hover:bg-orange-500/30 transition-colors disabled:opacity-50"
      >
        Escalate
      </button>
    </div>
  )
}

const ASwarmDashboard: React.FC = () => {
  const [activeRings, setActiveRings] = useState<Set<Ring>>(new Set([1, 2, 3]))
  const [killSwitchArmed, setKillSwitchArmed] = useState(false)
  const [simulationRunning, setSimulationRunning] = useState(false)
  const [filter, setFilter] = useState('')
  const [showKillModal, setShowKillModal] = useState(false)
  const [reason, setReason] = useState('')
  const [okSec, setOkSec] = useState(false)
  const [okOps, setOkOps] = useState(false)

  const { 
    events, 
    connectionStatus, 
    error, 
    eventCount, 
    latency,
    reconnectAttempts,
    sendMessage, 
    clearEvents, 
    reconnect 
  } = useWebSocketEvents({
    url: WS_URL,
    reconnectBaseMs: 1000,
    maxReconnectAttempts: 10,
    heartbeatIntervalMs: 30000,
    maxEvents: 1000
  })

  const connectionIndicator = useMemo(() => {
    const colors = {
      connecting: 'text-yellow-400 animate-pulse',
      connected: 'text-green-400',
      disconnected: 'text-gray-400',
      error: 'text-red-400'
    }
    return colors[connectionStatus] || 'text-gray-400'
  }, [connectionStatus])

  const systemMetrics = useMemo(() => ({
    mttd_p95_ms: 0.08,
    mttr_p95_s: 2.1,
    cpu_percent: 12,
    memory_mb: 245,
    packet_rate: 15420,
    error_rate: 0.02,
    queue_depth: 3
  }), [])

  // Filtered events for search
  const filteredEvents = useMemo(() => {
    if (!filter.trim()) return events
    const f = filter.toLowerCase()
    return events.filter(e =>
      e.source?.toLowerCase().includes(f) ||
      e.summary?.toLowerCase().includes(f) ||
      e.details?.toLowerCase().includes(f)
    )
  }, [events, filter])

  // Compute event counts once
  const { criticalCount, highPlusCount } = useMemo(() => {
    let c = 0, h = 0
    for (const e of events) {
      if (e.severity === 'critical') c++
      if (e.severity === 'high' || e.severity === 'critical') h++
    }
    return { criticalCount: c, highPlusCount: h }
  }, [events])

  const handleRingToggle = useCallback((ring: Ring) => {
    setActiveRings(prev => {
      const newSet = new Set(prev)
      if (newSet.has(ring)) {
        newSet.delete(ring)
      } else {
        newSet.add(ring)
      }
      return newSet
    })
    sendMessage({ type: 'ring_control', ring, active: !activeRings.has(ring) })
  }, [activeRings, sendMessage])

  const handleRingEscalate = useCallback((ring: Ring) => {
    if (ring >= 5) return
    const payload = { type: 'escalate', from_ring: ring, to_ring: (ring + 1) as Ring }
    sendMessage(payload)
  }, [sendMessage])

  const handleKillSwitch = useCallback(() => {
    sendMessage({ type: 'kill_switch', action: 'execute', reason })
    setShowKillModal(false)
    setKillSwitchArmed(false)
    setReason('')
    setOkSec(false)
    setOkOps(false)
  }, [reason, sendMessage])

  const handleAttackSimulation = useCallback(() => {
    if (simulationRunning) {
      sendMessage({ type: 'attack_sim', action: 'stop' })
    } else {
      sendMessage({ type: 'attack_sim', action: 'start', scenario: 'lateral_movement' })
    }
    setSimulationRunning(!simulationRunning)
  }, [simulationRunning, sendMessage])

  // CSV export function
  const exportCsv = useCallback(() => {
    const rows = filteredEvents.map(e => ({
      id: e.id, ts: new Date(e.ts).toISOString(), ring: e.ring, severity: e.severity,
      source: e.source, summary: e.summary, details: e.details ?? ''
    }))
    const csv = [
      'id,ts,ring,severity,source,summary,details',
      ...rows.map(r => Object.values(r).map(v => `"${String(v).replace(/"/g,'""')}"`).join(','))
    ].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = 'events.csv'; a.click()
    URL.revokeObjectURL(url)
  }, [filteredEvents])

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <div className="container mx-auto p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-3xl font-bold bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">
              A-SWARM Command Center
            </h1>
            <p className="text-gray-400">Autonomous Swarm Cybersecurity Defense Platform</p>
          </div>
          <div className="flex items-center space-x-4">
            <div className="flex items-center space-x-2">
              <div className={`w-2 h-2 rounded-full ${connectionIndicator}`} />
              <span className="text-sm">
                {connectionStatus.charAt(0).toUpperCase() + connectionStatus.slice(1)}
                {reconnectAttempts > 0 && ` (${reconnectAttempts} retries)`}
                {latency && ` • ${latency}ms`}
              </span>
            </div>
            {error && (
              <div className="text-red-400 text-sm bg-red-500/10 px-2 py-1 rounded border border-red-500/30">
                {error}
              </div>
            )}
            <button
              onClick={reconnect}
              className="px-3 py-1 bg-blue-500/20 text-blue-400 border border-blue-500/30 rounded text-sm hover:bg-blue-500/30 transition-colors"
              aria-label="Reconnect to WebSocket"
            >
              Reconnect
            </button>
          </div>
        </div>

        {/* System Metrics */}
        <div className="grid grid-cols-1 md:grid-cols-4 lg:grid-cols-7 gap-4 mb-6">
          <MetricCard
            title="MTTD P95"
            value={systemMetrics.mttd_p95_ms.toFixed(2)}
            unit="ms"
            status={systemMetrics.mttd_p95_ms < 1 ? 'good' : 'warn'}
            target="< 1ms"
          />
          <MetricCard
            title="MTTR P95"
            value={systemMetrics.mttr_p95_s.toFixed(1)}
            unit="s"
            status={systemMetrics.mttr_p95_s < 5 ? 'good' : 'warn'}
            target="< 5s"
          />
          <MetricCard
            title="CPU Usage"
            value={systemMetrics.cpu_percent}
            unit="%"
            status={systemMetrics.cpu_percent < 50 ? 'good' : systemMetrics.cpu_percent < 80 ? 'warn' : 'critical'}
          />
          <MetricCard
            title="Memory"
            value={systemMetrics.memory_mb}
            unit="MB"
            status={systemMetrics.memory_mb < 500 ? 'good' : 'warn'}
          />
          <MetricCard
            title="Packet Rate"
            value={(systemMetrics.packet_rate / 1000).toFixed(1)}
            unit="K/s"
            status="good"
          />
          <MetricCard
            title="Error Rate"
            value={systemMetrics.error_rate}
            unit="%"
            status={systemMetrics.error_rate < 0.1 ? 'good' : 'warn'}
          />
          <MetricCard
            title="Queue Depth"
            value={systemMetrics.queue_depth}
            status={systemMetrics.queue_depth < 10 ? 'good' : 'warn'}
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Event Timeline */}
          <div className="lg:col-span-2">
            <div className="bg-gray-800/50 border border-gray-700 rounded-lg">
              <div className="p-4 border-b border-gray-700">
                <div className="flex items-center justify-between">
                  <h2 className="text-xl font-semibold">Event Timeline</h2>
                  <div className="flex items-center space-x-4">
                    <input 
                      value={filter} 
                      onChange={e => setFilter(e.target.value)} 
                      placeholder="Filter source/text…" 
                      className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm"
                      aria-label="Filter events"
                    />
                    <span className="text-sm text-gray-400" aria-live="polite">
                      {eventCount} events • {criticalCount} critical • {highPlusCount} high+
                    </span>
                    <button
                      onClick={exportCsv}
                      className="px-3 py-1 bg-blue-500/20 text-blue-400 border border-blue-500/30 rounded text-sm hover:bg-blue-500/30 transition-colors"
                      aria-label="Export events to CSV"
                    >
                      Export
                    </button>
                    <button
                      onClick={clearEvents}
                      className="px-3 py-1 bg-gray-600/50 text-gray-300 border border-gray-600 rounded text-sm hover:bg-gray-600/70 transition-colors"
                      aria-label="Clear all events"
                    >
                      Clear
                    </button>
                  </div>
                </div>
              </div>
              <div className="h-96">
                {filteredEvents.length > 0 ? (
                  <List
                    height={384}
                    itemCount={filteredEvents.length}
                    itemSize={120}
                    itemData={filteredEvents}
                    itemKey={(index, data) => data[index]?.id ?? index}
                    overscanCount={10}
                  >
                    {EventRow}
                  </List>
                ) : (
                  <div className="flex items-center justify-center h-full text-gray-500">
                    {filter ? 'No matching events' : 'No events detected'}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Controls Panel */}
          <div className="space-y-6">
            {/* Ring Controls */}
            <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-4">
              <h3 className="text-lg font-semibold mb-4">Ring Controls</h3>
              <div className="space-y-3">
                {([1, 2, 3, 4, 5] as Ring[]).map(ring => (
                  <RingControl
                    key={ring}
                    ring={ring}
                    active={activeRings.has(ring)}
                    onToggle={handleRingToggle}
                    onEscalate={handleRingEscalate}
                  />
                ))}
              </div>
            </div>

            {/* Emergency Controls */}
            <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-4">
              <h3 className="text-lg font-semibold mb-4 text-red-400">Emergency Controls</h3>
              
              {/* Kill Switch */}
              <div className="mb-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium">Network Kill Switch</span>
                  <button
                    onClick={() => setKillSwitchArmed(!killSwitchArmed)}
                    className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                      killSwitchArmed 
                        ? 'bg-red-500/20 text-red-400 border border-red-500/30' 
                        : 'bg-gray-600/50 text-gray-400 border border-gray-600'
                    }`}
                  >
                    {killSwitchArmed ? 'ARMED' : 'SAFE'}
                  </button>
                </div>
                <button
                  onClick={() => setShowKillModal(true)}
                  disabled={!killSwitchArmed}
                  className={`w-full px-4 py-2 rounded font-medium transition-colors ${
                    killSwitchArmed
                      ? 'bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30'
                      : 'bg-gray-700/50 text-gray-500 border border-gray-700 cursor-not-allowed'
                  }`}
                  aria-label="Execute network kill switch"
                >
                  EXECUTE KILL SWITCH
                </button>
              </div>

              {/* Attack Simulation */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium">Attack Simulation</span>
                  <div className={`px-2 py-1 rounded text-xs font-medium ${
                    simulationRunning 
                      ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30' 
                      : 'bg-gray-600/50 text-gray-400 border border-gray-600'
                  }`}>
                    {simulationRunning ? 'RUNNING' : 'IDLE'}
                  </div>
                </div>
                <button
                  onClick={handleAttackSimulation}
                  className={`w-full px-4 py-2 rounded font-medium transition-colors ${
                    simulationRunning
                      ? 'bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30'
                      : 'bg-orange-500/20 text-orange-400 border border-orange-500/30 hover:bg-orange-500/30'
                  }`}
                >
                  {simulationRunning ? 'STOP SIMULATION' : 'START SIMULATION'}
                </button>
              </div>
            </div>

            {/* System Status */}
            <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-4">
              <h3 className="text-lg font-semibold mb-4">System Status</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-gray-400">Active Nodes</span>
                  <span className="text-green-400 font-mono tabular-nums">12/12</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Policy Violations</span>
                  <span className="text-yellow-400 font-mono tabular-nums">{highPlusCount}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Critical Events</span>
                  <span className="text-red-400 font-mono tabular-nums">{criticalCount}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Fast-path Latency</span>
                  <span className="text-blue-400 font-mono tabular-nums">{systemMetrics.mttd_p95_ms.toFixed(2)}ms</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Network Health</span>
                  <span className="text-green-400">Optimal</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Kill Switch Modal */}
        {showKillModal && (
          <div role="dialog" aria-modal className="fixed inset-0 grid place-items-center bg-black/40">
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 w-full max-w-md">
              <h4 className="font-semibold mb-2">Confirm Kill Switch</h4>
              <textarea 
                value={reason} 
                onChange={e => setReason(e.target.value)} 
                placeholder="Reason (min 10 chars)" 
                className="w-full bg-gray-800 border border-gray-700 rounded p-2 text-sm"
                rows={3}
              />
              <div className="flex items-center justify-between mt-3 text-sm">
                <label className="flex items-center gap-2">
                  <input 
                    type="checkbox" 
                    checked={okSec} 
                    onChange={e => setOkSec(e.target.checked)} 
                  /> 
                  Security approval
                </label>
                <label className="flex items-center gap-2">
                  <input 
                    type="checkbox" 
                    checked={okOps} 
                    onChange={e => setOkOps(e.target.checked)} 
                  /> 
                  Operations approval
                </label>
              </div>
              <div className="flex justify-end gap-2 mt-3">
                <button 
                  onClick={() => setShowKillModal(false)} 
                  className="px-3 py-1 border border-gray-700 rounded hover:bg-gray-800 transition-colors"
                >
                  Cancel
                </button>
                <button
                  disabled={reason.trim().length < 10 || !okSec || !okOps}
                  onClick={handleKillSwitch}
                  className="px-3 py-1 bg-red-500/20 text-red-400 border border-red-500/30 rounded disabled:opacity-50 hover:bg-red-500/30 transition-colors"
                >
                  Execute
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default ASwarmDashboard