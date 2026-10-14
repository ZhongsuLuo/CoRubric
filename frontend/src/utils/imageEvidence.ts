import type { EvidenceAnchor } from '../types/schema'

const legacyImageBlockPattern =
  /^>\s*\*\*\[Image\]\*\*\s*(?<description>[^\r\n]*)\r?\n>\s*\*Source:\s*(?<src>[^*\r\n]+?)\s*\*\s*$/gim
const legacyImageSourcePattern = /Source:\s*([^*\r\n]+)/gi
const markdownImagePattern = /!\[([^\]]*)\]\(([^)\s]+)\)/g

const decodeImagePath = (value: string) => {
  let decoded = value
  try {
    for (let index = 0; index < 3; index += 1) {
      const nextValue = decodeURIComponent(decoded)
      if (nextValue === decoded) break
      decoded = nextValue
    }
    return decoded
  } catch {
    return ''
  }
}

const normalizedImageIdentity = (value?: string | null) => {
  const safe = safeReportImageSrc(value)
  if (!safe) return ''
  const withoutQuery = safe.split(/[?#]/, 1)[0] || ''
  return decodeImagePath(withoutQuery).replace(/\\/g, '/').replace(/^\.\//, '')
}

export const safeReportImageSrc = (value?: string | null) => {
  const raw = String(value || '')
    .trim()
    .replace(/\\/g, '/')
  if (!raw) return ''
  const path = decodeImagePath(raw.split(/[?#]/, 1)[0]?.replace(/^\.\//, '') || '')
  if (!path) return ''
  const parts = path.split('/').filter(Boolean)
  if (parts[0] === 'images' && !parts.some((part) => part === '.' || part === '..')) {
    return parts.join('/')
  }
  if (path.startsWith('/case-files/') && !parts.some((part) => part === '.' || part === '..')) {
    return raw
  }
  return ''
}

export const parseLegacyImageEvidence = (value?: string | null): EvidenceAnchor[] => {
  const source = String(value || '')
  const anchors: EvidenceAnchor[] = []
  const seen = new Set<string>()
  for (const match of source.matchAll(legacyImageBlockPattern)) {
    const imageSrc = safeReportImageSrc(match.groups?.src)
    if (!imageSrc || seen.has(imageSrc)) continue
    seen.add(imageSrc)
    anchors.push({
      anchor_type: 'image',
      image_src: imageSrc,
      description: match.groups?.description?.trim() || 'Image evidence',
    })
  }
  if (anchors.length) return anchors

  for (const match of source.matchAll(legacyImageSourcePattern)) {
    const imageSrc = safeReportImageSrc(match[1])
    if (!imageSrc || seen.has(imageSrc)) continue
    seen.add(imageSrc)
    anchors.push({ anchor_type: 'image', image_src: imageSrc, description: 'Image evidence' })
  }
  for (const match of source.matchAll(markdownImagePattern)) {
    const imageSrc = safeReportImageSrc(match[2])
    if (!imageSrc || seen.has(imageSrc)) continue
    seen.add(imageSrc)
    anchors.push({
      anchor_type: 'image',
      image_src: imageSrc,
      description: match[1]?.trim() || 'Image evidence',
    })
  }
  return anchors
}

export const evidenceAnchorsWithLegacyImages = (
  anchors: EvidenceAnchor[] | undefined,
  legacyText?: string | null,
) => {
  const structured = anchors || []
  if (structured.some((anchor) => anchor.anchor_type === 'image')) return structured
  const legacyImages = parseLegacyImageEvidence(legacyText)
  if (!legacyImages.length) return structured
  return [
    ...structured.filter(
      (anchor) =>
        anchor.anchor_type !== 'text' ||
        (!anchor.text?.includes('[Image]') && !anchor.text?.includes('Source:')),
    ),
    ...legacyImages,
  ]
}

export const resolveReportImageUrl = (
  markdown: string,
  imageSrc?: string | null,
  apiBase = '/api',
) => {
  const identity = normalizedImageIdentity(imageSrc)
  if (!identity) return ''
  const candidates = Array.from(markdown.matchAll(markdownImagePattern))
    .map((match) => safeReportImageSrc(match[2]))
    .filter(Boolean)
  const matched = candidates.find((candidate) => {
    const normalized = normalizedImageIdentity(candidate)
    return (
      normalized === identity ||
      normalized.endsWith(`/${identity}`) ||
      identity.endsWith(`/${normalized}`)
    )
  })
  const direct = safeReportImageSrc(imageSrc)
  const resolved = matched || (direct.startsWith('/case-files/') ? direct : '')
  if (!resolved) return ''
  return resolved.startsWith('/case-files/') ? `${apiBase}${resolved}` : resolved
}
