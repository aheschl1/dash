export default function CronCard({ data }) {
  return (
    <div className="card">
      <div className="card-title">
        Cron Jobs
        {data && data.length > 0 && <span className="cron-count">{data.length}</span>}
      </div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : data.length === 0 ? (
        <div className="empty">None found</div>
      ) : (
        <div className="cron-list">
          {data.map((job, i) => (
            <div className="cron-row" key={`${job.source}-${i}`}>
              <span className="cron-schedule">{job.schedule}</span>
              <span className="cron-user">{job.user || '—'}</span>
              <span className="cron-command">{job.command}</span>
              <span className="cron-source" title={job.source}>{job.source}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
