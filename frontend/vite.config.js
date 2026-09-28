import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    // The interface had no tests while four of the project's shipped bugs lived in it:
    // a form that never sent the department, a form applying the wrong rules, a toast
    // function that refired an effect nine times, errors rendered as [object Object].
    environment: 'jsdom',
    include: ['tests/**/*.test.{js,jsx}'],
  },
})
