import Layout from '../components/Layout'
import { useAuth } from '../auth/useAuth'

export default function Settings() {
  const { logout, isAuthenticated } = useAuth()

  return (
    <Layout title="Settings" maxWidth="480px">
      {/* Athlete Profile */}
      <Section title="Athlete Profile" icon="🏊‍♂️">
        <Row label="Age" value="43" />
        <Row label="LTHR Run" value="153 bpm" />
        <Row label="LTHR Bike" value="153 bpm" />
        <Row label="FTP" value="233W" />
        <Row label="CSS" value="2:21/100m" />
      </Section>

      {/* Race Goal */}
      <Section title="Race Goal" icon="🏁">
        <Row label="Event" value="Ironman 70.3" />
        <Row label="Date" value="Sep 15, 2026" />
        <Row label="CTL Target" value="75" />
        <Row label="Swim CTL" value="15" />
        <Row label="Bike CTL" value="35" />
        <Row label="Run CTL" value="25" />
      </Section>

      {/* AI Workouts */}
      <Section title="AI Workouts" icon="🤖">
        <Row label="Auto-generate" value="Coming soon" />
        <Row label="Auto-push to Garmin" value="Coming soon" />
      </Section>

      {/* Auth */}
      {isAuthenticated && (
        <div className="mt-6">
          <button
            onClick={logout}
            className="w-full py-3 bg-surface border border-border rounded-xl text-sm font-semibold text-red cursor-pointer hover:bg-surface-2 transition-colors font-sans"
          >
            Выйти
          </button>
        </div>
      )}
    </Layout>
  )
}

function Section({ title, icon, children }: { title: string; icon: string; children: React.ReactNode }) {
  return (
    <div className="bg-surface rounded-2xl p-4 mb-3">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-lg">{icon}</span>
        <span className="text-sm font-bold">{title}</span>
      </div>
      {children}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-border last:border-b-0">
      <span className="text-[13px] text-text-dim">{label}</span>
      <span className="text-[13px] font-medium">{value}</span>
    </div>
  )
}
