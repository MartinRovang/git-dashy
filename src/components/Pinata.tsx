import { useEffect, useRef, useState } from 'react'

const HITS = 200
// The loot: Tolkien and the gothics. A big pool on purpose — one round finds maybe a third of it,
// so the next round still turns up lines you have not read.
const QUOTES = [
  'Fly, you fools!',
  'You shall not pass!',
  'Not all those who wander are lost.',
  'The road goes ever on and on.',
  'All we have to decide is what to do with the time that is given us.',
  'Even the smallest person can change the course of the future.',
  'Speak, friend, and enter.',
  'My precious…',
  'A wizard is never late, nor is he early.',
  'I will take it, though I do not know the way.',
  'In a hole in the ground there lived a hobbit.',
  'Faithless is he that says farewell when the road darkens.',
  "It's a dangerous business, going out your door.",
  'Many that live deserve death. And some that die deserve life.',
  "There is some good in this world, and it's worth fighting for.",
  'I would have followed you, my brother. My captain. My king.',
  'One Ring to rule them all.',
  'Deeds will not be less valiant because they are unpraised.',
  'Courage is found in unlikely places.',
  'The board is set, the pieces are moving.',
  'Do not be too eager to deal out death in judgement.',
  'He that breaks a thing to find out what it is has left the path of wisdom.',
  'Little by little, one travels far.',
  'May it be a light to you in dark places.',
  'I am no man!',
  'I am a servant of the Secret Fire.',
  'The world is indeed full of peril.',
  'Short cuts make long delays.',
  "Where there's life there's hope, and need of vittles.",
  "It's the job that's never started as takes longest to finish.",
  "I don't know half of you half as well as I should like.",
  'Home is behind, the world ahead.',
  'End? No, the journey does not end here.',
  'Nobody tosses a dwarf.',
  'They have a cave troll.',
  'Look to my coming at first light on the fifth day.',
  'The Ring has awoken. It has heard its master’s call.',
  'I am glad you are here with me. Here at the end of all things.',
  'Moonlight drowns out all but the brightest stars.',
  'All that is gold does not glitter.',
  'The dwarves delved too greedily and too deep.',
  'Fool of a Took!',
  'Even the very wise cannot see all ends.',
  'There is nothing more to say.',
  'Go now, and die in what way seems best to you.',
  'Be at peace, son of Gondor.',
  'I can carry you!',
  'This day we fight!',
  'Death is just another path, one that we all must take.',
  'The grey rain-curtain turns all to silver glass.',
  "Quoth the Raven, 'Nevermore.'",
  'All that we see or seem is but a dream within a dream.',
  'Deep into that darkness peering, long I stood there wondering, fearing.',
  'Darkness there, and nothing more.',
  'Once upon a midnight dreary, while I pondered, weak and weary.',
  'It was the beating of his hideous heart!',
  'For the love of God, Montresor!',
  'The thousand injuries of Fortunato I had borne as I best could.',
  'I became insane, with long intervals of horrible sanity.',
  'There is no exquisite beauty without some strangeness in the proportion.',
  'The boundaries which divide life from death are at best shadowy and vague.',
  'And the silken, sad, uncertain rustling of each purple curtain.',
  'Listen to them, the children of the night. What music they make!',
  'The blood is the life.',
  'I am Dracula, and I bid you welcome.',
  'The dead travel fast.',
  'I want you to believe in things that you cannot.',
  'There are darknesses in life, and there are lights.',
  'Beware; for I am fearless, and therefore powerful.',
  'I ought to be thy Adam, but I am rather the fallen angel.',
  'Nothing is so painful to the human mind as a great and sudden change.',
  'I am malicious because I am miserable.',
  'Learn from me how dangerous is the acquirement of knowledge.',
  'You are my creator, but I am your master.',
  'I am no bird; and no net ensnares me.',
  'Reader, I married him.',
  'Whatever our souls are made of, his and mine are the same.',
  'I cannot live without my life! I cannot live without my soul!',
  'Be with me always — take any form — drive me mad!',
  'I have not broken your heart — you have broken it.',
  'Last night I dreamt I went to Manderley again.',
  'Hill House, not sane, stood by itself against its hills.',
  'Whatever walked there, walked alone.',
  'Journeys end in lovers meeting.',
  'The oldest and strongest emotion of mankind is fear.',
  'That is not dead which can eternal lie.',
  'We live on a placid island of ignorance in the midst of black seas of infinity.',
  'Man is not truly one, but truly two.',
  'Each of us has heaven and hell in him.',
  'The soul is a terrible reality.',
  'You are mine, you shall be mine, you and I are one for ever.',
  'Girls are caterpillars while they live in the world.',
  'No man can wear one face to himself and another to the multitude.',
  'We dream in our waking moments, and walk in our sleep.',
  'The story had held us, round the fire, sufficiently breathless.',
  'A well-informed mind is the best security against the contagion of folly.',
]

