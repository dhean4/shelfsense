/**
 * Consume a server-sent-events endpoint with fetch (EventSource cannot carry auth headers).
 * Calls `onEvent` for every frame with a `data:` line; resolves when the stream ends or the
 * signal aborts.
 */
export async function consumeSse(
  url: string,
  headers: Record<string, string>,
  onEvent: (event: string, data: unknown) => void,
  signal: AbortSignal,
  onOpen?: () => void,
): Promise<void> {
  const response = await fetch(url, { headers, signal });
  if (!response.ok || !response.body) return;
  onOpen?.();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    for (const frame of splitFrames(buffer)) {
      buffer = frame.rest;
      const parsed = parseFrame(frame.frame);
      if (parsed) onEvent(parsed.event, parsed.data);
    }
  }
}

function* splitFrames(buffer: string): Generator<{ frame: string; rest: string }> {
  let index: number;
  let rest = buffer;
  while ((index = rest.indexOf("\n\n")) !== -1) {
    const frame = rest.slice(0, index);
    rest = rest.slice(index + 2);
    yield { frame, rest };
  }
}

export function parseFrame(frame: string): { event: string; data: unknown } | null {
  let event = "message";
  let data: string | null = null;
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) data = line.slice(6);
  }
  if (data === null) return null;
  try {
    return { event, data: JSON.parse(data) as unknown };
  } catch {
    return null;
  }
}
