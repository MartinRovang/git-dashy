import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

/** The small player the top-left logo toggles, with a sound slider of its own. Drag it by its body. */
export function FloatingVideo() {
  const [volume, setVolume] = useState(50)
  const [cover, setCover] = useState(true)
  const [pos, setPos] = useState({ x: 14, y: 54 })
  const frame = useRef<HTMLIFrameElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const grab = useRef<{ dx: number; dy: number } | null>(null)
  useEffect(() => {
    const t = setTimeout(() => setCover(false), 5500)
    return () => clearTimeout(t)
  }, [])
  const send = (func: string, args: number[]) =>
    frame.current?.contentWindow?.postMessage(JSON.stringify({ event: 'command', func, args }), '*')
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
    >
      <div className="frame">
        <iframe
          ref={frame}
          src="https://www.youtube.com/embed/bTyq_1kGzgY?autoplay=1&enablejsapi=1&controls=0&modestbranding=1&rel=0&iv_load_policy=3&playsinline=1"
          title="gitdashy"
          allow="autoplay; encrypted-media; picture-in-picture"
          allowFullScreen
          onLoad={() => {
            send('unMute', [])
            send('setVolume', [volume])
          }}
        />
        <span className={`cover${cover ? '' : ' gone'}`}>
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
