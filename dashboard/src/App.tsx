import MissionControl from './components/MissionControl'
import { ToastProvider } from './components/ui/use-toast'

function App() {
  return (
    <ToastProvider>
      <MissionControl />
    </ToastProvider>
  )
}

export default App