import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Diner from './pages/Diner'
import Kitchen from './pages/Kitchen'
import OrderStatus from './pages/OrderStatus'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/t/:token" element={<Diner />} />
        <Route path="/order/:publicId" element={<OrderStatus />} />
        <Route path="/kitchen" element={<Kitchen />} />
      </Routes>
    </BrowserRouter>
  )
}
