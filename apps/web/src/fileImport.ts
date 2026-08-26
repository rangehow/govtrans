export const MAX_SOURCE_CHARS = 100_000

const MAX_SOURCE_BYTES = MAX_SOURCE_CHARS * 4 + 4

const TEXT_EXTENSIONS = new Set([
  'txt', 'text', 'md', 'markdown', 'rst',
  'csv', 'tsv', 'json', 'jsonl', 'xml', 'yaml', 'yml',
  'log', 'ini', 'cfg', 'conf', 'properties',
  'srt', 'vtt', 'tex',
])

const TEXT_MIME_TYPES = new Set([
  'text/plain', 'text/markdown', 'text/csv', 'text/tab-separated-values',
  'text/vtt', 'application/json', 'application/ld+json',
  'application/xml', 'text/xml', 'application/x-yaml',
])

export interface ImportedTextFile {
  name: string
  path: string
  size: number
  text: string
}

export interface RejectedFolderFile {
  path: string
  reason: string
}

export interface FolderScanResult {
  folderName: string
  files: ImportedTextFile[]
  rejected: RejectedFolderFile[]
}

type DirectoryFile = File & { webkitRelativePath?: string }

function relativePath(file: File): string {
  return (file as DirectoryFile).webkitRelativePath || file.name
}

function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return dot > -1 ? filename.slice(dot + 1).toLowerCase() : ''
}

function isTextCandidate(file: File): boolean {
  const extension = extensionOf(file.name)
  return TEXT_EXTENSIONS.has(extension)
    || TEXT_MIME_TYPES.has(file.type.toLowerCase())
    || (!extension && !file.type)
}

function inferredUtf16Encoding(bytes: Uint8Array): 'utf-16le' | 'utf-16be' | null {
  if (bytes.length < 8) return null
  const sample = bytes.subarray(0, Math.min(bytes.length, 4_096))
  let evenNulls = 0
  let oddNulls = 0
  for (let index = 0; index < sample.length; index += 1) {
    if (sample[index] !== 0) continue
    if (index % 2 === 0) evenNulls += 1
    else oddNulls += 1
  }
  const pairs = Math.floor(sample.length / 2)
  if (oddNulls / pairs > 0.3 && evenNulls / pairs < 0.08) return 'utf-16le'
  if (evenNulls / pairs > 0.3 && oddNulls / pairs < 0.08) return 'utf-16be'
  return null
}

function looksBinary(bytes: Uint8Array): boolean {
  const sample = bytes.subarray(0, Math.min(bytes.length, 8_192))
  if (sample.length === 0) return false
  let suspicious = 0
  for (const byte of sample) {
    if (byte === 0) return true
    if (byte < 7 || (byte > 13 && byte < 32)) suspicious += 1
  }
  return suspicious / sample.length > 0.08
}

function decodeText(buffer: ArrayBuffer): string | null {
  const bytes = new Uint8Array(buffer)
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return new TextDecoder('utf-8').decode(bytes.subarray(3))
  }
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) {
    return new TextDecoder('utf-16le').decode(bytes.subarray(2))
  }
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
    return new TextDecoder('utf-16be').decode(bytes.subarray(2))
  }

  const utf16 = inferredUtf16Encoding(bytes)
  if (utf16) return new TextDecoder(utf16).decode(bytes)
  if (looksBinary(bytes)) return null

  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    try {
      return new TextDecoder('gb18030', { fatal: true }).decode(bytes)
    } catch {
      return null
    }
  }
}

export async function scanFolder(fileList: FileList | File[]): Promise<FolderScanResult> {
  const selected = Array.from(fileList)
  const files: ImportedTextFile[] = []
  const rejected: RejectedFolderFile[] = []
  const firstPath = selected[0] ? relativePath(selected[0]) : ''
  const folderName = firstPath.includes('/') ? firstPath.split('/')[0] : '所选文件夹'

  for (const file of selected) {
    const path = relativePath(file)
    if (!isTextCandidate(file)) {
      rejected.push({ path, reason: '不是受支持的文本格式' })
      continue
    }
    if (file.size > MAX_SOURCE_BYTES) {
      rejected.push({ path, reason: '文件过大（单个任务最多 100,000 字符）' })
      continue
    }

    let decoded: string | null
    try {
      decoded = decodeText(await file.arrayBuffer())
    } catch {
      rejected.push({ path, reason: '文件读取失败' })
      continue
    }
    if (decoded === null) {
      rejected.push({ path, reason: '内容不像可读取的文本' })
      continue
    }

    const text = decoded.replace(/^\ufeff/, '').trim()
    if (!text) {
      rejected.push({ path, reason: '空文件' })
      continue
    }
    if (text.length > MAX_SOURCE_CHARS) {
      rejected.push({ path, reason: '超过 100,000 字符' })
      continue
    }
    files.push({ name: file.name, path, size: file.size, text })
  }

  files.sort((left, right) => left.path.localeCompare(right.path, 'zh-CN', { numeric: true }))
  rejected.sort((left, right) => left.path.localeCompare(right.path, 'zh-CN', { numeric: true }))
  return { folderName, files, rejected }
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1_024) return `${bytes} B`
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(bytes < 10_240 ? 1 : 0)} KB`
  return `${(bytes / 1_048_576).toFixed(1)} MB`
}
