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
    cancelled?: boolean
  }
  items: Item[]
  out_root?: string
}
interface FontItem {
  id: string
  name: string
  source: 'builtin' | 'system' | 'user'
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
  const [fontId, setFontId] = useState('auto')
  const [fonts, setFonts] = useState<FontItem[]>([])
  const [busy, setBusy] = useState(false)
  const [previewItem, setPreviewItem] = useState<Item | null>(null)
  const [showOriginal, setShowOriginal] = useState(false)
  const pollRef = useRef<number | null>(null)
  // 模式：上传 vs 文件夹
  const [mode, setMode] = useState<'upload' | 'folder'>('upload')
  const [folderPath, setFolderPath] = useState('')

  // ---- 加载字体列表 ----
  const loadFonts = useCallback(async () => {
    try {
      const res = await fetch('/api/fonts')
      const data = await res.json()
      setFonts(data.fonts || [])
    } catch {
      /* 忽略 */
    }
  }, [])

  useEffect(() => {
    loadFonts()
  }, [loadFonts])

  // ---- 上传字体 ----
  const onUploadFont = useCallback(
    async (list: FileList | null) => {
      if (!list || !list.length) return
      const fd = new FormData()
      fd.append('file', list[0])
      try {
        const res = await fetch('/api/fonts/upload', { method: 'POST', body: fd })
        const data = await res.json()
        if (data.ok) {
          await loadFonts()
          setFontId(data.font.id)
        } else {
          alert('上传失败: ' + (data.error || '未知错误'))
        }
      } catch (e) {
        alert('上传失败: ' + (e as Error).message)
      }
    },
    [loadFonts],
  )

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
      JSON.stringify({
        target_lang: targetLang,
        direction,
        font_size_offset: fontSizeOffset,
        font_id: fontId,
      }),
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

  // ---- 文件夹模式提交 ----
  const submitFolder = useCallback(async () => {
    const p = folderPath.trim()
    if (!p) return
    setBusy(true)
    setStatus(null)
    setFiles([])
    try {
      const res = await fetch('/api/translate/folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          input_root: p,
          config: { target_lang: targetLang, direction, font_size_offset: fontSizeOffset, font_id: fontId },
        }),
      })
      const data = await res.json()
      if (data.error) {
        alert('提交失败: ' + data.error)
        setBusy(false)
        return
      }
      setBatchId(data.batch_id)
    } catch (e) {
      alert('提交失败: ' + (e as Error).message)
      setBusy(false)
    }
  }, [folderPath, targetLang, direction, fontSizeOffset, fontId])

  // ---- 取消批次 ----
  const cancelBatchReq = useCallback(async () => {
    if (!batchId) return
    if (!confirm('取消未处理的项目？正在翻译的那张会跑完。')) return
    try {
      await fetch(`/api/batch/${batchId}/cancel`, { method: 'POST' })
    } catch {
      /* 忽略 */
    }
  }, [batchId])

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
          {/* 模式切换 */}
          <div className="mode-tabs">
            <button
              className={mode === 'upload' ? 'active' : ''}
              onClick={() => setMode('upload')}
              disabled={busy}
            >
              上传模式
            </button>
            <button
              className={mode === 'folder' ? 'active' : ''}
              onClick={() => setMode('folder')}
              disabled={busy}
            >
              文件夹模式
            </button>
          </div>

          {/* 上传模式的拖拽区 */}
          {mode === 'upload' && (
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
          )}

          {/* 文件夹模式的路径输入 */}
          {mode === 'folder' && (
            <div className="folder-input">
              <label className="folder-label">输入文件夹路径</label>
              <input
                type="text"
                value={folderPath}
                onChange={(e) => setFolderPath(e.target.value)}
                placeholder="D:\manga\kusuri"
                disabled={busy}
              />
              <div className="hint">
                递归扫描所有图片，输出到 outputs/&lt;目录名&gt;_时间戳/，
                完整保留目录结构和原文件名，可直接移回原文件夹覆盖。
              </div>
            </div>
          )}

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
            <label>
              嵌字字体
              <select value={fontId} onChange={(e) => setFontId(e.target.value)}>
                {fonts.length === 0 && <option value="auto">自动（加载中…）</option>}
                {fonts.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              上传自己的字体（.ttf / .ttc / .otf）
              <input
                type="file"
                accept=".ttf,.ttc,.otf"
                onChange={(e) => onUploadFont(e.target.files)}
                style={{ fontSize: 12, padding: '4px' }}
              />
            </label>
          </div>

          <div className="actions">
            {mode === 'upload' ? (
              <>
                <button className="btn-primary" onClick={submit} disabled={!files.length || busy}>
                  {busy ? '翻译中…' : `翻译全部（${files.length}）`}
                </button>
                <button className="btn-ghost" onClick={clearAll} disabled={busy}>
                  清空
                </button>
              </>
            ) : (
              <>
                <button
                  className="btn-primary"
                  onClick={submitFolder}
                  disabled={!folderPath.trim() || busy}
                >
                  {busy ? '翻译中…' : '扫描并翻译'}
                </button>
                <button className="btn-ghost" onClick={() => setFolderPath('')} disabled={busy}>
                  清空路径
                </button>
              </>
            )}
          </div>

          {/* 文件夹模式提交后显示输出目录名 */}
          {mode === 'folder' && status?.out_root && (
            <div className="folder-outroot">
              输出到：outputs/{status.out_root}/
            </div>
          )}

          {status && (
            <div className="progress-summary">
              <div>
                进度：{status.summary.done + status.summary.error} / {status.summary.total}
              </div>
              {status.summary.cancelled && <div className="err">已取消</div>}
              {status.summary.error > 0 && !status.summary.cancelled && (
                <div className="err">失败 {status.summary.error}</div>
              )}
              {status.summary.running && !status.summary.cancelled && (
                <button className="btn-cancel-small" onClick={cancelBatchReq}>
                  取消未处理
                </button>
              )}
              {status.summary.finished && !status.summary.cancelled && (
                <div className="ok">✓ 全部完成</div>
              )}
            </div>
          )}
        </aside>

        {/* 右侧：队列 */}
        <main className="queue">
          {!files.length && !items.length && (
            <div className="empty">
              {mode === 'upload' ? '还没有图片，先从左侧上传。' : '请在左侧输入文件夹路径。'}
            </div>
          )}

          {/* 上传模式：本地文件渲染 */}
          {mode === 'upload' &&
            files.map((f, idx) => {
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

          {/* 文件夹模式：直接渲染后端 items */}
          {mode === 'folder' &&
            items.map((it, idx) => (
              <FileRow
                key={it.id || idx}
                filename={it.filename}
                localUrl=""
                item={it}
                onRemove={() => {}}
                showRemove={false}
                onPreview={() => {
                  if (it.status === 'done') {
                    setPreviewItem(it)
                    setShowOriginal(false)
                  }
                }}
              />
            ))}
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
  showRemove = true,
}: {
  filename: string
  localUrl: string
  item?: Item
  onRemove: () => void
  onPreview: () => void
  showRemove?: boolean
}) {
  const status = item?.status ?? 'queued'
  const progress = item?.progress || ''
  const statusLabel =
    status === 'queued'
      ? '排队中'
      : status === 'running'
        ? `处理中·${progress || ''}`
        : status === 'done'
          ? '✓ 完成'
          : progress === 'cancelled'
            ? '⊘ 已取消'
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
      {showRemove && (
        <button className="row-remove" onClick={onRemove} title="移除">
          ✕
        </button>
      )}
    </div>
  )
}
