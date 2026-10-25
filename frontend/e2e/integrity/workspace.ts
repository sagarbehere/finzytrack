// The demo workspace the acceptance harness opens for each run (review:27 §1(d): isolated backend on its own port and
// data dir, a fresh demo, a frozen clock, the LLM stub; roadmap §1 "Left in M0" item 4). Until the harness exists
// the integrity flows stay pending; its first slice replaces this constant with the harness fixture.
export const WS = 'demo'
