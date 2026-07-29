export function flagEmoji(countryCode: string): string {
  return countryCode
    .toUpperCase()
    .split('')
    .map((c) => String.fromCodePoint(127397 + c.charCodeAt(0)))
    .join('')
}
