import type { LocalJob } from '../types'

const STORAGE_KEY = 'verifierforge.demo.jobs.v1'

export function readLocalJobs(): LocalJob[] {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    return value ? (JSON.parse(value) as LocalJob[]) : []
  } catch {
    return []
  }
}

export function saveLocalJob(job: LocalJob): void {
  const jobs = readLocalJobs().filter((existing) => existing.id !== job.id)
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify([job, ...jobs]))
}

export function findLocalJob(id: string): LocalJob | undefined {
  return readLocalJobs().find((job) => job.id === id)
}
