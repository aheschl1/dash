import { useState, useEffect, useCallback, useRef } from 'react'
import logo from './assets/logo.jpg'
import { isAuthed, currentUser, clearToken, authedFetch } from './auth'
import LoginModal from './components/LoginModal'
import SystemCard from './components/SystemCard'
import HardwareCard from './components/HardwareCard'
import VmsCard from './components/VmsCard'
import GpuCard from './components/GpuCard'
import TempsCard from './components/TempsCard'
import ContainersTable from './components/ContainersTable'
import PortsCard from './components/PortsCard'
import ConnectionsCard from './components/ConnectionsCard'
import WireguardCard from './components/WireguardCard'
import HistoryChart from './components/HistoryChart'
import LogsModal from './components/LogsModal'
import DiskCard from './components/DiskCard'
import ProcessesCard from './components/ProcessesCard'
import NetworkCard from './components/NetworkCard'
import AlertBanner from './components/AlertBanner'
import EventsFeed from './components/EventsFeed'
import SessionsCard from './components/SessionsCard'
import CronCard from './components/CronCard'
import DirectoryMenu from './components/DirectoryMenu'
import FeedbackModal from './components/FeedbackModal'
import FeedbackPanel from './components/FeedbackPanel'
import MemoriesCard from './components/MemoriesCard'
import JobsCard from './components/JobsCard'
import AddClientModal from './components/AddClientModal'
import AgentPanel from './components/AgentPanel'
import AskSelection from './components/AskSelection'

const POLL_MS = 5000
const NET_POLL_MS = 3000
const EVENTS_POLL_MS = 15000
const SLOW_POLL_MS = 30000

async function fetchJson(path) {
  try {
    const r = await fetch(path)
    if (!r.ok) return null
    return r.json()
  } catch {
    return null
  }
}

