import Hero from './sections/Hero'
import LandingPreview from './sections/LandingPreview'
import TechStack from './sections/TechStack'
import Footer from './sections/Footer'

export default function App() {
  return (
    <div className="min-h-screen bg-bg text-text">
      <Hero />
      <LandingPreview />
      <TechStack />
      <Footer />
    </div>
  )
}
