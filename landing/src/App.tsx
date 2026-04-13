import Hero from './sections/Hero'
import LandingPreview from './sections/LandingPreview'
import Features from './sections/Features'
import HowItWorks from './sections/HowItWorks'
import MorningReportDeepDive from './sections/MorningReportDeepDive'
import OpenSource from './sections/OpenSource'
import Footer from './sections/Footer'

export default function App() {
  return (
    <div className="min-h-screen bg-bg text-text">
      <Hero />
      <LandingPreview />
      <Features />
      <HowItWorks />
      <MorningReportDeepDive />
      <OpenSource />
      <Footer />
    </div>
  )
}
