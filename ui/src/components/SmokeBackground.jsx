import { useEffect, useRef } from 'react'

// Dynamic "smoke" background (the fbm domain-warp shader from IAM UI files/shader —
// its billowing noise reads as slow-drifting smoke). Theme-aware via a `u_dark`
// uniform and tuned to the app palette: a faint green smoke over #0e1512 in dark,
// and over #f3f5f3 in light — subtle enough that headings/cards stay readable.
// Self-contained; if WebGL is missing it renders nothing (the page bg shows).
export function SmokeBackground({ dark = true }) {
  const canvasRef = useRef(null)
  const darkRef = useRef(dark)
  darkRef.current = dark

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl')
    if (!gl) return

    const syncSize = () => {
      const w = canvas.clientWidth || 1280
      const h = canvas.clientHeight || 720
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w
        canvas.height = h
      }
    }
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(syncSize) : null
    ro?.observe(canvas)
    syncSize()

    const vs = `attribute vec2 a_position;
varying vec2 v_texCoord;
void main(){ v_texCoord = a_position * 0.5 + 0.5; gl_Position = vec4(a_position, 0.0, 1.0); }`

    const fs = `precision highp float;
varying vec2 v_texCoord;
uniform float u_time;
uniform vec2 u_resolution;
uniform float u_dark;
float random(vec2 st){ return fract(sin(dot(st.xy, vec2(12.9898,78.233))) * 43758.5453123); }
float noise(vec2 st){
  vec2 i = floor(st); vec2 f = fract(st);
  float a = random(i); float b = random(i + vec2(1.0,0.0));
  float c = random(i + vec2(0.0,1.0)); float d = random(i + vec2(1.0,1.0));
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(a,b,u.x) + (c-a)*u.y*(1.0-u.x) + (d-b)*u.x*u.y;
}
float fbm(vec2 st){
  float v = 0.0; float a = 0.5;
  for (int i = 0; i < 6; i++){ v += a * noise(st); st *= 2.0; a *= 0.5; }
  return v;
}
void main(){
  vec2 st = v_texCoord * u_resolution.xy / min(u_resolution.x, u_resolution.y);
  st *= 1.55;
  // Domain-warped fbm — the slow drift that reads as smoke.
  vec2 q;
  q.x = fbm(st + 0.05 * u_time);
  q.y = fbm(st + vec2(1.0) + 0.04 * u_time);
  vec2 r;
  r.x = fbm(st + 1.0 * q + vec2(1.7, 9.2) + 0.06 * u_time);
  r.y = fbm(st + 1.0 * q + vec2(8.3, 2.8) + 0.05 * u_time);
  float f = fbm(st + r);
  float smoke = clamp(f * f * 1.8, 0.0, 1.0);
  float wisp = clamp(length(q) - 0.30, 0.0, 0.7);
  if (u_dark > 0.5) {
    vec3 base = vec3(0.055, 0.082, 0.071);   // #0e1512
    vec3 hi   = vec3(0.16, 0.40, 0.26);      // green smoke
    vec3 color = mix(base, hi, smoke * 0.85);
    color += vec3(0.05, 0.14, 0.08) * wisp;  // brighter tendrils
    gl_FragColor = vec4(color, 1.0);
  } else {
    vec3 base = vec3(0.949, 0.961, 0.949);   // #f3f5f3
    vec3 hi   = vec3(0.63, 0.80, 0.68);      // visible green-grey smoke
    vec3 color = mix(base, hi, smoke * 0.75);
    color -= vec3(0.04, 0.03, 0.04) * wisp;  // darker wisps for depth
    gl_FragColor = vec4(color, 1.0);
  }
}`

    const compile = (type, src) => {
      const s = gl.createShader(type)
      gl.shaderSource(s, src)
      gl.compileShader(s)
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.warn('SmokeBackground shader compile failed:', gl.getShaderInfoLog(s))
      }
      return s
    }
    const prog = gl.createProgram()
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, vs))
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fs))
    gl.linkProgram(prog)
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.warn('SmokeBackground program link failed:', gl.getProgramInfoLog(prog))
    }
    gl.useProgram(prog)

    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
    const pos = gl.getAttribLocation(prog, 'a_position')
    gl.enableVertexAttribArray(pos)
    gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0)

    const uTime = gl.getUniformLocation(prog, 'u_time')
    const uRes = gl.getUniformLocation(prog, 'u_resolution')
    const uDark = gl.getUniformLocation(prog, 'u_dark')

    let raf = 0
    const render = (t) => {
      if (!ro) syncSize()
      gl.viewport(0, 0, canvas.width, canvas.height)
      if (uTime) gl.uniform1f(uTime, t * 0.001)
      if (uRes) gl.uniform2f(uRes, canvas.width, canvas.height)
      if (uDark) gl.uniform1f(uDark, darkRef.current ? 1.0 : 0.0)
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
      raf = requestAnimationFrame(render)
    }
    raf = requestAnimationFrame(render)

    // NOTE: intentionally do NOT call WEBGL_lose_context here. Under React
    // StrictMode the effect mounts→cleans up→mounts again; losing the context on
    // the first cleanup left the second mount with a dead context (blank canvas →
    // the page's plain background showed through). Cancelling the frame + dropping
    // the observer is enough; the re-mount reuses the same live context.
    return () => {
      cancelAnimationFrame(raf)
      ro?.disconnect()
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="fixed inset-0 z-0 h-full w-full"
      style={{ display: 'block' }}
    />
  )
}
