// Unit tests for components/common/modalStack.ts (UI_DESIGN.md §4.1, slice F4): the external store that tells the
// Toaster and the Announcer which open modal <dialog> is topmost. The module is import-free, so Node loads it as is;
// plain objects stand in for HTMLDialogElement (the store only keeps references).
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { getTopModal, registerModal, subscribeModalStack } from '../../../frontend/src/components/common/modalStack.ts'

const dialog = (name) => ({ name })

test('empty stack has no top modal', () => {
  assert.equal(getTopModal(), null)
})

test('the last registered dialog is on top; unregistering restores the previous one', () => {
  const settings = dialog('settings')
  const sheet = dialog('create-sheet')
  const popSettings = registerModal(settings)
  assert.equal(getTopModal(), settings)
  const popSheet = registerModal(sheet)
  assert.equal(getTopModal(), sheet)
  popSheet()
  assert.equal(getTopModal(), settings)
  popSettings()
  assert.equal(getTopModal(), null)
})

test('dialogs may close in any order', () => {
  const a = dialog('a')
  const b = dialog('b')
  const c = dialog('c')
  const popA = registerModal(a)
  const popB = registerModal(b)
  const popC = registerModal(c)
  popB()
  assert.equal(getTopModal(), c)
  popC()
  assert.equal(getTopModal(), a)
  popA()
  assert.equal(getTopModal(), null)
})

test('an unregister function works once (StrictMode re-runs effects)', () => {
  const a = dialog('a')
  const popFirst = registerModal(a)
  popFirst()
  const popSecond = registerModal(a)
  popFirst() // stale cleanup must not remove the new registration
  assert.equal(getTopModal(), a)
  popSecond()
  assert.equal(getTopModal(), null)
})

test('the snapshot is stable between changes (useSyncExternalStore contract)', () => {
  const a = dialog('a')
  const pop = registerModal(a)
  assert.equal(getTopModal(), getTopModal())
  pop()
})

test('subscribers are notified on every push and pop, and can unsubscribe', () => {
  let calls = 0
  const unsubscribe = subscribeModalStack(() => {
    calls += 1
  })
  const pop = registerModal(dialog('a'))
  assert.equal(calls, 1)
  pop()
  assert.equal(calls, 2)
  pop() // no-op: no notification
  assert.equal(calls, 2)
  unsubscribe()
  registerModal(dialog('b'))()
  assert.equal(calls, 2)
})

test('a listener may unsubscribe while being notified', () => {
  const seen = []
  const unsubscribeFirst = subscribeModalStack(() => {
    seen.push('first')
    unsubscribeFirst()
  })
  const unsubscribeSecond = subscribeModalStack(() => seen.push('second'))
  registerModal(dialog('a'))()
  assert.deepEqual(seen, ['first', 'second', 'second'])
  unsubscribeSecond()
})
