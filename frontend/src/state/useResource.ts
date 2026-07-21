import { type DependencyList, useCallback, useEffect, useRef, useState } from 'react'

export type ResourceStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

export interface Resource<T> {
  status: ResourceStatus
  data: T | null
  error: string | null
  reload: () => void
}

interface ResourceOptions<T> {
  enabled?: boolean
  empty?: (value: T) => boolean
  pollMs?: number | ((value: T) => number | null)
}

export function useResource<T>(
  loader: () => Promise<T>,
  dependencies: DependencyList,
  options: ResourceOptions<T> = {},
): Resource<T> {
  const { enabled = true, empty, pollMs } = options
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState<Omit<Resource<T>, 'reload'>>({ status: 'idle', data: null, error: null })
  const mounted = useRef(true)
  const reload = useCallback(() => setRevision((value) => value + 1), [])

  useEffect(() => () => { mounted.current = false }, [])
  useEffect(() => {
    if (!enabled) {
      setState({ status: 'idle', data: null, error: null })
      return
    }
    let cancelled = false
    let timer: number | undefined
    const load = async () => {
      setState((current) => ({ status: current.data === null ? 'loading' : current.status, data: current.data, error: null }))
      try {
        const data = await loader()
        if (cancelled || !mounted.current) return
        setState({ status: empty?.(data) ? 'empty' : 'ready', data, error: null })
        const interval = typeof pollMs === 'function' ? pollMs(data) : pollMs
        if (interval && !document.hidden) timer = window.setTimeout(load, interval)
      } catch (error) {
        if (!cancelled && mounted.current) {
          setState((current) => ({ status: 'error', data: current.data, error: error instanceof Error ? error.message : 'Request failed' }))
        }
      }
    }
    void load()
    return () => { cancelled = true; if (timer) window.clearTimeout(timer) }
    // dependencies are supplied by callers to control loader identity deliberately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, enabled, revision])

  return { ...state, reload }
}
