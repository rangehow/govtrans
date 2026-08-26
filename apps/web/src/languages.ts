import type { LanguageSpec } from './types'

export const FALLBACK_LANGUAGES: LanguageSpec[] = [
  ['zh', '简体中文', 'Simplified Chinese', 'zh-CN'],
  ['en', '英语', 'English', 'en-US'],
  ['ja', '日语', 'Japanese', 'ja-JP'],
  ['ko', '韩语', 'Korean', 'ko-KR'],
  ['fr', '法语', 'French', 'fr-FR'],
  ['de', '德语', 'German', 'de-DE'],
  ['es', '西班牙语', 'Spanish', 'es-ES'],
  ['pt', '葡萄牙语', 'Portuguese', 'pt-PT'],
  ['it', '意大利语', 'Italian', 'it-IT'],
  ['ru', '俄语', 'Russian', 'ru-RU'],
  ['uk', '乌克兰语', 'Ukrainian', 'uk-UA'],
  ['ar', '阿拉伯语', 'Arabic', 'ar', true],
  ['hi', '印地语', 'Hindi', 'hi-IN'],
  ['th', '泰语', 'Thai', 'th-TH'],
  ['vi', '越南语', 'Vietnamese', 'vi-VN'],
  ['id', '印度尼西亚语', 'Indonesian', 'id-ID'],
  ['tr', '土耳其语', 'Turkish', 'tr-TR'],
  ['nl', '荷兰语', 'Dutch', 'nl-NL'],
  ['pl', '波兰语', 'Polish', 'pl-PL'],
].map(([code, name_zh, name_en, bcp47, rtl = false]) => ({
  code: String(code),
  name_zh: String(name_zh),
  name_en: String(name_en),
  bcp47: String(bcp47),
  rtl: Boolean(rtl),
}))

export function languageInfo(code: string, languages = FALLBACK_LANGUAGES): LanguageSpec {
  return languages.find((language) => language.code === code)
    || FALLBACK_LANGUAGES.find((language) => language.code === code)
    || { code, name_zh: code, name_en: code, bcp47: code, rtl: false }
}
