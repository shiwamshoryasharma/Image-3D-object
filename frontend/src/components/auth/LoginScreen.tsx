import { Lock, LogIn, User } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'

import { useAuthStore } from '../../store/authStore'
import { Button } from '../common/Button'
import { Logo } from '../layout/Logo'
import styles from './LoginScreen.module.css'

/** Sign-in form shown when `/auth/status` reports `auth_required` and the session is not authenticated. */
export function LoginScreen() {
  const login = useAuthStore((s) => s.login)
  const pending = useAuthStore((s) => s.loginPending)
  const error = useAuthStore((s) => s.loginError)
  const notice = useAuthStore((s) => s.notice)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const usernameId = useId()
  const passwordId = useId()
  const errorId = useId()

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!username.trim() || !password) return
    const ok = await login(username.trim(), password)
    if (!ok) setPassword('')
  }

  return (
    <main className={styles.screen}>
      <form className={styles.card} onSubmit={(event) => void submit(event)} aria-describedby={error ? errorId : undefined}>
        <div className={styles.brand}>
          <Logo size={44} />
          <div>
            <h1 className={styles.title}>Img3D</h1>
            <p className={styles.subtitle}>Sign in to continue</p>
          </div>
        </div>

        {notice && <p className={styles.notice}>{notice}</p>}

        <label className={styles.label} htmlFor={usernameId}>
          Username
        </label>
        <div className={styles.inputWrap}>
          <User size={16} aria-hidden className={styles.inputIcon} />
          <input
            id={usernameId}
            className={styles.input}
            name="username"
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            disabled={pending}
          />
        </div>

        <label className={styles.label} htmlFor={passwordId}>
          Password
        </label>
        <div className={styles.inputWrap}>
          <Lock size={16} aria-hidden className={styles.inputIcon} />
          <input
            id={passwordId}
            className={styles.input}
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={pending}
          />
        </div>

        {error && (
          <p id={errorId} className={styles.error} role="alert">
            {error}
          </p>
        )}

        <Button
          type="submit"
          variant="primary"
          size="lg"
          fullWidth
          icon={<LogIn size={16} />}
          loading={pending}
          disabled={!username.trim() || !password}
        >
          Sign in
        </Button>
        <p className={styles.footnote}>Credentials are configured on the backend (UI_USERNAME / UI_PASSWORD).</p>
      </form>
    </main>
  )
}
