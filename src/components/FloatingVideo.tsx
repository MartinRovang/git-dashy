import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

const EMBED = 'https://www.youtube-nocookie.com'

/** The small player the top-left logo toggles, with a sound slider of its own. Drag it by its body. */
export function FloatingVideo() {
  const [volume, setVolume] = useState(50)
  const [pos, setPos] = useState({ x: 14, y: 54 })
  const frame = useRef<HTMLIFrameElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const grab = useRef<{ dx: number; dy: number } | null>(null)
  // ponytail: keep the box on screen when the window shrinks
  useEffect(() => {
    const fit = () => {
      const r = box.current!.getBoundingClientRect()
      setPos((p) => ({ x: Math.min(p.x, Math.max(0, window.innerWidth - r.width)), y: Math.min(p.y, Math.max(0, window.innerHeight - r.height)) }))
    }
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [])
  const send = (func: string, args: unknown[]) =>
    frame.current?.contentWindow?.postMessage(JSON.stringify({ event: 'command', func, args }), EMBED)
  // Commands sent before the player is ready are dropped, so set the sound on onReady. Captions can
  // load even with cc_load_policy off, so unload both caption modules ("captions", and "cc" on older
  // players) whenever they announce themselves and whenever playback starts. Clearing the track first
  // covers a viewer whose own YouTube settings turn captions on.
  const vol = useRef(volume)
  vol.current = volume
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== EMBED || e.source !== frame.current?.contentWindow) return
      let m: { event?: string; info?: { namespaces?: string[]; playerState?: number } | null }
      try {
        m = JSON.parse(e.data)
      } catch {
        return
      }
      if (m.event === 'onReady') {
        send('unMute', [])
        send('setVolume', [vol.current])
      }
      const captions = m.event === 'apiInfoDelivery' && m.info?.namespaces?.includes('captions')
      const playing = m.event === 'infoDelivery' && m.info?.playerState === 1
      if (captions || playing) {
        send('setOption', ['captions', 'track', {}])
        send('unloadModule', ['captions'])
        send('unloadModule', ['cc'])
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const apply = (v: number) => {
    setVolume(v)
    send('setVolume', [v])
  }
  const onDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('input')) return
    const r = box.current!.getBoundingClientRect()
    grab.current = { dx: e.clientX - r.left, dy: e.clientY - r.top }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!grab.current) return
    const r = box.current!.getBoundingClientRect()
    setPos({
      x: Math.min(Math.max(0, e.clientX - grab.current.dx), window.innerWidth - r.width),
      y: Math.min(Math.max(0, e.clientY - grab.current.dy), window.innerHeight - r.height),
    })
  }
  return (
    <div
      className="floatvid"
      ref={box}
      style={{ left: pos.x, top: pos.y }}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={() => (grab.current = null)}
      onLostPointerCapture={() => (grab.current = null)}
    >
      <div className="frame">
        <iframe
          ref={frame}
          src={`${EMBED}/embed/bTyq_1kGzgY?autoplay=1&enablejsapi=1&controls=0&modestbranding=1&rel=0&iv_load_policy=3&playsinline=1&cc_load_policy=0`}
          title="gitdashy"
          allow="autoplay; encrypted-media; picture-in-picture"
          allowFullScreen
          onLoad={() => frame.current?.contentWindow?.postMessage(JSON.stringify({ event: 'listening' }), EMBED)}
        />
        <span className="cover">
          <span className="shim">
            <img src="/head.png" alt="" />
          </span>
          <span className="covertext">
            <span className="title">gitdashy</span>
          </span>
        </span>
      </div>
      <div className="vol">
        <span>🔊</span>
        <input type="range" min="0" max="100" value={volume} onChange={(e) => apply(+e.target.value)} />
      </div>
    </div>
  )
}