export default function App() {
  const [stats, setStats] = useState(null)
  const [hardware, setHardware] = useState(null)
  const [vms, setVms] = useState(null)
  const [gpu, setGpu] = useState(null)
  const [gpuProcs, setGpuProcs] = useState(null)
  const [temps, setTemps] = useState(null)
  const [containers, setContainers] = useState(null)
  const [hostPorts, setHostPorts] = useState(null)
  const [connections, setConnections] = useState(null)
  const [wireguard, setWireguard] = useState(null)
  const [history, setHistory] = useState(null)
  const [historyWindow, setHistoryWindow] = useState(1440)
  const [logsContainer, setLogsContainer] = useState(null)
  const [logsVm, setLogsVm] = useState(null)
  const [disk, setDisk] = useState(null)
  const [processes, setProcesses] = useState(null)
  const [network, setNetwork] = useState(null)
  const [smart, setSmart] = useState(null)
  const [alerts, setAlerts] = useState(null)
  const [events, setEvents] = useState(null)
  const [sessions, setSessions] = useState(null)
  const [cron, setCron] = useState(null)
  const [directory, setDirectory] = useState(null)
  const [directoryOpen, setDirectoryOpen] = useState(false)
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [addClientOpen, setAddClientOpen] = useState(false)
  const [rebootConfirm, setRebootConfirm] = useState(false)
  const [rebooting, setRebooting] = useState(false)
  const [loginOpen, setLoginOpen] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  const [askContext, setAskContext] = useState(null)
  const [authUser, setAuthUser] = useState(currentUser())
  const pendingAction = useRef(null)

  // Run a protected action; if unauthenticated (or token expired), prompt login
  // first and replay the action on success. This is the only thing that triggers
  // the login modal — initial load never does.
  const runProtected = useCallback((fn) => {
    if (isAuthed()) {
      fn()
    } else {
      pendingAction.current = fn
      setLoginOpen(true)
    }
  }, [])

  const onLoginSuccess = useCallback(() => {
    setLoginOpen(false)
    setAuthUser(currentUser())
    const fn = pendingAction.current
    pendingAction.current = null
    if (fn) fn()
  }, [])

  const logout = useCallback(() => {
    clearToken()
    setAuthUser(null)
  }, [])

  const containerAction = useCallback((name, action) => {
    runProtected(async () => {
      try {
        const r = await authedFetch(`/api/containers/${encodeURIComponent(name)}/action`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action }),
        })
        if (r.status === 401) {
          setAuthUser(null)
          pendingAction.current = () => containerAction(name, action)
          setLoginOpen(true)
          return
        }
        if (!r.ok) {
          const err = await r.json().catch(() => ({}))
          alert(`Action failed: ${err.error || r.statusText}`)
        }
      } catch (e) {
        alert(`Action failed: ${e.message}`)
      }
      poll()
    })
  }, [runProtected])

  const openAddClient = useCallback(() => {
    runProtected(() => setAddClientOpen(true))
  }, [runProtected])

  const rebootHost = useCallback(() => {
    runProtected(async () => {
      setRebooting(true)
      const r = await authedFetch('/api/host/reboot', { method: 'POST' })
      if (r.status === 401) {
        setRebooting(false)
        setAuthUser(null)
        pendingAction.current = rebootHost
        setLoginOpen(true)
      }
    })
  }, [runProtected])

  const poll = useCallback(async () => {
    const [s, hw, vm, g, gp, t, c, p, w, d, pr, sm, conn] = await Promise.all([
      fetchJson('/api/stats'),
      fetchJson('/api/hardware'),
      fetchJson('/api/vms'),
      fetchJson('/api/gpu'),
      fetchJson('/api/gpu/processes'),
      fetchJson('/api/temps'),
      fetchJson('/api/containers'),
      fetchJson('/api/ports'),
      fetchJson('/api/wireguard'),
      fetchJson('/api/disk'),
      fetchJson('/api/processes'),
      fetchJson('/api/smart'),
      fetchJson('/api/connections'),
    ])
    setStats(s)
    setHardware(hw)
    setVms(vm)
    setGpu(g)
    setGpuProcs(gp)
    setTemps(t)
    setContainers(c)
    setHostPorts(p)
    setWireguard(w)
    setConnections(conn)
    setDisk(d)
    setProcesses(pr)
    setSmart(sm)
  }, [])

  const pollHistory = useCallback(async () => {
    const h = await fetchJson(`/api/history?minutes=${historyWindow}`)
    setHistory(h)
  }, [historyWindow])

  const pollNetwork = useCallback(async () => {
    const n = await fetchJson('/api/network')
    setNetwork(n)
  }, [])

  const pollAlerts = useCallback(async () => {
    const a = await fetchJson('/api/alerts')
    setAlerts(a)
  }, [])

  const pollEvents = useCallback(async () => {
    const e = await fetchJson('/api/events')
    setEvents(e)
  }, [])

  const pollSessions = useCallback(async () => {
    const s = await fetchJson('/api/sessions')
    setSessions(s)
  }, [])

  const pollCron = useCallback(async () => {
    const c = await fetchJson('/api/cron')
    setCron(c)
  }, [])

  useEffect(() => {
    poll()
    pollNetwork()
    pollAlerts()
    pollEvents()
    pollSessions()
    pollCron()
    const fast = setInterval(poll, POLL_MS)
    const net = setInterval(pollNetwork, NET_POLL_MS)
    const alertsInterval = setInterval(pollAlerts, SLOW_POLL_MS)
    const eventsInterval = setInterval(pollEvents, EVENTS_POLL_MS)
    const sessionsInterval = setInterval(pollSessions, SLOW_POLL_MS)
    const cronInterval = setInterval(pollCron, SLOW_POLL_MS)
    return () => {
      clearInterval(fast)
      clearInterval(net)
      clearInterval(alertsInterval)
      clearInterval(eventsInterval)
      clearInterval(sessionsInterval)
      clearInterval(cronInterval)
    }
  }, [poll, pollNetwork, pollAlerts, pollEvents, pollSessions, pollCron])

  // Directory is a static CSV; fetch once on mount.
  useEffect(() => {
    fetchJson('/api/directory').then(setDirectory)
  }, [])

  // History has its own loop so changing the zoom window refetches immediately
  // without restarting the other pollers.
  useEffect(() => {
    pollHistory()
    const id = setInterval(pollHistory, 60_000)
    return () => clearInterval(id)
  }, [pollHistory])

  return (
    <div className={`app${agentOpen ? ' agent-open' : ' agent-collapsed'}`}>
      <header className="header">
        <div className="header-left">
          <button
            className="hamburger-btn"
            onClick={() => setDirectoryOpen(true)}
            title="Directory"
            aria-label="Open directory"
          >
            ☰
          </button>
          <img className="header-logo" src={logo} alt="Admin Dashboard logo" />
        </div>
        <div className="header-right">
          {stats && <span className="uptime">up {stats.uptime}</span>}
          {authUser && (
            <span className="auth-indicator">
              <span className="admin-badge">admin</span>
              {authUser}
              <button className="logout-btn" onClick={logout} title="Log out">logout</button>
            </span>
          )}
          <button className="feedback-header-btn" onClick={() => setFeedbackOpen(true)}>
            Feedback
          </button>
          <button
            className={`reboot-header-btn${rebootConfirm ? ' confirm' : ''}`}
            disabled={rebooting}
            onClick={() => {
              if (!rebootConfirm) {
                setRebootConfirm(true)
                setTimeout(() => setRebootConfirm(false), 4000)
                return
              }
              setRebootConfirm(false)
              rebootHost()
            }}
          >
            {rebooting ? 'Rebooting…' : rebootConfirm ? 'Click to confirm' : 'Restart'}
          </button>
        </div>
      </header>

      <AlertBanner alerts={alerts} />

      <main className="main">
        <SystemCard data={stats} />
        <HardwareCard data={hardware} />
        <GpuCard data={gpu} procs={gpuProcs} />
        <TempsCard data={temps} />
        <NetworkCard data={network} />
        <DiskCard data={disk} smart={smart} />
        <div className="full-width">
          <VmsCard data={vms} onViewVmLogs={(v) => setLogsVm({ name: v.name, kind: 'vm' })} />
        </div>
        <div className="full-width">
          <ContainersTable data={containers} onViewLogs={setLogsContainer} onContainerAction={containerAction} />
        </div>
        <div className="full-width">
          <ProcessesCard data={processes} />
        </div>
        <PortsCard data={hostPorts} />
        <WireguardCard data={wireguard} onAddClient={openAddClient} />
        <SessionsCard data={sessions} />
        <div className="full-width">
          <ConnectionsCard data={connections} />
        </div>
        <div className="full-width">
          <CronCard data={cron} />
        </div>
        <div className="full-width">
          <EventsFeed data={events} />
        </div>
        <div className="full-width">
          <HistoryChart data={history} window={historyWindow} onWindowChange={setHistoryWindow} />
        </div>
        <div className="full-width">
          <MemoriesCard runProtected={runProtected} />
        </div>
        <div className="full-width">
          <JobsCard />
        </div>
        <div className="full-width">
          <FeedbackPanel />
        </div>
      </main>

      <AgentPanel
        open={agentOpen}
        setOpen={setAgentOpen}
        runProtected={runProtected}
        pendingContext={askContext}
        clearPendingContext={() => setAskContext(null)}
      />

      <AskSelection onAsk={(text) => { setAskContext(text); setAgentOpen(true) }} />

      <DirectoryMenu data={directory} open={directoryOpen} onClose={() => setDirectoryOpen(false)} />

      {logsContainer && (
        <LogsModal container={logsContainer} onClose={() => setLogsContainer(null)} />
      )}
      {logsVm && (
        <LogsModal container={logsVm} onClose={() => setLogsVm(null)} />
      )}
      {feedbackOpen && (
        <FeedbackModal onClose={() => setFeedbackOpen(false)} />
      )}
      {addClientOpen && (
        <AddClientModal onClose={() => setAddClientOpen(false)} onCreated={poll} />
      )}
      {loginOpen && (
        <LoginModal
          onClose={() => { setLoginOpen(false); pendingAction.current = null }}
          onSuccess={onLoginSuccess}
        />
      )}
    </div>
  )
}
