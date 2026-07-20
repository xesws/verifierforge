import { createHash } from 'node:crypto'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const frontendDirectory = path.resolve(scriptDirectory, '..')
const artifactDirectory = path.resolve(frontendDirectory, '../data/demo-artifacts')

const sources = {
  main: path.join(artifactDirectory, 'jobs/d4-m3-1p5b-r1-v0125/metrics.jsonl'),
  control: path.join(artifactDirectory, 'jobs/d4-m4-0p5b-random-v0126/metrics.jsonl'),
  heldout: path.join(artifactDirectory, 'jobs/d4-m3-1p5b-r1-v0125/heldout-report.json'),
  manifest: path.join(artifactDirectory, 'manifest.json'),
}

const requiredMetricKeys = ['step', 'reward_mean', 'pass_at_1', 'entropy', 'timestamp']

async function parseMetrics(sourcePath) {
  const text = await readFile(sourcePath, 'utf8')
  const rows = text
    .split(/\r?\n/u)
    .filter((line) => line.trim().length > 0)
    .map((line, index) => {
      const row = JSON.parse(line)
      for (const key of requiredMetricKeys) {
        if (!(key in row)) {
          throw new Error(`${path.basename(sourcePath)} row ${index + 1} is missing ${key}`)
        }
      }
      return Object.fromEntries(requiredMetricKeys.map((key) => [key, row[key]]))
    })

  return { rows, sha256: createHash('sha256').update(text).digest('hex') }
}

const [main, control, heldoutText, manifestText] = await Promise.all([
  parseMetrics(sources.main),
  parseMetrics(sources.control),
  readFile(sources.heldout, 'utf8'),
  readFile(sources.manifest, 'utf8'),
])

const heldout = JSON.parse(heldoutText)
const manifest = JSON.parse(manifestText)

if (main.rows.length !== 400 || control.rows.length !== 200) {
  throw new Error(`Unexpected metric counts: main=${main.rows.length}, control=${control.rows.length}`)
}
if (control.rows.at(-1)?.step !== 200 || control.rows.some((row) => row.step > 200)) {
  throw new Error('Control metrics must end naturally at step 200')
}
if (heldout.selected_checkpoint_step !== 350 || manifest.main_job !== 'd4-m3-1p5b-r1-v0125') {
  throw new Error('Held-out or manifest identity does not match the locked demo evidence')
}

const output = {
  schemaVersion: 1,
  source: {
    mainJob: manifest.main_job,
    controlJob: manifest.control_job,
    mainSha256: main.sha256,
    controlSha256: control.sha256,
    heldoutSha256: createHash('sha256').update(heldoutText).digest('hex'),
    manifestSha256: createHash('sha256').update(manifestText).digest('hex'),
  },
  evidence: {
    heldoutBefore: heldout.before,
    heldoutAfter: heldout.after,
    checkpoints: heldout.checkpoints.map(({ step, metrics }) => ({ step, ...metrics })),
    heldoutRows: heldout.frozen_identity.record_count,
    selectedCheckpointStep: heldout.selected_checkpoint_step,
    selectionRule: heldout.selection_rule,
    verdict: 'real_gain',
  },
  main: main.rows,
  control: control.rows,
}

const outputPath = path.join(frontendDirectory, 'src/data/generated/trainingMetrics.json')
await mkdir(path.dirname(outputPath), { recursive: true })
await writeFile(outputPath, `${JSON.stringify(output, null, 2)}\n`, 'utf8')
console.log(`Generated ${path.relative(frontendDirectory, outputPath)} (${main.rows.length} main, ${control.rows.length} control)`)
