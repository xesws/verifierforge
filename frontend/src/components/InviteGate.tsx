import { type FormEvent, useState } from 'react'
import { BookOpenText, KeyRound, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { VerifierForgeClient, configuredApiBaseUrl } from '../api/client'
import { useAuth } from '../state/AuthContext'

export function InviteGate() {
  const { configurationError, setInvitation } = useAuth()
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const invitation = value.trim()
    if (!invitation || configurationError) return
    setChecking(true)
    setError(null)
    try {
      const client = new VerifierForgeClient({ baseUrl: configuredApiBaseUrl(), invitation: () => invitation })
      await client.listClusters()
      setInvitation(invitation)
      setValue('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Invitation could not be verified')
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="invite-screen">
      <form className="invite-card" onSubmit={submit}>
        <span className="brand-mark" aria-hidden="true"><i>V</i><i>F</i></span>
        <span className="eyebrow">Reviewer access</span>
        <h1>Enter your invitation.</h1>
        <p>The code is held only for this browser session and is never placed in the URL or build.</p>
        {configurationError ? <div className="inline-notice error">{configurationError}</div> : <label><span><KeyRound size={15} />Invitation code</span><input type="password" value={value} onChange={(event) => setValue(event.target.value)} autoComplete="one-time-code" required /></label>}
        {error && <div className="inline-notice error" role="alert">{error}</div>}
        <button className="primary-button" type="submit" disabled={checking || Boolean(configurationError)}><ShieldCheck size={16} />{checking ? 'Checking…' : 'Enter VerifierForge'}</button>
        <Link className="invite-tech-link" to="/tech"><BookOpenText size={15} />Read the technical deep dive — no invitation required</Link>
      </form>
    </div>
  )
}
