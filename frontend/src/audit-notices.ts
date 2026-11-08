// Older evaluators put build identifiers and actionable warnings in the same field.
const revisionOnly = /^[a-z][\w.-]*\s*[: ]\s*[a-f0-9]{64}\.?$/i;
const profileMarker = /^(Эксперимент |Перепроверка потребности и ведения диалога [\w.-]+,|Адресная (?:локальная )?проверка [\w.-]+,|Проверка оснований для изменения оценки |Калибровка )[\s\S]*SHA-256(?: набора)?:?\s*[a-f0-9]{64}\.?$/i;

export function auditNotices(warnings: readonly string[] = []): string[] {
  return [...new Set(warnings.map(w => w.trim()).filter(w => w && !revisionOnly.test(w) && !profileMarker.test(w)))];
}
