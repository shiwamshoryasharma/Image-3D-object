// Runs babel-plugin-react-compiler over frontend/src and reports every component/hook it could not compile.
// Usage: node test/frontend/review/compiler_check.mjs
import { createRequire } from 'node:module'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const FRONTEND = 'E:/14-OCT-2026/Img3D/frontend'
const require = createRequire(`${FRONTEND}/package.json`)
const babel = require('@babel/core')
const compiler = require.resolve('babel-plugin-react-compiler')

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return walk(path)
    return /\.(tsx?|jsx?)$/.test(name) && !name.endsWith('.d.ts') ? [path] : []
  })
}

const events = []
let successes = 0
for (const file of walk(`${FRONTEND}/src`)) {
  const code = readFileSync(file, 'utf8')
  babel.transformSync(code, {
    filename: file,
    babelrc: false,
    configFile: false,
    parserOpts: { plugins: ['typescript', 'jsx'] },
    plugins: [
      [
        compiler,
        {
          panicThreshold: 'none',
          logger: {
            logEvent(filename, event) {
              if (event.kind === 'CompileSuccess') { successes += 1; return }
              events.push({ file: filename.replace(FRONTEND + '/', ''), kind: event.kind, fn: event.fnName ?? null,
                reason: event.detail?.reason ?? event.detail?.options?.reason ?? event.reason ?? String(event.detail ?? event.data ?? ''),
                loc: event.fnLoc?.start?.line ?? event.detail?.loc?.start?.line ?? null })
            },
          },
        },
      ],
    ],
  })
}
const relevant = events.filter((e) => e.kind !== 'CompileSkip' || e.reason)
console.log(JSON.stringify(relevant, null, 1))
console.log(`compiled functions: ${successes}, non-success events: ${relevant.length}`)
