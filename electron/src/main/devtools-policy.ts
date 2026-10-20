export function shouldOpenDevTools(
  environment: Record<string, string | undefined> = process.env,
): boolean {
  return environment.VOICEYM_OPEN_DEVTOOLS === '1';
}
