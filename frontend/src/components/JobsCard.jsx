import { useEffect, useState } from 'react'

function fmtNext(iso) {
  if (!iso) return 'paused'
  const t = new Date(iso).getTime()
  const delta = Math.round((t - Date.now()) / 1000)
  if (delta <= 0) return 'due'
  if (delta < 60) return `in ${delta}s`
  if (delta < 3600) return `in ${Math.round(delta / 60)}m`
  return new Date(iso).toLocaleString()
}

export default function JobsCard() {
  const [jobs, setJobs] = useState(null)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const r = await fetch('/api/jobs')
        const d = await r.json()
        if (alive) setJobs(d.jobs || [])
      } catch {
        if (alive) setJobs([])
      }
    }
    load()
    const t = setInterval(load, 30000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  return (
    <div className="card">
      <div className="card-title">Scheduled Jobs</div>
      {!jobs ? (
        <div className="loading">Loading…</div>
      ) : jobs.length === 0 ? (
        <div className="empty">No scheduled jobs</div>
      ) : (
        <div className="jobs-list">
          {jobs.map((j) => (
            <div className="job-row" key={j.id}>
              <span className="job-id">{j.name || j.id}</span>
              <span className="job-trigger">{j.trigger}</span>
              <span className="job-next">{fmtNext(j.next_run_time)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
