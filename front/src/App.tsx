import { useCallback, useEffect, useRef, useState } from 'react'

// ---------- 类型 ----------
interface Item {
  id: string
  filename: string
  status: 'queued' | 'running' | 'done' | 'error'
  progress: string
  error: string | null
  source_thumb?: string
  result?: string
  result_url?: string  // 静态文件 URL（落盘后的下载地址）
}
interface BatchStatus {
  id: string
  summary: {
    total: number
    done: number
    error: number
    running: boolean
    finished: boolean
  }
  items: Item[]
}

// ---------- 配置选项 ----------
const TARGET_LANGS = [
  { code: 'CHS', label: '简体中文' },
  { code: 'CHT', label: '繁體中文' },
  { code: 'ENG', label: 'English' },
  { code: 'JPN', label: '日本語' },
]
const DIRECTIONS = [
  { code: 'auto', label: '自动' },
  { code: 'h', label: '横排' },
  { code: 'v', label: '竖排' },
]

export default function App() {
  const [files, setFiles] = useState<File[]>([])
  const [batchId, setBatchId] = useState<string | null>(null)
  const [status, setStatus] = useState<BatchStatus | null>(null)
  const [targetLang, setTargetLang] = useState('CHS')
  const [direction, setDirection] = useState('auto')
  const [fontSizeOffset, setFontSizeOffset] = useState(0)
  const [busy, setBusy] = useState(false)
  const [previewItem, setPreviewItem] = useState<Item | null>(null)
  const [showOriginal, setShowOriginal] = useState(false)
  const pollRef = useRef<number | null>(null)

  // ---- 文件选择 ----
  const onPick = useCallback((list: FileList | null) => {
    if (!list) return
    const arr = Array.from(list).filter((f) => f.type.startsWith('image/'))
    setFiles((prev) => [...prev, ...arr])
    // 选择新文件时清掉上一次结果
    setBatchId(null)
    setStatus(null)
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      onPick(e.dataTransfer.files)
    },
    [onPick],
  )

  // ---- 提交翻译 ----
  const submit = useCallback(async () => {
    if (!files.length) return
    setBusy(true)
    setStatus(null)
    const fd = new FormData()
    files.forEach((f) => fd.append('images', f))
    fd.append(
      'config',
      JSON.stringify({ target_lang: targetLang, direction, font_size_offset: fontSizeOffset }),
    )
    try {
      const res = await fetch('/api/translate/batch', { method: 'POST', body: fd })
      const data = await res.json()
      setBatchId(data.batch_id)
    } catch (e) {
      alert('提交失败: ' + (e as Error).message)
      setBusy(false)
    }
  }, [files, targetLang, direction, fontSizeOffset])

  // ---- 轮询状态 ----
  useEffect(() => {
    if (!batchId) return
    const poll = async () => {
      try {
        const res = await fetch(`/api/batch/${batchId}/status`)
        const data: BatchStatus = await res.json()
        setStatus(data)
        if (data.summary.finished) {
          setBusy(false)
          if (pollRef.current) window.clearInterval(pollRef.current)
          pollRef.current = null
        }
      } catch {
        /* 忽略瞬时网络错误，下次重试 */
      }
    }
    poll()
    pollRef.current = window.setInterval(poll, 1500)
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
    }
  }, [batchId])

  // ---- 停止服务 ----
  const shutdown = useCallback(async () => {
    if (!confirm('确定停止后端服务？需要手动重新启动。')) return
    try {
      await fetch('/api/shutdown', { method: 'POST' })
      alert('已发送停止指令，服务即将退出。')
    } catch {
      /* 服务关闭后连接断开，正常 */
    }
  }, [])

  // ---- 打开输出目录 ----
  const openOutputs = useCallback(async () => {
    try {
      const res = await fetch('/api/open-outputs', { method: 'POST' })
      const data = await res.json()
      if (!data.ok) alert('无法自动打开，请手动前往：\n' + data.path)
    } catch (e) {
      alert('打开失败: ' + (e as Error).message)
    }
  }, [])

  const removeFile = (idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx))
  }

  const clearAll = () => {
    setFiles([])
    setBatchId(null)
    setStatus(null)
  }

  // 把后端 items 跟本地 files 对齐（按顺序；后端按上传顺序处理）
  const items: Item[] = status?.items ?? []

  return (
    <div className="app">
      <header className="topbar">
        <h1>漫画翻译 · Lite</h1>
        <span className="subtitle">DeepSeek · GPU · 本地部署</span>
        <button className="btn-ghost" onClick={openOutputs} title="在文件管理器打开 outputs 目录">
          📁 输出目录
        </button>
        <button className="btn-danger" onClick={shutdown} title="停止后端进程">
          停止服务
        </button>
      </header>

      <div className="layout">
        {/* 左侧：上传 + 选项 */}
        <aside className="sidebar">
          <div
            className="dropzone"
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
            onClick={() => document.getElementById('file-input')?.click()}
          >
            <input
              id="file-input"
              type="file"
              multiple
              accept="image/*"
              style={{ display: 'none' }}
              onChange={(e) => onPick(e.target.files)}
            />
            <div className="dropzone-text">
              <div className="big">＋</div>
              <div>点击或拖拽图片到这里</div>
              <div className="hint">支持批量，PNG/JPG/WebP</div>
            </div>
          </div>

          <div className="options">
            <label>
              目标语言
              <select value={targetLang} onChange={(e) => setTargetLang(e.target.value)}>
                {TARGET_LANGS.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              文字方向
              <select value={direction} onChange={(e) => setDirection(e.target.value)}>
                {DIRECTIONS.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              字号偏移（-10 ~ 10）
              <input
                type="number"
                min={-10}
                max={10}
                value={fontSizeOffset}
                onChange={(e) => setFontSizeOffset(Number(e.target.value))}
              />
            </label>
          </div>

          <div className="actions">
            <button className="btn-primary" onClick={submit} disabled={!files.length || busy}>
              {busy ? '翻译中…' : `翻译全部（${files.length}）`}
            </button>
            <button className="btn-ghost" onClick={clearAll} disabled={busy}>
              清空
            </button>
          </div>

          {status && (
            <div className="progress-summary">
              <div>
                进度：{status.summary.done + status.summary.error} / {status.summary.total}
              </div>
              {status.summary.error > 0 && <div className="err">失败 {status.summary.error}</div>}
              {status.summary.finished && <div className="ok">✓ 全部完成</div>}
            </div>
          )}
        </aside>

        {/* 右侧：队列 */}
        <main className="queue">
          {!files.length && !items.length && (
            <div className="empty">还没有图片，先从左侧上传。</div>
          )}

          {/* 上传了但还没提交 / 提交了但没状态：显示本地文件 */}
          {files.map((f, idx) => {
            const it = items[idx]
            return (
              <FileRow
                key={idx}
                filename={f.name}
                localUrl={URL.createObjectURL(f)}
                item={it}
                onRemove={() => removeFile(idx)}
                onPreview={() => {
                  if (it) {
                    setPreviewItem(it)
                    setShowOriginal(false)
                  }
                }}
              />
            )
          })}
        </main>
      </div>

      {/* 预览弹窗 */}
      {previewItem && (
        <div className="modal" onClick={() => setPreviewItem(null)}>
          <div className="modal-body" onClick={(e) => e.stopPropagation()}>
            <div className="modal-tabs">
              <button
                className={!showOriginal ? 'active' : ''}
                onClick={() => setShowOriginal(false)}
              >
                译文
              </button>
              <button
                className={showOriginal ? 'active' : ''}
                onClick={() => setShowOriginal(true)}
              >
                原图
              </button>
              <a
                className="download-link"
                href={previewItem.result_url || previewItem.result}
                download={
                  (previewItem.result_url
                    ? undefined
                    : previewItem.filename.replace(/\.\w+$/, '') + '_translated.png') as any
                }
                target={previewItem.result_url ? '_blank' : undefined}
                rel="noreferrer"
              >
                ↓ 下载
              </a>
              <button className="close" onClick={() => setPreviewItem(null)}>
                ✕
              </button>
            </div>
            <img
              src={
                showOriginal
                  ? previewItem.source_thumb
                  : previewItem.result ?? previewItem.source_thumb
              }
              alt={previewItem.filename}
            />
          </div>
        </div>
      )}
    </div>
  )
}

// ---------- 单行组件 ----------
function FileRow({
  filename,
  localUrl,
  item,
  onRemove,
  onPreview,
}: {
  filename: string
  localUrl: string
  item?: Item
  onRemove: () => void
  onPreview: () => void
}) {
  const status = item?.status ?? 'queued'
  const statusLabel =
    status === 'queued'
      ? '排队中'
      : status === 'running'
        ? `处理中·${item?.progress || ''}`
        : status === 'done'
          ? '✓ 完成'
          : `✗ ${item?.error || '失败'}`

  return (
    <div className={`row row-${status}`}>
      <img
        className="thumb"
        src={item?.source_thumb || localUrl}
        alt={filename}
        onClick={status === 'done' ? onPreview : undefined}
      />
      <div className="row-info">
        <div className="row-name" title={filename}>
          {filename}
        </div>
        <div className={`row-status status-${status}`}>{statusLabel}</div>
      </div>
      <button className="row-remove" onClick={onRemove} title="移除">
        ✕
      </button>
    </div>
  )
}
