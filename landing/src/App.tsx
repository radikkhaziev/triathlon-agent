import Gauge from './components/Gauge'
import StatusBadge from './components/StatusBadge'

export default function App() {
  return (
    <main className="min-h-screen bg-bg text-text flex items-center justify-center p-6">
      <div className="max-w-md w-full bg-surface border border-border rounded-2xl p-6 shadow-sm">
        <div className="text-xs uppercase tracking-wide text-text-dim mb-2">Scaffold preview</div>
        <div className="flex items-center gap-4">
          <Gauge value={78} size={96} />
          <div>
            <div className="text-lg font-bold">Good Recovery</div>
            <div className="text-sm text-text-dim">Zone 2 OK</div>
          </div>
        </div>
        <div className="mt-4 flex gap-2">
          <StatusBadge status="green" label="HRV 62.3 ms" />
          <StatusBadge status="green" label="RHR 52 bpm" />
        </div>
      </div>
    </main>
  )
}
