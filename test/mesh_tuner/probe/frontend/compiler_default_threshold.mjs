// PROBE (test-only): does babel-plugin-react-compiler with DEFAULT options (what vite.config.ts's reactCompilerPreset()
// passes) throw on a bailout, or silently skip the function? Usage: node test/mesh_tuner/probe/frontend/compiler_default_threshold.mjs
import { createRequire } from 'node:module'
const require = createRequire('E:/14-OCT-2026/Img3D/frontend/package.json')
const babel = require('@babel/core')
const compiler = require.resolve('babel-plugin-react-compiler')
const code = `import { useState } from 'react'
export function C() { const [b, setB] = useState(false); const go = async () => { setB(true); try { await 1 } finally { setB(false) } }; return <button onClick={() => void go()}>{String(b)}</button> }`
try {
  const out = babel.transformSync(code, { filename: 'c.tsx', babelrc: false, configFile: false, parserOpts: { plugins: ['typescript', 'jsx'] }, plugins: [[compiler, {}]] })
  console.log('default options: no throw; memoized =', /react\/compiler-runtime|_c\(/.test(out.code))
} catch (e) { console.log('default options: THROWS ->', String(e.message).split('\n')[0]) }
