import { useState } from 'react'

type Props = {
  /** ISO 3166-1 alpha-2, lowercase - the same code the pipeline keys on. */
  code: string | null | undefined
  /** Country name, used as the image's alt text. */
  name?: string
  size?: 'sm' | 'md'
}

/**
 * A country flag as an image, not an emoji.
 *
 * Emoji flags are two Unicode "regional indicator" characters that a font is
 * meant to substitute with a single glyph. Apple's emoji font does; Microsoft's
 * never has - so on Windows every flag rendered as the bare letters ("US",
 * "JP"), which is unfixable in JavaScript because it's a font gap, not a
 * string problem.
 *
 * Images render identically on Windows, macOS, iOS and Android. flagcdn serves
 * SVGs keyed by lowercase alpha-2, which is exactly the code this project
 * already uses, so no mapping table is needed.
 */
export function CountryFlag({ code, name, size = 'sm' }: Props) {
  const [failed, setFailed] = useState(false)

  // Render nothing rather than a broken-image icon or a placeholder box. The
  // country name always sits next to the flag in this app, so losing it costs
  // no information - unlike the old emoji, which degraded into letters that
  // looked like intentional content.
  if (!code || failed) return null

  return (
    <img
      className={`flag flag--${size}`}
      src={`https://flagcdn.com/${code.toLowerCase()}.svg`}
      alt={name ? `Flag of ${name}` : ''}
      // Decorative when unlabelled: the country name is always adjacent, so
      // announcing the flag too would just repeat it.
      aria-hidden={name ? undefined : true}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  )
}