const HUES = ['#d8452f', '#f2b33d', '#2f9e8f', '#6f4ea8', '#e8709b', '#3fa9f5', '#fff3dd']

type Part = { x: number; y: number; vx: number; vy: number; g: number; rot: number; vr: number; s: number; color: string; bounce: number; life: number; rest: number; kind: string }

/** The corner piñata: click it, whack it until it bursts. A burst piñata stays burst — close the
 *  window and click the icon again to hang a new one. */
export function Pinata() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <span className="ib" title="piñata" onClick={() => setOpen(true)} style={{ padding: 3 }}>
        <img draggable={false} src="/pinata/1.png" alt="" style={{ height: 18, display: 'block' }} />
      </span>
      {open ? <Party onClose={() => setOpen(false)} /> : null}
    </>
  )
}

function Party({ onClose }: { onClose: () => void }) {
  const [dmg, setDmg] = useState(0)
  const [whack, setWhack] = useState(0)
  const [quotes, setQuotes] = useState<{ id: number; text: string; x: number; y: number }[]>([])
  const [found, setFound] = useState<string[]>([])
  const canvas = useRef<HTMLCanvasElement>(null)
  const body = useRef<HTMLDivElement>(null)
  const host = useRef<HTMLDivElement>(null)
  const parts = useRef<Part[]>([])
  // ponytail: the hit count lives in a ref because clicks land faster than React re-renders — `dmg + 1`
  // off the render's closure silently drops every hit in the same batch. State only mirrors it for paint.
  const hits = useRef(0)
  const quoteFree = useRef(0) // one quote in the air at a time, so each stays readable
  const burst = dmg >= HITS

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation() // the app's esc menu must not open behind us
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  // One rAF loop for the whole party: gravity, one bounce off the floor, then fade.
  useEffect(() => {
    const c = canvas.current!
    const ctx = c.getContext('2d')!
    let raf = 0
    const frame = () => {
      raf = requestAnimationFrame(frame)
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const w = c.clientWidth
      const h = c.clientHeight
      if (c.width !== w * dpr || c.height !== h * dpr) {
        c.width = w * dpr
        c.height = h * dpr
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      const floor = h - 34
      for (const p of parts.current) {
        p.vy += p.g
        p.vx *= 0.995
        p.x += p.vx
        p.y += p.vy
        p.rot += p.vr
        if (p.y > floor) {
          p.y = floor
          p.vy *= -p.bounce
          p.vx *= 0.7
          p.vr *= 0.6
          p.rest += 1
        }
        if (p.rest > 8) p.life -= 0.018
        ctx.save()
        ctx.translate(p.x, p.y)
        ctx.rotate(p.rot)
        ctx.globalAlpha = Math.max(0, Math.min(1, p.life))
        ctx.fillStyle = p.color
        if (p.kind === 'confetti') ctx.fillRect(-p.s * 0.5, -p.s * 0.18, p.s, p.s * 0.36)
        else if (p.kind === 'candy') {
          ctx.beginPath()
          ctx.ellipse(0, 0, p.s * 0.55, p.s * 0.38, 0, 0, 6.3)
          ctx.fill()
          ctx.fillRect(-p.s * 0.9, -p.s * 0.12, p.s * 0.36, p.s * 0.24)
          ctx.fillRect(p.s * 0.54, -p.s * 0.12, p.s * 0.36, p.s * 0.24)
        } else {
          ctx.beginPath()
          ctx.arc(0, 0, p.s * 0.42, 0, 6.3)
          ctx.fill()
        }
        ctx.restore()
      }
      parts.current = parts.current.filter((p) => p.life > 0 && p.x > -80 && p.x < w + 80).slice(-700)
    }
    frame()
    return () => cancelAnimationFrame(raf)
  }, [])

  const spawn = (x: number, y: number, n: number, big: boolean) => {
    const kinds = big ? ['candy', 'confetti', 'confetti', 'bead'] : ['confetti', 'confetti', 'bead', 'candy']
    for (let i = 0; i < n; i++) {
      const kind = kinds[(Math.random() * kinds.length) | 0]
      const a = -Math.PI / 2 + (Math.random() - 0.5) * (big ? 2.6 : 1.6)
      const sp = big ? 5 + Math.random() * 11 : 2.5 + Math.random() * 5
      parts.current.push({
        kind,
        x: x + (Math.random() - 0.5) * (big ? 70 : 44),
        y: y + (Math.random() - 0.5) * 30,
        vx: Math.cos(a) * sp,
        vy: Math.sin(a) * sp - (big ? 3 : 1),
        g: 0.34 + Math.random() * 0.16,
        rot: Math.random() * 6.3,
        vr: (Math.random() - 0.5) * 0.5,
        s: kind === 'candy' ? 11 + Math.random() * 7 : 6 + Math.random() * 8,
        color: HUES[(Math.random() * HUES.length) | 0],
        bounce: 0.32 + Math.random() * 0.2,
        life: 1,
        rest: 0,
      })
    }
  }

  // Only a hit ON the piñata counts; the transparent corners of the sprite count too, close enough.
  const onHit = (e: React.MouseEvent) => {
    e.stopPropagation()
    const rect = host.current!.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top // the confetti bursts where the stick landed
    const next = Math.min(HITS, hits.current + 1)
    hits.current = next
    if (next >= HITS) {
      const b = body.current?.getBoundingClientRect()
      const bx = b ? b.left - rect.left + b.width / 2 : rect.width / 2
      const by = b ? b.top - rect.top + b.height * 0.55 : rect.height * 0.45
      spawn(bx, by, 280, true)
      setTimeout(() => spawn(bx, by - 20, 140, true), 170)
    } else {
      spawn(x, y, 10 + Math.round((next / HITS) * 34), false)
    }
    setDmg(next)
    setWhack((n) => n + 1)
    // ponytail: most hits are just confetti; a quote is a find, and never on top of another one.
    if (Math.random() > 0.22 || performance.now() < quoteFree.current) return
    quoteFree.current = performance.now() + 1600
    const said = QUOTES[(Math.random() * QUOTES.length) | 0]
    const id = next * 1000 + Math.floor(Math.random() * 999)
    // scattered around the top of the stage so two in the air never land on each other
    const x0 = rect.width / 2 + (Math.random() - 0.5) * 120
    const y0 = 40 + Math.random() * 80
    setQuotes((q) => [...q, { id, text: said, x: x0, y: y0 }].slice(-2))
    setTimeout(() => setQuotes((q) => q.filter((n) => n.id !== id)), 2800)
    setFound((all) => (all.includes(said) ? all : [...all, said]))
  }

  const f = dmg / HITS
  const stage = f >= 0.8 ? 4 : f >= 0.55 ? 3 : f >= 0.28 ? 2 : 1
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'grid', placeItems: 'center', background: 'rgba(0,0,0,0.45)' }} onClick={onClose}>
      <style>{`
        @keyframes pn-swing { 0%,100% { transform: rotate(-5deg) } 50% { transform: rotate(5deg) } }
        @keyframes pn-a { 0% { transform: translate(0,0) rotate(0) } 18% { transform: translate(10px,4px) rotate(10deg) } 46% { transform: translate(-7px,1px) rotate(-8deg) } 72% { transform: translate(4px,0) rotate(4deg) } 100% { transform: translate(0,0) rotate(0) } }
        @keyframes pn-b { 0% { transform: translate(0,0) rotate(0) } 18% { transform: translate(-10px,4px) rotate(-10deg) } 46% { transform: translate(7px,1px) rotate(8deg) } 72% { transform: translate(-4px,0) rotate(-4deg) } 100% { transform: translate(0,0) rotate(0) } }
        @keyframes pn-drop { 0% { transform: translateY(0) rotate(0); opacity: 1 } 100% { transform: translateY(300px) rotate(72deg); opacity: 0 } }
        @keyframes pn-quote { from { transform: translate(-50%,20px); opacity: 0 } 12% { opacity: 1 } 70% { opacity: 1 } to { transform: translate(-50%,-70px); opacity: 0 } }
        @keyframes pn-wreck { 0% { transform: translateY(-24px) scale(0.8); opacity: 0 } 60% { opacity: 1 } 100% { transform: translateY(0) scale(1); opacity: 1 } }
      `}</style>

      <div style={{ display: 'flex', alignItems: 'stretch', gap: 12 }} onClick={(e) => e.stopPropagation()}>
      <div
        ref={host}
        style={{
          position: 'relative',
          width: 440,
          height: 480,
          overflow: 'hidden',
          borderRadius: 10,
          border: '1px solid rgba(58,42,34,0.35)',
          boxShadow: '0 18px 50px rgba(0,0,0,0.45)',
          userSelect: 'none',
          background: 'radial-gradient(110% 80% at 50% 0%, #fff8ea 0%, #f6e8d0 55%, #ecd9bb 100%)',
        }}
      >
        <canvas ref={canvas} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 4 }} />

        <div style={{ position: 'absolute', top: -6, left: '50%', width: 0, height: 0, zIndex: 3 }}>
          <div style={{ position: 'absolute', transformOrigin: '0 0', animation: burst ? 'pn-drop 0.9s 0.1s cubic-bezier(0.5,0,0.9,0.4) forwards' : `pn-swing ${3.3 - f * 1.5}s ease-in-out infinite` }}>
            <div style={{ position: 'absolute', top: 0, left: -1.5, width: 3, height: 250, borderRadius: 2, background: 'linear-gradient(#3a2a22, #7a5644)' }} />
            <div style={{ position: 'absolute', top: 244, left: -5, width: 10, height: 8, borderRadius: '50%', background: '#3a2a22' }} />
            <div key={whack} style={{ position: 'absolute', top: 158, left: -80, width: 160, animation: whack ? `pn-${whack % 2 ? 'a' : 'b'} 0.4s ease-out` : 'none' }}>
              <div ref={body} onClick={onHit} style={{ position: 'relative', width: 160, height: 192, cursor: burst ? 'default' : 'crosshair', pointerEvents: burst ? 'none' : 'auto', filter: 'drop-shadow(0 14px 14px rgba(58,42,34,0.26))' }}>
                <img draggable={false} src={`/pinata/${stage}.png`} alt="" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain' }} />
              </div>
            </div>
          </div>
        </div>

        {burst ? (
          <>
            <div style={{ position: 'absolute', left: 0, right: 0, top: 60, zIndex: 6, textAlign: 'center', fontSize: 30, fontWeight: 800, color: '#c4452f', textShadow: '0 1px 0 #fff5e3', animation: 'pn-wreck 0.5s 0.2s cubic-bezier(0.2,1.4,0.4,1) both' }}>
              Congratulations!
            </div>
            <div style={{ position: 'absolute', left: '50%', bottom: 70, width: 260, marginLeft: -130, zIndex: 3, animation: 'pn-wreck 0.5s 0.25s cubic-bezier(0.2,1.4,0.4,1) both' }}>
              <img draggable={false} src="/pinata/burst.png" alt="" style={{ width: '100%', filter: 'drop-shadow(0 14px 16px rgba(58,42,34,0.3))' }} />
            </div>
          </>
        ) : null}

        {quotes.map((q) => (
          <div
            key={q.id}
            style={{ position: 'absolute', left: q.x, top: q.y, width: 230, zIndex: 6, pointerEvents: 'none', textAlign: 'center', fontFamily: 'Georgia, serif', fontStyle: 'italic', fontSize: 15, fontWeight: 700, lineHeight: 1.3, color: '#2a0d08', padding: '4px 8px', borderRadius: 6, background: 'rgba(255,248,234,0.9)', boxShadow: '0 2px 8px rgba(58,42,34,0.25)', animation: 'pn-quote 2.8s ease-out both' }}
          >
            {q.text}
          </div>
        ))}

        <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 110, background: 'linear-gradient(transparent, rgba(206,170,124,0.5))', pointerEvents: 'none', zIndex: 2 }} />

        <span onClick={onClose} title="close (esc)" style={{ position: 'absolute', top: 8, right: 12, zIndex: 7, fontSize: 16, color: '#3a2a22', cursor: 'pointer' }}>
          ✕
        </span>
      </div>

      {burst ? (
        <div style={{ width: 260, height: 480, overflowY: 'auto', borderRadius: 10, border: '1px solid rgba(58,42,34,0.35)', background: '#fff8ea', padding: '14px 16px', boxShadow: '0 18px 50px rgba(0,0,0,0.45)' }}>
          <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.22em', textTransform: 'uppercase', color: '#a8705a', marginBottom: 12 }}>
            {found.length} quotes found
          </div>
          {found.map((q) => (
            <div key={q} style={{ marginBottom: 12, fontFamily: 'Georgia, serif', fontStyle: 'italic', fontSize: 14, lineHeight: 1.35, color: '#5a3a2a' }}>
              “{q}”
            </div>
          ))}
        </div>
      ) : null}
      </div>
    </div>
  )
}
